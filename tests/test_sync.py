import asyncio
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mobile_backend import MobileBackend
from schedules import assignment_totals, save_assignment
from sync_core import SyncJournal, decode_batch, encode_batch, new_pairing_key
from telegram_sync import SyncEngine, SyncService
from tracker import Store, Tracker


class FakeCloud:
    """Append-only Saved Messages substitute, including a lost send acknowledgement."""
    def __init__(self):
        self.messages = []
        self.fail_after_send = False
        self.key = new_pairing_key()

    async def receive(self, after):
        for message_id, payload in list(self.messages):
            if message_id > after:
                yield message_id, decode_batch(payload, self.key)

    async def send(self, batch):
        self.messages.append((len(self.messages) + 1, encode_batch(batch, self.key)))
        if self.fail_after_send:
            self.fail_after_send = False
            raise OSError("connection lost after upload")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.cloud = FakeCloud()
        self.stores = [Store(":memory:"), Store(":memory:")]
        self.journals = [SyncJournal(s.db) for s in self.stores]
        self.engines = [SyncEngine(j, threading.RLock(), self.cloud) for j in self.journals]
        for journal in self.journals:
            journal.connect(self.cloud.key)

    def tearDown(self):
        for store in self.stores:
            store.db.close()

    def sync(self, device):
        return asyncio.run(self.engines[device].synchronize())

    def target(self, device=0, platform="windows"):
        db = self.stores[device].db
        db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon,platform) VALUES('t','작업','app',?,'#336699','app',?)", ("example.editor" if platform == "android" else "editor.exe", platform))
        db.commit()

    def assignment(self, device=0):
        return save_assignment(self.stores[device].db, {"title": "일러스트", "client": "고객", "start_date": "2026-10-01", "fee": 150000, "net_fee": 132912, "status": "paid"}, now=1000)

    def test_existing_records_bootstrap_both_directions_and_no_echo(self):
        # Capture may be enabled after years of local records, rather than before inserts.
        self.stores[0].db.execute("UPDATE sync_control SET enabled=0")
        self.target()
        self.stores[0].add_session("t", 1000, 1600, "auto")
        aid = self.assignment()
        self.journals[0].connect(self.cloud.key)
        self.sync(0)
        self.sync(1)
        self.assertEqual(self.stores[1].daily(), self.stores[0].daily())
        self.assertEqual(self.stores[1].db.execute("SELECT net_fee FROM assignments WHERE id=?", (aid,)).fetchone()[0], 132912)
        count = len(self.cloud.messages)
        self.sync(0); self.sync(1)
        self.assertEqual(len(self.cloud.messages), count)

    def test_offline_edits_to_different_fields_merge(self):
        aid = self.assignment(); self.sync(0); self.sync(1)
        self.stores[0].db.execute("UPDATE assignments SET title='PC 수정' WHERE id=?", (aid,)); self.stores[0].db.commit()
        self.stores[1].db.execute("UPDATE assignments SET net_fee=130000 WHERE id=?", (aid,)); self.stores[1].db.commit()
        self.sync(0); self.sync(1); self.sync(0)
        for store in self.stores:
            row = store.db.execute("SELECT * FROM assignments WHERE id=?", (aid,)).fetchone()
            self.assertEqual((row["title"], row["net_fee"]), ("PC 수정", 130000))

    def test_same_field_converges_by_message_order_and_keeps_audit(self):
        aid = self.assignment(); self.sync(0); self.sync(1)
        for device, title in ((0, "먼저 업로드"), (1, "나중 업로드")):
            self.stores[device].db.execute("UPDATE assignments SET title=? WHERE id=?", (title, aid)); self.stores[device].db.commit()
        self.sync(0); self.sync(1); self.sync(0)
        for store in self.stores:
            self.assertEqual(store.db.execute("SELECT title FROM assignments WHERE id=?", (aid,)).fetchone()[0], "나중 업로드")
        self.assertGreater(self.stores[1].db.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0], 0)

    def test_unknown_send_result_does_not_duplicate_or_lose_data(self):
        self.target(); self.cloud.fail_after_send = True
        with self.assertRaises(OSError): self.sync(0)
        self.assertEqual(len(self.cloud.messages), 1)
        self.sync(0); self.sync(1)
        self.assertEqual(len(self.cloud.messages), 1)
        self.assertEqual(len(self.stores[1].targets()), 1)

    def test_changes_during_upload_remain_pending(self):
        self.target(); self.sync(0); self.sync(1)
        store = self.stores[0]; sid = store.add_session("t", 1000, 1010, "auto")
        pending = self.journals[0].pending()
        store.finish(sid, 1020)
        asyncio.run(self.cloud.send(pending)); asyncio.run(self.engines[0].pull())
        self.assertEqual(store.sessions()[0]["end"], 1020)
        self.assertGreater(self.journals[0].status()["pending"], 0)
        self.sync(0); self.sync(1)
        self.assertEqual(self.stores[1].sessions()[0]["end"], 1020)

    def test_idle_trim_deletions_and_shortenings_propagate(self):
        self.target(); first = self.stores[0].add_session("t", 1000, 1100, "auto"); self.stores[0].add_session("t", 1101, 1200, "auto")
        self.sync(0); self.sync(1)
        self.stores[0].trim_idle(1050, 1000); self.sync(0); self.sync(1)
        self.assertEqual([(s["id"],s["end"]) for s in self.stores[1].sessions()], [(first,1050)])

    def test_replay_and_duplicate_batch_are_idempotent(self):
        self.target(); self.sync(0); self.sync(1)
        old = decode_batch(self.cloud.messages[0][1], self.cloud.key)
        self.stores[0].db.execute("UPDATE targets SET name='새 이름'"); self.stores[0].db.commit(); self.sync(0); self.sync(1)
        self.journals[1].apply(old, 100)
        self.assertEqual(self.stores[1].targets()[0]["name"], "새 이름")

    def test_invalid_batch_rolls_back_and_keeps_cursor(self):
        self.target(); batch = self.journals[0].pending(); invalid = deepcopy(batch)
        invalid["changes"][0]["fields"]["platform"] = "executable-code"
        before = self.journals[1].get("cursor",0)
        with self.assertRaises(ValueError): self.journals[1].apply(invalid,1)
        self.assertEqual(self.stores[1].targets(), [])
        self.assertEqual(self.journals[1].get("cursor",0), before)
        self.assertEqual(self.stores[1].db.execute("SELECT applying FROM sync_control").fetchone()[0],0)

    def test_encryption_tampering_wrong_key_and_large_payload(self):
        self.target(); payload = encode_batch(self.journals[0].pending(), self.cloud.key)
        self.assertNotIn("작업".encode(), payload)
        for corrupt, key in ((payload,new_pairing_key()), (payload[:-1]+bytes([payload[-1]^1]),self.cloud.key), (b"wrong",self.cloud.key)):
            with self.assertRaises(ValueError): decode_batch(corrupt,key)

    def test_journal_coalesces_per_second_updates(self):
        self.target(); sid = self.stores[0].add_session("t",1000,1000,"auto")
        for now in range(1001,1300): self.stores[0].finish(sid,now)
        self.assertLess(self.stores[0].db.execute("SELECT COUNT(*) FROM sync_dirty").fetchone()[0],20)

    def test_active_timer_does_not_create_an_upload_loop(self):
        self.target();sid=self.stores[0].add_session("t",1000,1001,"auto")
        original_send=self.cloud.send
        async def send_while_recording(batch):
            await original_send(batch)
            current=self.stores[0].sessions()[0]["end"]
            self.stores[0].finish(sid,current+1)
        self.cloud.send=send_while_recording
        self.sync(0)
        self.assertEqual(len(self.cloud.messages),1)
        self.assertGreater(self.journals[0].status()["pending"],0)

    def test_disconnect_never_deletes_business_records(self):
        self.target(); self.journals[0].disconnect()
        self.assertEqual(len(self.stores[0].targets()),1)
        self.assertEqual(self.journals[0].status()["pending"],0)

    def test_large_initial_history_crosses_batches_and_preserves_recovery(self):
        self.target()
        for index in range(520):
            self.stores[0].add_session("t",1000+index*100,1060+index*100,"auto")
        first, second = self.stores[0].sessions()[:2]
        self.stores[0].db.execute("INSERT INTO gap_recoveries VALUES(?,?,?,?,?)",(first["id"],second["id"],1050,1060,60000))
        self.stores[0].db.commit()
        self.sync(0); self.sync(1)
        self.assertGreater(len(self.cloud.messages),2)
        self.assertEqual(self.stores[0].sessions(),self.stores[1].sessions())

    def test_offline_date_ranges_remain_valid(self):
        aid=self.assignment();self.sync(0);self.sync(1)
        self.stores[0].db.execute("UPDATE assignments SET start_date='2026-11-01',due_date='2026-11-02' WHERE id=?",(aid,));self.stores[0].db.commit()
        self.stores[1].db.execute("UPDATE assignments SET due_date='2026-10-10' WHERE id=?",(aid,));self.stores[1].db.commit()
        self.sync(0);self.sync(1);self.sync(0)
        for store in self.stores:
            row=store.db.execute("SELECT start_date,due_date FROM assignments WHERE id=?",(aid,)).fetchone()
            self.assertEqual(tuple(row),('2026-10-01','2026-10-10'))


