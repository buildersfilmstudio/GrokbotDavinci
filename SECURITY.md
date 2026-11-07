# Security

This is a single-tenant, read-only preview. Do not deploy it as a general-purpose
remote-control service. No Mac inbound ports, arbitrary Python, shell commands,
generic file access or mutation tools are provided.

Use HTTPS ingress with rate/connection/request-size limits and private token
provisioning. Do not paste credentials, worker lease values, project names or
private videos into public issues. Report a suspected issue privately to the
repository owner through GitHub's available private reporting channel; if none is
configured, ask the owner for a private channel without publishing exploit details.

Neither the Dockerfile nor the README provisions infrastructure. Deployment,
Mac installation, persistent services, project editing and connection of a client
need separate operator decisions/approval. Existing local Resolve/Tailscale
installations are not managed or changed by this package.
