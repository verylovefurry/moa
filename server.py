"""Moa prototype server. Run `python server.py`, then visit localhost:3847."""
from __future__ import annotations

import argparse
from datetime import datetime
import hmac
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit
import webbrowser

from tracker import COLORS, KST, SWITCH_GRACE_MINUTES, Store, Tracker, key
from schedules import save_assignment, change_assignment_status, archive_assignment

ROOT = Path(__file__).resolve().parent


def validate_target(data):
    name = str(data.get("name", "")).strip()
    kind = data.get("kind")
    matcher = str(data.get("matcher", "")).strip()
    if not name or len(name) > 48:
        raise ValueError("작업 이름은 1~48자로 입력해주세요.")
    if kind not in ("app", "url") or not matcher or len(matcher) > 2048:
        raise ValueError("추적할 프로그램 또는 URL을 입력해주세요.")
    if kind == "app":
        matcher = matcher.replace("\\", "/").split("/")[-1].lower()
        if not matcher.endswith(".exe"):
            matcher += ".exe"
        if not re.fullmatch(r"[^<>:\"/\\|?*\x00-\x1f]+\.exe", matcher):
            raise ValueError("프로그램 실행 파일 이름을 확인해주세요. 예: blender.exe")
    else:
        if "://" not in matcher:
            matcher = "https://" + matcher
        parsed = urlsplit(matcher)
        try:
            valid = parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username
            parsed.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("올바른 웹 주소를 입력해주세요.")
        matcher = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
    color = data.get("color", COLORS[0])
    if color not in COLORS:
        raise ValueError("작업 색상을 선택해주세요.")
    icon = str(data.get("icon", "app" if kind == "app" else "globe"))
    if icon not in ("figma", "code", "document", "sheet", "app", "globe", "pen"):
        icon = "app" if kind == "app" else "globe"
    return name, kind, matcher, color, icon


