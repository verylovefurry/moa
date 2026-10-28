"""Desktop host for the existing tracker and extension API. No browser UI."""
from __future__ import annotations

import csv
from contextlib import closing
from datetime import datetime
from http.server import ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import make_handler
from tracker import KST, Store, Tracker, split_by_day


def desktop_data_directory() -> Path:
    if os.environ.get("MOA_DATA_DIR"):
        return Path(os.environ["MOA_DATA_DIR"])
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Moa"


def migrate_legacy_database(destination: Path) -> bool:
    """Use SQLite's backup API so a WAL-backed prototype database stays intact."""
    if destination.exists() or os.environ.get("MOA_DATA_DIR"):
        return False
    candidates = [Path(__file__).resolve().parent / "data" / "moa.sqlite3"]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent.parent / "data" / "moa.sqlite3")
    for source in candidates:
        if source.is_file() and source.resolve() != destination.resolve():
            with closing(sqlite3.connect(str(source))) as old, closing(sqlite3.connect(str(destination))) as new:
                old.backup(new)
            return True
    return False


def format_duration(seconds: float, precise=False) -> str:
    seconds = max(0, int(seconds))
    if precise and seconds < 60:
        return f"{seconds}초"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours}시간 {minutes:02d}분" if hours else f"{minutes}분"


def format_clock(seconds: float) -> str:
    hours, rest = divmod(max(0, int(seconds)), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, KST).strftime("%H:%M")


def csv_safe(value) -> str:
    value = str(value or "")
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


