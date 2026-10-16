import unittest

from tracker import Store
from tests.fixtures import seed_test_store
from schedules import STATUSES, archive_assignment, change_assignment_status, save_assignment, schedule_state, fee_text


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
