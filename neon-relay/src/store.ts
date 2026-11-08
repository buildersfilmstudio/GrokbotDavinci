import { createHash, randomBytes, randomUUID } from "node:crypto";
import type { Pool, PoolClient } from "pg";
export const canonical = (v: any): string =>
  JSON.stringify(
    v === null || typeof v !== "object"
      ? v
      : Array.isArray(v)
        ? v.map((x) => JSON.parse(canonical(x)))
        : Object.fromEntries(
            Object.keys(v)
              .sort()
              .map((k) => [k, JSON.parse(canonical(v[k]))]),
          ),
  );
export const hash = (v: string) => createHash("sha256").update(v).digest("hex");
export class Store {
  constructor(readonly pool: Pool) {}
  async tx<T>(f: (c: PoolClient) => Promise<T>): Promise<T> {
    const c = await this.pool.connect();
    try {
      await c.query("BEGIN");
      await c.query("SET LOCAL statement_timeout='3s'");
      await c.query("SELECT pg_advisory_xact_lock(844839)");
      const out = await f(c);
      await c.query("COMMIT");
      return out;
    } catch (e) {
      await c.query("ROLLBACK");
      throw e;
    } finally {
      c.release();
    }
  }
  async session() {
    return this.tx(async (c) => {
      const {
        rows: [b],
      } = await c.query(
        "INSERT INTO gbd_budget(day,sessions,client_calls) VALUES(CURRENT_DATE,1,0) ON CONFLICT(day) DO UPDATE SET sessions=gbd_budget.sessions+1 WHERE gbd_budget.sessions<2 RETURNING sessions",
      );
      if (!b) throw Error("daily_session_budget_exhausted");
      await c.query(
        "DELETE FROM gbd_jobs WHERE expires_at<extract(epoch from now())-86400",
      );
      const {
        rows: [s],
      } = await c.query(
        "INSERT INTO gbd_sessions(id,expires_at,last_claim) VALUES($1,extract(epoch from now())+7200,0) RETURNING *",
        [randomUUID()],
      );
      return s;
    });
  }
  async clientBudget() {
    return this.tx(async (c) => {
      const r = await c.query(
        "INSERT INTO gbd_budget(day,sessions,client_calls) VALUES(CURRENT_DATE,0,1) ON CONFLICT(day) DO UPDATE SET client_calls=gbd_budget.client_calls+1 WHERE gbd_budget.client_calls<600 RETURNING day",
      );
      if (!r.rowCount) throw Error("daily_client_budget_exhausted");
    });
  }
  async enqueue(operation: string, payload: object) {
    if (Buffer.byteLength(JSON.stringify(payload)) > 32768)
      throw Error("payload_too_large");
    return this.tx(async (c) => {
      const {
        rows: [s],
      } = await c.query(
        "SELECT id FROM gbd_sessions WHERE expires_at>extract(epoch from now()) LIMIT 1",
      );
      if (!s) throw Error("worker_session_required");
      const {
        rows: [n],
      } = await c.query(
        "SELECT count(*)::int AS n FROM gbd_jobs WHERE expires_at>extract(epoch from now()) AND done=false",
      );
      if (n.n >= 16) throw Error("queue_full");
      const {
        rows: [count],
      } = await c.query("SELECT count(*)::int AS n FROM gbd_jobs");
      if (count.n >= 4096) throw Error("receipt_limit");
      const id = randomUUID();
      await c.query(
        "INSERT INTO gbd_jobs(id,operation,payload,expires_at) VALUES($1,$2,$3,extract(epoch from now())+25)",
        [id, operation, JSON.stringify(payload)],
      );
      return id;
    });
  }
  async claim(session: string) {
    return this.tx(async (c) => {
      const admission = await c.query(
        "UPDATE gbd_sessions SET last_claim=extract(epoch from now()) WHERE id=$1 AND expires_at>extract(epoch from now()) AND last_claim<extract(epoch from now())-4 RETURNING id",
        [session],
      );
      if (!admission.rowCount) {
        const { rows: [active] } = await c.query(
          "SELECT id FROM gbd_sessions WHERE id=$1 AND expires_at>extract(epoch from now())",
          [session],
        );
        if (active) throw Error("poll_too_fast");
        throw Error("session_expired");
      }
      const lease = randomBytes(32).toString("base64url");
      const {
        rows: [j],
      } = await c.query(
        `UPDATE gbd_jobs SET lease_hash=$1,claimed=true WHERE id=(SELECT id FROM gbd_jobs WHERE claimed=false AND done=false AND expires_at>extract(epoch from now()) ORDER BY expires_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id,operation,payload,expires_at`,
        [hash(lease)],
      );
      return j ? { ...j, expires_at: Number(j.expires_at), lease } : null;
    });
  }
  async complete(id: string, lease: string, ok: boolean, result: object) {
    return this.tx(async (c) => {
      const {
        rows: [j],
      } = await c.query(
        "SELECT * FROM gbd_jobs WHERE id=$1 AND claimed=true AND lease_hash=$2 AND expires_at>extract(epoch from now())",
        [id, hash(lease)],
      );
      if (!j) throw Error("invalid_expired_or_unclaimed_receipt");
      const receipt = hash(canonical({ ok, result }));
      if (j.done) {
        if (j.receipt !== receipt) throw Error("conflicting_receipt");
        return "duplicate";
      }
      await c.query(
        "UPDATE gbd_jobs SET done=true,ok=$2,result=$3,receipt=$4 WHERE id=$1",
        [id, ok, JSON.stringify(result), receipt],
      );
      return "completed";
    });
  }
  async result(id: string) {
    const {
      rows: [j],
    } = await this.pool.query(
      "SELECT done,ok,result,expires_at FROM gbd_jobs WHERE id=$1",
      [id],
    );
    if (!j || Number(j.expires_at) < Date.now() / 1000)
      throw Error("expired_do_not_retry_mutation");
    return j.done ? { ok: j.ok, result: j.result } : null;
  }
}
