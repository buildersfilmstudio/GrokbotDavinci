import asyncio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.testclient import TestClient
from grokbot_davinci.relay import create_app, Config

CLIENT = "c" * 40
WORKER = "w" * 40


def app(tmp_path, ttl=2):
    return create_app(
        Config(CLIENT, WORKER, "mac", ("testserver",), str(tmp_path / "q.db"), ttl)
    )


def auth(token):
    return {"Authorization": "Bearer " + token}


def test_security_and_worker_protocol(tmp_path):
    a = app(tmp_path)
    with TestClient(a) as c:
        assert c.get("/healthz").status_code == 200
        assert c.post("/worker/claim", json={"worker_id": "mac"}).status_code == 401
        assert (
            c.post(
                "/worker/claim", headers=auth(CLIENT), json={"worker_id": "mac"}
            ).status_code
            == 401
        )
        assert c.post("/mcp", headers=auth(WORKER), json={}).status_code == 401
        assert c.get("/healthz", headers={"Host": "evil.example"}).status_code == 421
        assert (
            c.get("/healthz", headers={"Origin": "https://evil.example"}).status_code
            == 403
        )
        assert (
            c.post(
                "/worker/claim", headers=auth(WORKER), json={"worker_id": "other"}
            ).status_code
            == 403
        )
        assert (
            c.post(
                "/worker/claim",
                headers=auth(WORKER),
                json={"worker_id": "mac", "eval": "x"},
            ).status_code
            == 400
        )
        assert (
            c.post(
                "/worker/claim",
                headers={**auth(WORKER), "Content-Type": "application/json"},
                content="x" * 65537,
            ).status_code
            == 413
        )
        assert (
            c.post("/worker/claim", headers=auth(WORKER), content="{}").status_code
            == 415
        )
        assert c.post(
            "/worker/claim", headers=auth(WORKER), json={"worker_id": "mac"}
        ).json() == {"job": None}
        id = a.state.queue.enqueue("version")
        job = c.post(
            "/worker/claim", headers=auth(WORKER), json={"worker_id": "mac"}
        ).json()["job"]
        body = dict(
            worker_id="mac", lease=job["lease"], ok=True, result={"version": "21"}
        )
        url = f"/worker/jobs/{id}/complete"
        assert (
            c.post(url, headers=auth(WORKER), json=body).json()["status"] == "completed"
        )
        assert (
            c.post(url, headers=auth(WORKER), json=body).json()["status"] == "duplicate"
        )
        assert (
            c.post(url, headers=auth(WORKER), json={**body, "result": {}}).status_code
            == 409
        )
        assert (
            c.post(
                "/worker/jobs/unknown/complete", headers=auth(WORKER), json=body
            ).status_code
            == 409
        )


def test_invalid_config(tmp_path):
    with pytest.raises(ValueError):
        create_app(Config(CLIENT, CLIENT, "mac", ("testserver",), str(tmp_path / "a")))
    with pytest.raises(ValueError):
        create_app(Config(CLIENT, WORKER, "mac", ("*",), str(tmp_path / "a")))


@pytest.mark.asyncio
async def test_standard_mcp_end_to_end_mock_worker(tmp_path):
    a = app(tmp_path)
    # Explicit lifespan is required for ASGI transports and the SDK session manager.
    async with a.app.router.lifespan_context(a.app):
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=auth(CLIENT)
        ) as client:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=client
            ) as (r, w, _):
                async with ClientSession(r, w) as session:
                    init = await session.initialize()
                    assert init.serverInfo.name == "GrokbotDavinci"
                    tools = await session.list_tools()
                    assert len(tools.tools) == 4

                    async def mock_worker():
                        async with httpx.AsyncClient(
                            transport=transport,
                            base_url="http://testserver",
                            headers=auth(WORKER),
                        ) as worker:
                            for _ in range(100):
                                response = await worker.post(
                                    "/worker/claim", json={"worker_id": "mac"}
                                )
                                job = response.json()["job"]
                                if job:
                                    assert job["operation"] == "version"
                                    done = await worker.post(
                                        f"/worker/jobs/{job['id']}/complete",
                                        json=dict(
                                            worker_id="mac",
                                            lease=job["lease"],
                                            ok=True,
                                            result={"version": "21-mock"},
                                        ),
                                    )
                                    assert done.status_code == 200
                                    return
                                await asyncio.sleep(0.02)
                            raise AssertionError("No job received")

                    task = asyncio.create_task(mock_worker())
                    result = await session.call_tool("resolve_version", {})
                    await task
                    assert not result.isError and "21-mock" in result.content[0].text
                    offline = await session.call_tool("current_project", {})
                    assert offline.isError and "expired" in offline.content[0].text


@pytest.mark.asyncio
async def test_chunked_limit_and_mcp_allowlist(tmp_path):
    a = app(tmp_path)
    async with a.app.router.lifespan_context(a.app):
        transport = httpx.ASGITransport(app=a)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=auth(WORKER)
        ) as c:

            async def chunks():
                yield b"x" * 32768
                yield b"x" * 32769

            response = await c.post(
                "/worker/claim",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 413
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver", headers=auth(CLIENT)
        ) as c:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=c
            ) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "execute_python", {"code": "raise RuntimeError()"}
                    )
                    assert result.isError
                    assert a.state.queue.claim("mac") is None
