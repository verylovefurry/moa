"""Windows user-bound DPAPI storage; credentials never enter the public API state."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def protect(payload, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("이 보안 저장소는 Windows에서만 사용할 수 있습니다.")
    buffer = ctypes.create_string_buffer(payload)
    source = Blob(len(payload), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise OSError("기기 연동 정보를 안전하게 저장하거나 읽지 못했습니다.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


class WindowsSyncVault:
    def __init__(self, directory):
        self.path = Path(directory) / "telegram-sync.dpapi"

    def load(self):
        if not self.path.exists():
            return {}
        return json.loads(protect(self.path.read_bytes(), decrypt=True))

    def save(self, config):
        if not config:
            self.path.unlink(missing_ok=True)
            return
        pending = self.path.with_suffix(".pending")
        pending.write_bytes(protect(json.dumps(config).encode()))
        pending.replace(self.path)
