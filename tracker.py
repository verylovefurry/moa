"""Local-first time tracking. No third-party dependencies or keystroke capture."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit

from schedules import ensure_schedule_schema, schedule_state
from recovery import ensure_recovery_schema

KST = timezone(timedelta(hours=9))
COLORS = ["#9b87c4", "#7798bd", "#d4a36b", "#78a38c", "#cf8a8a", "#84939d"]
BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "arc.exe"}
SWITCH_GRACE_MINUTES = (0, 1, 3, 5, 10, 15, 30)


def key() -> str:
    return uuid.uuid4().hex[:16]


def url_matches(url: str, pattern: str) -> bool:
    """Match the exact host and a path prefix; ignore query strings and fragments."""
    try:
        candidate = urlsplit(url)
        rule = urlsplit(pattern if "://" in pattern else "https://" + pattern)
        if candidate.scheme not in ("http", "https") or not rule.hostname:
            return False
        candidate_port = candidate.port
        rule_port = rule.port
        if candidate_port == {"https": 443, "http": 80}.get(candidate.scheme):
            candidate_port = None
        if rule_port == {"https": 443, "http": 80}.get(rule.scheme):
            rule_port = None
        if candidate.hostname != rule.hostname or candidate_port != rule_port:
            return False
        prefix = rule.path.rstrip("/")
        return not prefix or candidate.path == prefix or candidate.path.startswith(prefix + "/")
    except (ValueError, AttributeError):
        return False


def split_by_day(start: float, end: float):
    cursor = start
    while cursor < end:
        day = datetime.fromtimestamp(cursor, KST)
        boundary = (day.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()
        stop = min(end, boundary)
        yield day.strftime("%Y-%m-%d"), cursor, stop
        cursor = stop


class WindowsActivity:
    supported = os.name == "nt"

    def __init__(self):
        if not self.supported:
            return
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.GetTickCount.restype = wintypes.DWORD

    def process_for_window(self, hwnd):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self.kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if self.kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return Path(buffer.value).name.lower()
            return ""
        finally:
            self.kernel.CloseHandle(handle)

    def sample(self):
        if not self.supported:
            return {"exe": "", "idle": 0.0}

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(info)
        if not self.user.GetLastInputInfo(ctypes.byref(info)):
            # Fail closed: an unavailable input clock must not count idle time.
            return {"exe": "", "idle": float("inf")}
        idle = ((self.kernel.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000
        return {"exe": self.process_for_window(self.user.GetForegroundWindow()), "idle": idle}

    def running_apps(self):
        if not self.supported:
            return []
        names = set()
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                name = self.process_for_window(hwnd)
                if name:
                    names.add(name)
            return True

        self.user.EnumWindows(visit, 0)
        return sorted(names)


class Store:
    def __init__(self, path: Path | str, seed=False):
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS targets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                matcher TEXT NOT NULL, color TEXT NOT NULL, icon TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY, target_id TEXT NOT NULL REFERENCES targets(id),
                start REAL NOT NULL, end REAL NOT NULL, source TEXT NOT NULL, note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS excluded (
                id TEXT PRIMARY KEY, start REAL NOT NULL, end REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_time ON sessions(start, end);
        """)
        ensure_schedule_schema(self.db)
        ensure_recovery_schema(self.db)
        fresh = not self.db.execute("SELECT 1 FROM settings WHERE key='initialized'").fetchone()
        if fresh:
            self.set_setting("idle_minutes", 5)
            self.set_setting("daily_goal", 6)
            self.set_setting("extension_token", secrets.token_hex(3).upper())
            self.set_setting("initialized", True)
        self.db.execute("INSERT OR IGNORE INTO settings VALUES ('switch_grace_minutes','5')")
        self.db.commit()
        self.remove_legacy_defaults()

    def settings(self):
        return {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT * FROM settings")}

    def set_setting(self, name, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (name, json.dumps(value)))
        self.db.commit()

    def targets(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM targets ORDER BY rowid")]

    def sessions(self):
        return [dict(r) for r in self.db.execute("""SELECT s.*,
            CASE WHEN g.session_id IS NULL THEN 0
                 ELSE MAX(0,MIN(s.end,g.restored_end)-g.original_end) END AS recovered_seconds
            FROM sessions s LEFT JOIN gap_recoveries g ON g.session_id=s.id ORDER BY s.start""")]

    def remove_legacy_defaults(self):
        """Archive unchanged built-ins once; preserve their history and user edits."""
        if self.settings().get("default_targets_removed_v1"):
            return
        defaults = (
            ("figma", "Figma", "app", "figma.exe"),
            ("code", "Visual Studio Code", "app", "code.exe"),
            ("docs", "Google 문서", "url", "https://docs.google.com/document/"),
            ("sheets", "Google 스프레드시트", "url", "https://docs.google.com/spreadsheets/"),
        )
        with self.db:
            self.db.executemany("UPDATE targets SET enabled=0,archived=1 WHERE id=? AND name=? AND kind=? AND matcher=?", defaults)
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('default_targets_removed_v1','true')")

    def add_session(self, target_id, start, end, source, note=""):
        sid = key()
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (sid, target_id, start, end, source, note))
        self.db.commit()
        return sid

    def finish(self, sid, end):
        self.db.execute("UPDATE sessions SET end=MAX(start, ?) WHERE id=?", (end, sid))
        self.db.commit()

    def trim_idle(self, cutoff, run_start):
        # Include earlier target segments: the threshold can span a target change.
        self.db.execute("DELETE FROM sessions WHERE source IN ('auto','timer') AND start>=? AND start>=?", (cutoff, run_start))
        self.db.execute("UPDATE sessions SET end=? WHERE source IN ('auto','timer') AND end>? AND start>=?", (cutoff, cutoff, run_start))
        self.db.commit()

    def daily(self):
        result = {}
        for session in self.sessions():
            for date, start, end in split_by_day(session["start"], session["end"]):
                day = result.setdefault(date, {"total": 0, "targets": {}})
                seconds = end - start
                day["total"] += seconds
                day["targets"][session["target_id"]] = day["targets"].get(session["target_id"], 0) + seconds
        return result


class Tracker:
    def __init__(self, store: Store, activity=None, clock=time.time):
        self.store = store
        self.activity = activity or WindowsActivity()
        self.clock = clock
        self.lock = threading.RLock()
        self.enabled = False
        self.active = None
        self.session_id = None
        self.session_start = None
        self.focus_lost_at = None
        self.run_start = clock()
        self.last_tick = clock()
        self.status = "off"
        self.last_exe = ""
        self.last_idle = 0
        self.idle_id = None
        self.browser_events = {}
        self.error = None

    def stop_segment(self, end):
        if self.session_id:
            self.store.finish(self.session_id, end)
        self.session_id = None
        self.session_start = None
        self.active = None
        self.focus_lost_at = None

    def set_enabled(self, enabled):
        with self.lock:
            was_enabled = self.enabled
            self.tick()
            self.stop_segment(self.clock())
            self.enabled = enabled
            if enabled and not was_enabled:
                self.run_start = self.clock()
            self.tick()

    def browser_update(self, payload):
        browser = payload.get("browser", "chrome.exe")
        if browser not in BROWSERS:
            raise ValueError("지원하지 않는 브라우저입니다.")
        url = str(payload.get("url", ""))[:8192]
        if url and urlsplit(url).scheme not in ("http", "https"):
            raise ValueError("웹 주소를 확인해주세요.")
        self.browser_events[browser] = {"url": url, "focused": bool(payload.get("focused")), "seen": self.clock()}

    def match_target(self, exe, now):
        targets = [t for t in self.store.targets() if t["enabled"] and not t["archived"]]
        if exe in BROWSERS:
            event = self.browser_events.get(exe)
            if event and event["focused"] and now - event["seen"] < 40:
                matches = [t for t in targets if t["kind"] == "url" and url_matches(event["url"], t["matcher"])]
                if matches:
                    return max(matches, key=lambda t: len(t["matcher"]))
        return next((t for t in targets if t["kind"] == "app" and t["matcher"].lower().replace("\\", "/").split("/")[-1] == exe), None)

    def tick(self, now=None, sample=None):
        with self.lock:
            now = self.clock() if now is None else now
            sample = self.activity.sample() if sample is None else sample
            idle = sample["idle"]
            self.last_exe = sample["exe"]
            self.last_idle = idle if idle != float("inf") else 0
            settings = self.store.settings()
            # Never bridge suspended time, a stopped server, or a backwards clock.
            if now - self.last_tick > 10 or now < self.last_tick:
                self.stop_segment(self.last_tick)
            self.last_tick = now
            if not self.enabled:
                self.stop_segment(now)
                self.status = "off"
                self.close_idle(now)
                return
            if idle >= settings["idle_minutes"] * 60:
                if not self.idle_id:
                    cutoff = max(self.run_start, now - idle) if idle != float("inf") else now
                    self.store.trim_idle(cutoff, self.run_start)
                    self.session_id = None
                    self.session_start = None
                    self.active = None
                    self.focus_lost_at = None
                    self.idle_id = key()
                    self.store.db.execute("INSERT INTO excluded VALUES (?,?,?)", (self.idle_id, cutoff, now))
                else:
                    self.store.db.execute("UPDATE excluded SET end=? WHERE id=?", (now, self.idle_id))
                self.store.db.commit()
                self.status = "away"
                return
            self.close_idle(now - idle)
            grace = settings["switch_grace_minutes"] * 60
            if self.focus_lost_at is not None and now >= self.focus_lost_at + grace:
                self.stop_segment(self.focus_lost_at + grace)
            target = self.match_target(sample["exe"], now)
            if not target:
                eligible = self.active and any(t["id"] == self.active and t["enabled"] and not t["archived"] for t in self.store.targets())
                if eligible and grace:
                    if self.focus_lost_at is None:
                        self.focus_lost_at = now
                    self.store.finish(self.session_id, now)
                    self.status = "continuing"
                else:
                    self.stop_segment(now)
                    self.status = "waiting"
                return
            self.focus_lost_at = None
            if self.active != target["id"]:
                self.stop_segment(now)
                self.active = target["id"]
                self.session_start = now
                self.session_id = self.store.add_session(target["id"], now, now, "auto")
            else:
                self.store.finish(self.session_id, now)
            self.status = "recording"

    def close_idle(self, end):
        if self.idle_id:
            self.store.db.execute("UPDATE excluded SET end=MAX(start, ?) WHERE id=?", (end, self.idle_id))
            self.store.db.commit()
            self.idle_id = None

    def state(self):
        with self.lock:
            sessions = self.store.sessions()
            return {
                "today": datetime.now(KST).strftime("%Y-%m-%d"),
                "now": self.clock(), "timezone": "Asia/Seoul",
                "targets": self.store.targets(), "sessions": sessions,
                "daily": self.store.daily(), "settings": self.store.settings(),
                "excluded": [dict(r) for r in self.store.db.execute("SELECT * FROM excluded ORDER BY start")],
                "has_demo": any(s["source"] == "demo" for s in sessions),
                **schedule_state(self.store.db),
                "extension_path": getattr(self, "extension_path", str(Path(__file__).parent / "browser-extension")),
                "tracker": {
                    "enabled": self.enabled, "active": self.active,
                    "status": self.status, "session_start": self.session_start,
                    "exe": self.last_exe, "idle_seconds": self.last_idle,
                    "continuation_remaining": max(0, self.focus_lost_at + self.store.settings()["switch_grace_minutes"] * 60 - self.clock()) if self.focus_lost_at is not None else 0,
                    "windows": self.activity.supported, "error": self.error,
                    "browser_connected": any(self.clock() - e["seen"] < 40 for e in self.browser_events.values()),
                },
            }

    def loop(self, stop):
        while not stop.wait(1):
            try:
                self.tick()
                self.error = None
            except Exception as exc:
                with self.lock:
                    self.stop_segment(self.last_tick)
                    self.error = "감지가 중단되었습니다. 앱을 다시 실행해주세요."
                    self.status = "error"
                print(f"Tracking error: {type(exc).__name__}", flush=True)
