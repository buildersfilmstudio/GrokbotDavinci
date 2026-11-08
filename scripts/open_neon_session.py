"""Owner-only local provisioning. Never prints or passes credentials in argv."""

import argparse
import json
import os
from pathlib import Path
import stat
import urllib.request

p = argparse.ArgumentParser()
p.add_argument(
    "--credentials",
    required=True,
    help="Private JSON containing client token and allowed host",
)
p.add_argument(
    "--output", required=True, help="New private session JSON for Mac transfer"
)
a = p.parse_args()
path = Path(a.credentials)
if (
    path.is_symlink()
    or not stat.S_ISREG(path.stat().st_mode)
    or path.stat().st_mode & 0o077
):
    raise SystemExit("Credentials must be owner-only regular file")
e = json.loads(path.read_text())
host = e["DAVINCI_ALLOWED_HOST"]
if any(c in host for c in "/@?#\\") or not host.endswith(".aws.neon.tech"):
    raise SystemExit("Unexpected Neon invocation host")
request = urllib.request.Request(
    "https://" + host + "/owner/session",
    data=b"{}",
    headers={"Authorization": "Bearer " + e["DAVINCI_CLIENT_TOKEN"]},
    method="POST",
)


# Refuse redirects: do not forward a credential to another origin.
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as r:
    raw = r.read(4097)
    if len(raw) > 4096:
        raise SystemExit("Unexpected response size")
    grant = json.loads(raw)
    assert set(grant) == {"session", "expires_at"}
out = Path(a.output)
if out.exists() or out.is_symlink():
    raise SystemExit("Choose a NEW session output path; existing file not overwritten")
os.umask(0o077)
with out.open("x") as f:
    json.dump(grant, f)
print("Private 120-minute read-only worker session saved. No capability printed.")
