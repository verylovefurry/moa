"""Offscreen integration checks. These only use temporary desktop databases."""
import csv
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QDate, QEvent, QPoint, QTime
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog
from desktop import MoaWindow, configure_application, parse_arguments
from desktop_backend import DesktopBackend, migrate_legacy_database
from desktop_dialogs import AssignmentDialog, MonthDialog, SettingsDialog, TargetDialog
from schedules import STATUSES, save_assignment
from tracker import Store
from tests.fixtures import seed_test_store
from desktop_update import UpdateDialog
from startup import StartupManager
from tests.test_persistence_startup import FakeRegistry


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        if cls.app.platformName() == "offscreen" and os.name == "nt":
            fonts=Path(os.environ.get("WINDIR","C:/Windows"))/"Fonts"
            for name in ("malgun.ttf","malgunbd.ttf","segoeui.ttf","consola.ttf"):
                if (fonts/name).is_file():
                    QFontDatabase.addApplicationFont(str(fonts/name))
        configure_application(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = DesktopBackend(Path(self.temp.name), port=0, seed=True, auto_track=False)
        seed_test_store(self.backend.store, sessions=True)
        self.window = MoaWindow(self.backend)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.timer.stop()
        self.window.tray.hide()
        self.window.hide()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.backend.close()
        self.temp.cleanup()

    def test_native_views_and_standard_checkboxes(self):
        self.assertEqual(self.window.targets_table.rowCount(), 4)
        self.assertIn(self.window.calendar.rowCount(), (4, 5, 6))
        for row in range(4):
            checkbox = self.window.targets_table.cellWidget(row, 0).findChild(QCheckBox)
            self.assertIsNotNone(checkbox)
            self.assertTrue(checkbox.isChecked())
        for page in (1, 2, 0):
            self.window.nav.setCurrentRow(page)
            self.app.processEvents()
            self.assertEqual(self.window.pages.currentIndex(), page)

    def test_native_target_toggle_persists(self):
        checkbox = self.window.targets_table.cellWidget(0, 0).findChild(QCheckBox)
        checkbox.setChecked(False)
        target = next(t for t in self.backend.state()["targets"] if t["id"] == "figma")
        self.assertFalse(target["enabled"])

    def test_date_selection_crosses_month_without_losing_records(self):
        before = len(self.backend.state()["sessions"])
        self.window.select_date("2026-11-01")
        self.assertEqual((self.window.year, self.window.month), (2026, 11))
        self.assertEqual(self.window.calendar_month.text(), "2026년 11월")
        self.assertEqual(self.window.selected, "2026-11-01")
        self.assertEqual(len(self.backend.state()["sessions"]), before)

    def test_target_filter_applies_to_calendar_and_day_records(self):
        self.window.filters = {"code"}
        self.window.render_views()
        for payload in self.window.filtered_daily().values():
            self.assertEqual(set(payload["targets"]), {"code"})
        for row in range(self.window.day_table.rowCount()):
            self.assertIn("Visual Studio Code", self.window.day_table.item(row, 0).text())

    def test_target_dialog_uses_shared_validation_and_saves(self):
        dialog = TargetDialog(self.window, self.backend)
        dialog.name.setText("블렌더 테스트")
        dialog.matcher.setText("C:\\Apps\\Blender\\blender.exe")
        dialog.save()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        saved = next(t for t in self.backend.state()["targets"] if t["name"] == "블렌더 테스트")
        self.assertEqual(saved["matcher"], "blender.exe")
        dialog.deleteLater()

    def test_automatic_record_csv_splits_at_midnight(self):
        from datetime import datetime
        from tracker import KST
        start = datetime(2099, 11, 1, 23, 30, tzinfo=KST).timestamp()
        self.backend.store.add_session("figma", start, start + 5400, "auto", "=unsafe spreadsheet expression")
        destination = Path(self.temp.name) / "export.csv"
        self.assertEqual(self.backend.export_csv(destination, 2099, 11), 2)
        with destination.open(encoding="utf-8-sig", newline="") as file:
            rows = list(csv.reader(file))
        self.assertEqual([row[4] for row in rows[1:]], ["30.00", "60.00"])
        self.assertTrue(rows[1][6].startswith("'="))

    def test_manual_time_controls_are_removed(self):
        self.assertFalse(hasattr(self.window, "timer_target"))
        self.assertFalse(hasattr(self.window, "timer_button"))
        self.assertFalse(hasattr(self.window, "add_record"))
        self.assertFalse(hasattr(self.window, "delete_record"))

    def test_month_title_opens_picker_and_clamps_day(self):
        self.window.select_date("2026-01-31")
        fake = Mock()
        fake.exec.return_value = QDialog.DialogCode.Accepted
        fake.year.value.return_value = 2028
        fake.month.currentData.return_value = 2
        with patch("desktop.MonthDialog", return_value=fake) as picker:
            self.window.calendar_month.click()
        picker.assert_called_once_with(self.window, 2026, 1)
        self.assertEqual(self.window.selected, "2028-02-29")

    def test_month_picker_extreme_years_do_not_overflow_calendar(self):
        for year,month in ((1900,1),(9999,12)):
            self.window.set_month(year,month)
            self.assertEqual((self.window.year,self.window.month),(year,month))

    def test_assignment_dialog_saves_optional_fields(self):
        dialog = AssignmentDialog(self.window, self.backend)
        dialog.title.setText("개인 작업 정리")
        dialog.start.setDate(QDate(2026, 10, 1))
        dialog.save()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        item = self.backend.state()["assignments"][0]
        self.assertIsNone(item["due_date"])
        self.assertIsNone(item["fee"])
        self.assertEqual(item["client"], "")
        dialog.deleteLater()

    def test_timeline_and_six_status_changes_persist(self):
        self.backend.command("assignments", {"title":"표지 작업", "client":"테스트 의뢰인", "start_date":"2026-10-01", "due_date":"2026-11-02", "fee":500000})
        self.window.set_month(2026, 10)
        self.window.refresh(force=True)
        page = self.window.schedule_page
        self.assertEqual(page.timeline.rowCount(), 1)
        status_control = page.timeline.cellWidget(0, 1).findChild(QComboBox)
        self.assertEqual(status_control.count(), 6)
        self.assertLessEqual(status_control.maximumHeight(), 30)
        assignment_id = self.backend.state()["assignments"][0]["id"]
        for status, _ in STATUSES:
            page.change_status(assignment_id, status)
            self.assertEqual(self.backend.state()["assignments"][0]["status"], status)
        self.assertEqual(len(self.backend.state()["assignment_history"]), 6)
        self.window.set_month(2026, 11)
        self.assertEqual(page.timeline.rowCount(), 1)

    def test_no_deadline_schedule_only_appears_in_its_start_month(self):
        from datetime import datetime
        from tracker import KST
        completed=datetime(2026,11,2,tzinfo=KST).timestamp()
        cases=(
            {"title":"지난달 미완료", "start_date":"2026-09-30"},
            {"title":"지난달 시작 후 완료", "start_date":"2026-09-30", "status":"completed"},
            {"title":"이달 첫날", "start_date":"2026-10-01"},
            {"title":"이달 마지막날", "start_date":"2026-10-31"},
            {"title":"다음달 첫날", "start_date":"2026-11-01"},
            {"title":"마감이 있는 이전달 의뢰", "start_date":"2026-09-30", "due_date":"2026-10-01"},
        )
        for payload in cases:
            save_assignment(self.backend.store.db,payload,now=completed)
        self.window.refresh(force=True)
        page=self.window.schedule_page
        expected={
            9:{"지난달 미완료", "지난달 시작 후 완료", "마감이 있는 이전달 의뢰"},
            10:{"이달 첫날", "이달 마지막날", "마감이 있는 이전달 의뢰"},
            11:{"다음달 첫날"},
        }
        for month,titles in expected.items():
            self.window.set_month(2026,month)
            self.assertEqual({item["title"] for item in page.visible_assignments()},titles)
            self.assertEqual(page.timeline.rowCount(),len(titles))
            self.assertEqual(page.list.rowCount(),len(titles))

    def test_schedule_details_show_full_notes_fields_and_status_history(self):
        note=("전달 파일: 원본과 미리보기\n"+"상세 작업 범위와 요청 사항. "*60).strip()
        self.backend.command("assignments", {"title":"표지 작업", "client":"의뢰인", "start_date":"2026-10-01", "due_date":"2026-10-31", "fee":0, "note":note})
        self.window.refresh(force=True)
        self.window.set_month(2026,10)
        self.window.nav.setCurrentRow(1)
        page=self.window.schedule_page
        assignment_id=page.selected_id
        for status,_ in STATUSES:
            page.change_status(assignment_id,status)
        self.assertEqual(page.detail_title.text(),"표지 작업")
        self.assertEqual(page.detail_status.text(),"최종 완료")
        self.assertEqual({key:label.text() for key,label in page.detail_fields.items()},
                         {"client":"의뢰인", "start_date":"2026-10-01", "due_date":"2026-10-31", "fee":"0원"})
        self.assertEqual(page.detail_note.toPlainText(),note)
        self.assertTrue(page.detail_note.isReadOnly())
        self.assertEqual(page.detail_history.rowCount(),6)
        self.assertEqual(page.detail_history.item(0,1).text(),"보수 수령 → 최종 완료")
        self.assertEqual(page.detail_history.item(5,1).text(),"등록: 미착수")
        self.window.set_month(2026,11)
        self.assertTrue(page.detail_panel.isHidden())
        self.assertEqual(page.detail_note.toPlainText(),"")
        self.assertEqual(page.detail_history.rowCount(),0)

    def test_stats_remain_separate_and_scrollable_when_window_shrinks(self):
        self.window.nav.setCurrentRow(2)
        scroll=self.window.pages.widget(2)
        for width,height in ((1270,820),(1050,720)):
            self.window.resize(width,height)
            self.app.processEvents()
            chart=self.window.chart
            totals=self.window.stats_totals_heading
            table=self.window.stats_table
            self.assertGreaterEqual(chart.height(),chart.minimumHeight())
            self.assertGreater(totals.y(),chart.geometry().bottom())
            self.assertGreater(table.y(),totals.geometry().bottom())
            self.assertGreaterEqual(table.height(),table.minimumHeight())
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            self.app.processEvents()
            location=totals.mapTo(scroll.viewport(),QPoint(0,0))
            self.assertGreaterEqual(location.y(),0)
            self.assertLess(location.y()+totals.height(),scroll.viewport().height())

    def test_settings_dialog_persists_tracking_settings(self):
        dialog = SettingsDialog(self.window, self.backend, self.window.preferences)
        dialog.idle.setCurrentIndex(dialog.idle.findData(10))
        dialog.goal.setCurrentIndex(dialog.goal.findData(8))
        dialog.save()
        settings = self.backend.state()["settings"]
        self.assertEqual(settings["idle_minutes"], 10)
        self.assertEqual(settings["daily_goal"], 8)
        self.assertTrue(self.window.preferences.value("auto_updates", type=bool))
        dialog.deleteLater()

    def test_portable_app_can_enable_and_disable_login_startup_in_settings(self):
        registry=FakeRegistry()
        portable=Path(self.temp.name)/"Desktop"/"내 작업"/"Moa.exe"
        manager=StartupManager(executable=portable)
        with patch("startup.winreg",registry):
            dialog=SettingsDialog(self.window,self.backend,self.window.preferences,manager)
            self.assertTrue(dialog.start_with_windows.isEnabled())
            dialog.start_with_windows.setChecked(True)
            dialog.save()
            self.assertEqual(dialog.result(),QDialog.DialogCode.Accepted)
            self.assertEqual(registry.values["Moa"],manager.command)
            self.assertTrue(self.window.preferences.value("start_with_windows",type=bool))
            dialog.start_with_windows.setChecked(False)
            dialog.save()
            self.assertNotIn("Moa",registry.values)
            self.assertFalse(self.window.preferences.value("start_with_windows",type=bool))
            dialog.deleteLater()

    def test_test_mode_uses_isolated_data_and_a_free_port_without_extra_flags(self):
        with patch.dict(os.environ),patch("desktop.is_test_build",return_value=False):
            os.environ.pop("MOA_TEST_MODE",None)
            normal=parse_arguments([])
            self.assertFalse(normal.test_mode)
            self.assertEqual(normal.port,3847)
            self.assertIsNone(normal.data_dir)
            test=parse_arguments(["--test-mode"])
            self.assertTrue(test.test_mode)
            self.assertEqual(test.port,0)
            self.assertEqual(test.data_dir.name,"test-profile")
            chosen=Path(self.temp.name)/"chosen-profile"
            custom=parse_arguments(["--test-mode","--data-dir",str(chosen),"--port","3850"])
            self.assertEqual(custom.data_dir,chosen)
            self.assertEqual(custom.port,3850)
            with patch("desktop.is_test_build",return_value=True):
                moved=parse_arguments([])
            self.assertTrue(moved.test_mode)
            self.assertEqual(moved.port,0)
            self.assertEqual(moved.data_dir,test.data_dir)

    def test_update_dialog_progress_ready_and_restart_controls(self):
        controller = self.window.updater
        dialog = UpdateDialog(controller, self.window)
        controller.busy = True
        controller.on_progress(25,100)
        self.assertEqual(dialog.progress.value(),25)
        self.assertFalse(dialog.check_button.isEnabled())
        controller.can_install = True
        controller.on_completed({"version":"9.1.0","notes":"작업 기록을 유지합니다."}, "")
        self.assertTrue(dialog.install_button.isEnabled())
        self.assertFalse(controller.badge.isHidden())
        self.assertEqual(dialog.notes.toPlainText(),"작업 기록을 유지합니다.")
        with patch.object(self.window,"quit_app") as quit_app:
            dialog.install_button.click()
        self.assertTrue(controller.restart_requested)
        quit_app.assert_called_once()
        dialog.deleteLater()

    def test_automatic_checks_are_disabled_for_source_and_explicit_opt_out(self):
        controller=self.window.updater
        controller.can_install=False
        controller.enable_automatic()
        self.assertFalse(controller.timer.isActive())
        controller.can_install=True
        self.window.preferences.setValue("auto_updates",False)
        with patch.object(controller,"check") as check:
            controller.automatic_check()
        check.assert_not_called()

    def test_empty_calendar_remains_usable(self):
        self.backend.command("clear-demo", {})
        self.window.refresh(force=True)
        self.assertEqual(self.window.day_table.rowCount(), 0)
        self.assertFalse(self.window.day_empty.isHidden())
        self.assertFalse(self.window.live_clock.isHidden())
        self.assertFalse(hasattr(self.window, "delete_record_button"))

    def test_minimum_window_keeps_controls_in_bounds(self):
        self.window.resize(1050, 720)
        self.app.processEvents()
        self.assertGreaterEqual(self.window.calendar.width(), 470)
        self.assertGreaterEqual(self.window.live_clock.width(), 100)
        self.assertGreaterEqual(self.window.day_table.width(), 285)


class MigrationTests(unittest.TestCase):
    def test_backup_preserves_data_without_replacing_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            source = Store(root / "data" / "moa.sqlite3", seed=False)
            seed_test_store(source)
            source.add_session("code", 1000, 2000, "manual", "preserve me")
            destination = root / "native.sqlite3"
            with patch("desktop_backend.__file__", str(root / "desktop_backend.py")), patch.dict(os.environ):
                os.environ.pop("MOA_DATA_DIR", None)
                self.assertTrue(migrate_legacy_database(destination))
                self.assertFalse(migrate_legacy_database(destination))
            copied = Store(destination, seed=False)
            self.assertEqual(copied.sessions()[0]["note"], "preserve me")
            self.assertTrue((root / "data" / "moa.sqlite3").exists())
            copied.db.close()
            source.db.close()


if __name__ == "__main__":
    unittest.main()
