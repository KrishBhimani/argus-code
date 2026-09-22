"""`template create` must not snapshot credentials into a reusable template.

docs/SECURITY_AUDIT_2026-07-31.md #8. `_safe_top_files` copied every top-level
file in `.claude/`, excluding only `history.jsonl` and `*.local.json`. A real
`~/.claude/` holds `.credentials.json` (OAuth tokens) and `.env`.

There is no remote attacker here — it is a footgun, but the dangerous path is
the *inviting* one. `--path` defaults to `.`, so a user who has tuned their
global setup and runs `argus claude template create mysetup` from their home
directory copies their tokens into ~/.argus/templates/<name>/, and from there
into every project scaffolded from it — projects people commit to git.

The guard is deliberately paired with a loud report rather than a silent drop:
a heuristic that quietly omits a file the user wanted is its own bug, so the
CLI prints what it withheld.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from argus.scaffold.snapshot import secret_files_in, snapshot_template


def _home_shaped_claude(tmp_path: Path) -> Path:
    """A `.claude/` shaped like a real ~/.claude — secrets and all."""
    proj = tmp_path / "home"
    claude = proj / ".claude"
    claude.mkdir(parents=True)
    # Secrets that must never be snapshotted.
    (claude / ".credentials.json").write_text('{"oauth":"tok"}', encoding="utf-8")
    (claude / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-secret", encoding="utf-8")
    # Legitimate config that must still be snapshotted.
    (claude / "settings.json").write_text('{"theme":"dark"}', encoding="utf-8")
    (claude / "CLAUDE.md").write_text("# my setup", encoding="utf-8")
    return proj


SECRET_NAMES = [
    ".credentials.json",
    "credentials.json",
    ".env",
    ".env.local",
    "my-api-key.txt",
    "auth-token.json",
    "client_secret.json",
]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_secret_files_are_recognised(name: str):
    assert secret_files_in.__module__  # sanity: imported
    from argus.scaffold.snapshot import is_secret_file

    assert is_secret_file(name), f"{name} should be treated as a secret"


@pytest.mark.parametrize("name", ["settings.json", "CLAUDE.md", "notes.md", "tools.md"])
def test_ordinary_files_are_not_flagged(name: str):
    from argus.scaffold.snapshot import is_secret_file

    assert not is_secret_file(name), f"{name} was wrongly treated as a secret"


def test_snapshot_omits_credentials_but_keeps_config(tmp_path: Path):
    proj = _home_shaped_claude(tmp_path)

    target = snapshot_template(proj, "mysetup", tmp_path / "data", include_subdirs=[])

    copied = {p.name for p in (target / ".claude").iterdir()}
    assert ".credentials.json" not in copied, "OAuth tokens were snapshotted"
    assert ".env" not in copied, "env secrets were snapshotted"
    # Fail-closed must not mean fail-always.
    assert copied == {"settings.json", "CLAUDE.md"}


def test_secret_files_in_reports_what_was_withheld(tmp_path: Path):
    """The CLI needs this to tell the user, rather than silently dropping."""
    proj = _home_shaped_claude(tmp_path)

    withheld = secret_files_in(proj / ".claude")

    assert set(withheld) == {".credentials.json", ".env"}


def test_snapshot_does_not_dereference_a_symlinked_top_file(tmp_path: Path):
    """A link out of the tree must not pull its target's contents in."""
    proj = tmp_path / "proj"
    claude = proj / ".claude"
    claude.mkdir(parents=True)
    (claude / "settings.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "id_rsa"
    outside.write_text("PRIVATE KEY", encoding="utf-8")
    try:
        (claude / "notes.md").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"symlinks not permitted here: {exc}")

    target = snapshot_template(proj, "t", tmp_path / "data", include_subdirs=[])

    assert not (target / ".claude" / "notes.md").exists()
    assert (target / ".claude" / "settings.json").exists()


# ─── H7: chosen subfolders are walked with the same rules, at every depth ──
#
# REGRESSION: the secret filter covered only top-level files; chosen subfolders
# were copied whole with shutil.copytree, which follows symlinks/junctions.
# A snapshot contained .claude/agents/credentials.json, .claude/hooks/.env,
# .claude/hooks/settings.local.json and .claude/skills/id_rsa (the last via a
# junction to a directory outside the project) while the CLI said "secrets are
# never snapshotted".

MORE_SECRET_NAMES = [
    "id_rsa", "id_ed25519", "id_ecdsa", "server.pem", "tls.key", "cert.p12", "cert.pfx",
    "token.json", "auth.json", ".netrc", ".npmrc", ".pypirc", "oauth_creds.json",
    "service-account.json", ".git-credentials", ".pgpass",
]


@pytest.mark.parametrize("name", MORE_SECRET_NAMES)
def test_more_credential_files_are_recognised(name: str):
    from argus.scaffold.snapshot import is_secret_file

    assert is_secret_file(name), f"{name} should be treated as a secret"


