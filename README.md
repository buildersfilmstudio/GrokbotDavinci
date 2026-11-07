# GrokbotDavinci

An original, **read-only first** MCP connector for DaVinci Resolve Studio on a Mac.
No Tailscale, VPN, inbound Mac port, public Resolve API, or video upload required.
The Neon Free relay is deployed; scoped native editing validation is in progress.

```text
Grok Bot -- HTTPS + CLIENT token --> hosted relay /mcp
                                      ^
                                      | outbound HTTPS polling + WORKER token
                                      |
                                   Mac worker --> official local Resolve scripting API
```

Both relay implementations use official SDK standard MCP Streamable HTTP.
The deployed Neon relay uses TypeScript/Postgres; the alternative Python relay uses SQLite. The Mac worker is a separate process and opens **no listening socket**.
Video stays on the Mac; requested project, timeline and clip names leave it.
Anyone holding the client token can request these reads while the worker is running.
This is not an official Blackmagic or Grok Bot product.

## Hosting update: v0.4 Neon Free

See [neon-relay/README.md](neon-relay/README.md) for the deployed TypeScript/Postgres
relay. The live HTTPS SDK initialization and discovery now verify all **seven tools**,
including `prepare_edit` and `execute_edit` with the same 14 typed actions as Python.
Version/project/timeline reads were live-tested through the outbound Mac worker.
Invalid/missing bearer credentials return401. Discovery is not an editing roundtrip.
Worker sessions expire after30minutes; no always-on polling or Tailscale required.
Existing connected clients may need tool discovery refreshed, not a new endpoint.

## Implemented tools (v0.4, both relays)

Seven MCP tools: `resolve_version`, `current_project`, `current_timeline`,
`current_media_bin`, `inspect_resolve`, `prepare_edit`, and `execute_edit`.
The first five only inspect. `inspect_resolve` accepts capabilities, inventory,
audio, fusion or render domains, providing stable IDs for scoped edits.
`prepare_edit` validates and returns a command plan/digest; it does not execute
or approve anything. `execute_edit` queues only that exact plan after explicit
`EXECUTE <digest>` confirmation. The Mac independently requires a local approval.

### Capability matrix

All rows are implemented and mock-tested. On Studio21.1 / Python3.14, native tests
in a new disposable project successfully duplicated a working timeline, added a
track, imported synthetic PNG/WAV, appended video and audio, transformed a clip,
added a marker, set/read back voice isolation and applied CDL in a local grade
version. These are API-level tests, not independent visual/audio-quality validation.

Follow-up native tests now verify a four-node Fusion composition, two scalar
keyframes, graph connections and exact Text+ text readback. Synthetic render
completed. Actual source preview was independently probed as H.264 video + AAC
audio,1920x1080, and a frame was visually inspected. A harmless marker also passed
live Neon prepare → exact Mac-local approval → remote execute → native readback.

Resolve21 can inherit hidden Audio Only preset state even when ExportVideo=True
and job metadata reports video. v0.4.1 pins an explicit installed preset before
rendering (default H.264 Master for video, Audio Only for audio), validates the
appropriate codec catalogue, and warns that output streams still need independent
verification. WAV uses actual GetAudioRenderCodecs tokens, not video codec tokens.

A synthetic2-point LUT was rejected; LUT support remains unverified. Fairlight
preset application is untested because no saved presets were present. User's saved
original was exported to a unique local backup and imported under a new project
name; violin footage was identified by its actual source path and inspected frame,
then a further working timeline was duplicated. Original media/audio unchanged.
Score transcription and synchronized highlights are **not completed**. No guessed
score was inserted. A lossless analysis-only audio render is ready for transcription.

Native methods are feature-detected; unsupported versions fail closed. This is
useful supported-API coverage, **not full GUI parity**.

| Typed action | Implemented scope / limits |
|---|---|
| `copy_timeline` | Duplicate under a new non-conflicting name, register and activate working copy |
| `track` | Add video/stereo audio track; name, enable/disable, lock/unlock |
| `append_clip` | Append current-bin media; optional explicit video-only frame/track overlay placement, root-restricted encoded MOV/MP4, matching FPS, occupied-interval refusal and exact native timing readback |
| `transform` | Clip pan, tilt, zoom, rotation, opacity; no arbitrary properties |
| `marker` | Timeline marker with name, note, color, duration |
| `title` | Insert Text+ at timecode and set text; target track/duration follow native UI selection/defaults |
| `import_media` | Explicit media files inside configured local roots, extension allowlist; no recursive browsing |
| `voice_isolation` | Audio-track state/amount, Resolve 20.1+ and available API |
| `fairlight_preset` | Apply an existing named preset, Resolve 20.2.2+ and available API |
| `cdl` | Clip/node CDL in a newly created and loaded local grade version |
| `lut` | Existing `.cube` LUT under configured roots; new local grade version |
| `fusion_build` | New clip composition, typed nodes, numeric keyframes and validated DAG connections |
| `queue_render` | Explicit installed preset and supported video/audio codec; fresh command-owned output directory under allowed root |
| `start_render` | Start only a job queued by this worker for this working timeline, once |

