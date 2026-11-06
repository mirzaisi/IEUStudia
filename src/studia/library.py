"""Local library (SQLite): courses, recordings, transcript index and full-text search."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .domain import Course, Recording, Transcript

log = logging.getLogger("studia")
SCHEMA_VERSION = 1
_NO_SOUND = re.compile(r"no sound|no speech", re.I)     # a "no sound" failure from before its warning (catchup's too)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS courses(
  id TEXT PRIMARY KEY, title TEXT, code TEXT, name TEXT, instructor TEXT, term TEXT, status TEXT,
  section TEXT, folder_id TEXT, has_panopto INTEGER DEFAULT 1, position INTEGER DEFAULT 0, active INTEGER DEFAULT 1,
  cover_url TEXT DEFAULT '', progress INTEGER);
CREATE TABLE IF NOT EXISTS recordings(
  delivery_id TEXT PRIMARY KEY, course_id TEXT, folder_id TEXT, folder_path TEXT, name TEXT, start TEXT,
  duration_s REAL, viewer_url TEXT, session_id TEXT, kind TEXT, section_tag TEXT, is_new INTEGER DEFAULT 0,
  duplicate_of TEXT, hidden INTEGER DEFAULT 0, status TEXT DEFAULT 'available', error TEXT DEFAULT '',
  first_seen TEXT, last_seen TEXT, gone INTEGER DEFAULT 0, warnings TEXT DEFAULT '[]', live INTEGER DEFAULT 0,
  uploaded TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_rec_course ON recordings(course_id);
CREATE TABLE IF NOT EXISTS transcripts(
  delivery_id TEXT PRIMARY KEY, created_at TEXT, model TEXT, engine_version TEXT, json_path TEXT,
  language_seconds TEXT, duration_s REAL, speech_s REAL, quality TEXT DEFAULT '{}', slides INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS chats(
  id TEXT PRIMARY KEY, course_id TEXT, title TEXT DEFAULT '', scope TEXT DEFAULT '{}', model TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS chat_messages(
  chat_id TEXT, seq INTEGER, role TEXT, text TEXT DEFAULT '', status TEXT DEFAULT 'done',
  usage TEXT DEFAULT '{}', created_at TEXT, PRIMARY KEY(chat_id, seq));
CREATE TABLE IF NOT EXISTS course_info(
  course_id TEXT PRIMARY KEY, overview TEXT DEFAULT '{}', syllabus TEXT DEFAULT '{}',
  overview_at TEXT DEFAULT '', syllabus_at TEXT DEFAULT '', content_at TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS units(
  course_id TEXT, unit_id TEXT, position INTEGER, title TEXT, start_date TEXT DEFAULT '', end_date TEXT DEFAULT '',
  readings TEXT DEFAULT '', PRIMARY KEY(course_id, unit_id));
CREATE TABLE IF NOT EXISTS materials(
  resource_id TEXT PRIMARY KEY, course_id TEXT, unit_id TEXT, position INTEGER DEFAULT 0, title TEXT,
  file_name TEXT DEFAULT '', kind TEXT DEFAULT '', ext TEXT DEFAULT '', url TEXT DEFAULT '', download_url TEXT DEFAULT '',
  local_path TEXT DEFAULT '', size INTEGER DEFAULT 0, downloaded_at TEXT DEFAULT '', downloaded_url TEXT DEFAULT '',
  pages INTEGER DEFAULT 0, text_status TEXT DEFAULT '', first_seen TEXT, last_seen TEXT, is_new INTEGER DEFAULT 0,
  gone INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_mat_course ON materials(course_id);
CREATE TABLE IF NOT EXISTS announcements(
  id TEXT PRIMARY KEY, course_id TEXT, title TEXT, author TEXT, posted_at TEXT, body TEXT, first_seen TEXT,
  is_new INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS plans(
  id TEXT PRIMARY KEY, course_id TEXT, title TEXT DEFAULT '', scope TEXT DEFAULT '{}', status TEXT DEFAULT 'new',
  stage TEXT DEFAULT '', progress REAL DEFAULT 0, message TEXT DEFAULT '', data TEXT DEFAULT '{}',
  result TEXT DEFAULT '{}', marks TEXT DEFAULT '{}', usage TEXT DEFAULT '{}', error TEXT DEFAULT '',
  parent_id TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_plans_course ON plans(course_id);
CREATE TABLE IF NOT EXISTS marks(
  delivery_id TEXT, t REAL, text TEXT DEFAULT '', note TEXT DEFAULT '', created_at TEXT, PRIMARY KEY(delivery_id, t));
"""
_REC_COLS = ["delivery_id", "course_id", "folder_id", "folder_path", "name", "start", "duration_s", "viewer_url",
             "session_id", "kind", "section_tag", "is_new", "duplicate_of", "hidden", "status", "error", "first_seen",
             "live", "uploaded"]
DAY_MOVES_KEY = "day_moves"            # meta: transcribed lectures whose class day changed, files not moved yet
_COURSE_COLS = ["id", "title", "code", "name", "instructor", "term", "status", "section", "folder_id",
                "has_panopto", "position", "cover_url", "progress"]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _fts_word(word: str, prefix: bool = False) -> str:
    """A query word for FTS5 however its i's were written: the tokenizer folds I and İ to i but keeps ı, so "SINAV"
    is indexed as "sinav" and "sınav" as "sınav". Every spelling of (up to four of) its i's is tried."""
    base = word.replace("İ", "i").replace("I", "i").lower().replace("ı", "i")
    spots = [k for k, ch in enumerate(base) if ch == "i"][:4]
    forms = []
    for mask in range(1 << len(spots)):
        chars = list(base)
        for bit, k in enumerate(spots):
            if mask >> bit & 1:
                chars[k] = "ı"
        forms.append('"' + "".join(chars) + '"' + ("*" if prefix else ""))
    return forms[0] if len(forms) == 1 else "(" + " OR ".join(forms) + ")"


def _fts_query(words: list[str]) -> str:
    """All the words, the last one as a prefix (it may still be being typed)."""
    return " AND ".join(_fts_word(w, i == len(words) - 1) for i, w in enumerate(words))


def _search_order(words: list[str]) -> str:
    """Best match first; newest first while every word has at most 2 letters (still being typed: "th" matches most
    of the index, and ranking every match takes long)."""
    return "rowid DESC" if all(len(w) <= 2 for w in words) else "rank"


