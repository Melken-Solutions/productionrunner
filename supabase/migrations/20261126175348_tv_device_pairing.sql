SET LOCAL search_path TO productionrunner, pg_catalog;
CREATE TABLE IF NOT EXISTS tv_devices (
 id TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 name TEXT NOT NULL, station_id INTEGER, version INTEGER NOT NULL DEFAULT 1,
 applied_version INTEGER NOT NULL DEFAULT 0, last_seen TEXT, revoked_at TEXT, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS tv_devices_tenant ON tv_devices(tenant_id);
CREATE TABLE IF NOT EXISTS tv_pairings (
 id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE, browser_hash TEXT NOT NULL,
 expires_at DOUBLE PRECISION NOT NULL, device_id TEXT REFERENCES tv_devices(id), claimed_at TEXT
);
CREATE INDEX IF NOT EXISTS tv_pairings_expiry ON tv_pairings(expires_at);
REVOKE ALL ON tv_devices,tv_pairings FROM PUBLIC,productionrunner_runtime;
GRANT SELECT,INSERT,UPDATE ON tv_devices TO productionrunner_runtime;
GRANT SELECT,INSERT,UPDATE,DELETE ON tv_pairings TO productionrunner_runtime;