Fusion allowlist: Background, TextPlus, Merge, Transform, Blur, RectangleMask,
EllipseMask and MediaOut. Inputs are separately allowlisted by node type. Maximum
24 nodes / 48 edges / 128 keyframes per animated control. No Loader/Saver, scripts,
Fuses, custom/group nodes, expressions, arbitrary serialized compositions or generic
method execution. Existing compositions are not edited.

**Not implemented:** GUI-only ripple editing/transitions, broad Fairlight mixer/EQ/
plugin automation, arbitrary Fusion nodes, automatic transcription/score generation,
project deletion, replacing media, arbitrary scripting, or every color/Deliver GUI
control. Audio edits here are real clip assembly, track controls, voice isolation
and saved presets—not an invented volume/pan API. A finished violin lesson workflow
still needs explicit media/score decisions, native validation, and additional scoped
operations where required. No raw API/shell/eval/file-download escape hatch exists.

## Alternative Python/SQLite hosting: 1. Choose hosting

The following sections describe the optional Python relay, not Neon deployment.
For the deployed Free route use the Neon README above.

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
Blackmagic's installed `Developer/Scripting/README.md (or README.txt on older versions)` as the authoritative API
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

## Local edit authorization (disabled by default)

Create an owner-only state directory on the Mac, then explicitly start the worker
with these additional flags (roots must be existing absolute local directories):

```text
--allow-edits --state /private/owner-only/resolve-approval.sqlite3
--import-root /Users/yourname/Movies/Inputs
--output-root /Users/yourname/Movies/Exports
```

1. Inspect stable project/timeline IDs. Prepare a `copy_timeline` command first.
2. Save the exact returned `plan` JSON in a local plan file; inspect it on the Mac.
3. On that Mac run:

```sh
/path/to/venv/bin/grokbot-davinci-approve \
  --state /private/owner-only/resolve-approval.sqlite3 \
  --plan-file /private/owner-only/plan.json
```

Type the displayed literal `APPROVE <digest>` only after checking the full plan.
An approval lasts at most 300 seconds, is consumed atomically before mutation, and
cannot be reissued for that command. Never grant approvals from relay requests
automatically. There is no network approval endpoint.
4. Call `execute_edit` with the same plan and `confirmation: EXECUTE <digest>`.
5. Subsequent commands must target the locally registered working-copy ID, and
require their own new plan, local approval and execute confirmation.

A stolen client/relay credential alone cannot approve edits. Approvals bind the
whole payload, command UUID, project and timeline. Expiry, partial failure, child
termination or lost responses **never** automatically rearm/retry a mutation.
`unknown_or_partial_do_not_retry` means inspect the working copy manually; a fresh
command is a new authorized change, not a recovery guarantee. Results explicitly
say `api_reported_applied` with `independent_verification: false`; success booleans
are not proof of visual/audible correctness.

**Protection limits:** timeline duplication is not a full project backup. Shared
media-pool imports, Fairlight/native linked resources and project render settings
can affect shared project state; approved operations do not rewrite source media.
Use an expendable project copy for initial testing and independently back up work.
Native Resolve plugins, fonts, presets, existing render settings and local filesystem
are trusted. No atomic rollback is promised. Canonical root validation cannot defeat
a malicious local user racing filesystem changes; secure the worker account/roots.
Output folders are fresh and existing paths are not overwritten by this code.

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
  current project while a read is queued. Edits verify current project/timeline IDs
  immediately before mutation; this is not an atomic native GUI lock.

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
Mutation tests additionally cover every action kind, scoped working copies, local
approval consumption/expiry/replay across restart, denied paths/nodes/code, Fusion
keyframes/connections, audio version gates, render ownership/start-once and actual
SDK prepare → confirmation → local-approved mock edit over ASGI.
They do not require Resolve, a paid host or Tailscale.

**Deployment/native status:** the dedicated authenticated Neon HTTPS service and
bounded outbound macOS worker have been exercised together. This does not imply
production-load validation or live verification of every action. Feature-specific
native results are recorded below; CI mocks are not native integration evidence.
CI covers Python 3.12/3.13/3.14; local execution is not implied for every platform.

## References

- Official Fusion scripting guide: https://documents.blackmagicdesign.com/UserManuals/Fusion8_Scripting_Guide.pdf
- Versioned community API reference (verify against installed docs): https://wheheohu.github.io/bmd_doc/ResolveAPI/21.0.4/resolve_api/Timeline
- Official Python MCP SDK: https://github.com/modelcontextprotocol/python-sdk
- Public SDK security settings: https://py.sdk.modelcontextprotocol.io/api/mcp/server/transport_security/
- Blackmagic Resolve developer resources: https://www.blackmagicdesign.com/developer/
- Authoritative version-specific scripting docs ship with Resolve under
  `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/`.

Original implementation, MIT licensed. No upstream community Resolve MCP code copied.

The installed Studio 21 scripting README could not be read through the approved
local-file access boundary during development. Public API references informed the
implementation, but exact Mac/21.1 behavior remains an explicit native test gate.

Native render-state caveat reference (observed and reproduced locally):
https://github.com/samuelgursky/davinci-resolve-mcp/issues/123

## v0.5 explicit overlay placement

`append_clip` accepts optional `record_frame` and `track_index` together. Positioned
placement is video-only, scoped to a registered working timeline, and only imports
from configured asset roots. Source video FPS must match timeline FPS; no hidden
retiming. The target interval must be unoccupied. Source `end` is exclusive on the
live-verified Resolve21.1 path. The worker independently reads back start/end and
fails consumed commands closed on any mismatch, never retrying a partial mutation.

Only encoded MOV/MP4 assets support positioned placement: Resolve21 ignored the
requested still-image duration and inserted its native default5seconds. That failure
was caught by native timing verification in the disposable project, not hidden.
Still placement is now refused before mutation; encode timed alpha-overlay video.
A24-frame24fps alpha MOV subsequently placed at exact absolute frame86640 on
track3, read back with exact24-frame duration; overlapping placement was rejected.
These are synthetic test positions, not user-original edits.

Actual violin transcription is not complete: independent Spotify Basic Pitch and
YIN/envelope analysis expose repeated-bow segmentation disagreements. No guessed
published score or unverified note sequence was inserted into original footage.

## v0.6: isolated-project original-media relink

`copy_project` requires an exact unique `.drp` backup path inside configured output
roots and a new project name. Its one-use local approval covers saving existing
source edits, exporting the backup, importing/loading a **separate project**,
verifying its edit structure and registering only that new project for relinking.
An ordinary timeline working copy does NOT authorize project-wide media relinking.

`relink_media` initially supports verified video originals only. Its exact approved
plan binds MediaPool ID, offline old path, full new path, SHA256, size and expected
frames/FPS/resolution/codec. Candidate roots are canonical allowlists; symlinks,
wrong identities/hashes/metadata and ambiguous case-insensitive basenames fail
closed. The copied project's recorded DRP must still match its SHA256.
Only `MediaPool.RelinkClips([oneItem], exactParent)` runs, followed by path/ID,
missing-list and complete bounded edit-structure readback and SaveProject.
No media is moved, deleted, replaced or rendered. Errors consume approval; never
retry an unknown/partial outcome. Backup/copy failure leaves the source intact
and the new copy unregistered. DRP includes grades; structure verification checks
clip properties and current grade versions, not opaque grade graph byte equality.

This feature is separately locally approved, not an expansion to arbitrary API
execution. Original projects are rejected even when they contain a registered
timeline copy. Both relay schemas and reviewed Mac wheel must be upgraded together.

Project-copy execution alone has a 15-second native ceiling (including backup/import/readback), clipped to its existing job expiry. Other operations retain 8 seconds; no expiry/retry/session limits are relaxed.

Native v0.6 acceptance: an approved DRP backup and distinct project import completed
through deployed standard StreamableHTTP MCP. A fingerprint-verified video relink
reduced missing files from 5 to 4 in that copy. Independent subsequent macOS API
readback confirmed the exact new path and unchanged edit-structure digest across
all 23 timelines, and rejected original-project relink ownership. The other four
offline originals were deliberately left unresolved. No video render, file move or
media deletion occurred. This is one verified video-original case, not proof that
all formats, grades or arbitrary projects have identical behavior.