class Library:
    def __init__(self, path: Path, app_version: str = "") -> None:
        """``app_version``: the app opening it; when that is a new version, a copy of the library as the previous
        version left it is kept first (see _backup_before_upgrade)."""
        self.path = Path(path)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            if app_version:
                self._backup_before_upgrade(app_version)
            self.db.executescript(_SCHEMA)
            self._add_columns()
            self.fts = self._init_fts()
            self.db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
            if app_version:
                self.db.execute("INSERT INTO meta(key, value) VALUES('app_version', ?) ON CONFLICT(key) DO UPDATE "
                                "SET value=excluded.value WHERE value IS NOT excluded.value", (app_version,))
            self.db.commit()

    def _backup_before_upgrade(self, app_version: str, keep: int = 3) -> None:
        """The first time a new version opens the library: a copy of it in ``backups\\library-<old version>.db``
        (the newest ``keep`` copies stay), so a mistake in a new version can never cost what is stored."""
        try:
            row = self.db.execute("SELECT value FROM meta WHERE key='app_version'").fetchone()
            has_data = self.db.execute("SELECT 1 FROM courses LIMIT 1").fetchone() is not None
        except sqlite3.OperationalError:          # a new library: nothing to keep
            return
        old = row[0] if row else ""
        if old == app_version or not has_data:
            return
        folder = self.path.parent / "backups"
        try:
            folder.mkdir(exist_ok=True)
            dest = folder / f"library-{old or 'before-' + app_version}.db"
            copy = sqlite3.connect(str(dest))
            try:
                self.db.backup(copy)
            finally:
                copy.close()
            for extra in sorted(folder.glob("library-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)[keep:]:
                extra.unlink(missing_ok=True)
            log.info("Library copied to %s before version %s opens it", dest, app_version)
        except (OSError, sqlite3.Error) as exc:
            log.warning("Could not keep a copy of the library before the upgrade: %s", exc)

    def _add_columns(self) -> None:
        """Columns added after a table first shipped (CREATE TABLE IF NOT EXISTS leaves old tables as they were)."""
        for table, col, decl in (("courses", "cover_url", "TEXT DEFAULT ''"), ("courses", "progress", "INTEGER"),
                                 ("recordings", "live", "INTEGER DEFAULT 0"),
                                 ("recordings", "uploaded", "TEXT DEFAULT ''")):
            if col not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def _init_fts(self) -> bool:
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5("
                            "delivery_id UNINDEXED, t UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS material_fts USING fts5("
                            "resource_id UNINDEXED, page UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
            return True
        except sqlite3.OperationalError:
            log.warning("SQLite FTS5 unavailable; search falls back to LIKE")
            self.db.execute("CREATE TABLE IF NOT EXISTS transcript_fts(delivery_id TEXT, t REAL, text TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS material_fts(resource_id TEXT, page INTEGER, text TEXT)")
            return False

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, value))
            self.db.commit()

    def rebase_paths(self, moved: Callable[[str], Optional[str]]) -> None:
        """Change the file paths kept here (downloaded materials, plan files, saved weekly digests) after the output
        folder moved: ``moved`` returns a path's new value, or None to keep it."""
        def moved_file(blob: str) -> Optional[str]:         # a JSON object with the path in "file"
            try:
                d = json.loads(blob or "{}")
            except ValueError:
                return None
            new = moved(str(d.get("file") or "")) if isinstance(d, dict) else None
            return json.dumps({**d, "file": new}, ensure_ascii=False) if new else None

        with self._lock:
            for rid, path in self.db.execute("SELECT resource_id, local_path FROM materials WHERE local_path<>''").fetchall():
                new = moved(path)
                if new:
                    self.db.execute("UPDATE materials SET local_path=? WHERE resource_id=?", (new, rid))
            for pid, result in self.db.execute("SELECT id, result FROM plans").fetchall():
                new = moved_file(result)
                if new:
                    self.db.execute("UPDATE plans SET result=? WHERE id=?", (new, pid))
            for key, value in self.db.execute("SELECT key, value FROM meta WHERE key LIKE 'digest:%'").fetchall():
                new = moved_file(value)
                if new:
                    self.db.execute("UPDATE meta SET value=? WHERE key=?", (new, key))
            self.db.commit()

    # ------------------------------------------------------------------ courses
    def upsert_courses(self, courses: list[Course]) -> None:
        with self._lock:
            ids = [c.id for c in courses]
            for c in courses:
                self.db.execute(
                    "INSERT INTO courses(id, title, code, name, instructor, term, status, section, position, cover_url, "
                    "progress, active) VALUES(?,?,?,?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET title=excluded.title, "
                    "code=excluded.code, name=excluded.name, instructor=excluded.instructor, term=excluded.term, "
                    "status=excluded.status, section=excluded.section, position=excluded.position, "
                    "cover_url=excluded.cover_url, progress=excluded.progress, active=1",
                    (c.id, c.title, c.code, c.name, c.instructor, c.term, c.status, c.section, c.position, c.cover_url,
                     c.progress))
            if ids:
                self.db.execute(f"UPDATE courses SET active=0 WHERE id NOT IN ({','.join('?' * len(ids))})", ids)
            self.db.commit()

    def courses(self, include_inactive: bool = False) -> list[Course]:
        q = "SELECT * FROM courses" + ("" if include_inactive else " WHERE active=1") + " ORDER BY position, code"
        with self._lock:
            rows = self.db.execute(q).fetchall()
        return [self._course(r) for r in rows]

    @staticmethod
    def _course(row: sqlite3.Row) -> Course:
        return Course(**{k: (bool(row[k]) if k == "has_panopto" else row[k]) for k in _COURSE_COLS})

    def get_course(self, course_id: str) -> Optional[Course]:
        with self._lock:
            row = self.db.execute("SELECT * FROM courses WHERE id=?", (course_id,)).fetchone()
        return self._course(row) if row else None

    def set_course_folder(self, course_id: str, folder_id: Optional[str], has_panopto: bool) -> None:
        with self._lock:
            self.db.execute("UPDATE courses SET folder_id=?, has_panopto=? WHERE id=?",
                            (folder_id, 1 if has_panopto else 0, course_id))
            self.db.commit()

    # ------------------------------------------------------------------ recordings
    @staticmethod
    def _rec(row: sqlite3.Row) -> Recording:
        d = {k: row[k] for k in _REC_COLS}
        d["start"] = dt.datetime.fromisoformat(d["start"])
        d["is_new"], d["hidden"], d["live"] = bool(d["is_new"]), bool(d["hidden"]), bool(d["live"])
        d["error"], d["uploaded"] = d["error"] or "", d["uploaded"] or ""
        return Recording(**d)

    def has_recordings(self, course_id: str) -> bool:
        with self._lock:
            return self.db.execute("SELECT 1 FROM recordings WHERE course_id=? LIMIT 1", (course_id,)).fetchone() is not None

    def merge_recordings(self, course_id: str, recs: list[Recording], first_sync: bool,
                         new_after: str = "") -> list[str]:
        """Insert/update a course's recordings; returns ids that are NEW since the last sync. ``new_after``: in a
        first sync, recordings that started after this time are new all the same. One still being recorded is new
        (told about) when it has finished. A transcribed one whose class day changed (its start was the upload time)
        is noted for the files to follow (see day_moves)."""
        now = _now()
        new_ids: list[str] = []
        moves: list[list[str]] = []
        with self._lock:
            # what is stored, to write only what changed (last_seen: when it last changed; nothing reads it)
            stored = {r[0]: tuple(r[1:]) for r in self.db.execute(
                "SELECT delivery_id, folder_id, folder_path, name, start, duration_s, viewer_url, session_id, kind, "
                "section_tag, live, uploaded, gone FROM recordings WHERE course_id=?", (course_id,))}
            existing = set(stored)
            fresh = [r.delivery_id for r in recs if r.delivery_id not in existing]
            elsewhere: dict[str, bool] = {}      # in another course: True if it is gone from there
            for i in range(0, len(fresh), 500):
                chunk = fresh[i:i + 500]
                for row in self.db.execute(
                        "SELECT r.delivery_id, r.gone, c.active FROM recordings r LEFT JOIN courses c ON c.id=r.course_id "
                        f"WHERE r.delivery_id IN ({','.join('?' * len(chunk))})", chunk):
                    elsewhere[row[0]] = bool(row[1]) or not row[2]
            for r in recs:
                start = r.start.isoformat()
                if r.delivery_id in elsewhere:   # already in another of your courses
                    if elsewhere[r.delivery_id]:     # gone from there (or that course is gone): it moved here
                        self.db.execute(
                            "UPDATE recordings SET course_id=?, folder_id=?, folder_path=?, name=?, start=?, duration_s=?, "
                            "viewer_url=?, session_id=?, kind=?, section_tag=?, live=?, uploaded=?, last_seen=?, gone=0 "
                            "WHERE delivery_id=?",
                            (course_id, r.folder_id, r.folder_path, r.name, start, r.duration_s, r.viewer_url,
                             r.session_id, r.kind, r.section_tag, int(r.live), r.uploaded, now, r.delivery_id))
                    continue                         # listed by both: it stays where it is, and is not "new"
                if r.delivery_id in existing:
                    row = (r.folder_id, r.folder_path, r.name, start, r.duration_s, r.viewer_url, r.session_id, r.kind,
                           r.section_tag, int(r.live), r.uploaded, 0)
                    old = stored[r.delivery_id]
                    if old == row:
                        continue
                    stored[r.delivery_id] = row
                    self.db.execute(
                        "UPDATE recordings SET folder_id=?, folder_path=?, name=?, start=?, duration_s=?, viewer_url=?, "
                        "session_id=?, kind=?, section_tag=?, live=?, uploaded=?, last_seen=?, gone=0 WHERE delivery_id=?",
                        (*row[:-1], now, r.delivery_id))
                    if old[9] and not r.live:        # finished: it can be downloaded now (a try before said "not ready")
                        self.db.execute("UPDATE recordings SET status='available', error='' "
                                        "WHERE delivery_id=? AND status='processing'", (r.delivery_id,))
                        if self.db.execute("SELECT is_new FROM recordings WHERE delivery_id=?",
                                           (r.delivery_id,)).fetchone()[0]:
                            new_ids.append(r.delivery_id)
                    if (old[3][:10], old[7]) != (start[:10], r.kind) and self.db.execute(
                            "SELECT 1 FROM transcripts WHERE delivery_id=?", (r.delivery_id,)).fetchone():
                        moves.append([r.delivery_id, f"{course_id}|{old[3][:10]}|{old[7]}"])
                else:
                    is_new = 0 if first_sync and not (new_after and start > new_after) else 1
                    cur = self.db.execute(
                        "INSERT OR IGNORE INTO recordings(delivery_id, course_id, folder_id, folder_path, name, start, "
                        "duration_s, viewer_url, session_id, kind, section_tag, is_new, first_seen, last_seen, live, uploaded) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r.delivery_id, course_id, r.folder_id, r.folder_path, r.name, start, r.duration_s, r.viewer_url,
                         r.session_id, r.kind, r.section_tag, is_new, now, now, int(r.live), r.uploaded))
                    if is_new and cur.rowcount == 1 and not r.live:
                        new_ids.append(r.delivery_id)
            seen = {r.delivery_id for r in recs}
            if recs:  # an empty listing is more likely an auth hiccup than a wiped folder: keep what we know
                for gone in existing - seen:
                    if not stored[gone][-1]:
                        self.db.execute("UPDATE recordings SET gone=1 WHERE delivery_id=?", (gone,))
                # one a try found "not ready on Panopto yet" (still being encoded) and that is listed finished: ready
                # to try again, so a batch takes it (a try that still finds it not ready says so again)
                self.db.execute("UPDATE recordings SET status='available', error='' WHERE course_id=? AND "
                                "status='processing' AND live=0 AND gone=0", (course_id,))
            dups = self._mark_duplicates(course_id)
            if moves:
                self._note_day_moves(moves)
            self.db.commit()
        return [i for i in new_ids if i not in dups]

    def _note_day_moves(self, moves: list[list[str]]) -> None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (DAY_MOVES_KEY,)).fetchone()
        try:
            noted = json.loads(row[0]) if row and row[0] else []
        except ValueError:
            noted = []
        noted += [m for m in moves if m not in noted]
        self.db.execute("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (DAY_MOVES_KEY, json.dumps(noted)))

    def day_moves(self) -> list[list[str]]:
        """The transcribed lectures whose class day changed and whose files have not moved yet: [delivery id, old
        day key]. They stay noted until drop_day_moves."""
        with self._lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (DAY_MOVES_KEY,)).fetchone()
        try:
            return [list(m) for m in json.loads(row[0])] if row and row[0] else []
        except (ValueError, TypeError):
            return []

    def drop_day_moves(self, moves: list[list[str]]) -> None:
        """Forget the moves that were done (those noted since are kept)."""
        if not moves:
            return
        with self._lock:
            row = self.db.execute("SELECT value FROM meta WHERE key=?", (DAY_MOVES_KEY,)).fetchone()
            try:
                noted = json.loads(row[0]) if row and row[0] else []
            except ValueError:
                noted = []
            self.db.execute("UPDATE meta SET value=? WHERE key=?",
                            (json.dumps([m for m in noted if m not in moves]), DAY_MOVES_KEY))
            self.db.commit()

    def live_courses(self, since: str = "") -> set[str]:
        """The courses with a recording still being recorded (that started after ``since``)."""
        with self._lock:
            return {r[0] for r in self.db.execute(
                "SELECT DISTINCT course_id FROM recordings WHERE live=1 AND gone=0 AND start>=?", (since,))}

    def _mark_duplicates(self, course_id: str) -> set[str]:
        rows = self.db.execute(
            "SELECT r.delivery_id, r.start, r.duration_s, r.kind, r.first_seen, t.delivery_id AS done, r.status, "
            "r.error, r.warnings FROM recordings r LEFT JOIN transcripts t ON t.delivery_id=r.delivery_id "
            "WHERE r.course_id=? AND r.gone=0 ORDER BY r.start", (course_id,)).fetchall()
        # a copy whose try found no sound (as catchup.no_sound) goes behind the others: the lecturer's re-upload with
        # sound is the one kept, and the silent one is its duplicate
        items = [(dt.datetime.fromisoformat(r["start"]), float(r["duration_s"] or 0), r["kind"], r["delivery_id"],
                  bool(r["done"]), r["first_seen"] or "",
                  '"no_speech"' in (r["warnings"] or "")
                  or (r["status"] == "failed" and bool(_NO_SOUND.search(r["error"] or "")))) for r in rows]
        dup_of: dict[str, Optional[str]] = {i[3]: None for i in items}
        used = [False] * len(items)
        for i, a in enumerate(items):
            if used[i]:
                continue
            cluster = [i]
            for j in range(i + 1, len(items)):
                b = items[j]
                if (b[0] - a[0]).total_seconds() > 60:
                    break
                if not used[j] and b[2] == a[2] and abs(b[1] - a[1]) <= 2.0 and a[1] > 0:
                    cluster.append(j)
            if len(cluster) > 1:
                canon = min(cluster, key=lambda k: (not items[k][4], items[k][6], items[k][5], items[k][3]))
                for k in cluster:
                    used[k] = True
                    if k != canon:
                        dup_of[items[k][3]] = items[canon][3]
        dups = {k for k, v in dup_of.items() if v}
        for did, canon in dup_of.items():
            self.db.execute("UPDATE recordings SET duplicate_of=?, is_new=CASE WHEN ? IS NULL THEN is_new ELSE 0 END "
                            "WHERE delivery_id=? AND duplicate_of IS NOT ?", (canon, canon, did, canon))   # (changed)
        return dups

    def recordings(self, course_id: Optional[str] = None, include_gone: bool = False) -> list[Recording]:
        q = "SELECT * FROM recordings WHERE 1=1"
        args: list[Any] = []
        if course_id is not None:
            q += " AND course_id=?"
            args.append(course_id)
        if not include_gone:
            q += " AND (gone=0 OR delivery_id IN (SELECT delivery_id FROM transcripts))"
        q += " ORDER BY start DESC"
        with self._lock:
            rows = self.db.execute(q, args).fetchall()
        return [self._rec(r) for r in rows]

    def day_recordings(self, day_key: str) -> list[Recording]:
        """The recordings of one class day (``Recording.day_key``), as :meth:`recordings` lists them, without loading
        the rest of the course."""
        course_id, date_s = (day_key.split("|") + [""])[:2]
        with self._lock:
            rows = self.db.execute("SELECT * FROM recordings WHERE course_id=? AND substr(start, 1, 10)=? AND "
                                   "(gone=0 OR delivery_id IN (SELECT delivery_id FROM transcripts)) ORDER BY start DESC",
                                   (course_id, date_s)).fetchall()
        return [r for r in map(self._rec, rows) if r.day_key == day_key]

    def get_recording(self, delivery_id: str) -> Optional[Recording]:
        with self._lock:
            row = self.db.execute("SELECT * FROM recordings WHERE delivery_id=?", (delivery_id,)).fetchone()
        return self._rec(row) if row else None

    def set_status(self, delivery_id: str, status: str, error: str = "") -> None:
        with self._lock:
            self.db.execute("UPDATE recordings SET status=?, error=? WHERE delivery_id=?", (status, error or "", delivery_id))
            if status == "done":
                self.db.execute("UPDATE recordings SET is_new=0 WHERE delivery_id=?", (delivery_id,))
            self.db.commit()

    def set_hidden(self, delivery_id: str, hidden: bool) -> None:
        with self._lock:
            self.db.execute("UPDATE recordings SET hidden=? WHERE delivery_id=?", (1 if hidden else 0, delivery_id))
            self.db.commit()

    def clear_new(self, course_id: Optional[str] = None) -> None:
        with self._lock:
            if course_id:
                self.db.execute("UPDATE recordings SET is_new=0 WHERE course_id=?", (course_id,))
            else:
                self.db.execute("UPDATE recordings SET is_new=0")
            self.db.commit()

    def set_warnings(self, delivery_id: str, warnings: list[dict[str, Any]]) -> None:
        with self._lock:
            self.db.execute("UPDATE recordings SET warnings=? WHERE delivery_id=?",
                            (json.dumps(warnings, ensure_ascii=False), delivery_id))
            self.db.commit()

    def warnings(self, delivery_id: str) -> list[dict[str, Any]]:
        with self._lock:
            row = self.db.execute("SELECT warnings FROM recordings WHERE delivery_id=?", (delivery_id,)).fetchone()
        try:
            return json.loads(row[0]) if row and row[0] else []
        except ValueError:
            return []

    def all_warnings(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            rows = self.db.execute("SELECT delivery_id, warnings FROM recordings WHERE warnings NOT IN ('', '[]')").fetchall()
        out = {}
        for r in rows:
            try:
                out[r[0]] = json.loads(r[1])
            except ValueError:
                pass
        return out

    def reset_interrupted(self) -> None:
        with self._lock:
            self.db.execute("UPDATE recordings SET status='done', error='' WHERE status IN ('queued','downloading','transcribing') "
                            "AND delivery_id IN (SELECT delivery_id FROM transcripts)")
            self.db.execute("UPDATE recordings SET status='available', error='' WHERE status IN ('queued','downloading','transcribing')")
            self.db.execute("UPDATE chat_messages SET status='stopped' WHERE status='streaming'")
            self.db.commit()

    # ------------------------------------------------------------------ transcripts
    def save_transcript(self, t: Transcript, json_path: Path, quality: Optional[dict[str, Any]] = None,
                        slides: int = 0) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO transcripts(delivery_id, created_at, model, engine_version, json_path, language_seconds, "
                "duration_s, speech_s, quality, slides) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(delivery_id) DO UPDATE SET "
                "created_at=excluded.created_at, model=excluded.model, engine_version=excluded.engine_version, "
                "json_path=excluded.json_path, language_seconds=excluded.language_seconds, duration_s=excluded.duration_s, "
                "speech_s=excluded.speech_s, quality=excluded.quality, slides=excluded.slides",
                (t.delivery_id, t.created_at, t.model, t.engine_version, str(json_path),
                 json.dumps(t.language_seconds), t.duration_s, t.speech_s, json.dumps(quality or {}), slides))
            self.db.execute("DELETE FROM transcript_fts WHERE delivery_id=?", (t.delivery_id,))
            self.db.executemany("INSERT INTO transcript_fts(delivery_id, t, text) VALUES(?,?,?)",
                                [(t.delivery_id, s.start, s.text) for s in t.segments])
            self.db.commit()

    def set_slides(self, delivery_id: str, count: int) -> None:
        with self._lock:
            self.db.execute("UPDATE transcripts SET slides=? WHERE delivery_id=?", (count, delivery_id))
            self.db.commit()

    def transcript_info(self, delivery_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self.db.execute("SELECT * FROM transcripts WHERE delivery_id=?", (delivery_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        for k in ("language_seconds", "quality"):
            try:
                d[k] = json.loads(d[k] or "{}")
            except ValueError:
                d[k] = {}
        return d

    def transcribed_ids(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self.db.execute("SELECT delivery_id FROM transcripts")}

    def delete_transcript(self, delivery_id: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM transcripts WHERE delivery_id=?", (delivery_id,))
            self.db.execute("DELETE FROM transcript_fts WHERE delivery_id=?", (delivery_id,))
            self.db.execute("UPDATE recordings SET status='available' WHERE delivery_id=?", (delivery_id,))
            self.db.commit()

    # ------------------------------------------------------------------ course content (IEULearn + syllabus)
    def course_info(self, course_id: str) -> dict[str, Any]:
        with self._lock:
            row = self.db.execute("SELECT * FROM course_info WHERE course_id=?", (course_id,)).fetchone()
        if not row:
            return {"overview": {}, "syllabus": {}, "overview_at": "", "syllabus_at": "", "content_at": ""}
        d = dict(row)
        for k in ("overview", "syllabus"):
            try:
                d[k] = json.loads(d[k] or "{}")
            except ValueError:
                d[k] = {}
        return d

    def set_course_info(self, course_id: str, **fields: Any) -> None:
        """overview / syllabus (dicts) and their *_at timestamps."""
        vals = {k: (json.dumps(v, ensure_ascii=False) if k in ("overview", "syllabus") else v)
                for k, v in fields.items() if k in ("overview", "syllabus", "overview_at", "syllabus_at", "content_at")}
        if not vals:
            return
        with self._lock:
            self.db.execute("INSERT OR IGNORE INTO course_info(course_id) VALUES(?)", (course_id,))
            self.db.execute(f"UPDATE course_info SET {', '.join(f'{k}=?' for k in vals)} WHERE course_id=?",
                            [*vals.values(), course_id])
            self.db.commit()

    def merge_units(self, course_id: str, units: list[Any], first_sync: bool) -> list[str]:
        """Replace a course's weekly units and merge their materials; returns ids of NEW materials."""
        now = _now()
        new_ids: list[str] = []
        with self._lock:
            rows = [(u.id, u.position, u.title, u.start.isoformat() if u.start else "", u.end.isoformat() if u.end else "",
                     u.readings) for u in units]
            # an empty plan is more likely a hiccup than a course without weeks: keep what we know (and the same plan)
            if units and set(rows) != {tuple(r) for r in self.db.execute(
                    "SELECT unit_id, position, title, start_date, end_date, readings FROM units WHERE course_id=?",
                    (course_id,))}:
                self.db.execute("DELETE FROM units WHERE course_id=?", (course_id,))
                self.db.executemany("INSERT OR REPLACE INTO units(course_id, unit_id, position, title, start_date, "
                                    "end_date, readings) VALUES(?,?,?,?,?,?,?)", [(course_id, *r) for r in rows])
            # what is stored, to write only what changed (last_seen: when it last changed; nothing reads it)
            stored = {r[0]: tuple(r[1:]) for r in self.db.execute(
                "SELECT resource_id, unit_id, position, title, file_name, kind, ext, url, download_url, gone "
                "FROM materials WHERE course_id=?", (course_id,))}
            existing = set(stored)
            seen: set[str] = set()
            for u in units:
                for pos, r in enumerate(u.resources):
                    seen.add(r.id)
                    if r.id in existing:
                        row = (u.id, pos, r.title, r.file_name, r.kind, r.ext, r.url, r.download_url, 0)
                        if stored[r.id] == row:
                            continue
                        stored[r.id] = row
                        self.db.execute("UPDATE materials SET unit_id=?, position=?, title=?, file_name=?, kind=?, ext=?, "
                                        "url=?, download_url=?, last_seen=?, gone=0, "
                                        "is_new=CASE WHEN url<>? AND downloaded_at<>'' THEN 1 ELSE is_new END "
                                        "WHERE resource_id=?", (*row[:-1], now, r.url, r.id))
                    else:
                        self.db.execute("INSERT OR IGNORE INTO materials(resource_id, course_id, unit_id, position, title, "
                                        "file_name, kind, ext, url, download_url, first_seen, last_seen, is_new) "
                                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                        (r.id, course_id, u.id, pos, r.title, r.file_name, r.kind, r.ext, r.url,
                                         r.download_url, "" if first_sync else now, now, 0 if first_sync else 1))
                        if not first_sync:
                            new_ids.append(r.id)
            if units:   # an empty plan is more likely a hiccup than a course without materials: keep what we know
                for rid in existing - seen:
                    if not stored[rid][-1]:
                        self.db.execute("UPDATE materials SET gone=1 WHERE resource_id=?", (rid,))
            self.db.commit()
        return new_ids

    def units(self, course_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM units WHERE course_id=? ORDER BY position", (course_id,)).fetchall()
        return [dict(r) for r in rows]

    def materials(self, course_id: Optional[str] = None, include_gone: bool = False) -> list[dict[str, Any]]:
        q = "SELECT * FROM materials WHERE 1=1"
        args: list[Any] = []
        if course_id is not None:
            q += " AND course_id=?"
            args.append(course_id)
        if not include_gone:
            q += " AND (gone=0 OR local_path<>'')"
        with self._lock:
            rows = self.db.execute(q + " ORDER BY course_id, unit_id, position", args).fetchall()
        return [dict(r) for r in rows]

    def get_material(self, resource_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self.db.execute("SELECT * FROM materials WHERE resource_id=?", (resource_id,)).fetchone()
        return dict(row) if row else None

    def get_materials(self, resource_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """:meth:`get_material` for several at once: {resource_id: material} of those that exist."""
        ids = list(dict.fromkeys(resource_ids))
        if not ids:
            return {}
        with self._lock:
            rows = self.db.execute(f"SELECT * FROM materials WHERE resource_id IN ({','.join('?' * len(ids))})",
                                   ids).fetchall()
        return {r["resource_id"]: dict(r) for r in rows}

    def content_counts(self) -> dict[str, dict[str, int]]:
        """Per course, for the course list: its course files as :meth:`materials` lists them ("materials", the new
        ones, the downloaded ones) and the new ones of the latest 500 announcements."""
        out: dict[str, dict[str, int]] = {}
        with self._lock:
            for cid, n, new, got in self.db.execute(
                    "SELECT course_id, COUNT(*), SUM(CASE WHEN is_new THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN COALESCE(local_path, '')<>'' THEN 1 ELSE 0 END) FROM materials "
                    "WHERE gone=0 OR local_path<>'' GROUP BY course_id"):
                out[cid] = {"materials": n, "materials_new": new, "downloaded": got, "announcements_new": 0}
            for cid, new in self.db.execute(
                    "SELECT course_id, SUM(CASE WHEN is_new THEN 1 ELSE 0 END) FROM (SELECT course_id, is_new FROM "
                    "announcements ORDER BY posted_at DESC, first_seen DESC LIMIT 500) GROUP BY course_id"):
                out.setdefault(cid, {"materials": 0, "materials_new": 0, "downloaded": 0})["announcements_new"] = new
        return out

    def set_material_file(self, resource_id: str, local_path: str, size: int, url: str) -> None:
        with self._lock:
            self.db.execute("UPDATE materials SET local_path=?, size=?, downloaded_at=?, downloaded_url=?, is_new=0, "
                            "text_status='' WHERE resource_id=?", (local_path, size, _now(), url, resource_id))
            self.db.commit()

    def set_material_text(self, resource_id: str, pages: list[str], status: str = "done") -> None:
        """Index the extracted text of a material (one entry per page or slide) for search and AI."""
        with self._lock:
            self.db.execute("DELETE FROM material_fts WHERE resource_id=?", (resource_id,))
            self.db.executemany("INSERT INTO material_fts(resource_id, page, text) VALUES(?,?,?)",
                                [(resource_id, i + 1, t) for i, t in enumerate(pages) if t.strip()])
            self.db.execute("UPDATE materials SET pages=?, text_status=? WHERE resource_id=?",
                            (len(pages), status, resource_id))
            self.db.commit()

    def material_pages(self, resource_id: str) -> list[str]:
        with self._lock:
            rows = self.db.execute("SELECT page, text FROM material_fts WHERE resource_id=? ORDER BY page",
                                   (resource_id,)).fetchall()
            total = self.db.execute("SELECT pages FROM materials WHERE resource_id=?", (resource_id,)).fetchone()
        pages = [""] * max(int(total[0] or 0) if total else 0, max((int(r[0]) for r in rows), default=0))
        for p, t in rows:
            pages[int(p) - 1] = t
        return pages

    def clear_new_materials(self, course_id: Optional[str] = None) -> None:
        with self._lock:
            self.db.execute("UPDATE materials SET is_new=0" + (" WHERE course_id=?" if course_id else ""),
                            (course_id,) if course_id else ())
            self.db.commit()

    def merge_announcements(self, course_id: str, items: list[Any], first_sync: bool) -> list[str]:
        now = _now()
        new_ids: list[str] = []
        with self._lock:
            for a in items:
                aid = a.id                             # title, author and minute: the same in each course it went to
                held = self.db.execute("SELECT course_id FROM announcements WHERE id=?", (aid,)).fetchone()
                copy = held is not None and held[0] != course_id
                if copy:                               # kept here too, but it is no news: another course has it
                    aid = f"{a.id}-{course_id}"
                posted = a.posted_at.isoformat(timespec="minutes") if a.posted_at else ""
                if posted and not self.db.execute("SELECT 1 FROM announcements WHERE id=?", (aid,)).fetchone():
                    # its title edited on IEULearn (a new id): the post of the same author and minute whose title
                    # isn't on the page any more is this one, updated below, not a second one; at the next syncs it
                    # is the one already holding the new title (its id stays the old one)
                    shown = {x.title for x in items if x.author == a.author and x.posted_at == a.posted_at}
                    rows = [r for r in self.db.execute(
                        "SELECT id, title FROM announcements WHERE course_id=? AND author=? AND posted_at=?",
                        (course_id, a.author, posted)) if r[1] == a.title or r[1] not in shown]
                    aid = min(rows, key=lambda r: r[1] != a.title)[0] if rows else aid
                news = not first_sync and not copy
                cur = self.db.execute("INSERT OR IGNORE INTO announcements(id, course_id, title, author, posted_at, body, "
                                      "first_seen, is_new) VALUES(?,?,?,?,?,?,?,?)",
                                      (aid, course_id, a.title, a.author,
                                       a.posted_at.isoformat(timespec="minutes") if a.posted_at else "", a.body, now,
                                       1 if news else 0))
                if cur.rowcount and news:
                    new_ids.append(aid)
                elif not cur.rowcount and a.body:      # known: an edited post keeps its id, so take its new text
                    self.db.execute("UPDATE announcements SET title=?, body=? WHERE id=? AND course_id=? AND "
                                    "(title IS NOT ? OR body IS NOT ?)", (a.title, a.body, aid, course_id, a.title,
                                                                          a.body))
            self.db.commit()
        return new_ids

    def announcements(self, course_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        q = "SELECT * FROM announcements" + (" WHERE course_id=?" if course_id else "") + \
            " ORDER BY posted_at DESC, first_seen DESC LIMIT ?"
        with self._lock:
            rows = self.db.execute(q, ((course_id, limit) if course_id else (limit,))).fetchall()
        return [dict(r) for r in rows]

    def term_start(self, term: str) -> Optional[str]:
        """The earliest dated unit of any course in this term (for courses whose weekly plan has no dates)."""
        with self._lock:
            row = self.db.execute("SELECT MIN(u.start_date) FROM units u JOIN courses c ON c.id = u.course_id "
                                  "WHERE c.term = ? AND u.start_date != ''", (term,)).fetchone()
        return row[0] if row and row[0] else None

    def clear_new_announcements(self, course_id: Optional[str] = None) -> None:
        with self._lock:
            self.db.execute("UPDATE announcements SET is_new=0" + (" WHERE course_id=?" if course_id else ""),
                            (course_id,) if course_id else ())
            self.db.commit()

    def search_materials(self, query: str, limit: int = 40) -> list[dict[str, Any]]:
        words = re.findall(r"[\w']+", query or "", flags=re.UNICODE)
        if not words:
            return []
        with self._lock:
            if self.fts:
                q = _fts_query(words)
                try:
                    rows = self.db.execute(
                        "SELECT resource_id, page, snippet(material_fts, 2, char(2), char(3), '…', 14) FROM material_fts "
                        f"WHERE material_fts MATCH ? ORDER BY {_search_order(words)} LIMIT ?", (q, limit)).fetchall()
                    return [{"resource_id": r[0], "page": int(r[1]), "snippet": r[2]} for r in rows]
                except sqlite3.OperationalError:
                    pass
            like = "%" + "%".join(words) + "%"
            rows = self.db.execute("SELECT resource_id, page, text FROM material_fts WHERE text LIKE ? LIMIT ?",
                                   (like, limit)).fetchall()
        return [{"resource_id": r[0], "page": int(r[1]), "snippet": r[2][:160]} for r in rows]

    # ------------------------------------------------------------------ retrieval (for Claude)
    _STOP = set((
        "the a an and or of to in on for with is are was were be been being it its this that these those what which who "
        "whom how why when where do does did done can could should would will shall may might must i you we they he she "
        "my your our their me us them about from by at as into than then there here not no yes if so but also just very "
        "more most some any all each every one two use used using get give tell explain show please mean means does "
        "ve bir bu şu o ne ile için mi mı mu mü da de ki ya ama gibi daha en çok olan nasıl neden hangi nedir var yok "
        "ise veya hem her şey").split())

    def search_any(self, query: str, course_id: Optional[str] = None, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
        """Pages of course files and transcript passages that share the most words with ``query`` (any word, best
        first; common words ignored): {"materials": [{resource_id, page, text}], "transcripts": [{delivery_id, t, text}]}."""
        words = [w for w in re.findall(r"[\w']+", (query or "").replace("İ", "i").lower(), flags=re.UNICODE)
                 if len(w) >= 3 and w not in self._STOP and not w.isdigit()]
        words = list(dict.fromkeys(words))[:10]
        out: dict[str, list[dict[str, Any]]] = {"materials": [], "transcripts": []}
        if not words or not self.fts:
            return out
        q = " OR ".join(_fts_word(w, len(w) >= 5) for w in words)
        with self._lock:
            try:
                rows = self.db.execute(
                    "SELECT f.resource_id, f.page, f.text FROM material_fts f JOIN materials m ON m.resource_id = f.resource_id "
                    "WHERE material_fts MATCH ?" + (" AND m.course_id=?" if course_id else "") + " ORDER BY rank LIMIT ?",
                    (q, course_id, limit) if course_id else (q, limit)).fetchall()
                out["materials"] = [{"resource_id": r[0], "page": int(r[1]), "text": r[2]} for r in rows]
                rows = self.db.execute(
                    "SELECT f.delivery_id, f.t, f.text FROM transcript_fts f JOIN recordings r ON r.delivery_id = f.delivery_id "
                    "WHERE transcript_fts MATCH ?" + (" AND r.course_id=?" if course_id else "") + " ORDER BY rank LIMIT ?",
                    (q, course_id, limit * 3) if course_id else (q, limit * 3)).fetchall()
                out["transcripts"] = [{"delivery_id": r[0], "t": float(r[1]), "text": r[2]} for r in rows]
            except sqlite3.OperationalError:
                log.debug("search_any failed for %r", q, exc_info=True)
        return out

    # ------------------------------------------------------------------ video study plans
    _PLAN_JSON = ("scope", "data", "result", "marks", "usage")

    def create_plan(self, plan_id: str, course_id: str, scope: dict[str, Any], title: str = "",
                    parent_id: str = "") -> None:
        now = _now()
        with self._lock:
            self.db.execute("INSERT INTO plans(id, course_id, title, scope, status, parent_id, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?,?)", (plan_id, course_id, title, json.dumps(scope, ensure_ascii=False),
                                                        "new", parent_id, now, now))
            self.db.commit()

    def update_plan(self, plan_id: str, **fields: Any) -> None:
        cols = {k: (json.dumps(v, ensure_ascii=False) if k in self._PLAN_JSON else v) for k, v in fields.items()}
        cols["updated_at"] = _now()
        with self._lock:
            self.db.execute(f"UPDATE plans SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", [*cols.values(), plan_id])
            self.db.commit()

    def plan(self, plan_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self.db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return self._plan_row(row) if row else None

    _PLAN_BRIEF = ("id, course_id, title, scope, status, stage, progress, message, error, parent_id, created_at, "
                   "updated_at, usage")

    def plans(self, course_id: Optional[str] = None) -> list[dict[str, Any]]:
        """All plans (newest first), without their bulky stage data and result."""
        q = f"SELECT {self._PLAN_BRIEF} FROM plans" + (" WHERE course_id=?" if course_id else "") + " ORDER BY created_at DESC"
        with self._lock:
            rows = self.db.execute(q, (course_id,) if course_id else ()).fetchall()
        return [self._plan_row(r) for r in rows]

    def plan_brief(self, plan_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self.db.execute(f"SELECT {self._PLAN_BRIEF} FROM plans WHERE id=?", (plan_id,)).fetchone()
        return self._plan_row(row) if row else None

    def _plan_row(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in self._PLAN_JSON:
            if k in d:
                try:
                    d[k] = json.loads(d[k] or "{}")
                except ValueError:
                    d[k] = {}
        return d

    def delete_plan(self, plan_id: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM plans WHERE id=?", (plan_id,))
            self.db.commit()

    def pause_running_plans(self) -> None:
        """Plans that were being built when the app closed can be resumed (their finished stages are kept)."""
        from .i18n import t

        with self._lock:
            self.db.execute("UPDATE plans SET status='paused', message=? WHERE status IN ('running','queued')",
                            (t("Interrupted — resume to continue"),))
            self.db.commit()

    # ------------------------------------------------------------------ chats with Claude
    @staticmethod
    def _chat(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["scope"] = json.loads(d.get("scope") or "{}")
        except ValueError:
            d["scope"] = {}
        return d

    def create_chat(self, chat_id: str, course_id: str, scope: dict[str, Any], model: str, title: str = "") -> None:
        now = _now()
        with self._lock:
            self.db.execute("INSERT INTO chats(id, course_id, title, scope, model, created_at, updated_at) "
                            "VALUES(?,?,?,?,?,?,?)", (chat_id, course_id, title, json.dumps(scope), model, now, now))
            self.db.commit()

    def update_chat(self, chat_id: str, **fields: Any) -> None:
        allowed = {k: (json.dumps(v) if k == "scope" else v) for k, v in fields.items()
                   if k in ("title", "scope", "model")}
        allowed["updated_at"] = _now()
        with self._lock:
            self.db.execute(f"UPDATE chats SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?",
                            [*allowed.values(), chat_id])
            self.db.commit()

    def move_chat_day(self, old_key: str, new_key: str, keep_old: bool) -> None:
        """A transcribed lecture's class day changed: the chats holding its old day hold the new day in its place,
        or right after it when the old day keeps lectures (``keep_old``), so their lecture numbers stay in order."""
        with self._lock:
            rows = self.db.execute("SELECT id, scope FROM chats WHERE course_id=?",
                                   (old_key.split("|", 1)[0],)).fetchall()
            for cid, raw in rows:
                try:
                    scope = json.loads(raw or "{}")
                    days = [str(k) for k in scope.get("days") or []]
                except (ValueError, AttributeError):
                    continue
                if old_key not in days or (keep_old and new_key in days):
                    continue
                at = days.index(old_key)
                if keep_old:
                    days.insert(at + 1, new_key)
                else:
                    days[at:at + 1] = [] if new_key in days else [new_key]
                self.db.execute("UPDATE chats SET scope=? WHERE id=?", (json.dumps({**scope, "days": days}), cid))
            self.db.commit()

    def get_chat(self, chat_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self.db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return self._chat(row) if row else None

    def chats(self, chat_id: Optional[str] = None) -> list[dict[str, Any]]:
        """All chats (or the one with ``chat_id``), most recently used first, with their first question and message
        count."""
        with self._lock:
            rows = self.db.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id=c.id) AS n_messages, "
                "(SELECT text FROM chat_messages m WHERE m.chat_id=c.id AND m.role='user' ORDER BY seq LIMIT 1) "
                "AS first_question FROM chats c" + (" WHERE c.id=?" if chat_id is not None else "")
                + " ORDER BY c.updated_at DESC, c.created_at DESC", (chat_id,) if chat_id is not None else ()).fetchall()
        return [self._chat(r) for r in rows]

    def chat_and_plan_courses(self) -> set[str]:
        """The courses that have a chat or a study plan."""
        with self._lock:
            return {r[0] for r in self.db.execute("SELECT course_id FROM chats UNION SELECT course_id FROM plans")}

    def delete_chat(self, chat_id: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM chat_messages WHERE chat_id=?", (chat_id,))
            self.db.execute("DELETE FROM chats WHERE id=?", (chat_id,))
            self.db.commit()

    def chat_messages(self, chat_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self.db.execute("SELECT * FROM chat_messages WHERE chat_id=? ORDER BY seq", (chat_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["usage"] = json.loads(d.get("usage") or "{}")
            except ValueError:
                d["usage"] = {}
            out.append(d)
        return out

    def add_chat_message(self, chat_id: str, role: str, text: str, status: str = "done") -> int:
        now = _now()
        with self._lock:
            seq = self.db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_messages WHERE chat_id=?",
                                  (chat_id,)).fetchone()[0]
            self.db.execute("INSERT INTO chat_messages(chat_id, seq, role, text, status, created_at) VALUES(?,?,?,?,?,?)",
                            (chat_id, seq, role, text, status, now))
            self.db.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            self.db.commit()
        return int(seq)

    def delete_chat_messages(self, chat_id: str, from_seq: int) -> None:
        with self._lock:
            self.db.execute("DELETE FROM chat_messages WHERE chat_id=? AND seq>=?", (chat_id, from_seq))
            self.db.commit()

    def update_chat_message(self, chat_id: str, seq: int, text: str, status: str,
                            usage: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            self.db.execute("UPDATE chat_messages SET text=?, status=?, usage=? WHERE chat_id=? AND seq=?",
                            (text, status, json.dumps(usage or {}), chat_id, seq))
            self.db.commit()

    # ------------------------------------------------------------------ the student's own marks in transcripts
    def set_mark(self, delivery_id: str, t: float, text: str = "", note: Optional[str] = None) -> None:
        """Mark a passage (at its paragraph's start, ``t``), or change the note of a mark. ``text``: the passage,
        kept so the mark still reads well if the lecture is transcribed again."""
        t = round(float(t), 1)
        with self._lock:
            row = self.db.execute("SELECT 1 FROM marks WHERE delivery_id=? AND t=?", (delivery_id, t)).fetchone()
            if row is None:
                self.db.execute("INSERT INTO marks(delivery_id, t, text, note, created_at) VALUES(?,?,?,?,?)",
                                (delivery_id, t, (text or "")[:400], (note or "").strip(), _now()))
            elif note is not None:
                self.db.execute("UPDATE marks SET note=? WHERE delivery_id=? AND t=?", (note.strip(), delivery_id, t))
            self.db.commit()

    def delete_mark(self, delivery_id: str, t: float) -> None:
        with self._lock:
            self.db.execute("DELETE FROM marks WHERE delivery_id=? AND t=?", (delivery_id, round(float(t), 1)))
            self.db.commit()

    def marks(self, delivery_ids: Optional[Iterable[str]] = None) -> list[dict[str, Any]]:
        """Marks, of some recordings or all, in the order of the lecture."""
        with self._lock:
            if delivery_ids is None:
                rows = self.db.execute("SELECT delivery_id, t, text, note, created_at FROM marks").fetchall()
            else:
                ids = list(delivery_ids)
                rows = self.db.execute("SELECT delivery_id, t, text, note, created_at FROM marks "
                                       f"WHERE delivery_id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
        return sorted(({"delivery_id": r[0], "t": r[1], "text": r[2], "note": r[3], "created_at": r[4]} for r in rows),
                      key=lambda m: (m["delivery_id"], m["t"]))

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        words = re.findall(r"[\w']+", query or "", flags=re.UNICODE)
        if not words:
            return []
        with self._lock:
            if self.fts:
                q = _fts_query(words)
                try:
                    rows = self.db.execute(
                        "SELECT delivery_id, t, snippet(transcript_fts, 2, char(2), char(3), '…', 14) AS snip "
                        f"FROM transcript_fts WHERE transcript_fts MATCH ? ORDER BY {_search_order(words)} LIMIT ?",
                        (q, limit)).fetchall()
                    return [{"delivery_id": r[0], "t": float(r[1]), "snippet": r[2]} for r in rows]
                except sqlite3.OperationalError:
                    pass
            like = "%" + "%".join(words) + "%"
            rows = self.db.execute("SELECT delivery_id, t, text FROM transcript_fts WHERE text LIKE ? LIMIT ?",
                                   (like, limit)).fetchall()
        return [{"delivery_id": r[0], "t": float(r[1]), "snippet": r[2][:160]} for r in rows]
