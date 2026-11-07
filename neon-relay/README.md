# Neon Free relay: v0.4 scoped editing deployment

This is the **separate hosted relay for Neon Functions**, not the original Python
SQLite relay. It runs Node.js 24 and uses a dedicated Lakebase Postgres project.
The Python Mac adapter implements 14 locally approved editing operations; this
relay now exposes `prepare_edit` and `execute_edit` alongside five inspection tools.
The seven-tool live SDK discovery and401 auth rejection were verified. Native
synthetic editing tests and remaining gaps are recorded in the root README. Do not
claim full native Fusion/audio/render parity from tool discovery or mock tests.

## Verified scope

Official MCP SDK WebStandardStreamableHTTPServerTransport, stateless JSON responses.
`resolve_version`, `current_project`, `current_timeline`, `current_media_bin`, and
`inspect_resolve`, `prepare_edit`, `execute_edit`. Single Peter-owned client, worker and database. No registration,
public tools, arbitrary SQL, shell, filesystem browsing, or Mac inbound listener.

Separate client/worker bearer tokens live only in owner-readable files and Function
secret environment. Constant-time fixed-size SHA256 comparisons reject invalid
credentials **before body parsing, Postgres, queue or SDK work**. Exact URL/Host
checks and Origin rejection remain enabled. Requests max64KiB/results max32KiB.
Authenticated MCP requests have a shared durable 600/day cap; active queued work
max16; retained receipts max4096. The native HTTPS URL is still reachable, and
invalid traffic can consume Function invocations/CPU before our 401. Repository
privacy is not ingress protection. No zero-abuse or guaranteed-availability claim.
Neon docs do not document a free customer-configurable native-URL WAF/allowlist;
no gateway/paid feature was silently added.

## Free budget and intentional sessions

Verified official Free plan: database 100CUh/month, Functions 10 active/400 waiting
capacity-hours and 1M invocations/month; shared project outbound transfer 5GB.
Use a dedicated Free organization project; never upgrade automatically.

Database fixed0.25CU with provider-mandatory idle suspension. Set project quotas
at creation: 1,296,000 endpoint-active seconds (360h, at fixed0.25CU =90CUh),
4,000,000,000 transfer bytes and100,000,000 logical bytes. Quotas can intentionally
suspend the connector before the free allowance is exhausted. Check the live Free
plan and caps; free quota errors are a stop condition, not permission to upgrade.

Mac polling requires a signed 30-minute session, verified **before DB access**.
Owner-only `/owner/session` reserves at most8 sessions/day (4 worker-hours/day).
No automatically refreshed session, public session issuance or background DB
heartbeat. Expired/invalid worker sessions cannot query Postgres. Worker stops12s
before expiration, polls at5s, and has no auto-start service. Session issuance and
DB job state survive isolate restarts; quotas/leases are shared across isolates.
Owner may open a new session with `scripts/open_neon_session.py` on the trusted box,
then securely transfer that session file to the Mac. Never paste grants into chat.

At maximum intended4h/day: about89,280 idle polls/month (31 days), plus <=18,600
MCP requests and small completion traffic—well below1M invocations. Thirty-minute
sessions and5min idle tails imply about36.2 database CUh/month if used in8 separate
sessions/day. Even pathological authenticated scheduling is bounded by the90CUh
project-active quota. 600 MCP requests/day ×25s worst-case waiting is129.2 waiting
hours/month, below400h; polling/native calls return promptly. These are budgeting
estimates, not CPU/egress measurements or a guarantee against credential theft,
anonymous floods, operational restarts or provider limit changes. Function CPU
usage must still be monitored. Never store/transfer original video through relay.

## Build/deploy

`npm ci && npm run check && npm test && npm run build` in this directory.
`schema.sql` initializes only a NEW isolated connector DB. Existing v0.3 databases
need the reviewed `migrations/002-edit-operations.sql` constraint migration, which
allows typed prepare/edit jobs without dropping receipts, approvals or other data.
`dist/index.mjs` bundles runtime dependencies, archived as root `index.mjs` in ZIP.
Deploy with official Neon `deploy_function` or authenticated CLI; Node.js24.
Environment: DAVINCI_CLIENT_TOKEN, DAVINCI_WORKER_TOKEN, DAVINCI_ALLOWED_HOST.
DATABASE_URL is injected by Neon. pg pool max2; idle clients released after1s;
attachDatabasePool handles idle suspension without crashing the process.

The first deployment can use an intentionally invalid configured host, then set
ONLY the returned exact invocation hostname after reading `get_function`. During
rollout, old isolates may briefly serve421 responses; do not disable Host checks.
Once stable, require full SDK initialize/list/read tests and401 rejection tests.

On Mac, install the reviewed0.4.1 wheel, chmod600 token/session files; run worker:

```
grokbot-davinci-worker --relay https://ACTUAL-INVOCATION-HOST \
 --worker-id peters-mac --token-file /PRIVATE/worker-token \
 --session-file /PRIVATE/session.secret.json
```

Read-only is the worker default. To enable scoped editing, additionally set
`--allow-edits --state /PRIVATE/approvals.sqlite3 --import-root /ALLOWED/MEDIA
--output-root /ALLOWED/EXPORTS`. Use exact private roots, never filesystem root.
Every edit needs an exact one-use Mac-local approval and a registered copied
timeline (except the operation creating that copy). Remote confirmation alone
cannot authorize a mutation. Preparation runs full Python validation on the worker
without connecting to or editing Resolve. The published schemas are generated from
the same Python models; schema parity is regression-tested. Python/Node digest
verification uses RFC8785 canonical JSON including floats/defaults/Unicode. Upgrading
changes older plan digests; obtain new explicit approvals, never reset consumed ones.
Use documented Resolve scripting environment from the root README. Session must be renewed explicitly; losing a response never
requeues a claimed job. Identical completion receipts are idempotent only while
unexpired; wrong lease, changed result, unclaimed or expired receipts fail closed.

Before adding the connector to Grok Bot, approve its exact `/mcp` URL and private
Authorization header through the account confirmation widget. No endpoint or
secret is hardcoded in this repository. Read-only live testing is not a production
load/abuse test. Native edits require explicit safe-project readiness. Initial synthetic tests used
an entirely separate project and working timeline. Never apply tests to an open
original; preserve saved originals and create uniquely named backups/copies first.

Sources: https://neon.com/pricing and
https://neon.com/docs/compute/functions/overview

Render plans now include the exact installed preset (v0.4.1), populated before
approval/digest calculation. Pinning avoids Resolve21 inherited Audio Only state.
Audio exports validate GetAudioRenderCodecs, video exports GetRenderCodecs. Always
probe completed artifacts for the requested streams; API success alone is not proof.
Regenerate schemas after model changes with `PYTHONPATH=src python
scripts/generate_neon_schemas.py` from the repository root.

Relay v0.4.2 distinguishes an authorized worker polling too soon (429 with
Retry-After5) from an expired/unknown session (403). Rapid manual worker restarts
can hit the existing four-second admission interval; this is not a credential
failure and must not terminate the worker. Session signatures, expiry, budgets and
admission limits are unchanged; the distinction is regression-tested.

The v0.5 schemas include the finite explicit overlay placement fields described in
the root README. Python preparation enforces their paired-field/video-only rules;
Mac preflight enforces media roots/FPS/occupancy and execution verifies actual
start/end. No generic method calls or arbitrary execution were introduced.
