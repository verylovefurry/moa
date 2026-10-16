from pathlib import Path
import tempfile
import unittest
from tests.fixtures import seed_test_store
from tracker import Store


class EmptyDefaultsTests(unittest.TestCase):
    def test_every_new_install_starts_without_targets_or_fabricated_records(self):
        for seed in (False, True):
            store=Store(":memory:",seed=seed)
            self.assertEqual(store.targets(),[])
            self.assertEqual(store.sessions(),[])
            store.db.close()

    def test_upgrade_archives_only_unchanged_builtins_preserving_all_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"moa.sqlite3"
            old=Store(path)
            seed_test_store(old)
            old.add_session("figma",1000,1100,"auto")
            old.db.execute("INSERT INTO targets VALUES ('user','내 작업','app','custom.exe','#78a38c','app',1,0)")
            old.db.execute("UPDATE targets SET name='수정한 기본 항목' WHERE id='code'")
            old.db.execute("DELETE FROM settings WHERE key='default_targets_removed_v1'")
            old.db.commit()
            old.db.close()
            upgraded=Store(path)
            targets={item['id']:item for item in upgraded.targets()}
            self.assertEqual([item['id'] for item in targets.values() if not item['archived']],['code','user'])
            self.assertFalse(targets['figma']['enabled'])
            self.assertEqual(len(upgraded.sessions()),1)
            self.assertEqual(upgraded.daily()['1970-01-01']['total'],100)
            upgraded.db.close()
            reopened=Store(path)
            self.assertEqual(len(reopened.sessions()),1)
            self.assertEqual(len(reopened.targets()),5)
            reopened.db.close()
