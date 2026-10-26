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
CREATE TABLE IF NOT EXISTS pin_credentials (
 tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL, pin_hash TEXT,
 version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS login_limits (
 key TEXT PRIMARY KEY, failures INTEGER NOT NULL, expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS login_limits_expiry ON login_limits(expires_at);
CREATE TABLE IF NOT EXISTS auth_events (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 action TEXT NOT NULL, actor TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS auth_events_tenant ON auth_events(tenant_id,id);
CREATE TRIGGER IF NOT EXISTS auth_events_no_update BEFORE UPDATE ON auth_events BEGIN SELECT RAISE(ABORT, 'Authentication events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS auth_events_no_delete BEFORE DELETE ON auth_events BEGIN SELECT RAISE(ABORT, 'Authentication events are immutable'); END;
CREATE UNIQUE INDEX IF NOT EXISTS operations_owner_id ON operations(tenant_id,ticket_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS events_owner_id ON events(tenant_id,ticket_id,id);
CREATE TABLE IF NOT EXISTS serial_units (
 id TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, serial TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'ready' CHECK(state IN ('ready','in_progress','awaiting_inspection','accepted','held')),
 version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,ticket_id,id), UNIQUE(ticket_id,serial),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS unit_completions (
 tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, unit_id TEXT NOT NULL, operation_id INTEGER NOT NULL,
 actor_id INTEGER NOT NULL, completed_at TEXT NOT NULL,
 PRIMARY KEY(unit_id,operation_id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS unit_inspections (
 tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, unit_id TEXT PRIMARY KEY,
 inspector_id INTEGER NOT NULL, result TEXT NOT NULL CHECK(result IN ('pass','fail')),
 notes TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,inspector_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS unit_event_links (
 tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, unit_id TEXT NOT NULL, event_id INTEGER PRIMARY KEY,
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,event_id) REFERENCES events(tenant_id,ticket_id,id)
);
CREATE INDEX IF NOT EXISTS unit_events_unit ON unit_event_links(tenant_id,unit_id,event_id);
CREATE TRIGGER IF NOT EXISTS unit_links_no_update BEFORE UPDATE ON unit_event_links BEGIN SELECT RAISE(ABORT, 'Unit audit links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS unit_links_no_delete BEFORE DELETE ON unit_event_links BEGIN SELECT RAISE(ABORT, 'Unit audit links are immutable'); END;
CREATE TABLE IF NOT EXISTS ticket_priorities (
 tenant_id INTEGER NOT NULL, ticket_id TEXT PRIMARY KEY,
 priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 99), hot_since TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS ticket_priority_order ON ticket_priorities(tenant_id,priority,hot_since);
