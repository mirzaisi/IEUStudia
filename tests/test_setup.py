"""First-start setup and the machine-wide parts: the hardware verdict, the downloads, the app settings, starting
with Windows, the tray helper's notifications and the AI providers (Claude / ChatGPT through Codex / offline)."""

import datetime as dt
import hashlib
import io
import json
import sys
import threading
import zipfile

import pytest

from studia import ai, appconfig, components, hardware, launch


def _facts(**over):
    f = {"windows": {"name": "Windows 11", "build": 26100, "version": "24H2", "x64": True},
         "cpu": {"name": "CPU", "cores": 16, "avx": True, "avx2": True, "arch": "AMD64"},
         "ram_gb": 16.0, "ram_free_gb": 8.0, "webview2": "130.0",
         "gpu": {"present": True, "name": "RTX 4060", "vram_mb": 8188, "driver": "566.14"},
         "disk": {"data_dir": "C:\\Users\\x\\AppData\\Local\\IEUStudia", "data_free_gb": 120.0}}
    for k, v in over.items():
        f[k] = {**f[k], **v} if isinstance(v, dict) else v
    return f


def test_storage_counts_the_library_copies_the_codex_cli_an_update_and_the_logs(isolated_home):
    """The Storage card left out backups\\ (up to three whole copies of the library), tools\\ (the Codex CLI, about
    440 MB), updates\\ and logs\\; an added profile's downloaded audio counts as temporary, not as its library."""
    files = {"library.db": 100, "transcripts/a.json": 10, "backups/library-3.2.5.db": 1000,
             "tools/codex/bin/codex.exe": 5000, "updates/IEUStudia-9.9.9-setup.exe": 700, "logs/studia.log": 30,
             "cache/audio/x.m4a": 3, "profiles/p-12345678/library.db": 50,
             "profiles/p-12345678/cache/audio/y.m4a": 7,
             "chat-files/c1/slides.pdf": 400, "Unsaved notes/MATH 250 - Study notes.md": 20}    # the first profile's
    for name, size in files.items():
        (isolated_home / name).parent.mkdir(parents=True, exist_ok=True)
        (isolated_home / name).write_bytes(b"x" * size)
    use = components.disk_usage()
    assert use["library"] == 100 + 10 + 1000 + 50 + 400 + 20
    assert use["ai_tools"] == 5000
    assert use["cache"] == 3 + 700 + 30 + 7
    assert sum(use.values()) == sum(files.values())                 # nothing in the app's folder is left out


def test_removing_a_speech_model_found_only_in_the_shared_cache_says_why_it_stays(tmp_path, monkeypatch):
    from studia.engine import models

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    snap = tmp_path / "hf" / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    for name in ("model.bin", "config.json", "tokenizer.json"):
        (snap / name).write_bytes(b"x")
    assert models.model_is_downloaded("large-v3-turbo")              # Settings → Storage lists it
    with pytest.raises(RuntimeError, match="shared Hugging Face cache") as err:
        models.delete_model("large-v3-turbo")
    assert str(snap.parents[1]) in str(err.value) and (snap / "model.bin").is_file()     # left there, and said so
    own = models._models_root() / "large-v3-turbo"                     # IEUStudia's own copy is removed as before
    own.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", ".complete"):
        (own / name).write_bytes(b"x")
    models.delete_model("large-v3-turbo")
    assert not own.exists()


def test_hardware_verdicts():
    good = hardware.assess(_facts())
    assert good["ok"] and good["gpu"] and good["recommended_model"] == "large-v3-turbo" and good["recommended_llm"] == "qwen3-4b"
    old = hardware.assess(_facts(windows={"build": 10240}))
    assert not old["ok"] and "Windows 10" in old["blockers"][0]
    no_avx = hardware.assess(_facts(cpu={"avx": False, "avx2": False}))
    assert not no_avx["ok"] and any("AVX" in b for b in no_avx["blockers"])
    small = hardware.assess(_facts(ram_gb=3.5))
    assert not small["ok"] and any("4 GB" in b for b in small["blockers"])
    full = hardware.assess(_facts(disk={"data_free_gb": 2.0}), need_gb=3.0)
    assert not full["ok"] and any("disk space" in b for b in full["blockers"])
    old_driver = hardware.assess(_facts(gpu={"driver": "472.12"}))
    assert old_driver["ok"] and not old_driver["gpu"] and any("driver" in w for w in old_driver["warnings"])
    cpu_only = hardware.assess(_facts(gpu={"present": False, "name": "", "vram_mb": 0, "driver": ""}, ram_gb=8.0))
    assert cpu_only["recommended_model"] == "small" and cpu_only["recommended_llm"] == "qwen3-1.7b"
    assert not cpu_only["models"]["large-v3"]["ok"] and cpu_only["models"]["small"]["ok"]
    assert hardware.assess(_facts(gpu={"present": False, "name": "", "vram_mb": 0, "driver": ""}, ram_gb=6.0))["recommended_llm"] == ""
    strong_cpu = hardware.assess(_facts(gpu={"present": False, "name": "", "vram_mb": 0, "driver": ""}, ram_gb=32.0))
    assert strong_cpu["recommended_model"] == "small"           # medium on a processor takes hours per lecture
    assert any("20 minutes to an hour" in w for w in strong_cpu["warnings"])


