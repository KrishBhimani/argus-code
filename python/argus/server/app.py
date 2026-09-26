"""FastAPI app builder + uvicorn launcher."""
from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..adapters.base import Adapter
from ..collector.first_run import FirstRunHandle
from ..pricing.types import PricingTable
from ..store.repository import Repository
from .api import ApiDeps, build_api, is_allowed_origin

logger = logging.getLogger(__name__)


@dataclass
class ServerOpts:
    pricing_table_version: str
    ingest_status: Any  # callable returning IngestStatus
    dashboard_dir: Path
    port: int
    adapters: list[Adapter]
    pricing_table: PricingTable
    host: str = "127.0.0.1"
    daemon: bool = False
    work_data_dir: Path | None = None  # set only by `argus start --work`


# Host header values that name the loopback interface. A browser only puts one
# of these in Host when the user actually typed a loopback URL -- an attacker who
# rebinds evil.com to 127.0.0.1 still sends ``Host: evil.com``, because Host
# carries the URL's *name*, not the resolved address.
_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


def is_loopback_host(host_header: str | None) -> bool:
    """True if ``Host`` names the loopback interface (any port).

    Fails closed on a missing/malformed header: HTTP/1.1 requires Host, and a
    request without one has no business reaching the dashboard.

    Port-agnostic on purpose. The port adds nothing here -- what defeats DNS
    rebinding is that the *hostname* must be a loopback literal, and a hostile
    page cannot obtain one while still believing it is same-origin with its own
    domain. A direct cross-origin fetch to ``127.0.0.1:4242`` does carry a
    loopback Host, but the response stays unreadable under the Same-Origin
    Policy because we emit no CORS headers.
    """
    if not host_header:
        return False
    h = host_header.strip().lower()
    if h.startswith("["):  # bracketed IPv6 literal, e.g. [::1]:4242
        end = h.find("]")
        if end == -1:
            return False
        hostname, rest = h[: end + 1], h[end + 1 :]
        if rest and not rest.startswith(":"):
            return False
    else:
        hostname, _, port = h.partition(":")
        if port and not port.isdigit():
            return False
    return hostname in _LOOPBACK_HOSTNAMES


class SafeStaticFiles(StaticFiles):
    """StaticFiles that 404s (never 500s) on an unservable path.

    On Windows, ``os.stat`` of a path whose segment contains ``:`` (e.g. a
    sub-agent id like ``claude_code:<uuid>/agent-<hex>`` that fell through to
    the SPA mount) raises ``OSError [WinError 123]``. Swallow any OSError and
    report "not found" so a malformed URL can never crash a request.
    """

    def lookup_path(self, path: str) -> tuple[str, Any]:
        try:
            return super().lookup_path(path)
        except OSError:
            return "", None


