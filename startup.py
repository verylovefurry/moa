"""Per-user Windows login startup. No administrator privileges required."""
from __future__ import annotations

import os
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


def startup_manager_for_run(data_directory, port=3847, disabled=False, *, frozen=None, executable=None):
    """Only the installed production app may change Windows login startup.

    A source run, staged build, isolated profile, custom port, or explicit test
    mode must not replace the real app's existing Run entry.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if disabled or not frozen or port != 3847:
        return None
    if os.environ.get("MOA_TEST_MODE") or os.environ.get("MOA_DATA_DIR"):
        return None
    production_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Moa"
    if Path(data_directory).resolve() != production_data.resolve():
        return None
    manager = StartupManager(executable=executable or sys.executable)
    return manager if manager.registration_allowed else None


class StartupManager:
    def __init__(self, executable=None, script=None):
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
        return len(self.arguments) == 2 and Path(self.arguments[0]).resolve() == installed_executable().resolve()

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
            raise ValueError("Windows 자동 실행은 설치된 정식 모아에서만 등록할 수 있습니다.")
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
