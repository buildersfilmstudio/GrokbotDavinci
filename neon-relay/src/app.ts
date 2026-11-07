import { createHmac, timingSafeEqual } from "node:crypto";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { Ajv } from "ajv";
import canonicalize from "canonicalize";
import schemas from "./schemas.json" with { type: "json" };
import { WebStandardStreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/webStandardStreamableHttp.js";
import { z } from "zod";
import { Store, hash } from "./store.js";
export type Config = {
  clientToken: string;
  workerToken: string;
  host: string;
  workerId: string;
};
const eq = (a: string, b: string) =>
  timingSafeEqual(Buffer.from(hash(a), "hex"), Buffer.from(hash(b), "hex"));
export function createApp(store: Store, cfg: Config) {
  if (
    cfg.clientToken.length < 40 ||
    cfg.workerToken.length < 40 ||
    eq(cfg.clientToken, cfg.workerToken) ||
    !cfg.host ||
    cfg.host.includes("*")
  )
    throw Error("unsafe_configuration");
  const sign = (p: string) =>
    createHmac("sha256", cfg.clientToken)
      .update("worker-session:" + p)
      .digest("base64url");
  function session(token: string) {
    const [p, s, ...rest] = token.split(".");
    if (!p || !s || rest.length || !eq(sign(p), s))
      throw Error("invalid_session");
    const v = JSON.parse(Buffer.from(p, "base64url").toString());
    if (
      v.worker !== cfg.workerId ||
      !Number.isFinite(v.exp) ||
      v.exp <= Date.now() / 1000 ||
      v.exp > Date.now() / 1000 + 1801 ||
      !z.uuid().safeParse(v.id).success
    )
      throw Error("expired_session");
    return v;
  }
  async function body(req: Request) {
    if (!req.body) return {};
    const reader = req.body.getReader();
    let n = 0;
    const chunks = [];
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        n += value.length;
        if (n > 65536) {
          await reader.cancel();
          throw Error("body_too_large");
        }
        chunks.push(value);
      }
    } finally {
      reader.releaseLock();
    }
    return JSON.parse(Buffer.concat(chunks).toString());
  }
  return {
    async fetch(req: Request): Promise<Response> {
      const url = new URL(req.url),
        path = url.pathname;
      const worker = path.startsWith("/worker/");
      // Authenticate before any body parsing, database/queue work, or transport creation.
      if (
        !eq(
          req.headers.get("authorization") ?? "",
          "Bearer " + (worker ? cfg.workerToken : cfg.clientToken),
        )
      )
        return new Response("Unauthorized", { status: 401 });
      if (
        url.host !== cfg.host ||
        (req.headers.get("host") && req.headers.get("host") !== cfg.host)
      )
        return Response.json(
          {
            error: "Invalid Host",
            url_host: url.host,
            header_host: req.headers.get("host"),
          },
          { status: 421 },
        );
      if (req.headers.has("origin"))
        return new Response("Browser origins forbidden", { status: 403 });
      if (req.method !== "POST" && path !== "/mcp")
        return new Response("Method not allowed", { status: 405 });
      try {
        if (path === "/owner/session") {
          const s = await store.session();
          const p = Buffer.from(
            JSON.stringify({
              id: s.id,
              exp: Number(s.expires_at),
              worker: cfg.workerId,
            }),
          ).toString("base64url");
          return Response.json(
            { session: p + "." + sign(p), expires_at: Number(s.expires_at) },
            { headers: { "Cache-Control": "no-store" } },
          );
        }
        if (worker) {
          // Session expiry is checked cryptographically before touching the DB.
          const s = session(req.headers.get("x-worker-session") ?? "");
          const data = await body(req);
          if (path === "/worker/claim") {
            const v = z
              .object({ worker_id: z.literal(cfg.workerId) })
              .strict()
              .parse(data);
            void v;
            return Response.json({ job: await store.claim(s.id) });
          }
          const match = path.match(
            /^\/worker\/jobs\/([0-9a-f-]{36})\/complete$/,
          );
          if (match) {
            z.uuid().parse(match[1]);
            const v = z
              .object({
                worker_id: z.literal(cfg.workerId),
                lease: z.string().min(40).max(128),
                ok: z.boolean(),
                result: z.record(z.string(), z.unknown()),
              })
              .strict()
              .parse(data);
            if (Buffer.byteLength(JSON.stringify(v.result)) > 32768)
              throw Error("result_too_large");
            return Response.json({
              status: await store.complete(match[1], v.lease, v.ok, v.result),
            });
          }
          return new Response("Not found", { status: 404 });
        }
        if (path !== "/mcp") return new Response("Not found", { status: 404 });
        // Every authenticated MCP request gets a durable quota, including initialize/list.
        await store.clientBudget();
        const server = new Server(
          { name: "grokbot-davinci-neon", version: "0.5.0" },
          { capabilities: { tools: {} } },
        );
        async function query(op: string, payload = {}) {
          const id = await store.enqueue(op, payload);
          const deadline = Date.now() + 23000;
          while (Date.now() < deadline) {
            if (req.signal.aborted) throw Error("client_disconnected");
            const out = await store.result(id);
            if (out) {
              if (!out.ok) throw Error("worker_operation_failed");
              return {
                content: [
                  { type: "text" as const, text: JSON.stringify(out.result) },
                ],
              };
            }
            await new Promise((r) => setTimeout(r, 1000));
          }
          throw Error("worker_offline_or_timeout");
        }
        const reads: Record<string, string> = {
          resolve_version: "version",
          current_project: "project",
          current_timeline: "timeline",
          current_media_bin: "media",
        };
        const inspectSchema = {
          type: "object",
          additionalProperties: false,
          required: ["domain"],
          properties: {
            domain: {
              enum: ["capabilities", "inventory", "audio", "fusion", "render"],
            },
            clip_id: { type: "string", pattern: "^[A-Za-z0-9_:-]{1,128}$" },
          },
        };
        const tools = [
          ...Object.keys(reads).map((name) => ({
            name,
            description: "Read-only live Resolve metadata",
            inputSchema: {
              type: "object" as const,
              properties: {},
              additionalProperties: false,
            },
          })),
          {
            name: "inspect_resolve",
            description: "Read-only scoped API inventory",
            inputSchema: inspectSchema,
          },
          {
            name: "prepare_edit",
            description:
              "Validate a typed scoped edit on the Mac and return exact plan/digest. Does not edit or grant approval. Supports 14 kinds: copy_timeline, track, append_clip, transform, marker, title, import_media, voice_isolation, fairlight_preset, cdl, lut, fusion_build, queue_render, start_render.",
            inputSchema: schemas.preparation,
          },
          {
            name: "execute_edit",
            description:
              "Queue exact prepared plan with EXECUTE <digest> confirmation. Every mutation additionally requires one-use Mac-local approval and working-copy scope. No automatic retries after partial/unknown result.",
            inputSchema: schemas.execution,
          },
        ];
        server.setRequestHandler(ListToolsRequestSchema, async () => ({
          tools,
        }));
        server.setRequestHandler(CallToolRequestSchema, async (request) => {
          try {
            const tool = tools.find((t) => t.name === request.params.name);
            if (!tool) throw Error("unknown_tool");
            const args = request.params.arguments ?? {};
            const validate = new Ajv({
              strict: false,
              allErrors: false,
            }).compile(tool.inputSchema);
            if (!validate(args)) throw Error("invalid_typed_arguments");
            if (request.params.name in reads)
              return await query(reads[request.params.name]);
            if (request.params.name === "inspect_resolve")
              return await query("inspect", args);
            if (request.params.name === "prepare_edit")
              return await query("prepare", args);
            const plan = args.plan as object;
            const key = hash(canonicalize(plan)!);
            if (args.confirmation !== "EXECUTE " + key)
              throw Error("exact_digest_confirmation_required");
            return await query("edit", plan);
          } catch {
            return {
              isError: true,
              content: [
                {
                  type: "text" as const,
                  text: "Operation rejected or failed; mutation may be partial. Inspect working copy; never automatically retry or rearm approval.",
                },
              ],
            };
          }
        });
        const transport = new WebStandardStreamableHTTPServerTransport({
          sessionIdGenerator: undefined,
          enableJsonResponse: true,
        });
        await server.connect(transport);
        const data = await body(req);
        const rebuilt = new Request(req.url, {
          method: req.method,
          headers: req.headers,
          body: req.method === "POST" ? JSON.stringify(data) : undefined,
          signal: req.signal,
        });
        try {
          return await transport.handleRequest(rebuilt);
        } finally {
          await server.close();
        }
      } catch (e) {
        const msg = e instanceof Error ? e.message : "";
        if (msg.includes("session"))
          return new Response(
            "Worker session required/expired or session budget exceeded",
            { status: 403 },
          );
        return new Response("Request rejected or temporarily unavailable", {
          status:
            msg.includes("budget") || msg.includes("poll_too_fast") ? 429 : 400,
          headers: { "Retry-After": "5" },
        });
      }
    },
  };
}
