"""Exercise the real frozen updater against isolated files and a real parent PID.

No desktop window, user's database, or Windows startup setting is opened.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import socket
import time
from urllib.request import urlopen
from xml.etree import ElementTree

from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from updates import child_environment,file_hash,write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--exe",type=Path,default=ROOT/"release"/"Moa.exe")
    args=parser.parse_args()
    archive=CArchiveReader(str(args.exe))
    resources={name.replace("\\","/"):name for name in archive.toc}
    manifest=json.loads(archive.extract(resources["assets/reicon/source.json"]))
    assert b"MIT License" in archive.extract(resources["assets/reicon/LICENSE"])
    for name in manifest["icons"]:
        svg=ElementTree.fromstring(archive.extract(resources[f"assets/reicon/{name}.svg"]))
        assert svg.attrib["viewBox"]=="0 0 24 24"
    print(f"Packaged icon assets passed: {len(manifest['icons'])} local SVGs, manifest and MIT license.")
    output=(ROOT/"output").resolve()
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="packaged-update-",dir=output) as directory:
        root=Path(directory).resolve()
        assert root.is_relative_to(output)
        sync_result=root/"sync-runtime.json"
        subprocess.run([str(args.exe.resolve()),"--verify-sync-runtime",str(sync_result)],env=child_environment(),timeout=60,check=True)
        assert json.loads(sync_result.read_text(encoding="utf-8"))["ok"]
        print("Packaged sync runtime passed: SQLite journal, AES-GCM, DPAPI, Telegram client, no network.")
        source=root/"updates"/"9.1.0"/"Moa.exe"
        source.parent.mkdir(parents=True)
        shutil.copy2(args.exe,source)
        target=root/"installed"/"Moa.exe"
        target.parent.mkdir()
        target.write_bytes(b"MZprevious version fixture")
        helper=root/"updates"/"MoaUpdater.exe"
        shutil.copy2(args.exe,helper)
        db=root/"moa.sqlite3"
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("CREATE TABLE preserved(value TEXT)")
            connection.execute("INSERT INTO preserved VALUES ('work records and schedules')")
        before=file_hash(db)
        # The handle is held until this real process exits. The updater must wait.
        parent=subprocess.Popen([sys.executable,"-c","import sys,time; f=open(sys.argv[1],'rb'); time.sleep(2)",str(target)])
        job=root/"updates"/"pending.json"
        digest=file_hash(source)
        write_json(job,{"version":"9.1.0","path":str(source),"target":str(target),"sha256":digest,
                        "parent_pid":parent.pid,"restart":False,"arguments":[]})
        try:
            completed=subprocess.run([str(helper),"--apply-update",str(job)],env=child_environment(),timeout=60)
            parent.wait(timeout=10)
            result=json.loads((root/"updates"/"last-result.json").read_text(encoding="utf-8"))
            assert completed.returncode==0 and result["ok"],result
            assert file_hash(target)==digest
            assert list(target.parent.iterdir())==[target]
            assert file_hash(db)==before
            assert not job.exists()
            print("Frozen updater passed: parent exit, verified replacement, no leftover executable, database preservation.")
            with socket.socket() as available:
                available.bind(("127.0.0.1",0))
                port=available.getsockname()[1]
            environment=child_environment()
            environment["MOA_TEST_MODE"]="1"
            app=subprocess.Popen([str(target),"--background","--data-dir",str(root),"--port",str(port),"--no-startup","--no-update-check"],env=environment)
            try:
                deadline=time.monotonic()+20
                while True:
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/api/state",timeout=1) as response:
                            state=json.load(response)
                        break
                    except OSError:
                        if app.poll() is not None or time.monotonic()>deadline:
                            raise RuntimeError("Packaged native app did not start")
                        time.sleep(0.2)
                assert state["targets"]==[] and state["sessions"]==[]
                assert state["tracker"]["enabled"]
                with closing(sqlite3.connect(db)) as connection:
                    assert connection.execute("SELECT value FROM preserved").fetchone()[0]=="work records and schedules"
                print("Packaged native app passed: clean targets, automatic tracking, existing database preserved.")
            finally:
                if app.poll() is None:
                    # This process and its children belong exclusively to this temporary QA run.
                    subprocess.run(["taskkill","/PID",str(app.pid),"/T","/F"],check=True,stdout=subprocess.DEVNULL,timeout=15)
                app.wait(timeout=15)
                deadline=time.monotonic()+10
                while target.exists():
                    try:
                        target.unlink()
                    except PermissionError:
                        if time.monotonic()>deadline:
                            raise
                        time.sleep(0.1)
        finally:
            if parent.poll() is None:
                parent.terminate()
                parent.wait(timeout=10)


if __name__=="__main__":
    main()
