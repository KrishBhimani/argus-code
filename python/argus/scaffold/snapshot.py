"""Snapshot a project's .claude/ into a named user template (`template create`)."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .storage import (
    RESERVED_TEMPLATE_NAMES,
    user_templates_dir,
    validate_template_name,
)

# Never snapshot these — session/history/cache, or machine-local config.
_EXCLUDED_DIRS = {
    "projects", "todos", "shell-snapshots", "statsig", "logs", "ide",
    "__pycache__", ".DS_Store",
}
_EXCLUDED_TOP_FILE_SUFFIXES = (".local.json",)
_EXCLUDED_TOP_FILE_NAMES = {"history.jsonl"}
_IGNORED_NAMES = {"__pycache__", ".DS_Store"}

# Credentials must never land in a template. A real ~/.claude/ holds
# `.credentials.json` (OAuth tokens) and `.env`, and `--path` defaults to `.`,
# so snapshotting from your home directory is both easy and inviting — the
# result then gets copied into every scaffolded project, which people commit.
_SECRET_FILE_NAMES = {
    ".credentials.json", "credentials.json", ".env",
    # Private keys and credential stores commonly left next to tooling.
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "token.json", "auth.json", "oauth_creds.json",
    ".netrc", ".npmrc", ".pypirc", ".git-credentials", ".pgpass",
}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")
# Substring match on the stem. Deliberately narrow: a heuristic that silently
# drops a file the user wanted is its own bug, which is why callers surface
# `secret_files_in()` to the user rather than omitting things quietly.
_SECRET_STEM_MARKERS = (
    "credential", "secret", "password", "api-key", "api_key", "apikey",
    "service-account", "service_account",
)
_SECRET_STEM_SUFFIXES = ("-token", "_token", "-key", "_key")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def is_secret_file(name: str) -> bool:
    """True if a filename looks like it carries a credential."""
    lower = name.lower()
    if lower in _SECRET_FILE_NAMES or lower.startswith(".env"):
        return True
    if lower.endswith(_SECRET_SUFFIXES):
        return True
    stem = lower.rsplit(".", 1)[0] if "." in lower else lower
    if any(m in stem for m in _SECRET_STEM_MARKERS):
        return True
    return any(stem.endswith(s) for s in _SECRET_STEM_SUFFIXES)


def _is_link(p: Path) -> bool:
    """Symlink, NTFS junction, or any other reparse point — never followed.

    ``Path.is_junction`` is 3.12+; on 3.11 fall back to the reparse-point
    attribute ``os.lstat`` reports on Windows.
    """
    if p.is_symlink():
        return True
    is_junction = getattr(p, "is_junction", None)
    if is_junction is not None and is_junction():
        return True
    try:
        attrs = getattr(os.lstat(p), "st_file_attributes", 0)
    except OSError:
        return True  # can't tell what it is: don't copy it
    return bool(attrs & _FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass
class SnapshotPlan:
    """What ``template create`` copies and what it deliberately leaves out.

    ``files`` and ``withheld`` are paths relative to ``.claude/`` (POSIX
    separators). One walk builds both, so the CLI's "left out" message always
    matches what was actually copied.
    """

    files: list[str] = field(default_factory=list)
    withheld: list[str] = field(default_factory=list)


def _top_file_withheld(p: Path) -> bool:
    return is_secret_file(p.name) or any(
        p.name.endswith(s) for s in _EXCLUDED_TOP_FILE_SUFFIXES
    )


def plan_snapshot(claude: Path, include_subdirs: list[str] | tuple[str, ...] = ()) -> SnapshotPlan:
    """Walk ``claude`` (top-level files + chosen subfolders, every depth)."""
    plan = SnapshotPlan()
    if not claude.is_dir():
        return plan
    for p in sorted(claude.iterdir()):
        # Links first: a link out of the tree must not have its target's
        # contents pulled in (copying dereferences).
        if _is_link(p):
            # A linked *chosen* subfolder is reported by _walk below.
            if p.name not in include_subdirs:
                if not p.is_dir():
                    plan.withheld.append(p.name)
            continue
        if not p.is_file() or p.name in _EXCLUDED_TOP_FILE_NAMES:
            continue
        (plan.withheld if _top_file_withheld(p) else plan.files).append(p.name)
    for sub in include_subdirs:
        _walk(claude, claude / sub, plan)
    return plan


def _walk(claude: Path, d: Path, plan: SnapshotPlan) -> None:
    rel = d.relative_to(claude).as_posix()
    if _is_link(d):
        plan.withheld.append(rel)
        return
    if not d.is_dir():
        return
    for p in sorted(d.iterdir()):
        rel_p = p.relative_to(claude).as_posix()
        if p.name in _IGNORED_NAMES or p.name.endswith(".pyc"):
            continue
        if _is_link(p):
            plan.withheld.append(rel_p)
        elif p.is_dir():
            _walk(claude, p, plan)
        elif p.is_file():
            # *.local.json is machine-local config (often holds tokens/paths).
            if is_secret_file(p.name) or p.name.endswith(".local.json"):
                plan.withheld.append(rel_p)
            else:
                plan.files.append(rel_p)


def secret_files_in(claude: Path, include_subdirs: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Paths under ``claude`` that snapshotting will withhold (credentials,
    machine-local config, links) — top level plus the chosen subfolders."""
    return sorted(plan_snapshot(claude, include_subdirs).withheld)


def snapshot_candidates(project_dir: Path) -> list[str]:
    """Subfolder names under ``project_dir/.claude/`` eligible for snapshotting."""
    claude = project_dir / ".claude"
    if not claude.is_dir():
        return []
    return sorted(
        p.name
        for p in claude.iterdir()
        if p.is_dir() and p.name not in _EXCLUDED_DIRS
    )


def snapshot_template(
    project_dir: Path, name: str, data_dir: Path, *, include_subdirs: list[str]
) -> Path:
    """Copy chosen ``.claude/`` subfolders + safe top-level files into a template.

    Raises ``ValueError`` if ``name`` is reserved, the template already exists,
    or there is no ``.claude/`` directory to snapshot. Returns the new template dir.
    """
    validate_template_name(name)
    if name in RESERVED_TEMPLATE_NAMES:
        raise ValueError(f"'{name}' is reserved, pick another name")
    claude = project_dir / ".claude"
    if not claude.is_dir():
        raise ValueError(f"no .claude/ directory found in {project_dir}")

    target_root = user_templates_dir(data_dir) / name
    if target_root.exists():
        raise ValueError(
            f"template '{name}' already exists; delete it or choose another name"
        )

    dest_claude = target_root / ".claude"
    dest_claude.mkdir(parents=True)

    # Copy file by file from one walk that never follows links and skips
    # secrets at every depth (shutil.copytree followed symlinks/junctions and
    # only the top level was filtered).
    for rel in plan_snapshot(claude, include_subdirs).files:
        dst = dest_claude / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(claude / rel, dst, follow_symlinks=False)
    return target_root
