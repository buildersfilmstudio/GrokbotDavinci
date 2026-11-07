"""Mac worker: makes outbound HTTPS requests only, never opens a listening socket."""

import argparse
import json
import multiprocessing
import os
import stat
from pathlib import Path
import time
from urllib.parse import urlsplit
import httpx
from .adapter import ResolveAdapter
from .protocol import Job, MAX_BODY, MAX_RESULT
from .edits import Plan, prepare
from .editor import Editor, EditPolicy


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


def _read(operation, sender, payload, policy):
    try:
        if operation == "edit" and not policy.enabled:
            raise PermissionError("edits_disabled")
        if operation == "prepare":
            sender.send(dict(ok=True, result=prepare(payload)))
            return
        adapter = ResolveAdapter.connect()
        if operation == "edit":
            result = Editor(adapter.resolve, policy).execute(
                Plan.model_validate(payload)
            )
        elif operation == "inspect":
            result = adapter.inspect(payload)
        else:
            result = adapter.execute(operation)
        encoded = json.dumps(result, allow_nan=False).encode()
        if len(encoded) > MAX_RESULT:
            raise ValueError("too_large")
        sender.send(dict(ok=True, result=result))
    except Exception:
        sender.send(
            dict(
                ok=False,
                result={
                    "error": "operation_failed",
                    "mutation_outcome": "unknown_or_partial_do_not_retry"
                    if operation == "edit"
                    else "not_applicable",
                },
            )
        )
    finally:
        sender.close()


def read_bounded(operation, timeout, payload=None, policy=None):
    """A stuck native scripting call cannot wedge the network worker indefinitely."""
    ctx = multiprocessing.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    process = ctx.Process(
        target=_read, args=(operation, sender, payload or {}, policy or EditPolicy())
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            return dict(
                ok=False,
                result={
                    "error": "resolve_timeout",
                    "mutation_outcome": "unknown_or_partial_do_not_retry"
                    if operation == "edit"
                    else "not_applicable",
                },
            )
        return receiver.recv()
    except EOFError:
        return dict(
            ok=False,
            result={
                "error": "resolve_unavailable",
                "mutation_outcome": "unknown_or_partial_do_not_retry"
                if operation == "edit"
                else "not_applicable",
            },
        )
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
    def __init__(
        self, client, worker_id, reader=read_bounded, clock=time.time, policy=None
    ):
        self.policy = policy or EditPolicy()
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
        if job.operation == "edit" and not self.policy.enabled:
            result = {"ok": False, "result": {"error": "worker_edits_disabled"}}
        elif job.operation in ("edit", "inspect", "prepare"):
            # Backup + import + readback is one finite, at-most-once operation.
            # Only project copying gets a15s ceiling; every other native call stays8s.
            bound = (
                15
                if job.operation == "edit"
                and job.payload.get("action", {}).get("kind") == "copy_project"
                else 8
            )
            result = self.reader(
                job.operation, min(bound, remaining - 1), job.payload, self.policy
            )
        else:
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
    parser.add_argument(
        "--session-file",
        help="Private signed Neon session JSON; worker stops before expiry",
    )
    parser.add_argument(
        "--allow-edits",
        action="store_true",
        help="Still requires one-use local approval for EVERY command",
    )
    parser.add_argument(
        "--state", help="Owner-only local approval/working-copy SQLite file"
    )
    parser.add_argument("--import-root", action="append", default=[])
    parser.add_argument("--output-root", action="append", default=[])
    args = parser.parse_args()
    policy = EditPolicy(
        args.allow_edits,
        args.state or "",
        tuple(args.import_root),
        tuple(args.output_root),
    )
    if args.allow_edits:
        if not args.state:
            raise SystemExit("--state required with --allow-edits")
        state = Path(args.state)
        state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if state.parent.stat().st_mode & 0o077:
            raise SystemExit("State directory must be owner-only")
        if state.exists() and state.stat().st_mode & 0o077:
            raise SystemExit("State file must be owner-only")
        os.umask(0o077)
    url = validate_url(args.relay)
    path = Path(args.token_file)
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode) or mode & 0o077:
        raise SystemExit("Token file must be a regular owner-only file (chmod 600)")
    token = path.read_text().strip()
    if not 32 <= len(token) <= 256:
        raise SystemExit("Invalid worker token")
    headers = {"Authorization": "Bearer " + token}
    expires_at = None
    if args.session_file:
        session_path = Path(args.session_file)
        if session_path.is_symlink() or session_path.stat().st_mode & 0o077:
            raise SystemExit("Session file must be private and not a symlink")
        grant = json.loads(session_path.read_text())
        expires_at = float(grant["expires_at"])
        if not time.time() + 12 < expires_at <= time.time() + 1801:
            raise SystemExit("Session expired or invalid")
        headers["X-Worker-Session"] = grant["session"]
    with httpx.Client(
        base_url=url,
        headers=headers,
        timeout=12,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        worker = Worker(client, args.worker_id, policy=policy)
        while expires_at is None or time.time() + 12 < expires_at:
            try:
                worker.run_once()
                time.sleep(5)
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
