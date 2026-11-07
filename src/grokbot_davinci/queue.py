"""Single-tenant SQLite queue. Terminal leases never requeue: no replay execution.

One relay process / one Mac worker. SQLite persists receipts across relay restarts.
No background expiry task is required: every state transition checks wall-clock TTL.
"""

import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from .protocol import MAX_RESULT


class QueueError(Exception):
    pass


class Queue:
    def __init__(self, path, worker_id, ttl=20, clock=time.time):
        self.worker_id, self.ttl, self.clock = worker_id, ttl, clock
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, operation TEXT NOT NULL, worker TEXT NOT NULL,
            state TEXT NOT NULL, expires REAL NOT NULL, lease TEXT,
            result TEXT, digest TEXT)""")
        self.last_seen = 0.0

    def expire(self):
        now = self.clock()
        self.db.execute(
            "UPDATE jobs SET state='expired' WHERE expires<=? AND state IN ('queued','claimed')",
            (now,),
        )
        # Receipt retention is bounded in time; expired IDs can never be reused.
        self.db.execute("DELETE FROM jobs WHERE expires<?", (now - 86400,))

    def enqueue(self, operation):
        if operation not in ("version", "project", "timeline", "media"):
            raise QueueError("unsupported_operation")
        self.expire()
        if (
            self.db.execute(
                "SELECT count(*) FROM jobs WHERE state IN ('queued','claimed')"
            ).fetchone()[0]
            >= 16
            or self.db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= 4096
        ):
            raise QueueError("queue_full")
        job_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO jobs VALUES(?,?,?,?,?,NULL,NULL,NULL)",
            (job_id, operation, self.worker_id, "queued", self.clock() + self.ttl),
        )
        return job_id

    def claim(self, worker_id):
        if worker_id != self.worker_id:
            raise QueueError("wrong_worker")
        self.last_seen = self.clock()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.expire()
            row = self.db.execute(
                "SELECT id,operation,expires FROM jobs WHERE worker=? AND state='queued' ORDER BY rowid LIMIT 1",
                (worker_id,),
            ).fetchone()
            if not row:
                self.db.execute("COMMIT")
                return None
            lease = secrets.token_urlsafe(32)
            # Only a digest is persisted; the capability is returned to its worker once.
            self.db.execute(
                "UPDATE jobs SET state='claimed',lease=? WHERE id=?",
                (hashlib.sha256(lease.encode()).hexdigest(), row[0]),
            )
            self.db.execute("COMMIT")
            return dict(id=row[0], operation=row[1], expires_at=row[2], lease=lease)
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def complete(self, job_id, worker_id, lease, ok, result):
        encoded = json.dumps(
            dict(ok=ok, result=result),
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(encoded.encode()) > MAX_RESULT:
            raise QueueError("result_too_large")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        self.expire()
        row = self.db.execute(
            "SELECT worker,state,expires,lease,digest FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row or row[0] != worker_id or worker_id != self.worker_id:
            raise QueueError("unknown_job_or_owner")
        expected = hashlib.sha256(lease.encode()).hexdigest()
        if not row[3] or not secrets.compare_digest(row[3], expected):
            raise QueueError("invalid_lease")
        if row[2] <= self.clock() or row[1] == "expired":
            raise QueueError("expired")
        if row[1] == "done":
            if row[4] == digest:
                return "duplicate"
            raise QueueError("conflicting_completion")
        if row[1] != "claimed":
            raise QueueError("not_claimed")
        self.db.execute(
            "UPDATE jobs SET state='done', result=?, digest=? WHERE id=?",
            (encoded, digest, job_id),
        )
        return "completed"

    def result(self, job_id):
        self.expire()
        row = self.db.execute(
            "SELECT state,result FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row or row[0] == "expired":
            raise QueueError("worker_offline_or_job_expired")
        return json.loads(row[1]) if row[0] == "done" else None

    def cancel(self, job_id):
        self.db.execute(
            "UPDATE jobs SET state='expired' WHERE id=? AND state IN ('queued','claimed')",
            (job_id,),
        )

    def close(self):
        self.db.close()
