import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend import DesktopBackend
from schedules import save_assignment
from startup import StartupManager
from tracker import Store
from tests.fixtures import seed_test_store


class PersistenceTests(unittest.TestCase):
    def test_abrupt_exit_keeps_last_committed_record(self):
        script = """
import os,sys
from tracker import Store,Tracker
from tests.fixtures import seed_test_store
class Activity:
    supported=True
    def sample(self):return {'exe':'code.exe','idle':0}
store=Store(sys.argv[1],seed=False)
seed_test_store(store)
now=[1000]
tracker=Tracker(store,Activity(),clock=lambda:now[0])
tracker.enabled=True
for second in range(1000,1013):
    now[0]=second
    tracker.tick()
os._exit(0)
"""
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"crash.sqlite3"
            subprocess.run([sys.executable,"-c",script,str(path)],check=True,cwd=Path(__file__).resolve().parent.parent)
            recovered=Store(path,seed=False)
            self.assertEqual(sum(s["end"]-s["start"] for s in recovered.sessions()),12)
            self.assertEqual(recovered.db.execute("PRAGMA quick_check").fetchone()[0],"ok")
            self.assertEqual(recovered.db.execute("PRAGMA synchronous").fetchone()[0],2)
            recovered.db.close()

    def test_shutdown_backup_contains_work_and_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=DesktopBackend(Path(directory),port=0,auto_track=False)
            seed_test_store(backend.store)
            backend.store.add_session("code",1000,1060,"auto")
            save_assignment(backend.store.db,{"title":"백업 테스트","start_date":"2026-10-16"})
            backup_dir=backend.backup_dir
            backend.close()
            self.assertTrue((backup_dir/"latest.sqlite3").is_file())
            self.assertEqual(len(list(backup_dir.glob("moa-*.sqlite3"))),1)
            backup=Store(backup_dir/"latest.sqlite3",seed=False)
            self.assertEqual(len(backup.sessions()),1)
            self.assertEqual(backup.db.execute("SELECT title FROM assignments").fetchone()[0],"백업 테스트")
            backup.db.close()

    def test_desktop_resumes_auto_tracking_without_manual_start(self):
        with tempfile.TemporaryDirectory() as directory:
            backend=DesktopBackend(Path(directory),port=0)
            self.assertTrue(backend.state()["tracker"]["enabled"])
            backend.close()
            resumed=DesktopBackend(Path(directory),port=0)
            self.assertTrue(resumed.state()["tracker"]["enabled"])
            resumed.close()


class FakeRegistry:
    HKEY_CURRENT_USER=1
    KEY_READ=2
    KEY_SET_VALUE=3
    REG_SZ=1

    def __init__(self):
        self.values={"OtherApp":"keep"}

    def __enter__(self):return self
    def __exit__(self,*args):return False
    def OpenKey(self,*args):return self
    def CreateKeyEx(self,*args):return self
    def QueryValueEx(self,key,name):
        if name not in self.values:raise FileNotFoundError(name)
        return self.values[name],self.REG_SZ
    def SetValueEx(self,key,name,reserved,type,value):self.values[name]=value
    def DeleteValue(self,key,name):
        if name not in self.values:raise FileNotFoundError(name)
        del self.values[name]


class StartupTests(unittest.TestCase):
    def test_login_startup_quotes_path_and_only_changes_moa(self):
        registry=FakeRegistry()
        manager=StartupManager(executable=Path("C:/Program Files/Moa/Moa.exe"))
        with patch("startup.winreg",registry):
            self.assertFalse(manager.is_enabled())
            manager.set_enabled(True)
            self.assertTrue(manager.is_enabled())
            self.assertIn('"',registry.values["Moa"])
            self.assertTrue(registry.values["Moa"].endswith("--background"))
            manager.set_enabled(False)
            self.assertFalse(manager.is_enabled())
            self.assertEqual(registry.values["OtherApp"],"keep")
