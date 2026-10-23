from pathlib import Path
import sqlite3
import tempfile
import unittest

from tracker import Store
from tests.fixtures import seed_test_store
from schedules import STATUSES, archive_assignment, assignment_totals, change_assignment_status, save_assignment, schedule_state, fee_text


class ScheduleDataTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:", seed=False)
        seed_test_store(self.store)
        self.db = self.store.db
        self.payload = {"title":"브랜드 일러스트", "client":"의뢰인", "start_date":"2026-10-16", "due_date":"2026-11-05", "fee":800000}

    def tearDown(self):
        self.db.close()

    def test_optional_dates_and_zero_fee_are_distinct_from_no_fee(self):
        item = {**self.payload,"due_date":None,"fee":0}
        assignment_id = save_assignment(self.db,item)
        saved = schedule_state(self.db)["assignments"][0]
        self.assertIsNone(saved["due_date"])
        self.assertEqual(saved["fee"],0)
        self.assertEqual(fee_text(0),"0원")
        self.assertEqual(fee_text(None),"—")

    def test_all_six_stages_keep_audit_history(self):
        assignment_id = save_assignment(self.db,self.payload,now=1000)
        for index,(status,_) in enumerate(STATUSES):
            change_assignment_status(self.db,assignment_id,status,now=1001+index)
        state=schedule_state(self.db)
        self.assertEqual(len(state["assignment_history"]),6)
        self.assertEqual(state["assignments"][0]["status"],"completed")
        self.assertEqual(state["assignments"][0]["completed_at"],1006)

    def test_commission_and_net_received_are_independent_optional_amounts(self):
        for fee, net_fee in ((150000, 132912), (150000, None), (None, 132912), (0, 0), (150000, 160000)):
            with self.subTest(fee=fee, net_fee=net_fee):
                assignment_id = save_assignment(self.db, {**self.payload, "fee":fee, "net_fee":net_fee})
                saved = next(item for item in schedule_state(self.db)["assignments"] if item["id"] == assignment_id)
                self.assertEqual((saved["fee"], saved["net_fee"]), (fee, net_fee))

    def test_edit_preserves_omitted_net_amount_and_explicit_null_clears_it(self):
        assignment_id = save_assignment(self.db, {**self.payload, "net_fee":132912, "status":"paid"})
        payload = {**self.payload, "id":assignment_id, "fee":150000, "status":"paid"}
        save_assignment(self.db, payload)
        saved = schedule_state(self.db)["assignments"][0]
        self.assertEqual((saved["fee"], saved["net_fee"]), (150000, 132912))
        save_assignment(self.db, {**payload, "net_fee":0})
        self.assertEqual(schedule_state(self.db)["assignments"][0]["net_fee"], 0)
        save_assignment(self.db, {**payload, "net_fee":None})
        state = schedule_state(self.db)
        self.assertIsNone(state["assignments"][0]["net_fee"])
        self.assertEqual(len(state["assignment_history"]), 1)

    def test_invalid_net_amounts_reject_create_and_edit_without_partial_changes(self):
        for value in (-1, 12.5, True, "132912", 1_000_000_000_000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_assignment(self.db, {**self.payload, "net_fee":value})
        self.assertEqual(schedule_state(self.db)["assignments"], [])
        self.assertEqual(schedule_state(self.db)["assignment_history"], [])
        assignment_id = save_assignment(self.db, {**self.payload, "fee":150000, "net_fee":132912})
        before = schedule_state(self.db)
        for value in (-1, 12.5, True, "132912", 1_000_000_000_000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                save_assignment(self.db, {**self.payload, "id":assignment_id, "net_fee":value, "status":"paid"})
            self.assertEqual(schedule_state(self.db), before)

    def test_legacy_schedule_migration_keeps_amounts_history_and_persists_new_field(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with sqlite3.connect(path) as legacy:
                legacy.executescript("""
                    CREATE TABLE assignments (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, client TEXT NOT NULL DEFAULT '',
                        start_date TEXT NOT NULL, due_date TEXT, fee INTEGER,
                        status TEXT NOT NULL DEFAULT 'not_started', note TEXT NOT NULL DEFAULT '',
                        created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
                        archived INTEGER NOT NULL DEFAULT 0,
                        CHECK (fee IS NULL OR fee >= 0),
                        CHECK (status IN ('not_started','in_progress','first_done','delivered','paid','completed'))
                    );
                    CREATE TABLE assignment_history (
                        id TEXT PRIMARY KEY, assignment_id TEXT NOT NULL REFERENCES assignments(id),
                        from_status TEXT, to_status TEXT NOT NULL, changed_at REAL NOT NULL
                    );
                    INSERT INTO assignments VALUES
                        ('legacy','기존 의뢰','의뢰인','2026-10-01',NULL,150000,'paid','기존 메모',100,200,NULL,0);
                    INSERT INTO assignment_history VALUES ('history','legacy',NULL,'paid',100);
                """)
            legacy.close()
            upgraded = Store(path, seed=False)
            try:
                state = schedule_state(upgraded.db)
                saved = state["assignments"][0]
                self.assertEqual((saved["fee"], saved["net_fee"]), (150000, None))
                self.assertEqual((saved["title"], saved["note"], saved["created_at"]), ("기존 의뢰", "기존 메모", 100))
                self.assertEqual(state["assignment_history"][0]["id"], "history")
                save_assignment(upgraded.db, {**saved, "net_fee":132912})
            finally:
                upgraded.db.close()
            reopened = Store(path, seed=False)
            try:
                state = schedule_state(reopened.db)
                self.assertEqual((state["assignments"][0]["fee"], state["assignments"][0]["net_fee"]), (150000, 132912))
                self.assertEqual(len(state["assignment_history"]), 1)
                self.assertEqual(reopened.db.execute("PRAGMA quick_check").fetchone()[0], "ok")
            finally:
                reopened.db.close()

    def test_reopening_completed_task_preserves_history(self):
        assignment_id=save_assignment(self.db,self.payload)
        change_assignment_status(self.db,assignment_id,"completed")
        change_assignment_status(self.db,assignment_id,"in_progress")
        state=schedule_state(self.db)
        self.assertIsNone(state["assignments"][0]["completed_at"])
        self.assertEqual(len(state["assignment_history"]),3)

    def test_metadata_edit_does_not_add_fake_status_transition(self):
        assignment_id=save_assignment(self.db,self.payload)
        save_assignment(self.db,{**self.payload,"id":assignment_id,"title":"수정된 이름"})
        self.assertEqual(len(schedule_state(self.db)["assignment_history"]),1)

    def test_invalid_deadline_fee_and_status_are_rejected_atomically(self):
        for change in ({"due_date":"2026-10-15"},{"fee":-1},{"fee":12.5},{"fee":True},{"status":"invalid"},{"start_date":"2026-02-30"},{"title":""}):
            with self.assertRaises(ValueError):
                save_assignment(self.db,{**self.payload,**change})
        self.assertEqual(schedule_state(self.db)["assignments"],[])
        self.assertEqual(schedule_state(self.db)["assignment_history"],[])

    def test_archiving_schedule_never_removes_work_time(self):
        assignment_id=save_assignment(self.db,self.payload)
        self.store.add_session("code",1000,2000,"auto")
        archive_assignment(self.db,assignment_id)
        self.assertEqual(schedule_state(self.db)["assignments"],[])
        self.assertEqual(len(self.store.sessions()),1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM assignment_history").fetchone()[0],1)


class AssignmentTotalTests(unittest.TestCase):
    def setUp(self):
        self.assignments = [
            {"start_date":day, "fee":fee, "net_fee":net_fee, "status":"paid"}
            for day, fee, net_fee in (
                ("2025-12-31", 500000, 480000), ("2026-01-01", 150000, 132912),
                ("2026-09-30", 100000, 90000), ("2026-10-01", 200000, None),
                ("2026-10-31", 0, 0), ("2026-11-01", 300000, 270000),
                ("2026-12-31", 400000, 350000), ("2027-01-01", 600000, 550000),
            )
        ]
        self.assignments[2]["due_date"] = "2026-10-31"
        self.assignments[4]["status"] = "completed"
        self.assignments.append({"start_date":"2026-10-15", "fee":999999, "net_fee":999999, "status":"paid", "archived":1})

    def test_month_uses_start_dates_and_never_counts_spanning_tasks_twice(self):
        self.assertEqual(assignment_totals(self.assignments, "2026-10-01", "2026-10-31"),
                         {"count":2, "fee":200000, "net_fee":200000, "fee_entered":2, "net_fee_entered":1, "net_fee_fallback":1})

    def test_year_and_cumulative_totals_include_both_calendar_boundaries(self):
        self.assertEqual(assignment_totals(self.assignments, "2026-01-01", "2026-12-31"),
                         {"count":6, "fee":1150000, "net_fee":1042912, "fee_entered":6, "net_fee_entered":5, "net_fee_fallback":1})
        self.assertEqual(assignment_totals(self.assignments),
                         {"count":8, "fee":2250000, "net_fee":2072912, "fee_entered":8, "net_fee_entered":7, "net_fee_fallback":1})

    def test_custom_range_is_inclusive_and_rejects_reverse_bounds(self):
        self.assertEqual(assignment_totals(self.assignments, "2026-10-31", "2026-11-01"),
                         {"count":2, "fee":300000, "net_fee":270000, "fee_entered":2, "net_fee_entered":2, "net_fee_fallback":0})
        with self.assertRaises(ValueError):
            assignment_totals(self.assignments, "2026-11-01", "2026-10-31")

    def test_missing_amounts_are_separate_from_zero_and_large_sums_stay_exact(self):
        result = assignment_totals([{"start_date":"2026-10-01", "fee":None, "net_fee":0, "status":"paid"}])
        self.assertEqual(result, {"count":1, "fee":0, "net_fee":0, "fee_entered":0, "net_fee_entered":1, "net_fee_fallback":0})
        maximum = {"start_date":"2026-10-01", "fee":999999999999, "net_fee":999999999998, "status":"paid"}
        result = assignment_totals([maximum, maximum])
        self.assertEqual((result["fee"], result["net_fee"]), (1999999999998, 1999999999996))
        self.assertEqual(assignment_totals([]), {"count":0, "fee":0, "net_fee":0, "fee_entered":0, "net_fee_entered":0, "net_fee_fallback":0})

    def test_unchecked_net_uses_commission_but_explicit_zero_and_both_missing_stay_distinct(self):
        assignments = [
            {"start_date":"2026-10-01", "fee":150000, "net_fee":None, "status":"paid"},
            {"start_date":"2026-10-01", "fee":200000, "net_fee":0, "status":"completed"},
            {"start_date":"2026-10-01", "fee":None, "net_fee":None, "status":"paid"},
        ]
        self.assertEqual(assignment_totals(assignments),
                         {"count":3, "fee":350000, "net_fee":150000, "fee_entered":2, "net_fee_entered":1, "net_fee_fallback":1})

    def test_only_paid_and_completed_contribute_in_every_period(self):
        base = {"start_date":"2026-10-15", "fee":150000, "net_fee":132912}
        assignments = [{**base, "status":status} for status, _ in STATUSES]
        assignments.extend((base, {**base, "status":"invalid"}, {**base, "status":"paid", "archived":1}))
        expected = {"count":2, "fee":300000, "net_fee":265824, "fee_entered":2, "net_fee_entered":2, "net_fee_fallback":0}
        for start, end in ((None,None), ("2026-01-01","2026-12-31"), ("2026-10-01","2026-10-31"), ("2026-10-15","2026-10-15")):
            with self.subTest(start=start, end=end):
                self.assertEqual(assignment_totals(assignments, start, end), expected)
