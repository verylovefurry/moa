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
        if winreg is None:
            if enabled:
                raise RuntimeError("Windows에서만 자동 실행을 등록할 수 있습니다.")
            return
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, self.command)
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
