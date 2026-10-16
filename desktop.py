"""Moa native Windows desktop application (Qt Widgets, no embedded web view)."""
from __future__ import annotations

import argparse
import calendar as calendar_module
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import sys

# The copied update helper never opens Qt, the tracking service, or user data.
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--apply-update":
    from updates import apply_update
    raise SystemExit(apply_update(sys.argv[2]))

from PySide6.QtCore import QDate, QLocale, QSettings, QSize, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPalette
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QSizePolicy, QSplitter, QStackedWidget,
    QStyleFactory, QSystemTrayIcon, QTabBar, QTableWidget, QTableWidgetItem,
    QToolBar, QToolButton, QVBoxLayout, QWidget, QComboBox,
)

from desktop_backend import DesktopBackend, desktop_data_directory, format_clock, format_duration, format_time
from desktop_dialogs import ConnectionDialog, HelpDialog, MonthDialog, SettingsDialog, TargetDialog, muted_label
from desktop_schedule import SchedulePage
from startup import StartupManager
from desktop_update import UpdateController
from updates import cached_update, launch_installer, UpdateError
from version import APP_VERSION
from desktop_widgets import DAY_ROLE, GREEN, LINE, TEXT, DailyChart, RecordDelegate, WorkCalendar, app_icon, color_icon, prepare_table
from tracker import KST, split_by_day

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")

STYLE = """
QMainWindow, QDialog { background: #ffffff; }
QPushButton { min-height: 24px; padding: 2px 10px; }
QComboBox, QLineEdit, QDateEdit, QTimeEdit, QSpinBox { min-height: 24px; }
QLabel[muted="true"] { color: #66736b; }
QWidget#navigationPane { background: #f4f6f5; border-right: 1px solid #dfe4e1; }
QListWidget#navigation { border: 0; background: transparent; outline: 0; }
QListWidget#navigation::item { padding: 8px 11px; border: 0; color: #47564e; }
QListWidget#navigation::item:selected { background: #dfe9e2; color: #274c38; }
QListWidget#navigation::item:hover:!selected { background: #e9eeea; }
QToolBar { background: #ffffff; border: 0; border-bottom: 1px solid #dfe4e1; spacing: 10px; padding: 8px 16px; }
QPushButton#trackingButton { background: #365f4c; color: #ffffff; border: 1px solid #365f4c; border-radius: 4px; padding: 6px 14px; min-height: 18px; }
QPushButton#trackingButton:hover { background: #2b4f3e; }
QPushButton#trackingButton:focus { border: 2px solid #839d8d; padding: 5px 13px; }
QPushButton#trackingButton[tracking="true"] { background: #ffffff; color: #365f4c; border-color: #a4b4aa; }
QTableWidget { background: #ffffff; border: 1px solid #dfe4e1; selection-background-color: #e4eee7; selection-color: #243f2f; }
QHeaderView::section { background: #f5f7f5; color: #637167; border: 0; border-bottom: 1px solid #dfe4e1; padding: 7px 9px; }
QTableWidget#workCalendar QHeaderView::section { padding: 5px 0; }
QTableWidget::item { padding-left: 9px; padding-right: 9px; }
QProgressBar { background: #e7ece8; border: 0; max-height: 5px; min-height: 5px; }
QProgressBar::chunk { background: #7f9d88; }
QSplitter::handle:horizontal { background: #ffffff; width: 13px; }
QStatusBar { background: #f7f8f7; border-top: 1px solid #e1e6e2; color: #66736b; }
QStatusBar::item { border: 0; }
QFrame[divider="true"] { color: #e0e5e1; }
"""


def divider():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Plain)
    line.setProperty("divider", True)
    return line


def heading(text, size=15):
    label = QLabel(text)
    label.setFont(QFont(QApplication.font().family(), size, QFont.Weight.DemiBold))
    return label


def button(text, callback, tooltip=""):
    control = QPushButton(text)
    control.clicked.connect(callback)
    if tooltip:
        control.setToolTip(tooltip)
    return control


