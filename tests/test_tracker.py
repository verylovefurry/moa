from datetime import datetime
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import make_handler, validate_target
from tracker import KST, Store, Tracker, url_matches
from tests.fixtures import seed_test_store


class FakeActivity:
    supported = True

    def sample(self):
        return {"exe": "code.exe", "idle": 0}

    def running_apps(self):
        return ["code.exe", "figma.exe"]


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", seed=False)
        seed_test_store(self.store)
        self.now = 1000.0
        self.tracker = Tracker(self.store, FakeActivity(), clock=lambda: self.now)
        self.tracker.enabled = True
        self.store.set_setting("idle_minutes", 1)

    def tearDown(self):
        self.store.db.close()

    def tick(self, at, exe="code.exe", idle=0):
        self.now = float(at)
        self.tracker.tick(sample={"exe": exe, "idle": idle})

    def advance(self, start, end, exe="code.exe", last_input=None):
        for at in range(start, end + 1):
            self.tick(at, exe, 0 if last_input is None else at - last_input)

    def total(self):
        return sum(s["end"] - s["start"] for s in self.store.sessions())

    def test_foreground_switch_does_not_double_count(self):
        self.advance(1000, 1010)
        self.advance(1011, 1020, "figma.exe")
        self.advance(1021, 1030, "other.exe")
        self.assertEqual(self.total(), 21)
        self.assertEqual([s["target_id"] for s in self.store.sessions()], ["code", "figma"])
        self.assertEqual(self.tracker.status, "waiting")

    def test_idle_threshold_removes_time_from_last_input_and_resumes(self):
        self.advance(1000, 1010)
        self.advance(1011, 1070, last_input=1010)
        self.assertEqual(self.tracker.status, "away")
        self.assertEqual(self.total(), 10)
        self.advance(1071, 1079, last_input=1010)
        self.advance(1080, 1090)
        self.assertEqual(self.total(), 20)
        self.assertEqual(self.tracker.status, "recording")
        excluded = self.store.db.execute("SELECT * FROM excluded").fetchone()
        self.assertEqual(excluded["start"], 1010)
        self.assertEqual(excluded["end"], 1080)

    def test_idle_trims_earlier_target_segments_too(self):
        self.advance(1000, 1010)
        self.advance(1011, 1030, last_input=1010)
        self.advance(1031, 1070, "figma.exe", last_input=1010)
        self.assertEqual(self.total(), 10)
        self.assertEqual(len(self.store.sessions()), 1)

    def test_idle_does_not_trim_demo_or_direct_entries(self):
        self.store.add_session("code", 1010, 1040, "demo")
        self.store.add_session("code", 1010, 1040, "manual")
        self.advance(1000, 1070, last_input=1000)
        self.assertEqual(self.total(), 60)

    def test_suspended_time_is_not_counted(self):
        self.advance(1000, 1010)
        self.tick(2000)
        self.advance(2001, 2010)
        self.assertEqual(self.total(), 20)

    def test_backwards_clock_does_not_create_negative_duration(self):
        self.advance(1000, 1010)
        self.tick(1005)
        self.assertTrue(all(s["end"] >= s["start"] for s in self.store.sessions()))

    def test_automatic_tracking_pauses_when_idle_and_resolves_foreground_on_return(self):
        self.advance(1000, 1010, "figma.exe")
        self.advance(1011, 1070, "figma.exe", last_input=1010)
        self.assertEqual(self.total(), 10)
        self.assertEqual(self.tracker.status, "away")
        self.tick(1071, "code.exe")
        self.assertEqual(self.tracker.active, "code")

    def test_browser_requires_fresh_event_and_matching_foreground(self):
        self.tracker.browser_update({"browser": "chrome.exe", "url": "https://docs.google.com/document/d/123/edit", "focused": True})
        self.tick(1000, "chrome.exe")
        self.assertEqual(self.tracker.active, "docs")
        self.tick(1001, "other.exe")
        self.assertEqual(self.tracker.status, "waiting")
        self.tick(1041, "chrome.exe")
        self.assertEqual(self.tracker.status, "waiting")

    def test_browser_cannot_steal_time_from_another_browser(self):
        self.tracker.browser_update({"browser": "chrome.exe", "url": "https://docs.google.com/document/d/123", "focused": True})
        self.tick(1000, "msedge.exe")
        self.assertIsNone(self.tracker.active)

    def test_more_specific_url_wins(self):
        self.store.db.execute("INSERT INTO targets VALUES ('specific','My doc','url','https://docs.google.com/document/d/123','#9b87c4','globe',1,0)")
        self.store.db.commit()
        self.tracker.browser_update({"browser": "chrome.exe", "url": "https://docs.google.com/document/d/123/edit", "focused": True})
        self.tick(1000, "chrome.exe")
        self.assertEqual(self.tracker.active, "specific")

    def test_midnight_splits_totals(self):
        start = datetime(2026, 10, 15, 23, 30, tzinfo=KST).timestamp()
        self.store.add_session("code", start, start + 7200, "manual")
        daily = self.store.daily()
        self.assertEqual(daily["2026-10-15"]["total"], 1800)
        self.assertEqual(daily["2026-10-16"]["total"], 5400)

    def test_daily_sum_equals_sessions_sum(self):
        start = datetime(2026, 10, 15, 23, 30, tzinfo=KST).timestamp()
        self.store.add_session("code", start, start + 172800, "manual")
        self.assertEqual(sum(d["total"] for d in self.store.daily().values()), 172800)

    def test_restart_never_counts_server_downtime(self):
        self.advance(1000, 1010)
        restarted = Tracker(self.store, FakeActivity(), clock=lambda: 2000)
        restarted.tick()
        self.assertFalse(restarted.enabled)
        self.assertEqual(self.total(), 10)

    def test_enabling_while_already_idle_excludes_only_armed_period(self):
        self.tracker.enabled = False
        self.store.add_session("code", 1000, 1010, "auto")
        self.now = 2000
        self.tracker.activity.sample = lambda: {"exe": "code.exe", "idle": 900}
        self.tracker.set_enabled(True)
        excluded = self.store.db.execute("SELECT * FROM excluded").fetchone()
        self.assertEqual(excluded["start"], 2000)
        self.assertEqual(self.total(), 10)

    def test_pausing_while_idle_does_not_shorten_excluded_interval(self):
        self.advance(1000, 1070, last_input=1000)
        self.now = 1090
        self.tracker.activity.sample = lambda: {"exe": "code.exe", "idle": 90}
        self.tracker.set_enabled(False)
        excluded = self.store.db.execute("SELECT * FROM excluded").fetchone()
        self.assertEqual(excluded["end"], 1090)