@pytest.mark.parametrize("name", ["id_rsa.pub", "keyboard.md", "tokens.md", "author.md"])
def test_public_or_ordinary_names_are_not_flagged(name: str):
    from argus.scaffold.snapshot import is_secret_file

    assert not is_secret_file(name), f"{name} was wrongly treated as a secret"


def _project_with_nested_secrets(tmp_path: Path) -> Path:
    proj = tmp_path / "proj"
    claude = proj / ".claude"
    (claude / "agents" / "deep").mkdir(parents=True)
    (claude / "hooks").mkdir()
    (claude / "agents" / "reviewer.md").write_text("# agent", encoding="utf-8")
    (claude / "agents" / "credentials.json").write_text("{}", encoding="utf-8")
    (claude / "agents" / "deep" / "id_rsa").write_text("PRIVATE", encoding="utf-8")
    (claude / "hooks" / "pre.sh").write_text("echo hi", encoding="utf-8")
    (claude / "hooks" / ".env").write_text("K=V", encoding="utf-8")
    (claude / "hooks" / "settings.local.json").write_text("{}", encoding="utf-8")
    return proj


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_nested_secrets_are_not_snapshotted(tmp_path: Path):
    proj = _project_with_nested_secrets(tmp_path)

    target = snapshot_template(proj, "t", tmp_path / "data", include_subdirs=["agents", "hooks"])

    assert _files(target / ".claude") == {"agents/reviewer.md", "hooks/pre.sh"}


def test_withheld_report_comes_from_the_same_walk(tmp_path: Path):
    proj = _project_with_nested_secrets(tmp_path)

    withheld = secret_files_in(proj / ".claude", include_subdirs=["agents", "hooks"])

    assert set(withheld) == {
        "agents/credentials.json", "agents/deep/id_rsa", "hooks/.env", "hooks/settings.local.json",
    }


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # pragma: no cover - env dependent
        pytest.skip(f"symlinks not permitted here: {exc}")


def test_nested_symlink_out_of_the_tree_is_not_followed(tmp_path: Path):
    proj = _project_with_nested_secrets(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "notes.md").write_text("private notes", encoding="utf-8")
    _symlink(proj / ".claude" / "agents" / "linked", outside)
    _symlink(proj / ".claude" / "agents" / "file.md", outside / "notes.md")

    target = snapshot_template(proj, "t", tmp_path / "data", include_subdirs=["agents"])

    assert _files(target / ".claude") == {"agents/reviewer.md"}
    withheld = secret_files_in(proj / ".claude", include_subdirs=["agents"])
    assert {"agents/linked", "agents/file.md"} <= set(withheld)


def test_symlinked_subfolder_is_not_followed(tmp_path: Path):
    proj = _project_with_nested_secrets(tmp_path)
    outside = tmp_path / "keys"
    outside.mkdir()
    (outside / "readme.md").write_text("outside", encoding="utf-8")
    _symlink(proj / ".claude" / "skills", outside)

    target = snapshot_template(proj, "t", tmp_path / "data", include_subdirs=["skills"])

    assert not (target / ".claude" / "skills").exists()
    assert "skills" in secret_files_in(proj / ".claude", include_subdirs=["skills"])


def test_cli_reports_nested_withheld_files(tmp_path: Path):
    from typer.testing import CliRunner

    from argus.cli import app

    proj = _project_with_nested_secrets(tmp_path)
    res = CliRunner().invoke(app, ["claude", "template", "create", "t", "--path", str(proj),
                                   "--all", "--data-dir", str(tmp_path / "data")])
    assert res.exit_code == 0, res.output
    assert "hooks/.env" in res.output and "agents/deep/id_rsa" in res.output


def test_reparse_point_directory_is_treated_as_a_link(tmp_path: Path, monkeypatch):
    """Windows junctions: Path.is_junction is 3.12+, so 3.11 relies on the
    reparse-point attribute. Simulated here (junctions can't be made on POSIX)."""
    import os as _os

    from argus.scaffold import snapshot

    proj = _project_with_nested_secrets(tmp_path)
    junction = proj / ".claude" / "agents" / "deep"
    real_lstat = _os.lstat

    class _St:
        def __init__(self, st):
            self._st = st
            self.st_file_attributes = 0x400

        def __getattr__(self, name):
            return getattr(self._st, name)

    monkeypatch.setattr(snapshot.os, "lstat",
                        lambda p, *a, **k: _St(real_lstat(p)) if Path(p) == junction else real_lstat(p))
    monkeypatch.setattr(Path, "is_junction", lambda self: False, raising=False)

    target = snapshot_template(proj, "t", tmp_path / "data", include_subdirs=["agents"])

    assert not (target / ".claude" / "agents" / "deep").exists()
    assert "agents/deep" in secret_files_in(proj / ".claude", include_subdirs=["agents"])
