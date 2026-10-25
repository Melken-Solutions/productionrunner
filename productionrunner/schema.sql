PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tenants (
 id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), user_id INTEGER NOT NULL REFERENCES users(id),
 roles TEXT NOT NULL, PRIMARY KEY(tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS tickets (
 id TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), pn TEXT NOT NULL, revision TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity > 0), tracking TEXT NOT NULL CHECK(tracking IN ('batch','serial')),
 serials TEXT NOT NULL, instructions TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ready','in_progress','awaiting_inspection','accepted','held')),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id, id),
 FOREIGN KEY(tenant_id, created_by) REFERENCES memberships(tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS operations (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL REFERENCES tickets(id),
 sequence INTEGER NOT NULL, name TEXT NOT NULL, instructions TEXT NOT NULL,
 completed_by INTEGER REFERENCES users(id), completed_at TEXT,
 UNIQUE(ticket_id, sequence),
 FOREIGN KEY(tenant_id, ticket_id) REFERENCES tickets(tenant_id, id),
 FOREIGN KEY(tenant_id, completed_by) REFERENCES memberships(tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS inspections (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(id),
 inspector_id INTEGER NOT NULL REFERENCES users(id), result TEXT NOT NULL CHECK(result IN ('pass','fail')),
 notes TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id, ticket_id) REFERENCES tickets(tenant_id, id),
 FOREIGN KEY(tenant_id, inspector_id) REFERENCES memberships(tenant_id, user_id)
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL REFERENCES tickets(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id, ticket_id) REFERENCES tickets(tenant_id, id),
 FOREIGN KEY(tenant_id, actor_id) REFERENCES memberships(tenant_id, user_id)
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
CREATE INDEX IF NOT EXISTS tickets_tenant_created ON tickets(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS events_tenant_ticket ON events(tenant_id, ticket_id, id);
