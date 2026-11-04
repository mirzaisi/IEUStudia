"""Connecting Claude or ChatGPT: the Codex CLI from the npm registry (checked, unpacked safely), Git for Windows
Bash, the plan verdicts, the test message and the status the set-up shows. No network, no real installs."""

import base64
import hashlib
import io
import sys
import tarfile
import time

import pytest

from studia import ai, ai_setup


@pytest.fixture(autouse=True)
def fresh_state():
    """Installs and tests are remembered per process; every test starts without any."""
    ai_setup._jobs.clear()
    ai_setup._tests.clear()
    yield
    ai_setup._jobs.clear()
    ai_setup._tests.clear()


def _cached(value):
    """A stand-in for an lru_cache'd function (ai_status clears the cache first)."""
    def f():
        return value
    f.cache_clear = lambda: None
    return f


def _tgz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


V = "package/vendor/x86_64-pc-windows-msvc/"
PACKAGE = _tgz({V + "bin/codex.exe": b"MZ codex", V + "codex-resources/codex-command-runner.exe": b"MZ runner",
                V + "codex-path/rg.exe": b"MZ rg", "package/package.json": b"{}", "package/vendor/../../evil.txt": b"x",
                "package/vendor/aarch64-pc-windows-msvc/bin/codex.exe": b"MZ arm"})


