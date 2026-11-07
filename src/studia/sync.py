"""Sign-in + fast synchronisation of courses and Panopto recordings."""

from __future__ import annotations

import contextlib
import copy
import dataclasses as dc
import datetime as dt
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterator, Optional

import requests

from . import secrets_store
from .config import Settings
from .domain import Course, Profile
from .i18n import t
from .ieulearn import AccountAction, AuthError, Busy, IEULearn, NeedLogin, SiteChanged, account_action
from .library import Library
from .net import clone_session, export_cookies, import_cookies, make_session, merge_cookies, plain_session
from .panopto import Panopto

log = logging.getLogger("studia")


@dc.dataclass
class SyncResult:
    profile: Optional[Profile]
    courses: list[Course]
    new_ids: list[str]
    errors: list[str]
    seconds: float


class SyncService:
    def __init__(self, library: Library, settings_getter: Callable[[], Settings], log_: Optional[logging.Logger] = None) -> None:
        self.lib = library
        self.settings = settings_getter
        self.log = log_ or log
        self._lock = threading.RLock()
        self._session = make_session()
        self._gen = 0                    # + 1 at each sign-in and sign-out
        import_cookies(self._session, secrets_store.load_cookies())
        self._stored: Optional[list] = export_cookies(self._session)   # what secrets.bin has: not written again
        self.after_sync: Callable[[], Any] = lambda: None   # the app: lectures whose class day changed get their files
        self._login_failed: Optional[tuple[float, BaseException]] = None   # see ensure_login
        self._account_action: Optional[AccountAction] = None
        self._flight: dict[int, tuple[str, str, float]] = {}                # see slow()
        self._flight_lock = threading.Lock()

    # ------------------------------------------------------------------ what a slow sync waits for
    @contextlib.contextmanager
    def _asking(self, service: str, course: str = "") -> Iterator[None]:
        """While a request to ``service`` ("IEULearn", "Panopto") for ``course`` (a course code) waits for its answer."""
        key = object()
        with self._flight_lock:
            self._flight[id(key)] = (service, course, time.monotonic())
        try:
            yield
        finally:
            with self._flight_lock:
                self._flight.pop(id(key), None)

    def _timed(self, service: str, course: str, fn: Callable[..., Any], *args: Any) -> Any:
        with self._asking(service, course):
            return fn(*args)

    def slow(self, after_s: float) -> list[tuple[str, list[str], float]]:
        """What has waited longer than ``after_s`` for an answer, the longest wait first: [("IEULearn", ["EEE 215"],
        42.0)] (no courses: the service itself, as for the sign-in). The page says so while a sync seems to stand
        still."""
        now = time.monotonic()
        out: dict[str, tuple[list[str], float]] = {}
        with self._flight_lock:
            waiting = list(self._flight.values())
        for service, course, since in waiting:
            if now - since >= after_s:
                codes, longest = out.get(service, ([], 0.0))
                if course and course not in codes:
                    codes.append(course)
                out[service] = (codes, max(longest, now - since))
        return sorted(((s, sorted(c), w) for s, (c, w) in out.items()), key=lambda x: -x[2])

    # ------------------------------------------------------------------ session helpers
    def http(self) -> requests.Session:
        return self._session

    def worker_session(self) -> requests.Session:
        """A private session (copy of the cookies) for a background thread."""
        with self._lock:
            s = clone_session(self._session)
            s._studia_gen = self._gen  # type: ignore[attr-defined]
            return s

    def absorb(self, session: requests.Session) -> None:
        """Merge cookies a worker session received (e.g. a refreshed Panopto ticket) and persist them."""
        with self._lock:
            if getattr(session, "_studia_gen", self._gen) != self._gen:
                return          # taken before a sign-out (or another sign-in): it must not sign this PC in again
            merge_cookies(self._session, session)
            self._save_cookies()

    def _save_cookies(self) -> None:
        cookies = export_cookies(self._session)
        if cookies == self._stored:      # nothing changed: no DPAPI round and no rewrite of secrets.bin
            return
        try:
            secrets_store.save_cookies(cookies)
            self._stored = cookies
        except OSError:
            self.log.warning("Could not store session cookies", exc_info=True)

    @property
    def profile(self) -> Optional[Profile]:
        raw = self.lib.get_meta("profile")
        if not raw:
            return None
        try:
            return Profile(**json.loads(raw))
        except (TypeError, ValueError):
            return None

    def _set_profile(self, p: Profile) -> None:
        self.lib.set_meta("profile", json.dumps(dc.asdict(p), ensure_ascii=False))

    def has_credentials(self) -> bool:
        return secrets_store.get_credentials() is not None

    def can_resume(self) -> bool:
        """A stored password or session exists, so the app can try to work without asking to sign in."""
        return self.has_credentials() or bool(secrets_store.load_cookies())

    # ------------------------------------------------------------------ account
    def sign_in(self, username: str, password: str) -> Profile:
        with self._lock:
            s = make_session()
            learn = IEULearn(s)
            learn.login(username, password)
            profile, courses = learn.dashboard()
            profile.username = username.strip()
            secrets_store.set_credentials(username.strip(), password)
            self._session = s
            self._gen += 1
            self._login_failed = self._account_action = None
            self._save_cookies()
            self.lib.upsert_courses(courses)
            self._set_profile(profile)
            self.log.info("Signed in as %s (%d courses)", profile.name or username, len(courses))
            return profile

    def sign_out(self) -> None:
        """Forget the stored password and session. The library (and whose it is) stays, so signing in again
        continues where it was; another person's sign-in gets a profile of its own."""
        with self._lock:
            secrets_store.clear_all()
            self._session = make_session()
            self._stored = None
            self._gen += 1

    @staticmethod
    def check_account(username: str, password: str) -> tuple[Profile, list[Course], list[dict]]:
        """Sign in to an IEULearn account in a session of its own (the current one is not touched): the account's
        identity, its courses and the new session cookies. Raises AuthError for a wrong password."""
        s = make_session()
        learn = IEULearn(s)
        learn.login(username, password)
        profile, courses = learn.dashboard()
        profile.username = username.strip()
        return profile, courses, export_cookies(s)

    LOGIN_RETRY_AFTER_S = 300.0     # an automatic sign-in IEULearn answered without signing in isn't tried sooner

    def allow_sign_in(self) -> None:
        """You asked (Sync): the next automatic sign-in may try at once, although one failed a moment ago."""
        self._login_failed = None

    def ensure_login(self) -> None:
        """Make sure the IEULearn session is valid, signing in again with stored credentials if needed. A refused
        password (or a page IEULearn wants you to finish first) stops it for good, until you sign in again; IEULearn
        answering without a sign-in (busy, maintenance, its words along with a server error) stops it for
        LOGIN_RETRY_AFTER_S: downloads, files and the helper each asking at once can't make a burst of sign-ins (a
        burst could lock the account). A lost connection doesn't: it is tried as soon as it is back."""
        with self._lock:
            learn = IEULearn(self._session)
            if learn.is_logged_in():
                return
            creds = secrets_store.get_credentials()
            if not creds:
                if secrets_store.rejected():
                    if self._account_action is not None:
                        raise copy.copy(self._account_action)
                    why = secrets_store.rejected_for()     # (after a restart: what IEULearn wanted, kept with it)
                    if why:
                        raise account_action(why)
                    raise NeedLogin(t("IEULearn didn't accept your saved password (was it changed?). Sign in again."))
                raise NeedLogin(t("Please sign in to IEULearn."))
            failed = self._login_failed
            if failed is not None and time.monotonic() - failed[0] < self.LOGIN_RETRY_AFTER_S:
                self.log.info("Not signing in again yet (IEULearn: %s)", failed[1])
                raise copy.copy(failed[1])
            self.log.info("IEULearn session expired; signing in again")
            try:
                with self._asking("IEULearn"):
                    learn.login(*creds)
            except AuthError as exc:       # the password was changed: never try it again in the background
                if not exc.server_error:   # (IEULearn's words along with a server error may be about the server)
                    secrets_store.mark_rejected(creds[1], exc.why if isinstance(exc, AccountAction) else "")
                    self._account_action = exc if isinstance(exc, AccountAction) else None
                else:
                    self._login_failed = (time.monotonic(), exc)
                raise
            except (Busy, SiteChanged) as exc:
                self._login_failed = (time.monotonic(), exc)
                raise
            self._login_failed = None
            self._save_cookies()

    def ensure_panopto(self, course_id: Optional[str] = None) -> None:
        """Panopto signed in. With ``course_id`` (Panopto refused one of that course's recordings) the course is
        opened through IEULearn again even while the sign-in works: the access a launch gives a course runs out
        before the sign-in cookie does (see _reopen_empty)."""
        with self._lock:
            if course_id is None and Panopto(self._session).is_authenticated():
                return
            self.ensure_login()
            cid = course_id or next((c.id for c in self.lib.courses() if c.has_panopto), None)
            if cid is None:
                raise RuntimeError(t("None of your courses has Panopto."))
            known = self.lib.get_course(cid)
            try:
                with self._asking("Panopto", known.code if known else ""):
                    folder = IEULearn(self._session).launch_panopto(cid)
            except RuntimeError as exc:        # (its own words name the course by IEULearn's number: by its code here)
                self.log.warning("%s", exc)
                raise RuntimeError(t("Panopto didn't open {course} through IEULearn, so the lecture couldn't be "
                                     "downloaded. Try again in a few minutes; if it keeps happening, open the course's "
                                     "Panopto page on learn.ieu.edu.tr in your browser once.",
                                     course=(known.code or known.name) if known else cid)) from exc
            if not Panopto(self._session).is_authenticated():
                raise RuntimeError(t("Panopto didn't open through IEULearn, so the lecture couldn't be downloaded. Try "
                                     "again in a few minutes; if it keeps happening, open a course's Panopto page on "
                                     "learn.ieu.edu.tr in your browser once."))
            if folder and known is not None and folder != known.folder_id:     # the course moved to another folder
                self.lib.set_course_folder(cid, folder, True)
            if folder:
                self._launched(cid)
            self._save_cookies()

    # ------------------------------------------------------------------ sync
    def _dashboard(self) -> tuple[Profile, list[Course]]:
        try:
            return IEULearn(self._session).dashboard()
        except NeedLogin:
            self.ensure_login()   # only ever called on the thread that holds the lock
            return IEULearn(self._session).dashboard()

    def _launch(self, course_id: str, s: requests.Session) -> tuple[str, Optional[str], requests.Session]:
        known = self.lib.get_course(course_id)
        try:
            with self._asking("Panopto", known.code if known else ""):
                return course_id, IEULearn(s).launch_panopto(course_id), s
        except RuntimeError as exc:  # transient: try again on the next sync, keep has_panopto
            self.log.warning("%s", exc)
            return course_id, "", s

    def _panopto_open(self) -> bool:
        try:
            with self._asking("Panopto"):
                return Panopto(self._session).is_authenticated()
        except (requests.RequestException, OSError):
            return False

    def _merge(self, course_id: str, recs: Optional[list], heads: dict) -> list[str]:
        """A course's first listing is what was there already (not new). Its folder may have been empty then (the
        term had not started): a recording that started after that first listing is new. ``recs`` None: its folders
        were probed and nothing changed. ``heads``: what its folders' first rows say, for the next probe."""
        if recs is None:
            return []
        first = not self.lib.has_recordings(course_id)
        key = f"listed:{course_id}"
        since = self.lib.get_meta(key) or ""
        if first and not since:
            self.lib.set_meta(key, dt.datetime.now().isoformat(timespec="seconds"))
        new = self.lib.merge_recordings(course_id, recs, first_sync=first, new_after=since if first else "")
        if heads != self._heads(course_id, empty_too=True):
            self.lib.set_meta(f"folders:{course_id}", json.dumps(heads))
        self.lib.set_meta(f"listed_full:{course_id}", dt.datetime.now().isoformat(timespec="seconds"))
        return new

    def _launched(self, course_id: str) -> None:
        """IEULearn opened the course's Panopto folder now, or was asked to for a course with no recordings yet (see
        _reopen_empty)."""
        self.lib.set_meta(f"launched:{course_id}", dt.datetime.now().isoformat(timespec="seconds"))

    def _reopen_empty(self, courses: list[Course], listed: dict[str, Optional[list]], failed: list[str],
                      progress: Callable[[str], None]) -> list[str]:
        """Courses whose folder came back empty: Panopto shows a course's folder only for a while after IEULearn
        opened it for this account, and the sign-in cookie outlives that (the listing then says 0 recordings, without
        an error). Each is opened again through IEULearn and listed once more: one that had recordings each time, one
        that never had any (a new course, no lecture yet) at most every REOPEN_EMPTY_EVERY. New ids."""
        empty = [c for c in courses if listed.get(c.id) == [] and (
            self.lib.has_recordings(c.id)
            or self._stale(self.lib.get_meta(f"launched:{c.id}") or "", self.REOPEN_EMPTY_EVERY))]
        if not empty:
            return []
        progress(t("Connecting to Panopto…"))
        new_ids: list[str] = []
        sessions = [clone_session(self._session) for _ in empty]
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="lti") as pool:
            launched = list(pool.map(self._launch, [c.id for c in empty], sessions))
        for c, (cid, folder, s) in zip(empty, launched):
            merge_cookies(self._session, s)
            had = self.lib.has_recordings(cid)
            if not had:                                 # tried: a failing course isn't opened again at every sync
                self._launched(cid)
            if folder is None and not had:              # its page has no Panopto any more (as for a new course)
                self.lib.set_course_folder(cid, None, False)
                self.lib.set_meta(f"nofolder:{cid}", dt.datetime.now().isoformat(timespec="seconds"))
                continue
            if not folder:                              # IEULearn didn't open it: said, and tried again next time
                self.log.warning("Panopto listed no recordings for %s and IEULearn didn't open it again",
                                 c.code or c.id)
                failed.append(c.code or c.name or c.id)
                continue
            self._launched(cid)
            if folder != c.folder_id:                   # the course moved to another folder
                self.lib.set_course_folder(cid, folder, True)
                c = dc.replace(c, folder_id=folder)
            try:
                course, recs, s2, got = self._list(c, s)
            except Exception as exc:  # noqa: BLE001 - one course failing must not stop the rest
                self.log.warning("Listing the recordings of %s failed: %s", c.code or c.id, exc)
                failed.append(c.code or c.name or c.id)
                continue
            merge_cookies(self._session, s2)
            self.log.info("Panopto listed no recordings for %s; opened through IEULearn again: %d", c.code or c.id,
                          len(recs or []))
            listed[course.id] = recs
            new_ids += self._merge(course.id, recs, got)
        self._save_cookies()
        return new_ids

    def _heads(self, course_id: str, empty_too: bool = False) -> Optional[dict]:
        """What the course's folders' first rows said at its last full listing (None: never listed in full). Folders
        that were all empty give None too (``empty_too``: as stored): a probe can't tell an empty folder from one whose
        access ran out, so such a course is listed in full and opened again if need be (see _reopen_empty)."""
        try:
            heads = json.loads(self.lib.get_meta(f"folders:{course_id}") or "null") or None
        except ValueError:
            return None
        if not empty_too and isinstance(heads, dict) and all(
                isinstance(v, list) and v and not v[0] for v in heads.values()):
            return None
        return heads

    @staticmethod
    def _list(course: Course, s: requests.Session, heads: Optional[dict] = None
              ) -> tuple[Course, Optional[list], requests.Session, dict]:
        """A course's recordings and what its folders' first rows say. With ``heads`` (from the last full listing)
        the folders are probed first, one row each, and listed only if a count, a newest recording or a subfolder
        changed: None for the recordings when nothing did."""
        p = Panopto(s)
        if heads is not None:
            now = p.probe(course.folder_id or "")
            if now == heads:
                return course, None, s, now
        got: dict = {}
        return course, p.list_recordings(course.id, course.folder_id or "", got), s, got

    @staticmethod
    def _course_list(s: requests.Session, profile: Profile, stored: dict[str, Course]) -> tuple[Profile, list[Course]]:
        """The tray helper's courses: IEULearn's small "My courses" list (~2 KB on the wire instead of the dashboard's
        ~80 KB), with the profile and a known course's term and progress as the app's last sync stored them. The
        dashboard when the list doesn't come back whole. An expired session raises NeedLogin, as the dashboard does."""
        learn = IEULearn(s)
        courses = learn.course_list()
        if courses is None:
            return learn.dashboard()
        for c in courses:
            old = stored.get(c.id)
            if old:
                c.instructor, c.term, c.progress = c.instructor or old.instructor, c.term or old.term, old.progress
                c.cover_url = c.cover_url or old.cover_url
        return dc.replace(profile), courses

    def sync(self, progress: Callable[[str], None] = lambda m: None,
             partial: Callable[[list[str]], None] = lambda ids: None,
             full_every: Optional[dt.timedelta] = None, dashboard: bool = True) -> SyncResult:
        """``full_every`` (the tray helper): each course's Panopto folders are listed in full at least this often; in
        between only when their probe shows a change, so a renamed, moved or deleted older recording (or one
        published with an older date while another is deleted) may wait until then; nothing is marked gone from a
        probe. A course with a recording still being recorded (started in the last LIVE_LISTED) is listed in full
        each time, so its end is noticed. Each course keeps its own clock: one that keeps failing doesn't stop the
        others' probes. None (the app): every folder in full, each time. ``dashboard`` False (the tray helper): the
        courses come from the small "My courses" list and the profile stays as stored (see _course_list); an expired
        session is noticed the same way and signs in again with the dashboard. The app always loads the dashboard.
        ``partial``: the new ids found so far, before the dashboard arrives; also when the sync then fails (IEULearn
        down, a changed password), so what was merged before is still told about."""
        found: list[str] = []
        try:
            return self._sync(progress, partial, full_every, dashboard, found)
        except BaseException:
            if found:
                partial(list(found))
            raise

    def _sync(self, progress: Callable[[str], None], partial: Callable[[list[str]], None],
              full_every: Optional[dt.timedelta], dashboard: bool, new_ids: list[str]) -> SyncResult:
        t0 = time.monotonic()
        errors: list[str] = []
        with self._lock:
            progress(t("Checking IEULearn and Panopto…"))
            listed: dict[str, Optional[list]] = {}
            probing = full_every is not None
            live = self.lib.live_courses((dt.datetime.now() - self.LIVE_LISTED).isoformat()) if probing else set()

            def heads(c: Course) -> Optional[dict]:          # probes only for a course listed in full lately
                if (not probing or c.id in live
                        or self._stale(self.lib.get_meta(f"listed_full:{c.id}") or "", full_every)):
                    return None
                return self._heads(c.id)
            stored_profile = None if dashboard else self.profile      # (none stored yet: the dashboard has it)
            with ThreadPoolExecutor(max_workers=8, thread_name_prefix="sync") as pool:
                probe_session = clone_session(self._session)
                dash_session = clone_session(self._session)
                if stored_profile is None:
                    f_dash = pool.submit(self._timed, "IEULearn", "", lambda: IEULearn(dash_session).dashboard())
                else:
                    f_dash = pool.submit(self._timed, "IEULearn", "", self._course_list, dash_session, stored_profile,
                                         {c.id: c for c in self.lib.courses(include_inactive=True)})
                panopto_down = False
                try:
                    with self._asking("Panopto"):
                        panopto_ok = Panopto(probe_session).is_authenticated()
                except (requests.RequestException, OSError) as exc:   # IEULearn's own content still syncs
                    self.log.warning("Panopto can't be reached: %s", exc)
                    panopto_ok, panopto_down = False, True
                early = []
                if panopto_ok:  # fast path: list known folders while the dashboard loads
                    early = [pool.submit(self._timed, "Panopto", c.code, self._list, c, clone_session(probe_session),
                                         heads(c)) for c in self.lib.courses() if c.folder_id]
                for n, f in enumerate(early, 1):
                    try:
                        course, recs, s, got = f.result()
                        merge_cookies(self._session, s)
                        listed[course.id] = recs
                        new_ids += self._merge(course.id, recs, got)
                    except Exception as exc:  # noqa: BLE001 - retried below
                        self.log.info("Early listing failed (%s); retrying", exc)
                    progress(t("Looking for new recordings: {done} of {total} courses…", done=n, total=len(early)))
                if listed:
                    partial(list(new_ids))   # the UI can show new recordings before the dashboard arrives
                try:
                    profile, courses = f_dash.result()      # NetworkError propagates
                    merge_cookies(self._session, dash_session)
                except NeedLogin:
                    profile, courses = self._dashboard()    # signs in again with stored credentials
            old = self.profile
            if old and old.username and not profile.username:
                profile.username = old.username
            if old and not profile.name:          # a page that came back without the name: keep who this is
                profile.name, profile.student_number = old.name, profile.student_number or old.student_number
            self._set_profile(profile)
            self.lib.upsert_courses(courses)
            known = {c.id: c for c in self.lib.courses()}

            # courses whose Panopto folder we don't know yet (first sync / new course)
            todo = []
            for c in known.values():
                if c.folder_id:
                    continue
                last = self.lib.get_meta(f"nofolder:{c.id}")
                if not c.has_panopto and last and (dt.datetime.now() - dt.datetime.fromisoformat(last)).days < 1:
                    continue
                todo.append(c.id)
            if panopto_down:
                errors.append(t("Panopto isn't answering right now: new recordings are checked again next time."))
            elif todo or not panopto_ok:
                progress(t("Connecting to Panopto…"))      # (the dashboard or course list showed the sign-in works)
                launch_ids = todo or [next((c.id for c in known.values() if c.folder_id), None)]
                launch_ids = [i for i in launch_ids if i]
                sessions = [clone_session(self._session) for _ in launch_ids]  # cloned here: workers must not take the lock
                unopened: list[str] = []                   # courses of todo whose launch ran but gave no folder
                with ThreadPoolExecutor(max_workers=4, thread_name_prefix="lti") as pool:
                    for n, (cid, folder, s) in enumerate(pool.map(self._launch, launch_ids, sessions), 1):
                        if len(launch_ids) > 1:
                            progress(t("Connecting to Panopto: {done} of {total} courses…", done=n,
                                       total=len(launch_ids)))
                        merge_cookies(self._session, s)
                        if folder:
                            self.lib.set_course_folder(cid, folder, True)
                            self._launched(cid)
                        elif folder is None and cid in todo:   # "" = launch failed, retry next time
                            self.lib.set_course_folder(cid, None, False)
                            self.lib.set_meta(f"nofolder:{cid}", dt.datetime.now().isoformat(timespec="seconds"))
                        elif cid in todo:
                            unopened.append(cid)
                self._save_cookies()
                some = any(c.folder_id for c in self.lib.courses())
                if some and not self._panopto_open():
                    panopto_down = True
                    errors.append(t("Panopto didn't open through IEULearn: new recordings are checked again next time."))
                elif unopened and not some:                # a new library where not one course opened: never "Up to date"
                    errors.append(t("Panopto didn't open through IEULearn for any of your courses, so no recordings "
                                    "could be looked for. Open the Panopto page of one of your courses on "
                                    "learn.ieu.edu.tr in your browser once (it may ask you to confirm something), "
                                    "then sync again."))
                elif unopened:
                    errors.append(t("Panopto didn't open through IEULearn for {courses}: their recordings are looked "
                                    "for again at the next sync.", courses=", ".join(sorted(
                                        known[c].code or known[c].name or c for c in unopened))))

            progress(t("Looking for new recordings…"))
            with_folders = [c for c in self.lib.courses() if c.folder_id] if not panopto_down else []
            missing = [c for c in with_folders if c.id not in listed]
            failed: list[str] = []
            with ThreadPoolExecutor(max_workers=6, thread_name_prefix="list") as pool:
                futures = {pool.submit(self._timed, "Panopto", c.code, self._list, c, clone_session(self._session),
                                       heads(c)): c for c in missing}
                for n, (f, c) in enumerate(futures.items(), 1):
                    try:
                        course, recs, s, got = f.result()
                    except Exception as exc:  # noqa: BLE001 - one course failing must not stop the rest
                        self.log.warning("Listing the recordings of %s failed: %s", c.code or c.id, exc)
                        failed.append(c.code or c.name or c.id)
                        continue
                    finally:
                        progress(t("Looking for new recordings: {done} of {total} courses…", done=n,
                                   total=len(futures)))
                    merge_cookies(self._session, s)
                    listed[course.id] = recs
                    new_ids += self._merge(course.id, recs, got)
            new_ids += self._reopen_empty(with_folders, listed, failed, progress)
            if failed:
                errors.append(t("Couldn't check new recordings for {courses}: Panopto didn't answer. They're checked "
                                "again next time.", courses=", ".join(sorted(set(failed)))))
            self._save_cookies()
            self.lib.set_meta("last_sync", dt.datetime.now().isoformat(timespec="seconds"))
            secs = time.monotonic() - t0
            self.log.info("Sync done in %.1fs: %d courses, %d new recordings (%s)", secs, len(courses), len(new_ids),
                          f"{sum(r is not None for r in listed.values())} of {len(listed)} listed after a probe"
                          if probing else "full listing")
            res = SyncResult(self.profile, self.lib.courses(), new_ids, errors, secs)
        try:
            self.after_sync()
        except Exception:  # noqa: BLE001 - the sync itself worked
            self.log.warning("After the sync: failed", exc_info=True)
        return res

    # ------------------------------------------------------------------ course content
    OVERVIEW_EVERY = dt.timedelta(hours=20)       # outcomes, weekly topics, grading: rarely change
    SYLLABUS_EVERY = dt.timedelta(days=7)
    HELPER_UNITS_EVERY = dt.timedelta(hours=6)    # the tray helper, when it doesn't tell about new files
    HELPER_PANOPTO_EVERY = dt.timedelta(hours=6)  # the tray helper's full Panopto listing (probes in between)
    LIVE_LISTED = dt.timedelta(hours=12)          # (one "being recorded" for longer is stuck: the 6-hourly listing)
    REOPEN_EMPTY_EVERY = dt.timedelta(hours=4)    # a course with no recordings yet whose folder lists empty is opened
                                                  # through IEULearn again at most this often

    @staticmethod
    def _stale(stamp: str, every: dt.timedelta) -> bool:
        try:
            return dt.datetime.now() - dt.datetime.fromisoformat(stamp) > every
        except (TypeError, ValueError):
            return True

    def _content(self, course: Course, s: requests.Session, info: dict, units: bool = True) -> dict:
        from .coursesite import CourseSite

        site = CourseSite(s)
        out: dict = {"course": course, "session": s}
        if units:
            out["units"] = site.unit_plan(course.id, course.title)
        out["announcements"] = site.announcements(course.id)
        if self._stale(info.get("overview_at", ""), self.OVERVIEW_EVERY):
            out["overview"] = site.overview(course.id)
        return out

    def sync_content(self, progress: Callable[[str], None] = lambda m: None,
                     units_every: Optional[dt.timedelta] = None) -> dict:
        """Weekly units, materials and announcements of every course (each sync, ~100 KB per course), the course
        overview (daily) and the public English syllabus (weekly). Nothing is downloaded: materials are listed.
        ``units_every``: the weekly units and materials at most this often (a new course's always); None: each time."""
        from .coursesite import cover_file, fetch_cover
        from .syllabus import fetch_syllabus

        t0 = time.monotonic()
        courses = [c for c in self.lib.courses() if c.code]
        infos = {c.id: self.lib.course_info(c.id) for c in courses}
        units = units_every is None or self._stale(self.lib.get_meta("units_at") or "", units_every)
        with self._lock:
            sessions = [clone_session(self._session) for _ in courses]   # cloned here: workers must not take the lock
        new_materials: list[str] = []
        new_announcements: list[str] = []
        failures: dict[str, list[str]] = {}       # what went wrong (in words) -> the courses it went wrong for
        relogin: list[Course] = []                # IEULearn's sign-in page came back: the session ran out halfway

        def want(c: Course) -> bool:
            return units or not infos[c.id].get("content_at")

        def take(res: dict) -> None:
            nonlocal new_materials, new_announcements
            c = res["course"]
            now = dt.datetime.now().isoformat(timespec="seconds")
            first = not infos[c.id].get("content_at")
            if "units" in res:
                new_materials += self.lib.merge_units(c.id, res["units"], first_sync=first)
            new_announcements += self.lib.merge_announcements(c.id, res["announcements"], first_sync=first)
            fields = {"content_at": now}
            if "overview" in res:
                fields.update(overview=res["overview"].to_json(), overview_at=now)
            self.lib.set_course_info(c.id, **fields)
            with self._lock:
                merge_cookies(self._session, res["session"])

        progress(t("Loading course content…"))
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="content") as pool:
            futures = {pool.submit(self._timed, "IEULearn", c.code, self._content, c, s, infos[c.id], want(c)): c
                       for c, s in zip(courses, sessions)}
            plain = plain_session()
            stale = [c for c in courses if self._stale(infos[c.id].get("syllabus_at", ""), self.SYLLABUS_EVERY)]
            by_code = {code: pool.submit(fetch_syllabus, code, session=plain)       # sections share one syllabus
                       for code in dict.fromkeys(c.code for c in stale)}
            syl = {c.id: by_code[c.code] for c in stale}
            for n, (f, c) in enumerate(futures.items(), 1):
                try:
                    take(f.result())
                except NeedLogin:
                    relogin.append(c)
                except Exception as exc:  # noqa: BLE001 - one course failing must not stop the rest
                    self._content_failed(c, exc, failures)
                progress(t("Course files and announcements: {done} of {total} courses…", done=n, total=len(courses)))
            if relogin:                           # signed in again once (it raises when it can't), then read again
                self.log.info("IEULearn's session ran out while reading %d courses; signing in again", len(relogin))
                self.ensure_login()
                with self._lock:
                    again = [clone_session(self._session) for _ in relogin]
                for c, s in zip(relogin, again):
                    try:
                        take(self._timed("IEULearn", c.code, self._content, c, s, infos[c.id], want(c)))
                    except NeedLogin:
                        failures.setdefault(t("IEULearn asked to sign in again for its pages although IEUStudia had "
                                              "just signed in."), []).append(c.code)
                    except Exception as exc:  # noqa: BLE001
                        self._content_failed(c, exc, failures)
            if units and courses and not failures:
                self.lib.set_meta("units_at", dt.datetime.now().isoformat(timespec="seconds"))
            for cid, f in syl.items():
                try:
                    s = f.result()
                except Exception as exc:  # noqa: BLE001 - the public site may be down; try again next time
                    self.log.info("Syllabus failed for %s: %s", cid, exc)
                    continue
                if not s and infos[cid].get("syllabus"):
                    # "not found" may also be a department site that didn't answer: keep the one we have and look
                    # again tomorrow instead of in a week
                    retry = dt.datetime.now() - self.SYLLABUS_EVERY + dt.timedelta(days=1)
                    self.lib.set_course_info(cid, syllabus_at=retry.isoformat(timespec="seconds"))
                    continue
                self.lib.set_course_info(cid, syllabus=s.to_json() if s else {},
                                         syllabus_at=dt.datetime.now().isoformat(timespec="seconds"))
            for f in [pool.submit(fetch_cover, c.cover_url) for c in courses
                      if c.cover_url and not cover_file(c.cover_url).is_file()]:
                try:
                    f.result()
                except Exception as exc:  # noqa: BLE001 - only a picture; the Home page has a fallback
                    self.log.info("Cover picture failed: %s", exc)
        self.log.info("Course content in %.1fs: %d new materials, %d new announcements", time.monotonic() - t0,
                      len(new_materials), len(new_announcements))
        errors = [t("{courses}: {problem}", courses=", ".join(sorted(set(codes))), problem=problem)
                  for problem, codes in failures.items()]
        return {"new_materials": new_materials, "new_announcements": new_announcements, "errors": errors}

    def _content_failed(self, course: Course, exc: BaseException, failures: dict[str, list[str]]) -> None:
        self.log.warning("Course content of %s failed: %s", course.code or course.id, exc)
        failures.setdefault(self._content_problem(exc), []).append(course.code or course.name or course.id)

    @staticmethod
    def _content_problem(exc: BaseException) -> str:
        """Why a course's pages couldn't be read, in words for anyone (never "429 Client Error ... for url")."""
        from .coursesite import CourseClosed
        from .net import NetworkError, plain_error

        if isinstance(exc, (CourseClosed, NetworkError)):
            return str(exc)
        if isinstance(exc, requests.HTTPError) and exc.response is not None:
            code = exc.response.status_code
            if code in (404, 410):
                return t("IEULearn didn't find the course's pages.")
            if code in (401, 403):
                return t("IEULearn refused the course's pages: you may no longer have access to the course.")
            return plain_error(exc)
        if isinstance(exc, (requests.RequestException, OSError)):
            return plain_error(exc)
        return t("IEUStudia couldn't read the course's pages (IEULearn's pages may have changed). If it stays like "
                 "this, check for an IEUStudia update in Settings → About.")


__all__ = ["SyncService", "SyncResult", "AuthError", "NeedLogin"]