def test_an_8_gb_laptop_counts_as_8_gb():
    """Windows can use a little less than is built in (firmware, integrated graphics): 7.7 of 8 GB."""
    no_gpu = {"present": False, "name": "", "vram_mb": 0, "driver": ""}
    for facts in (_facts(gpu=no_gpu, ram_gb=7.7, ram_installed_gb=8.0), _facts(gpu=no_gpu, ram_gb=7.4)):
        v = hardware.assess(facts)
        assert v["recommended_llm"] == "qwen3-1.7b" and v["models"]["medium"]["ok"]
        assert not any("less than 8 GB" in w for w in v["warnings"])
        assert next(r for r in v["summary"] if r["label"] == "Memory")["ok"] == "yes"
    six = hardware.assess(_facts(gpu=no_gpu, ram_gb=5.8, ram_installed_gb=6.0))
    assert six["recommended_llm"] == "" and six["models"]["medium"] == {"ok": False, "why": "needs 8 GB of memory"}
    assert next(r for r in six["summary"] if r["label"] == "Memory")["value"] == "6 GB"


def test_hardware_check_runs_on_this_pc(tmp_path):
    f = hardware.check(tmp_path)
    assert (f["windows"]["build"] > 0 or f["windows"].get("linux")) and f["cpu"]["cores"] >= 1 and f["ram_gb"] > 0 and f["disk"]["data_free_gb"] > 0
    assert "summary" in hardware.assess(f)


def test_download_plan(monkeypatch):
    from studia.engine import models

    monkeypatch.setattr(models, "model_is_downloaded", lambda m: m == "small")
    monkeypatch.setattr(components, "cuda_ready", lambda: False)
    monkeypatch.setattr(components, "llm_ready", lambda k: False)
    items = components.plan("large-v3-turbo", "qwen3-1.7b", gpu=True)
    assert [i["id"] for i in items] == ["speech:large-v3-turbo", "cuda", "llm:qwen3-1.7b"]
    assert components.plan("small", "", gpu=False) == []                     # all there already


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows DLL wheels")
def test_cuda_download_extracts_only_the_dlls_and_checks_the_hash(monkeypatch, tmp_path):
    def wheel(names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, b"dll-" + n.encode())
        return buf.getvalue()

    blobs = {"cublas.whl": wheel(["nvidia/cublas/bin/cublas64_12.dll", "nvidia/cublas/bin/cublasLt64_12.dll",
                                  "nvidia/cublas/include/cublas.h"]),
             "cudart.whl": wheel(["nvidia/cuda_runtime/bin/cudart64_12.dll"])}

    class Session:
        def get(self, url, timeout=0):
            pkg = url.split("/pypi/")[1].split("/")[0]
            name = "cublas.whl" if "cublas" in pkg else "cudart.whl"
            files = [{"filename": f"{pkg}-12.8.1-py3-none-win_amd64.whl", "url": name, "size": len(blobs[name]),
                      "digests": {"sha256": hashlib.sha256(blobs[name]).hexdigest()}}]
            return _Resp({"releases": {"12.8.1": files, "12.9.0": files, "13.0.0": []}})

    def fake_download(url, dest, session=None, progress=None, cancel=None, parts=1):
        dest.write_bytes(blobs[url])
        progress(len(blobs[url]), len(blobs[url]))

    monkeypatch.setattr(components, "_session", Session)
    monkeypatch.setattr(components.httpdl, "download", fake_download)
    seen = []
    out = components.download_cuda(lambda d, t: seen.append((d, t)))
    assert sorted(p.name for p in out.glob("*.dll")) == ["cublas64_12.dll", "cublasLt64_12.dll", "cudart64_12.dll"]
    assert not (out / "cublas.h").exists() and (out / ".complete").exists() and components.cuda_ready()
    assert seen[-1][0] == seen[-1][1]
    # a damaged download is refused
    blobs["cublas.whl"] = blobs["cublas.whl"][:-3] + b"bad"
    monkeypatch.setattr(components, "_session", lambda: type("S", (), {"get": lambda self, url, timeout=0: _Resp(
        {"releases": {"12.8.1": [{"filename": "x-win_amd64.whl", "url": "cublas.whl", "size": len(blobs["cublas.whl"]),
                                  "digests": {"sha256": "0" * 64}}]}})})())
    with pytest.raises(RuntimeError, match="damaged"):
        components.download_cuda(lambda d, t: None)