def build_app(repo: Repository, opts: ServerOpts) -> FastAPI:
    """Build the FastAPI app with CSRF middleware, API routes, static mount."""
    app = FastAPI(title="Argus", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {"error": "bad request", "detail": exc.errors()}, status_code=400
        )

    # DNS-rebinding guard. The Origin check below deliberately exempts GET, and
    # every data-bearing route is a GET -- so on its own it does nothing against
    # a page that rebinds its own domain to 127.0.0.1 and thereby becomes
    # same-origin with us in the browser's eyes. Requiring a loopback Host is
    # what actually keeps SECURITY.md's "untrusted browsers cannot read Argus
    # data" true. Applies to the whole app, static mount included.
    #
    # Skipped when the user deliberately bound a non-loopback interface
    # (`--host 0.0.0.0`, which prints a loud warning): there they reach the
    # dashboard by LAN IP or hostname, which this allowlist would reject.
    host_checked = opts.host in ("127.0.0.1", "::1", "localhost")

    @app.middleware("http")
    async def host_header_check(request: Request, call_next):
        if host_checked and not is_loopback_host(request.headers.get("host")):
            return JSONResponse(
                {"error": "invalid host header"}, status_code=421
            )
        return await call_next(request)

    # CSRF origin guard for state-changing requests. GETs are unrestricted
    # because the dashboard issues same-origin GETs without an Origin
    # header in some setups; POSTs from browsers always carry Origin.
    @app.middleware("http")
    async def csrf_origin_check(request: Request, call_next):
        method = request.method
        if method in ("GET", "HEAD", "OPTIONS"):
            return await call_next(request)
        # Allow API-prefixed paths to enforce the check; non-API endpoints
        # don't exist on this server today, but apply uniformly.
        if not is_allowed_origin(request.headers.get("origin"), opts.port):
            return JSONResponse(
                {"error": "cross-origin requests not allowed"}, status_code=403
            )
        return await call_next(request)

    api = build_api(
        repo,
        ApiDeps(
            pricing_table_version=opts.pricing_table_version,
            ingest_status=opts.ingest_status,
            adapters=opts.adapters,
            pricing_table=opts.pricing_table,
            daemon=opts.daemon,
        ),
    )
    app.include_router(api)

    if opts.work_data_dir is not None:
        from ..work.api import build_work_router  # trial: imported only when on

        app.include_router(build_work_router(opts.work_data_dir))

    # Static dashboard mount LAST so /api/* routes win.
    #
    # The dashboard is a client-routed SPA: a deep link such as /sessions/<id>
    # exists only in the browser, so a refresh must serve index.html and let
    # the client router take over. ``StaticFiles(html=True)`` only does that at
    # directory roots, hence the 404 handler below. Paths whose last segment
    # has an extension (assets) and /api/* misses stay real 404s.
    if opts.dashboard_dir.exists():
        index_html = opts.dashboard_dir / "index.html"

        @app.exception_handler(StarletteHTTPException)
        async def _spa_fallback(request: Request, exc: StarletteHTTPException):
            path = request.url.path
            if (
                exc.status_code == 404
                and request.method == "GET"
                and not path.startswith("/api/")
                and "." not in path.rsplit("/", 1)[-1]
                and index_html.is_file()
            ):
                return FileResponse(index_html, media_type="text/html")
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None)
            )

        app.mount(
            "/",
            SafeStaticFiles(directory=str(opts.dashboard_dir), html=True),
            name="dashboard",
        )

    return app


def serve_blocking(app: FastAPI, *, host: str, port: int) -> None:
    """Run uvicorn in the current thread until SIGINT/SIGTERM or .should_exit.

    We install our own signal handlers *before* calling ``server.run()`` so that
    Ctrl+C produces a clean shutdown instead of the spurious
    ``KeyboardInterrupt`` + ``CancelledError`` traceback uvicorn otherwise dumps.

    Two layers conspire to print that traceback, and our handler defuses both:

      1. ``asyncio.run()`` installs its own SIGINT handler that cancels the main
         task and raises ``KeyboardInterrupt`` mid-loop -- but only when the
         current handler is ``signal.default_int_handler`` (see CPython
         ``asyncio/runners.py``). Installing ours first means asyncio leaves
         SIGINT alone.
      2. uvicorn's ``capture_signals()`` re-raises the captured signal after a
         graceful shutdown. With ours as the restored handler, that re-raise
         lands on a no-op instead of becoming a ``KeyboardInterrupt``.

    During ``server.run()`` uvicorn's own ``handle_exit`` is the active handler
    (it sets ``should_exit`` / ``force_exit`` on first/second signal); ours is
    the benign backstop on either side of that window.
    """
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",  # quiet access log; user opts in via --verbose
        access_log=False,
    )
    server = uvicorn.Server(config)

    def _request_exit(_signum: int, _frame: Any) -> None:
        if server.should_exit:
            server.force_exit = True
        server.should_exit = True

    previous: dict[int, Any] = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _request_exit)
        except (ValueError, OSError):
            # Not the main thread, or signal unsupported on this platform.
            pass

    try:
        server.run()
    finally:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass
