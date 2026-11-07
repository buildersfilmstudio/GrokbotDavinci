"""Hosted single-tenant relay; public HTTPS termination is an operator responsibility."""

import asyncio
import hashlib
import os
from pathlib import Path
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from pydantic import ValidationError
from .protocol import Claim, Completion, MAX_BODY
from .queue import Queue, QueueError


@dataclass(frozen=True)
class Config:
    client_token: str
    worker_token: str
    worker_id: str
    hosts: tuple[str, ...]
    db_path: str
    ttl: float = 20

    def validate(self):
        if (
            min(len(self.client_token), len(self.worker_token)) < 32
            or max(len(self.client_token), len(self.worker_token)) > 256
            or self.client_token == self.worker_token
        ):
            raise ValueError("Use distinct random tokens, each at least 32 characters")
        Claim(worker_id=self.worker_id)
        if not self.hosts or any(
            not h or "*" in h or "/" in h or "@" in h for h in self.hosts
        ):
            raise ValueError("Explicit exact Host values required (no wildcards)")
        if not 1 <= self.ttl <= 60:
            raise ValueError("TTL must be between 1 and 60 seconds")


class Guard:
    """No browser origins, redirects or implicit trust of forwarded headers.

    Buffer at most 64 KiB including chunked requests before dispatching to SDK/routes.
    The deployment must additionally limit connections/rate at its HTTPS ingress.
    """

    def __init__(self, app, config):
        self.app, self.config = app, config
        self.tokens = {
            role: hashlib.sha256(token.encode()).digest()
            for role, token in [
                ("client", config.client_token),
                ("worker", config.worker_token),
            ]
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = scope["headers"]

        def values(key):
            return [v.decode("latin1") for k, v in headers if k == key]

        async def reject(code, reason):
            await JSONResponse({"error": reason}, status_code=code)(
                scope, receive, send
            )

        host = values(b"host")
        if len(host) != 1 or host[0] not in self.config.hosts:
            return await reject(421, "invalid_host")
        if values(b"origin"):
            return await reject(403, "browser_origins_not_supported")
        path = scope["path"]
        if path == "/healthz" and scope["method"] == "GET":
            return await self.app(scope, receive, send)
        role = "worker" if path.startswith("/worker/") else "client"
        auth = values(b"authorization")
        supplied = (
            auth[0][7:] if len(auth) == 1 and auth[0].startswith("Bearer ") else ""
        )
        if not secrets.compare_digest(
            hashlib.sha256(supplied.encode()).digest(), self.tokens[role]
        ):
            return await reject(401, "unauthorized")
        if scope["method"] == "POST" and values(b"content-type") != [
            "application/json"
        ]:
            return await reject(415, "application_json_required")
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    msg = await receive()
                    if msg["type"] == "http.disconnect":
                        return
                    body.extend(msg.get("body", b""))
                    if len(body) > MAX_BODY:
                        return await reject(413, "request_too_large")
                    if not msg.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "body_timeout")
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def create_app(config: Config):
    config.validate()
    queue = Queue(config.db_path, config.worker_id, config.ttl)
    mcp = FastMCP(
        "GrokbotDavinci",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(config.hosts),
            allowed_origins=[],
        ),
    )

    async def query(operation):
        job_id = queue.enqueue(operation)
        try:
            async with asyncio.timeout(config.ttl + 1):
                while True:
                    result = queue.result(job_id)
                    if result is not None:
                        if not result["ok"]:
                            raise QueueError("worker_read_failed")
                        return result["result"]
                    await asyncio.sleep(0.1)
        finally:
            queue.cancel(job_id)

    @mcp.tool()
    async def resolve_version() -> dict:
        """Read the connected Mac's Resolve product/version; no project changes."""
        return await query("version")

    @mcp.tool()
    async def current_project() -> dict:
        """Read current project name and timeline count; never opens another project."""
        return await query("project")

    @mcp.tool()
    async def current_timeline() -> dict:
        """Read current timeline frame bounds and audio/video track counts."""
        return await query("timeline")

    @mcp.tool()
    async def current_media_bin() -> dict:
        """Read at most 100 current-bin clip names, never media paths or file contents."""
        return await query("media")

    async def health(request):
        return JSONResponse({"status": "ok"})

    async def claim(request):
        try:
            data = Claim.model_validate(await request.json())
            return JSONResponse({"job": queue.claim(data.worker_id)})
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except QueueError:
            return JSONResponse({"error": "wrong_worker"}, status_code=403)

    async def complete(request):
        try:
            data = Completion.model_validate(await request.json())
            status = queue.complete(
                request.path_params["job_id"],
                data.worker_id,
                data.lease,
                data.ok,
                data.result,
            )
            return JSONResponse({"status": status})
        except (ValidationError, ValueError):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        except QueueError:
            return JSONResponse({"error": "job_rejected"}, status_code=409)

    @asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield
        queue.close()

    inner = mcp.streamable_http_app()
    app = Starlette(
        routes=[
            Route("/healthz", health),
            Route("/worker/claim", claim, methods=["POST"]),
            Route("/worker/jobs/{job_id}/complete", complete, methods=["POST"]),
            Mount("/", app=inner),
        ],
        lifespan=lifespan,
    )
    app.state.queue = queue
    guarded = Guard(app, config)
    # Test/embedding handles contain no tokens; application config remains process-local.
    guarded.state = app.state
    return guarded


def app_from_env():
    os.umask(0o077)
    db = Path(os.environ.get("DAVINCI_DB", "state/relay.sqlite3"))
    db.parent.mkdir(parents=True, exist_ok=True)
    return create_app(
        Config(
            os.environ["DAVINCI_CLIENT_TOKEN"],
            os.environ["DAVINCI_WORKER_TOKEN"],
            os.environ.get("DAVINCI_WORKER_ID", "peters-mac"),
            tuple(os.environ["DAVINCI_ALLOWED_HOSTS"].split(",")),
            str(db),
        )
    )


def main():
    import uvicorn

    uvicorn.run(
        app_from_env(),
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8000")),
        access_log=False,
        proxy_headers=False,
    )
