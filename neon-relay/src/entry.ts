import { Pool } from "pg";
import { attachDatabasePool } from "@neon/functions";
import { Store } from "./store.js";
import { createApp } from "./app.js";
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 2,
  idleTimeoutMillis: 1000,
  connectionTimeoutMillis: 5000,
  query_timeout: 4000,
});
attachDatabasePool(pool);
export default createApp(new Store(pool), {
  clientToken: process.env.DAVINCI_CLIENT_TOKEN ?? "",
  workerToken: process.env.DAVINCI_WORKER_TOKEN ?? "",
  host: process.env.DAVINCI_ALLOWED_HOST ?? "",
  workerId: "peters-mac",
});
