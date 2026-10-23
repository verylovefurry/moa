"""Commission/task records and status history, stored with the work-time database."""
from __future__ import annotations

from datetime import date, datetime
import sqlite3
import time
import uuid

STATUSES = (
    ("not_started", "미착수"),
    ("in_progress", "진행중"),
    ("first_done", "1차 완료"),
    ("delivered", "파일 전달"),
    ("paid", "보수 수령"),
    ("completed", "최종 완료"),
)
STATUS_LABELS = dict(STATUSES)
STATUS_COLORS = {
    "not_started": "#a1aaa5", "in_progress": "#548268", "first_done": "#7795ad",
    "delivered": "#5f9590", "paid": "#b49a61", "completed": "#737e76",
}


def ensure_schedule_schema(db: sqlite3.Connection):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, client TEXT NOT NULL DEFAULT '',
            start_date TEXT NOT NULL, due_date TEXT, fee INTEGER, net_fee INTEGER,
            status TEXT NOT NULL DEFAULT 'not_started', note TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
            archived INTEGER NOT NULL DEFAULT 0,
            CHECK (fee IS NULL OR fee >= 0),
            CHECK (net_fee IS NULL OR net_fee >= 0),
            CHECK (status IN ('not_started','in_progress','first_done','delivered','paid','completed'))
        );
        CREATE TABLE IF NOT EXISTS assignment_history (
            id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
            from_status TEXT, to_status TEXT NOT NULL, changed_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS assignment_period ON assignments(start_date,due_date);
        CREATE INDEX IF NOT EXISTS assignment_history_id ON assignment_history(assignment_id,changed_at);
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(assignments)")}
    if "net_fee" not in columns:
        with db:
            db.execute("ALTER TABLE assignments ADD COLUMN net_fee INTEGER CHECK (net_fee IS NULL OR net_fee >= 0)")


def iso_date(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label}을 지정해주세요.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label}을 올바른 날짜로 지정해주세요.") from None
    if value != parsed.isoformat() or parsed.year < 1900:
        raise ValueError(f"{label}은 1900년 이후의 날짜로 지정해주세요.")
    return value


def validate_assignment(payload):
    title = str(payload.get("title", "")).strip()
    client = str(payload.get("client", "")).strip()
    note = str(payload.get("note", "")).strip()
    if not title or len(title) > 120:
        raise ValueError("의뢰·할 일 이름을 1~120자로 입력해주세요.")
    if len(client) > 80 or len(note) > 2000:
        raise ValueError("의뢰인은 80자, 메모는 2000자까지 입력할 수 있습니다.")
    start = iso_date(payload.get("start_date"), "시작일")
    due = payload.get("due_date") or None
    if due:
        due = iso_date(due, "마감일")
        if due < start:
            raise ValueError("마감일은 시작일보다 빠를 수 없습니다.")
    fee = payload.get("fee")
    net_fee = payload.get("net_fee")
    for value, label in ((fee, "의뢰비"), (net_fee, "실수령액")):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 999_999_999_999:
                raise ValueError(f"{label} 금액은 0~999,999,999,999원의 정수로 입력해주세요.")
    status = payload.get("status", "not_started")
    if status not in STATUS_LABELS:
        raise ValueError("일정의 진행 상태를 확인해주세요.")
    return title, client, start, due, fee, net_fee, status, note


def _history(db, assignment_id, old, new, timestamp):
    db.execute("INSERT INTO assignment_history VALUES (?,?,?,?,?)", (uuid.uuid4().hex, assignment_id, old, new, timestamp))


def save_assignment(db, payload, now=None):
    now = time.time() if now is None else now
    title, client, start, due, fee, net_fee, status, note = validate_assignment(payload)
    assignment_id = payload.get("id")
    with db:
        if assignment_id:
            old = db.execute("SELECT * FROM assignments WHERE id=? AND archived=0", (assignment_id,)).fetchone()
            if old is None:
                raise ValueError("일정을 찾을 수 없습니다.")
            if "net_fee" not in payload:
                net_fee = old["net_fee"]
            completed = old["completed_at"] if status == "completed" and old["status"] == "completed" else now if status == "completed" else None
            db.execute("UPDATE assignments SET title=?,client=?,start_date=?,due_date=?,fee=?,net_fee=?,status=?,note=?,updated_at=?,completed_at=? WHERE id=?", (title, client, start, due, fee, net_fee, status, note, now, completed, assignment_id))
            if old["status"] != status:
                _history(db, assignment_id, old["status"], status, now)
        else:
            assignment_id = uuid.uuid4().hex[:16]
            db.execute("INSERT INTO assignments(id,title,client,start_date,due_date,fee,net_fee,status,note,created_at,updated_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (assignment_id, title, client, start, due, fee, net_fee, status, note, now, now, now if status == "completed" else None))
            _history(db, assignment_id, None, status, now)
    return assignment_id


def change_assignment_status(db, assignment_id, status, now=None):
    if status not in STATUS_LABELS:
        raise ValueError("일정의 진행 상태를 확인해주세요.")
    now = time.time() if now is None else now
    with db:
        old = db.execute("SELECT status FROM assignments WHERE id=? AND archived=0", (assignment_id,)).fetchone()
        if old is None:
            raise ValueError("일정을 찾을 수 없습니다.")
        if old["status"] == status:
            return
        db.execute("UPDATE assignments SET status=?,updated_at=?,completed_at=? WHERE id=?", (status, now, now if status == "completed" else None, assignment_id))
        _history(db, assignment_id, old["status"], status, now)


def archive_assignment(db, assignment_id):
    with db:
        result = db.execute("UPDATE assignments SET archived=1,updated_at=? WHERE id=? AND archived=0", (time.time(), assignment_id))
        if not result.rowcount:
            raise ValueError("일정을 찾을 수 없습니다.")


def schedule_state(db):
    return {
        "assignments": [dict(row) for row in db.execute("SELECT * FROM assignments WHERE archived=0 ORDER BY start_date,title")],
        "assignment_history": [dict(row) for row in db.execute("SELECT h.* FROM assignment_history h JOIN assignments a ON a.id=h.assignment_id WHERE a.archived=0 ORDER BY h.changed_at,h.rowid")],
    }


def fee_text(value):
    return "—" if value is None else f"{value:,}원"


def assignment_period(item):
    start = date.fromisoformat(item["start_date"])
    return start, date.fromisoformat(item["due_date"]) if item["due_date"] else start
