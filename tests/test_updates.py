import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from updates import (Release, ReleaseClient, UpdateError, apply_update, cached_update,
                     child_environment, file_hash, launch_installer,
                     replace_executable, trusted_url, version_tuple, write_json)
from version import GITHUB_REPOSITORY

EXE = b"MZ" + b"native test executable" * 100
DIGEST = hashlib.sha256(EXE).hexdigest()
URL = f"https://github.com/{GITHUB_REPOSITORY}/releases/download/v9.1.0/Moa.exe"


def metadata(version="v9.1.0", **overrides):
    return {"tag_name": version, "body": "변경 사항", "draft": False, "prerelease": False,
            "assets": [{"name": "Moa.exe", "state": "uploaded", "browser_download_url": URL,
                        "digest": "sha256:" + DIGEST, "size": len(EXE), **overrides}]}


def client_response(data):
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(data).encode())
    return ReleaseClient(opener), opener


class ReleaseTests(unittest.TestCase):
    def test_semantic_versions_compare_numbers_and_reject_unsafe_tags(self):
        self.assertGreater(version_tuple("v0.10.0"), version_tuple("0.9.9"))
        for tag in ("main", "../9.1.0", "1.2.3-beta", "1.2", "9.1.0/extra"):
            with self.assertRaises(UpdateError):
                version_tuple(tag)

    def test_latest_requires_no_authentication_for_the_public_repository(self):
        client, opener = client_response(metadata())
        release = client.latest("0.4.0")
        self.assertEqual(release.sha256, DIGEST)
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", dict(request.header_items()))
        self.assertTrue(request.full_url.startswith("https://api.github.com/"))

    def test_downgrade_equal_draft_and_prerelease_are_ignored(self):
        for data in (metadata("v0.3.0"), metadata("v0.4.0"), {**metadata(), "draft": True}, {**metadata(), "prerelease": True}):
            client, _ = client_response(data)
            self.assertIsNone(client.latest("0.4.0"))

    def test_reject_invalid_assets(self):
        values = ({**metadata(), "assets": []}, metadata(digest=""), metadata(size=0),
                  metadata(browser_download_url="https://evil.test/Moa.exe"),
                  metadata(browser_download_url=URL.replace(GITHUB_REPOSITORY, "another/repo")))
        for data in values:
            client, _ = client_response(data)
            with self.assertRaises(UpdateError):
                client.latest("0.4.0")

    def test_public_release_checksum_sidecar_is_supported(self):
        data = metadata(digest=None)
        data["assets"].append({"name": "Moa.exe.sha256", "browser_download_url": URL + ".sha256"})
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(data).encode()), io.BytesIO((DIGEST+"  Moa.exe\n").encode())]
        self.assertEqual(ReleaseClient(opener).latest("0.4.0").sha256, DIGEST)

    def test_https_redirect_hosts_are_restricted(self):
        self.assertTrue(trusted_url("https://release-assets.githubusercontent.com/example"))
        for url in ("http://github.com/x", "https://github.com.evil.test/x", "https://user:secret@github.com/x", "https://github.com:444/x"):
            self.assertFalse(trusted_url(url))

    def test_not_found_offline_and_rate_limits_have_defined_results(self):
        for code in (404, 403, 429):
            opener = Mock()
            opener.open.side_effect = HTTPError(URL, code, "error", {}, None)
            if code == 404:
                self.assertIsNone(ReleaseClient(opener).latest())
            else:
                with self.assertRaises(UpdateError):
                    ReleaseClient(opener).latest()
        opener.open.side_effect = URLError("offline")
        with self.assertRaises(UpdateError):
            ReleaseClient(opener).latest()