@pytest.mark.skipif(sys.platform == "win32", reason="the Linux .so wheels")
def test_cuda_download_on_linux_takes_cublas_and_cudnn_from_the_manylinux_wheels(monkeypatch, tmp_path):
    def wheel(names):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for n in names:
                z.writestr(n, b"so-" + n.encode())
        return buf.getvalue()

    blobs = {"cublas.whl": wheel(["nvidia/cublas/lib/libcublas.so.12", "nvidia/cublas/lib/libcublasLt.so.12",
                                  "nvidia/cublas/include/cublas.h"]),
             "cudnn.whl": wheel(["nvidia/cudnn/lib/libcudnn.so.9", "nvidia/cudnn/lib/libcudnn_ops.so.9"])}

    class Session:
        def get(self, url, timeout=0):
            pkg = url.split("/pypi/")[1].split("/")[0]
            name, version = ("cublas.whl", "12.8.1") if "cublas" in pkg else ("cudnn.whl", "9.10.2")
            files = [{"filename": f"{pkg}-{version}-py3-none-win_amd64.whl", "url": "wrong", "size": 1,
                      "digests": {"sha256": "0" * 64}},
                     {"filename": f"{pkg}-{version}-py3-none-manylinux_2_27_x86_64.whl", "url": name,
                      "size": len(blobs[name]), "digests": {"sha256": hashlib.sha256(blobs[name]).hexdigest()}}]
            return _Resp({"releases": {version: files}})

    def fake_download(url, dest, session=None, progress=None, cancel=None, parts=1):
        dest.write_bytes(blobs[url])
        progress(len(blobs[url]), len(blobs[url]))

    monkeypatch.setattr(components, "_session", Session)
    monkeypatch.setattr(components.httpdl, "download", fake_download)
    out = components.download_cuda(lambda d, t: None)
    assert sorted(p.name for p in out.glob("lib*")) == ["libcublas.so.12", "libcublasLt.so.12", "libcudnn.so.9",
                                                         "libcudnn_ops.so.9"]
    assert not (out / "cublas.h").exists() and components.cuda_sources() == [str(out)]


def test_app_config_round_trip():
    c = appconfig.load()
    assert not c.onboarded and c.ai_provider == "" and not c.tray
    c = appconfig.update({"ai_provider": "codex", "tray": True, "sync_minutes": 2, "nonsense": 1})
    assert c.ai_provider == "codex" and c.tray and c.sync_minutes == 10                 # clamped
    assert appconfig.update({"ai_provider": "chatgpt"}).ai_provider == "chatgpt"
    assert appconfig.update({"ai_provider": "gemini"}).ai_provider == ""             # unknown -> not chosen


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows Run key")
def test_start_with_windows_registry(monkeypatch):
    import winreg

    key = r"Software\IEUStudiaTests\Run"
    monkeypatch.setattr(launch, "RUN_KEY", key)
    try:
        assert not launch.startup_enabled()
        launch.set_startup(True)
        assert launch.startup_enabled()
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            cmd = winreg.QueryValueEx(k, launch.RUN_VALUE)[0]
        assert "--agent" in cmd and "--at-login" in cmd and "studia.app" in cmd
        launch.set_startup(False)
        launch.set_startup(False)                                                     # twice is fine
        assert not launch.startup_enabled()
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\IEUStudiaTests")
        except OSError:
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="XDG autostart")
def test_start_at_login_on_linux(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    entry = tmp_path / "config" / "autostart" / "ieustudia-helper.desktop"
    assert not launch.startup_enabled()
    launch.set_startup(True)
    assert launch.startup_enabled()
    exec_line = next(line for line in entry.read_text().splitlines() if line.startswith("Exec="))
    assert "--agent" in exec_line and "--at-login" in exec_line and "studia.app" in exec_line
    launch.set_startup(False)
    launch.set_startup(False)                                                         # twice is fine
    assert not launch.startup_enabled() and not entry.exists()


def test_launch_commands_from_source():
    assert launch.app_command("--agent")[1:] == ["-m", "studia.app", "--agent"]
    sp = launch.splash_command("123", "dark")
    assert sp[1:3] == ["-I", "-S"] and sp[3].endswith("splash.py") and sp[4:] == ["123", "dark"]


def test_helper_notifications(tmp_path):
    from studia.agent import summarize
    from studia.config import CoursePrefs, Settings
    from studia.coursesite import Announcement
    from studia.domain import Course, Recording
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="b", title="t", code="EEE 215", name="Circuits", section="0 - 1")])

    def rec(did, name, hour):
        return Recording(delivery_id=did, course_id="b", folder_id="f", folder_path="", name=name,
                         start=dt.datetime(2026, 10, 6, hour), duration_s=3000, viewer_url=f"u/{did}")

    lib.merge_recordings("b", [rec("s1", "EEE 215 Section 1", 9), rec("s3", "EEE 215 Section 3", 13)], first_sync=True)
    lib.merge_announcements("b", [Announcement("a1", "Quiz 2 next week", "Dr", dt.datetime(2026, 10, 7, 9), "Quiz 2 covers LO3."),
                                  Announcement("a2", "Room change", "Dr", dt.datetime(2026, 10, 7, 10), "We meet in M01.")],
                            first_sync=True)
    s = Settings(course_prefs={"b": CoursePrefs(my_section="3")})
    cfg = appconfig.AppConfig(tray=True)
    seen = {}
    title, text, payload = summarize(lib, s, cfg, ["s1", "s3"], ["a1", "a2"], [], seen)
    assert title == "IEUStudia: heads-up" and text.splitlines()[0] == "EEE 215: Quiz · Quiz 2 next week"
    assert "EEE 215: 1 new lecture recording" in text and "Room change" in text
    assert payload == "course:b:announcements"                                         # opens what it's about
    assert summarize(lib, s, cfg, ["s1", "s3"], ["a1", "a2"], [], seen) is None        # already told
    assert summarize(lib, s, cfg, ["s1"], [], [], {}) is None                          # another section's lecture
    assert summarize(lib, s, cfg, ["s3"], [], [], {})[2] == "course:b:lectures"        # a recording: the lectures


