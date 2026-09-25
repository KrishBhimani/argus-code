"""``argus`` CLI — typer subcommand tree."""
from __future__ import annotations

import logging
import shutil
import sys
import webbrowser
from importlib import resources
from pathlib import Path

import typer

import argus.detectors  # noqa: F401  — triggers @register side-effects

from .pricing.load import load_pricing_table, user_pricing_dir
from .pricing.refresh import diff_pricing, fetch_litellm_table
from .scaffold.scaffolder import scaffold_project
from .scaffold.snapshot import (
    secret_files_in,
    snapshot_candidates,
    snapshot_template,
)
from .scaffold.storage import RESERVED_TEMPLATE_NAMES, list_templates, resolve_template
from .server.app import ServerOpts, build_app, serve_blocking
from .store.db import open_db
from .store.repository import Repository

logger = logging.getLogger("argus")
app = typer.Typer(help="Local-first dashboard for coding-agent costs", no_args_is_help=True)
pricing_app = typer.Typer(help="Pricing-table operations")
indexing_app = typer.Typer(help="Manage transcript indexing (powers full-text search and sub-agent Task-given)")


@indexing_app.callback()
def _indexing_group(ctx: typer.Context) -> None:
    # `search` is the old name, kept as a hidden alias so existing scripts and
    # muscle memory keep working. Nudge toward the new name when invoked that way.
    if ctx.info_name == "search":
        typer.echo("Note: `argus search` has been renamed to `argus indexing`; the old name still works.", err=True)


# Back-compat alias: keep this binding so `search_*` command references resolve.
search_app = indexing_app
app.add_typer(pricing_app, name="pricing")
app.add_typer(indexing_app, name="indexing")
app.add_typer(indexing_app, name="search", hidden=True)
claude_app = typer.Typer(help="Scaffold and manage .claude/ project setups")
template_app = typer.Typer(help="Manage scaffolding templates")
claude_app.add_typer(template_app, name="template")
app.add_typer(claude_app, name="claude")
daemon_app = typer.Typer(help="Background ingestion + detector daemon (argusd)")
app.add_typer(daemon_app, name="daemon")
work_app = typer.Typer(help="Work-analysis trial: git + session links (writes only ~/.argus/work.db)")
app.add_typer(work_app, name="work")


def _setup_logging(quiet: bool = False, verbose: bool = False) -> None:
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _dashboard_dir() -> Path:
    """Path of the bundled dashboard-dist (wheel) or repo-root one (dev)."""
    try:
        traversable = resources.files("argus") / "dashboard-dist"
        candidate = Path(str(traversable))
        if candidate.exists():
            return candidate
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return Path(__file__).resolve().parents[2] / "dashboard-dist"


def _default_data_dir() -> Path:
    return Path.home() / ".argus"


