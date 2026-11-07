import { test } from "node:test";
import assert from "node:assert/strict";
import { createApp } from "../src/app.js";
import type { Store } from "../src/store.js";
const cfg = {
  clientToken: "c".repeat(40),
  workerToken: "w".repeat(40),
  host: "test.example",
  workerId: "peters-mac",
};
const req = (path: string, token = cfg.clientToken, body: any = {}) =>
  new Request("https://test.example" + path, {
    method: "POST",
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
      Accept: "application/json, text/event-stream",
    },
    body: JSON.stringify(body),
  });
test("unauthenticated/wrong host/origin cannot reach DB or parse body", async () => {
  const store = new Proxy(
    {},
    {
      get() {
        throw Error("DB must not be used");
      },
    },
  ) as Store;
  const app = createApp(store, cfg);
  assert.equal((await app.fetch(req("/mcp", "wrong"))).status, 401);
  assert.equal((await app.fetch(req("/worker/claim"))).status, 401);
  assert.equal(
    (
      await app.fetch(
        new Request("https://wrong.example/mcp", {
          headers: { Authorization: "Bearer " + cfg.clientToken },
        }),
      )
    ).status,
    421,
  );
  const r = req("/mcp");
  r.headers.set("Origin", "https://evil.example");
  assert.equal((await app.fetch(r)).status, 403);
});
test("worker session signature required before database", async () => {
  const app = createApp(
    new Proxy(
      {},
      {
        get() {
          throw Error("DB must not be used");
        },
      },
    ) as Store,
    cfg,
  );
  assert.equal(
    (
      await app.fetch(
        req("/worker/claim", cfg.workerToken, { worker_id: "peters-mac" }),
      )
    ).status,
    403,
  );
});
test("SDK initialization and read-only tools list, durable quota", async () => {
  let calls = 0;
  const app = createApp(
    {
      clientBudget: async () => {
        calls++;
      },
    } as Store,
    cfg,
  );
  const init = await app.fetch(
    req("/mcp", cfg.clientToken, {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2025-03-26",
        capabilities: {},
        clientInfo: { name: "test", version: "1" },
      },
    }),
  );
  assert.equal(init.status, 200);
  const listed = await app.fetch(
    req("/mcp", cfg.clientToken, {
      jsonrpc: "2.0",
      id: 2,
      method: "tools/list",
    }),
  );
  const data: any = await listed.json();
  assert.equal(data.result.tools.length, 7);
  assert.equal(calls, 2);
  assert.ok(data.result.tools.some((t: any) => t.name === "execute_edit"));
});
test("SDK tool returns actual worker result and errors honestly", async () => {
  let ops: string[] = [];
  const app = createApp(
    {
      clientBudget: async () => {},
      enqueue: async (op: string) => {
        ops.push(op);
        return "job";
      },
      result: async () => ({ ok: true, result: { version: "21" } }),
    } as unknown as Store,
    cfg,
  );
  const r = await app.fetch(
    req("/mcp", cfg.clientToken, {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "resolve_version", arguments: {} },
    }),
  );
  const v: any = await r.json();
  assert.deepEqual(ops, ["version"]);
  assert.equal(JSON.parse(v.result.content[0].text).version, "21");
});
test("session signed grant and strict worker id", async () => {
  let claims = 0;
  const app = createApp(
    {
      session: async () => ({
        id: "12345678-1234-4234-8234-123456789abc",
        expires_at: Date.now() / 1000 + 100,
      }),
      claim: async () => {
        claims++;
        return null;
      },
    } as unknown as Store,
    cfg,
  );
  const grant: any = await (await app.fetch(req("/owner/session"))).json();
  const r = req("/worker/claim", cfg.workerToken, { worker_id: "peters-mac" });
  r.headers.set("x-worker-session", grant.session);
  assert.equal((await app.fetch(r)).status, 200);
  assert.equal(claims, 1);
  r.headers.set("x-worker-session", grant.session + "x");
  assert.equal((await app.fetch(r)).status, 403);
  assert.equal(claims, 1);
});

test("Python and Node RFC8785 digests agree for defaults, decimals and Unicode", async () => {
  const { readFile } = await import("node:fs/promises");
  const { default: canonicalize } = await import("canonicalize");
  const { hash } = await import("../src/store.js");
  const fixtures = JSON.parse(
    await readFile(
      new URL("./canonical-fixtures.json", import.meta.url),
      "utf8",
    ),
  );
  for (const f of fixtures) assert.equal(hash(canonicalize(f.plan)!), f.digest);
});

test("typed prepare and exact-confirm execute preserve plan and failure status", async () => {
  const { readFile } = await import("node:fs/promises");
  const fixtures = JSON.parse(
    await readFile(
      new URL("./canonical-fixtures.json", import.meta.url),
      "utf8",
    ),
  );
  const f = fixtures[0];
  let jobs: any[] = [];
  const app = createApp(
    {
      clientBudget: async () => {},
      enqueue: async (op: string, payload: object) => {
        jobs.push({ op, payload });
        return "job";
      },
      result: async () => ({
        ok: true,
        result: {
          plan: f.plan,
          digest: f.digest,
          requires_local_approval: true,
        },
      }),
    } as unknown as Store,
    cfg,
  );
  const call = async (name: string, args: object) => {
    const r = await app.fetch(
      req("/mcp", cfg.clientToken, {
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: { name, arguments: args },
      }),
    );
    return ((await r.json()) as any).result;
  };
  const prepared = await call("prepare_edit", {
    project_id: "p",
    timeline_id: "t",
    action: { kind: "transform", clip_id: "clip1", pan: 0.0000001 },
  });
  assert.equal(prepared.isError, undefined);
  assert.equal(jobs[0].op, "prepare");
  assert.equal(
    (await call("execute_edit", { plan: f.plan, confirmation: "yes" })).isError,
    true,
  );
  assert.equal(jobs.length, 1);
  await call("execute_edit", {
    plan: f.plan,
    confirmation: "EXECUTE " + f.digest,
  });
  assert.equal(jobs[1].op, "edit");
  assert.deepEqual(jobs[1].payload, f.plan);
  const changed = structuredClone(f.plan);
  changed.timeline_id = "other";
  assert.equal(
    (
      await call("execute_edit", {
        plan: changed,
        confirmation: "EXECUTE " + f.digest,
      })
    ).isError,
    true,
  );
  assert.equal(jobs.length, 2);
  assert.equal(
    (
      await call("prepare_edit", {
        project_id: "p",
        timeline_id: "t",
        action: { kind: "eval", code: "bad" },
      })
    ).isError,
    true,
  );
  assert.equal(jobs.length, 2);
});


test("rapid authorized worker restart is retryable429, expired session remains403", async () => {
  for (const [reason, expected] of [["poll_too_fast",429],["session_expired",403]] as const) {
    const app = createApp({session:async()=>({id:"12345678-1234-4234-8234-123456789abc",expires_at:Date.now()/1000+100}),claim:async()=>{throw Error(reason)}} as unknown as Store,cfg);
    const grant:any=await (await app.fetch(req("/owner/session"))).json();
    const request=req("/worker/claim",cfg.workerToken,{worker_id:"peters-mac"});request.headers.set("x-worker-session",grant.session);
    const response=await app.fetch(request);assert.equal(response.status,expected);
    if(expected===429)assert.equal(response.headers.get("Retry-After"),"5");
  }
});