def _quiz(**kw):
    return {"uid": "b-quiz-2", "course": "MATH 240", "name": "Quiz 2", "course_id": "b", "kind": "quiz",
            "classes": ["2026-10-13", "2026-10-15"], "ends": ["10:20", "12:50"], "los": [3, 4, 6],
            "start": "2026-10-13", "end": "2026-10-15", "week": 5, **kw}


def _evening_before():
    """agent.reminders with Calendar → Reminders set to "The evening before" for quizzes and exams (the default is
    2 days before: test_calendar.py)."""
    from studia import agent

    return lambda events, now, seen: agent.reminders(events, now, seen, {"exams": 1})


def test_a_quiz_is_reminded_once_the_evening_before():
    reminders = _evening_before()
    seen = {}
    assert reminders([_quiz()], dt.datetime(2026, 10, 12, 16, 30), seen) is None          # not yet
    title, text, payload = reminders([_quiz()], dt.datetime(2026, 10, 12, 19, 0), seen)
    assert title == "IEUStudia: coming up" and payload == "ready:b:Quiz 2"                # a click: Get ready
    assert text == "MATH 240: Quiz 2 may be tomorrow (Tue 13 Oct) or Thu 15 Oct, as announced. Covers LO3–LO4, LO6."
    assert reminders([_quiz()], dt.datetime(2026, 10, 12, 21, 0), seen) is None            # said once
    assert reminders([_quiz()], dt.datetime(2026, 10, 13, 9, 0), {})[1].startswith("MATH 240: Quiz 2 may be today or Thu")
    assert reminders([_quiz()], dt.datetime(2026, 10, 13, 11, 0), {}) is None             # Tuesday's class is over
    late = reminders([_quiz()], dt.datetime(2026, 10, 14, 18, 0), {})                      # off on Monday evening
    assert late[1].startswith("MATH 240: Quiz 2 may be tomorrow (Thu 15 Oct), as announced")
    assert reminders([_quiz()], dt.datetime(2026, 10, 15, 13, 0), {}) is None             # all its classes are over
    moved = _quiz(start="2026-10-20", classes=["2026-10-20"], ends=["10:20"], end="2026-10-20", week=6)
    title, text, _ = reminders([moved], dt.datetime(2026, 10, 19, 18, 0), seen)           # moved a week: said again
    assert title == "IEUStudia: Quiz 2 tomorrow" and text.startswith("MATH 240: Quiz 2 tomorrow (Tue 20 Oct), as announced")


def test_an_exam_week_is_reminded_once_without_a_day():
    reminders = _evening_before()
    mid = _quiz(uid="b-syllabus-week8", name="Midterm Exam", kind="exam", classes=[], ends=[], los=[],
                start="2026-11-09", end="2026-11-15", week=8)
    seen = {}
    title, text, payload = reminders([mid], dt.datetime(2026, 11, 8, 20, 0), seen)
    assert text == "MATH 240: Midterm Exam is in week 8, which starts tomorrow (Mon 9 Nov). The exam schedule on IEULearn has the day."
    assert payload == "week:b:8"                                    # the syllabus's word: Get ready doesn't know it
    announced = {**mid, "uid": "b-midterm-1", "name": "Midterm 1", "kind": "midterm"}
    assert reminders([announced], dt.datetime(2026, 11, 9, 9, 0), seen) is None            # the same week, said once
    title, text, payload = reminders([announced, _quiz(course="EEE 215", uid="c-quiz-1", course_id="c",
                                                       start="2026-11-09", classes=["2026-11-09"], ends=["15:00"],
                                                       los=[])], dt.datetime(2026, 11, 8, 20, 0), {})
    assert title == "IEUStudia: coming up" and payload == "home"
    assert text.splitlines() == ["MATH 240: Midterm 1 is in week 8, which starts tomorrow (Mon 9 Nov). The exam schedule "
                                 "on IEULearn has the day.", "EEE 215: Quiz 2 tomorrow (Mon 9 Nov), as announced."]


def test_stop_background_checks_in_the_tray_menu_asks_first_then_stays_off(monkeypatch):
    import types

    from studia import agent, osutil

    calls, asked, answer = [], [], [7]                  # IDNO first: a misclick changes nothing
    monkeypatch.setattr(launch, "set_startup", lambda on: calls.append(on))
    monkeypatch.setattr(osutil.impl(), "message_box", lambda text, title="", flags=0: asked.append((text, flags))
                        or answer[0])
    appconfig.update({"tray": True, "start_with_windows": True})
    helper = types.SimpleNamespace(stop=threading.Event(), wake=threading.Event(), asking=False,
                                   tray=types.SimpleNamespace(stop=lambda: calls.append("tray stopped")))
    agent.Helper._command(helper, agent.CMD_QUIT)
    cfg = appconfig.load()
    assert cfg.tray and cfg.start_with_windows and calls == [] and not helper.stop.is_set()
    text, flags = asked[0]
    assert "Settings" in text and flags & 0xF == 0x4 and flags & 0x100        # Yes/No, with No as the default
    answer[0] = 6                                       # IDYES
    agent.Helper._command(helper, agent.CMD_QUIT)
    cfg = appconfig.load()
    assert not cfg.tray and not cfg.start_with_windows and calls == [False, "tray stopped"] and helper.stop.is_set()
    assert not helper.asking


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdin = io.BytesIO()
        self.stdout = iter([json.dumps(x).encode() + b"\n" for x in lines])
        self.stderr = io.BytesIO(b"")
        self.returncode = rc

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


