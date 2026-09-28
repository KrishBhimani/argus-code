"""GitHub PR status, opt-in: asked through the user's own `gh` CLI, cached in work.db.

One switch and one clock, both in `meta`: the CLI, the dashboard and the background scan all go
through enable / disable / refresh / maybe_refresh, so they cannot drift. MERGED and CLOSED are
final and never asked again. Argus reads no token: gh does the auth."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta

from .db import get_meta, set_meta

GITHUB_INTERVAL = timedelta(minutes=30)
BATCH = 50
_SLUG = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")   # anything else never reaches a query
_NO_GH = "gh CLI not found: install it and run `gh auth login`"


class GhError(RuntimeError):
    def __init__(self, message: str, stdout: str | None = None) -> None:
        super().__init__(message)
        self.stdout = stdout   # gh exits 1 when any alias errors, yet prints the other answers


def gh_available() -> bool:
    return shutil.which("gh") is not None


def run_gh(args: list[str], timeout: float = 20) -> str:
    if not gh_available():
        raise GhError(_NO_GH)
    try:
        p = subprocess.run(["gh", *args], capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GhError(str(e)) from e
    if p.returncode != 0:
        raise GhError(p.stderr.strip() or f"gh exited {p.returncode}", stdout=p.stdout)
    return p.stdout


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def is_enabled(conn: sqlite3.Connection) -> bool:
    return get_meta(conn, "github_enabled") == "1"


def status(conn: sqlite3.Connection) -> dict:
    return {"enabled": is_enabled(conn), "checked_at": get_meta(conn, "github_checked_at"),
            "last_error": get_meta(conn, "github_last_error") or None, "gh_available": gh_available()}


def pr_states(conn: sqlite3.Connection, repo: str) -> dict[int, str]:
    return {r["number"]: r["state"] for r in conn.execute(
        "SELECT number, state FROM pr_status WHERE repo = ?", (repo.lower(),))}


def _pending(conn: sqlite3.Connection) -> dict[str, list[int]]:
    """PRs sessions opened on github.com whose last known state is not final, by owner/repo."""
    out: dict[str, set[int]] = {}
    for r in conn.execute(
        """SELECT DISTINCT lower(sp.pr_repository) AS repo, sp.pr_number AS n FROM session_prs sp
           LEFT JOIN pr_status ps ON ps.repo = lower(sp.pr_repository) AND ps.number = sp.pr_number
           WHERE typeof(sp.pr_number) = 'integer' AND sp.pr_number BETWEEN 1 AND 2147483647
             AND sp.pr_url LIKE 'https://github.com/%'
             AND (ps.state IS NULL OR ps.state NOT IN ('MERGED', 'CLOSED', 'NOT_FOUND'))"""):
        if r["repo"] and _SLUG.match(r["repo"]):
            out.setdefault(r["repo"], set()).add(int(r["n"]))
    return {k: sorted(v) for k, v in sorted(out.items())}


def _query(owner: str, name: str, numbers: list[int]) -> str:
    fields = " ".join(f"p{n}: pullRequest(number: {n}) {{ state mergedAt }}" for n in numbers)
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


def _partial(stdout: str | None) -> dict | None:
    """gh's stdout when it exited 1 over some aliases but still returned data for the rest."""
    try:
        out = json.loads(stdout or "")
    except ValueError:
        return None
    return out if isinstance(out, dict) and out.get("data") else None


def _messages(out: dict) -> str:
    return "; ".join(e.get("message", "") for e in out.get("errors") or [] if isinstance(e, dict))


def refresh(conn: sqlite3.Connection, now_iso: str) -> dict:
    """Ask GitHub now about every non-final PR, one batched query per repo, and reset the clock.
    A failing repo is recorded and skipped; answers already cached are kept."""
    checked, errors = 0, []
    if not gh_available():
        errors.append(_NO_GH)
    else:
        for repo, numbers in _pending(conn).items():
            owner, name = repo.split("/", 1)
            for i in range(0, len(numbers), BATCH):
                chunk = numbers[i:i + BATCH]
                try:
                    out = json.loads(run_gh(["api", "graphql", "-f", f"query={_query(owner, name, chunk)}"]))
                except GhError as e:
                    out = _partial(e.stdout)
                    if out is None:   # nothing usable came back: record it, try the next repo
                        errors.append(f"{repo}: {e}")
                        break
                except ValueError as e:
                    errors.append(f"{repo}: {e}")
                    break
                data = (out.get("data") or {}).get("repository") or {}
                if not data:   # the repo itself is unreadable (renamed, no access)
                    errors.append(f"{repo}: {_messages(out) or 'repository not found'}")
                    break
                missing = []
                for n in chunk:
                    pr = data.get(f"p{n}")
                    state = pr.get("state") if pr else "NOT_FOUND"   # final: an issue number, or gone
                    conn.execute("INSERT OR REPLACE INTO pr_status VALUES (?, ?, ?, ?, ?)",
                                 (repo, n, state, pr.get("mergedAt") if pr else None, now_iso))
                    if pr:
                        checked += 1
                    else:
                        missing.append(n)
                if missing:
                    errors.append(f"{repo}: no PR {', '.join(f'#{n}' for n in missing)}")
    error = "; ".join(errors) or None
    set_meta(conn, "github_checked_at", now_iso)     # every refresh resets the clock, errors included
    set_meta(conn, "github_last_error", error or "")
    return {"checked": checked, "error": error}


def enable(conn: sqlite3.Connection, now_iso: str) -> dict:
    set_meta(conn, "github_enabled", "1")
    return refresh(conn, now_iso)


def disable(conn: sqlite3.Connection) -> None:
    set_meta(conn, "github_enabled", "0")


def maybe_refresh(conn: sqlite3.Connection, now_iso: str) -> dict | None:
    """The scan's hook: refresh only when on and GITHUB_INTERVAL has passed since the last refresh."""
    if not is_enabled(conn):
        return None
    last = get_meta(conn, "github_checked_at")
    if last and _dt(now_iso) - _dt(last) < GITHUB_INTERVAL:
        return None
    return refresh(conn, now_iso)
