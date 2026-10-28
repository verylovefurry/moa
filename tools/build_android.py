"""Reproducible native APK build. Signing material stays outside version control."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--gradle", help="Path to Gradle 8.14.3 executable (or use checked-in wrapper)")
    args = parser.parse_args()
    wheels = ROOT / "output" / "android-wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    # PyAES is a pure-Python source-only dependency of Telethon. Chaquopy accepts
    # Android/any wheels, so build its official pinned source into an any wheel.
    subprocess.run([sys.executable, "-m", "pip", "wheel", "pyaes==1.6.1", "--no-deps", "--wheel-dir", str(wheels)], check=True)
    android = ROOT / "android"
    if args.release and not (android / "signing.properties").is_file():
        raise SystemExit("Create private android/signing.properties first; see android/README.md")
    environment = dict(os.environ)
    environment.setdefault("ANDROID_HOME", str(Path.home() / "AppData/Local/Android/Sdk"))
    command = args.gradle or str(android / ("gradlew.bat" if os.name == "nt" else "gradlew"))
    task = ":app:assembleRelease" if args.release else ":app:assembleDebug"
    subprocess.run([command, task, "--console=plain"], cwd=android, env=environment, check=True)
    variant = "release" if args.release else "debug"
    source = android / "app/build/outputs/apk" / variant / f"app-{variant}.apk"
    destination = ROOT / ("release" if args.release else "output/package") / ("Moa-Android.apk" if args.release else "Moa-Android-test.apk")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix(".apk.sha256").write_text(digest + "  " + destination.name + "\n", encoding="ascii")
    print(f"Android APK: {destination}")


if __name__ == "__main__":
    main()
