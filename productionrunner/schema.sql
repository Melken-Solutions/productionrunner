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
CREATE TABLE IF NOT EXISTS part_revisions (
 id TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, pn TEXT NOT NULL, revision TEXT NOT NULL,
 description TEXT NOT NULL, drawing_pdf BLOB, drawing_url TEXT, sha256 TEXT,
 created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(tenant_id,pn,revision), UNIQUE(tenant_id,id),
 CHECK((drawing_pdf IS NOT NULL AND drawing_url IS NULL AND sha256 IS NOT NULL) OR
       (drawing_pdf IS NULL AND drawing_url IS NOT NULL AND sha256 IS NULL)),
 FOREIGN KEY(tenant_id,created_by) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS ticket_drawings (
 tenant_id INTEGER NOT NULL, ticket_id TEXT PRIMARY KEY, part_revision_id TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,part_revision_id) REFERENCES part_revisions(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS part_events (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, part_revision_id TEXT NOT NULL,
 actor_id INTEGER NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,part_revision_id) REFERENCES part_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS part_revision_no_update BEFORE UPDATE ON part_revisions BEGIN SELECT RAISE(ABORT,'Part revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS part_revision_no_delete BEFORE DELETE ON part_revisions BEGIN SELECT RAISE(ABORT,'Part revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ticket_drawing_no_update BEFORE UPDATE ON ticket_drawings BEGIN SELECT RAISE(ABORT,'Ticket drawings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ticket_drawing_no_delete BEFORE DELETE ON ticket_drawings BEGIN SELECT RAISE(ABORT,'Ticket drawings are immutable'); END;
CREATE TRIGGER IF NOT EXISTS part_event_no_update BEFORE UPDATE ON part_events BEGIN SELECT RAISE(ABORT,'Part events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS part_event_no_delete BEFORE DELETE ON part_events BEGIN SELECT RAISE(ABORT,'Part events are immutable'); END;
CREATE TABLE IF NOT EXISTS part_dxf (
 tenant_id INTEGER NOT NULL, part_revision_id TEXT PRIMARY KEY, content BLOB NOT NULL,
 sha256 TEXT NOT NULL, uploaded_by INTEGER NOT NULL, uploaded_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,part_revision_id) REFERENCES part_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,uploaded_by) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS part_dxf_no_update BEFORE UPDATE ON part_dxf BEGIN SELECT RAISE(ABORT,'DXF revision files are immutable'); END;
CREATE TRIGGER IF NOT EXISTS part_dxf_no_delete BEFORE DELETE ON part_dxf BEGIN SELECT RAISE(ABORT,'DXF revision files are immutable'); END;

-- Separate edit versions from session epochs: PIN edits need not log out password sessions.
CREATE TABLE IF NOT EXISTS membership_controls (
 tenant_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 version INTEGER NOT NULL DEFAULT 0, session_epoch INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(tenant_id,user_id),
 FOREIGN KEY(tenant_id,user_id) REFERENCES memberships(tenant_id,user_id)
);

CREATE TABLE IF NOT EXISTS ticket_cancellations (
 tenant_id INTEGER NOT NULL, ticket_id TEXT PRIMARY KEY, actor_id INTEGER NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS cancellations_no_update BEFORE UPDATE ON ticket_cancellations BEGIN SELECT RAISE(ABORT,'Cancellation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cancellations_no_delete BEFORE DELETE ON ticket_cancellations BEGIN SELECT RAISE(ABORT,'Cancellation is immutable'); END;
CREATE TABLE IF NOT EXISTS rework_cases (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, unit_id TEXT,
 authorized_by INTEGER NOT NULL, reason TEXT NOT NULL, instructions TEXT NOT NULL, criteria TEXT NOT NULL,
 created_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
 completed_by INTEGER, completion_notes TEXT, completed_at TEXT,
 inspector_id INTEGER, result TEXT CHECK(result IN ('pass','fail')), inspection_notes TEXT, inspected_at TEXT,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,authorized_by) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,completed_by) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,inspector_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_batch_rework ON rework_cases(ticket_id) WHERE unit_id IS NULL AND result IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS one_open_unit_rework ON rework_cases(unit_id) WHERE unit_id IS NOT NULL AND result IS NULL;
CREATE TRIGGER IF NOT EXISTS rework_no_delete BEFORE DELETE ON rework_cases BEGIN SELECT RAISE(ABORT,'Rework history cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS rework_final_no_update BEFORE UPDATE ON rework_cases WHEN OLD.result IS NOT NULL BEGIN SELECT RAISE(ABORT,'Final rework evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS organization_profiles (
 tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),
 contact_name TEXT NOT NULL DEFAULT '', contact_email TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS developer_accounts (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS platform_events (
 id INTEGER PRIMARY KEY, actor TEXT NOT NULL, tenant_id INTEGER REFERENCES tenants(id),
 action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS platform_events_no_update BEFORE UPDATE ON platform_events BEGIN SELECT RAISE(ABORT,'Platform history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS platform_events_no_delete BEFORE DELETE ON platform_events BEGIN SELECT RAISE(ABORT,'Platform history is immutable'); END;

CREATE TABLE IF NOT EXISTS stations (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id), name TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,name), UNIQUE(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS operation_assignments (
 tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, operation_id INTEGER PRIMARY KEY,
 station_id INTEGER NOT NULL, operator_id INTEGER,
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS ticket_dispatch (
 tenant_id INTEGER NOT NULL, ticket_id TEXT PRIMARY KEY, released INTEGER NOT NULL DEFAULT 0 CHECK(released IN (0,1)),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS completion_assignments (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, operation_id INTEGER NOT NULL,
 unit_id TEXT, station_id INTEGER, station_name TEXT, assigned_user_id INTEGER, assigned_username TEXT,
 actual_user_id INTEGER NOT NULL, completed_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id),
 FOREIGN KEY(tenant_id,assigned_user_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,actual_user_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS batch_assignment_snapshot ON completion_assignments(operation_id) WHERE unit_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS unit_assignment_snapshot ON completion_assignments(unit_id,operation_id) WHERE unit_id IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS completion_assignments_no_update BEFORE UPDATE ON completion_assignments BEGIN SELECT RAISE(ABORT,'Completion assignments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS completion_assignments_no_delete BEFORE DELETE ON completion_assignments BEGIN SELECT RAISE(ABORT,'Completion assignments are immutable'); END;
CREATE TABLE IF NOT EXISTS station_events (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,station_id INTEGER NOT NULL,actor_id INTEGER NOT NULL,
 action TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS station_events_no_update BEFORE UPDATE ON station_events BEGIN SELECT RAISE(ABORT,'Station history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS station_events_no_delete BEFORE DELETE ON station_events BEGIN SELECT RAISE(ABORT,'Station history is immutable'); END;
