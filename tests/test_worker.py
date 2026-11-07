import json, time
import httpx
import pytest
from grokbot_davinci.worker import Worker, validate_url


def test_url_policy():
    assert validate_url("https://relay.example/") == "https://relay.example"
    for url in [
        "http://relay.example",
        "https://u:p@relay.example",
        "https://relay.example/x",
        "https://relay.example?q=x",
    ]:
        with pytest.raises(ValueError):
            validate_url(url)


def test_worker_retries_receipt_not_execution():
    calls = []
    posts = []

    def handler(request):
        if request.url.path == "/worker/claim":
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "12345678-1234-4234-8234-123456789abc",
                        "operation": "version",
                        "lease": "l" * 40,
                        "expires_at": time.time() + 15,
                    }
                },
            )
        posts.append(json.loads(request.content))
        if len(posts) == 1:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json={"status": "duplicate"})

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://relay.example"
    ) as c:
        w = Worker(
            c,
            "mac",
            reader=lambda op, timeout: (
                calls.append(op) or {"ok": True, "result": {"version": "21"}}
            ),
        )
        assert w.run_once()
    assert calls == ["version"] and posts[0] == posts[1]


def test_worker_rejects_unrecognized_operation_and_expired_jobs():
    for op, expiry in [("eval", time.time() + 15), ("version", time.time() - 1)]:

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "job": {
                        "id": "12345678-1234-4234-8234-123456789abc",
                        "operation": op,
                        "lease": "l" * 40,
                        "expires_at": expiry,
                    }
                },
            )

        with httpx.Client(
            transport=httpx.MockTransport(handler), base_url="https://relay.example"
        ) as c:
            with pytest.raises(ValueError):
                Worker(
                    c, "mac", reader=lambda *_: pytest.fail("must not execute")
                ).run_once()


def test_worker_does_not_follow_redirects_or_accept_path_injection():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "job": {
                    "id": "../../mcp",
                    "operation": "version",
                    "lease": "l" * 40,
                    "expires_at": time.time() + 15,
                }
            },
        )

    with httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://relay.example"
    ) as c:
        with pytest.raises(ValueError):
            Worker(
                c, "mac", reader=lambda *_: pytest.fail("must not execute")
            ).run_once()


def test_worker_offline_no_execution():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"job": None})
        ),
        base_url="https://relay.example",
    ) as c:
        assert not Worker(
            c, "mac", reader=lambda *_: pytest.fail("must not execute")
        ).run_once()