class MobileTests(unittest.TestCase):
    def test_legacy_database_migrates_without_changing_existing_records(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"legacy.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.executescript("""CREATE TABLE targets(id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL,matcher TEXT NOT NULL,color TEXT NOT NULL,icon TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,archived INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE sessions(id TEXT PRIMARY KEY,target_id TEXT NOT NULL REFERENCES targets(id),start REAL NOT NULL,end REAL NOT NULL,source TEXT NOT NULL,note TEXT NOT NULL DEFAULT '');
                    INSERT INTO targets VALUES('t','기존 작업','app','work.exe','#336699','app',1,0);
                    INSERT INTO sessions VALUES('s','t',1000,1600,'auto','기존 메모');""")
            store=Store(path)
            self.assertEqual(store.targets()[0]["platform"],"windows")
            self.assertEqual(store.sessions()[0]["note"],"기존 메모")
            self.assertEqual(store.sessions()[0]["end"],1600)
            self.assertEqual(store.db.execute("SELECT enabled FROM sync_control").fetchone()[0],0)
            store.db.close()

    def test_android_records_screen_off_and_restarts_without_downtime(self):
        with tempfile.TemporaryDirectory() as directory:
            app = MobileBackend(directory)
            app.command("target", {"name":"편집 앱","matcher":"example.editor"})
            for now in range(1000,1061): app.tick("example.editor",True,now=now)
            app.tick("example.editor",False,now=1061)
            for now in range(1062,1080): app.tick("example.editor",False,now=now)
            seconds = sum(s["end"]-s["start"] for s in app.store.sessions())
            self.assertEqual(seconds,61)
            app.store.db.close()
            app = MobileBackend(directory)
            for now in range(2000,2011): app.tick("example.editor",True,now=now)
            self.assertEqual(sum(s["end"]-s["start"] for s in app.store.sessions()),71)
            app.store.db.close()

    def test_windows_idle_never_trims_android_records(self):
        store=Store(":memory:")
        store.db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon,platform) VALUES('a','Android','app','example.editor','#336699','app','android')")
        store.add_session("a",1000,2000,"auto")
        store.trim_idle(1100,1000)
        self.assertEqual(store.sessions()[0]["end"],2000)
        class Android:
            platform="android"
            supported=True
        self.assertIsNotNone(Tracker(store,Android()).match_target("example.editor",1000))
        self.assertIsNone(Tracker(store).match_target("example.editor",1000))
        store.db.close()

    def test_schedule_status_and_net_fee_fallback_same_as_desktop(self):
        with tempfile.TemporaryDirectory() as directory:
            app=MobileBackend(directory)
            aid=save_assignment(app.store.db,{"title":"테스트","start_date":"2026-10-01","fee":150000,"status":"not_started"})
            self.assertEqual(app.command("totals",{})["fee"],0)
            app.command("assignment-status",{"id":aid,"status":"paid"})
            self.assertEqual(app.command("totals",{})["net_fee"],150000)
            app.store.db.close()


class TelegramAuthTests(unittest.TestCase):
    def test_code_then_two_factor_auth_saves_session_without_exposing_secrets(self):
        from telethon.errors import SessionPasswordNeededError
        store=Store(":memory:")
        saved=[]
        service=SyncService(store,threading.RLock(),lambda:{},lambda config:saved.append(dict(config)))
        client=SimpleNamespace(
            send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="in-memory-only")),
            sign_in=AsyncMock(side_effect=[SessionPasswordNeededError(None),None]),
            disconnect=AsyncMock(),session=SimpleNamespace(save=lambda:"private-test-session"))
        async def connect(config):service.client=client
        try:
            with patch.object(service,"_new_client",side_effect=connect),patch.object(service,"_sync",new=AsyncMock()):
                key=new_pairing_key()
                result=service.submit("setup",{"api_id":"123456","api_hash":"a"*32,"phone":"+821012345678","pairing_key":key}).result(3)
                self.assertEqual(result["phase"],"code")
                self.assertEqual(saved,[])
                self.assertEqual(service.submit("code",{"value":"12345"}).result(3)["phase"],"password")
                self.assertEqual(service.submit("password",{"value":"secret-test-password"}).result(3)["phase"],"ready")
                self.assertEqual(saved[-1]["session"],"private-test-session")
                self.assertNotIn("private-test-session",json.dumps(service.status()))
                self.assertNotIn("secret-test-password",json.dumps(saved))
                service.submit("disconnect").result(3)
                self.assertEqual(saved[-1],{})
        finally:
            service.close();store.db.close()

    def test_cancel_auth_allows_requesting_a_new_code(self):
        store=Store(":memory:")
        service=SyncService(store,threading.RLock(),lambda:{},lambda value:None)
        try:
            service.phone_code_hash="test-hash"
            self.assertEqual(service.submit("cancel-auth").result(3)["phase"],"setup")
            self.assertIsNone(service.phone_code_hash)
        finally:
            service.close();store.db.close()



if __name__ == "__main__":
    unittest.main()
