ALTER TABLE gbd_jobs DROP CONSTRAINT gbd_jobs_operation_check, ADD CONSTRAINT gbd_jobs_operation_check CHECK(operation IN ('version','project','timeline','media','inspect','prepare','edit'));
