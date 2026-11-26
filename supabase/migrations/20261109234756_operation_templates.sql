SET LOCAL search_path TO productionrunner, pg_catalog;

CREATE TABLE IF NOT EXISTS operation_templates (
 tenant_id INTEGER NOT NULL,template_key TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),active INTEGER NOT NULL CHECK(active IN (0,1)),data TEXT NOT NULL,actor_id INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,template_key,version),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS operation_templates_actor ON operation_templates(tenant_id,actor_id);
CREATE TRIGGER operation_templates_immutable BEFORE UPDATE OR DELETE ON operation_templates FOR EACH ROW EXECUTE FUNCTION part_event_no_update_fn();
GRANT SELECT,INSERT ON operation_templates TO productionrunner_runtime;