def test_chatgpt_through_the_codex_cli(monkeypatch):
    appconfig.update({"ai_provider": "codex", "codex_model": "gpt-x"})
    monkeypatch.setattr(ai, "codex_cli_info", lambda: {"available": True, "path": "codex.exe", "version": "0.1"})
    monkeypatch.setattr(ai, "codex_models", lambda: ["gpt-x"])
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        return _FakeProc([{"type": "thread.started", "thread_id": "t"},
                          {"type": "item.completed", "item": {"type": "agent_message", "text": "Hello **notes**"}},
                          {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 1000, "output_tokens": 30}}])

    monkeypatch.setattr(ai.subprocess, "Popen", popen)
    assert ai.provider() == "codex" and ai.ai_info()["label"] == "ChatGPT"
    r = ai.one_shot("You write notes.", [{"type": "text", "text": "transcript"}], model="opus")
    assert r["text"] == "Hello **notes**" and r["usage"]["input_tokens"] == 1200
    cmd = calls[0]
    for flag in ("exec", "--json", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check"):
        assert flag in cmd
    assert cmd[cmd.index("-s") + 1] == "read-only" and 'model_reasoning_effort="high"' in cmd and cmd[cmd.index("-m") + 1] == "gpt-x"
    s = ai.new_session("ctx", model="sonnet")
    assert s.ask([{"type": "text", "text": "q1"}])["text"] == "Hello **notes**"
    s.ask([{"type": "text", "text": "q2"}])
    assert len(calls) == 3                                                            # one exec per turn
    monkeypatch.setattr(ai, "codex_models", lambda: ["gpt-y"])                       # a model the CLI doesn't offer
    ai.one_shot("x", [{"type": "text", "text": "y"}])
    assert "-m" not in calls[-1]

    monkeypatch.setattr(ai.subprocess, "Popen", lambda cmd, **kw: _FakeProc([{"type": "error", "message": "You've hit your usage limit"}]))
    with pytest.raises(RuntimeError, match="usage limit"):
        ai.one_shot("x", [{"type": "text", "text": "y"}])


def test_no_ai_and_offline_choices(monkeypatch):
    appconfig.update({"ai_provider": "none"})
    assert not ai.ai_info()["available"]
    with pytest.raises(RuntimeError, match="turned off"):
        ai.one_shot("x", [{"type": "text", "text": "y"}])
    appconfig.update({"ai_provider": "local"})
    info = ai.ai_info()
    assert info["label"] == "Offline model" and not info["available"] and not info["can_plans"]


def test_messages_name_the_chosen_ai():
    for provider, name, label in (("chatgpt", "ChatGPT", "ChatGPT, medium reasoning"),
                                  ("codex", "ChatGPT", "ChatGPT, medium reasoning"),
                                  ("claude", "Claude", "Claude Sonnet"), ("local", "The offline model", "Offline model")):
        appconfig.update({"ai_provider": provider})
        assert (ai.ai_name(), ai.model_label("sonnet")) == (name, label)


def test_offline_model_prompt_and_chunks(monkeypatch):
    from studia import local_llm

    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "Q"}]
    assert local_llm._prompt(msgs, "qwen3").endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")
    assert local_llm._prompt(msgs, "qwen3-instruct").endswith("<|im_start|>assistant\n")
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    parts = local_llm._chunks("\n".join(["a b c d e"] * 10), "k", 12)
    assert len(parts) == 5 and all(len(p.split()) <= 12 for p in parts)


def test_the_offline_model_keeps_the_task_when_it_condenses(monkeypatch):
    from studia import local_llm
    from studia.digest import digest_prompt

    appconfig.update({"ai_provider": "local"})
    sent = []
    monkeypatch.setattr(local_llm, "budget", lambda key: 500)
    monkeypatch.setattr(local_llm, "count_tokens", lambda text, key: len(text.split()))
    monkeypatch.setattr(local_llm, "generate", lambda msgs, key, *a, **k: sent.append(msgs)
                        or {"text": "notes", "usage": {"input_tokens": 1, "output_tokens": 1}})
    transcript = "\n".join(f"[00:{i:02d}] a sentence about the topic of the lecture, number {i}" for i in range(60))
    ai.run_claude("Create 20–30 flashcards.", transcript)
    assert len(sent) > 2 and not any("flashcards" in m["content"] for m in sent[0])   # the parts are condensed...
    assert "Create 20–30 flashcards." in sent[-1][0]["content"]                     # ...and the task stays whole
    ai.one_shot(ai.SYSTEM_PROMPT, [{"type": "text", "text": digest_prompt({"week": {"label": "W"}, "courses": [],
                                                                             "todo": []})}])
    assert "Write a short weekly digest" in sent[-1][0]["content"] and sent[-1][1]["content"].startswith("WEEK: W")


