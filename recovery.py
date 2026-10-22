"""Conservative, explicitly requested recovery of short gaps in old records."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

KST = timezone(timedelta(hours=9))


def ensure_recovery_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS gap_recoveries (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        right_session_id TEXT NOT NULL, original_end REAL NOT NULL,
        restored_end REAL NOT NULL, restored_at REAL NOT NULL
    )""")


def gap_candidates(sessions, excluded, limit_seconds, before):
    if not 0 < limit_seconds <= 1800 or not math.isfinite(before):
        raise ValueError("복원 범위를 확인해주세요.")
    rows = sorted((s for s in sessions if s["end"] > s["start"]), key=lambda s: (s["start"], s["end"]))
    result = []
    boundary = None
    for item in rows:
        if boundary and item["start"] > boundary["end"]:
            start, end = boundary["end"], item["start"]
            same_day = datetime.fromtimestamp(start, KST).date() == datetime.fromtimestamp(end, KST).date()
            if (boundary["source"] == item["source"] == "auto" and boundary["target_id"] == item["target_id"]
                    and end - start <= limit_seconds and item["end"] <= before and same_day
                    and not any(e["start"] < end and e["end"] > start for e in excluded)):
                result.append({"left_id": boundary["id"], "right_id": item["id"],
                               "target_id": item["target_id"], "start": start, "end": end})
        if boundary is None or item["end"] >= boundary["end"]:
            boundary = item
    return result


def apply_gap_plan(store, plan, backup_dir):
    """Revalidate against the app's live database, back up, then commit once."""
    if plan.get("format") != 1 or plan.get("approved") is not True:
        raise ValueError("승인된 공백 복원 계획이 필요합니다.")
    limit = plan["limit_seconds"]
    before = plan["before"]
    if type(limit) is not int or not 0 < limit <= 1800 or not isinstance(before, (int, float)) or not math.isfinite(before):
        raise ValueError("복원 범위를 확인해주세요.")
    approved = plan["candidates"]
    required = {item[key] for item in approved for key in ("left_id", "right_id")}
    if not required.issubset({s["id"] for s in store.sessions()}):
        raise ValueError("실행 중인 앱의 원본 기록과 다른 데이터베이스입니다. 기록을 수정하지 않았습니다.")
    expected = {item["left_id"]: item for item in approved}
    if len(expected) != len(approved):
        raise ValueError("복원 계획에 중복된 구간이 있습니다.")

    def eligible():
        excluded = [dict(row) for row in store.db.execute("SELECT start,end FROM excluded")]
        return [item for item in gap_candidates(store.sessions(), excluded, limit, before)
                if expected.get(item["left_id"]) == item]

    if not eligible():
        return {"applied": 0, "seconds": 0, "backup": None}
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    backup = backup_dir / f"moa-before-gap-recovery-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    with closing(sqlite3.connect(str(backup))) as destination:
        store.db.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("복원 전 백업 검증에 실패했습니다.")
    applied = []
    with store.db:
        store.db.execute("BEGIN IMMEDIATE")
        for item in eligible():
            store.db.execute("INSERT INTO gap_recoveries VALUES (?,?,?,?,?)",
                             (item["left_id"], item["right_id"], item["start"], item["end"], time.time()))
            store.db.execute("UPDATE sessions SET end=? WHERE id=?", (item["end"], item["left_id"]))
            applied.append(item)
        result = {"applied": len(applied), "seconds": sum(item["end"] - item["start"] for item in applied),
                  "backup": str(backup), "finished_at": time.time()}
        store.db.execute("INSERT OR REPLACE INTO settings VALUES ('last_gap_recovery',?)", (json.dumps(result),))
    return result


def run_recovery_job(job_path):
    """A maintenance-only entry point: no Qt, tracking, or startup registration."""
    from tracker import Store
    job_path = Path(job_path).resolve()
    result_path = job_path.with_suffix(".result.json")
    store = None
    try:
        plan = json.loads(job_path.read_text(encoding="utf-8"))
        profile = Path(plan["data_directory"]).resolve()
        if not (profile / "moa.sqlite3").is_file():
            raise ValueError("기존 작업 기록을 찾지 못했습니다.")
        store = Store(profile / "moa.sqlite3")
        result = {"ok": True, **apply_gap_plan(store, plan, profile / "backups")}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    finally:
        if store:
            store.db.close()
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1
