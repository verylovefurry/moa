"""Local change journal and encrypted, idempotent Saved Messages protocol.

Business tables stay authoritative offline. Message IDs order remote field changes;
the journal coalesces one-second timer updates until the next network exchange.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import secrets
import sqlite3
import uuid
import zlib

TABLES = {
    "targets": ("name", "kind", "matcher", "color", "icon", "enabled", "archived", "platform"),
    "assignments": ("title", "client", "start_date", "due_date", "fee", "net_fee", "status", "note", "created_at", "updated_at", "completed_at", "archived"),
    "sessions": ("target_id", "start", "end", "source", "note"),
    "excluded": ("start", "end"),
    "assignment_history": ("assignment_id", "from_status", "to_status", "changed_at"),
    "gap_recoveries": ("right_session_id", "original_end", "restored_end", "restored_at"),
}
PRIMARY_KEYS = {"gap_recoveries": "session_id"}
MAX_FILE = 4 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
MAGIC = b"MOASYNC1"


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def new_pairing_key():
    return "moa1_" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")


def key_bytes(key):
    if not isinstance(key, str) or not re.fullmatch(r"moa1_[A-Za-z0-9_-]{43}", key):
        raise ValueError("연동 키를 확인해주세요.")
    value = base64.urlsafe_b64decode(key[5:] + "=")
    if base64.urlsafe_b64encode(value).decode().rstrip("=") != key[5:]:
        raise ValueError("연동 키를 확인해주세요.")
    return value


def stream_id(key):
    return hashlib.sha256(key_bytes(key)).hexdigest()[:24]


def caption_prefix(key):
    return "MOA_SYNC_V1_" + stream_id(key)


def encode_batch(batch, key):
    from Crypto.Cipher import AES
    raw = dumps(batch).encode("utf-8")
    if len(raw) > MAX_JSON:
        raise ValueError("동기화 파일이 너무 큽니다.")
    nonce = secrets.token_bytes(12)
    cipher = AES.new(key_bytes(key), AES.MODE_GCM, nonce=nonce)
    cipher.update(MAGIC)
    ciphertext, tag = cipher.encrypt_and_digest(zlib.compress(raw))
    result = MAGIC + nonce + tag + ciphertext
    if len(result) > MAX_FILE:
        raise ValueError("동기화 파일이 너무 큽니다.")
    return result


def decode_batch(payload, key):
    from Crypto.Cipher import AES
    if not isinstance(payload, bytes) or len(payload) > MAX_FILE or len(payload) < 37 or payload[:8] != MAGIC:
        raise ValueError("지원하지 않는 동기화 파일입니다.")
    try:
        cipher = AES.new(key_bytes(key), AES.MODE_GCM, nonce=payload[8:20])
        cipher.update(MAGIC)
        compressed = cipher.decrypt_and_verify(payload[36:], payload[20:36])
        decoder = zlib.decompressobj()
        raw = decoder.decompress(compressed, MAX_JSON + 1)
        if len(raw) > MAX_JSON or not decoder.eof or decoder.unused_data:
            raise ValueError()
        batch = json.loads(raw)
        if not isinstance(batch, dict) or batch.get("version") != 1:
            raise ValueError()
        if not re.fullmatch(r"[a-f0-9]{32}", batch.get("batch_id", "")):
            raise ValueError()
        changes = batch.get("changes")
        if not isinstance(changes, list) or len(changes) > 2000:
            raise ValueError()
        for change in changes:
            validate_change(change)
        return batch
    except (ValueError, TypeError, KeyError, zlib.error, UnicodeError) as exc:
        raise ValueError("동기화 파일을 확인할 수 없습니다. 같은 연동 키인지 확인해주세요.") from exc


def ensure_sync_schema(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS sync_control (
            id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0,
            applying INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO sync_control(id) VALUES(1);
        CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_dirty (
            entity TEXT NOT NULL,row_id TEXT NOT NULL,field TEXT NOT NULL,
            value TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(entity,row_id,field)
        );
        CREATE TABLE IF NOT EXISTS sync_versions (
            entity TEXT NOT NULL,row_id TEXT NOT NULL,field TEXT NOT NULL,
            message_id INTEGER NOT NULL,PRIMARY KEY(entity,row_id,field)
        );
        CREATE TABLE IF NOT EXISTS sync_seen(batch_id TEXT PRIMARY KEY,message_id INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_pending(id INTEGER PRIMARY KEY CHECK(id=1),batch TEXT NOT NULL,revisions TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sync_conflicts (
            id INTEGER PRIMARY KEY,entity TEXT,row_id TEXT,field TEXT,
            previous TEXT,incoming TEXT,message_id INTEGER
        );
    """)
    for table, fields in TABLES.items():
        primary = PRIMARY_KEYS.get(table, "id")
        for action in ("INSERT", "UPDATE", "DELETE"):
            statements = ["UPDATE sync_control SET revision=revision+1 WHERE id=1;"]
            if action == "DELETE":
                statements.append(f"DELETE FROM sync_dirty WHERE entity='{table}' AND row_id=OLD.{primary};")
                expressions = [("__deleted", "1", "1", "OLD")]
            else:
                expressions = [(f, f"json_quote(NEW.{f})", "1" if action == "INSERT" else f"NEW.{f} IS NOT OLD.{f}", "NEW") for f in fields]
                if action == "INSERT":
                    expressions.append(("__deleted", "0", "1", "NEW"))
            for field, value, condition, record in expressions:
                # The date range is one invariant: offline edits cannot merge a
                # later start from one device with an earlier due date from another.
                if table == "assignments" and action == "UPDATE" and field in ("start_date", "due_date"):
                    condition = "NEW.start_date IS NOT OLD.start_date OR NEW.due_date IS NOT OLD.due_date"
                statements.append(f"""INSERT INTO sync_dirty(entity,row_id,field,value,revision)
                    SELECT '{table}',{record}.{primary},'{field}',{value},revision FROM sync_control WHERE id=1 AND ({condition})
                    ON CONFLICT(entity,row_id,field) DO UPDATE SET value=excluded.value,revision=excluded.revision;""")
            db.execute(f"""CREATE TRIGGER IF NOT EXISTS sync_{table}_{action.lower()} AFTER {action} ON {table}
                WHEN (SELECT enabled=1 AND applying=0 FROM sync_control WHERE id=1)
                BEGIN {' '.join(statements)} END""")
    db.commit()


