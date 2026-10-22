from contextlib import closing
import sqlite3
import json
from pathlib import Path
import tempfile
import unittest

from tracker import Store
from recovery import apply_gap_plan,gap_candidates
from tests.fixtures import seed_test_store


class GapRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(":memory:")
        seed_test_store(self.store)
        self.root=tempfile.TemporaryDirectory()

    def tearDown(self):
        self.store.db.close()
        self.root.cleanup()

    def plan(self):
        excluded=[dict(r) for r in self.store.db.execute("SELECT start,end FROM excluded")]
        return {"format":1,"approved":True,"limit_seconds":300,"before":10000,
                "candidates":gap_candidates(self.store.sessions(),excluded,300,10000)}

    def test_gap_recovery_is_backed_up_labeled_and_idempotent(self):
        left=self.store.add_session("code",1000,1100,"auto","preserve note")
        right=self.store.add_session("code",1150,1200,"auto")
        plan=self.plan()
        result=apply_gap_plan(self.store,plan,Path(self.root.name))
        self.assertEqual((result["applied"],result["seconds"]),(1,50))
        record=self.store.sessions()[0]
        self.assertEqual(record["id"],left)
        self.assertEqual(record["end"],1150)
        self.assertEqual(record["note"],"preserve note")
        self.assertEqual(record["recovered_seconds"],50)
        self.assertEqual(self.store.sessions()[1]["id"],right)
        with closing(sqlite3.connect(result["backup"])) as backup:
            self.assertEqual(backup.execute("SELECT end FROM sessions WHERE id=?",(left,)).fetchone()[0],1100)
        repeated=apply_gap_plan(self.store,plan,Path(self.root.name))
        self.assertEqual(repeated["applied"],0)
        self.assertEqual(len(list(Path(self.root.name).glob('*.sqlite3'))),1)

    def test_idle_other_targets_long_gaps_and_manual_records_are_excluded(self):
        self.store.add_session("code",1000,1100,"auto")
        self.store.add_session("code",1200,1300,"auto")
        self.store.db.execute("INSERT INTO excluded VALUES ('idle',1100,1200)")
        self.store.db.commit()
        self.store.add_session("figma",1350,1400,"auto")
        self.store.add_session("code",1450,1500,"auto")
        self.store.add_session("code",1900,2000,"auto")
        self.store.add_session("code",2050,2100,"manual")
        self.assertEqual(self.plan()["candidates"],[])

    def test_overlapping_records_cannot_hide_existing_coverage(self):
        self.store.add_session("figma",1000,1500,"auto")
        self.store.add_session("code",1100,1200,"auto")
        self.store.add_session("code",1300,1400,"auto")
        self.assertEqual(self.plan()["candidates"],[])

    def test_wrong_database_or_changed_boundaries_are_never_modified(self):
        left=self.store.add_session("code",1000,1100,"auto")
        self.store.add_session("code",1150,1200,"auto")
        plan=self.plan()
        self.store.finish(left,1090)
        result=apply_gap_plan(self.store,plan,Path(self.root.name))
        self.assertEqual(result["applied"],0)
        self.assertEqual(self.store.sessions()[0]["end"],1090)
        plan["candidates"][0]["right_id"]="missing"
        with self.assertRaises(ValueError):
            apply_gap_plan(self.store,plan,Path(self.root.name))

    def test_setting_default_is_added_without_overwriting_a_saved_choice(self):
        path=Path(self.root.name)/'settings.sqlite3'
        old=Store(path)
        self.assertEqual(old.settings()["switch_grace_minutes"],5)
        old.set_setting("switch_grace_minutes",0)
        old.db.close()
        reopened=Store(path)
        self.assertEqual(reopened.settings()["switch_grace_minutes"],0)
        reopened.db.close()

    def test_app_startup_applies_only_the_approved_profile_and_consumes_plan(self):
        from desktop_backend import DesktopBackend
        root=Path(self.root.name)
        profile=root/'profile'
        profile.mkdir()
        store=Store(profile/'moa.sqlite3')
        seed_test_store(store)
        store.add_session('code',1000,1100,'auto')
        store.add_session('code',1150,1200,'auto')
        plan={'format':1,'approved':True,'data_directory':str(profile),'limit_seconds':300,'before':10000,
              'candidates':gap_candidates(store.sessions(),[],300,10000)}
        store.db.close()
        path=root/'Moa.recovery.json'
        path.write_text(json.dumps(plan),encoding='utf-8')
        backend=DesktopBackend(profile,port=0,auto_track=False,recovery_plan=path)
        try:
            self.assertEqual(backend.recovery_result['applied'],1)
            self.assertEqual(backend.state()['sessions'][0]['recovered_seconds'],50)
            self.assertFalse(path.exists())
            self.assertTrue(Path(backend.recovery_result['backup']).is_file())
            self.assertEqual(json.loads((profile/'gap-recovery-result.json').read_text())['seconds'],50)
            self.assertEqual(backend.state()['settings']['last_gap_recovery']['applied'],1)
        finally:
            backend.close()