def test_the_system_check_asks_nvidia_smi_once(monkeypatch):
    import subprocess
    import types

    from studia.api import Api
    from studia.services import Services

    runs = []

    def run(cmd, **kw):
        runs.append(cmd)
        out = "NVIDIA GeForce RTX 4070 Laptop GPU, 8188, 581.57" if "driver_version" in " ".join(cmd) else "x, 1"
        return types.SimpleNamespace(stdout=out, returncode=0)

    api = Api(Services())
    pushed = []
    monkeypatch.setattr(api, "_push", pushed.append)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ai, "claude_info", lambda: {})
    monkeypatch.setattr(components, "cuda_ready", lambda: True)
    api._system_info()
    assert len([c for c in runs if "nvidia-smi" in str(c[0])]) == 1
    (info,) = pushed
    assert {k: info["gpu"][k] for k in ("available", "name", "vram_mb", "used", "fits")} == \
        {"available": True, "name": "NVIDIA GeForce RTX 4070 Laptop GPU", "vram_mb": 8188, "used": True, "fits": True}


def test_setup_api(monkeypatch):
    from studia import agent
    from studia.api import Api
    from studia.services import Services

    started = []
    monkeypatch.setattr(agent, "ensure_running", lambda: started.append(1) or True)
    monkeypatch.setattr(launch, "set_startup", lambda on: started.append(("startup", on)))
    monkeypatch.setattr(launch, "startup_enabled", lambda: False)
    api = Api(Services())
    # get_bootstrap's check 6 s later (and app_config's) would otherwise run during a later test, in its folder
    monkeypatch.setattr(api, "_system_info", lambda: None)
    st = api.setup_state()
    assert st["ok"] and "verdict" in st and st["catalog"]["speech"] and not st["app"]["onboarded"]
    r = api.app_config({"tray": True, "start_with_windows": True, "ai_provider": "none"})
    assert r["ok"] and r["app"]["tray"] and started and ("startup", True) in started
    plan = api.setup_plan("small", "")
    assert plan["ok"] and "items" in plan
    done = api.finish_setup()
    assert done["ok"] and done["app"]["onboarded"] and done["app"]["terms_accepted"]
    assert api.get_bootstrap()["app"]["onboarded"]
    # a finished sign-in or install, and signing out, change whether the AI can be used: the app checks again
    checked, pushed = threading.Event(), []
    monkeypatch.setattr(api, "_system_info", checked.set)
    monkeypatch.setattr(api, "_push", pushed.append)
    api._ai_event({"type": "chatgpt", "state": "error", "error": "x"})
    assert pushed and not checked.wait(0.3)
    api._ai_event({"type": "chatgpt", "state": "done", "error": ""})
    assert checked.wait(5)
    checked.clear()
    from studia import chatgpt

    monkeypatch.setattr(chatgpt, "sign_out", lambda: {"revoked": True})
    assert api.ai_sign_out("chatgpt")["revoked"] and checked.wait(5)


def test_a_setup_download_error_reads_as_one_sentence(monkeypatch):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    pushed, idle = [], threading.Event()
    monkeypatch.setattr(api, "_system_info", lambda: None)
    monkeypatch.setattr(api, "_push", lambda e: pushed.append(e) or (e.get("state") == "idle" and idle.set()))

    def gone(cid, progress, cancel):                 # an antivirus took a part away
        raise FileNotFoundError(2, "The system cannot find the file specified", r"C:\x\model.bin.part0")

    monkeypatch.setattr(components, "download", gone)
    assert api.setup_download(["speech-small"])["ok"] and idle.wait(10)
    (error,) = [e["error"] for e in pushed if e.get("state") == "error"]
    assert error.endswith("? (Downloading again continues where it stopped.)"), error


def test_the_setup_can_go_on_while_it_downloads_and_a_cut_off_download_is_offered_again(monkeypatch):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    pushed, idle, gate = [], threading.Event(), threading.Event()
    monkeypatch.setattr(api, "_system_info", lambda: None)
    monkeypatch.setattr(api, "_push", lambda e: pushed.append(e) or (e.get("state") == "idle" and idle.set()))
    monkeypatch.setattr(components, "download", lambda cid, progress, cancel: gate.wait(10))
    monkeypatch.setattr(components, "ready", lambda cid: False)
    assert api.setup_download(["speech:small", "cuda"])["ok"]
    assert api._s.jobs.setup_busy() is True                     # a transcription waits for it
    boot = api.get_bootstrap()
    assert boot["downloading"] is True
    assert [d["id"] for d in boot["pending_downloads"]] == ["speech:small", "cuda"]    # if IEUStudia closed now
    assert boot["pending_downloads"][0]["label"].startswith("Speech model")
    gate.set()
    assert idle.wait(10) and api._s.jobs.setup_busy() is False
    assert appconfig.load().pending_downloads == []             # done: nothing left to offer


