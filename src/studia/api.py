"""The bridge the web UI talks to (pywebview js_api). Methods never raise: errors come back as
``{"ok": false, "error": "..."}``; long work runs in threads and reports through events."""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__, net, store, views
from .cancel import Cancelled
from .i18n import t, tn
from .ieulearn import AuthError, NeedLogin
from .net import NetworkError
from .services import NOTE_KINDS, Services
from .osutil import (TaskbarProgress, flash_taskbar, is_foreground, normal_priority, open_in_browser, open_path,
                     reveal, set_clipboard)

log = logging.getLogger("studia")
AUTO_SYNC_S = 30 * 60        # while the app is open: look for new lectures, materials and announcements
# the page gets its events at most this often (each time all that came, coalesced); a running job reports every 0.25 s,
# so more often only meant more calls into the window (each one waits for its thread and Python's lock)
EVENT_GAP_S = 0.25
_EVENTS_JS = ("(function (a) { var f = window.__lsEvent; if (!f) return; "
              "for (var i = 0; i < a.length; i++) { try { f(a[i]); } catch (x) { console.error(x); } } })(%s)")


def events_script(events: list[dict[str, Any]]) -> str:
    """One script that hands the page all these events in order (one that fails doesn't stop the others)."""
    parts = []
    for e in events:
        try:
            parts.append(json.dumps(e, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            log.debug("event not sent: %r", e.get("type"), exc_info=True)
    return _EVENTS_JS % ("[" + ",".join(parts).replace("</", "<\\/") + "]")


def safe(fn: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(fn)
    def wrapper(self: "Api", *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(self, *args, **kwargs)
        except (AuthError, NeedLogin, NetworkError) as exc:
            return {"ok": False, "error": str(exc), "need_login": isinstance(exc, (AuthError, NeedLogin))}
        except Exception as exc:  # noqa: BLE001
            log.exception("API %s failed", fn.__name__)
            msg = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc   # KeyError's str() adds quotes
            return {"ok": False, "error": str(msg) or exc.__class__.__name__}
    return wrapper


class Api:
    def __init__(self, services: Optional[Services] = None) -> None:
        self._services: Optional[Services] = None
        self._ready = threading.Event()
        self._startup_error = ""
        self._window: Any = None
        self._events: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._sync_lock = threading.Lock()
        self._ai_cancel: Optional[threading.Event] = None
        self._courses_dirty = threading.Event()
        self._mat_lock = threading.Lock()
        self._mat_queue: list[str] = []
        self._mat_running = False
        self._restart = False             # switching profiles: app.py starts a fresh copy once the window closed
        self._close_ok = False            # closing while lectures are transcribed: you said so (see ask_close)
        self._close_asked = self._close_seen = 0.0
        self._open_target = ""            # --open course:<id> (a notification was clicked)
        self._setup_cancel: Optional[threading.Event] = None
        self._setup_running = False
        self._setup_left: tuple[str, ...] = ()          # what the setup's download has still to get (component ids)
        self._update_cancel: Optional[threading.Event] = None
        self._update_running = False
        self._update_installer: Optional[Any] = None    # downloaded: app.py runs it once the window has closed
        self._hwnd = 0                                  # the window (app.py sets it once it's shown)
        self._taskbar = TaskbarProgress()
        self._queue_seen: dict[str, Any] = {}           # the last queue event (the taskbar button shows it)
        self._cur_progress: tuple[str, float] = ("", 0.0)
        self._notifier: Optional[Any] = None
        self._notify_lock = threading.Lock()
        self._remind_lock = threading.Lock()
        threading.Thread(target=self._dispatch, name="ui-events", daemon=True).start()
        if services is not None:
            self._set_services(services)

    # pywebview exposes public attributes too: keep non-API state private (leading underscore)
    def _attach(self, window: Any) -> None:
        self._window = window

    def _set_services(self, services: Services) -> None:
        """The app's services start in the background while the window opens; calls wait for them."""
        self._services = services
        # a transcription waits while the setup still downloads what it needs: the speech model it uses or the graphics
        # card's files (not another speech model, not the offline AI model)
        jobs = services.jobs
        jobs.setup_busy = lambda: self._setup_running and any(
            c == "cuda" or c == f"speech:{jobs.settings().model}" for c in self._setup_left)
        services.subscribe(self._on_service_event)
        self._ready.set()
        threading.Thread(target=self._auto_sync_loop, name="auto-sync", daemon=True).start()
        threading.Thread(target=self._watch_open_requests, name="open-requests", daemon=True).start()
        threading.Thread(target=self._update_loop, name="update-check", daemon=True).start()

    def _update_loop(self) -> None:
        """About once a day (and shortly after the start), if allowed: is there a new version? The page shows it."""
        from . import appconfig, updates

        if not updates.installed():               # run from source or the portable zip: nothing to update with
            return
        time.sleep(20)
        updates.cleanup()
        while True:
            try:
                if appconfig.load().check_updates and updates.due():
                    st = updates.check()
                    if st["available"]:
                        self._push({"type": "update", **st})
            except Exception:  # noqa: BLE001
                log.debug("Update check failed", exc_info=True)
            time.sleep(3 * 3600)

    def _auto_sync_loop(self) -> None:
        """A quiet sync every AUTO_SYNC_S while the app is open, so new lectures, materials and announcements (and
        with them the heads-up) show up by themselves. Nothing is downloaded or transcribed, and a failure only
        shows in the top bar (no sign-in screen popping up while you read)."""
        while True:
            time.sleep(AUTO_SYNC_S)
            try:
                if self._services is None or not self._services.sync.has_credentials():
                    continue
            except Exception:  # noqa: BLE001
                continue
            if self._sync_lock.acquire(blocking=False):        # not while another sync or a full sync runs
                self._run_sync(quiet=True)

    def _startup_failed(self, message: str) -> None:
        self._startup_error = message or t("IEUStudia could not start.")
        self._ready.set()

    @property
    def _s(self) -> Services:
        if not self._ready.wait(120):
            raise RuntimeError(t("IEUStudia is still starting. Try again in a moment."))
        if self._services is None:
            raise RuntimeError(self._startup_error)
        return self._services

    # ------------------------------------------------------------------ events
    def _push(self, evt: dict[str, Any]) -> None:
        self._events.put(evt)

    def _on_service_event(self, evt: dict[str, Any]) -> None:
        self._push(evt)
        kind = evt.get("type")
        if kind in ("queue", "job"):
            try:
                self._show_progress(evt)
            except Exception:  # noqa: BLE001 - cosmetic
                log.debug("taskbar progress failed", exc_info=True)
        if kind == "job" and evt.get("final"):
            self._courses_dirty.set()
            if evt.get("batch_done"):
                try:
                    if evt.get("stage") in ("done", "failed"):
                        flash_taskbar(self._hwnd)
                    threading.Thread(target=self._tell_batch_done, args=(evt,), name="batch-done", daemon=True).start()
                except Exception:  # noqa: BLE001 - cosmetic
                    log.debug("telling the batch is done failed", exc_info=True)

    def _show_progress(self, evt: dict[str, Any]) -> None:
        """The taskbar button fills up as the queued lectures are transcribed (yellow while paused)."""
        if evt.get("type") == "queue":
            self._queue_seen = evt
        q = self._queue_seen
        jobs, b = q.get("jobs") or [], q.get("batch") or {}
        if not jobs:
            self._taskbar.set(self._hwnd, TaskbarProgress.OFF)
            return
        cur = next((j for j in jobs if j["stage"] in ("transcribing", "writing")), None)
        if cur and evt.get("type") == "job" and evt.get("id") == cur["id"] and not evt.get("final"):
            self._cur_progress = (cur["id"], float(evt.get("progress") or 0.0))
        p = self._cur_progress[1] if cur and self._cur_progress[0] == cur["id"] else cur["progress"] if cur else 0.0
        total = b.get("total") or len(jobs)
        self._taskbar.set(self._hwnd, TaskbarProgress.PAUSED if q.get("paused") else TaskbarProgress.NORMAL,
                          (b.get("finished", 0) + p) / max(1, total))

    def _tell_batch_done(self, evt: dict[str, Any]) -> None:
        """Transcription you may have left running in the background is done: a Windows notification (only when the
        window isn't the one you're using: it says so itself then)."""
        r = evt.get("batch_result") or {}                 # none while IEUStudia closes
        done, failed = int(r.get("done") or 0), int(r.get("failed") or 0)
        if not (done or failed) or is_foreground(self._hwnd):
            return
        last = r.get("last_done") or {}                   # the lecture that was done (the last to end may be another)
        rec = (self._s.lib.get_recording(last["id"]) if done == 1 and not failed and last.get("id")
               and not last.get("local") else None)
        if rec is not None:
            title, text, target = (t("Transcribed: {title}", title=last["title"]) if last.get("title") else
                                   t("Your lecture is transcribed"),
                                   t("It's ready to read. Click to open it."), f"day:{rec.day_key}")
        else:
            title = tn(done, "{n} lecture transcribed", "{n} lectures transcribed") if done else t("Transcription finished")
            text = " ".join(filter(None, [
                tn(done, "It's ready to read in IEUStudia.", "They're ready to read in IEUStudia.") if done else "",
                tn(failed, "{n} couldn't be transcribed: the course page says why.",
                   "{n} couldn't be transcribed: the course page says why.") if failed else ""]))
            target = "home"
        self._notify(title, text, target)

    def _notify(self, title: str, text: str, target: str = "home") -> None:
        from .osutil import Notifier

        with self._notify_lock:                           # one icon, however many threads tell something at once
            if self._notifier is None:
                self._notifier = Notifier(Path(__file__).with_name("assets") / "icon.ico", self._notification_clicked)
            self._notifier.notify(title, text, target)

    def _notification_clicked(self, target: Optional[Any]) -> None:
        from .osutil import bring_to_front

        if self._hwnd:
            bring_to_front(self._hwnd)
        if isinstance(target, str) and target:
            self._push({"type": "open", "target": target})

    def _remind(self) -> None:
        """The evening before an announced quiz (and before an exam week): a reminder, once. In the window if you're
        using it, else as a Windows notification. The tray helper does the same while the window is closed."""
        from . import appconfig
        from .agent import _load_seen, _save_seen, reminders
        from .calendar_export import assessment_events

        if not appconfig.load().notify_reminders:
            return
        with self._remind_lock:                           # two syncs ending together remind once
            seen = _load_seen()
            note = reminders(assessment_events(self._s.lib, self._s.settings), dt.datetime.now(), seen)
            if note is None:
                return
            _save_seen(seen)
        title, text, target = note
        if is_foreground(self._hwnd):
            self._push({"type": "toast", "level": "info", "message": text.replace("\n", " · ")})
        else:
            self._notify(title, text, target)

    def _dispatch(self) -> None:
        """Deliver events to the page from one thread; coalesce bursts (only the latest job/queue state) and hand each
        burst over in one call, at most every EVENT_GAP_S. Every call into the page waits for the window's thread, and
        that thread needs Python's lock for it: during a full sync (lectures and files downloading, audio decoding)
        a hundred calls a burst kept the window from answering for seconds."""
        last = 0.0
        while True:
            batch = [self._events.get()]
            time.sleep(max(0.05, last + EVENT_GAP_S - time.monotonic()))
            while True:
                try:
                    batch.append(self._events.get_nowait())
                except queue.Empty:
                    break
            latest: dict[str, dict[str, Any]] = {}
            ordered: list[dict[str, Any]] = []
            for e in batch:
                if e.get("type") == "job" and not e.get("final"):
                    latest[f"job:{e['id']}"] = e
                elif e.get("type") == "chat":        # full snapshots of one answer: the newest one wins
                    latest[f"chat:{e['chat_id']}:{e['seq']}"] = e
                elif e.get("type") == "material":    # likewise per file (progress, then done/error)
                    latest[f"mat:{e['id']}"] = e
                elif e.get("type") == "plan":        # and per study plan
                    latest[f"plan:{e['id']}"] = e
                elif e.get("type") in ("queue", "engine", "ai"):
                    latest[e["type"]] = e
                else:
                    ordered.append(e)
            # every course's view (~100 KB): only the newest goes, where the last one was; one built now if courses
            # changed since (a job ended) is newer still
            at = max((i for i, e in enumerate(ordered) if e.get("type") == "courses"), default=-1)
            ordered = [e for i, e in enumerate(ordered) if i == at or e.get("type") != "courses"]
            out = ordered + list(latest.values())
            if self._courses_dirty.is_set():
                self._courses_dirty.clear()
                fresh = {"type": "courses", "courses": self._course_views()}
                if at >= 0:
                    out[next(i for i, e in enumerate(out) if e.get("type") == "courses")] = fresh
                else:
                    out.append(fresh)
            if self._window is None:
                continue
            run = getattr(self._window, "run_js", None) or self._window.evaluate_js   # (waits for the page too)
            try:
                run(events_script(out))
            except Exception:  # noqa: BLE001 - window closing
                log.debug("event delivery failed", exc_info=True)
            last = time.monotonic()

    def _toast(self, message: str, level: str = "info") -> None:
        self._push({"type": "toast", "level": level, "message": message})

    def _course_views(self) -> list[dict[str, Any]]:
        return views.all_course_views(self._s.lib, self._s.settings, self._s.jobs.snapshot())

    # ------------------------------------------------------------------ language
    def i18n(self) -> dict[str, Any]:
        """The page's language and its Turkish table. Doesn't wait for the services: the first screen needs it."""
        from . import i18n

        try:
            return {"ok": True, **i18n.page_data()}
        except Exception as exc:  # noqa: BLE001 - the page falls back to English
            log.exception("i18n failed")
            return {"ok": False, "error": str(exc), "lang": "en", "table": {}}

    @safe
    def set_language(self, code: str) -> dict[str, Any]:
        """Settings or the first setup screen: "" (as Windows), "en" or "tr". New messages use it at once (the
        helper from its next check); the page then reloads to draw everything in it."""
        from . import appconfig, i18n

        appconfig.update({"language": code if code in ("", "en", "tr") else ""})
        i18n.load_language()
        if self._services is not None:          # the speech models' names and descriptions, in the new language
            threading.Thread(target=self._system_info, daemon=True).start()
        return {"ok": True, **i18n.page_data()}

    # ------------------------------------------------------------------ bootstrap / system
    @safe
    def get_bootstrap(self) -> dict[str, Any]:
        from . import accounts

        profile = self._s.sync.profile
        # Signed in = a known profile plus something to authenticate with (stored password or session
        # cookies). If the session turns out to be dead and no password is stored, the first sync
        # reports need_login and the UI switches to the sign-in screen.
        signed_in = bool(profile and profile.name) and self._s.sync.can_resume()
        active_id = accounts.load()[0]
        if profile and profile.username:          # whose library this is (the profile switcher lists it)
            accounts.remember(active_id, profile.username, profile.name, profile.student_number)
        refresh = threading.Timer(6.0, self._system_info)    # GPU / Claude check, once the first screens are up
        refresh.daemon = True
        refresh.start()
        try:
            cached_system = json.loads(self._s.lib.get_meta("system_info") or "null")
        except ValueError:
            cached_system = None
        from . import appconfig, updates
        from .ai import usage_limits

        target, self._open_target = self._open_target, ""
        return {
            "ok": True, "version": __version__, "signed_in": signed_in, "system": cached_system,
            "limits": usage_limits(), "app": appconfig.load().to_json(), "open": target, "update": updates.status(),
            "updated_from": updates.note_version(),
            "first_run": not self._s.settings.first_run_done,
            "profile": profile.__dict__ if profile else None,
            "settings": self._s.settings.to_json(), "last_sync": self._s.lib.get_meta("last_sync"),
            "courses": self._course_views() if signed_in else [], "queue": self._s.jobs.snapshot(),
            "paused": self._s.jobs.paused(), "resume": self._s.jobs.leftover_view() if signed_in else None,
            "pending_downloads": self._pending_downloads(), "downloading": self._setup_running,
            **self._profiles_view(),
        }

    def _pending_downloads(self) -> list[dict[str, Any]]:
        """What the setup started downloading and isn't on this PC yet (IEUStudia was closed during it)."""
        from . import appconfig
        from .components import describe, ready

        try:      # only an offer: an id this version doesn't know (another version wrote it) must never stop the start
            return [d for c in appconfig.load().pending_downloads if (d := describe(c)) and not ready(c)]
        except Exception:  # noqa: BLE001
            log.exception("pending downloads failed")
            return []

    def _system_info(self) -> None:
        try:
            from .ai import claude_info
            from .engine.models import MODEL_PRESETS, model_is_downloaded
            from .hardware import MIN_CUDA_DRIVER, _driver_number, nvidia_gpu

            nv = nvidia_gpu()                        # one nvidia-smi run: the card for the display and its driver
            gpu = {"available": nv["present"], "name": nv["name"], "vram_mb": nv["vram_mb"]}
            presets = [{"id": k, "label": t(v["label"]), "description": t(v["description"]), "size_mb": v["size_mb"],
                        "downloaded": model_is_downloaded(k)} for k, v in MODEL_PRESETS.items()]
            from .components import cuda_ready

            used = bool(gpu.get("available")) and cuda_ready() and self._s.settings.device != "cpu"
            # a card the setup would set up: enough graphics memory and a recent driver (as hardware.assess)
            fits = (bool(gpu.get("available")) and int(gpu.get("vram_mb") or 0) >= 3500
                    and _driver_number(nv.get("driver", "")) >= MIN_CUDA_DRIVER)
            info = {"gpu": {**gpu, "libraries": cuda_ready(), "used": used, "fits": fits}, "presets": presets,
                    "ai": claude_info(),
                    "engine": {"model": self._s.settings.model, "loaded": self._s.jobs.engine_loaded()}}
            self._s.lib.set_meta("system_info", json.dumps(info))
            self._push({"type": "system", **info})
        except Exception:  # noqa: BLE001
            log.exception("system info failed")

    # ------------------------------------------------------------------ first-start setup and machine settings
    @safe
    def setup_state(self) -> dict[str, Any]:
        """What the first-start screen needs: this PC's check, the components and their sizes, the choices so far."""
        from . import appconfig, hardware, launch
        from .components import catalog

        facts = hardware.check(output_dir=Path(self._s.settings.output_dir))
        verdict = hardware.assess(facts)
        cfg = appconfig.load()
        return {"ok": True, "facts": facts, "verdict": verdict, "catalog": catalog(facts), "app": cfg.to_json(),
                "startup": launch.startup_enabled(), "model": self._s.settings.model,
                "output_dir": self._s.settings.output_dir, "frozen": launch.frozen()}

    @safe
    def setup_plan(self, speech: str, llm: str = "") -> dict[str, Any]:
        """The downloads a choice needs, their total and whether the disk has room."""
        from . import hardware
        from .components import plan

        facts = hardware.check()
        verdict = hardware.assess(facts)
        items = plan(speech, llm, gpu=verdict["gpu"])
        need_gb = sum(i["size_mb"] for i in items) / 1024 * 1.4          # unpacked + room to work
        again = hardware.assess(facts, need_gb)
        return {"ok": True, "items": items, "total_mb": sum(i["size_mb"] for i in items),
                "free_gb": facts["disk"]["data_free_gb"], "blockers": again["blockers"]}

    @safe
    def setup_download(self, ids: list[str]) -> dict[str, Any]:
        """Download the components one after another in the background (events: type "setup")."""
        import requests

        from .components import download
        from .net import plain_error
        from .osutil import KeepAwake

        if self._setup_running:
            return {"ok": True, "running": True}
        self._setup_left = tuple(ids)             # before the flag: a transcription never sees the last run's
        self._setup_running = True
        cancel = self._setup_cancel = threading.Event()
        awake = KeepAwake("IEUStudia is downloading what it needs")

        def run() -> None:
            failed = ""
            awake.on()
            try:
                for cid in ids:
                    last = [0.0]

                    def progress(done: int, total: int, cid: str = cid) -> None:
                        now = time.monotonic()
                        if now - last[0] > 0.25 or done >= total:
                            last[0] = now
                            self._push({"type": "setup", "id": cid, "state": "downloading", "done": done, "total": total})

                    self._push({"type": "setup", "id": cid, "state": "downloading", "done": 0, "total": 0})
                    try:
                        download(cid, progress, cancel)
                        self._setup_left = tuple(c for c in self._setup_left if c != cid)
                        self._push({"type": "setup", "id": cid, "state": "done"})
                    except Exception as exc:  # noqa: BLE001
                        if cancel.is_set():
                            self._push({"type": "setup", "id": cid, "state": "cancelled"})
                            return
                        log.exception("Download of %s failed", cid)
                        failed = plain_error(exc)
                        if isinstance(exc, (requests.RequestException, OSError)):   # its parts are kept for the next try
                            failed = ((failed if failed.endswith((".", "?", "!")) else failed + ".")
                                      + " " + t("(Downloading again continues where it stopped.)"))
                        self._push({"type": "setup", "id": cid, "state": "error", "error": failed})
                        return
            finally:
                awake.off()
                self._setup_running = False
                ok = not failed and not cancel.is_set()
                if ok:
                    self._note_downloads([])
                self._push({"type": "setup", "id": "", "state": "idle", "ok": ok})
                threading.Thread(target=self._system_info, daemon=True).start()

        try:
            self._note_downloads(ids)                 # a download cut off is offered again next start
            threading.Thread(target=run, name="setup-download", daemon=True).start()
        except BaseException:
            self._setup_running = False           # it never started: no transcription or next try may wait for it
            raise
        return {"ok": True}

    def _note_downloads(self, ids: list[str]) -> None:
        """Note in app.json what the next start offers again if the download is cut off ([] once it's done). Another
        program (the tray helper, an antivirus) can hold app.json open for a moment: then only the note is lost, and
        the download still runs and still reports its end."""
        from . import appconfig

        try:
            appconfig.update({"pending_downloads": list(ids)})
        except OSError:
            log.warning("Could not note the downloads in app.json", exc_info=True)

    @safe
    def setup_cancel(self) -> dict[str, Any]:
        if self._setup_cancel is not None:
            self._setup_cancel.set()
        return {"ok": True}

    # ------------------------------------------------------------------ new versions
    @safe
    def update_check(self) -> dict[str, Any]:
        """Look for a new version now (Settings → About)."""
        from . import updates

        st = updates.check(force=True)
        self._push({"type": "update", **st})
        return {"ok": True, **st}

    @safe
    def update_install(self) -> dict[str, Any]:
        """Download the new version, check it, then close: app.py runs its installer, which opens IEUStudia again."""
        from . import updates
        from .cancel import Cancelled
        from .net import plain_error

        st = updates.status()
        if self._update_running:
            return {"ok": True, "running": True}
        if not st["available"]:
            return {"ok": False, "error": t("You already have the newest version.")}
        if not st["can_install"]:
            return {"ok": False, "error": t("This copy of IEUStudia can't update itself (it wasn't installed with the "
                                            "installer). Download the new version from its release page.")}
        if self._s.jobs.busy():
            return {"ok": False, "error": t("A transcription is still running. Update when it has finished, or cancel "
                                            "it first.")}
        if self._setup_running:
            return {"ok": False, "error": t("IEUStudia is still downloading what it needs. Update when that's done.")}
        self._update_running = True
        cancel = self._update_cancel = threading.Event()

        def run() -> None:
            last = [0.0]

            def progress(done: int, total: int) -> None:
                now = time.monotonic()
                if now - last[0] > 0.25 or done >= total:
                    last[0] = now
                    self._push({"type": "update", "state": "downloading", "done": done, "total": total})

            try:
                self._push({"type": "update", "state": "downloading", "done": 0, "total": 0})
                path = updates.download(progress, cancel)
                if self._s.jobs.busy():                # a transcription was started meanwhile: it isn't cut off
                    self._push({"type": "update", "state": "error", "error": t(
                        "A transcription started while the update downloaded. Update when it has finished (the "
                        "download is kept).")})
                    return
                self._update_installer = path
                self._push({"type": "update", "state": "installing"})
                time.sleep(1.5)                        # long enough to read that the window closes now
                if self._s.jobs.busy():                # started in that moment: closing would cut it off
                    self._update_installer = None
                    self._push({"type": "update", "state": "error", "error": t(
                        "A transcription started. Update when it has finished (the download is kept).")})
                    return
                self._window.destroy()
            except Cancelled:
                self._push({"type": "update", "state": "cancelled"})
            except Exception as exc:  # noqa: BLE001
                log.exception("Update failed")
                self._push({"type": "update", "state": "error", "error": plain_error(exc)})
            finally:
                self._update_running = False

        threading.Thread(target=run, name="update", daemon=True).start()
        return {"ok": True}

    @safe
    def update_cancel(self) -> dict[str, Any]:
        if self._update_cancel is not None:
            self._update_cancel.set()
        return {"ok": True}

    @safe
    def app_config(self, patch: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Read or change the machine-wide settings (AI choice, background helper, notifications)."""
        from . import agent, appconfig, launch

        cfg = appconfig.update(patch) if patch else appconfig.load()
        if patch and "start_with_windows" in patch:
            launch.set_startup(bool(cfg.start_with_windows and cfg.tray))
        if patch and "tray" in patch:
            if cfg.tray:
                agent.ensure_running()
            elif launch.startup_enabled():
                launch.set_startup(False)
        if patch and {"ai_provider", "codex_model", "local_model"} & set(patch):
            from .ai import claude_cli_info, codex_cli_info

            claude_cli_info.cache_clear()
            codex_cli_info.cache_clear()
            threading.Thread(target=self._system_info, daemon=True).start()
        return {"ok": True, "app": cfg.to_json(), "startup": launch.startup_enabled(), "helper": agent.running()}

    @safe
    def ai_status(self, which: str = "") -> dict[str, Any]:
        from .ai import ai_status

        return {"ok": True, **ai_status(which or "")}

    @safe
    def ai_login(self, which: str, consent: bool = False) -> dict[str, Any]:
        """Sign in: "chatgpt" is "Continue with ChatGPT" in the browser (events: type "chatgpt"); "claude" and "codex"
        open Claude Code's or the Codex CLI's own sign-in."""
        from . import ai_setup, chatgpt
        from .ai import start_login

        if which == "chatgpt":
            ai_setup.forget_test("chatgpt")
            return {"ok": True, **chatgpt.start_sign_in(self._ai_event, consent=bool(consent),
                                                        open_browser=open_in_browser)}
        with normal_priority():
            start_login(which)
        return {"ok": True}

    def _ai_event(self, e: dict[str, Any]) -> None:
        """A sign-in or an install to the page; once it is through, whether the AI can be used changed."""
        self._push(e)
        if e.get("state") == "done":
            threading.Thread(target=self._system_info, daemon=True).start()

    @safe
    def ai_sign_out(self, which: str) -> dict[str, Any]:
        """Sign out of ChatGPT ("Continue with ChatGPT"): OpenAI revokes the session, IEUStudia forgets it."""
        from . import ai_setup, chatgpt

        if which != "chatgpt":
            return {"ok": False, "error": t("Sign out in Claude Code or the Codex CLI itself.")}
        ai_setup.forget_test("chatgpt")
        out = chatgpt.sign_out()
        threading.Thread(target=self._system_info, daemon=True).start()
        return {"ok": True, **out}

    @safe
    def ai_install(self, which: str) -> dict[str, Any]:
        """Install Claude Code or the Codex CLI in the background (events: type "ai_setup")."""
        from .ai_setup import start_install

        return {"ok": True, "job": start_install(which, self._ai_event)}

    @safe
    def ai_test(self, which: str) -> dict[str, Any]:
        """A short test message through ChatGPT, Claude Code or the Codex CLI (a few tokens of the user's plan)."""
        from .ai_setup import test

        return {"ok": True, "test": test(which)}

    @safe
    def finish_setup(self) -> dict[str, Any]:
        from . import agent, appconfig

        cfg = appconfig.update({"onboarded": True, "terms_accepted": dt.date.today().isoformat()})
        if not self._s.settings.first_run_done:
            self._s.update_settings({"first_run_done": True})
        if cfg.tray:
            agent.ensure_running()
        return {"ok": True, "app": cfg.to_json()}

    @safe
    def storage(self) -> dict[str, Any]:
        from . import hardware
        from .components import catalog, disk_usage

        facts = hardware.check()
        return {"ok": True, "usage": disk_usage(Path(self._s.settings.output_dir)), "catalog": catalog(facts),
                "free_gb": facts["disk"]["data_free_gb"], "data_dir": facts["disk"]["data_dir"]}

    @safe
    def delete_component(self, cid: str) -> dict[str, Any]:
        from .components import delete_llm
        from .engine.models import delete_model

        kind, _, key = cid.partition(":")
        if kind == "speech":
            if key == self._s.settings.model:
                return {"ok": False, "error": t("That is the speech model in use; pick another one first.")}
            delete_model(key)
        elif kind == "llm":
            from . import appconfig

            if appconfig.load().ai_provider == "local" and appconfig.load().local_model == key:
                return {"ok": False, "error": t("That offline model is in use; pick another AI first.")}
            delete_llm(key)
        else:
            return {"ok": False, "error": t("This part cannot be removed.")}
        return self.storage()

    def _watch_open_requests(self) -> None:
        """Another start of the app (e.g. a notification was clicked) asks this window to show a page: it writes the
        page to a file and sets an event this thread waits for."""
        from .osutil import instance_name, open_request_event, take_open_request, wait_event

        name = instance_name()
        event = open_request_event(name)
        while True:
            target = take_open_request(name)            # (first: one made before the event existed)
            if target:
                self._push({"type": "open", "target": target})
            if not (event and wait_event(event)):
                time.sleep(1.0)

    # ------------------------------------------------------------------ account / sync
    @safe
    def sign_in(self, username: str, password: str) -> dict[str, Any]:
        from . import accounts

        cur = accounts.active()
        if cur.username and cur.username.strip().casefold() != (username or "").strip().casefold():
            # someone else: their courses and transcripts get a profile of their own (then the UI switches to it)
            res = self.profile_add(username, password)
            if not res.get("ok"):
                return res
            return {"ok": True, "switch_to": res["profile"]["id"], "profile": res["profile"]}
        profile = self._s.sync.sign_in(username, password)
        accounts.remember(cur.id, profile.username, profile.name, profile.student_number)
        self.sync()
        return {"ok": True, "profile": profile.__dict__}

    @safe
    def sign_out(self) -> dict[str, Any]:
        self._s.jobs.cancel(None)
        self._s.sync.sign_out()
        return {"ok": True, **self._profiles_view()}

    # ------------------------------------------------------------------ profiles (several accounts on this PC)
    def _profiles_view(self) -> dict[str, Any]:
        from . import accounts

        active_id, accs = accounts.load()
        return {"profile_id": active_id, "profiles": [a.view(active_id) for a in accs]}

    @safe
    def profiles(self) -> dict[str, Any]:
        return {"ok": True, **self._profiles_view()}

    @safe
    def profile_add(self, username: str, password: str) -> dict[str, Any]:
        """Sign in to another IEULearn account and keep it as a profile of its own (nothing switches yet)."""
        from . import accounts, secrets_store
        from .sync import SyncService

        username = (username or "").strip()
        if not username or not password:
            return {"ok": False, "error": t("Enter the username and password.")}
        cur = accounts.active()
        if cur.username and cur.username.casefold() == username.casefold():
            return {"ok": False, "error": t("That is the profile you are using now.")}
        profile, courses, cookies = SyncService.check_account(username, password)
        acc = accounts.find(username)
        created = acc is None
        if acc is None:
            acc = accounts.create(username, profile.name, profile.student_number)
            accounts.seed(acc, profile, courses, self._s.settings)
        else:
            accounts.remember(acc.id, username, profile.name, profile.student_number)
        secrets_store.set_credentials(username, password, folder=acc.path)
        secrets_store.save_cookies(cookies, folder=acc.path)
        log.info("Profile %s ready (%s)", acc.id, "new" if created else "existing")
        active_id = accounts.load()[0]
        return {"ok": True, "created": created, "profile": (accounts.get(acc.id) or acc).view(active_id)}

    @safe
    def profile_switch(self, profile_id: str, force: bool = False) -> dict[str, Any]:
        """Open another profile: the app closes and starts again with that account's library."""
        from . import accounts

        if profile_id == accounts.load()[0]:
            return {"ok": True, "same": True}
        busy = self._services is not None and self._services.jobs.busy()
        if busy and not force:
            return {"ok": False, "busy": True, "error": t("A transcription is still running.")}
        acc = accounts.set_active(profile_id)
        if busy:
            self._services.jobs.shutdown()           # what is still waiting is kept for this profile
        log.info("Switching to profile %s", acc.id)
        self._restart = True
        timer = threading.Timer(0.4, self._close_for_restart)     # the page shows "Switching…" first
        timer.daemon = True
        timer.start()
        return {"ok": True, "profile": acc.view(acc.id)}

    def _close_for_restart(self) -> None:
        try:
            self._window.destroy()
        except Exception:  # noqa: BLE001 - the window is already gone
            log.debug("closing for restart failed", exc_info=True)

    # ------------------------------------------------------------------ closing while lectures are transcribed
    def _ask_close(self) -> bool:
        """app.py: the window is closed while lectures are transcribed. True: the page asks (web/failsafe.js) and
        close_now closes the window. False: the page didn't show the last question (it is stuck or still starting):
        app.py asks in a window of its own."""
        now = time.monotonic()
        if self._close_asked and self._close_seen < self._close_asked and now - self._close_asked > 3:
            return False
        self._close_asked = now
        self._push({"type": "close-ask"})
        return True

    def close_seen(self) -> dict[str, Any]:
        self._close_seen = time.monotonic()
        return {"ok": True}

    @safe
    def close_now(self, dont_ask: bool = False) -> dict[str, Any]:
        """Close although lectures are transcribed (they go on where they stopped next time)."""
        if dont_ask:
            self._s.update_settings({"ask_before_closing": False})
        self._close_ok = True
        threading.Thread(target=self._close_for_restart, name="close", daemon=True).start()
        return {"ok": True}

    @safe
    def profile_remove(self, profile_id: str) -> dict[str, Any]:
        from . import accounts

        accounts.remove(profile_id)
        return {"ok": True, **self._profiles_view()}

    @safe
    def sync(self) -> dict[str, Any]:
        if not self._sync_lock.acquire(blocking=False):
            return {"ok": True, "running": True}
        threading.Thread(target=self._run_sync, name="sync", daemon=True).start()
        return {"ok": True}

    def _run_sync(self, quiet: bool = False) -> bool:
        """One sync (the caller holds ``_sync_lock``; released here). True when it worked. ``quiet``: started by the
        app itself (the page then shows errors in the top bar only)."""
        q = {"quiet": True} if quiet else {}

        def again(fn: Callable[[], Any], partial: Callable[[Any], bool]) -> Any:
            # no internet connection (the PC just woke up, say): a sync you asked for waits for it and goes on
            return fn() if quiet else net.until_online(fn, lambda: self._push({
                "type": "sync", "phase": "progress", "offline": True,
                "message": t("Waiting for the internet connection. The sync goes on by itself.")}), partial)
        try:
            self._push({"type": "sync", "phase": "start", "message": t("Syncing…"), **q})
            res = again(lambda: self._s.sync.sync(
                lambda m: self._push({"type": "sync", "phase": "progress", "message": m, **q}),
                lambda ids: self._push({"type": "courses", "courses": self._course_views()})),
                lambda r: bool(r.errors))
            course_views = self._course_views()
            self._push({"type": "courses", "courses": course_views})
            new = self._own_new(course_views, res.new_ids)
            self._push({"type": "sync", "phase": "done", "new_count": new,
                        "message": t("{n} new recording(s)", n=new) if new else t("Up to date"),
                        "last_sync": self._s.lib.get_meta("last_sync"),
                        "profile": res.profile.__dict__ if res.profile else None,
                        "errors": res.errors, **q})
            # then each course's weekly materials, announcements, outcomes (listed, nothing downloaded)
            self._push({"type": "content", "phase": "start", **q})
            content = again(self._s.sync.sync_content, lambda c: bool(c["errors"]))
            self._courses_dirty.set()                     # (built once with whatever else changed meanwhile)
            self._push({"type": "content", "phase": "done", "new_materials": len(content["new_materials"]),
                        "new_announcements": len(content["new_announcements"]), "errors": content["errors"], **q})
            return True
        except (AuthError, NeedLogin) as exc:
            self._push({"type": "sync", "phase": "error", "need_login": True, "message": str(exc), **q})
        except Exception as exc:  # noqa: BLE001
            log.log(logging.WARNING if quiet else logging.ERROR, "sync failed", exc_info=True)
            self._push({"type": "sync", "phase": "error", "message": str(exc) or t("Sync failed"), **q})
        finally:
            self._sync_lock.release()
            try:                                          # from what is on this PC: also when IEULearn can't be reached
                self._remind()
            except Exception:  # noqa: BLE001 - a reminder never fails a sync
                log.warning("Reminders failed", exc_info=True)
        return False

    @staticmethod
    def _own_new(course_views: list[dict[str, Any]], new_ids: list[str]) -> int:
        """New recordings worth telling you about: not other sections' lectures in a course where you follow yours."""
        new = set(new_ids)
        n = 0
        for c in course_views:
            mine_only = c.get("show_mode") == "mine" and len(c.get("available_sections") or []) > 1
            for d in c.get("days", []):
                n += sum(1 for r in d["recordings"]
                         if r["id"] in new and not r["duplicate"] and not (mine_only and r.get("mine") is False))
        return n

    # ------------------------------------------------------------------ full sync / course sync
    def _gpu(self) -> bool:
        """Will the graphics card transcribe? (An NVIDIA card without its libraries, or one that failed to load the
        model, leaves it to the processor.)"""
        if self._s.settings.device == "cpu":
            return False
        engine = self._s.jobs._engine
        if engine is not None and getattr(engine, "loaded", False):
            return engine.device_info.get("device") == "cuda"
        try:
            gpu = json.loads(self._s.lib.get_meta("system_info") or "{}").get("gpu") or {}
        except ValueError:
            return False
        return bool(gpu.get("available") and gpu.get("libraries"))

    @safe
    def catch_up_estimate(self, course_id: Optional[str] = None) -> dict[str, Any]:
        """What a full sync (or one course's sync) would queue, from what is known now."""
        from .catchup import course_needs, summary

        needs = course_needs(self._s.lib, self._s.settings, [course_id] if course_id else None,
                             self._s.jobs.active_ids())
        return {"ok": True, **summary(needs, self._gpu(), self._s.settings.model)}

    @safe
    def catch_up(self, course_ids: Optional[list[str]] = None, materials: bool = True,
                 include_unknown: Optional[list[str]] = None) -> dict[str, Any]:
        """Sync, then queue every lecture of your sections that is not transcribed yet (newest first) and, if asked,
        download the materials. ``course_ids``: None = every course; ``include_unknown``: courses whose lectures that
        match no section are queued too this time (you said so in the dialog). Claude is not involved."""
        from .catchup import course_needs, queue_order

        ids = [str(c) for c in course_ids] if course_ids else None
        scope = "all" if ids is None else (ids[0] if len(ids) == 1 else "some")
        include = [str(c) for c in include_unknown or ()]

        def run() -> None:
            self._push({"type": "catchup", "phase": "sync", "scope": scope})
            if self._sync_lock.acquire(blocking=False):
                ok = self._run_sync()
            else:                                  # a sync is running already: use its result
                with self._sync_lock:
                    ok = True
            if not ok:
                self._push({"type": "catchup", "phase": "error", "scope": scope,
                            "message": t("The sync failed, so nothing was queued.")})
                return
            try:
                needs = course_needs(self._s.lib, self._s.settings, ids, self._s.jobs.active_ids(), include)
                order = queue_order(needs)
                queued = self._s.jobs.enqueue(order, batch=True) if order else 0     # after what you ask for
                mats = [m for n in needs for m in n.materials] if materials else []
                if mats:
                    self.materials_download(mats)
                self._courses_dirty.set()
                self._push(self._s.jobs.queue_event())
                self._push({"type": "catchup", "phase": "done", "scope": scope, "lectures": queued,
                            "materials": len(mats), "audio_s": round(sum(n.audio_s for n in needs)),
                            "waiting": [n.code for n in needs if n.needs_section and n.waiting],
                            "unknown": sum(len(n.unknown) for n in needs)})
            except Exception as exc:  # noqa: BLE001
                log.exception("catch-up failed")
                self._push({"type": "catchup", "phase": "error", "scope": scope, "message": str(exc) or t("Failed")})

        threading.Thread(target=run, name="catch-up", daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ transcription
    @safe
    def transcribe(self, ids: list[str]) -> dict[str, Any]:
        """Queue lectures you asked for: they go before a full or course sync's batch. One still being recorded can't
        be: said when nothing else was asked for."""
        ids = [str(i) for i in ids]
        live = [i for i in ids if (r := self._s.lib.get_recording(i)) is not None and r.live]
        if live and len(live) == len(ids):
            return {"ok": False, "error": tn(len(live), "This lecture is still being recorded. It can be transcribed "
                                             "once Panopto has finished it.", "These lectures are still being recorded. "
                                             "They can be transcribed once Panopto has finished them.")}
        n = self._s.jobs.enqueue([i for i in ids if i not in live])
        self._courses_dirty.set()
        self._push(self._s.jobs.queue_event())
        return {"ok": True, "queued": n}

    @safe
    def transcribe_day(self, day_key: str, batch: bool = False) -> dict[str, Any]:
        """Queue a class day's lectures. Where several sections publish and you follow yours, only your section's
        (as Sync course does): in a ``batch`` (a chat's or study plan's weeks) a day with yours queues those and a
        day that matches no section nothing; a day you ask for queues all but the other sections', and a batch queues
        nothing while your section is unknown. ``unknown``: lectures left out for matching no section; ``needs_section``: choose it first."""
        from .catchup import no_sound
        from .sections import available_sections, classify, guess_my_section

        course, recs = self._s.day(day_key)
        s = self._s.settings
        lib = self._s.lib
        ids = [r.delivery_id for r in recs if not r.duplicate_of and not r.hidden and r.status != "done"
               and not (0 < r.duration_s < s.min_duration_s) and not no_sound(r, lib.warnings(r.delivery_id))]
        prefs = s.prefs(course.id)
        every = self._s.lib.recordings(course.id)
        unknown, needs_section = 0, False
        if prefs.show_mode != "all" and len(available_sections(every)) > 1:
            my_section = prefs.my_section or guess_my_section(course)
            mine = classify(course, every, my_section)
            if not my_section:
                needs_section, ids = batch and bool(ids), ([] if batch else ids)
            elif batch and any(mine.get(r.delivery_id) is True for r in recs):   # yours that day: not the other
                unknown = sum(1 for i in ids if mine.get(i) is None)         # sections', nor one that matches none
                ids = [i for i in ids if mine.get(i) is True]
            elif batch:                                                      # matches no section (or another's)
                unknown, ids = sum(1 for i in ids if mine.get(i) is None), []
            elif any(mine.get(r.delivery_id) is not False for r in recs):    # you asked for this day: not another's
                ids = [i for i in ids if mine.get(i) is not False]
        return {**self.transcribe(ids), "unknown": unknown, "needs_section": needs_section}

    @safe
    def cancel(self, job_id: Optional[str] = None) -> dict[str, Any]:
        self._s.jobs.cancel(job_id or None)
        return {"ok": True}

    @safe
    def queue_first(self, job_id: str) -> dict[str, Any]:
        """Do this lecture next."""
        return {"ok": self._s.jobs.first(str(job_id))}

    @safe
    def queue_pause(self, on: bool) -> dict[str, Any]:
        self._s.jobs.pause(bool(on))
        return {"ok": True, "paused": self._s.jobs.paused()}

    @safe
    def queue_resume(self) -> dict[str, Any]:
        """Go on with the lectures that were still waiting when IEUStudia closed."""
        n = self._s.jobs.resume_leftover()
        self._courses_dirty.set()
        return {"ok": True, "queued": n}

    @safe
    def queue_forget(self) -> dict[str, Any]:
        self._s.jobs.forget_leftover()
        return {"ok": True}

    @safe
    def transcribe_local_file(self) -> dict[str, Any]:
        import re

        import webview

        def kind(name: str, patterns: str) -> str:      # pywebview refuses a name with anything but letters and spaces
            return f"{re.sub(r'[^\w ]+', '', name).strip()} ({patterns})"

        paths = self._window.create_file_dialog(
            webview.OPEN_DIALOG, allow_multiple=True,
            file_types=(kind(t("Audio and video files"), "*.mp4;*.m4a;*.mp3;*.wav;*.mkv;*.mov;*.webm;*.aac;*.ogg;*.flac"),
                        kind(t("All files"), "*.*")))
        for p in paths or []:
            self._s.jobs.enqueue_local(Path(p))
        return {"ok": True, "queued": len(paths or [])}

    # ------------------------------------------------------------------ reading
    @safe
    def get_day(self, day_key: str) -> dict[str, Any]:
        detail = views.day_detail(self._s.lib, self._s.settings, day_key, self._s.day_files(day_key))
        if detail is None:
            return {"ok": False, "error": t("This class day no longer exists.")}
        from .outputs.flashcards import parse_cards

        files = detail["files"]
        detail["notes"] = [{"kind": k, "label": t(NOTE_KINDS[k]), "md": Path(files[f"notes:{k}"]).read_text(encoding="utf-8")}
                           for k in files.get("notes_kinds", [])]          # newest first
        for n in detail["notes"]:
            if n["kind"] == "flashcards":                                  # to practise them one by one
                n["cards"] = parse_cards(n["md"])
        return {"ok": True, "day": detail}

    @safe
    def mark_set(self, delivery_id: str, at: float, text: str = "", note: Optional[str] = None) -> dict[str, Any]:
        """Mark a passage of a transcript for later (or change the mark's note). ``at``: its start (seconds)."""
        self._s.lib.set_mark(str(delivery_id), float(at), str(text or ""), None if note is None else str(note))
        return {"ok": True}

    @safe
    def mark_delete(self, delivery_id: str, at: float) -> dict[str, Any]:
        self._s.lib.delete_mark(str(delivery_id), float(at))
        return {"ok": True}

    @safe
    def course_marks(self, course_id: str) -> dict[str, Any]:
        """The marks in a course's transcripts, with their class day (Get ready lists those of its weeks)."""
        from .outputs.common import format_ts, select_parts

        recs = {r.delivery_id: r for r in self._s.lib.recordings(course_id)}
        done = {i: True for i in self._s.lib.transcribed_ids() if i in recs}
        parts: dict[str, int] = {}                        # numbered as the transcript page numbers them
        for day in {r.day_key for r in recs.values()}:
            own = select_parts([r for r in recs.values() if r.day_key == day], done)
            parts.update({r.delivery_id: i for i, r in enumerate(own, start=1)})
        out = [{"day_key": recs[m["delivery_id"]].day_key, "date": recs[m["delivery_id"]].start.date().isoformat(),
                "part": parts.get(m["delivery_id"], 1), "t": m["t"], "ts": format_ts(m["t"]), "text": m["text"],
                "note": m["note"]} for m in self._s.lib.marks(recs) if m["delivery_id"] in recs]
        return {"ok": True, "marks": sorted(out, key=lambda m: (m["date"], m["part"], m["t"]))}

    @safe
    def library(self) -> dict[str, Any]:
        out = []
        for cv in self._course_views():
            days = [d for d in cv["days"] if d["any_done"]]
            if days:
                out.append({"course_id": cv["id"], "code": cv["code"], "name": cv["display_name"], "days": days})
        return {"ok": True, "courses": out}

    @safe
    def search(self, query: str) -> dict[str, Any]:
        hits = self._s.lib.search(query, limit=60)
        out = []
        cache: dict[str, Any] = {}
        courses = {c.id: c for c in self._s.lib.courses(True)}
        for h in hits:
            rec = cache.get(h["delivery_id"]) or self._s.lib.get_recording(h["delivery_id"])
            if rec is None:
                continue
            cache[h["delivery_id"]] = rec
            course = courses.get(rec.course_id)
            at = float(h["t"])
            out.append({"day_key": rec.day_key, "delivery_id": rec.delivery_id, "t": at,
                        "ts": f"{int(at // 60):02d}:{int(at % 60):02d}", "snippet": h["snippet"],
                        "course_code": course.code if course else "", "date": rec.start.date().isoformat(),
                        "time": f"{rec.start:%H:%M}", "viewer_url": rec.viewer_url})
        mats = []
        found = self._s.lib.search_materials(query, limit=30)
        known = self._s.lib.get_materials(h["resource_id"] for h in found)
        for h in found:
            m = known.get(h["resource_id"])
            if not m:
                continue
            course = courses.get(m["course_id"])
            mats.append({"resource_id": m["resource_id"], "title": m["title"], "page": h["page"], "snippet": h["snippet"],
                         "course_id": m["course_id"], "course_code": course.code if course else ""})
        return {"ok": True, "results": out, "materials": mats}

    # ------------------------------------------------------------------ course content & materials
    @safe
    def course_detail(self, course_id: str) -> dict[str, Any]:
        detail = views.course_detail(self._s.lib, self._s.settings, course_id, self._s.jobs.snapshot())
        if detail is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        return {"ok": True, "course": detail}

    @safe
    def materials_download(self, resource_ids: list[str]) -> dict[str, Any]:
        """Download materials (and index their text) one after another in the background."""
        ids = [str(i) for i in resource_ids or []]
        with self._mat_lock:
            fresh = [i for i in ids if i not in self._mat_queue]
            self._mat_queue.extend(fresh)
            start = not self._mat_running
            self._mat_running = True
        for i in fresh:
            self._push({"type": "material", "id": i, "state": "queued"})
        if start:
            threading.Thread(target=self._material_worker, name="materials", daemon=True).start()
        return {"ok": True, "queued": len(fresh)}

    def _material_worker(self) -> None:
        session = self._s.sync.worker_session()
        while True:
            with self._mat_lock:
                if not self._mat_queue:
                    self._mat_running = False
                    break
                rid = self._mat_queue.pop(0)
            last = [0.0]

            def progress(done: int, total: int, rid: str = rid) -> None:
                now = time.monotonic()
                if now - last[0] > 0.15:
                    last[0] = now
                    self._push({"type": "material", "id": rid, "state": "downloading", "done": done, "total": total})

            try:
                self._push({"type": "material", "id": rid, "state": "downloading", "done": 0, "total": 0})
                waits = [5, 15, 30]                    # no connection (Wi-Fi coming back after sleep): wait it out
                while True:
                    try:
                        path = self._s.materials.download(rid, session, progress)
                        break
                    except NetworkError:               # no connection, or a timeout
                        if waits:
                            time.sleep(waits.pop(0))
                            continue
                        if net.online():               # the internet is there, IEULearn isn't
                            raise
                    # none at all, for longer (the PC asleep): the files wait for it, then go on by themselves
                    with self._mat_lock:
                        rest = self._mat_queue[:]
                    for one in [rid, *rest]:
                        self._push({"type": "material", "id": one, "state": "queued", "offline": True})
                    net.wait_online()
                    waits = [5, 15, 30]
                    self._push({"type": "material", "id": rid, "state": "downloading", "done": 0, "total": 0,
                                "offline": False})
                    for one in rest:
                        self._push({"type": "material", "id": one, "state": "queued", "offline": False})
                pages = self._s.materials.index_text(rid)
                self._push({"type": "material", "id": rid, "state": "done", "path": str(path), "pages": pages})
            except NetworkError as exc:                # still none: the rest would fail the same way, say it once
                with self._mat_lock:
                    rest, self._mat_queue = self._mat_queue[:], []
                log.warning("Materials stopped: %s", exc)
                for i, one in enumerate([rid, *rest]):
                    self._push({"type": "material", "id": one, "state": "error", "error": str(exc), "quiet": i > 0})
            except NeedLogin:
                try:
                    self._s.sync.ensure_login()
                    session = self._s.sync.worker_session()
                    with self._mat_lock:
                        self._mat_queue.insert(0, rid)
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._push({"type": "material", "id": rid, "state": "error", "error": str(exc)})
            except Exception as exc:  # noqa: BLE001 - reported per file
                log.warning("Material %s failed: %s", rid, exc)
                self._push({"type": "material", "id": rid, "state": "error", "error": str(exc) or t("Download failed")})
        self._s.sync.absorb(session)
        self._courses_dirty.set()
        self._push({"type": "material", "id": "", "state": "idle"})

    @safe
    def material_open(self, resource_id: str, reveal_only: bool = False) -> dict[str, Any]:
        m = self._s.lib.get_material(resource_id)
        if not m or not m.get("local_path") or not Path(m["local_path"]).is_file():
            return {"ok": False, "error": t("Download it first.")}
        (reveal if reveal_only else open_path)(m["local_path"])
        return {"ok": True}

    @safe
    def course_mark_seen(self, course_id: str, what: str = "all") -> dict[str, Any]:
        if what in ("all", "materials"):
            self._s.lib.clear_new_materials(course_id or None)
        if what in ("all", "announcements"):
            self._s.lib.clear_new_announcements(course_id or None)
        return {"ok": True, "courses": self._course_views()}

    # ------------------------------------------------------------------ files & links
    @safe
    def open_file(self, day_key: str, what: str = "md") -> dict[str, Any]:
        files = self._s.day_files(day_key)
        target = files.get(what) or files.get("folder")
        if not target:
            return {"ok": False, "error": t("File not found.")}
        (reveal if what in ("zip", "pdf_folder") else open_path)(target)
        return {"ok": True}

    @safe
    def open_output(self) -> dict[str, Any]:
        try:
            self._s.output_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            from .net import plain_error

            return {"ok": False, "error": t("The folder for your transcripts ({folder}) can't be created. {error} "
                                            "You can choose another folder in Settings → Files.",
                                            folder=self._s.output_root, error=plain_error(exc))}
        open_path(self._s.output_root)
        return {"ok": True}

    @safe
    def open_course_folder(self, course_id: str) -> dict[str, Any]:
        from .outputs.writer import course_dir

        course = self._s.lib.get_course(course_id)
        folder = course_dir(self._s.output_root, course, self._s.lib.courses(True)) if course else self._s.output_root
        open_path(folder if folder.exists() else self._s.output_root)
        return {"ok": True}

    @safe
    def export_calendar(self) -> dict[str, Any]:
        """The announced quizzes and exams of every course as an .ics file in the output folder, shown in Explorer."""
        from .calendar_export import assessment_events, ics

        events = assessment_events(self._s.lib, self._s.settings)
        if not events:
            return {"ok": False, "error": t("No quiz or exam with a known week has been announced yet.")}
        path = self._s.output_root / "IEUStudia - quizzes and exams.ics"
        path.write_bytes(ics(events).encode("utf-8"))
        reveal(path)
        return {"ok": True, "count": len(events), "path": str(path)}

    @safe
    def open_output_file(self, path: str) -> dict[str, Any]:
        """Open a file IEUStudia wrote in the output folder (nothing outside it)."""
        p = Path(path or "")
        root = self._s.output_root.resolve()
        if not p.is_file() or not p.resolve().is_relative_to(root):
            return {"ok": False, "error": t("File not found.")}
        open_path(p)
        return {"ok": True}

    @safe
    def open_logs(self) -> dict[str, Any]:
        from .paths import logs_dir

        open_path(logs_dir())
        return {"ok": True}

    @safe
    def open_url(self, url: str) -> dict[str, Any]:
        if url.startswith(("https://", "http://")):
            open_in_browser(url)
        return {"ok": True}

    @safe
    def copy_for_ai(self, scope: str, template: str = "notes") -> dict[str, Any]:
        from .outputs.ai_copy import build_ai_text

        if scope.startswith("course:"):
            cid = scope.split(":", 1)[1]
            course = self._s.lib.get_course(cid)
            done = self._s.lib.transcribed_ids()
            recs = [r for r in self._s.lib.recordings(cid) if r.delivery_id in done]
            keys = sorted({r.day_key for r in recs}, key=lambda k: k.split("|")[1])
            days = [([r for r in recs if r.day_key == k], store.load_transcripts([r.delivery_id for r in recs if r.day_key == k]))
                    for k in keys]
        else:
            course, recs = self._s.day(scope)
            days = [(recs, store.load_transcripts([r.delivery_id for r in recs]))]
        text = build_ai_text(template, course, days)
        if not text.strip():
            return {"ok": False, "error": t("Nothing transcribed yet.")}
        from .ai import text_tokens

        ok = set_clipboard(text)
        return {"ok": ok, "chars": len(text), "tokens": text_tokens(text),
                "error": None if ok else t("Could not access the clipboard.")}

    # ------------------------------------------------------------------ export / AI
    @safe
    def export_pack(self, day_key: str) -> dict[str, Any]:
        def run() -> None:
            try:
                self._push({"type": "export", "phase": "start", "day_key": day_key})
                out = self._s.export_pack(day_key)
                self._push({"type": "export", "phase": "done", "day_key": day_key,
                            "files": {k: str(v) for k, v in out.items()}})
                reveal(out.get("pdf") or out["zip"])
            except Exception as exc:  # noqa: BLE001
                log.exception("export failed")
                from .net import plain_error

                self._push({"type": "export", "phase": "error", "day_key": day_key, "message": plain_error(exc)})

        threading.Thread(target=run, name="export", daemon=True).start()
        return {"ok": True}

    @safe
    def ai_estimate(self, day_key: str, include_slides: bool = False) -> dict[str, Any]:
        from .ai import claude_info, estimate_tokens

        text, images, _, _ = self._s.ai_inputs(day_key, include_slides)
        est = estimate_tokens(text, min(len(images), 60))
        return {"ok": True, "estimate": est, "ai": claude_info(), "model": self._s.settings.ai_model}

    @safe
    def ai_notes(self, day_key: str, template: str = "notes", include_slides: bool = False,
                 level: str = "") -> dict[str, Any]:
        from .ai import ai_name
        from .effort import resolve

        if self._ai_cancel is not None:
            return {"ok": False, "error": t("{ai} is already working on something.", ai=ai_name())}
        template = template if template in NOTE_KINDS else "notes"
        cancel = threading.Event()
        self._ai_cancel = cancel
        kind = {"kind": template, "label": t(NOTE_KINDS[template])}
        level = resolve("chat", level, settings=self._s.settings)      # this request's reasoning level, else Settings'

        def run() -> None:
            try:
                self._push({"type": "ai", "phase": "start", "day_key": day_key, "text": "", **kind})
                path = self._s.ai_notes(day_key, template, include_slides, self._s.settings.ai_model, cancel,
                                        lambda so_far: self._push({"type": "ai", "phase": "progress",
                                                                   "day_key": day_key, "text": so_far, **kind}),
                                        effort=level)
                text = path.read_text(encoding="utf-8")
                cards = {}
                if template == "flashcards":                  # to practise them one by one
                    from .outputs.flashcards import parse_cards

                    cards = {"cards": parse_cards(text)}
                self._push({"type": "ai", "phase": "done", "day_key": day_key, "path": str(path), "text": text,
                            **kind, **cards})
            except Cancelled:
                self._push({"type": "ai", "phase": "error", "day_key": day_key, "message": "", "cancelled": True, **kind})
            except Exception as exc:  # noqa: BLE001
                self._push({"type": "ai", "phase": "error", "day_key": day_key, "message": str(exc), **kind})
            finally:
                self._ai_cancel = None

        threading.Thread(target=run, name="claude", daemon=True).start()
        return {"ok": True}

    @safe
    def ai_cancel(self) -> dict[str, Any]:
        if self._ai_cancel is not None:
            self._ai_cancel.set()
        return {"ok": True}

    # ------------------------------------------------------------------ video study plans
    @safe
    def plan_options(self, course_id: str) -> dict[str, Any]:
        """What the new-plan dialog offers: the weeks (with what is downloaded and transcribed) and the assessments
        the lecturer announced, each with the weeks it most likely covers."""
        from .ai import claude_info

        d = views.course_detail(self._s.lib, self._s.settings, course_id)
        if d is None:
            return {"ok": False, "error": t("This course is no longer in your list.")}
        weeks = []
        for w in d["weeks"]:
            mats = [m for u in w["units"] for m in u["materials"]]
            own = [x for x in w["days"] if x.get("mine") is not False]       # your section's class days
            weeks.append({"no": w["no"], "start": w["start"], "end": w["end"], "current": w["current"], "past": w["past"],
                          "topic": w["topic_en"] or w["topic_tr"] or " · ".join(u["title"] for u in w["units"]),
                          "exam": w["exam"], "files": len(mats),
                          "files_ready": sum(1 for m in mats if m["downloaded"] and m["text"] == "done"),
                          "missing": [m["id"] for m in mats if not m["downloaded"]],
                          "lectures": len(own), "lectures_done": sum(1 for x in own if x["any_done"]),
                          "todo_days": [x["day_key"] for x in own if not x["transcribed"]]})
        assess, prev, midterm = [], 0, 0
        for e in sorted((e for e in (d.get("alerts") or {}).get("plan") or [] if e.get("week")), key=lambda e: e["week"]):
            if e["kind"] == "midterm":                  # Midterm 2: since Midterm 1
                start = midterm + 1 if e["no"] >= 2 else 1
                midterm = e["week"]
            else:                                       # a quiz: since the last assessment
                start = 1 if e["kind"] == "final" else prev + 1
            assess.append({**e, "weeks": list(range(min(start, e["week"]), e["week"] + 1))})
            prev = e["week"]
        return {"ok": True, "course": {"id": d["id"], "code": d["code"], "name": d["display_name"]}, "weeks": weeks,
                "assessments": assess, "ai": claude_info(),
                "model": self._s.settings.plan_model or self._s.settings.ai_model}

    @safe
    def plan_estimate(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        from .plans import estimate

        return {"ok": True, "estimate": estimate(self._s.lib, self._s.settings, course_id, scope or {})}

    @safe
    def plan_create(self, course_id: str, scope: dict[str, Any]) -> dict[str, Any]:
        from .ai import claude_info

        info = claude_info()
        if not info["available"]:
            return {"ok": False, "error": t("Set up ChatGPT or Claude in Settings → AI first (needed to build the plan).")}
        if info.get("can_plans") is False:
            return {"ok": False, "error": t("Study plans need ChatGPT or Claude — the offline model is too small for "
                                            "them.")}
        pid = self._s.plans.create(course_id, scope or {})
        self._s.plans.start(pid)
        return {"ok": True, "plan": self._s.lib.plan_brief(pid)}

    @safe
    def plan_list(self, course_id: Optional[str] = None) -> dict[str, Any]:
        return {"ok": True, "plans": self._s.lib.plans(course_id or None)}

    @safe
    def plan_get(self, plan_id: str) -> dict[str, Any]:
        p = self._s.lib.plan(plan_id)
        if p is None:
            return {"ok": False, "error": t("This plan no longer exists.")}
        data = p.pop("data", {}) or {}
        p["concepts"] = (data.get("concepts") or {}).get("concepts", [])
        p["stages_done"] = [s for s in ("gather", "concepts", "search", "inspect", "verify") if s in data]
        kind = (p.get("scope") or {}).get("kind")
        if kind in ("pack", "sheet"):                     # a study pack's chapters (a cheatsheet's topics) so far
            from .studypack import progress_info

            p.update(progress_info(data, kind))
        return {"ok": True, "plan": p}

    @safe
    def plan_resume(self, plan_id: str, from_stage: str = "") -> dict[str, Any]:
        self._s.plans.start(plan_id, from_stage)
        return {"ok": True}

    @safe
    def plan_cancel(self, plan_id: str) -> dict[str, Any]:
        self._s.plans.cancel(plan_id)
        return {"ok": True}

    @safe
    def plan_delete(self, plan_id: str) -> dict[str, Any]:
        self._s.plans.cancel(plan_id)
        self._s.lib.delete_plan(plan_id)
        return {"ok": True}

    @safe
    def plan_mark(self, plan_id: str, key: str, done: bool) -> dict[str, Any]:
        p = self._s.lib.plan_brief(plan_id)
        if p is None:
            return {"ok": False, "error": t("This plan no longer exists.")}
        full = self._s.lib.plan(plan_id) or {}
        marks = dict(full.get("marks") or {})
        if done:
            marks[str(key)] = True
        else:
            marks.pop(str(key), None)
        self._s.lib.update_plan(plan_id, marks=marks)
        return {"ok": True, "marks": marks}

    @safe
    def plan_update(self, plan_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """A new version of a plan (other weeks, difficulties, time…): one consolidated plan that says what was
        added, replaced or is no longer needed. The old version stays until deleted."""
        old = self._s.lib.plan_brief(plan_id)
        if old is None:
            return {"ok": False, "error": t("This plan no longer exists.")}
        scope = {**(old.get("scope") or {}), **(patch or {})}
        pid = self._s.plans.create(old["course_id"], scope, parent_id=plan_id)
        self._s.plans.start(pid)
        return {"ok": True, "plan": self._s.lib.plan_brief(pid)}

    @safe
    def plan_open_file(self, plan_id: str) -> dict[str, Any]:
        p = self._s.lib.plan(plan_id) or {}
        f = (p.get("result") or {}).get("file")
        if not f or not Path(f).is_file():
            return {"ok": False, "error": t("The plan file was not found.")}
        open_path(f)
        return {"ok": True}

    # ------------------------------------------------------------------ YouTube choices for video plans (ytprefs.py)
    @safe
    def yt_prefs(self) -> dict[str, Any]:
        from . import ytprefs

        return {"ok": True, "prefs": ytprefs.load().to_json()}

    @safe
    def yt_set_lang(self, lang: str) -> dict[str, Any]:
        from . import ytprefs

        return {"ok": True, "prefs": ytprefs.set_lang(str(lang or "")).to_json()}

    @safe
    def yt_channel_search(self, query: str) -> dict[str, Any]:
        from . import videos

        try:
            found = videos.search_channels(str(query or ""), 8)
        except videos.VideoError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "channels": [c.to_json() for c in found]}

    @safe
    def yt_channel_set(self, channel: dict[str, Any], which: str = "") -> dict[str, Any]:
        """A channel on the preferred ("allow") or never-shown ("block") list, or off both (""). From a video card
        only its id and name may be known: its picture is looked up (cached)."""
        from . import videos, ytprefs

        ch = {k: v for k, v in dict(channel or {}).items() if v}
        if which and not ch.get("avatar"):
            try:
                found = videos.channel(str(ch.get("id") or ""))
            except videos.VideoError:
                found = None
            if found is not None:
                ch = {**found.to_json(), **ch, "avatar": found.avatar}
        try:
            prefs = ytprefs.set_channel(ch, which or None)
        except ValueError:
            return {"ok": False, "error": t("That isn't a YouTube channel.")}
        return {"ok": True, "prefs": prefs.to_json()}

    # ------------------------------------------------------------------ study packs (studypack.py; built as plans)
    @safe
    def pack_options(self, course_id: str) -> dict[str, Any]:
        from . import studypack
        from .ai import claude_info

        return {"ok": True, **studypack.options(self._s.lib, self._s.settings, course_id), "ai": claude_info(),
                "model": self._s.settings.plan_model or self._s.settings.ai_model}

    @safe
    def pack_doc(self, plan_id: str) -> dict[str, Any]:
        """The study pack's document for its page (the same HTML as its file)."""
        from .studypack import shown_body

        p = self._s.lib.plan(plan_id)
        if p is None:
            return {"ok": False, "error": t("This plan no longer exists.")}
        return {"ok": True, "html": shown_body(self._s, p)}

    @safe
    def pack_pdf(self, plan_id: str, how: str = "open") -> dict[str, Any]:
        """The study pack as a PDF: opened (``how`` "open"), saved where the student picks ("save") or shown in its
        folder ("folder"). It is printed again when it is missing."""
        import shutil

        import webview

        from .studypack import make_pdf

        p = self._s.lib.plan(plan_id)
        if p is None:
            return {"ok": False, "error": t("This plan no longer exists.")}
        pdf = make_pdf(self._s, p)
        if pdf is None:
            return {"ok": False, "error": t("The PDF could not be made: Microsoft Edge was not found.")}
        if how == "folder":
            reveal(pdf)
        elif how == "save":
            to = self._window.create_file_dialog(webview.SAVE_DIALOG, directory=str(Path.home() / "Downloads"),
                                                 save_filename=pdf.name)
            to = to[0] if isinstance(to, (list, tuple)) else to
            if not to:
                return {"ok": True, "saved": ""}
            shutil.copyfile(pdf, to)
            return {"ok": True, "saved": str(to)}
        else:
            open_path(pdf)
        return {"ok": True}

    # ------------------------------------------------------------------ weekly digest
    @safe
    def digest(self, offset: int = 0) -> dict[str, Any]:
        from .digest import weekly_digest

        d = weekly_digest(self._s.lib, self._s.settings, int(offset or 0))
        try:
            saved = json.loads(self._s.lib.get_meta(f"digest:{d['week']['start']}") or "{}")
        except ValueError:
            saved = {}
        return {"ok": True, "digest": d, "summary": saved}

    @safe
    def digest_summary(self, offset: int = 0, level: str = "") -> dict[str, Any]:
        """A short written summary of the week by the chosen AI (light model); saved, and written to the output
        folder. ``level``: its reasoning level (effort.py; "" = the one in Settings)."""
        from .ai import ai_name, claude_info, model_label, one_shot
        from .digest import digest_markdown, digest_prompt, weekly_digest
        from .effort import resolve

        if not claude_info()["available"]:
            return {"ok": False, "error": t("Set up AI in Settings → AI first.")}
        if self._ai_cancel is not None:
            return {"ok": False, "error": t("{ai} is already working on something.", ai=ai_name())}
        cancel = threading.Event()
        self._ai_cancel = cancel
        offset = int(offset or 0)
        model = getattr(self._s.settings, "ai_model_light", "") or "haiku"
        level = resolve("digest", str(level or ""), settings=self._s.settings)

        def run() -> None:
            try:
                d = weekly_digest(self._s.lib, self._s.settings, offset)
                self._push({"type": "digest", "phase": "start", "offset": offset, "text": ""})
                res = one_shot("You write brief, accurate study digests for a university student, in Markdown.",
                               [{"type": "text", "text": digest_prompt(d)}], model, level, cancel,
                               lambda so_far: self._push({"type": "digest", "phase": "progress", "offset": offset,
                                                          "text": so_far}),
                               think=False)          # a short summary of the facts given: no extended thinking
                text = (res.get("text") or "").strip()
                saved = {"text": text, "model": model_label(model, level), "at": dt.datetime.now().isoformat(timespec="seconds"),
                         "usage": res.get("usage") or {}}
                folder = self._s.output_root / "Weekly digests"
                folder.mkdir(parents=True, exist_ok=True)
                path = folder / f"{d['week']['start']} weekly digest.md"
                path.write_bytes(digest_markdown(d, text).encode("utf-8"))
                saved["file"] = str(path)
                self._s.lib.set_meta(f"digest:{d['week']['start']}", json.dumps(saved, ensure_ascii=False))
                self._push({"type": "digest", "phase": "done", "offset": offset, **saved})
            except Exception as exc:  # noqa: BLE001
                self._push({"type": "digest", "phase": "error", "offset": offset,
                            "message": str(exc) or t("{ai} could not write the summary.", ai=ai_name())})
            finally:
                self._ai_cancel = None

        threading.Thread(target=run, name="digest", daemon=True).start()
        return {"ok": True}

    # ------------------------------------------------------------------ chat with Claude
    @safe
    def chat_courses(self) -> dict[str, Any]:
        from .ai import usage_limits

        return {"ok": True, "courses": self._s.chat.courses(), "limits": usage_limits()}

    @safe
    def chat_scope(self, course_id: str) -> dict[str, Any]:
        return {"ok": True, **self._s.chat.scope_options(course_id)}

    @safe
    def chat_estimate(self, course_id: str, day_keys: list[str], focus: str = "full",
                      slides: bool = False, send: Optional[dict[str, Any]] = None, uploads: str = "") -> dict[str, Any]:
        from .chatfiles import draft_target

        keys = self._s.chat.all_days(course_id) if focus == "course" else list(day_keys or [])
        days = self._s.chat.ordered_days(course_id, keys)
        ctx = self._s.chat.context(course_id, days, focus, bool(slides), send, draft_target(str(uploads or "")))
        return {"ok": True, "tokens": ctx["tokens"] if days else 0, "images": len(ctx["images"]),
                "slides_total": ctx["slides_total"], "days": len(days)}

    @safe
    def chat_list(self) -> dict[str, Any]:
        from .ai import usage_limits

        return {"ok": True, "chats": self._s.chat.list(), "limits": usage_limits()}

    @safe
    def chat_get(self, chat_id: str) -> dict[str, Any]:
        return {"ok": True, "chat": self._s.chat.view(chat_id)}

    @safe
    def chat_create(self, course_id: str, day_keys: list[str], focus: str = "full", slides: bool = False,
                    model: Optional[str] = None, level: str = "",
                    send: Optional[dict[str, Any]] = None, uploads: str = "") -> dict[str, Any]:
        chat = self._s.chat.create(course_id, list(day_keys or []), focus, bool(slides),
                                   model or self._s.settings.ai_model, level or "", send=send, uploads=str(uploads or ""))
        return {"ok": True, "chat": chat}

    # "What this chat sees", the student's own files and the viewers (chatinputs.py). ``spec``: {"chat_id"}, or a new
    # chat's {"course_id", "days", "focus", "slides", "send", "uploads": token}
    @safe
    def chat_inputs(self, spec: Any) -> dict[str, Any]:
        from . import chatinputs

        return {"ok": True, **chatinputs.inputs(self._s.chat, spec)}

    @safe
    def chat_upload_part(self, token: str, data_b64: str, first: bool = False) -> dict[str, Any]:
        from . import chatfiles

        return {"ok": True, "size": chatfiles.receive(str(token or ""), str(data_b64 or ""), bool(first))}

    @safe
    def chat_upload(self, spec: Any, token: str, name: str) -> dict[str, Any]:
        from . import chatinputs

        return {"ok": True, **chatinputs.upload(self._s.chat, spec, str(token or ""), str(name or ""))}

    @safe
    def chat_upload_check(self, spec: Any, name: str, size: int) -> dict[str, Any]:
        from . import chatfiles, chatinputs

        target = chatinputs.spec_of(self._s.chat, spec)["target"]
        return {"ok": True, "kind": chatfiles.check(target, str(name or ""), int(size or 0))}

    @safe
    def chat_upload_remove(self, spec: Any, upload_id: str) -> dict[str, Any]:
        from . import chatinputs

        return {"ok": True, **chatinputs.remove_upload(self._s.chat, spec, str(upload_id or ""))}

    @safe
    def chat_file_view(self, spec: Any, ref: str, page: int = 0, zoom: float = 1.0) -> dict[str, Any]:
        from . import chatinputs

        return {"ok": True, **chatinputs.file_view(self._s.chat, spec, str(ref or ""), int(page or 0), float(zoom or 1))}

    @safe
    def chat_slides(self, course_id: str, day_keys: list[str], focus: str = "full", captured: bool = False,
                    send: Optional[dict[str, Any]] = None, uploads: str = "") -> dict[str, Any]:
        """The "send slides" chooser: the course's slide files and what the choice adds to the chat (``uploads``: the
        chat's id or a new chat's ``draft-<token>``, whose files count too)."""
        return {"ok": True, **self._s.chat.slide_options(course_id, list(day_keys or []), focus, bool(captured), send,
                                                         str(uploads or ""))}

    @safe
    def chat_send(self, chat_id: str, text: str) -> dict[str, Any]:
        return {"ok": True, **self._s.chat.send(chat_id, text)}

    @safe
    def chat_stop(self, chat_id: str) -> dict[str, Any]:
        self._s.chat.stop(chat_id)
        return {"ok": True}

    @safe
    def chat_update(self, chat_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "chat": self._s.chat.update(chat_id, patch or {})}

    @safe
    def chat_delete(self, chat_id: str) -> dict[str, Any]:
        self._s.chat.delete(chat_id)
        return {"ok": True}

    @safe
    def chat_export(self, chat_id: str) -> dict[str, Any]:
        path = self._s.chat.export(chat_id)
        reveal(path)
        return {"ok": True, "path": str(path)}

    @safe
    def chat_retry(self, chat_id: str, seq: int) -> dict[str, Any]:
        return {"ok": True, **self._s.chat.retry(chat_id, int(seq))}

    @safe
    def log_client_error(self, info: Any) -> dict[str, Any]:
        """A script error in the UI: written to the log so problems can be traced (works before services start)."""
        d = info if isinstance(info, dict) else {"message": info}
        log.warning("UI error (%s, view %s): %s\n%s", str(d.get("kind", ""))[:20], str(d.get("view", ""))[:20],
                    str(d.get("message", ""))[:500], str(d.get("stack", ""))[:2000])
        return {"ok": True}

    @safe
    def copy_text(self, text: str) -> dict[str, Any]:
        ok = set_clipboard(str(text or ""))
        return {"ok": ok, "error": None if ok else t("Could not access the clipboard.")}

    # ------------------------------------------------------------------ preferences
    @safe
    def set_course_prefs(self, course_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        cur = self._s.settings.prefs(course_id).__dict__.copy()
        cur.update({k: v for k, v in (patch or {}).items() if k in cur})
        self._s.update_settings({"course_prefs": {course_id: cur}})
        return {"ok": True, "courses": self._course_views()}

    @safe
    def set_hidden(self, delivery_id: str, hidden: bool) -> dict[str, Any]:
        self._s.lib.set_hidden(delivery_id, bool(hidden))
        return {"ok": True, "courses": self._course_views()}

    @safe
    def mark_seen(self, course_id: Optional[str] = None) -> dict[str, Any]:
        self._s.lib.clear_new(course_id or None)
        return {"ok": True, "courses": self._course_views()}

    @safe
    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        old_out = self._s.settings.output_dir
        s = self._s.update_settings(patch or {})
        if s.output_dir != old_out:
            threading.Thread(target=self._rewrite_all, args=(old_out,), daemon=True).start()
        if {"model", "device"} & set(patch or {}):         # the GPU chip and Settings follow the choice
            threading.Thread(target=self._system_info, daemon=True).start()
        return {"ok": True, "settings": s.to_json()}

    def _rewrite_all(self, old_out: str) -> None:
        try:
            copied = self._s.relocate_outputs(Path(old_out), self._s.output_root)
        except Exception:  # noqa: BLE001
            log.exception("copying outputs failed")
            copied = 0
        for c in self._s.lib.courses():
            try:
                self._s.rewrite_course(c.id)
            except Exception:  # noqa: BLE001
                log.exception("rewrite failed for %s", c.id)
        self._toast(t("Your transcripts were copied to the new folder (the old folder was left as it was).")
                    if copied else t("New transcripts will be saved in the new folder."), "success")

    @safe
    def pick_folder(self) -> dict[str, Any]:
        import webview

        res = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=self._s.settings.output_dir)
        return {"ok": True, "path": (res[0] if isinstance(res, (list, tuple)) else res) if res else None}

    @safe
    def finish_first_run(self) -> dict[str, Any]:
        self._s.update_settings({"first_run_done": True})
        return {"ok": True}
