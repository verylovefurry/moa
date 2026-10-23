import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from desktop_backend import DesktopBackend
from schedules import save_assignment
from startup import StartupManager, installed_executable, startup_manager_for_run, is_test_build, startup_disabled_reason
from install_desktop import validate_install_source
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
            save_assignment(backend.store.db,{"title":"백업 테스트","start_date":"2026-10-16","fee":150000,"net_fee":132912})
            backup_dir=backend.backup_dir
            backend.close()
            self.assertTrue((backup_dir/"latest.sqlite3").is_file())
            self.assertEqual(len(list(backup_dir.glob("moa-*.sqlite3"))),1)
            backup=Store(backup_dir/"latest.sqlite3",seed=False)
            self.assertEqual(len(backup.sessions()),1)
            self.assertEqual(backup.db.execute("SELECT title FROM assignments").fetchone()[0],"백업 테스트")
            self.assertEqual(tuple(backup.db.execute("SELECT fee,net_fee FROM assignments").fetchone()),(150000,132912))
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

    def test_source_and_test_binaries_cannot_register_startup_even_without_cli_flag(self):
        registry=FakeRegistry()
        with patch("startup.winreg",registry):
            for manager in (StartupManager(),StartupManager(executable=Path("output/package/Moa.exe"),test_mode=True)):
                with self.assertRaises(ValueError):
                    manager.set_enabled(True)
            self.assertEqual(registry.values,{"OtherApp":"keep"})

    def test_test_instance_cannot_remove_the_official_registration(self):
        registry=FakeRegistry()
        official=StartupManager(installed_executable())
        test=StartupManager(executable=Path("output/package/Moa.exe"),test_mode=True)
        with patch("startup.winreg",registry):
            official.set_enabled(True)
            test.set_enabled(False)
            self.assertEqual(registry.values["Moa"],official.command)
            official.set_enabled(False)
            self.assertNotIn("Moa",registry.values)

    def test_normal_portable_and_installed_apps_can_register_but_test_runs_cannot(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ",{"LOCALAPPDATA":directory},clear=False):
            with patch.dict("os.environ"):
                import os
                os.environ.pop("MOA_TEST_MODE",None)
                os.environ.pop("MOA_DATA_DIR",None)
                profile=Path(directory)/"Moa"
                installed=installed_executable()
                manager=startup_manager_for_run(profile,frozen=True,executable=installed)
                self.assertIsNotNone(manager)
                # Updater restarts pass an explicit but unchanged data directory.
                self.assertTrue(manager.registration_allowed)
                portable=Path(directory)/"Desktop"/"내 작업"/"Moa.exe"
                portable_manager=startup_manager_for_run(profile,frozen=True,executable=portable)
                self.assertIsNotNone(portable_manager)
                self.assertTrue(portable_manager.registration_allowed)
                self.assertIn(str(portable.resolve()),portable_manager.command)
                for arguments in (
                    {"frozen":False,"executable":installed},
                    {"frozen":True,"executable":portable,"test_mode":True},
                    {"frozen":True,"executable":installed,"port":3848},
                    {"frozen":True,"executable":installed,"disabled":True},
                ):
                    self.assertIsNone(startup_manager_for_run(profile,**arguments))
                self.assertIsNone(startup_manager_for_run(Path(directory)/"test-profile",frozen=True,executable=installed))
                for variable in ("MOA_TEST_MODE","MOA_DATA_DIR"):
                    with patch.dict("os.environ",{variable:"1"}):
                        self.assertIsNone(startup_manager_for_run(profile,frozen=True,executable=installed))

    def test_embedded_test_marker_survives_a_move_to_a_normal_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/"build-info.json"
            with patch("startup.sys.frozen",True,create=True),patch("startup.sys._MEIPASS",directory,create=True):
                self.assertFalse(is_test_build())  # Legacy release without a marker.
                marker.write_text('{"channel":"release"}',encoding="utf-8")
                self.assertFalse(is_test_build())
                marker.write_text('{"channel":"test"}',encoding="utf-8")
                self.assertTrue(is_test_build())
                self.assertFalse(StartupManager(executable=Path(directory)/"Desktop"/"Moa.exe").registration_allowed)
                self.assertIn("테스트 모드",startup_disabled_reason(Path(directory),frozen=True))

    def test_test_installer_source_is_rejected_before_changing_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/"Moa.exe"
            source.write_bytes(b"MZfixture")
            validate_install_source(source)  # Downloaded releases need no sidecar.
            source.with_suffix(".exe.build.json").write_text('{"channel":"test"}',encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_install_source(source)
            source.with_suffix(".exe.build.json").write_text('{"channel":"release"}',encoding="utf-8")
            validate_install_source(source)
