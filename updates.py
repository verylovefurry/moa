"""Public GitHub releases and an independent, transactional Windows updater.

Only the executable is replaced. SQLite, settings, and backups are never touched.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from version import APP_VERSION, GITHUB_REPOSITORY, RELEASE_ASSET

MAX_DOWNLOAD = 250 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{64}")


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        raise UpdateError("정식 버전 번호를 확인하지 못했습니다.")
    return tuple(int(part) for part in match.groups())


def file_hash(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".tmp")
    with pending.open("w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    pending.replace(path)


def trusted_url(url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        return parsed.scheme == "https" and not parsed.username and not parsed.password and parsed.port in (None, 443) and (
            host in ("api.github.com", "github.com") or host.endswith(".githubusercontent.com")
        )
    except ValueError:
        return False


class GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not trusted_url(newurl):
            raise UpdateError("다운로드 주소의 보안 연결을 확인하지 못했습니다.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""


class ReleaseClient:
    def __init__(self, opener=None):
        self.opener = opener or build_opener(GitHubRedirects())

    def open(self, url, accept="application/vnd.github+json"):
        if not trusted_url(url):
            raise UpdateError("업데이트 주소가 올바르지 않습니다.")
        request = Request(url, headers={"User-Agent": f"Moa/{APP_VERSION}", "Accept": accept, "X-GitHub-Api-Version": "2026-03-10"})
        return self.opener.open(request, timeout=20)

    def latest(self, current=APP_VERSION):
        try:
            with self.open(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest") as response:
                raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise UpdateError("릴리스 응답이 너무 큽니다.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise UpdateError("릴리스 정보 형식이 올바르지 않습니다.")
            if data.get("draft") or data.get("prerelease"):
                return None
            tag = data.get("tag_name", "")
            if version_tuple(tag) <= version_tuple(current):
                return None
            asset = next((item for item in data.get("assets", []) if item.get("name") == RELEASE_ASSET and item.get("state") == "uploaded"), None)
            if not asset:
                raise UpdateError("새 버전의 Windows 실행 파일이 아직 준비되지 않았습니다.")
            url = asset.get("browser_download_url", "")
            parsed = urlsplit(url)
            expected = f"/{GITHUB_REPOSITORY}/releases/download/{tag}/{RELEASE_ASSET}"
            if not trusted_url(url) or parsed.hostname != "github.com" or unquote(parsed.path) != expected or parsed.query or parsed.fragment:
                raise UpdateError("릴리스 파일 주소를 확인하지 못했습니다.")
            digest = str(asset.get("digest") or "").removeprefix("sha256:")
            if not HASH.fullmatch(digest):
                # Older uploads may lack GitHub's digest. Our publisher includes this sidecar.
                checksum = next((item for item in data.get("assets", []) if item.get("name") == RELEASE_ASSET + ".sha256"), None)
                if not checksum:
                    raise UpdateError("파일의 SHA-256 검증 정보가 없습니다.")
                checksum_url = url + ".sha256"
                if checksum.get("browser_download_url") != checksum_url:
                    raise UpdateError("파일 검증 정보 주소가 올바르지 않습니다.")
                with self.open(checksum_url, "application/octet-stream") as response:
                    digest = response.read(256).decode("ascii").split()[0]
            if not HASH.fullmatch(digest):
                raise UpdateError("파일의 SHA-256 검증 정보가 올바르지 않습니다.")
            size = asset.get("size", 0)
            if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD:
                raise UpdateError("업데이트 파일 크기가 올바르지 않습니다.")
            return Release(tag.removeprefix("v"), url, digest, size, str(data.get("body") or "")[:16000])
        except HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code in (403, 429):
                raise UpdateError("GitHub 요청 한도에 도달했습니다. 잠시 후 다시 확인해주세요.") from exc
            raise UpdateError(f"업데이트 서버에 연결하지 못했습니다. (HTTP {exc.code})") from exc
        except (URLError, OSError, ValueError, KeyError, StopIteration, IndexError, TypeError) as exc:
            raise UpdateError("업데이트 정보를 읽지 못했습니다. 인터넷 연결을 확인해주세요.") from exc

    def download(self, release, data_dir, progress=lambda received, total: None, cancelled=lambda: False):
        directory = Path(data_dir) / "updates" / release.version
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / RELEASE_ASSET
        temporary = directory / (RELEASE_ASSET + ".part")
        digest = hashlib.sha256()
        received = 0
        try:
            with self.open(release.url, "application/octet-stream") as response, temporary.open("wb") as file:
                while True:
                    if cancelled():
                        raise UpdateError("다운로드를 중단했습니다.")
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > release.size or received > MAX_DOWNLOAD:
                        raise UpdateError("다운로드 파일 크기가 릴리스 정보와 다릅니다.")
                    file.write(chunk)
                    digest.update(chunk)
                    progress(received, release.size)
                file.flush()
                os.fsync(file.fileno())
            if received != release.size or digest.hexdigest() != release.sha256:
                raise UpdateError("다운로드 파일 검증에 실패했습니다. 기존 앱은 유지됩니다.")
            with temporary.open("rb") as file:
                if file.read(2) != b"MZ":
                    raise UpdateError("Windows 실행 파일 형식이 올바르지 않습니다.")
            temporary.replace(destination)
            ready = {**asdict(release), "path": str(destination.resolve())}
            write_json(Path(data_dir) / "updates" / "ready.json", ready)
            return ready
        except (URLError, OSError) as exc:
            raise UpdateError("업데이트를 내려받지 못했습니다. 기존 앱은 유지됩니다.") from exc
        finally:
            temporary.unlink(missing_ok=True)


def cached_update(data_dir, current=APP_VERSION):
    try:
        root = (Path(data_dir) / "updates").resolve()
        ready = json.loads((root / "ready.json").read_text(encoding="utf-8"))
        version_tuple(ready["version"])
        source = Path(ready["path"]).resolve()
        if version_tuple(ready["version"]) <= version_tuple(current):
            return None
        if source != root / ready["version"] / RELEASE_ASSET or not HASH.fullmatch(ready["sha256"]):
            return None
        if source.stat().st_size != ready["size"] or file_hash(source) != ready["sha256"]:
            return None
        return ready
    except (OSError, ValueError, KeyError, TypeError, UpdateError):
        return None


def child_environment():
    environment = dict(os.environ)
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def launch_installer(data_dir, ready, target=None, restart=False, arguments=()):
    target = Path(target or sys.executable).resolve()
    root = (Path(data_dir) / "updates").resolve()
    if target.name.lower() != RELEASE_ASSET.lower():
        raise UpdateError("Moa.exe 실행 파일에서 업데이트를 설치해주세요.")
    verified = cached_update(data_dir)
    if not verified or verified["sha256"] != ready.get("sha256") or verified["version"] != ready.get("version"):
        raise UpdateError("저장된 업데이트 파일을 확인하지 못했습니다.")
    helper = root / "MoaUpdater.exe"
    if helper == target:
        raise UpdateError("업데이트 도구와 설치 위치가 같습니다.")
    shutil.copy2(target, helper)
    job = {**verified, "target": str(target), "parent_pid": os.getpid(), "restart": bool(restart), "arguments": list(arguments)}
    job_path = root / "pending.json"
    write_json(job_path, job)
    return subprocess.Popen([str(helper), "--apply-update", str(job_path)], env=child_environment(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)


def wait_for_exit(pid, timeout=120):
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        raise UpdateError("종료할 앱의 프로세스 정보가 올바르지 않습니다.")
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Parent has already exited.
                return
            raise UpdateError("앱 종료를 확인하지 못했습니다.")
        try:
            if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise UpdateError("앱이 종료되지 않아 업데이트를 보류했습니다.")
        finally:
            kernel.CloseHandle(handle)
    else:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.2)
        raise UpdateError("앱이 종료되지 않아 업데이트를 보류했습니다.")


def replace_executable(source, target, expected_hash, attempts=80):
    source, target = Path(source).resolve(), Path(target).resolve()
    if target.name.lower() != "moa.exe" or not HASH.fullmatch(expected_hash) or source == target:
        raise UpdateError("업데이트 설치 경로가 올바르지 않습니다.")
    if file_hash(source) != expected_hash:
        raise UpdateError("설치 직전 파일 검증에 실패했습니다.")
    staged = target.with_name("Moa.update-new")
    try:
        shutil.copy2(source, staged)
        if file_hash(staged) != expected_hash:
            raise UpdateError("설치 파일을 복사하는 중 검증에 실패했습니다.")
        for attempt in range(attempts):
            try:
                # A failed atomic replacement leaves the existing executable intact.
                os.replace(staged, target)
                return
            except PermissionError:
                if attempt + 1 == attempts:
                    raise UpdateError("실행 파일이 사용 중입니다. 기존 버전을 유지합니다.")
                time.sleep(0.25)
    finally:
        staged.unlink(missing_ok=True)


def apply_update(job_path):
    """Invoked in a copied executable, before importing Qt or opening any database."""
    job_path = Path(job_path).resolve()
    job = None
    validated = False
    result = {"ok": False}
    try:
        if job_path.name != "pending.json" or job_path.parent.name != "updates":
            raise UpdateError("업데이트 작업 파일 위치가 올바르지 않습니다.")
        job = json.loads(job_path.read_text(encoding="utf-8"))
        version_tuple(job["version"])
        source = Path(job["path"]).resolve()
        if source != job_path.parent / job["version"] / RELEASE_ASSET:
            raise UpdateError("업데이트 원본 위치가 올바르지 않습니다.")
        target = Path(job["target"]).resolve()
        if not target.is_file() or target.name.lower() != "moa.exe" or target == source:
            raise UpdateError("기존 실행 파일을 찾지 못했습니다.")
        arguments = job.get("arguments", [])
        if not isinstance(arguments, list) or not all(isinstance(arg, str) for arg in arguments):
            raise UpdateError("다시 시작할 앱의 실행 옵션이 올바르지 않습니다.")
        validated = True
        wait_for_exit(job["parent_pid"])
        replace_executable(source, target, job["sha256"])
        result = {"ok": True, "version": job["version"]}
    except Exception as exc:
        result["error"] = str(exc)
    # Clear the ready marker on either outcome to avoid a failed-install restart loop.
    if job_path.name == "pending.json" and job_path.parent.name == "updates":
        write_json(job_path.parent / "last-result.json", result)
        (job_path.parent / "ready.json").unlink(missing_ok=True)
        job_path.unlink(missing_ok=True)
    if validated and job.get("restart") and Path(job["target"]).is_file():
        try:
            subprocess.Popen([job["target"], *job.get("arguments", [])], env=child_environment(), close_fds=True)
        except OSError as exc:
            write_json(job_path.parent / "last-result.json", {**result, "restart_error": str(exc)})
    return 0 if result["ok"] else 1
