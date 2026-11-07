"""The installer writes a new version beside the old one and swaps them only once it is all there
(packaging/update-swap.iss): an update that fails halfway, is cut off or finds the old copy in use leaves a copy that
starts. Before, the old _internal was deleted first, and a cancelled or failed update left an app that couldn't start.

The script's shape is checked always; the behaviour with real (tiny, silent, uninstallable-free) test installers when
Inno Setup is on this PC. They install into the test's own folder under their own AppId: no shortcuts, no registry,
no window."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def test_the_installer_never_deletes_the_old_copy_before_the_new_one_is_in_place():
    iss = (PACKAGING / "IEUStudia.iss").read_text(encoding="utf-8-sig")
    assert "[InstallDelete]" not in iss                               # the old copy is never removed up front
    files = iss.split("[Files]")[1].split("\n[")[0]
    assert 'DestDir: "{app}\\_internal.new"' in files and 'DestName: "IEUStudia.exe.new"' in files
    assert '#include "update-swap.iss"' in iss and "Check: RelaunchNew" in iss
    assert "en.NotSwapped=" in iss and "tr.NotSwapped=" in iss
    for name in ("_internal", "_internal.new", "_internal.old", "IEUStudia.exe", "IEUStudia.exe.new",
                 "IEUStudia.exe.old"):
        assert f'Name: "{{app}}\\{name}"' in iss.split("[UninstallDelete]")[1].split("\n[")[0]
    swap = (PACKAGING / "update-swap.iss").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*\[", swap)                       # (a line starting with [ ends the Code section)


def _iscc():
    for p in (shutil.which("iscc"), os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe")):
        if p and os.path.isfile(p):
            return p
    return None


TEST_ISS = r"""
[Setup]
AppId={{5D0C6E2B-7A1F-4C55-9C1E-IEUSTUDIATEST}
AppName=IEUStudiaSwapTest
AppVersion=1.0
DefaultDirName=%(app)s
UsePreviousAppDir=no
Uninstallable=no
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
OutputDir=%(out)s
OutputBaseFilename=%(name)s
Compression=none
CloseApplications=force
RestartApplications=no

[CustomMessages]
NotSwapped=Not swapped (test)

[Files]
Source: "%(payload)s\IEUStudia.exe"; DestDir: "{app}"; DestName: "IEUStudia.exe.new"; Flags: ignoreversion
Source: "%(payload)s\_internal\*"; DestDir: "{app}\_internal.new"; Flags: ignoreversion recursesubdirs createallsubdirs
%(fail)s

[Code]
#include "%(swap)s"
"""


@pytest.mark.skipif(sys.platform != "win32" or not _iscc(), reason="Inno Setup is not on this PC")
def test_an_update_that_fails_is_cut_off_or_finds_the_old_copy_in_use_leaves_a_copy_that_starts(tmp_path):
    app, out = tmp_path / "app", tmp_path / "out"

    def payload(v):
        p = tmp_path / f"v{v}"
        (p / "_internal" / "sub").mkdir(parents=True)
        (p / "IEUStudia.exe").write_text(f"exe v{v}")
        (p / "_internal" / "version.txt").write_text(f"v{v}")
        (p / "_internal" / "sub" / "a.txt").write_text(f"a v{v}")
        if v == 1:
            (p / "_internal" / "only_in_v1.txt").write_text("stale")
        return p

    def install(v, fail=False):
        name = f"setup-v{v}{'-fail' if fail else ''}"
        script = tmp_path / f"{name}.iss"
        script.write_text(TEST_ISS % {
            "app": app, "out": out, "name": name, "payload": payload(v), "swap": PACKAGING / "update-swap.iss",
            # replacing a file the test holds open fails after the new files are written (Abort, when silent)
            "fail": f'Source: "{tmp_path / f"v{v}" / "IEUStudia.exe"}"; DestDir: "{{app}}\\locked"; DestName: "held.txt"'
                    if fail else ""}, encoding="utf-8")
        built = subprocess.run([_iscc(), "/Q", str(script)], capture_output=True, text=True, timeout=120)
        assert built.returncode == 0, built.stdout + built.stderr
        log = tmp_path / f"{name}.log"
        ran = subprocess.run([str(out / f"{name}.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"],
                             timeout=120)
        return ran.returncode, log.read_text(encoding="utf-8", errors="replace")

    def state():
        left = sorted(p.name for p in app.iterdir() if p.name.endswith((".new", ".old")))
        return ((app / "_internal" / "version.txt").read_text(), (app / "IEUStudia.exe").read_text(), left,
                (app / "_internal" / "only_in_v1.txt").exists())

    assert install(1)[0] == 0 and state() == ("v1", "exe v1", [], True)
    assert install(2)[0] == 0 and state() == ("v2", "exe v2", [], False)        # nothing of v1 left over
    # fails halfway (a file it can't replace): rolled back, the old copy as it was
    (app / "locked").mkdir()
    (app / "locked" / "held.txt").write_text("held")
    with open(app / "locked" / "held.txt", "rb"):
        code, log = install(3, fail=True)
    assert code == 5 and state() == ("v2", "exe v2", [], False)
    shutil.rmtree(app / "locked")
    # cut off earlier (the PC switched off halfway): its leftovers are cleared, and the update goes through
    (app / "_internal.new" / "junk").mkdir(parents=True)
    (app / "IEUStudia.exe.new").write_text("half")
    assert install(4)[0] == 0 and state() == ("v4", "exe v4", [], False)
    # the old copy in use (a program that won't let go of its files): it stays as it was, and is said in the log
    with open(app / "_internal" / "version.txt", "rb"):
        code, log = install(5)
    assert code == 0 and state() == ("v4", "exe v4", [], False) and "The old copy stays" in log
    assert install(6)[0] == 0 and state() == ("v6", "exe v6", [], False)
    # a program started from the old copy during the install (a click on the icon): renames don't stop it, so it is
    # ended before the swap, instead of running on as the old version from _internal.old (left behind)
    shutil.copy(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "PING.EXE", app / "_internal" / "helper.exe")
    running = subprocess.Popen([str(app / "_internal" / "helper.exe"), "-n", "120", "127.0.0.1"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
    try:
        code, log = install(7)
        assert code == 0 and state() == ("v7", "exe v7", [], False) and "Before the swap" in log
        assert running.wait(10) is not None
    finally:
        if running.poll() is None:
            running.kill()
