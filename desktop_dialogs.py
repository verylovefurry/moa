"""Standard Windows-style dialogs; input validation is shared with the backend."""
from __future__ import annotations

from PySide6.QtCore import QDate, Qt, QRegularExpression
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSpinBox, QPlainTextEdit, QVBoxLayout, QWidget,
)

from desktop_widgets import color_icon
from tracker import COLORS, SWITCH_GRACE_MINUTES
from schedules import STATUSES


def muted_label(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setProperty("muted", True)
    return label


class FormDialog(QDialog):
    def __init__(self, parent, title, submit="저장"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.setModal(True)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(22, 20, 22, 18)
        self.layout.setSpacing(16)
        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.form.setHorizontalSpacing(22)
        self.form.setVerticalSpacing(14)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.layout.addLayout(self.form)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #a43f32;")
        self.error.hide()
        self.layout.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText(submit)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        self.layout.addWidget(self.buttons)

    def show_error(self, error):
        self.error.setText(str(error))
        self.error.show()
        self.adjustSize()

    def save(self):
        self.accept()


class TargetDialog(FormDialog):
    def __init__(self, parent, backend, target=None):
        super().__init__(parent, "추적 대상 편집" if target else "추적 대상 추가", "저장" if target else "추가")
        self.backend = backend
        self.target = target
        self.name = QLineEdit((target or {}).get("name", ""))
        self.name.setMaxLength(48)
        self.kind = QComboBox()
        self.kind.addItem("프로그램", "app")
        self.kind.addItem("웹페이지 URL", "url")
        self.matcher = QLineEdit((target or {}).get("matcher", ""))
        self.matcher.setMaxLength(2048)
        self.browse = QPushButton("찾아보기…")
        self.browse.clicked.connect(self.choose_file)
        match_row = QHBoxLayout()
        match_row.setSpacing(7)
        match_row.addWidget(self.matcher, 1)
        match_row.addWidget(self.browse)
        self.running = QComboBox()
        self.running.addItem("실행 중인 프로그램에서 선택", "")
        self.running.currentIndexChanged.connect(self.choose_running)
        self.reload_apps = QPushButton("새로고침")
        self.reload_apps.clicked.connect(self.load_apps)
        running_row = QHBoxLayout()
        running_row.addWidget(self.running, 1)
        running_row.addWidget(self.reload_apps)
        self.running_box = QWidget()
        self.running_box.setLayout(running_row)
        running_row.setContentsMargins(0, 0, 0, 0)
        self.color = QComboBox()
        for label, value in zip(("보라", "파랑", "황토", "초록", "분홍", "회색"), COLORS):
            self.color.addItem(color_icon(value), label, value)
        if target:
            self.kind.setCurrentIndex(0 if target["kind"] == "app" else 1)
            self.color.setCurrentIndex(COLORS.index(target["color"]))
        self.form.addRow("작업 이름(&N)", self.name)
        self.form.addRow("구분(&T)", self.kind)
        self.matcher_label = QLabel("실행 파일(&P)")
        self.matcher_label.setBuddy(self.matcher)
        self.form.addRow(self.matcher_label, match_row)
        self.form.addRow("", self.running_box)
        self.form.addRow("표시 색상(&C)", self.color)
        self.kind.currentIndexChanged.connect(self.update_kind)
        self.update_kind()
        self.load_apps()
        self.name.setFocus()

    def update_kind(self):
        is_app = self.kind.currentData() == "app"
        self.matcher_label.setText("실행 파일(&P)" if is_app else "작업 주소(&U)")
        self.browse.setVisible(is_app)
        self.running_box.setVisible(is_app)
        self.adjustSize()

    def load_apps(self):
        self.running.blockSignals(True)
        self.running.clear()
        self.running.addItem("실행 중인 프로그램에서 선택", "")
        for exe in self.backend.running_apps():
            self.running.addItem(exe, exe)
        self.running.blockSignals(False)

    def choose_running(self):
        exe = self.running.currentData()
        if exe:
            self.matcher.setText(exe)
            if not self.name.text().strip():
                self.name.setText(exe.removesuffix(".exe"))

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, "추적할 프로그램 선택", "", "Windows 실행 파일 (*.exe)")
        if filename:
            self.matcher.setText(filename)
            if not self.name.text().strip():
                from pathlib import Path
                self.name.setText(Path(filename).stem)

    def save(self):
        data = {
            "id": self.target["id"] if self.target else "", "name": self.name.text().strip(),
            "kind": self.kind.currentData(), "matcher": self.matcher.text().strip(),
            "color": self.color.currentData(),
            "icon": self.target["icon"] if self.target and self.target["kind"] == self.kind.currentData() else "app" if self.kind.currentData() == "app" else "globe",
        }
        try:
            self.backend.command("targets", data)
        except (ValueError, RuntimeError) as exc:
            self.show_error(exc)
            return
        self.accept()


class MonthDialog(FormDialog):
    def __init__(self, parent, year, month):
        super().__init__(parent, "연·월 바로 이동", "이동")
        self.setMinimumWidth(340)
        self.year = QSpinBox()
        self.year.setRange(1900, 9999)
        self.year.setSuffix("년")
        self.year.setValue(year)
        self.year.setKeyboardTracking(False)
        self.month = QComboBox()
        for value in range(1, 13):
            self.month.addItem(f"{value:02d}월", value)
        self.month.setCurrentIndex(month - 1)
        self.form.addRow("연도(&Y)", self.year)
        self.form.addRow("월(&M)", self.month)
        self.year.setFocus()
        self.year.selectAll()


class AssignmentDialog(FormDialog):
    def __init__(self, parent, backend, assignment=None, default_date=None):
        super().__init__(parent, "일정 편집" if assignment else "새 의뢰·할 일", "저장" if assignment else "추가")
        self.backend = backend
        self.assignment = assignment
        item = assignment or {}
        self.title = QLineEdit(item.get("title", ""))
        self.title.setMaxLength(120)
        self.client = QLineEdit(item.get("client", ""))
        self.client.setMaxLength(80)
        self.start = QDateEdit(QDate.fromString(item.get("start_date", default_date or backend.state()["today"]), "yyyy-MM-dd"))
        self.start.setMinimumDate(QDate(1900, 1, 1))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.has_due = QCheckBox("마감일 있음")
        self.has_due.setChecked(bool(item.get("due_date")))
        self.due = QDateEdit(QDate.fromString(item.get("due_date") or self.start.date().toString("yyyy-MM-dd"), "yyyy-MM-dd"))
        self.due.setMinimumDate(QDate(1900, 1, 1))
        self.due.setDisplayFormat("yyyy-MM-dd (ddd)")
        self.due.setCalendarPopup(True)
        self.due.setEnabled(self.has_due.isChecked())
        self.has_due.toggled.connect(self.due.setEnabled)
        self.start.dateChanged.connect(lambda value: self.due.setDate(value) if not self.has_due.isChecked() else None)
        due_row = QHBoxLayout()
        due_row.addWidget(self.has_due)
        due_row.addWidget(self.due, 1)
        self.has_fee = QCheckBox("보수 있음")
        self.has_fee.setChecked(item.get("fee") is not None)
        self.fee = QLineEdit(f"{item['fee']:,}" if item.get("fee") is not None else "")
        self.fee.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9,]{0,15}"), self.fee))
        self.fee.setEnabled(self.has_fee.isChecked())
        self.has_fee.toggled.connect(self.fee.setEnabled)
        fee_row = QHBoxLayout()
        fee_row.addWidget(self.has_fee)
        fee_row.addWidget(self.fee, 1)
        fee_row.addWidget(QLabel("원"))
        self.status = QComboBox()
        for value, label in STATUSES:
            self.status.addItem(label, value)
        self.status.setCurrentIndex(self.status.findData(item.get("status", "not_started")))
        self.note = QPlainTextEdit(item.get("note", ""))
        self.note.setMaximumHeight(85)
        self.form.addRow("의뢰·할 일(&T)", self.title)
        self.form.addRow("의뢰인(&C)", self.client)
        self.form.addRow("시작일(&S)", self.start)
        self.form.addRow("마감", due_row)
        self.form.addRow("보수", fee_row)
        self.form.addRow("진행 상태(&P)", self.status)
        self.form.addRow("메모(&N)", self.note)
        self.title.setFocus()

    def save(self):
        try:
            fee = None
            if self.has_fee.isChecked():
                digits = self.fee.text().replace(",", "").strip()
                if not digits:
                    raise ValueError("보수 금액을 입력해주세요.")
                fee = int(digits)
            self.backend.command("assignments", {
                "id": self.assignment["id"] if self.assignment else None,
                "title": self.title.text(), "client": self.client.text(),
                "start_date": self.start.date().toString("yyyy-MM-dd"),
                "due_date": self.due.date().toString("yyyy-MM-dd") if self.has_due.isChecked() else None,
                "fee": fee, "status": self.status.currentData(), "note": self.note.toPlainText(),
            })
        except (ValueError, RuntimeError) as exc:
            self.show_error(exc)
            return
        self.accept()


