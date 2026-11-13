-- Server-only application role; no API-facing schema or public grants.
DO $$ BEGIN
 IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='productionrunner_runtime') THEN
  CREATE ROLE productionrunner_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
END $$;
GRANT USAGE ON SCHEMA productionrunner TO productionrunner_runtime;
GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA productionrunner TO productionrunner_runtime;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA productionrunner TO productionrunner_runtime;
REVOKE INSERT,UPDATE,DELETE ON productionrunner.schema_migrations FROM productionrunner_runtime;
-- Immutable-history triggers still apply to the runtime role. It owns no tables
-- and cannot disable triggers, replace functions, or change the schema.
