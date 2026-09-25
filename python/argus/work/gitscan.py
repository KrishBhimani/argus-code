"""Read local git history with the git CLI (no network), store every author."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

_RS, _US, _GS = "\x1e", "\x1f", "\x1d"
LOG_FORMAT = (
    "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s%x1f"
    "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"
)
AGENT_TRAILER = re.compile(r"claude|noreply@anthropic\.com", re.IGNORECASE)
RESCAN_OVERLAP_DAYS = 2
FALLBACK_DAYS = 180


class GitError(RuntimeError):
    pass


@dataclass
class FileStat:
    path: str
    added: int | None
    deleted: int | None


@dataclass
class CommitRec:
    sha: str
    parents: int
    author_name: str
    author_email: str
    authored_at: str
    committed_at: str
    subject: str
    agent_coauthored: bool
    files: list[FileStat] = field(default_factory=list)


def run_git(args: list[str], cwd: Path | str, timeout: float = 60) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:  # git missing, bad cwd, timeout
        raise GitError(str(e)) from e
    if p.returncode != 0:
        raise GitError(p.stderr.strip() or f"git {args[0]} exited {p.returncode}")
    return p.stdout


def _utc(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def repo_root(cwd: str) -> str | None:
    if not cwd or not Path(cwd).is_dir():
        return None
    try:
        return run_git(["rev-parse", "--show-toplevel"], cwd, timeout=15).strip() or None
    except GitError:
        return None


def global_user_email() -> str | None:
    try:
        return run_git(["config", "--global", "user.email"], Path.home(), timeout=15).strip() or None
    except GitError:
        return None


def ensure_repo(conn: sqlite3.Connection, root: str) -> int:
    conn.execute("INSERT OR IGNORE INTO repos (root, display_name) VALUES (?, ?)",
                 (root, root.rstrip("/").rsplit("/", 1)[-1]))
    return conn.execute("SELECT id FROM repos WHERE root = ?", (root,)).fetchone()["id"]


def parse_log(text: str) -> list[CommitRec]:
    out: list[CommitRec] = []
    for rec in text.split(_RS):
        if not rec.strip():
            continue
        header, _, body = rec.partition("\n")
        f = header.split(_US)
        if len(f) < 8:
            continue
        sha, parents, an, ae, ad, cd, subject, trailers = f[:8]
        files: list[FileStat] = []
        for line in body.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            a, d, path = parts
            files.append(FileStat(path, None if a == "-" else int(a), None if d == "-" else int(d)))
        out.append(CommitRec(
            sha=sha, parents=len(parents.split()), author_name=an, author_email=ae.lower(),
            authored_at=_utc(ad), committed_at=_utc(cd), subject=subject,
            agent_coauthored=any(AGENT_TRAILER.search(t) for t in trailers.split(_GS) if t.strip()),
            files=files,
        ))
    return out


def _store(conn: sqlite3.Connection, repo_id: int, commits: list[CommitRec]) -> None:
    for c in commits:
        conn.execute(
            """INSERT OR REPLACE INTO commits (repo_id, sha, author_name, author_email, authored_at,
                 committed_at, subject, parents, agent_coauthored, added, deleted, files)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (repo_id, c.sha, c.author_name, c.author_email, c.authored_at, c.committed_at,
             c.subject, c.parents, int(c.agent_coauthored),
             sum(f.added or 0 for f in c.files), sum(f.deleted or 0 for f in c.files), len(c.files)),
        )
        conn.execute("DELETE FROM commit_files WHERE repo_id = ? AND sha = ?", (repo_id, c.sha))
        conn.executemany(
            "INSERT OR REPLACE INTO commit_files (repo_id, sha, path, added, deleted) VALUES (?,?,?,?,?)",
            [(repo_id, c.sha, f.path, f.added, f.deleted) for f in c.files],
        )


def scan_repo(conn: sqlite3.Connection, repo_id: int, root: str, now_iso: str) -> int:
    row = conn.execute("SELECT last_scanned_at FROM repos WHERE id = ?", (repo_id,)).fetchone()
    base = ["log", "--all", "--no-color", "--no-renames", "--numstat", f"--format={LOG_FORMAT}"]
    error = None
    if row["last_scanned_at"]:
        since = datetime.fromisoformat(row["last_scanned_at"].replace("Z", "+00:00")) - timedelta(days=RESCAN_OVERLAP_DAYS)
        text = run_git([*base, f"--since={since.isoformat()}"], root)
    else:
        try:
            text = run_git(base, root)
        except GitError as e:
            since = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(days=FALLBACK_DAYS)
            text = run_git([*base, f"--since={since.isoformat()}"], root)
            error = f"full history unavailable ({e}); read the last {FALLBACK_DAYS} days"
    commits = parse_log(text)
    try:
        emails = [run_git(["config", "user.email"], root, timeout=15).strip().lower()]
    except GitError:
        emails = []
    conn.execute("BEGIN")
    try:
        _store(conn, repo_id, commits)
        conn.execute(
            "UPDATE repos SET user_emails = ?, last_scanned_at = ?, last_error = ?, present = 1 WHERE id = ?",
            (json.dumps([e for e in emails if e]), now_iso, error, repo_id),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(commits)
