"""Native Telegram setup. Network actions run in the shared sync service thread."""
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from sync_core import new_pairing_key


class SyncDialog(QDialog):
    def __init__(self, parent, service):
        super().__init__(parent)
        self.service = service
        self.future = None
        self.setWindowTitle("기기 연동")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(16)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName("기기 연동 상태")
        layout.addWidget(self.status)
        intro = QLabel('텔레그램의 저장된 메시지를 사용합니다. 각 기기에서 같은 계정과 연동 키를 사용하세요. '
                       '파일은 연동 키로 암호화됩니다. 연결 후 5분마다, 앱 실행 시 동기화합니다.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.setup = QWidget()
        form = QFormLayout(self.setup)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(12)
        link = QLabel('<a href="https://my.telegram.org/apps">텔레그램 API ID·API hash 발급</a>')
        link.setOpenExternalLinks(True)
        form.addRow(link)
        self.api_id = QLineEdit()
        self.api_id.setMaxLength(12)
        self.api_hash = QLineEdit()
        self.api_hash.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_hash.setMaxLength(32)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("+821012345678")
        form.addRow("API ID", self.api_id)
        form.addRow("API hash", self.api_hash)
        form.addRow("전화번호", self.phone)
        self.key = QLineEdit(new_pairing_key())
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setMaxLength(60)
        self.key.setAccessibleName("기기 간에 공유할 연동 키")
        form.addRow("연동 키", self.key)
        key_buttons = QHBoxLayout()
        self.new_key = QPushButton("새 키 생성")
        self.new_key.clicked.connect(lambda: self.key.setText(new_pairing_key()))
        self.copy = QPushButton("키 복사")
        self.copy.clicked.connect(self.copy_key)
        key_buttons.addWidget(self.new_key)
        key_buttons.addWidget(self.copy)
        key_buttons.addStretch()
        form.addRow("", key_buttons)
        self.send = QPushButton("인증번호 받기")
        self.send.clicked.connect(lambda: self.run("setup", {"api_id": self.api_id.text(), "api_hash": self.api_hash.text(), "phone": self.phone.text(), "pairing_key": self.key.text()}))
        form.addRow("", self.send)
        layout.addWidget(self.setup)
        self.auth = QWidget()
        auth_layout = QFormLayout(self.auth)
        auth_layout.setContentsMargins(0, 0, 0, 0)
        self.code = QLineEdit()
        self.code.setEchoMode(QLineEdit.EchoMode.Password)
        self.code.setAccessibleName("인증번호 또는 2단계 인증 비밀번호")
        auth_layout.addRow("인증", self.code)
        confirm = QPushButton("확인")
        confirm.clicked.connect(self.authorize)
        self.code.returnPressed.connect(self.authorize)
        auth_layout.addRow("", confirm)
        restart = QPushButton("인증 다시 시작")
        restart.clicked.connect(lambda: self.run("cancel-auth"))
        auth_layout.addRow("", restart)
        layout.addWidget(self.auth)
        actions = QHBoxLayout()
        self.sync = QPushButton("지금 동기화")
        self.sync.clicked.connect(lambda: self.run("sync"))
        self.copy_connected = QPushButton("연동 키 복사")
        self.copy_connected.clicked.connect(lambda: self.run("pairing"))
        self.disconnect = QPushButton("연동 해제")
        self.disconnect.clicked.connect(self.unlink)
        for item in (self.sync, self.copy_connected, self.disconnect):
            actions.addWidget(item)
        layout.addLayout(actions)
        self.last = QLabel()
        layout.addWidget(self.last)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color:#a43f32")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)
        self.refresh()

    def copy_key(self):
        QApplication.clipboard().setText(self.key.text())
        self.last.setText("연동 키를 복사했습니다. 다른 기기의 연동 키에 붙여넣으세요.")

    def authorize(self):
        phase = self.service.status()["phase"]
        self.run(phase, {"value": self.code.text()})
        self.code.clear()

    def unlink(self):
        if QMessageBox.question(self, "기기 연동 해제", "이 기기의 연동을 해제할까요? 기기에 저장된 기록은 유지됩니다.", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.run("disconnect")

    def run(self, action, params=None):
        if self.future:
            return
        self.error.clear()
        self.future = self.service.submit(action, params)
        self.refresh()

    def refresh(self):
        if self.future and self.future.done():
            try:
                result = self.future.result()
                if result.get("pairing_key"):
                    QApplication.clipboard().setText(result["pairing_key"])
                    self.last.setText("연동 키를 복사했습니다.")
            except Exception as exc:
                self.error.setText(str(exc))
            self.future = None
        state = self.service.status()
        ready = state["phase"] == "ready"
        busy = state["busy"] or self.future is not None
        self.status.setText(state["message"])
        self.setup.setVisible(not ready)
        self.setup.setEnabled(not busy and state["phase"] == "setup")
        self.auth.setVisible(state["phase"] in ("code", "password"))
        self.auth.setEnabled(not busy)
        self.code.setPlaceholderText("2단계 인증 비밀번호" if state["phase"] == "password" else "텔레그램 인증번호")
        for item in (self.sync, self.copy_connected, self.disconnect):
            item.setVisible(ready)
            item.setEnabled(not busy)
        if state.get("last_sync"):
            self.last.setText("최근 동기화  " + datetime.fromtimestamp(state["last_sync"]).strftime("%m.%d %H:%M") + (f" · 전송 대기 {state['pending']}건" if state["pending"] else ""))
