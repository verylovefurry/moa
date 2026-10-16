"""Per-user Windows login startup. No administrator privileges required."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

try:
    import winreg
except ImportError:
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Moa"


def installed_executable():
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs" / "Moa" / "Moa.exe"


def is_test_build():
    """A staged build carries its test identity even when moved or renamed."""
    if not getattr(sys, "frozen", False):
        return False
    root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    try:
        info = json.loads((root / "build-info.json").read_text(encoding="utf-8"))
        return isinstance(info, dict) and info.get("channel") == "test"
    except (OSError, ValueError):
        return False


def startup_disabled_reason(data_directory, port=3847, disabled=False, *, frozen=None, test_mode=False):
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if disabled:
        return "실행 옵션으로 Windows 자동 실행 등록을 사용하지 않습니다."
    if not frozen:
        return "소스 코드 실행에서는 Windows 자동 실행을 등록하지 않습니다."
    if test_mode or os.environ.get("MOA_TEST_MODE") or is_test_build():
        return "테스트 모드에서는 Windows 자동 실행을 등록하지 않습니다."
    production_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Moa"
    if os.environ.get("MOA_DATA_DIR") or Path(data_directory).resolve() != production_data.resolve():
        return "별도 데이터 폴더를 사용하는 실행에서는 자동 실행을 등록하지 않습니다."
    if port != 3847:
        return "별도 포트를 사용하는 실행에서는 자동 실행을 등록하지 않습니다."
    return ""


def startup_manager_for_run(data_directory, port=3847, disabled=False, *, frozen=None, executable=None, test_mode=False):
    """Normal portable releases are eligible; explicit test runs stay isolated."""
    if startup_disabled_reason(data_directory, port, disabled, frozen=frozen, test_mode=test_mode):
        return None
    manager = StartupManager(executable=executable or sys.executable, test_mode=test_mode)
    return manager if manager.registration_allowed else None


class StartupManager:
    def __init__(self, executable=None, script=None, test_mode=False):
        self.test_mode = bool(test_mode or os.environ.get("MOA_TEST_MODE") or is_test_build())
        if executable is not None:
            self.arguments = [str(Path(executable).resolve()), "--background"]
        elif getattr(sys, "frozen", False):
            self.arguments = [str(Path(sys.executable).resolve()), "--background"]
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            self.arguments = [str(pythonw if pythonw.is_file() else Path(sys.executable)), str(Path(script or Path(__file__).parent / "desktop.py").resolve()), "--background"]

    @property
    def command(self):
        return subprocess.list2cmdline(self.arguments)

    @property
    def registration_allowed(self):
        return not self.test_mode and len(self.arguments) == 2 and Path(self.arguments[0]).suffix.lower() == ".exe"

    def is_enabled(self):
        if winreg is None:
            return False
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, RUN_NAME)
                return value == self.command
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if enabled and not self.registration_allowed:
            raise ValueError("소스·테스트 실행에서는 Windows 자동 실행을 등록할 수 없습니다.")
        if winreg is None:
            if enabled:
                raise RuntimeError("Windows에서만 자동 실행을 등록할 수 있습니다.")
            return
        if enabled:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, self.command)
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                value, _ = winreg.QueryValueEx(key, RUN_NAME)
                if value == self.command:
                    winreg.DeleteValue(key, RUN_NAME)
        except FileNotFoundError:
            pass
