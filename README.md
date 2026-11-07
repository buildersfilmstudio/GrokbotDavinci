# GrokbotDavinci

An original, **read-only first** MCP connector for DaVinci Resolve Studio on a Mac.
No Tailscale, VPN, inbound Mac port, public Resolve API, or video upload required.
This is a working local/mock-tested implementation, **not a deployed service**.

```text
Grok Bot -- HTTPS + CLIENT token --> hosted relay /mcp
                                      ^
                                      | outbound HTTPS polling + WORKER token
                                      |
                                   Mac worker --> official local Resolve scripting API
```

The hosted relay implements standard MCP Streamable HTTP using the official
Python SDK. The Mac worker is a separate process and opens **no listening socket**.
Video stays on the Mac; requested project, timeline and clip names leave it.
Anyone holding the client token can request these reads while the worker is running.
This is not an official Blackmagic or Grok Bot product.

## Implemented tools

| Tool | Read-only output |
|---|---|
| `resolve_version` | Product and version |
| `current_project` | Current project name, timeline count |
| `current_timeline` | Current timeline name, frame bounds, track counts |
| `current_media_bin` | Current bin name and at most 100 clip names |

These tools never open, switch, save or modify projects. No arbitrary shell,
Python execution, raw API calls, paths, media bytes or filesystem browsing tools.
There are **no editing tools or edit-enable flag in v0.1**. The violin sheet-music
workflow requires a separately reviewed, explicitly authorized edit capability;
this version establishes the secure connection and read-only inspection first.

## 1. Choose hosting (not provisioned by this repository)

Use one always-on Linux container/VM with a persistent disk and managed HTTPS
termination (for example a container host or an existing VPS behind its HTTPS
ingress). The service must support streamed HTTP, maintain connections for at
least 65 seconds, and preserve the `Authorization` and `Host` headers.

Run **one replica, one Uvicorn worker**, one SQLite database and one registered Mac.
No serverless/ephemeral filesystem deployment or autoscaling. A managed database,
identity model and coordination would be needed before multi-tenant/multi-replica use.
No account, deployment, paid resources, DNS or certificates are created by this code.
Choose the host/budget with the owner before provisioning anything.

## 2. Provision distinct credentials

Generate **two independent** `secrets.token_urlsafe(32)` values using your secret
manager or local provisioning tool. Do not commit them, put them in command-line
arguments, share them in chat or use the same token for both roles.

Set relay environment through the hosting provider's secret store:

```text
DAVINCI_CLIENT_TOKEN=<client-only secret>
DAVINCI_WORKER_TOKEN=<different worker-only secret>
DAVINCI_WORKER_ID=peters-mac
DAVINCI_ALLOWED_HOSTS=resolve-relay.example.com,resolve-relay.example.com:443
DAVINCI_DB=/state/relay.sqlite3
```

Replace the example host with the **actual** relay hostname. Host values are exact,
including ports when supplied. No wildcard hosts. The HTTPS proxy must preserve
that host. Protection is configured through the SDK's public
`TransportSecuritySettings` constructor, never disabled or monkeypatched.
The app also rejects wrong Hosts and all browser Origins before dispatching requests.
A bare `GET /healthz` returns only liveness, not queue/project information.

Keep the worker secret only in an owner-readable file on the Mac (`chmod 600`).
The client token belongs in Grok Bot's custom connector **Authorization header**;
never put it in the URL. Token rotation requires updating the corresponding
endpoint config and restarting the relay/worker. There is no refresh/OAuth flow.

## 3. Run the relay

Python 3.12+:

```sh
python3 -m venv .venv
.venv/bin/pip install .
# Provide environment above using your secret manager, not copied shell history.
.venv/bin/grokbot-davinci-relay
```

The CLI binds `127.0.0.1:8000` by default for use behind a local HTTPS ingress.
`PORT` changes that port. A supplied Dockerfile binds only the **hosted relay's**
container interface on port 8000; expose it only through your managed HTTPS ingress,
not plaintext internet HTTP. Never expose a Resolve or Mac listener.

Configure ingress connection/request limits, rate limits, maximum request body
64 KiB and timeout of at least 65 seconds. Do not log Authorization headers or
request/response bodies. Encrypt storage/backups if project names are sensitive.
Only health endpoints should be unauthenticated. Don't put the service behind
browser-only SSO: MCP and worker clients use bearer headers.

The relay process defaults to owner-only new files; ensure the persistent disk is
owner-only, including pre-existing databases. SQLite contains project-read results
and hashed lease capabilities. Results are retained up to 24 hours, pruned on queue
activity; delete the local database only when deliberately resetting all receipts.
Maximum 16 active jobs / 4096 retained receipts. A full queue fails closed.

## 4. Install the worker on the Mac (separate approval required)