def make_handler(tracker, port):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT / "dist"), **kwargs)

        def log_message(self, fmt, *args):
            if args and str(args[0]).startswith(("POST", "DELETE")):
                print(fmt % args, flush=True)

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            origin = self.headers.get("Origin", "")
            if origin.startswith("chrome-extension://"):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            super().end_headers()

        def host_valid(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def local_origin(self):
            return self.headers.get("Origin", "") in ("", f"http://127.0.0.1:{port}", f"http://localhost:{port}")

        def bridge_authorized(self):
            expected = tracker.store.settings()["extension_token"]
            return hmac.compare_digest(self.headers.get("X-Moa-Token", ""), expected)

        def send_json(self, data, status=200):
            payload = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_OPTIONS(self):
            if not self.host_valid():
                return self.send_json({"error": "허용되지 않은 주소입니다."}, 403)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Moa-Token")
            self.end_headers()

        def do_GET(self):
            if not self.host_valid():
                return self.send_json({"error": "허용되지 않은 주소입니다."}, 403)
            path = urlsplit(self.path).path
            if path == "/api/bridge":
                with tracker.lock:
                    if not self.bridge_authorized():
                        return self.send_json({"error": "연결 코드를 확인해주세요."}, 401)
                    rules = [{"matcher": t["matcher"]} for t in tracker.store.targets() if t["kind"] == "url" and t["enabled"] and not t["archived"]]
                    return self.send_json({"ok": True, "rules": rules, "enabled": tracker.enabled})
            if not self.local_origin():
                return self.send_json({"error": "허용되지 않은 요청입니다."}, 403)
            if path == "/api/state":
                return self.send_json(tracker.state())
            if path == "/api/apps":
                return self.send_json({"apps": tracker.activity.running_apps()})
            if path.startswith("/api/"):
                return self.send_json({"error": "요청한 기능을 찾을 수 없습니다."}, 404)
            return super().do_GET()

        def read_body(self):
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("JSON 형식으로 요청해주세요.")
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 32768:
                raise ValueError("요청 크기를 확인해주세요.")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError("요청 형식을 확인해주세요.")
            return data

        def do_POST(self):
            if not self.host_valid():
                return self.send_json({"error": "허용되지 않은 주소입니다."}, 403)
            path = urlsplit(self.path).path
            if path != "/api/browser" and not self.local_origin():
                return self.send_json({"error": "허용되지 않은 요청입니다."}, 403)
            try:
                data = self.read_body()
                with tracker.lock:
                    if path == "/api/browser":
                        if not self.bridge_authorized():
                            return self.send_json({"error": "연결 코드를 확인해주세요."}, 401)
                        tracker.browser_update(data)
                        return self.send_json({"ok": True})
                    if path == "/api/tracking":
                        if not isinstance(data.get("enabled"), bool):
                            raise ValueError("추적 상태를 확인해주세요.")
                        tracker.set_enabled(data["enabled"])
                    elif path in ("/api/timer", "/api/sessions", "/api/session-delete"):
                        return self.send_json({"error": "작업 시간은 자동 감지로만 기록되며 수동 추가·수정·삭제를 지원하지 않습니다."}, 410)
                    elif path == "/api/targets":
                        fields = validate_target(data)
                        target_id = data.get("id")
                        if target_id:
                            existing = tracker.store.db.execute("SELECT id FROM targets WHERE id=? AND archived=0", (target_id,)).fetchone()
                            if not existing:
                                raise ValueError("추적 대상을 찾을 수 없습니다.")
                            if tracker.active == target_id:
                                tracker.stop_segment(tracker.clock())
                            tracker.store.db.execute("UPDATE targets SET name=?,kind=?,matcher=?,color=?,icon=? WHERE id=?", (*fields, target_id))
                        else:
                            tracker.store.db.execute("INSERT INTO targets(id,name,kind,matcher,color,icon) VALUES (?,?,?,?,?,?)", (key(), *fields))
                        tracker.store.db.commit()
                    elif path == "/api/target-toggle":
                        tracker.store.db.execute("UPDATE targets SET enabled=? WHERE id=? AND archived=0", (int(bool(data.get("enabled"))), data.get("id")))
                        tracker.store.db.commit()
                        tracker.tick()
                    elif path == "/api/target-delete":
                        tracker.store.db.execute("UPDATE targets SET archived=1,enabled=0 WHERE id=?", (data.get("id"),))
                        tracker.store.db.commit()
                        tracker.tick()
                    elif path == "/api/settings":
                        idle = data.get("idle_minutes")
                        goal = data.get("daily_goal")
                        if idle not in (1, 3, 5, 10, 15, 30) or goal not in (2, 4, 6, 8, 10, 12):
                            raise ValueError("설정값을 확인해주세요.")
                        grace = data.get("switch_grace_minutes", tracker.store.settings()["switch_grace_minutes"])
                        if type(grace) is not int or grace not in SWITCH_GRACE_MINUTES:
                            raise ValueError("창 전환 후 이어 기록 시간을 확인해주세요.")
                        tracker.store.set_setting("idle_minutes", idle)
                        tracker.store.set_setting("daily_goal", goal)
                        tracker.store.set_setting("switch_grace_minutes", grace)
                        tracker.tick()
                    elif path == "/api/assignments":
                        save_assignment(tracker.store.db, data)
                    elif path == "/api/assignment-status":
                        change_assignment_status(tracker.store.db, data.get("id"), data.get("status"))
                    elif path == "/api/assignment-delete":
                        archive_assignment(tracker.store.db, data.get("id"))
                    elif path == "/api/clear-demo":
                        tracker.store.db.execute("DELETE FROM sessions WHERE source='demo'")
                        tracker.store.db.commit()
                    else:
                        return self.send_json({"error": "요청한 기능을 찾을 수 없습니다."}, 404)
                    return self.send_json(tracker.state())
            except (ValueError, TypeError, json.JSONDecodeError, OverflowError) as exc:
                return self.send_json({"error": str(exc) if isinstance(exc, ValueError) else "입력값을 확인해주세요."}, 400)
            except Exception as exc:
                print(f"API error: {type(exc).__name__}", flush=True)
                return self.send_json({"error": "요청을 처리하지 못했습니다. 다시 시도해주세요."}, 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="모아 — 나의 작업 시간을 모으다")
    parser.add_argument("--port", type=int, default=3847)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    data_path = Path(os.environ.get("MOA_DATA_DIR", ROOT / "data"))
    data_path.mkdir(parents=True, exist_ok=True)
    store = Store(data_path / "moa.sqlite3")
    tracker = Tracker(store)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(tracker, args.port))
    except OSError:
        print(f"Port {args.port} is in use. Open http://127.0.0.1:{args.port} or use --port.")
        if not args.no_open:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return
    server.daemon_threads = True
    stop = threading.Event()
    worker = threading.Thread(target=tracker.loop, args=(stop,), daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{args.port}"
    print(f"MOA is ready: {address}\nData stays on this computer. Press Ctrl+C to stop.", flush=True)
    if not args.no_open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(3)
        with tracker.lock:
            tracker.stop_segment(tracker.last_tick)
        server.server_close()
        store.db.close()


if __name__ == "__main__":
    main()