def test_a_transcription_waits_for_the_setups_download(tmp_path, monkeypatch):
    import datetime as dt

    from studia import jobs as jobs_mod
    from studia.config import Settings
    from studia.domain import Course, Recording
    from studia.library import Library

    lib = Library(tmp_path / "lib.db")
    lib.upsert_courses([Course(id="c", title="t", code="EEE 215", name="n")])
    lib.merge_recordings("c", [Recording(delivery_id="r1", course_id="c", folder_id="f", folder_path="", name="r1",
                                         start=dt.datetime(2026, 10, 13, 11), duration_s=2400, viewer_url="u")],
                         first_sync=True)
    events = []
    jm = jobs_mod.JobManager(lib, None, lambda: Settings(), lambda s: None, events.append, lambda did: {}, lambda *a: [])
    jm.setup_busy = lambda: True
    job = jobs_mod.Job(id="r1", title="t")
    threading.Timer(0.6, job.cancel.set).start()               # cancelled while it waits
    from studia.cancel import Cancelled

    try:
        jm._transcribe(job)
        raise AssertionError("it should have waited")
    except Cancelled:
        pass
    assert job.detail == "Waiting for the speech model to finish downloading"


def test_a_transcription_waits_for_the_speech_model_not_for_the_offline_ai(monkeypatch):
    """The setup gets the offline AI model (1.7 to 4 GB) last, and Settings → "Set up AI" gets only that: a lecture
    waits only for what it needs, the speech model and the graphics card's files."""
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    idle = threading.Event()
    monkeypatch.setattr(api, "_system_info", lambda: None)
    monkeypatch.setattr(api, "_push", lambda e: e.get("state") == "idle" and idle.set())
    ids = ["speech:small", "cuda", "llm:qwen3-1.7b"]
    started, gates = {c: threading.Event() for c in ids}, {c: threading.Event() for c in ids}
    monkeypatch.setattr(components, "download", lambda cid, progress, cancel: started[cid].set() or gates[cid].wait(10))
    assert api.setup_download(ids)["ok"]
    for cid in ids:
        assert started[cid].wait(5)
        assert api._s.jobs.setup_busy() is (cid != "llm:qwen3-1.7b"), cid
        gates[cid].set()
    assert idle.wait(10) and api._s.jobs.setup_busy() is False
    idle.clear()
    started["llm:qwen3-1.7b"].clear()
    gates["llm:qwen3-1.7b"].clear()
    assert api.setup_download(["llm:qwen3-1.7b"])["ok"]
    assert started["llm:qwen3-1.7b"].wait(5) and api._setup_running and api._s.jobs.setup_busy() is False
    gates["llm:qwen3-1.7b"].set()
    assert idle.wait(10)


def test_a_speech_model_is_downloaded_once_at_a_time(monkeypatch):
    """A lecture's own download of its speech model and the setup's "Download now" would write the same part files:
    the second one waits (and can be cancelled meanwhile), then finds the model there without going online."""
    from studia.cancel import Cancelled
    from studia.engine import models

    asked, inside, release = [], threading.Event(), threading.Event()

    class Session:
        def __init__(self):
            self.headers = {}

        def get(self, url, params=None, timeout=0):
            asked.append(url)
            return _Resp({"siblings": [{"rfilename": n, "size": 4} for n in ("config.json", "model.bin", "tokenizer.json")]})

    def fake_download(url, dest, session=None, progress=None, cancel=None, parts=1):
        inside.set()
        release.wait(10)
        dest.write_bytes(b"data")
        return dest

    monkeypatch.setattr(models, "plain_session", Session)
    monkeypatch.setattr(models.httpdl, "download", fake_download)
    first = threading.Thread(target=models.download_model, args=("small", lambda done, total: None))
    first.start()
    assert inside.wait(5)
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    with pytest.raises(Cancelled):                                    # Cancel works while it waits
        models.download_model("small", lambda done, total: None, cancel)
    seen = []
    second = threading.Thread(target=lambda: seen.append(models.download_model("small", lambda *a: seen.append(a))))
    second.start()
    second.join(0.3)
    assert second.is_alive() and len(asked) == 1                     # it waits for the first one...
    release.set()
    first.join(5)
    second.join(5)
    assert not second.is_alive() and len(asked) == 1                 # ...and then finds the model there
    assert seen[-1] == models._models_root() / "small" and seen[0][0] == seen[0][1] > 0
    assert models.model_is_downloaded("small")


def test_a_download_another_version_noted_never_stops_the_start(tmp_path, monkeypatch):
    """IEUStudia was closed while it downloaded a speech model a later version offers, then this version was installed
    again: the start skips what it doesn't know."""
    from studia.api import Api
    from studia.services import Services

    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))          # no model this PC has cached
    api = Api(Services())
    monkeypatch.setattr(api, "_system_info", lambda: None)
    appconfig.update({"pending_downloads": ["speech:large-v4", "speech:small", "llm:qwen9"]})
    assert components.ready("speech:large-v4") and components.describe("speech:large-v4") is None
    boot = api.get_bootstrap()
    assert boot["ok"] and [d["id"] for d in boot["pending_downloads"]] == ["speech:small"]
    monkeypatch.setattr(components, "ready", lambda cid: 1 / 0)       # nor whatever else goes wrong there
    boot = api.get_bootstrap()
    assert boot["ok"] and boot["pending_downloads"] == []


