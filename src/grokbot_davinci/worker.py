"""Mac worker: makes outbound HTTPS requests only, never opens a listening socket."""

import argparse
import json
import multiprocessing
import stat
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from .adapter import ResolveAdapter
from .protocol import Job, MAX_BODY, MAX_RESULT


def validate_url(url, allow_loopback_http=False):
    parsed = urlsplit(url)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or not parsed.hostname
    ):
        raise ValueError(
            "Relay must be an origin URL without credentials, path or query"
        )
    if parsed.scheme != "https" and not (
        allow_loopback_http
        and parsed.scheme == "http"
        and parsed.hostname == "127.0.0.1"
    ):
        raise ValueError("HTTPS required (explicit loopback HTTP is for tests only)")
    return url.rstrip("/")


def _read(operation, sender):
    try:
        result = ResolveAdapter.connect().execute(operation)
        encoded = json.dumps(result, allow_nan=False).encode()
        if len(encoded) > MAX_RESULT:
            raise ValueError("too_large")
        sender.send(dict(ok=True, result=result))
    except Exception:
        sender.send(dict(ok=False, result={"error": "resolve_read_failed"}))
    finally:
        sender.close()


def read_bounded(operation, timeout):
    """A stuck native scripting call cannot wedge the network worker indefinitely."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_read, args=(operation, sender))
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            return dict(ok=False, result={"error": "resolve_timeout"})
        return receiver.recv()
    except EOFError:
        return dict(ok=False, result={"error": "resolve_unavailable"})
    finally:
        receiver.close()
        process.join(timeout=0.2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
        if process.is_alive():
            process.kill()
            process.join()


class Worker:
    def __init__(self, client, worker_id, reader=read_bounded, clock=time.time):
        self.client, self.worker_id, self.reader, self.clock = (
            client,
            worker_id,
            reader,
            clock,
        )

    def run_once(self):
        # Buffered by httpx; reject a misconfigured/hostile relay before JSON processing.
        with self.client.stream(
            "POST", "/worker/claim", json={"worker_id": self.worker_id}
        ) as r:
            r.raise_for_status()
            raw = bytearray()
            for chunk in r.iter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise ValueError("oversized_claim")
        payload = json.loads(raw)
        if set(payload) != {"job"}:
            raise ValueError("invalid_claim")
        if payload["job"] is None:
            return False
        job = Job.model_validate(payload["job"])
        remaining = job.expires_at - self.clock()
        if remaining <= 1 or remaining > 61:
            raise ValueError("invalid_job_expiry")
        result = self.reader(job.operation, min(8, remaining - 1))
        if len(json.dumps(result, allow_nan=False).encode()) > MAX_RESULT:
            result = dict(ok=False, result={"error": "result_too_large"})
        completion = dict(worker_id=self.worker_id, lease=job.lease, **result)
        # One execution only. Transport retry repeats its receipt, never the Resolve call.
        for attempt in range(2):
            try:
                r = self.client.post(f"/worker/jobs/{job.id}/complete", json=completion)
                if r.status_code == 409:
                    return True  # expired/rejected; never replay execution
                r.raise_for_status()
                return True
            except httpx.TransportError:
                if attempt:
                    raise
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--relay", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--worker-id", default="peters-mac")
    args = parser.parse_args()
    url = validate_url(args.relay)
    path = Path(args.token_file)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise SystemExit("Token file must be a regular owner-only file (chmod 600)")
    token = path.read_text().strip()
    if not 32 <= len(token) <= 256:
        raise SystemExit("Invalid worker token")
    with httpx.Client(
        base_url=url,
        headers={"Authorization": "Bearer " + token},
        timeout=12,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        worker = Worker(client, args.worker_id)
        while True:
            try:
                worked = worker.run_once()
                time.sleep(0.2 if worked else 1)
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise SystemExit(
                        "Worker authorization rejected; check provisioning"
                    ) from None
                print("Relay rejected request; retrying in 5 seconds", flush=True)
                time.sleep(5)
            except (httpx.TransportError, ValueError):
                print(
                    "Relay unavailable or invalid job; retrying in 5 seconds",
                    flush=True,
                )
                time.sleep(5)
