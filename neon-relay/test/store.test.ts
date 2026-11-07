import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { PGlite } from "@electric-sql/pglite";
import { Store } from "../src/store.js";
import type { Pool } from "pg";
test("Postgres queue leases, expiry, completion replay, durable budget and sessions", async () => {
  const db = new PGlite();
  await db.exec(
    await readFile(new URL("../schema.sql", import.meta.url), "utf8"),
  );
  const query = async (sql: string, args: any[] = []) => {
    if (sql.includes("pg_advisory_xact_lock")) return { rows: [], rowCount: 1 };
    const r = await db.query(sql, args);
    return { ...r, rowCount: r.affectedRows ?? r.rows.length };
  };
  const store = new Store({
    query,
    connect: async () => ({ query, release() {} }),
  } as unknown as Pool);
  await assert.rejects(store.enqueue("version", {}), /session_required/);
  const s = await store.session();
  await store.clientBudget();
  const id = await store.enqueue("version", {});
  assert.equal(await store.result(id), null);
  const j = await store.claim(s.id);
  assert.equal(j.id, id);
  assert.equal(j.operation, "version");
  await assert.rejects(store.claim(s.id), /poll_too_fast/);
  await assert.rejects(store.complete(id, "wrong", true, {}), /invalid/);
  assert.equal(
    await store.complete(id, j.lease, true, { version: "21" }),
    "completed",
  );
  assert.equal(
    await store.complete(id, j.lease, true, { version: "21" }),
    "duplicate",
  );
  await assert.rejects(
    store.complete(id, j.lease, true, { version: "22" }),
    /conflicting/,
  );
  assert.deepEqual(await store.result(id), {
    ok: true,
    result: { version: "21" },
  });
  await db.query("UPDATE gbd_jobs SET expires_at=0 WHERE id=$1", [id]);
  await assert.rejects(
    store.complete(id, j.lease, true, { version: "21" }),
    /expired/,
  );
  await assert.rejects(store.result(id), /expired/);
  for (let i = 1; i < 8; i++) await store.session();
  await assert.rejects(store.session(), /budget/);
  await db.query("UPDATE gbd_budget SET client_calls=600");
  await assert.rejects(store.clientBudget(), /budget/);
  await db.close();
});
