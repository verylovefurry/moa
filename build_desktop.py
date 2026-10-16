"""Build a single native Windows executable using the existing Python runtime."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys
from version import APP_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", action="store_true", help="Build without replacing a currently running release")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    destination = root / "output" / "package" if args.stage else root / "release"
    (root / "output").mkdir(exist_ok=True)
    build_info = root / "output" / "build-info.json"
    build_info.write_text(json.dumps({"channel":"test" if args.stage else "release","version":APP_VERSION}),encoding="utf-8")
    numbers = tuple(int(part) for part in APP_VERSION.split(".")) + (0,)
    version_info = root / "output" / "version-info.txt"
    version_info.write_text(f'''VSVersionInfo(
      ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f,
                        flags=0, OS=0x40004, fileType=0x1, subtype=0, date=(0,0)),
      kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName','Moa'), StringStruct('FileDescription','Moa Desktop'),
        StringStruct('FileVersion','{APP_VERSION}'), StringStruct('ProductVersion','{APP_VERSION}'),
        StringStruct('ProductName','Moa'), StringStruct('OriginalFilename','Moa.exe')
      ])]), VarFileInfo([VarStruct('Translation',[1033,1200])])])''', encoding="utf-8")
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    icon = assets / "moa.ico"
    from PIL import Image, ImageDraw
    image = Image.new("RGBA", (256, 256))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=48, fill="#365f4c")
    draw.ellipse((56, 56, 200, 200), outline="white", width=15)
    draw.line((128, 84, 128, 132, 164, 152), fill="white", width=15, joint="curve")
    image.save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    command = [
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", "Moa", "--icon", str(icon),
        "--version-file", str(version_info),
        "--distpath", str(destination), "--workpath", str(root / "output" / "build"),
        "--specpath", str(root / "output"),
        "--add-data", f"{root / 'browser-extension'};browser-extension",
        "--add-data", f"{build_info};.",
        "--exclude-module", "PySide6.QtWebEngineCore",
        "--exclude-module", "PySide6.QtWebEngineWidgets",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        str(root / "desktop.py"),
    ]
    # Keep unrelated CLI bundles (Poppler/libheif) out of DLL dependency discovery.
    # Their app-local UCRT stubs can shadow the Windows runtime needed by Qt.
    import PySide6
    import shiboken6
    qt = Path(PySide6.__file__).parent
    python = Path(sys.executable).parent
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    environment = dict(os.environ)
    environment["PATH"] = os.pathsep.join(str(path) for path in (
        qt, Path(shiboken6.__file__).parent, python, python / "DLLs",
        windows / "System32", windows,
    ))
    for dll in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll"):
        if (qt / dll).is_file():
            command[3:3] = ["--add-binary", f"{qt / dll};."]
    subprocess.run(command, cwd=root, env=environment, check=True)
    from PyInstaller.archive.readers import CArchiveReader
    embedded = json.loads(CArchiveReader(str(destination / "Moa.exe")).extract("build-info.json"))
    if embedded != {"channel":"test" if args.stage else "release","version":APP_VERSION}:
        raise RuntimeError("The executable's build identity does not match the requested mode")
    with (destination / "Moa.exe").open("rb") as file:
        digest = hashlib.file_digest(file,"sha256").hexdigest()
    (destination / "Moa.exe.sha256").write_text(digest + "  Moa.exe\n", encoding="ascii")
    (destination / "Moa.exe.build.json").write_text(json.dumps({"channel":"test" if args.stage else "release","version":APP_VERSION,"sha256":digest}),encoding="utf-8")
    print(f"Desktop executable: {destination / 'Moa.exe'}")


if __name__ == "__main__":
    main()