Use an installed package/wheel from this repository's reviewed revision; no need
to clone a repository onto the Mac. For example build a wheel on a trusted build
machine with `pip wheel --no-deps . --wheel-dir dist`, transfer the reviewed wheel,
then install it in a dedicated Python virtual environment on the Mac.

Use a maintained Python matching the Mac's architecture and supported by its
Resolve build (Python 3.14 was locally verified against Peter's Studio 21.1 in
setup work; this new adapter has **not** been run against that Mac).
Resolve Studio must be running and allow **Local** external scripting. Use
Blackmagic's installed `Developer/Scripting/README.txt` as the authoritative API
reference. Typical macOS environment:

```sh
export PYTHONPATH='/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules'
export RESOLVE_SCRIPT_API='/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting'
export RESOLVE_SCRIPT_LIB='/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so'
# Token file has already been provisioned securely; no secret in argv.
/path/to/venv/bin/grokbot-davinci-worker \
  --relay https://resolve-relay.example.com \
  --token-file /private/owner-only/worker-token \
  --worker-id peters-mac
```

Keep clocks synchronized (TTL verification uses wall-clock timestamps). Worker polls
once per second while idle, with 5-second backoff after transport errors. Stop it
with Ctrl-C. It is **not auto-installed as a login service**; a reviewed LaunchAgent
can be added later. It does not depend on or change any older Tailscale setup.

Native API calls run in a spawned child with an 8-second maximum; timed-out children
are terminated, not retried. A timed-out job is never requeued. This is deliberately
at-most-once execution: loss of a claim or completion may lose a read, but cannot
trigger a replay. Repeating an MCP read creates a fresh bounded read request.

## 5. Connect Grok Bot (after testing and approval)

Add a custom remote MCP connector:

```text
URL: https://resolve-relay.example.com/mcp
Header name: Authorization
Header value: Bearer <CLIENT token from your secret manager>
```

Use the actual deployed URL and approve the account's add-connector confirmation.
Do not use a localhost/Mac address. Test initialization, `resolve_version`, then
`current_project` with the Mac worker running. Disconnect/stop worker when idle.
A failed/offline worker expires a request in about 20 seconds and returns an MCP
error, never a fabricated success. `/healthz` is not proof of Mac connectivity.

## Security model and constraints

- Single tenant/worker, separate constant-time-compared client/worker credentials.
  Worker credentials cannot call MCP; client credentials cannot claim jobs.
- Closed operation enum, strict fields, finite expiry, UUID job IDs, random one-use
  claim capabilities. Owner checks on claim/completion; expired work never requeues.
- Completion repeats are idempotent **only for the identical receipt** and correct
  unexpired capability. Altered, unclaimed, unknown, stale or wrong-worker receipts fail.
- TLS verified; redirects disabled for worker; HTTPS-only relay URLs. Token file
  must be owner-only. No authorization values or lease capabilities in application logs.
- Authenticated requests are still untrusted: size, operation, TTL and queue limits
  apply. Bodies are limited even without Content-Length. Ingress rate/connection
  controls are required for internet deployment; this is not a DDoS protection service.
- Relay compromise or a stolen worker token can forge read results; stolen client
  token can request allowed metadata. Mac process/user compromise is out of scope.
  No cryptographic device attestation or end-to-end encrypted job payloads.
- Read results are a snapshot, not a lock on Resolve. The user can change the
  current project while a read is queued. There is intentionally no edit surface.

## Tests and real-world limitations

```sh
python3 -m venv .venv
.venv/bin/pip install '.[test]'
.venv/bin/pytest -q
```

Tests include durable job receipts, wrong credentials/owners/leases, expiry and
non-requeue, duplicate/conflicting completions, queue/result limits, closed API
allowlist, wrong Host/Origin, oversized requests, read-only adapter mocks, worker
receipt-only retries, invalid/expired jobs, and a real SDK client initialize → list
tools → read → offline error through an in-process ASGI transport and mock worker.
They do not require Resolve, a paid host or Tailscale.

**Not yet tested:** live Resolve adapter on macOS, actual internet HTTPS deployment,
provider proxy compatibility, token provisioning into Grok Bot, real session
reconnects or production load. Do not label the connector connected until those
steps pass. CI matrix covers Python 3.12/3.13/3.14; local test execution is recorded
in the PR, not implied for every platform.

## References

- Official Python MCP SDK: https://github.com/modelcontextprotocol/python-sdk
- Public SDK security settings: https://py.sdk.modelcontextprotocol.io/api/mcp/server/transport_security/
- Blackmagic Resolve developer resources: https://www.blackmagicdesign.com/developer/
- Authoritative version-specific scripting docs ship with Resolve under
  `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/`.

Original implementation, MIT licensed. No upstream community Resolve MCP code copied.
