CREATE TABLE gbd_budget(day date PRIMARY KEY, sessions integer NOT NULL DEFAULT 0,client_calls integer NOT NULL DEFAULT 0);
CREATE TABLE gbd_sessions(id uuid PRIMARY KEY, expires_at double precision NOT NULL,last_claim double precision NOT NULL);
CREATE INDEX gbd_sessions_expiry ON gbd_sessions(expires_at);
CREATE TABLE gbd_jobs(id uuid PRIMARY KEY,operation text NOT NULL CHECK(operation IN ('version','project','timeline','media','inspect','prepare','edit')),payload jsonb NOT NULL,expires_at double precision NOT NULL,claimed boolean NOT NULL DEFAULT false,done boolean NOT NULL DEFAULT false,lease_hash text,ok boolean,result jsonb,receipt text);
CREATE INDEX gbd_job_pending ON gbd_jobs(expires_at) WHERE claimed=false AND done=false;