class MatchingTests(unittest.TestCase):
    def test_url_boundary(self):
        rule = "https://docs.google.com/document/"
        self.assertTrue(url_matches("https://docs.google.com/document/d/123?tab=a#section", rule))
        self.assertTrue(url_matches("https://docs.google.com/document", rule))
        self.assertFalse(url_matches("https://docs.google.com/documentation", rule))
        self.assertFalse(url_matches("https://docs.google.com.evil.test/document/", rule))
        self.assertFalse(url_matches("file:///document/", rule))
        self.assertFalse(url_matches("https://docs.google.com:9999/document/", rule))
        self.assertTrue(url_matches("https://docs.google.com:443/document/", rule))
        self.assertFalse(url_matches("https://docs.google.com:bad/document/", rule))

    def test_executable_validation(self):
        result = validate_target({"name": "블렌더", "kind": "app", "matcher": "C:\\Apps\\Blender\\blender.exe"})
        self.assertEqual(result[2], "blender.exe")
        self.assertEqual(validate_target({"name": "코딩", "kind": "app", "matcher": "Code"})[2], "code.exe")
        with self.assertRaises(ValueError):
            validate_target({"name": "앱", "kind": "app", "matcher": "bad*.exe"})


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(":memory:", seed=False)
        cls.tracker = Tracker(cls.store, FakeActivity())
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.tracker, 0))
        cls.port = cls.server.server_port
        cls.server.RequestHandlerClass = make_handler(cls.tracker, cls.port)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()
        cls.store.db.close()

    def call(self, path, data=None, headers=None):
        request = Request(f"http://127.0.0.1:{self.port}/api/{path}", data=json.dumps(data).encode() if data is not None else None, headers={"Content-Type": "application/json", **(headers or {})})
        with urlopen(request) as response:
            return json.load(response)

    def test_extension_token_required(self):
        with self.assertRaises(HTTPError) as error:
            self.call("browser", {"url": "", "focused": False})
        self.assertEqual(error.exception.code, 401)
        code = self.store.settings()["extension_token"]
        response = self.call("browser", {"url": "", "focused": False}, {"X-Moa-Token": code, "Origin": "chrome-extension://test"})
        self.assertTrue(response["ok"])

    def test_external_origin_cannot_change_tracking(self):
        with self.assertRaises(HTTPError) as error:
            self.call("tracking", {"enabled": True}, {"Origin": "https://evil.example"})
        self.assertEqual(error.exception.code, 403)

    def test_target_and_record_persist(self):
        state = self.call("targets", {"name": "메모장", "kind": "app", "matcher": "notepad.exe"})
        target = next(t for t in state["targets"] if t["name"] == "메모장")
        self.store.add_session(target["id"], datetime(2026,10,12,23,30,tzinfo=KST).timestamp(), datetime(2026,10,13,1,0,tzinfo=KST).timestamp(), "auto")
        result = self.call("state")
        self.assertEqual(result["daily"]["2026-10-12"]["targets"][target["id"]], 1800)
        self.assertEqual(result["daily"]["2026-10-13"]["targets"][target["id"]], 3600)

    def test_manual_time_endpoints_are_disabled(self):
        for endpoint in ("timer", "sessions", "session-delete"):
            with self.assertRaises(HTTPError) as error:
                self.call(endpoint, {"target_id":"code", "minutes":60})
            self.assertEqual(error.exception.code, 410)


if __name__ == "__main__":
    unittest.main()