def validate_change(change):
    if not isinstance(change, dict) or change.get("table") not in TABLES:
        raise ValueError("동기화 항목을 확인해주세요.")
    if not isinstance(change.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", change["id"]):
        raise ValueError("동기화 항목 ID가 올바르지 않습니다.")
    fields = change.get("fields")
    if not isinstance(fields, dict) or not fields or set(fields) - set(TABLES[change["table"]]) - {"__deleted"}:
        raise ValueError("지원하지 않는 동기화 필드입니다.")
    for name, value in fields.items():
        if isinstance(value, (dict, list, bool)) or (isinstance(value, str) and len(value) > 8192):
            raise ValueError("동기화 값이 올바르지 않습니다.")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("동기화 숫자가 올바르지 않습니다.")
        if name == "__deleted" and (type(value) is not int or value not in (0, 1)):
            raise ValueError("동기화 삭제 값이 올바르지 않습니다.")


def validate_row(table, row):
    def text(field, limit, empty=True):
        value = row[field]
        if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
            raise ValueError("동기화 문자열을 확인해주세요.")

    def timestamp(field, nullable=False):
        value = row[field]
        if value is None and nullable:
            return
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 253402300800:
            raise ValueError("동기화 시간을 확인해주세요.")

    if table in ("targets", "assignments") and (type(row["archived"]) is not int or row["archived"] not in (0, 1)):
        raise ValueError("보관 상태를 확인해주세요.")
    if table == "targets":
        text("name", 48, False)
        text("matcher", 2048, False)
        text("icon", 40)
        if row["kind"] not in ("app", "url") or row["platform"] not in ("windows", "android"):
            raise ValueError("추적 대상 종류를 확인해주세요.")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", row["color"]) or type(row["enabled"]) is not int or row["enabled"] not in (0, 1):
            raise ValueError("추적 대상 상태를 확인해주세요.")
    elif table == "assignments":
        from schedules import validate_assignment
        text("title", 120, False)
        text("client", 80)
        text("note", 2000)
        validate_assignment(row)
        for field in ("created_at", "updated_at", "completed_at"):
            timestamp(field, field == "completed_at")
    elif table in ("sessions", "excluded"):
        timestamp("start")
        timestamp("end")
        if row["end"] < row["start"]:
            raise ValueError("종료 시간이 시작 시간보다 빠릅니다.")
        if table == "sessions":
            text("target_id", 80, False)
            text("note", 2000)
            if row["source"] not in ("auto", "timer", "manual", "demo"):
                raise ValueError("기록 방식을 확인해주세요.")
    elif table == "assignment_history":
        from schedules import STATUS_LABELS
        text("assignment_id", 80, False)
        if row["to_status"] not in STATUS_LABELS or row["from_status"] not in (None, *STATUS_LABELS):
            raise ValueError("일정 상태를 확인해주세요.")
        timestamp("changed_at")
    elif table == "gap_recoveries":
        text("right_session_id", 80, False)
        for field in ("original_end", "restored_end", "restored_at"):
            timestamp(field)
        if row["restored_end"] < row["original_end"]:
            raise ValueError("복원 기록의 시간을 확인해주세요.")


class SyncJournal:
    """All methods are called while the host's shared tracker lock is held."""

    def __init__(self, db):
        self.db = db

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO sync_state VALUES(?,?)", (key, dumps(value)))

    def connect(self, pairing_key):
        workspace = stream_id(pairing_key)
        previous = self.get("workspace")
        if previous and previous != workspace:
            raise ValueError("기존 연동을 해제한 뒤 다른 연동 키를 사용해주세요.")
        self.set("workspace", workspace)
        if not self.get("device"):
            self.set("device", uuid.uuid4().hex)
        self.db.execute("UPDATE sync_control SET enabled=1 WHERE id=1")
        self.db.commit()

    def disconnect(self):
        # Local business records remain untouched; a new link can bootstrap them.
        with self.db:
            self.db.execute("UPDATE sync_control SET enabled=0 WHERE id=1")
            for table in ("sync_state", "sync_dirty", "sync_versions", "sync_seen", "sync_pending", "sync_conflicts"):
                self.db.execute(f"DELETE FROM {table}")

    def bootstrap(self):
        if self.get("bootstrapped"):
            return
        with self.db:
            for table, fields in TABLES.items():
                primary = PRIMARY_KEYS.get(table, "id")
                for row in self.db.execute(f"SELECT * FROM {table}").fetchall():
                    for field in (*fields, "__deleted"):
                        if self.db.execute("SELECT 1 FROM sync_versions WHERE entity=? AND row_id=? AND field=?", (table, row[primary], field)).fetchone():
                            continue
                        self.db.execute("UPDATE sync_control SET revision=revision+1 WHERE id=1")
                        value = 0 if field == "__deleted" else row[field]
                        self.db.execute("""INSERT OR IGNORE INTO sync_dirty
                            SELECT ?,?,?,?,revision FROM sync_control WHERE id=1""", (table, row[primary], field, dumps(value)))
            self.set("bootstrapped", True)

    def pending(self, max_revision=None):
        row = self.db.execute("SELECT batch FROM sync_pending WHERE id=1").fetchone()
        if row:
            return json.loads(row[0])
        changes, revisions = [], []
        if max_revision is None:
            max_revision = self.db.execute("SELECT revision FROM sync_control WHERE id=1").fetchone()[0]
        for table in TABLES:
            ids = self.db.execute("SELECT DISTINCT row_id FROM sync_dirty WHERE entity=? AND revision<=? ORDER BY row_id LIMIT ?", (table, max_revision, 250 - len(changes))).fetchall()
            for item in ids:
                fields = {}
                for row in self.db.execute("SELECT * FROM sync_dirty WHERE entity=? AND row_id=?", (table, item[0])):
                    fields[row["field"]] = json.loads(row["value"])
                    revisions.append([table, item[0], row["field"], row["revision"]])
                changes.append({"table": table, "id": item[0], "fields": fields})
            if len(changes) >= 250:
                break
        if not changes:
            return None
        batch = {"version": 1, "batch_id": uuid.uuid4().hex, "device": self.get("device"), "changes": changes}
        with self.db:
            self.db.execute("INSERT INTO sync_pending VALUES(1,?,?)", (dumps(batch), dumps(revisions)))
        return batch

    def acknowledge(self):
        row = self.db.execute("SELECT * FROM sync_pending WHERE id=1").fetchone()
        if not row or not self.db.execute("SELECT 1 FROM sync_seen WHERE batch_id=?", (json.loads(row["batch"])["batch_id"],)).fetchone():
            return False
        with self.db:
            for table, row_id, field, revision in json.loads(row["revisions"]):
                self.db.execute("DELETE FROM sync_dirty WHERE entity=? AND row_id=? AND field=? AND revision=?", (table, row_id, field, revision))
            self.db.execute("DELETE FROM sync_pending")
        return True

    def apply(self, batch, message_id):
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("메시지 ID를 확인해주세요.")
        for change in batch["changes"]:
            validate_change(change)
        with self.db:
            if not self.db.execute("SELECT 1 FROM sync_seen WHERE batch_id=?", (batch["batch_id"],)).fetchone():
                self.db.execute("UPDATE sync_control SET applying=1 WHERE id=1")
                order = {name: i for i, name in enumerate(TABLES)}
                for change in sorted(batch["changes"], key=lambda c: order[c["table"]]):
                    self._apply_change(change, message_id)
                self.db.execute("INSERT INTO sync_seen VALUES(?,?)", (batch["batch_id"], message_id))
                self.db.execute("UPDATE sync_control SET applying=0 WHERE id=1")
            self.set("cursor", max(message_id, self.get("cursor", 0)))
        self.acknowledge()

    def _apply_change(self, change, message_id):
        table, row_id, fields = change["table"], change["id"], change["fields"]
        primary = PRIMARY_KEYS.get(table, "id")
        existing = self.db.execute(f"SELECT * FROM {table} WHERE {primary}=?", (row_id,)).fetchone()
        current = dict(existing) if existing else {}
        accepted = {}
        own_pending = self.db.execute("SELECT batch FROM sync_pending WHERE id=1").fetchone()
        own_fields = {}
        if own_pending:
            own_fields = next((c["fields"] for c in json.loads(own_pending[0])["changes"] if c["table"] == table and c["id"] == row_id), {})
        for field, value in fields.items():
            version = self.db.execute("SELECT message_id FROM sync_versions WHERE entity=? AND row_id=? AND field=?", (table, row_id, field)).fetchone()
            if version and version[0] >= message_id:
                continue
            dirty = self.db.execute("SELECT value FROM sync_dirty WHERE entity=? AND row_id=? AND field=?", (table, row_id, field)).fetchone()
            protected = dirty and (field not in own_fields or json.loads(dirty[0]) != value or own_fields[field] != value)
            if not protected:
                accepted[field] = value
            if existing and field in current and current[field] != value:
                self.db.execute("INSERT INTO sync_conflicts(entity,row_id,field,previous,incoming,message_id) VALUES(?,?,?,?,?,?)", (table, row_id, field, dumps(current[field]), dumps(value), message_id))
            self.db.execute("INSERT OR REPLACE INTO sync_versions VALUES(?,?,?,?)", (table, row_id, field, message_id))
        deleted = accepted.pop("__deleted", 0)
        tombstone = self.db.execute("SELECT message_id FROM sync_versions WHERE entity=? AND row_id=? AND field='__deleted'", (table, row_id)).fetchone()
        if deleted:
            # Only ephemeral time/exclusion records are physically removed by either app.
            if table not in ("sessions", "excluded", "gap_recoveries"):
                raise ValueError("이 항목은 보관 처리가 필요합니다.")
            self.db.execute(f"DELETE FROM {table} WHERE {primary}=?", (row_id,))
        elif accepted:
            if not existing and tombstone and tombstone[0] > message_id:
                return
            merged = {**current, **accepted, primary: row_id}
            if set(TABLES[table]) - merged.keys():
                raise ValueError("동기화 항목의 이전 기록이 누락되었습니다.")
            validate_row(table, merged)
            if existing:
                self.db.execute(f"UPDATE {table} SET {','.join(f'{f}=?' for f in accepted)} WHERE {primary}=?", (*accepted.values(), row_id))
            else:
                names = (primary, *TABLES[table])
                self.db.execute(f"INSERT INTO {table}({','.join(names)}) VALUES({','.join('?' for _ in names)})", tuple(merged[f] for f in names))

    def status(self):
        return {"cursor": self.get("cursor", 0), "pending": self.db.execute("SELECT COUNT(DISTINCT entity||':'||row_id) FROM sync_dirty").fetchone()[0]}
