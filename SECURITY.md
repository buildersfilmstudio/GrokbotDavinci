# Security

This is a single-tenant, read-only-by-default preview. Do not deploy it as a general-purpose
remote-control service. No Mac inbound ports, arbitrary Python, shell commands,
generic file access are provided. Mutations require the explicit worker flag,
strict typed plan, matching working-copy scope and single-use Mac-local approval.
Never automatically approve plans delivered over the relay.

Use HTTPS ingress with rate/connection/request-size limits and private token
provisioning. Do not paste credentials, worker lease values, project names or
private videos into public issues. Report a suspected issue privately to the
repository owner through GitHub's available private reporting channel; if none is
configured, ask the owner for a private channel without publishing exploit details.

Neither the Dockerfile nor the README provisions infrastructure. Deployment,
Mac installation, persistent services, project editing and connection of a client
need separate operator decisions/approval. Existing local Resolve/Tailscale
installations are not managed or changed by this package.

Treat native plugin/render settings and the worker OS account as trusted. A copied
timeline is not an independent project backup; media-pool and render settings are
project-wide. On any timeout/partial result, stop and inspect rather than retry.
No rollback or exactly-once native transaction is claimed. Local approval is
consumed before execution; durable leases and receipts prevent transport replays.
Import/LUT paths are canonicalized into configured roots; output symlinks and
existing command output directories fail closed. Root checks do not eliminate
TOCTOU attacks by a malicious local user with filesystem write access.
