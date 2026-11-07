"""Mac-only approvals and durable at-most-once mutation receipts.

The relay cannot grant approval. A human must approve the exact canonical plan
locally; a consumed approval is never reissued, even after errors/timeouts/restarts.
"""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
from .edits import Plan, digest


class ApprovalStore:
    def __init__(self, path):
        path = Path(path)
        if path.is_symlink() or path.parent.stat().st_mode & 0o077:
            raise PermissionError("approval_state_requires_private_directory")
        if path.exists() and path.stat().st_mode & 0o077:
            raise PermissionError("approval_state_must_be_owner_only")
        os.umask(0o077)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS approvals (digest TEXT PRIMARY KEY, expires REAL, consumed INTEGER)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS copies (project TEXT, timeline TEXT, PRIMARY KEY(project,timeline))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS renders (project TEXT, timeline TEXT, job TEXT PRIMARY KEY, started INTEGER NOT NULL DEFAULT 0)"
        )

        if "started" not in {
            row[1] for row in self.db.execute("PRAGMA table_info(renders)")
        }:
            self.db.execute(
                "ALTER TABLE renders ADD COLUMN started INTEGER NOT NULL DEFAULT 0"
            )

    def approve(self, plan, seconds=300):
        # Existing command digests are never extended or reset.
        self.db.execute(
            "INSERT INTO approvals VALUES(?,?,0)",
            (digest(plan), time.time() + min(seconds, 300)),
        )

    def consume(self, plan):
        cur = self.db.execute(
            "UPDATE approvals SET consumed=1 WHERE digest=? AND expires>? AND consumed=0",
            (digest(plan), time.time()),
        )
        if cur.rowcount != 1:
            raise PermissionError("local_approval_missing_expired_or_consumed")

    def own_copy(self, project, timeline):
        self.db.execute("INSERT INTO copies VALUES(?,?)", (project, timeline))

    def require_copy(self, project, timeline):
        if not self.db.execute(
            "SELECT 1 FROM copies WHERE project=? AND timeline=?", (project, timeline)
        ).fetchone():
            raise PermissionError("only_connector_working_copies_can_be_edited")

    def own_render(self, project, timeline, job):
        self.db.execute(
            "INSERT INTO renders(project,timeline,job) VALUES(?,?,?)",
            (project, timeline, job),
        )

    def require_render(self, project, timeline, job):
        if not self.db.execute(
            "SELECT 1 FROM renders WHERE project=? AND timeline=? AND job=?",
            (project, timeline, job),
        ).fetchone():
            raise PermissionError("render_job_not_owned_by_scope")

    def claim_render_once(self, project, timeline, job):
        cur = self.db.execute(
            "UPDATE renders SET started=1 WHERE project=? AND timeline=? AND job=? AND started=0",
            (project, timeline, job),
        )
        if cur.rowcount != 1:
            raise PermissionError("render_job_already_started_or_not_owned")

    def close(self):
        self.db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Locally approve one exact Resolve edit for five minutes"
    )
    parser.add_argument("--plan-file", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    path = Path(args.plan_file)
    if path.stat().st_size > 49152:
        raise SystemExit("Plan too large")
    plan = Plan.model_validate_json(path.read_text())
    print(json.dumps(plan.model_dump(mode="json"), indent=2))
    key = digest(plan)
    print("One-use approval; no rollback guarantee. Close concurrent editing sessions.")
    if input("Type APPROVE " + key + " to continue: ") != "APPROVE " + key:
        raise SystemExit("Not approved")
    os.umask(0o077)
    state = Path(args.state)
    state.parent.mkdir(parents=True, exist_ok=True)
    store = ApprovalStore(state)
    store.approve(plan)
    store.close()
    print("Approved once for five minutes")