def _sri(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


class _Resp:
    def __init__(self, data=None, body=b""):
        self._data, self._body = data, body
        self.headers = {"Content-Length": str(len(body))}

    def raise_for_status(self):
        pass

    def json(self):
        return self._data

    def iter_content(self, n):
        for i in range(0, len(self._body), n):
            yield self._body[i:i + n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Registry:
    """The npm registry for @openai/codex: latest -> 0.200.0, its win32-x64 build, the tarball."""

    def __init__(self, body=PACKAGE, integrity=None):
        self.body, self.integrity, self.urls = body, integrity if integrity is not None else _sri(PACKAGE), []

    def get(self, url, timeout=0, stream=False):
        self.urls.append(url)
        if url.endswith("/@openai/codex/latest"):
            return _Resp({"version": "0.200.0"})
        if url.endswith("/@openai/codex/0.200.0-win32-x64"):
            return _Resp({"dist": {"tarball": "https://registry.npmjs.org/codex.tgz", "integrity": self.integrity}})
        if url.endswith("codex.tgz"):
            return _Resp(body=self.body)
        raise AssertionError(url)


def _wait(which, timeout=5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        j = ai_setup.job(which)
        if j.get("state") in ("done", "error"):
            return j
        time.sleep(0.02)
    raise AssertionError("install did not finish")


@pytest.mark.skipif(sys.platform != "win32", reason="IEUStudia installs the Windows build of Codex")
def test_codex_is_installed_from_the_npm_registry_without_node(monkeypatch):
    reg = _Registry()
    monkeypatch.setattr(ai_setup, "_session", lambda: reg)
    monkeypatch.setattr(ai_setup.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: (0, "codex-cli 0.200.0"))
    monkeypatch.setattr(ai.shutil, "which", lambda name: None)                 # no npm, no codex on PATH
    monkeypatch.setenv("APPDATA", str(ai_setup.tools_dir() / "nowhere"))
    events = []
    ai_setup.start_install("codex", events.append)
    j = _wait("codex")
    assert j["state"] == "done", j
    home = ai_setup.codex_home()
    assert (home / "bin" / "codex.exe").read_bytes() == b"MZ codex"
    assert (home / "codex-resources" / "codex-command-runner.exe").is_file() and (home / "codex-path" / "rg.exe").is_file()
    assert not list(home.rglob("evil.txt")) and not (home.parent / "evil.txt").exists()
    assert ai_setup.codex_exe() and ai.codex_cli_info()["path"] == ai_setup.codex_exe()
    assert any("Downloading Codex 0.200.0" in (e.get("line") or "") for e in events) and events[-1]["state"] == "done"
    assert reg.urls[:2] == ["https://registry.npmjs.org/@openai/codex/latest",
                            "https://registry.npmjs.org/@openai/codex/0.200.0-win32-x64"]


@pytest.mark.skipif(sys.platform != "win32", reason="IEUStudia installs the Windows build of Codex")
def test_a_damaged_codex_download_is_refused(monkeypatch):
    monkeypatch.setattr(ai_setup, "_session", lambda: _Registry(integrity=_sri(b"something else")))
    ai_setup.start_install("codex", lambda e: None)
    j = _wait("codex")
    assert j["state"] == "error" and "damaged" in j["error"] and not ai_setup.codex_exe()
    with pytest.raises(RuntimeError, match="checksum"):
        ai_setup._check_integrity(b"x", "", "")


def test_unpacking_replaces_an_older_copy(tmp_path):
    dest = tmp_path / "codex"
    (dest / "bin").mkdir(parents=True)
    (dest / "bin" / "codex.exe").write_bytes(b"old")
    (dest / "stale.txt").write_text("x")
    ai_setup.unpack_codex(PACKAGE, "x86_64-pc-windows-msvc", dest)
    assert (dest / "bin" / "codex.exe").read_bytes() == b"MZ codex" and not (dest / "stale.txt").exists()
    with pytest.raises(RuntimeError, match="no codex.exe"):
        ai_setup.unpack_codex(_tgz({"package/README.md": b"hi"}), "x86_64-pc-windows-msvc", tmp_path / "other")


def test_git_bash_is_found_and_passed_to_claude_code(monkeypatch, tmp_path):
    for k in ("CLAUDE_CODE_GIT_BASH_PATH", "ProgramW6432", "ProgramFiles(x86)"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    monkeypatch.setattr(ai_setup.shutil, "which", lambda name: None)
    assert ai_setup.git_bash() == "" and "CLAUDE_CODE_GIT_BASH_PATH" not in ai_setup.claude_env()
    bash = tmp_path / "pf" / "Git" / "bin" / "bash.exe"
    bash.parent.mkdir(parents=True)
    bash.write_bytes(b"MZ")
    assert ai_setup.git_bash() == str(bash) and ai_setup.claude_env()["CLAUDE_CODE_GIT_BASH_PATH"] == str(bash)
    monkeypatch.setenv("CLAUDE_CODE_GIT_BASH_PATH", str(bash))       # the user's own setting is kept
    assert ai_setup.claude_env()["CLAUDE_CODE_GIT_BASH_PATH"] == str(bash)


def test_plan_verdicts():
    p = ai_setup.plan_status
    assert p("claude", "max", True) == {"ok": True, "label": "Claude Max", "note": ""}
    assert p("claude", "free", True)["ok"] is False and "Pro or Max" in p("claude", "free", True)["note"]
    assert p("claude", "api", True)["ok"] and "billed per use" in p("claude", "api", True)["note"]
    assert p("claude", "", True)["ok"] is None and p("claude", "pro", False)["ok"] is None
    assert p("codex", "plus", True)["label"] == "ChatGPT Plus"
    assert p("codex", "free", True)["ok"] is None and "test message" in p("codex", "go", True)["note"]   # OpenAI decides
    assert p("codex", "chatgpt", True) == {"ok": None, "label": "ChatGPT",
                                           "note": "Codex does not show your plan; the test message below shows whether it works."}


def test_the_test_message(monkeypatch):
    monkeypatch.setattr(ai, "_claude_one_shot", lambda *a, **k: {"text": "OK"})
    r = ai_setup.test("claude")
    assert r["ok"] and r["error"] == "" and ai_setup.last_test("claude")["ok"]

    def boom(*a, **k):
        raise RuntimeError("Your organization does not have access to Claude Code.")

    monkeypatch.setattr(ai, "_claude_one_shot", boom)
    r = ai_setup.test("claude")
    assert not r["ok"] and r["error"] == "Your Claude plan doesn't include Claude Code. You need Claude Pro or Max."
    ai_setup.forget_test("claude")
    assert ai_setup.last_test("claude") == {}
    monkeypatch.setattr(ai, "_codex_one_shot", lambda *a, **k: {"text": ""})
    assert ai_setup.test("codex") == {**ai_setup.last_test("codex"), "ok": False, "error": "No answer came back."}
    monkeypatch.setattr(ai, "_chatgpt_one_shot", lambda *a, **k: {"text": "OK"})
    assert ai_setup.test("chatgpt")["ok"] and not ai_setup.last_test("codex")["ok"]
    from studia import chatgpt

    def refused(*a, **k):
        raise chatgpt.NotEligible("Your plan can't be used by apps.")      # the page knows it by the code, in any language

    monkeypatch.setattr(ai, "_chatgpt_one_shot", refused)
    r = ai_setup.test("chatgpt")
    assert not r["ok"] and r["code"] == "plan_not_eligible" and r["error"] == "Your plan can't be used by apps."


def test_status_for_one_ai_only(monkeypatch):
    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "2.1.0"}))
    monkeypatch.setattr(ai, "claude_auth", lambda: {"logged_in": True, "plan": "pro", "email": "a@b.c"})
    s = ai.ai_status("claude")
    assert "chatgpt" not in s and "codex" not in s and "local" not in s
    c = s["claude"]
    assert c["plan_status"]["label"] == "Claude Pro" and c["job"] == {} and c["test"] == {}


def test_the_full_status_asks_every_ai_at_once(monkeypatch):
    from studia import chatgpt

    def slow(value):
        def f():
            time.sleep(0.4)                     # a program started and asked (claude auth status, codex login status)
            return value
        return f

    monkeypatch.setattr(ai, "claude_cli_info", _cached({"available": True, "path": "claude.exe", "version": "2.1.0"}))
    monkeypatch.setattr(ai, "codex_cli_info", _cached({"available": True, "path": "codex.exe", "version": "0.1"}))
    monkeypatch.setattr(ai, "claude_auth", slow({"logged_in": True, "plan": "max", "email": "a@b.c"}))
    monkeypatch.setattr(ai, "codex_auth", slow({"logged_in": False, "plan": "", "email": ""}))
    monkeypatch.setattr(chatgpt, "status", slow({"signed_in": False, "email": "", "plan_allowed": False,
                                                 "registered": False, "sign_in": {}}))
    t0 = time.monotonic()
    s = ai.ai_status()
    assert time.monotonic() - t0 < 1.0                                  # not 1.2 s one after another
    assert s["claude"]["logged_in"] and not s["codex"]["logged_in"] and not s["chatgpt"]["signed_in"]
    assert "local" in s and s["chosen"] is not None


def test_the_codex_version_is_asked_again_only_when_its_file_changes(tmp_path, monkeypatch):
    exe = tmp_path / "codex.exe"
    exe.write_bytes(b"MZ 1")
    runs = []
    monkeypatch.setattr(ai, "_codex_candidates", lambda: [str(exe)])
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: runs.append(cmd) or (0, "codex-cli 0.1.0"))
    for _ in range(3):
        ai.codex_cli_info.cache_clear()                                 # (as ai_status does each time)
        assert ai.codex_cli_info()["version"] == "0.1.0"
    assert len(runs) == 1
    exe.write_bytes(b"MZ 2 updated")                                    # it updated itself
    monkeypatch.setattr(ai, "_run", lambda cmd, timeout=20, env=None: runs.append(cmd) or (0, "codex-cli 0.2.0"))
    ai.codex_cli_info.cache_clear()
    assert ai.codex_cli_info()["version"] == "0.2.0" and len(runs) == 2
    ai.codex_cli_info.cache_clear()