def test_a_setup_download_runs_even_while_app_json_is_held_open(monkeypatch):
    """Another program (the tray helper, an antivirus) can hold app.json open for a moment: the download still runs and
    reports its end, and nothing is left waiting for a download that never started."""
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    pushed, idle, got, update = [], threading.Event(), [], appconfig.update
    monkeypatch.setattr(api, "_system_info", lambda: None)
    monkeypatch.setattr(api, "_push", lambda e: pushed.append(e) or (e.get("state") == "idle" and idle.set()))
    monkeypatch.setattr(components, "download", lambda cid, progress, cancel: got.append(cid))

    def held(patch):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(appconfig, "update", held)
    assert api.setup_download(["speech:small"]) == {"ok": True} and idle.wait(10)
    assert got == ["speech:small"] and {"type": "setup", "id": "", "state": "idle", "ok": True} in pushed
    assert not api._setup_running and api._s.jobs.setup_busy() is False
    monkeypatch.setattr(appconfig, "update", lambda patch: 1 / 0)    # anything else: an error, but nothing stuck
    assert not api.setup_download(["speech:small"])["ok"]
    assert not api._setup_running and api._s.jobs.setup_busy() is False and got == ["speech:small"]
    monkeypatch.setattr(appconfig, "update", update)                  # the next try downloads
    idle.clear()
    assert api.setup_download(["speech:small"]) == {"ok": True} and idle.wait(10)
    assert got == ["speech:small", "speech:small"] and appconfig.load().pending_downloads == []


def test_one_off_claude_requests_write_no_prompt_cache_and_a_chat_keeps_it(monkeypatch):
    import os
    import sys

    probe = ("import json, os, sys; sys.stdin.read(); print(json.dumps({'type': 'result', 'subtype': 'success', "
             "'usage': {}, 'result': json.dumps({k: os.environ.get(k, '') for k in "
             "('DISABLE_PROMPT_CACHING', 'CLAUDE_CODE_DISABLE_THINKING')})}))")
    monkeypatch.setattr(ai, "_require_claude", lambda: sys.executable)
    monkeypatch.setattr(ai, "_claude_env", lambda: {"PATH": os.environ.get("PATH", ""),
                                                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")})
    monkeypatch.setattr(ai, "_command", lambda model, effort, sp, partial=False: [sys.executable, "-c", probe])
    seen = json.loads(ai._claude_one_shot("S", [{"type": "text", "text": "x"}])["text"])
    assert seen == {"DISABLE_PROMPT_CACHING": "1", "CLAUDE_CODE_DISABLE_THINKING": ""}
    appconfig.update({"ai_provider": "claude"})
    seen = json.loads(ai.one_shot("S", [{"type": "text", "text": "x"}], "haiku", think=False)["text"])
    assert seen == {"DISABLE_PROMPT_CACHING": "1", "CLAUDE_CODE_DISABLE_THINKING": "1"}
    envs = []

    def popen(cmd, **kw):                      # a chat's process: follow-up questions read its prompt cache
        envs.append(kw["env"])
        raise OSError("not started in this test")

    monkeypatch.setattr(ai.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="^Claude Code could not be started: "):      # said plainly, not as an OSError
        ai.ClaudeSession("S")
    assert "DISABLE_PROMPT_CACHING" not in envs[0]


def test_study_notes_send_the_lecture_first_and_the_task_last(monkeypatch):
    sent = []
    monkeypatch.setattr(ai, "one_shot", lambda system, content, *a, **k: sent.append(content) or {"text": ""})
    monkeypatch.setattr(ai, "image_block", lambda p: {"type": "image", "source": {"data": str(p)}})
    for task in ("Study notes.", "Flashcards."):
        ai.run_claude(task, "[00:00] The lecture.", ["s1.jpg"])
    assert sent[0][:2] == sent[1][:2]                      # every kind of notes begins alike (cached by ChatGPT)
    assert sent[0][0]["text"] == "=== LECTURE TRANSCRIPT ===\n\n[00:00] The lecture." and sent[0][1]["type"] == "image"
    assert sent[0][-1]["text"] == "=== TASK ===\n\nStudy notes." and sent[1][-1]["text"] == "=== TASK ===\n\nFlashcards."


def test_a_lecture_does_not_wait_for_a_speech_model_it_does_not_use(monkeypatch):
    from studia.api import Api
    from studia.services import Services

    api = Api(Services())
    monkeypatch.setattr(api, "_system_info", lambda: None)
    idle, started, gate = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(api, "_push", lambda e: e.get("state") == "idle" and idle.set())
    monkeypatch.setattr(components, "download", lambda cid, progress, cancel: started.set() or gate.wait(10))
    api._s.update_settings({"model": "small"})                 # the student switched to Small in Settings
    assert api.setup_download(["speech:medium"])["ok"]          # the setup still gets another model
    assert started.wait(5)
    busy_other = api._s.jobs.setup_busy()
    gate.set()
    assert idle.wait(10)
    started.clear(); gate.clear(); idle.clear()
    assert api.setup_download(["speech:small"])["ok"]           # its own model: it waits
    assert started.wait(5)
    busy_own = api._s.jobs.setup_busy()
    gate.set()
    assert idle.wait(10)
    assert (busy_other, busy_own) == (False, True)
