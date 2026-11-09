"""How many people use IEUStudia: each release's downloads on GitHub. Read only, no token needed.

    python scripts/usage.py           every release, newest first
    python scripts/usage.py --json    the same as JSON (to keep, or to compare with a later run)

The columns:
  installer   IEUStudia-Setup-<version>.exe and IEUStudia-Setup.exe (the README's link) of that release: first
              installs, and the updates the app downloads itself
  portable    the portable zip
  active.txt  an installed copy that looks for new versions by itself downloads its own version's active.txt about
              once a day (updates.py, at most once a day, nothing else sent), so this counts the days copies of that
              version were in use; "-" for releases from before the file was attached
  a day       active.txt divided by the days since the release came out: about how many copies used that version on
              an average day (copies that updated count for the newer version from then on)

Uses the gh command when it is installed and signed in (higher limits), else GitHub's public API without a token
(60 requests an hour; one request lists 100 releases)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
import urllib.request
from typing import Any, Callable, Optional

REPO = "mirzaisi/IEUStudia"
API = f"https://api.github.com/repos/{REPO}/releases?per_page=100"
ACTIVE = "active.txt"


def _pages(text: str) -> list[dict[str, Any]]:
    """`gh api --paginate` writes one JSON array per page, one after the other: all their releases."""
    out: list[dict[str, Any]] = []
    decoder, i = json.JSONDecoder(), 0
    text = text.strip()
    while i < len(text):
        page, i = decoder.raw_decode(text, i)
        out += page if isinstance(page, list) else [page]
        while i < len(text) and text[i].isspace():
            i += 1
    return out


def via_gh(run: Callable[..., Any] = subprocess.run) -> Optional[list[dict[str, Any]]]:
    """The releases through the gh command (signed in: its own limits); None without it, or when it fails."""
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        r = run([gh, "api", "--paginate", f"repos/{REPO}/releases?per_page=100"], capture_output=True, text=True,
                encoding="utf-8", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        return _pages(r.stdout)
    except ValueError:
        return None


def _next(link: str) -> str:
    """The next page from GitHub's Link header: <https://…&page=2>; rel="next"."""
    for part in (link or "").split(","):
        if 'rel="next"' in part and "<" in part and ">" in part:
            return part[part.index("<") + 1:part.index(">")]
    return ""


def via_api(opener: Callable[..., Any] = urllib.request.urlopen) -> list[dict[str, Any]]:
    """The releases from GitHub's public API, without a token, every page."""
    out: list[dict[str, Any]] = []
    url = API
    while url:
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "IEUStudia usage script"})
        with opener(req, timeout=30) as resp:
            out += json.loads(resp.read().decode("utf-8"))
            url = _next(resp.headers.get("Link", ""))
    return out


def counts(releases: list[dict[str, Any]], now: Optional[dt.datetime] = None) -> list[dict[str, Any]]:
    """Each release's downloads, newest first: {"tag", "published", "draft", "installer", "portable", "active",
    "days", "per_day"} ("active" and "per_day" None for a release without active.txt)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = []
    for rel in releases:
        assets = {str(a.get("name") or ""): int(a.get("download_count") or 0) for a in rel.get("assets") or []}
        when = str(rel.get("published_at") or rel.get("created_at") or "")
        try:
            published = dt.datetime.fromisoformat(when.replace("Z", "+00:00"))
            days = max(1, (now - published).days + 1)          # (out today: one day so far)
        except ValueError:
            published, days = None, 0
        active = assets.get(ACTIVE)
        rows.append({"tag": str(rel.get("tag_name") or ""), "published": published.date().isoformat() if published else "",
                     "draft": bool(rel.get("draft")),
                     "installer": sum(n for k, n in assets.items() if k.startswith("IEUStudia-Setup") and k.endswith(".exe")),
                     "portable": sum(n for k, n in assets.items() if k.endswith("-portable.zip")),
                     "active": active, "days": days,
                     "per_day": round(active / days, 1) if active is not None and days else None})
    return sorted(rows, key=lambda r: r["published"] or "9999", reverse=True)


def table(rows: list[dict[str, Any]]) -> str:
    head = f"{'release':<12}{'out':<12}{'installer':>10}{'portable':>10}{'active.txt':>12}{'a day':>8}"
    lines = [head, "-" * len(head)]
    for r in rows:
        tag = r["tag"] + (" (draft)" if r["draft"] else "")
        active = "-" if r["active"] is None else str(r["active"])
        per_day = "-" if r["per_day"] is None else f"{r['per_day']:.1f}"
        lines.append(f"{tag:<12}{r['published']:<12}{r['installer']:>10}{r['portable']:>10}{active:>12}{per_day:>8}")
    lines.append("-" * len(head))
    lines.append(f"{'all':<24}{sum(r['installer'] for r in rows):>10}{sum(r['portable'] for r in rows):>10}"
                 f"{sum(r['active'] or 0 for r in rows):>12}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Each IEUStudia release's downloads on GitHub (read only).")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = p.parse_args(argv)
    releases = via_gh()
    if releases is None:
        try:
            releases = via_api()
        except (OSError, ValueError) as exc:         # offline, GitHub's hourly limit, an answer that isn't GitHub's
            print(f"GitHub couldn't be asked: {exc}", file=sys.stderr)
            return 1
    rows = counts(releases)
    print(json.dumps(rows, indent=1) if args.json else table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
