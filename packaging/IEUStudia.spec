# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build of IEUStudia (one folder: dist\IEUStudia\IEUStudia.exe + _internal\).
#   uv run --group build pyinstaller packaging\IEUStudia.spec --noconfirm
# The speech model, the NVIDIA GPU libraries and the optional offline AI model are NOT bundled: the app downloads
# what this PC needs on first start (see src/studia/components.py), which keeps the download small.

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent
SRC = ROOT / "src"
version = re.search(r'__version__ = "([^"]+)"', (SRC / "studia" / "__init__.py").read_text(encoding="utf-8")).group(1)
nums = [int(x) for x in re.findall(r"\d+", version)][:3] + [0]
(Path(SPECPATH) / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({nums[0]}, {nums[1]}, {nums[2]}, 0), prodvers=({nums[0]}, {nums[1]}, {nums[2]}, 0),
                    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'IEUStudia contributors'),
      StringStruct('FileDescription', 'IEUStudia'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'IEUStudia'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'IEUStudia.exe'),
      StringStruct('ProductName', 'IEUStudia'),
      StringStruct('ProductVersion', '{version}')])]),
        VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")

datas = []
for pkg in ("studia", "faster_whisper", "pypdfium2_raw", "pypdfium2", "onnxruntime"):
    datas += collect_data_files(pkg)
binaries = []
for pkg in ("ctranslate2", "pypdfium2_raw", "onnxruntime", "av"):
    binaries += collect_dynamic_libs(pkg)
hiddenimports = collect_submodules("studia") + ["clr", "webview.platforms.winforms", "webview.platforms.edgechromium",
                                               "threadpoolctl"]       # (imported where it is used: engine/transcriber.py)

a = Analysis(
    [str(Path(SPECPATH) / "launcher.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "IPython", "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi", "cefpython3"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IEUStudia",
    icon=str(SRC / "studia" / "assets" / "icon.ico"),
    version=str(Path(SPECPATH) / "version_info.txt"),
    console=False,
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="IEUStudia")