class SettingsDialog(FormDialog):
    def __init__(self, parent, backend, preferences, startup=None):
        super().__init__(parent, "설정")
        self.backend = backend
        self.preferences = preferences
        self.startup = startup
        snapshot = backend.state()
        self.idle = QComboBox()
        for value in (1, 3, 5, 10, 15, 30):
            self.idle.addItem(f"{value}분", value)
        self.idle.setCurrentIndex(self.idle.findData(snapshot["settings"]["idle_minutes"]))
        self.switch_grace = QComboBox()
        for value in SWITCH_GRACE_MINUTES:
            self.switch_grace.addItem(f"{value}분" if value else "즉시 중지", value)
        self.switch_grace.setCurrentIndex(self.switch_grace.findData(snapshot["settings"]["switch_grace_minutes"]))
        self.goal = QComboBox()
        for value in (2, 4, 6, 8, 10, 12):
            self.goal.addItem(f"{value}시간", value)
        self.goal.setCurrentIndex(self.goal.findData(snapshot["settings"]["daily_goal"]))
        self.to_tray = QCheckBox("창을 닫으면 알림 영역에서 계속 실행")
        self.to_tray.setChecked(preferences.value("close_to_tray", True, type=bool))
        self.form.addRow("자리 비움 기준(&I)", self.idle)
        self.form.addRow("창 전환 후 이어 기록(&W)", self.switch_grace)
        self.form.addRow("하루 작업 목표(&G)", self.goal)
        self.form.addRow("닫기 동작", self.to_tray)
        self.start_with_windows = QCheckBox("Windows 로그인 시 알림 영역에서 자동 실행")
        self.start_with_windows.setChecked(startup.is_enabled() if startup else False)
        self.start_with_windows.setEnabled(startup is not None)
        self.form.addRow("자동 실행", self.start_with_windows)
        if startup is None:
            reason = getattr(parent, "startup_disabled_reason", "이 실행에서는 Windows 자동 실행을 등록하지 않습니다.")
            self.start_with_windows.setToolTip(reason)
            self.start_with_windows.setAccessibleDescription(reason)
        self.auto_updates = QCheckBox("새 버전을 자동으로 내려받고 정상 종료 시 설치")
        self.auto_updates.setChecked(preferences.value("auto_updates", True, type=bool))
        self.form.addRow("업데이트", self.auto_updates)
        location = QLineEdit(str(backend.data_dir))
        location.setReadOnly(True)
        self.form.addRow("데이터 저장 위치", location)
        backups = QLineEdit(str(backend.backup_dir))
        backups.setReadOnly(True)
        self.form.addRow("백업 위치", backups)
        if snapshot["has_demo"]:
            clear = QPushButton("샘플 기록 삭제…")
            clear.clicked.connect(self.clear_demo)
            self.form.addRow("샘플 데이터", clear)
            self.clear_button = clear
        self.idle.setFocus()

    def clear_demo(self):
        answer = QMessageBox.question(self, "샘플 기록 삭제", "화면 확인용 샘플 기록을 모두 삭제할까요?\n실제 측정하거나 직접 추가한 기록은 유지됩니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Yes:
            try:
                self.backend.command("clear-demo", {})
            except (ValueError, RuntimeError) as exc:
                self.show_error(exc)
                return
            self.clear_button.setEnabled(False)
            self.clear_button.setText("샘플 기록 삭제됨")

    def save(self):
        try:
            self.backend.command("settings", {"idle_minutes": self.idle.currentData(), "daily_goal": self.goal.currentData(), "switch_grace_minutes": self.switch_grace.currentData()})
        except (ValueError, RuntimeError) as exc:
            self.show_error(exc)
            return
        self.preferences.setValue("close_to_tray", self.to_tray.isChecked())
        self.preferences.setValue("auto_updates", self.auto_updates.isChecked())
        try:
            if self.startup:
                self.startup.set_enabled(self.start_with_windows.isChecked())
                self.preferences.setValue("start_with_windows", self.start_with_windows.isChecked())
        except OSError as exc:
            self.show_error(f"Windows 자동 실행 설정을 저장하지 못했습니다: {exc}")
            return
        self.accept()


class ConnectionDialog(QDialog):
    def __init__(self, parent, backend):
        super().__init__(parent)
        self.setWindowTitle("브라우저 확장 연결")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(16)
        form = QFormLayout()
        code_row = QHBoxLayout()
        code = QLineEdit(backend.state()["settings"]["extension_token"])
        code.setReadOnly(True)
        code.setMaximumWidth(160)
        code.setFont(QFont("Consolas", 14))
        copy = QPushButton("복사")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(code.text()))
        code_row.addWidget(code)
        code_row.addWidget(copy)
        code_row.addStretch()
        form.addRow("연결 코드", code_row)
        path = QLineEdit(str(backend.extension_dir))
        path.setReadOnly(True)
        form.addRow("확장 폴더", path)
        layout.addLayout(form)
        instructions = QLabel("1. 작업에 사용하는 브라우저에서 확장 관리 페이지를 엽니다.\n    Chrome: chrome://extensions   ·   Edge: edge://extensions\n\n2. 개발자 모드를 켜고 ‘압축해제된 확장 프로그램을 로드합니다’에서\n    위 확장 폴더를 선택합니다.\n\n3. 확장 아이콘을 눌러 연결 코드를 입력한 뒤,\n    모아에서 자동 추적을 시작합니다.")
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        connected = backend.state()["tracker"]["browser_connected"]
        layout.addWidget(muted_label("상태: 브라우저 연결됨" if connected else "상태: 연결된 브라우저 없음"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HelpDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("모아 사용 안내")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(17)
        title = QLabel("모아 · 작업 시간 기록")
        title.setFont(QFont(QApplication.font().family(), 13, QFont.Weight.DemiBold))
        layout.addWidget(title)
        text = QLabel("자동 기록\n앱이 실행되면 등록한 프로그램 또는 활성 URL을 자동으로 감지합니다.\n수동 시간 추가·수동 타이머·기록 수정은 지원하지 않습니다.\n\n자리 비움\n설정한 시간 이상 입력이 없으면 마지막 입력 이후 시간을 제외하고,\n복귀하여 작업을 시작하면 자동으로 이어집니다.\n\n일정 관리\n새 의뢰·할 일에 시작일, 의뢰인, 마감일, 보수를 입력합니다.\n타임라인에서 6단계 상태를 변경할 수 있으며 변경 이력도 보관됩니다.\n\n자동 실행과 저장\nWindows 로그인 시 알림 영역에서 실행됩니다. 설정에서 변경할 수 있습니다.\n기록은 매초 저장하고 일별·종료 시 백업합니다. 강제 종료 시에는\n마지막 저장 시점까지 복구하며 꺼져 있던 시간은 합산하지 않습니다.\n\n주요 단축키\nCtrl+L  연·월 바로 이동      Ctrl+N  새 일정\nCtrl+R  자동 추적 일시정지 / 재개\nCtrl+Shift+N  추적 대상 추가     Ctrl+Shift+E  CSV 내보내기\nAlt+1 / Alt+2 / Alt+3 / Alt+4  달력 / 일정 / 통계 / 추적 대상\nCtrl+Q  완전히 종료")
        text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
