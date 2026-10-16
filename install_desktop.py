"""Install/update Moa for this Windows account and register login startup."""
import argparse
from pathlib import Path
import shutil

from startup import StartupManager, installed_executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", type=Path, default=Path(__file__).parent / "release" / "Moa.exe")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit("빌드된 Moa.exe를 찾을 수 없습니다.")
    installed = installed_executable()
    installed.parent.mkdir(parents=True, exist_ok=True)
    if source != installed.resolve():
        shutil.copy2(source, installed)
    distributed = Path(__file__).parent / "release" / "Moa.exe"
    distributed.parent.mkdir(exist_ok=True)
    if source != distributed.resolve():
        shutil.copy2(source, distributed)
    StartupManager(installed).set_enabled(True)
    print(f"Installed: {installed}")
    print("Windows login startup: enabled (--background)")


if __name__ == "__main__":
    main()