@app.command()
def start(
    port: int = typer.Option(4242, "-p", "--port"),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help=(
            "Bind host. Default 127.0.0.1 (loopback only). Pass 0.0.0.0 to "
            "expose on LAN — see SECURITY.md."
        ),
    ),
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
    quiet: bool = typer.Option(False, "--quiet"),
    verbose: bool = typer.Option(False, "--verbose"),
    work: bool = typer.Option(
        False, "--work", help="Also run the work-analysis trial (writes only ~/.argus/work.db)"
    ),
) -> None:
    """Start the watcher, ingester, and dashboard server."""
    _setup_logging(quiet=quiet, verbose=verbose)

    from .core.runtime import CoreRuntime, NoAdaptersError
    from .daemon import pidfile

    daemon_pid = pidfile.running_pid(data_dir)
    if daemon_pid is not None:
        read_only = True
        logger.info("argusd %d active — dashboard is read-only.", daemon_pid)
    else:
        read_only = False
        stale_pid = pidfile.read(data_dir)
        if stale_pid is not None:
            logger.info(
                "Stale argusd PID file (PID %d is not argusd) — ignoring.", stale_pid
            )

    runtime = CoreRuntime(data_dir, read_only=read_only)
    try:
        runtime.start()
    except NoAdaptersError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)

    collector = None
    if work:
        from .adapters.claude_code.adapter import ClaudeCodeAdapter
        from .work.collector import WorkCollector

        collector = WorkCollector(data_dir, ClaudeCodeAdapter()).start()

    dash_dir = _dashboard_dir()
    server_app = build_app(
        runtime.repo,
        ServerOpts(
            pricing_table_version=runtime.pricing_table.version,
            ingest_status=runtime.ingest_status,
            dashboard_dir=dash_dir,
            port=port,
            host=host,
            adapters=runtime.adapters,
            pricing_table=runtime.pricing_table,
            daemon=read_only,
        ),
    )
    display_host = "localhost" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}"
    logger.info("Argus running at %s", url)
    if host in ("0.0.0.0", "::"):
        logger.warning(
            "⚠ WARNING: bound to all network interfaces. Anyone on your LAN "
            "can read your prompt history, transcripts, and project paths."
        )
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        pass

    try:
        serve_blocking(server_app, host=host, port=port)
    except KeyboardInterrupt:
        pass
    finally:
        if collector is not None and not collector.stop():
            logger.warning("work: collector still running at shutdown; it writes only work.db")
        runtime.stop()
        logger.info("Argus stopped.")