class DesktopBackend:
    def __init__(self, data_directory=None, port=3847, seed=False, auto_track=True, recovery_plan=None):
        self.data_dir = Path(data_directory) if data_directory else desktop_data_directory()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if data_directory is None:
            migrate_legacy_database(self.data_dir / "moa.sqlite3")
        self.store = Store(self.data_dir / "moa.sqlite3", seed=seed)
        self.tracker = Tracker(self.store)
        self.backup_dir = self.data_dir / "backups"
        self.backup_dir.mkdir(exist_ok=True)
        self._backup_day = None
        self.recovery_result = None
        if recovery_plan:
            from recovery import apply_gap_plan
            plan_path = Path(recovery_plan)
            try:
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                if Path(plan["data_directory"]).resolve() != self.data_dir.resolve():
                    raise ValueError("복원 계획의 데이터 저장 위치가 다릅니다.")
                with self.tracker.lock:
                    self.recovery_result = apply_gap_plan(self.store, plan, self.backup_dir)
                (self.data_dir / "gap-recovery-result.json").write_text(json.dumps(self.recovery_result, ensure_ascii=False, indent=2), encoding="utf-8")
                plan_path.unlink()
            except Exception as exc:
                self.recovery_result = {"error": str(exc)}
                logging.exception("Approved gap recovery deferred")
        self.backup_daily()
        self.stop_event = threading.Event()
        self.extension_dir = self.data_dir / "browser-extension"
        self.extension_dir.mkdir(exist_ok=True)
        bundled = Path(__file__).resolve().parent / "browser-extension"
        if bundled.is_dir() and bundled.resolve() != self.extension_dir.resolve():
            for filename in ("manifest.json", "background.js", "popup.html", "popup.css", "popup.js"):
                source = bundled / filename
                if source.is_file():
                    shutil.copy2(source, self.extension_dir / filename)
        self.tracker.extension_path = str(self.extension_dir)
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(self.tracker, port))
        except OSError as exc:
            self.store.db.close()
            raise RuntimeError("기록 서비스의 3847 포트가 사용 중입니다. 이전 모아 실행 창을 종료한 후 다시 실행해주세요.") from exc
        self.port = self.server.server_port
        if port == 0:
            self.server.RequestHandlerClass = make_handler(self.tracker, self.port)
        self.server.daemon_threads = True
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.tracker_thread = threading.Thread(target=self.tracker.loop, args=(self.stop_event,), daemon=True)
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.closed = False
        self.sync_service = None
        if (self.data_dir / "telegram-sync.dpapi").exists():
            self.get_sync_service()
        if auto_track:
            self.tracker.set_enabled(True)
        self.http_thread.start()
        self.tracker_thread.start()
        self.maintenance_thread.start()

    def state(self):
        return self.tracker.state()

    def get_sync_service(self):
        if self.sync_service is None:
            from sync_secrets import WindowsSyncVault
            from telegram_sync import SyncService
            vault = WindowsSyncVault(self.data_dir)
            self.sync_service = SyncService(self.store, self.tracker.lock, vault.load, vault.save,
                backup=lambda: self.backup_to(self.backup_dir / "before-first-sync.sqlite3"))
        return self.sync_service

    def command(self, command: str, payload: dict):
        # Reuse the tested mutation endpoints, including validation and idle rules.
        request = Request(
            f"http://127.0.0.1:{self.port}/api/{command}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ValueError(json.load(exc).get("error", "요청을 처리하지 못했습니다.")) from exc
        except OSError as exc:
            raise RuntimeError("기록 서비스에 연결하지 못했습니다. 앱을 다시 실행해주세요.") from exc

    def running_apps(self):
        return self.tracker.activity.running_apps()

    def backup_to(self, destination):
        destination = Path(destination)
        pending = destination.with_suffix(".pending")
        with self.tracker.lock:
            with closing(sqlite3.connect(str(pending))) as backup:
                self.store.db.backup(backup)
            pending.replace(destination)
        return destination

    def backup_daily(self):
        day = datetime.now(KST).strftime("%Y-%m-%d")
        if self._backup_day == day:
            return
        destination = self.backup_dir / f"moa-{day}.sqlite3"
        if not destination.exists():
            self.backup_to(destination)
        self._backup_day = day

    def _maintenance_loop(self):
        while not self.stop_event.wait(60):
            try:
                self.backup_daily()
            except (OSError, sqlite3.Error):
                logging.exception("Daily backup failed")

    def flush(self):
        if self.closed:
            return
        with self.tracker.lock:
            self.tracker.tick()
            self.store.db.commit()
            self.store.db.execute("PRAGMA wal_checkpoint(FULL)")

    def export_csv(self, path: Path, year: int, month: int) -> int:
        snapshot = self.state()
        names = {t["id"]: t["name"] for t in snapshot["targets"]}
        sources = {"demo": "샘플", "auto": "자동", "timer": "이전 수동 타이머", "manual": "이전 수동 기록"}
        prefix = f"{year}-{month:02d}"
        count = 0
        with Path(path).open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["날짜", "작업", "시작", "종료", "작업 시간(분)", "기록 방식", "메모"])
            for session in snapshot["sessions"]:
                for day, start, end in split_by_day(session["start"], session["end"]):
                    if day.startswith(prefix):
                        writer.writerow([
                            day, csv_safe(names.get(session["target_id"], "작업")),
                            format_time(start), format_time(end), f"{(end - start) / 60:.2f}",
                            sources[session["source"]] + (" · 추정 복원 포함" if session.get("recovered_seconds", 0) else ""),
                            csv_safe(session["note"]),
                        ])
                        count += 1
        return count

    def close(self):
        if self.closed:
            return
        if self.sync_service:
            self.sync_service.close()
        self.stop_event.set()
        self.tracker_thread.join(3)
        self.maintenance_thread.join(3)
        self.server.shutdown()
        self.server.server_close()
        self.http_thread.join(3)
        with self.tracker.lock:
            self.tracker.set_enabled(False)
            self.tracker.close_idle(self.tracker.clock())
            self.store.db.execute("PRAGMA wal_checkpoint(FULL)")
            try:
                self.backup_to(self.backup_dir / "latest.sqlite3")
            except (OSError, sqlite3.Error):
                logging.exception("Shutdown backup failed; live database is already committed")
            self.store.db.close()
            self.closed = True