class MoaWindow(QMainWindow):
    def __init__(self, backend: DesktopBackend, preferences=None, startup=None):
        super().__init__()
        self.backend = backend
        self.startup = startup
        self.preferences = preferences or QSettings(str(backend.data_dir / "window.ini"), QSettings.Format.IniFormat)
        self.snapshot = backend.state()
        self.selected = self.snapshot["today"]
        today = date.fromisoformat(self.selected)
        self.year, self.month = today.year, today.month
        self.filters = {t["id"] for t in self.snapshot["targets"]}
        self._known_targets = set(self.filters)
        self._signature = ""
        self._closing = False
        self._tray_notice = False
        self._target_ids = []
        self._record_ids = []
        self.setWindowTitle("작업 시간과 일정")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(1050, 720)
        self.resize(1270, 820)
        self.build_menu()
        self.build_toolbar()
        self.build_body()
        self.build_tray()
        self.status_text = QLabel("추적 중지")
        self.statusBar().addWidget(self.status_text, 1)
        self.idle_status = QLabel()
        self.statusBar().addPermanentWidget(self.idle_status)
        self.statusBar().addPermanentWidget(QLabel("  로컬 저장  "))
        self.updater = UpdateController(self)
        geometry = self.preferences.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if not any(screen.availableGeometry().intersects(self.frameGeometry()) for screen in QApplication.screens()):
            self.move(QApplication.primaryScreen().availableGeometry().center() - self.rect().center())
        self.nav.setCurrentRow(0)
        self.refresh(force=True)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)

    def action(self, text, callback, shortcut=None):
        action = QAction(text, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        return action

    def build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("파일(&F)")
        file_menu.addAction(self.action("CSV로 내보내기…", self.export_csv, "Ctrl+Shift+E"))
        file_menu.addSeparator()
        file_menu.addAction(self.action("알림 영역으로 최소화", self.hide_to_tray, "Ctrl+M"))
        file_menu.addAction(self.action("종료(&X)", self.quit_app, "Ctrl+Q"))
        record_menu = menu.addMenu("기록(&R)")
        self.tracking_action = self.action("자동 추적 시작", self.toggle_tracking, "Ctrl+R")
        record_menu.addAction(self.tracking_action)
        record_menu.addSeparator()
        record_menu.addAction(self.action("추적 대상 추가…", self.add_target, "Ctrl+Shift+N"))
        schedule_menu = menu.addMenu("일정(&S)")
        schedule_menu.addAction(self.action("새 의뢰·할 일…", self.add_assignment, "Ctrl+N"))
        view_menu = menu.addMenu("보기(&V)")
        for index, (label, shortcut) in enumerate((("작업 달력", "Alt+1"), ("일정 관리", "Alt+2"), ("시간 통계", "Alt+3"), ("추적 대상", "Alt+4"))):
            view_menu.addAction(self.action(label, lambda checked=False, i=index: self.nav.setCurrentRow(i), shortcut))
        view_menu.addSeparator()
        view_menu.addAction(self.action("오늘로 이동", self.go_today, "Ctrl+T"))
        view_menu.addAction(self.action("연·월 바로 이동…", self.choose_month, "Ctrl+L"))
        view_menu.addAction(self.action("이전 달", lambda: self.change_month(-1), "Alt+Left"))
        view_menu.addAction(self.action("다음 달", lambda: self.change_month(1), "Alt+Right"))
        tools_menu = menu.addMenu("도구(&T)")
        tools_menu.addAction(self.action("설정…", self.open_settings, "Ctrl+,"))
        tools_menu.addAction(self.action("브라우저 확장 연결…", self.open_connection))
        help_menu = menu.addMenu("도움말(&H)")
        help_menu.addAction(self.action("사용 안내", self.open_help, "F1"))
        help_menu.addAction(self.action("업데이트 확인…", self.open_updates))
        help_menu.addAction(self.action("모아 정보", lambda: QMessageBox.about(self, "모아 정보", f"모아 {APP_VERSION}\n자동 작업 시간 기록 · 의뢰와 일정 관리\n\n기록은 매초 이 컴퓨터에 저장됩니다.")))

    def build_toolbar(self):
        toolbar = QToolBar("기록 도구", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(toolbar)
        self.tracking_button = button("자동 추적 시작", self.toggle_tracking, "Ctrl+R · 등록한 프로그램이나 URL이 활성화되면 자동으로 기록")
        self.tracking_button.setObjectName("trackingButton")
        self.tracking_button.setAccessibleName("자동 추적 시작 또는 일시정지")
        toolbar.addWidget(self.tracking_button)
        self.tracking_status = muted_label("추적 중지")
        self.tracking_status.setMinimumWidth(170)
        toolbar.addWidget(self.tracking_status)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)
        toolbar.addWidget(button("새 일정…", self.add_assignment, "Ctrl+N"))

    def build_body(self):
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("navigationPane")
        sidebar.setFixedWidth(148)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 22, 12, 15)
        side.setSpacing(8)
        brand = heading("모아", 18)
        brand.setContentsMargins(10, 0, 0, 0)
        side.addWidget(brand)
        subtitle = muted_label("작업 시간 · 일정")
        subtitle.setContentsMargins(10, 0, 0, 18)
        side.addWidget(subtitle)
        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.setAccessibleName("화면 선택")
        self.nav.setSpacing(2)
        self.nav.setMaximumHeight(176)
        for name in ("작업 달력", "일정 관리", "시간 통계", "추적 대상"):
            item = QListWidgetItem(name)
            item.setSizeHint(QSize(112, 35))
            self.nav.addItem(item)
        side.addWidget(self.nav)
        side.addStretch()
        side.addWidget(button("브라우저 연결…", self.open_connection))
        side.addWidget(button("설정…", self.open_settings, "Ctrl+,"))
        root_layout.addWidget(sidebar)
        self.pages = QStackedWidget()
        self.pages.addWidget(self.build_calendar_page())
        self.schedule_page = SchedulePage(self.backend)
        self.schedule_page.changed.connect(lambda: self.refresh(force=True))
        self.schedule_page.month_requested.connect(self.set_month)
        self.schedule_page.choose_month_requested.connect(self.choose_month)
        self.pages.addWidget(self.schedule_page)
        self.pages.addWidget(self.build_stats_page())
        self.pages.addWidget(self.build_targets_page())
        root_layout.addWidget(self.pages, 1)
        self.nav.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.setCentralWidget(root)

    def page_frame(self, title):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(23, 20, 23, 17)
        layout.setSpacing(15)
        row = QHBoxLayout()
        row.addWidget(heading(title, 16))
        sample = QLabel("샘플 기록 포함")
        sample.setStyleSheet("color: #8b7046; font-size: 11px;")
        sample.setToolTip("기존 샘플 기록이 포함되어 있습니다. 설정에서 샘플만 삭제할 수 있습니다.")
        row.addWidget(sample)
        row.addStretch()
        layout.addLayout(row)
        return page, layout, sample

    def summary_strip(self, layout):
        strip = QHBoxLayout()
        strip.setSpacing(25)
        labels = []
        for text in ("이달 누적", "이번 주", "하루 평균", "기록한 날"):
            column = QVBoxLayout()
            column.setSpacing(5)
            caption = muted_label(text)
            value = QLabel("—")
            value.setFont(QFont(QApplication.font().family(), 15, QFont.Weight.DemiBold))
            column.addWidget(caption)
            column.addWidget(value)
            strip.addLayout(column, 1)
            labels.append((caption, value))
        layout.addLayout(strip)
        layout.addWidget(divider())
        return labels

    def month_controls(self, layout):
        row = QHBoxLayout()
        row.setSpacing(7)
        previous = button("‹", lambda: self.change_month(-1), "이전 달 · Alt+Left")
        previous.setAccessibleName("이전 달")
        previous.setFixedWidth(29)
        following = button("›", lambda: self.change_month(1), "다음 달 · Alt+Right")
        following.setAccessibleName("다음 달")
        following.setFixedWidth(29)
        label = QPushButton()
        label.setMinimumWidth(136)
        label.setObjectName("monthPicker")
        label.setToolTip("연·월을 지정해 바로 이동 · Ctrl+L")
        label.setAccessibleName("연·월 바로 이동")
        label.clicked.connect(self.choose_month)
        row.addWidget(previous)
        row.addWidget(label)
        row.addWidget(following)
        row.addWidget(button("오늘", self.go_today, "Ctrl+T"))
        row.addStretch()
        layout.addLayout(row)
        return row, label

    def build_calendar_page(self):
        page, layout, self.calendar_sample = self.page_frame("작업 달력")
        self.calendar_summary = self.summary_strip(layout)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        content = QVBoxLayout(left)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(10)
        row, self.calendar_month = self.month_controls(content)
        self.filter_button = QToolButton()
        self.filter_button.setText("전체 작업")
        self.filter_button.setToolTip("달력과 날짜별 기록에 표시할 작업 선택")
        self.filter_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.filter_menu = QMenu(self.filter_button)
        self.filter_button.setMenu(self.filter_menu)
        row.addWidget(self.filter_button)
        self.calendar_tabs = QTabBar()
        self.calendar_tabs.setExpanding(False)
        self.calendar_tabs.addTab("월간")
        self.calendar_tabs.addTab("목록")
        row.addWidget(self.calendar_tabs)
        self.calendar_views = QStackedWidget()
        self.calendar = WorkCalendar()
        self.calendar.date_selected.connect(self.select_date)
        self.calendar_views.addWidget(self.calendar)
        self.month_list = QTableWidget()
        prepare_table(self.month_list, ["날짜", "작업", "누적 시간"])
        self.month_list.setAccessibleName("월별 작업 목록")
        self.month_list.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.month_list.setColumnWidth(0, 135)
        self.month_list.setColumnWidth(2, 110)
        self.month_list.itemSelectionChanged.connect(self.list_date_selected)
        self.calendar_views.addWidget(self.month_list)
        self.calendar_tabs.currentChanged.connect(self.calendar_views.setCurrentIndex)
        content.addWidget(self.calendar_views, 1)
        foot = QHBoxLayout()
        self.calendar_foot = muted_label("")
        self.calendar_foot.setWordWrap(False)
        foot.addWidget(self.calendar_foot)
        foot.addStretch()
        foot.addWidget(button("CSV 내보내기…", self.export_csv))
        content.addLayout(foot)
        splitter.addWidget(left)
        splitter.addWidget(self.build_day_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([740, 330])
        self.calendar_splitter = splitter
        layout.addWidget(splitter, 1)
        return page

    def build_day_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 2, 0, 0)
        layout.setSpacing(11)
        self.day_heading = heading("", 12)
        layout.addWidget(self.day_heading)
        day_row = QHBoxLayout()
        day_row.addWidget(muted_label("작업 시간"))
        self.day_total = heading("", 17)
        day_row.addWidget(self.day_total)
        day_row.addStretch()
        layout.addLayout(day_row)
        goal_row = QHBoxLayout()
        self.goal_label = muted_label("")
        self.goal_ratio = muted_label("")
        goal_row.addWidget(self.goal_label)
        goal_row.addStretch()
        goal_row.addWidget(self.goal_ratio)
        layout.addLayout(goal_row)
        self.goal_progress = QProgressBar()
        self.goal_progress.setRange(0, 100)
        self.goal_progress.setTextVisible(False)
        self.goal_progress.setAccessibleName("하루 작업 목표 달성률")
        layout.addWidget(self.goal_progress)
        self.day_table = QTableWidget()
        prepare_table(self.day_table, ["작업 · 구간", "기록 시간"])
        self.day_table.setObjectName("dayRecords")
        self.day_table.setItemDelegate(RecordDelegate(self.day_table))
        self.day_table.verticalHeader().setDefaultSectionSize(58)
        self.day_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.day_table.setColumnWidth(1, 103)
        layout.addWidget(self.day_table, 1)
        self.day_empty = muted_label("이 날짜에는 기록이 없습니다.")
        self.day_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.day_empty)
        record_row = QHBoxLayout()
        self.record_count = muted_label("")
        record_row.addWidget(self.record_count)
        record_row.addStretch()
        layout.addLayout(record_row)
        self.excluded_label = muted_label("")
        self.excluded_label.setStyleSheet("color: #8b7046;")
        self.excluded_label.hide()
        layout.addWidget(self.excluded_label)
        layout.addSpacing(5)
        layout.addWidget(divider())
        layout.addWidget(heading("현재 자동 기록", 10))
        self.live_clock = QLabel("00:00:00")
        self.live_clock.setFont(QFont("Consolas", 22))
        self.live_clock.setAccessibleName("자동 감지된 현재 기록 구간 시간")
        layout.addWidget(self.live_clock)
        self.live_caption = muted_label("등록한 프로그램이나 URL을 사용하면 자동으로 기록합니다.")
        layout.addWidget(self.live_caption)
        return panel

    def build_stats_page(self):
        page, layout, self.stats_sample = self.page_frame("시간 통계")
        self.stats_summary = self.summary_strip(layout)
        row, self.stats_month = self.month_controls(layout)
        row.addWidget(button("CSV 내보내기…", self.export_csv))
        layout.addWidget(heading("일별 작업 시간", 11))
        self.chart = DailyChart()
        self.chart.date_selected.connect(self.chart_date_selected)
        layout.addWidget(self.chart, 2)
        layout.addSpacing(9)
        layout.addWidget(heading("작업별 누적", 11))
        self.stats_table = QTableWidget()
        prepare_table(self.stats_table, ["작업", "누적 시간", "비중"])
        self.stats_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.stats_table.setColumnWidth(1, 160)
        self.stats_table.setColumnWidth(2, 95)
        self.stats_table.setMinimumHeight(185)
        layout.addWidget(self.stats_table, 2)
        layout.addWidget(muted_label("하루 평균은 기록이 있는 날짜만 계산합니다. 그래프의 날짜를 누르면 해당 기록으로 이동합니다."))
        return page

    def build_targets_page(self):
        page, layout, self.targets_sample = self.page_frame("추적 대상")
        row = QHBoxLayout()
        self.target_count = muted_label("")
        row.addWidget(self.target_count, 1)
        row.addWidget(button("추가…", self.add_target, "Ctrl+Shift+N"))
        self.edit_target_button = button("편집…", self.edit_target)
        self.delete_target_button = button("삭제", self.delete_target)
        self.edit_target_button.setEnabled(False)
        self.delete_target_button.setEnabled(False)
        row.addWidget(self.edit_target_button)
        row.addWidget(self.delete_target_button)
        layout.addLayout(row)
        self.targets_table = QTableWidget()
        prepare_table(self.targets_table, ["사용", "작업 이름", "구분", "프로그램 / URL", "이달 누적"])
        self.targets_table.setObjectName("trackingTargets")
        self.targets_table.setAccessibleName("추적 대상 목록")
        self.targets_table.setColumnWidth(0, 52)
        self.targets_table.setColumnWidth(1, 210)
        self.targets_table.setColumnWidth(2, 95)
        self.targets_table.setColumnWidth(4, 115)
        self.targets_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.targets_table.itemSelectionChanged.connect(self.target_selection_changed)
        self.targets_table.itemDoubleClicked.connect(lambda item: self.edit_target() if item.column() != 0 else None)
        layout.addWidget(self.targets_table, 1)
        self.targets_empty = muted_label("추적할 작업이 없습니다. ‘추가’를 눌러 프로그램이나 URL을 등록하세요.")
        layout.addWidget(self.targets_empty)
        layout.addWidget(muted_label("프로그램은 활성 창 기준으로 기록합니다. 사용을 해제해도 이전 기록은 유지됩니다."))
        connection = QHBoxLayout()
        self.connection_status = muted_label("")
        connection.addWidget(self.connection_status, 1)
        connection.addWidget(button("브라우저 연결…", self.open_connection))
        layout.addLayout(connection)
        return page

    def build_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu(self)
        menu.addAction(self.action("모아 열기", self.show_window))
        self.tray_tracking_action = self.action("자동 추적 시작", self.toggle_tracking)
        menu.addAction(self.tray_tracking_action)
        self.tray_total_action = self.action("오늘 0분", lambda: None)
        self.tray_total_action.setEnabled(False)
        menu.addAction(self.tray_total_action)
        menu.addSeparator()
        menu.addAction(self.action("종료", self.quit_app))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def refresh(self, force=False):
        self.snapshot = self.backend.state()
        ids = {target["id"] for target in self.snapshot["targets"]}
        self.filters.update(ids - self._known_targets)
        self._known_targets = ids
        snapshot = self.snapshot
        signature = json.dumps([
            snapshot["today"], snapshot["targets"], snapshot["settings"],
            [(s["id"], s["target_id"], s["start"], int(s["end"] / 60), int((s["end"] - s["start"]) / 60)) for s in snapshot["sessions"]],
            [(e["id"], int(e["end"] / 60)) for e in snapshot["excluded"]],
            snapshot["assignments"], snapshot["assignment_history"],
        ], ensure_ascii=False)
        if force or signature != self._signature:
            self._signature = signature
            self.render_views()
        self.update_tracking()

    def target_map(self):
        return {target["id"]: target for target in self.snapshot["targets"]}

    def month_days(self):
        prefix = f"{self.year}-{self.month:02d}-"
        return [(day, data) for day, data in self.snapshot["daily"].items() if day.startswith(prefix)]

    def filtered_daily(self):
        result = {}
        for key, day in self.snapshot["daily"].items():
            values = {tid: sec for tid, sec in day["targets"].items() if tid in self.filters}
            if values:
                result[key] = {"total": sum(values.values()), "targets": values}
        return result

    def render_views(self):
        for label in (self.calendar_sample, self.stats_sample, self.targets_sample):
            label.setVisible(self.snapshot["has_demo"])
        title = f"{self.year}년 {self.month:02d}월"
        self.calendar_month.setText(title)
        self.stats_month.setText(title)
        month = self.month_days()
        total = sum(day["total"] for _, day in month)
        today = date.fromisoformat(self.snapshot["today"])
        monday = today - timedelta(days=today.weekday())
        week_total = sum(self.snapshot["daily"].get((monday + timedelta(days=i)).isoformat(), {}).get("total", 0) for i in range(7))
        values = [format_duration(total), format_duration(week_total), format_duration(total / len(month) if month else 0), f"{len(month)}일"]
        for labels in (self.calendar_summary, self.stats_summary):
            labels[0][0].setText(f"{self.month}월 누적")
            for (_, label), value in zip(labels, values):
                label.setText(value)
        daily = self.filtered_daily()
        self.calendar.display_month(self.year, self.month, self.selected, self.snapshot["today"], daily, self.snapshot["targets"])
        self.render_filter_menu()
        self.render_month_list(daily)
        self.render_day(daily)
        self.render_targets()
        self.schedule_page.update_data(self.snapshot, self.year, self.month)
        self.chart.set_data(self.year, self.month, self.snapshot["daily"], self.snapshot["today"])
        self.render_stats_table(month, total)
        self.calendar_foot.setText(f"자리 비움 기준: {self.snapshot['settings']['idle_minutes']}분")

    def render_filter_menu(self):
        self.filter_menu.clear()
        ids_with_records = {session["target_id"] for session in self.snapshot["sessions"]}
        targets = [target for target in self.snapshot["targets"] if not target["archived"] or target["id"] in ids_with_records]
        all_action = self.filter_menu.addAction("전체 표시")
        all_action.triggered.connect(self.show_all_filters)
        self.filter_menu.addSeparator()
        for target in targets:
            action = self.filter_menu.addAction(color_icon(target["color"]), target["name"])
            action.setCheckable(True)
            action.setChecked(target["id"] in self.filters)
            action.triggered.connect(lambda checked, tid=target["id"]: self.toggle_filter(tid, checked))
        count = sum(t["id"] in self.filters for t in targets)
        self.filter_button.setText("전체 작업" if count == len(targets) else "표시할 작업 없음" if count == 0 else f"작업 {count}개 표시")

    def show_all_filters(self):
        self.filters = {target["id"] for target in self.snapshot["targets"]}
        self.render_views()

    def toggle_filter(self, target_id, checked):
        if checked:
            self.filters.add(target_id)
        else:
            self.filters.discard(target_id)
        self.render_views()

    def render_month_list(self, daily):
        prefix = f"{self.year}-{self.month:02d}-"
        days = sorted(((day, data) for day, data in daily.items() if day.startswith(prefix)), reverse=True)
        table = self.month_list
        table.blockSignals(True)
        table.setRowCount(len(days))
        names = self.target_map()
        for row, (key, data) in enumerate(days):
            dt = date.fromisoformat(key)
            first = QTableWidgetItem(f"{dt.month}월 {dt.day}일 ({WEEKDAYS[dt.weekday()]})")
            first.setData(DAY_ROLE, key)
            table.setItem(row, 0, first)
            table.setItem(row, 1, QTableWidgetItem(", ".join(names.get(tid, {}).get("name", "작업") for tid in data["targets"])))
            table.setItem(row, 2, QTableWidgetItem(format_duration(data["total"])))
            if key == self.selected:
                table.selectRow(row)
        table.blockSignals(False)

    def render_day(self, daily):
        day = date.fromisoformat(self.selected)
        self.day_heading.setText(f"{day.month}월 {day.day}일 ({WEEKDAYS[day.weekday()]})" + (" · 오늘" if self.selected == self.snapshot["today"] else ""))
        total = daily.get(self.selected, {}).get("total", 0)
        self.day_total.setText(format_duration(total))
        goal = self.snapshot["settings"]["daily_goal"]
        ratio = round(total / (goal * 3600) * 100)
        self.goal_label.setText(f"목표 {goal}시간")
        self.goal_ratio.setText(f"{ratio}%")
        self.goal_progress.setValue(min(100, ratio))
        start = datetime.combine(day, datetime.min.time(), KST).timestamp()
        end = start + 86400
        entries = [session for session in self.snapshot["sessions"] if session["target_id"] in self.filters and session["end"] > start and session["start"] < end and session["end"] > session["start"]]
        old_selection = self.selected_record_id()
        self._record_ids = [entry["id"] for entry in entries]
        self.day_table.blockSignals(True)
        self.day_table.setRowCount(len(entries))
        targets = self.target_map()
        for row, entry in enumerate(entries):
            target = targets.get(entry["target_id"], {})
            clipped_start, clipped_end = max(start, entry["start"]), min(end, entry["end"])
            item = QTableWidgetItem(f"{target.get('name', '작업')}\n{format_time(clipped_start)} — {format_time(clipped_end)}")
            item.setToolTip(f"{target.get('name', '작업')}\n{format_time(clipped_start)} — {format_time(clipped_end)}" + (f"\n{entry['note']}" if entry["note"] else ""))
            self.day_table.setItem(row, 0, item)
            source = {"demo": "샘플", "auto": "자동", "timer": "이전 수동", "manual": "이전 수동"}[entry["source"]]
            second = QTableWidgetItem(f"{format_duration(clipped_end - clipped_start, precise=True)}\n{source}")
            self.day_table.setItem(row, 1, second)
            if entry["id"] == old_selection:
                self.day_table.selectRow(row)
        self.day_table.blockSignals(False)
        self.record_count.setText(f"{len(entries)}개 기록")
        self.day_empty.setVisible(not entries)
        excluded = sum(max(0, min(end, record["end"]) - max(start, record["start"])) for record in self.snapshot["excluded"])
        self.excluded_label.setVisible(excluded >= 60)
        self.excluded_label.setText(f"자리 비움 {format_duration(excluded)} 제외")

    def render_targets(self):
        targets = [target for target in self.snapshot["targets"] if not target["archived"]]
        previous = self.selected_target_id()
        self._target_ids = [t["id"] for t in targets]
        table = self.targets_table
        table.blockSignals(True)
        table.setRowCount(len(targets))
        for row, target in enumerate(targets):
            enabled = QTableWidgetItem()
            enabled.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, 0, enabled)
            checkbox = QCheckBox()
            checkbox.setAccessibleName(f"{target['name']} 추적 사용")
            checkbox.setChecked(bool(target["enabled"]))
            checkbox.toggled.connect(lambda checked, tid=target["id"]: self.command("target-toggle", {"id": tid, "enabled": checked}))
            cell = QWidget()
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(checkbox)
            table.setCellWidget(row, 0, cell)
            table.setItem(row, 1, QTableWidgetItem(color_icon(target["color"]), target["name"]))
            table.setItem(row, 2, QTableWidgetItem("프로그램" if target["kind"] == "app" else "URL"))
            matcher = QTableWidgetItem(target["matcher"])
            matcher.setToolTip(target["matcher"])
            table.setItem(row, 3, matcher)
            seconds = sum(day["targets"].get(target["id"], 0) for _, day in self.month_days())
            table.setItem(row, 4, QTableWidgetItem(format_duration(seconds)))
            if target["id"] == previous:
                table.selectRow(row)
        table.blockSignals(False)
        self.target_count.setText(f"{len(targets)}개 등록 · {sum(bool(t['enabled']) for t in targets)}개 사용")
        self.targets_empty.setVisible(not targets)
        self.target_selection_changed()


    def render_stats_table(self, month, total):
        values = [(target, sum(day["targets"].get(target["id"], 0) for _, day in month)) for target in self.snapshot["targets"]]
        values = sorted(((target, seconds) for target, seconds in values if seconds > 0), key=lambda row: -row[1])
        self.stats_table.setRowCount(len(values))
        for row, (target, seconds) in enumerate(values):
            self.stats_table.setItem(row, 0, QTableWidgetItem(color_icon(target["color"]), target["name"]))
            self.stats_table.setItem(row, 1, QTableWidgetItem(format_duration(seconds)))
            self.stats_table.setItem(row, 2, QTableWidgetItem(f"{round(seconds / total * 100)}%" if total else "0%"))

    def update_tracking(self):
        tracker = self.snapshot["tracker"]
        labels = {"off": "추적 중지", "waiting": "자동 추적 대기 · 등록한 작업을 사용하세요", "recording": "자동 기록 중", "away": "자리 비움 · 복귀하면 자동 재개", "error": "감지 오류 · 앱을 다시 실행하세요"}
        status = labels.get(tracker["status"], "추적 중지")
        if tracker["enabled"] and not any(not target["archived"] for target in self.snapshot["targets"]):
            status = "추적 대상을 추가하면 자동 기록을 시작합니다"
        active = self.target_map().get(tracker["active"], {}).get("name")
        self.tracking_status.setText(status + (f" · {active}" if active else ""))
        label = "추적 일시정지" if tracker["enabled"] else "자동 추적 시작"
        self.tracking_button.setText(label)
        if self.tracking_button.property("tracking") != tracker["enabled"]:
            self.tracking_button.setProperty("tracking", tracker["enabled"])
            self.tracking_button.style().unpolish(self.tracking_button)
            self.tracking_button.style().polish(self.tracking_button)
        self.tracking_action.setText(label)
        self.tray_tracking_action.setText(label)
        self.status_text.setText(status)
        self.idle_status.setText(f"자리 비움 {self.snapshot['settings']['idle_minutes']}분   ")
        elapsed = self.snapshot["now"] - tracker["session_start"] if tracker["status"] == "recording" and tracker["session_start"] else 0
        self.live_clock.setText(format_clock(elapsed))
        self.live_caption.setText("자리 비움 · 복귀 후 자동으로 이어집니다." if tracker["status"] == "away" else "현재 작업: " + active if active else "등록한 프로그램이나 URL을 사용하면 자동으로 기록합니다." if tracker["enabled"] else "자동 추적이 일시정지되어 있습니다.")
        connected = tracker["browser_connected"]
        self.connection_status.setText("URL 감지: 브라우저 연결됨" if connected else "URL 감지: 브라우저 확장 연결 필요")
        today_total = self.snapshot["daily"].get(self.snapshot["today"], {}).get("total", 0)
        self.tray.setToolTip(f"모아 — {status}\n오늘 {format_duration(today_total)}")
        self.tray_total_action.setText(f"오늘 {format_duration(today_total)}")

    def command(self, name, data, message=""):
        try:
            self.backend.command(name, data)
        except (ValueError, RuntimeError) as exc:
            QMessageBox.warning(self, "모아", str(exc))
            return False
        self.refresh(force=True)
        if message:
            self.statusBar().showMessage(message, 4500)
        return True

    def toggle_tracking(self):
        self.command("tracking", {"enabled": not self.snapshot["tracker"]["enabled"]})


    def change_month(self, delta):
        index = min(9999 * 12 + 11, max(1900 * 12, self.year * 12 + self.month - 1 + delta))
        year, month_index = divmod(index, 12)
        self.set_month(year, month_index + 1)

    def set_month(self, year, month):
        self.year, self.month = year, month
        previous = date.fromisoformat(self.selected)
        self.selected = date(year, month, min(previous.day, calendar_module.monthrange(year, month)[1])).isoformat()
        self.render_views()

    def choose_month(self):
        dialog = MonthDialog(self, self.year, self.month)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_month(dialog.year.value(), dialog.month.currentData())

    def add_assignment(self):
        self.nav.setCurrentRow(1)
        self.schedule_page.add_assignment()

    def go_today(self):
        self.select_date(self.snapshot["today"])

    def select_date(self, key):
        self.selected = key
        day = date.fromisoformat(key)
        self.year, self.month = day.year, day.month
        self.render_views()

    def list_date_selected(self):
        row = self.month_list.currentRow()
        if row >= 0 and self.month_list.item(row, 0):
            self.select_date(self.month_list.item(row, 0).data(DAY_ROLE))

    def chart_date_selected(self, key):
        self.select_date(key)
        self.nav.setCurrentRow(0)

    def selected_record_id(self):
        row = self.day_table.currentRow() if hasattr(self, "day_table") else -1
        return self._record_ids[row] if 0 <= row < len(self._record_ids) else None

    def selected_target_id(self):
        row = self.targets_table.currentRow() if hasattr(self, "targets_table") else -1
        return self._target_ids[row] if 0 <= row < len(self._target_ids) else None


    def target_selection_changed(self):
        selected = self.selected_target_id() is not None
        self.edit_target_button.setEnabled(selected)
        self.delete_target_button.setEnabled(selected)

    def add_target(self):
        dialog = TargetDialog(self, self.backend)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh(force=True)
            self.statusBar().showMessage("추적 대상을 추가했습니다.", 4000)

    def edit_target(self):
        target = self.target_map().get(self.selected_target_id())
        if target:
            TargetDialog(self, self.backend, target).exec()
            self.refresh(force=True)

    def delete_target(self):
        target = self.target_map().get(self.selected_target_id())
        if not target:
            return
        answer = QMessageBox.question(self, "추적 대상 삭제", f"‘{target['name']}’을 추적 대상에서 삭제할까요?\n이전 작업 기록은 유지됩니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            self.command("target-delete", {"id": target["id"]}, "추적 대상을 삭제했습니다.")




    def open_settings(self):
        SettingsDialog(self, self.backend, self.preferences, self.startup).exec()
        self.updater.enable_automatic()
        self.refresh(force=True)

    def open_updates(self):
        self.updater.show_dialog()

    def open_connection(self):
        ConnectionDialog(self, self.backend).exec()

    def open_help(self):
        HelpDialog(self).exec()

    def export_csv(self):
        if not self.month_days():
            QMessageBox.information(self, "CSV 내보내기", "선택한 달에는 내보낼 기록이 없습니다.")
            return
        documents = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation))
        filename, _ = QFileDialog.getSaveFileName(self, "작업 기록 내보내기", str(documents / f"모아_작업기록_{self.year}-{self.month:02d}.csv"), "CSV 파일 (*.csv)")
        if filename:
            try:
                count = self.backend.export_csv(Path(filename), self.year, self.month)
            except OSError as exc:
                QMessageBox.warning(self, "CSV 내보내기", f"파일을 저장하지 못했습니다.\n{exc}")
                return
            self.statusBar().showMessage(f"{count}개 기록을 내보냈습니다: {filename}", 7000)

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def hide_to_tray(self):
        if self.tray.isVisible():
            self.hide()
        else:
            self.showMinimized()

    def closeEvent(self, event):
        self.preferences.setValue("geometry", self.saveGeometry())
        if not self._closing and self.preferences.value("close_to_tray", True, type=bool) and self.tray.isVisible():
            self.hide()
            event.ignore()
            if not self._tray_notice:
                self.tray.showMessage("모아", "알림 영역에서 계속 실행됩니다. 완전히 종료하려면 모아 메뉴의 ‘종료’를 선택하세요.", QSystemTrayIcon.MessageIcon.Information, 4000)
                self._tray_notice = True
            return
        event.accept()
        QApplication.quit()

    def quit_app(self):
        self._closing = True
        self.preferences.setValue("geometry", self.saveGeometry())
        self.tray.hide()
        QApplication.quit()


