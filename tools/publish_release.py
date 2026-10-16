"""Publish a validated build; never overwrite an existing version's assets."""
import hashlib
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from version import APP_VERSION,GITHUB_REPOSITORY


def main():
    executable=ROOT/"release"/"Moa.exe"
    checksum=executable.with_suffix(".exe.sha256")
    with executable.open("rb") as file:
        digest=hashlib.file_digest(file,"sha256").hexdigest()
    if checksum.read_text(encoding="ascii").split()[0] != digest:
        raise SystemExit("Release checksum does not match Moa.exe")
    subprocess.run(["gh","release","create","v"+APP_VERSION,str(executable),str(checksum),
                    "--repo",GITHUB_REPOSITORY,"--verify-tag","--title","모아 "+APP_VERSION,
                    "--notes-file",str(ROOT/"RELEASE_NOTES.md"),"--latest"],cwd=ROOT,check=True)


if __name__=="__main__":
    main()
