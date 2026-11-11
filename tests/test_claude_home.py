"""Claude Code's own set-up puts its launcher under %HOME% when that variable is set, while its message names the
user folder: a friend's "installed but it doesn't start" with nothing found. IEUStudia looks where it really is."""
import os
import sys

import pytest

from studia import ai, ai_setup, health

NAME = "claude.exe" if sys.platform == "win32" else "claude"

# what `claude install latest` (2.1.322) printed with HOME set to another folder than the user folder
OUTPUT = """Checking installation status...
Installing Claude Code native build latest...
Setting up launcher and shell integration...
Claude Code successfully installed!
Version: 2.1.322
Location: {home}\\.local\\bin\\claude.exe
Next: Run claude --help to get started
Setup notes:
Native installation exists but {other}\\.local\\bin is not in your PATH. Add it by opening: System Properties"""


@pytest.fixture
def pc(tmp_path, monkeypatch):
    """A PC whose user folder is home and whose %HOME% is other; no Claude Code anywhere else (PATH has no npm)."""
    home, other = tmp_path / "home", tmp_path / "other"
    for d in (home, other, tmp_path / "appdata", tmp_path / "local"):
        d.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(other))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_BIN_HOME", raising=False)
    monkeypatch.setattr(ai, "_probe", lambda cmd, timeout=20, env=None: (0, "2.1.322 (Claude Code)\n", ""))
    ai.claude_cli_info.cache_clear()
    ai._failed.clear()
    yield home, other
    ai.claude_cli_info.cache_clear()


def _put(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / NAME).write_bytes(b"claude")
    return folder / NAME


def test_claude_code_under_home_is_found(pc):
    home, other = pc
    exe = _put(other / ".local" / "bin")
    assert str(exe) in ai._candidates()
    info = ai.claude_cli_info()
    assert info["available"] and os.path.normcase(info["path"]) == os.path.normcase(str(exe))


def test_the_folder_claude_install_named_is_remembered(pc):
    home, other = pc
    _put(other / ".local" / "bin")
    lines = OUTPUT.format(home=home, other=other).splitlines()
    said = ai_setup._remember_location(lines)
    assert os.path.normcase(said) == os.path.normcase(str(other / ".local" / "bin"))
    assert os.path.normcase(ai_setup.claude_bin_dirs()[0]) == os.path.normcase(said)


def test_a_named_folder_without_claude_is_not_remembered(pc):
    home, other = pc
    lines = OUTPUT.format(home=home, other=other).splitlines()
    assert ai_setup._remember_location(lines) == ""
    assert not (ai_setup.tools_dir() / ai_setup.CLAUDE_LOCATION).exists()


def test_the_build_is_handed_over_where_claude_install_looks(pc):
    home, other = pc
    assert ai_setup.claude_versions_dir() == other / ".local" / "share" / "claude" / "versions"


def test_copy_details_say_where_claude_code_was_looked_for_without_the_users_folder(pc):
    home, other = pc
    _put(other / ".local" / "bin")
    text = health.details()
    assert "AI programs:" in text and "Claude Code: 2.1.322 at %HOME%" in text
    assert str(home) not in text and str(other) not in text