def configure_application(app):
    app.setApplicationName("Moa")
    app.setApplicationDisplayName("모아")
    app.setOrganizationName("Moa")
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Malgun Gothic", 9))
    if "windows11" in QStyleFactory.keys():
        app.setStyle("windows11")
    else:
        app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor("#f7f8f7"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#dfece3"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#234330"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#929a95"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#929a95"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)
    QLocale.setDefault(QLocale(QLocale.Language.Korean, QLocale.Country.SouthKorea))


def main():
    parser = argparse.ArgumentParser(description="모아 Windows 데스크톱 앱")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=3847)
    parser.add_argument("--background", action="store_true", help="Start in the Windows notification area")
    parser.add_argument("--no-startup", action="store_true", help="Do not change login startup (development/testing)")
    parser.add_argument("--no-update-check", action="store_true", help="Disable background update checks for isolated testing")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    configure_application(app)
    data_dir = args.data_dir or desktop_data_directory()
    data_dir.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:
        sys.stdout = (data_dir / "app.log").open("a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    logging.basicConfig(filename=str(data_dir / "errors.log"), level=logging.WARNING, encoding="utf-8")

    def exception_handler(kind, value, traceback):
        logging.error("Unhandled desktop error", exc_info=(kind, value, traceback))
        QMessageBox.critical(None, "모아", f"동작 중 오류가 발생했습니다.\n{value}\n\n문제가 반복되면 앱을 다시 실행해주세요.")

    sys.excepthook = exception_handler
    channel = "moa-desktop-" + hashlib.sha256(str(data_dir.resolve()).lower().encode()).hexdigest()[:16]
    existing = QLocalSocket()
    existing.connectToServer(channel)
    if existing.waitForConnected(350):
        existing.write(b"show")
        existing.waitForBytesWritten(1000)
        return 0
    local = QLocalServer()
    QLocalServer.removeServer(channel)
    if not local.listen(channel):
        QMessageBox.warning(None, "모아", "앱 실행을 준비하지 못했습니다. 잠시 후 다시 실행해주세요.")
        return 1
    preferences = QSettings(str(data_dir / "window.ini"), QSettings.Format.IniFormat)
    restart_arguments = ["--data-dir", str(data_dir.resolve()), "--port", str(args.port)]
    if args.no_startup:
        restart_arguments.append("--no-startup")
    if args.no_update_check:
        restart_arguments.append("--no-update-check")
    if args.background:
        restart_arguments.append("--background")
    if getattr(sys, "frozen", False) and preferences.value("auto_updates", True, type=bool):
        ready = cached_update(data_dir)
        if ready:
            try:
                launch_installer(data_dir, ready, restart=True, arguments=restart_arguments)
                return 0
            except (UpdateError, OSError):
                logging.exception("Cached update install deferred")
    try:
        backend = DesktopBackend(args.data_dir, args.port)
    except RuntimeError as exc:
        QMessageBox.warning(None, "모아 실행", str(exc))
        return 1
    startup = None if args.no_startup else StartupManager()
    startup_error = None
    if startup and preferences.value("start_with_windows", True, type=bool):
        try:
            startup.set_enabled(True)
            preferences.setValue("start_with_windows", True)
        except OSError as exc:
            logging.exception("Windows login startup registration failed")
            startup_error = str(exc)
    window = MoaWindow(backend, preferences, startup)
    window.updater.arguments = restart_arguments
    if not args.no_update_check:
        window.updater.enable_automatic(initial=True)
    if startup_error:
        window.statusBar().showMessage("자동 실행 등록 실패: " + startup_error, 15000)

    def receive_instance():
        client = local.nextPendingConnection()
        if client:
            client.disconnectFromServer()
            client.deleteLater()
        window.show_window()

    local.newConnection.connect(receive_instance)
    app.aboutToQuit.connect(backend.close)
    app.aboutToQuit.connect(window.updater.finish)

    def save_before_shutdown(session):
        window._closing = True
        window.updater.system_shutdown = True
        window.preferences.setValue("geometry", window.saveGeometry())
        backend.flush()

    app.commitDataRequest.connect(save_before_shutdown, Qt.ConnectionType.DirectConnection)
    if not args.background or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