@pricing_app.command("refresh")
def pricing_refresh(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Fetch the latest LiteLLM pricing and (after confirm) save it to <data-dir>/pricing."""
    _setup_logging()
    out_dir = user_pricing_dir(data_dir)
    current = load_pricing_table(user_dir=out_dir)
    typer.echo("Fetching latest pricing from LiteLLM...")
    fresh = fetch_litellm_table()
    diff = diff_pricing(current, fresh)
    typer.echo(
        f"Added: {len(diff.added)}, Removed: {len(diff.removed)}, "
        f"Changed: {len(diff.changed)}, Unchanged: {len(diff.unchanged)}"
    )
    for k in diff.changed:
        typer.echo(f"  ~ {k}: {current.models[k].model_dump()} -> {fresh.models[k].model_dump()}")
    for k in diff.added:
        typer.echo(f"  + {k}: {fresh.models[k].model_dump()}")
    for k in diff.removed:
        typer.echo(f"  - {k}")
    if not (diff.added or diff.changed or diff.removed):
        typer.echo("Nothing to update.")
        return
    if not typer.confirm("Apply?"):
        typer.echo("Cancelled.")
        return
    # User data dir, not the installed package: site-packages is wiped by an
    # upgrade and may be read-only. load_pricing_table picks the newest
    # version across this dir and the bundled tables.
    out_file = out_dir / f"{fresh.version}.json"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file.write_text(fresh.model_dump_json(indent=2), encoding="utf-8")
    except OSError as e:
        typer.echo(f"Could not write {out_file}: {e}", err=True)
        raise typer.Exit(1) from e
    typer.echo(f"Wrote {out_file}")


@search_app.command("status")
def search_status(data_dir: Path = typer.Option(_default_data_dir(), "--data-dir")) -> None:
    db = open_db(data_dir / "argus.db")
    repo = Repository(db)
    enabled = repo.is_search_indexing_enabled()
    segs = repo.segment_stats()
    typer.echo(f"Status:   {'ENABLED' if enabled else 'disabled'}")
    typer.echo(f"Indexed:  {segs['total']:,} segments across {segs['sessions']} sessions")
    if not enabled and segs["total"] > 0:
        typer.echo("Note:     existing segments stay on disk but are hidden from search")
        typer.echo("          until you re-enable. Use 'argus indexing clear' to wipe them.")


@search_app.command("enable")
def search_enable(data_dir: Path = typer.Option(_default_data_dir(), "--data-dir")) -> None:
    db = open_db(data_dir / "argus.db")
    repo = Repository(db)
    if repo.is_search_indexing_enabled():
        typer.echo("Already enabled.")
        segs = repo.segment_stats()
        typer.echo(f"Indexed: {segs['total']:,} segments across {segs['sessions']} sessions")
        return
    repo.set_search_indexing_enabled(True)
    typer.echo("Enabled. Run `argus start` to backfill — historical sessions will")
    typer.echo("index in the background. New sessions are indexed automatically.")


@search_app.command("disable")
def search_disable(data_dir: Path = typer.Option(_default_data_dir(), "--data-dir")) -> None:
    db = open_db(data_dir / "argus.db")
    repo = Repository(db)
    repo.set_search_indexing_enabled(False)
    segs = repo.segment_stats()
    typer.echo("Disabled.")
    if segs["total"] > 0:
        typer.echo(f"{segs['total']:,} existing segments are still on disk but hidden")
        typer.echo("from search. Run `argus indexing clear` to wipe them.")


@search_app.command("clear")
def search_clear(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
    yes: bool = typer.Option(False, "-y", "--yes"),
) -> None:
    db = open_db(data_dir / "argus.db")
    repo = Repository(db)
    segs = repo.segment_stats()
    if segs["total"] == 0:
        typer.echo("Nothing to clear (0 indexed segments).")
        repo.set_search_indexing_enabled(False)
        return
    if not yes and not typer.confirm(f"Delete {segs['total']:,} indexed segments?"):
        typer.echo("Cancelled.")
        return
    before_bytes = repo.db_size_bytes()
    repo.clear_all_segments()
    repo.set_search_indexing_enabled(False)
    repo.vacuum()
    after_bytes = repo.db_size_bytes()
    freed = max(0, before_bytes - after_bytes)

    def fmt(n: int) -> str:
        if n >= 1024 * 1024:
            return f"{n / 1024 / 1024:.1f} MB"
        if n >= 1024:
            return f"{n / 1024:.1f} KB"
        return f"{n} B"

    typer.echo(f"Deleted {segs['total']:,} segments.")
    typer.echo(f"Freed {fmt(freed)} on disk (DB now {fmt(after_bytes)}).")


def _render_tree(paths: list[Path], root: Path) -> str:
    rels = sorted(p.relative_to(root).as_posix() for p in paths)
    seen: set[str] = set()
    lines: list[str] = []
    for r in rels:
        parts = r.split("/")
        for depth, part in enumerate(parts):
            prefix = "/".join(parts[: depth + 1])
            if prefix in seen:
                continue
            seen.add(prefix)
            is_dir = depth < len(parts) - 1
            lines.append("  " * depth + part + ("/" if is_dir else ""))
    return "\n".join(lines)


@claude_app.command("init")
def claude_init(
    path: Path = typer.Argument(Path("."), help="Target project directory"),
    template: str = typer.Option("default", "--template", help="Template name"),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing files (never CLAUDE.md)"
    ),
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Scaffold a .claude/ setup (and CLAUDE.md) into a project."""
    try:
        template_dir = resolve_template(template, data_dir)
    except ValueError:
        # Path-like name (e.g. ../../x) — rejected before it touches the disk.
        typer.echo(
            f"Invalid template name '{template}'. "
            "Use a plain name: letters, digits, dot, dash, underscore.",
            err=True,
        )
        raise typer.Exit(code=1)
    except KeyError:
        avail = ", ".join(list_templates(data_dir)) or "(none)"
        typer.echo(f"Unknown template '{template}'. Available: {avail}", err=True)
        raise typer.Exit(code=1)

    dest = path.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    result = scaffold_project(template_dir, dest, force=force)

    for p in result.created:
        typer.echo(f"  + {p.relative_to(dest).as_posix()}")
    for p, reason in result.skipped:
        typer.echo(f"  - {p.relative_to(dest).as_posix()}  ({reason})")

    touched = result.created + [p for p, _ in result.skipped]
    if touched:
        typer.echo("\nResulting layout:")
        typer.echo(_render_tree(touched, dest))
    typer.echo(f"\nScaffolded '{template}' into {dest}")
    typer.echo(f"{len(result.created)} created, {len(result.skipped)} skipped.")


@template_app.command("list")
def template_list(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """List available template names (user templates shadow bundled)."""
    names = list_templates(data_dir)
    if not names:
        typer.echo("No templates found.")
        return
    for n in names:
        typer.echo(n)


@template_app.command("create")
def template_create(
    name: str = typer.Argument(..., help="Template name to create"),
    path: Path = typer.Option(Path("."), "--path", help="Project dir to snapshot"),
    all_subdirs: bool = typer.Option(
        False, "--all", help="Include every subfolder without prompting"
    ),
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Snapshot the current project's .claude/ into a reusable user template."""
    if name in RESERVED_TEMPLATE_NAMES:
        typer.echo(f"'{name}' is reserved, pick another name", err=True)
        raise typer.Exit(code=1)

    project = path.resolve()
    if not (project / ".claude").is_dir():
        typer.echo(f"No .claude/ directory found in {project}", err=True)
        raise typer.Exit(code=1)

    candidates = snapshot_candidates(project)
    if all_subdirs:
        included = candidates
    else:
        included = [
            d for d in candidates if typer.confirm(f"Include {d}/?", default=True)
        ]

    try:
        target = snapshot_template(
            project, name, data_dir, include_subdirs=included
        )
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Created template '{name}' at {target}")
    # Say what was withheld. A credential filter that drops files silently is
    # its own bug — the user needs to know the template is deliberately
    # incomplete, and why.
    withheld = secret_files_in(project / ".claude", included)
    if withheld:
        typer.echo(
            "\nLeft out of the template (credentials, machine-local config, or "
            "links out of the folder): "
            + ", ".join(withheld)
            + "\nTemplates get copied into every project you scaffold, so these "
            "are never snapshotted."
        )


@app.command()
def wipe(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
    yes: bool = typer.Option(False, "-y", "--yes"),
) -> None:
    """Delete all local Argus data."""
    if not yes and not typer.confirm(f"Delete {data_dir}?"):
        typer.echo("Cancelled.")
        return
    if data_dir.exists():
        shutil.rmtree(data_dir)
    typer.echo(f"Deleted {data_dir}")


@daemon_app.command("run", hidden=True)
def daemon_run(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Blocking foreground daemon. Used by 'daemon start' and OS services."""
    from .daemon.service import DaemonAlreadyRunning, run_foreground

    try:
        run_foreground(data_dir)
    except DaemonAlreadyRunning as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1)


@daemon_app.command("start")
def daemon_start(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Start argusd in the background (detached) and write its PID file."""
    from .daemon import pidfile
    from .daemon.process import spawn_daemon, wait_for_pidfile

    existing = pidfile.running_pid(data_dir)
    if existing is not None:
        typer.echo(f"daemon already running, PID {existing}")
        raise typer.Exit(code=0)
    stale = pidfile.read(data_dir)
    if stale is not None:
        typer.echo(f"Clearing stale PID file (PID {stale} is not argusd).")
        pidfile.remove(data_dir)

    spawn_daemon(data_dir)
    pid = wait_for_pidfile(data_dir)
    if pid is None:
        typer.echo(
            "daemon did not start within timeout — check ~/.argus/argusd.log",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"argusd started, PID {pid}")


@daemon_app.command("stop")
def daemon_stop(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Stop argusd gracefully (removes the PID file)."""
    from .daemon import pidfile
    from .daemon.process import stop_daemon

    if stop_daemon(data_dir):
        typer.echo("argusd stopped.")
    elif pidfile.check(pidfile.read_record(data_dir)) == pidfile.UNVERIFIED:
        # stop_daemon declined to signal a PID it can't identify; don't claim
        # nothing is running.
        typer.echo(
            f"argusd not stopped: PID {pidfile.read(data_dir)} is alive but can't be "
            f"verified as argusd, so it was left alone. If it is an older argusd, stop "
            f"that process yourself; if it isn't argusd, delete {pidfile.path(data_dir)}.",
            err=True,
        )
        raise typer.Exit(code=1)
    else:
        typer.echo("argusd is not running.")


@daemon_app.command("restart")
def daemon_restart(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Stop then start argusd."""
    from .daemon.process import spawn_daemon, stop_daemon, wait_for_pidfile

    stop_daemon(data_dir)
    spawn_daemon(data_dir)
    pid = wait_for_pidfile(data_dir)
    if pid is None:
        typer.echo("daemon did not restart within timeout.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"argusd restarted, PID {pid}")


@daemon_app.command("status")
def daemon_status(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
) -> None:
    """Report whether argusd is running, its PID, and uptime."""
    import time as _time

    from .daemon import pidfile
    from .daemon.logging import log_path

    pid = pidfile.running_pid(data_dir)
    if pid is None:
        stale = pidfile.read(data_dir)
        if stale is not None:
            typer.echo(f"argusd: not running (stale PID file for {stale}).")
        else:
            typer.echo("argusd: not running.")
        raise typer.Exit(code=0)

    try:
        started = pidfile.path(data_dir).stat().st_mtime
        uptime_s = max(0, int(_time.time() - started))
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        uptime = f"{h}h{m:02d}m{s:02d}s"
    except OSError:
        uptime = "unknown"

    if pidfile.live_pid(data_dir) is None:
        typer.echo(
            f"argusd: possibly running (PID {pid} is alive but can't be verified "
            f"as argusd). If it isn't, delete {pidfile.path(data_dir)}."
        )
        raise typer.Exit(code=0)
    typer.echo(f"argusd: running (PID {pid}, uptime {uptime}).")
    typer.echo(f"Log: {log_path(data_dir)}")


@daemon_app.command("logs")
def daemon_logs(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
    lines: int = typer.Option(40, "-n", "--lines", help="Lines to show"),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow new output"),
) -> None:
    """Tail ~/.argus/argusd.log (rotation-aware with --follow)."""
    from .daemon.logging import follow as follow_log
    from .daemon.logging import log_path, tail_lines

    p = log_path(data_dir)
    if not p.exists():
        typer.echo(f"No log file yet at {p}.")
        raise typer.Exit(code=0)

    for line in tail_lines(p, lines):
        typer.echo(line.rstrip("\n"))

    if follow:
        try:
            follow_log(p, emit=lambda ln: typer.echo(ln))
        except KeyboardInterrupt:
            pass


@work_app.command("scan")
def work_scan(
    data_dir: Path = typer.Option(_default_data_dir(), "--data-dir"),
    claude_root: Path = typer.Option(Path.home() / ".claude", "--claude-root", hidden=True),
) -> None:
    """Run one work-analysis pass now."""
    from .adapters.claude_code.adapter import ClaudeCodeAdapter
    from .work.collector import run_pass

    r = run_pass(data_dir, ClaudeCodeAdapter(claude_root))
    typer.echo(
        f"{r.repos} repo(s), {r.commits} commit(s) read; links: "
        f"{r.links['exact']} exact, {r.links['coauthored']} co-authored, {r.links['inferred']} inferred"
    )
    for where, err in r.errors.items():
        typer.echo(f"  ! {where}: {err}", err=True)


@work_app.command("status")
def work_status(data_dir: Path = typer.Option(_default_data_dir(), "--data-dir")) -> None:
    """Show what the work-analysis trial has collected."""
    from .work.db import get_meta, open_work_db

    conn = open_work_db(data_dir)
    typer.echo(f"last scan: {get_meta(conn, 'last_scan_at') or 'never'}")
    for r in conn.execute("SELECT display_name, root, present, last_error FROM repos ORDER BY display_name"):
        flag = "" if r["present"] else "  (repo gone, archived)"
        err = f"  ! {r['last_error']}" if r["last_error"] else ""
        typer.echo(f"  {r['display_name']:<28} {r['root']}{flag}{err}")
    conn.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
