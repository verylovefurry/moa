"""Install/update Moa for this Windows account and register login startup."""
import argparse
import json
from pathlib import Path
import shutil

from startup import StartupManager, installed_executable


def validate_install_source(source):
    marker = source.with_suffix(".exe.build.json")
    if marker.is_file():
        info = json.loads(marker.read_text(encoding="utf-8"))
        if info.get("channel") == "test":
            raise ValueError("테스트 빌드는 자동 실행에 등록할 수 없습니다. python build_desktop.py로 배포 빌드를 만들어주세요.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="source", type=Path, default=Path(__file__).parent / "release" / "Moa.exe")
    args = parser.parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit("빌드된 Moa.exe를 찾을 수 없습니다.")
    try:
        validate_install_source(source)
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc))
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
