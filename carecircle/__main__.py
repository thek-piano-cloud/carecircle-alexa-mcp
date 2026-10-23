"""python -m carecircle  ->  MCP server on http://127.0.0.1:8780/mcp (+ simulator at /).

If CARECIRCLE_TOKEN is set, /mcp requires ``Authorization: Bearer <token>``
(the demo pages and /api are served only with CARECIRCLE_DEMO=1, the default on localhost).  Bind address: CARECIRCLE_HOST / CARECIRCLE_PORT.
A background watcher marks doses that are two hours late as missed and tells the family (CARECIRCLE_WATCH_SECONDS, 0 = off).
"""
import hmac
import os
import threading

import uvicorn
from starlette.responses import JSONResponse

from . import service
from .mcp_server import mcp


class BearerAuth:
    """Tiny ASGI middleware: constant-time token check for the MCP endpoint."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            given = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(given, b"Bearer " + self.token):
                host = dict(scope["headers"]).get(b"host", b"127.0.0.1").decode()
                base = os.environ.get("CARECIRCLE_PUBLIC_URL", f"{scope.get('scheme', 'http')}://{host}").rstrip("/")
                challenge = f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
                resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": challenge})
                return await resp(scope, receive, send)
        return await self.app(scope, receive, send)


PUBLIC_PATHS = ("/mcp", "/healthz", "/.well-known/")


class DemoOff:
    """With CARECIRCLE_DEMO=0 only the MCP endpoint, health check and auth metadata are served (no demo pages, no /api)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope["path"].startswith(PUBLIC_PATHS):
            return await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)


def build_app():
    app = mcp.streamable_http_app()
    # demo pages and /api are on by default only when bound to localhost
    local = mcp.settings.host in ("127.0.0.1", "localhost", "::1")
    if os.environ.get("CARECIRCLE_DEMO", "1" if local else "0") != "1":
        app = DemoOff(app)
    token = os.environ.get("CARECIRCLE_TOKEN")
    return BearerAuth(app, token) if token else app


def _watch(every: float) -> None:
    stop = threading.Event()
    while not stop.wait(every):
        try:
            service.sweep_missed()
        except Exception:  # noqa: BLE001 - the watcher must never take the server down
            pass


def main() -> None:
    every = float(os.environ.get("CARECIRCLE_WATCH_SECONDS", "60"))
    if every > 0:
        threading.Thread(target=_watch, args=(every,), daemon=True).start()
    uvicorn.run(build_app(), host=mcp.settings.host, port=mcp.settings.port, log_level="info")


if __name__ == "__main__":
    main()
