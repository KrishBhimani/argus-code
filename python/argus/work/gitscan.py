"""Read local git history with the git CLI (no network), store every author."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..store.repository import normalize_project_path

_RS, _US, _GS = "\x1e", "\x1f", "\x1d"
LOG_FORMAT = (
    "%x1e%H%x1f%P%x1f%an%x1f%ae%x1f%aI%x1f%cI%x1f%s%x1f"
    "%(trailers:key=Co-authored-by,valueonly,separator=%x1d)"
)
AGENT_TRAILER = re.compile(r"claude|noreply@anthropic\.com", re.IGNORECASE)
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


def run_git(args: list[str], cwd: Path | str, timeout: float = 60, input: str | None = None) -> str:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, input=input,
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


def normalize_remote(url: str) -> str | None:
    """host/owner/repo, lowercased, for any URL form of one hosted repo; None for a local path.

    Credentials in the URL (a user and token before the host) are dropped, so a token is never stored.
    """
    u = url.strip()
    m = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::\d+)?/(.+)$", u, re.I)
    if m and not u.lower().startswith("file:"):
        host, path = m.groups()
    else:
        m = re.match(r"^(?:[^@/]+@)?([^/:]+\.[^/:]+):(?!/)(.+)$", u)   # scp form: git@host:owner/repo
        if not m:
            return None
        host, path = m.groups()
    path = re.sub(r"(\.git)?/*$", "", path.strip("/"))
    return f"{host}/{path}".lower() if path else None


def project_key(root: str) -> str:
    """Which project a folder belongs to: its remote, else its first commit, else the folder itself.

    Clones and worktrees of one repo share a key; forks keep their own remote and stay apart.
    """
    try:
        remote = normalize_remote(run_git(["config", "--get", "remote.origin.url"], root, timeout=15))
    except GitError:
        remote = None
    if remote:
        return f"remote:{remote}"
    try:
        firsts = sorted(run_git(["rev-list", "--max-parents=0", "HEAD"], root, timeout=30).split())
    except GitError:
        firsts = []
    if firsts:
        return f"root:{firsts[0]}"
    return f"path:{normalize_project_path(root)}"


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


def _ref_tips(root: str) -> list[str]:
    """Every ref's commit plus HEAD: what a repo's history is reachable from."""
    out = run_git(["for-each-ref", "--format=%(objectname)"], root, timeout=15)
    tips = {line.strip() for line in out.splitlines() if line.strip()}
    try:
        tips.add(run_git(["rev-parse", "HEAD"], root, timeout=15).strip())
    except GitError:  # empty repo, no HEAD yet
        pass
    return sorted(t for t in tips if t)


def scan_repo(conn: sqlite3.Connection, repo_id: int, root: str, now_iso: str) -> int:
    row = conn.execute("SELECT last_scanned_at, ref_tips, project_key FROM repos WHERE id = ?", (repo_id,)).fetchone()
    base = ["log", "--all", "--no-color", "--no-renames", "--numstat", f"--format={LOG_FORMAT}"]
    error = None
    tips = _ref_tips(root)
    old = json.loads(row["ref_tips"]) if row["ref_tips"] else None
    if old is not None and set(old) == set(tips):
        text = ""  # no ref moved: nothing new to read
    elif old is not None:
        # Commits reachable from the refs now but not from the refs last time, whatever
        # their dates (a pull of older work, a fetched branch). Tips go in via stdin so
        # a repo with many refs can't overflow the command line.
        try:
            text = run_git([*base, "--stdin"], root, input="".join(f"^{t}\n" for t in old))
        except GitError:  # an old tip was garbage-collected (rebase + gc): read it all again
            text = run_git(base, root)
    else:
        try:
            text = run_git(base, root)
        except GitError as e:
            since = datetime.fromisoformat(now_iso.replace("Z", "+00:00")) - timedelta(days=FALLBACK_DAYS)
            text = run_git([*base, f"--since={since.isoformat()}"], root)
            error = f"full history unavailable ({e}); read the last {FALLBACK_DAYS} days"
    commits = parse_log(text)
    # Re-derived when refs move (cheap) or on first sight; a remote change with no new commit waits for one.
    key = row["project_key"] if row["project_key"] and old is not None and set(old) == set(tips) else project_key(root)
    try:
        emails = [run_git(["config", "user.email"], root, timeout=15).strip().lower()]
    except GitError:
        emails = []
    conn.execute("BEGIN")
    try:
        _store(conn, repo_id, commits)
        conn.execute(
            "UPDATE repos SET user_emails = ?, last_scanned_at = ?, last_error = ?, present = 1, ref_tips = ?, project_key = ? WHERE id = ?",
            (json.dumps([e for e in emails if e]), now_iso, error, json.dumps(tips), key, repo_id),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(commits)


def _resolves(root: str, ref: str) -> bool:
    try:
        run_git(["rev-parse", "--verify", "-q", ref + "^{commit}"], root, timeout=15)
        return True
    except GitError:
        return False


def default_ref(root: str) -> str | None:
    """The branch work lands on, first that resolves: origin/HEAD (a pruned target falls
    through), origin/main, origin/master, then local main / master for a repo with no remote.
    origin/HEAD is only set by clone; after git init + push, local main would count unpushed
    commits as shipped."""
    try:
        head = run_git(["symbolic-ref", "-q", "refs/remotes/origin/HEAD"], root, timeout=15).strip()
    except GitError:   # not a symbolic ref: no origin, or origin/HEAD never set
        head = ""
    for ref in (head, "refs/remotes/origin/main", "refs/remotes/origin/master", "refs/heads/main", "refs/heads/master"):
        if ref and _resolves(root, ref):
            return ref
    return None


def dirty_count(root: str) -> int:
    """Changed or untracked files in this folder (each worktree has its own)."""
    return sum(1 for line in run_git(["status", "--porcelain"], root, timeout=15).splitlines() if line.strip())


def remote_as_of(root: str) -> str | None:
    """When this repo last fetched (FETCH_HEAD's mtime in the shared git dir), else None.

    Argus never fetches: pushed / shipped are only as fresh as the user's last fetch or pull."""
    common = Path(run_git(["rev-parse", "--git-common-dir"], root, timeout=15).strip())
    head = (common if common.is_absolute() else Path(root) / common) / "FETCH_HEAD"
    if not head.exists():
        return None
    return datetime.fromtimestamp(head.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def merged_branches(root: str, ref: str) -> list[str]:
    """Remote branches whose tip is on the default branch (a release branch fast-forwarded into
    main), by name without 'origin/'. The default branch itself and HEAD are left out."""
    base = ref.rsplit("/", 1)[-1]
    out = run_git(["branch", "-r", "--merged", ref, "--format=%(refname:short)"], root, timeout=30)
    names = {line.strip().split("/", 1)[1] for line in out.splitlines() if "/" in line.strip()}
    return sorted(n for n in names if n not in (base, "HEAD"))


def _patch_ids(root: str, diff_text: str) -> set[str]:
    if not diff_text.strip():
        return set()
    return {line.split()[0] for line in run_git(["patch-id", "--stable"], root, input=diff_text).splitlines() if line.strip()}


def squash_landed(root: str, ref: str, commits: set[str]) -> set[str]:
    """Commits of branches whose whole change is on the default branch as one commit (a squash
    merge, whatever its title). Only branches containing one of `commits` (tied to a session) are
    tried; the session's commit need not be the tip, since later commits often follow it."""
    branch_tips: set[str] = set()
    todo = sorted(commits)
    for i in range(0, len(todo), 200):   # --contains ORs; chunks keep the command line short
        args = ["for-each-ref", "--format=%(objectname)"]
        for sha in todo[i:i + 200]:
            args += ["--contains", sha]
        branch_tips.update(line.strip() for line in run_git([*args, "refs/heads", "refs/remotes"], root).splitlines()
                           if line.strip())
    landed: set[str] = set()
    main_ids: dict[str, set[str]] = {}   # per merge-base: patch-ids of the default branch since then
    for tip in sorted(branch_tips):
        try:
            base = run_git(["merge-base", ref, tip], root, timeout=15).strip()
        except GitError:
            continue
        if not base or base == tip:
            continue
        mine = _patch_ids(root, run_git(["diff", base, tip], root))
        if not mine:
            continue
        if base not in main_ids:
            main_ids[base] = _patch_ids(root, run_git(["log", "-p", "--no-merges", f"{base}..{ref}"], root))
        if mine <= main_ids[base]:
            landed.update(run_git(["rev-list", f"{base}..{tip}"], root).split())
    return landed


def refresh_git_state(conn: sqlite3.Connection, repo_id: int, root: str, now_iso: str) -> None:
    """Per stored commit: on the default branch? on any remote? Re-read only when refs moved
    (a fast-forward merge moves them without new commits) or rows are missing. The folder's
    dirty count and last fetch time are read every pass. Raises GitError; the caller records it."""
    row = conn.execute("SELECT ref_tips, reach_tips, default_ref, has_remote FROM repos WHERE id = ?",
                       (repo_id,)).fetchone()
    missing = conn.execute(
        """SELECT 1 FROM commits c WHERE c.repo_id = ? AND NOT EXISTS
             (SELECT 1 FROM commit_reach r WHERE r.repo_id = c.repo_id AND r.sha = c.sha
                     AND r.reachable IS NOT NULL AND r.landed IS NOT NULL)
           LIMIT 1""",
        (repo_id,)).fetchone()
    ref = default_ref(root)
    remote = bool(run_git(["remote"], root, timeout=15).strip())
    dirty, fetched = dirty_count(root), remote_as_of(root)
    reread = (missing is not None or row["reach_tips"] != row["ref_tips"]
              or ref != row["default_ref"] or int(remote) != (row["has_remote"] or 0))
    if reread:   # git runs before the transaction, so no write lock is held while it works
        on_default = set(run_git(["rev-list", ref], root).split()) if ref else set()
        pushed = set(run_git(["rev-list", "--remotes"], root).split()) if remote else None
        # An amended or rebased commit stays in `commits` but no ref reaches it any more.
        reachable = set(run_git(["rev-list", "--all"], root).split())
        # A squash merge or a fast-forwarded release branch leaves the session's commits off main:
        # match them by content, and note which remote branches main already contains.
        linked = {r["sha"] for r in conn.execute("SELECT sha FROM session_commits WHERE repo_id = ?", (repo_id,))}
        candidates = {s for s in linked if s in reachable and s not in on_default}
        landed = squash_landed(root, ref, candidates) if ref and candidates else set()
        merged = merged_branches(root, ref) if ref and remote else []
        shas = [r["sha"] for r in conn.execute("SELECT sha FROM commits WHERE repo_id = ?", (repo_id,))]
    conn.execute("BEGIN")
    try:
        if reread:
            conn.execute("DELETE FROM commit_reach WHERE repo_id = ?", (repo_id,))
            conn.executemany(
                "INSERT INTO commit_reach (repo_id, sha, on_default, pushed, reachable, landed) VALUES (?, ?, ?, ?, ?, ?)",
                [(repo_id, s, int(s in on_default), None if pushed is None else int(s in pushed), int(s in reachable),
                  int(s in landed)) for s in shas])
            conn.execute("UPDATE repos SET merged_branches = ? WHERE id = ?", (json.dumps(merged), repo_id))
        conn.execute(
            """UPDATE repos SET default_ref = ?, has_remote = ?, reach_tips = ref_tips, dirty = ?,
                 dirty_checked_at = ?, remote_as_of = ? WHERE id = ?""",
            (ref, int(remote), dirty, now_iso, fetched, repo_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
