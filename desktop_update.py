"""Small native update dialog and background release checks."""
import logging
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from updates import ReleaseClient, UpdateError, cached_update, launch_installer
from version import APP_VERSION, GITHUB_REPOSITORY


class UpdateSignals(QObject):
    progress = Signal(int, int)
    completed = Signal(object, str)


class UpdateController(QObject):
    changed = Signal()

    def __init__(self, window, arguments=()):
        super().__init__(window)
        self.window = window
        self.data_dir = window.backend.data_dir
        self.preferences = window.preferences
        self.arguments = list(arguments)
        self.can_install = bool(getattr(sys, "frozen", False))
        self.ready = cached_update(self.data_dir)
        self.busy = False
        self.status = "다음 정상 종료 시 업데이트를 설치합니다." if self.ready else "업데이트를 확인할 수 있습니다."
        self.notes = self.ready.get("notes", "") if self.ready else ""
        self.progress = 100 if self.ready else 0
        self.restart_requested = False
        self.system_shutdown = False
        self.cancel = threading.Event()
        self.signals = UpdateSignals(self)
        self.signals.progress.connect(self.on_progress)
        self.signals.completed.connect(self.on_completed)
        self.timer = QTimer(self)
        self.timer.setInterval(6 * 60 * 60 * 1000)
        self.timer.timeout.connect(self.automatic_check)
        self.badge = QPushButton("업데이트 준비됨 · 다시 시작…")
        self.badge.clicked.connect(self.show_dialog)
        self.badge.setVisible(bool(self.ready))
        window.statusBar().addPermanentWidget(self.badge)
        self.badge.setVisible(bool(self.ready))

    def enable_automatic(self, initial=False):
        if self.can_install and self.preferences.value("auto_updates", True, type=bool):
            self.timer.start()
            QTimer.singleShot(10000 if initial else 0, self.automatic_check)
        else:
            self.timer.stop()
            self.cancel.set()

    def automatic_check(self):
        if self.preferences.value("auto_updates", True, type=bool) and self.can_install:
            self.check()

    def check(self):
        if self.busy or self.ready:
            return
        self.cancel = threading.Event()
        cancel = self.cancel
        self.busy = True
        self.progress = 0
        self.status = "최신 정식 버전을 확인하고 있습니다…"
        self.changed.emit()

        def work():
            try:
                client = ReleaseClient()
                release = client.latest()
                if cancel.is_set():
                    result = None
                elif release and self.can_install:
                    result = client.download(release, self.data_dir, self.signals.progress.emit, cancel.is_set)
                elif release:
                    result = {"source_only": True, "version": release.version, "notes": release.notes}
                else:
                    result = None
                self.signals.completed.emit(result, "")
            except Exception as exc:
                try:
                    self.signals.completed.emit(None, str(exc) if isinstance(exc, UpdateError) else "업데이트 확인 중 오류가 발생했습니다.")
                except RuntimeError:
                    pass  # The application has already exited.

        threading.Thread(target=work, name="moa-update", daemon=True).start()

    @Slot(int, int)
    def on_progress(self, received, total):
        self.progress = round(received * 100 / total)
        self.status = f"새 버전을 내려받고 있습니다… {self.progress}%"
        self.changed.emit()

    @Slot(object, str)
    def on_completed(self, result, error):
        self.busy = False
        if error:
            self.status = error
        elif result and result.get("source_only"):
            self.status = f"{result['version']} 버전이 있습니다. Moa.exe에서 업데이트를 설치해주세요."
            self.notes = result["notes"]
        elif result:
            self.ready = result
            self.notes = result.get("notes", "")
            self.progress = 100
            self.status = f"{result['version']} 버전이 준비되었습니다. 정상 종료 시 설치하거나 지금 다시 시작할 수 있습니다."
            self.badge.show()
            self.window.tray.showMessage("모아 업데이트", f"{result['version']} 버전을 받았습니다. 앱을 정상 종료할 때 설치합니다.")
        else:
            self.status = "업데이트 확인을 중단했습니다." if self.cancel.is_set() else f"최신 정식 버전입니다. ({APP_VERSION})"
        self.changed.emit()

    def show_dialog(self):
        dialog = UpdateDialog(self, self.window)
        if not self.ready and not self.busy:
            self.check()
        dialog.exec()

    def restart(self):
        if self.ready and self.can_install:
            self.restart_requested = True
            self.window.quit_app()

    def finish(self):
        self.cancel.set()
        automatic = self.preferences.value("auto_updates", True, type=bool)
        if self.ready and self.can_install and not self.system_shutdown and (automatic or self.restart_requested):
            try:
                launch_installer(self.data_dir, self.ready, restart=self.restart_requested, arguments=self.arguments)
            except (UpdateError, OSError):
                logging.exception("Update install deferred until next launch")


class UpdateDialog(QDialog):
    def __init__(self, controller, parent):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("모아 업데이트")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(QLabel(f"현재 버전  {APP_VERSION}    ·    {GITHUB_REPOSITORY}"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet("QProgressBar { min-height: 18px; max-height: 18px; border: 1px solid #dfe4e1; text-align: center; }")
        layout.addWidget(self.progress)
        self.notes = QPlainTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setMinimumHeight(150)
        layout.addWidget(self.notes)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("다시 확인")
        self.check_button.clicked.connect(controller.check)
        buttons.addWidget(self.check_button)
        buttons.addStretch()
        self.install_button = QPushButton("설치 후 다시 시작")
        self.install_button.clicked.connect(self.restart)
        buttons.addWidget(self.install_button)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        close.rejected.connect(self.reject)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        controller.changed.connect(self.render)
        self.finished.connect(lambda: controller.changed.disconnect(self.render))
        self.render()

    def render(self):
        controller = self.controller
        self.status.setText(controller.status)
        self.progress.setVisible(controller.busy or bool(controller.ready))
        self.progress.setRange(0, 0 if controller.busy and not controller.progress else 100)
        self.progress.setValue(controller.progress)
        self.notes.setPlainText(controller.notes)
        self.check_button.setEnabled(not controller.busy and not controller.ready)
        self.install_button.setEnabled(bool(controller.ready) and controller.can_install)

    def restart(self):
        self.accept()
        self.controller.restart()