class DownloadTests(unittest.TestCase):
    def download(self, root, content=EXE, digest=DIGEST, cancel=lambda: False):
        opener = Mock()
        opener.open.return_value = io.BytesIO(content)
        release = Release("9.1.0", URL, digest, len(EXE), "업데이트 내용")
        return ReleaseClient(opener).download(release, root, cancelled=cancel)

    def test_verified_download_is_cached_and_preserves_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/"moa.sqlite3").write_bytes(b"user database sentinel")
            ready=self.download(root)
            self.assertEqual(cached_update(root)["sha256"], DIGEST)
            self.assertEqual(Path(ready["path"]).read_bytes(), EXE)
            self.assertEqual((root/"moa.sqlite3").read_bytes(), b"user database sentinel")
            self.assertFalse(list((root/"updates").rglob("*.part")))

    def test_truncated_bad_hash_and_cancellation_never_mark_ready(self):
        for content, digest, cancel in ((EXE[:-10],DIGEST,lambda:False),(EXE,"0"*64,lambda:False),(EXE,DIGEST,lambda:True)):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(UpdateError):
                    self.download(directory,content,digest,cancel)
                self.assertIsNone(cached_update(directory))
                self.assertFalse(list(Path(directory).rglob("*.part")))

    def test_cache_is_reverified_and_never_reads_files_outside_update_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            ready=self.download(directory)
            Path(ready["path"]).write_bytes(b"tampered")
            self.assertIsNone(cached_update(directory))
            ready["path"]=str(Path(directory)/"external.exe")
            write_json(Path(directory)/"updates"/"ready.json",ready)
            self.assertIsNone(cached_update(directory))

    def test_current_or_newer_app_does_not_reinstall_cached_version(self):
        with tempfile.TemporaryDirectory() as directory:
            self.download(directory)
            self.assertIsNone(cached_update(directory,"9.1.0"))
            self.assertIsNone(cached_update(directory,"10.0.0"))


class InstallTests(unittest.TestCase):
    def test_atomic_replacement_leaves_only_the_updated_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,target=root/"download.exe",root/"Moa.exe"
            source.write_bytes(EXE)
            target.write_bytes(b"MZold app")
            replace_executable(source,target,DIGEST)
            self.assertEqual(target.read_bytes(),EXE)
            self.assertEqual(set(root.iterdir()), {source, target})

    def test_locked_target_keeps_original_and_cleans_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,target=root/"download.exe",root/"Moa.exe"
            source.write_bytes(EXE)
            target.write_bytes(b"MZold app")
            with patch("updates.os.replace",side_effect=PermissionError("locked")), self.assertRaises(UpdateError):
                replace_executable(source,target,DIGEST,attempts=1)
            self.assertEqual(target.read_bytes(),b"MZold app")
            self.assertEqual(set(root.iterdir()), {source, target})

    def test_failed_staging_keeps_original_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source,target=root/"download.exe",root/"Moa.exe"
            source.write_bytes(EXE)
            target.write_bytes(b"MZold app")

            def interrupted_copy(source, destination):
                Path(destination).write_bytes(b"partial download")
                raise OSError("disk full")

            with patch("updates.shutil.copy2",side_effect=interrupted_copy), self.assertRaises(OSError):
                replace_executable(source,target,DIGEST)
            self.assertEqual(target.read_bytes(),b"MZold app")
            self.assertEqual(set(root.iterdir()), {source, target})

    def test_bad_hash_does_not_touch_installed_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            source,target=Path(directory)/"new.exe",Path(directory)/"Moa.exe"
            source.write_bytes(EXE)
            target.write_bytes(b"original")
            with self.assertRaises(UpdateError):
                replace_executable(source,target,"0"*64)
            self.assertEqual(target.read_bytes(),b"original")

    def test_helper_waits_for_parent_and_preserves_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source=root/"updates"/"9.1.0"/"Moa.exe"
            source.parent.mkdir(parents=True)
            source.write_bytes(EXE)
            target=root/"installed"/"Moa.exe"
            target.parent.mkdir()
            target.write_bytes(b"old application")
            db=root/"moa.sqlite3"
            db.write_bytes(b"preserve schedules and records")
            job=root/"updates"/"pending.json"
            write_json(job,{"path":str(source),"target":str(target),"version":"9.1.0","sha256":DIGEST,"parent_pid":123,"restart":True,"arguments":["--background"]})
            with patch("updates.wait_for_exit") as wait,patch("updates.subprocess.Popen") as restart:
                self.assertEqual(apply_update(job),0)
            wait.assert_called_once_with(123)
            self.assertEqual(target.read_bytes(),EXE)
            self.assertEqual(list(target.parent.iterdir()), [target])
            self.assertEqual(db.read_bytes(),b"preserve schedules and records")
            restart.assert_called_once()
            self.assertEqual(restart.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"],"1")
            self.assertTrue(json.loads((job.parent/"last-result.json").read_text())["ok"])
            self.assertFalse(job.exists())

    def test_invalid_job_cannot_restart_arbitrary_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            target=root/"other.exe"
            target.write_bytes(b"other")
            job=root/"updates"/"pending.json"
            write_json(job,{"path":str(target),"target":str(target),"version":"9.1.0","restart":True})
            with patch("updates.subprocess.Popen") as restart:
                self.assertEqual(apply_update(job),1)
            restart.assert_not_called()
            self.assertEqual(target.read_bytes(),b"other")
