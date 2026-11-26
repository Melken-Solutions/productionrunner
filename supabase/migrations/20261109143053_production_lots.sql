SET LOCAL search_path TO productionrunner, pg_catalog;
CREATE TABLE IF NOT EXISTS lot_jobs (
 tenant_id INTEGER NOT NULL,ticket_id TEXT PRIMARY KEY,
 UNIQUE(tenant_id,ticket_id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS production_lots (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,unit_id TEXT PRIMARY KEY,
 quantity INTEGER NOT NULL CHECK(quantity>0),parent_id TEXT,scrapped INTEGER NOT NULL DEFAULT 0 CHECK(scrapped IN (0,1)),
 UNIQUE(tenant_id,unit_id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES lot_jobs(tenant_id,ticket_id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,parent_id) REFERENCES serial_units(tenant_id,ticket_id,id)
);
CREATE INDEX IF NOT EXISTS production_lots_ticket ON production_lots(tenant_id,ticket_id);
CREATE INDEX IF NOT EXISTS production_lots_parent ON production_lots(tenant_id,ticket_id,parent_id);
CREATE TABLE IF NOT EXISTS lot_check_results (
 tenant_id INTEGER NOT NULL,check_id INTEGER NOT NULL,unit_id TEXT NOT NULL,actor_id INTEGER NOT NULL,reference TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(check_id,unit_id),
 FOREIGN KEY(tenant_id,check_id) REFERENCES fulfillment_checks(tenant_id,id),
 FOREIGN KEY(tenant_id,unit_id) REFERENCES production_lots(tenant_id,unit_id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS lot_checks_unit ON lot_check_results(tenant_id,unit_id);
CREATE INDEX IF NOT EXISTS lot_checks_actor ON lot_check_results(tenant_id,actor_id);

GRANT SELECT,INSERT ON lot_jobs,production_lots TO productionrunner_runtime;
GRANT UPDATE(quantity,scrapped) ON production_lots TO productionrunner_runtime;
GRANT SELECT,INSERT ON lot_check_results TO productionrunner_runtime;
CREATE FUNCTION lot_check_immutable_fn() RETURNS trigger LANGUAGE plpgsql SET search_path TO productionrunner, pg_catalog AS $$ BEGIN RAISE EXCEPTION 'Lot checkpoint evidence is immutable' USING ERRCODE='23514'; END $$;
CREATE TRIGGER lot_check_immutable BEFORE UPDATE OR DELETE ON lot_check_results FOR EACH ROW EXECUTE FUNCTION lot_check_immutable_fn();

CREATE FUNCTION lot_identity_fn() RETURNS trigger LANGUAGE plpgsql SET search_path TO productionrunner, pg_catalog AS $$ BEGIN
 IF TG_OP='DELETE' OR TG_TABLE_NAME='lot_jobs' THEN RAISE EXCEPTION 'Lot identity cannot be deleted or changed' USING ERRCODE='23514'; END IF;
 IF NEW.tenant_id<>OLD.tenant_id OR NEW.ticket_id<>OLD.ticket_id OR NEW.unit_id<>OLD.unit_id OR NEW.parent_id IS DISTINCT FROM OLD.parent_id OR NEW.quantity>OLD.quantity OR (OLD.scrapped=1 AND (NEW.scrapped<>1 OR NEW.quantity<>OLD.quantity)) THEN RAISE EXCEPTION 'Lot identity and scrapped quantities are immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER lot_job_immutable BEFORE UPDATE OR DELETE ON lot_jobs FOR EACH ROW EXECUTE FUNCTION lot_identity_fn();
CREATE TRIGGER lot_identity BEFORE UPDATE OR DELETE ON production_lots FOR EACH ROW EXECUTE FUNCTION lot_identity_fn();
