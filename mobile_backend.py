"""Shared Python domain model behind the native Android views and service."""
from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
import json
from pathlib import Path
import re
import sqlite3
import time

from schedules import archive_assignment, assignment_totals, change_assignment_status, save_assignment
from sync_core import new_pairing_key
from tracker import COLORS, KST, Store, Tracker, key


class AndroidActivity:
    supported = True
    platform = "android"
    package = ""

    def sample(self):
        return {"exe": self.package, "idle": 0}


class MobileBackend:
    def __init__(self, directory, load_config=None, save_config=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.directory / "moa.sqlite3")
        self.activity = AndroidActivity()
        self.tracker = Tracker(self.store, self.activity)
        self.service = None
        self.backup_day = None
        self.backup()
        if load_config is not None:
            from telegram_sync import SyncService
            self.service = SyncService(self.store, self.tracker.lock, load_config, save_config,
                                       backup=lambda: self.backup(force=True), platform="Android")

    def backup(self, force=False):
        day = datetime.now(KST).date().isoformat()
        if not force and day == self.backup_day:
            return
        backups = self.directory / "backups"
        backups.mkdir(exist_ok=True)
        path = backups / (f"before-sync-{int(time.time())}.sqlite3" if force else f"moa-{day}.sqlite3")
        with self.tracker.lock, closing(sqlite3.connect(path)) as destination:
            self.store.db.backup(destination)
        self.backup_day = day

    def tick(self, package, screen_active, enabled=True, now=None):
        with self.tracker.lock:
            now = time.time() if now is None else now
            self.activity.package = package or ""
            if not enabled or not screen_active:
                self.tracker.stop_segment(min(now, self.tracker.last_tick + 1))
                self.tracker.last_tick = now
                self.tracker.enabled = enabled
                self.tracker.status = "waiting" if enabled else "off"
            else:
                if not self.tracker.enabled:
                    self.tracker.run_start = now
                self.tracker.enabled = True
                self.tracker.tick(now=now)
        self.backup()

    def state(self):
        result = self.tracker.state()
        result["sync"] = self.service.status() if self.service else {"connected": False, "message": "연동 안 됨"}
        return result

    def command(self, action, data):
        if action.startswith("sync-"):
            if not self.service:
                raise ValueError("연동 서비스를 사용할 수 없습니다.")
            return self.service.submit(action[5:], data).result(180)
        if action == "new-key":
            return {"pairing_key": new_pairing_key()}
        with self.tracker.lock:
            db = self.store.db
            if action == "target":
                package = str(data.get("matcher", ""))
                name = str(data.get("name", "")).strip()
                if not re.fullmatch(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+", package) or not name or len(name) > 48:
                    raise ValueError("추적할 앱을 다시 선택해주세요.")
                existing = db.execute("SELECT id FROM targets WHERE platform='android' AND matcher=? AND archived=0", (package,)).fetchone()
                if existing:
                    db.execute("UPDATE targets SET enabled=1 WHERE id=?", (existing[0],))
                else:
                    color = COLORS[len(self.store.targets()) % len(COLORS)]
                    db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon,platform) VALUES(?,?,'app',?,?,'app','android')", (key(), name, package, color))
                db.commit()
            elif action in ("target-toggle", "target-delete"):
                if action == "target-toggle":
                    db.execute("UPDATE targets SET enabled=? WHERE id=? AND platform='android' AND archived=0", (int(bool(data.get("enabled"))), data.get("id")))
                else:
                    db.execute("UPDATE targets SET archived=1,enabled=0 WHERE id=? AND platform='android'", (data.get("id"),))
                db.commit()
            elif action == "assignment":
                save_assignment(db, data)
            elif action == "assignment-status":
                change_assignment_status(db, data.get("id"), data.get("status"))
            elif action == "assignment-delete":
                archive_assignment(db, data.get("id"))
            elif action == "settings":
                grace = data.get("switch_grace_minutes")
                if type(grace) is not int or grace not in (0, 1, 3, 5, 10, 15, 30):
                    raise ValueError("이어 기록 시간을 확인해주세요.")
                self.store.set_setting("switch_grace_minutes", grace)
            elif action == "totals":
                start, end = data.get("start"), data.get("end")
                for value in (start, end):
                    if value:
                        date.fromisoformat(value)
                items = [dict(row) for row in db.execute("SELECT * FROM assignments")]
                return assignment_totals(items, start, end)
            else:
                raise ValueError("요청한 기능을 찾을 수 없습니다.")
        return {"ok": True}


_backend = None


def initialize(directory):
    global _backend
    if _backend is None:
        from java import jclass
        vault = jclass("com.verylovefurry.moa.SecureStore")
        def load():
            return json.loads(str(vault.read()))
        def save(config):
            vault.write(json.dumps(config))
        _backend = MobileBackend(directory, load, save)
    return True


def state():
    return json.dumps(_backend.state(), ensure_ascii=False)


def tick(package, screen_active, enabled):
    _backend.tick(str(package), bool(screen_active), bool(enabled))


def command(action, payload):
    try:
        result = _backend.command(str(action), json.loads(str(payload)))
        return json.dumps({"ok": True, "result": result}, ensure_ascii=False)
    except (ValueError, RuntimeError, TimeoutError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    except (sqlite3.Error, OSError):
        return json.dumps({"ok": False, "error": "기록을 저장하지 못했습니다. 저장 공간을 확인해주세요."}, ensure_ascii=False)
