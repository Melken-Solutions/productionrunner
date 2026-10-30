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
 object_key TEXT, object_size INTEGER,
 UNIQUE(tenant_id,pn,revision), UNIQUE(tenant_id,id),
 CHECK((drawing_pdf IS NOT NULL AND drawing_url IS NULL AND sha256 IS NOT NULL AND object_key IS NULL AND object_size IS NULL) OR
       (drawing_pdf IS NULL AND drawing_url IS NOT NULL AND sha256 IS NULL AND object_key IS NULL AND object_size IS NULL) OR
       (drawing_pdf IS NULL AND drawing_url IS NULL AND sha256 IS NOT NULL AND object_key IS NOT NULL AND object_size>0 AND object_size IS NOT NULL)),
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
 tenant_id INTEGER NOT NULL, part_revision_id TEXT PRIMARY KEY, content BLOB,
 sha256 TEXT NOT NULL, uploaded_by INTEGER NOT NULL, uploaded_at TEXT NOT NULL,
 object_key TEXT, object_size INTEGER,
 CHECK((content IS NOT NULL AND object_key IS NULL AND object_size IS NULL) OR (content IS NULL AND object_key IS NOT NULL AND object_size>0 AND object_size IS NOT NULL)),
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

CREATE TABLE IF NOT EXISTS organization_branding (
    tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),
    display_name TEXT NOT NULL DEFAULT '',
    primary_color TEXT NOT NULL DEFAULT '#102c3a',
    accent_color TEXT NOT NULL DEFAULT '#007477',
    logo BLOB,
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS storage_policies (
 tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),
 pdf_days INTEGER CHECK(pdf_days BETWEEN 1 AND 36500),
 dxf_days INTEGER CHECK(dxf_days BETWEEN 1 AND 36500),
 grace_days INTEGER NOT NULL DEFAULT 30 CHECK(grace_days BETWEEN 7 AND 365),
 version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS storage_file_controls (
 tenant_id INTEGER NOT NULL, part_revision_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('pdf','dxf')), keep_forever INTEGER NOT NULL CHECK(keep_forever IN (0,1)),
 version INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(tenant_id,part_revision_id,kind),
 FOREIGN KEY(tenant_id,part_revision_id) REFERENCES part_revisions(tenant_id,id)
);

CREATE TABLE IF NOT EXISTS part_imports (
 token TEXT PRIMARY KEY, tenant_id INTEGER NOT NULL, actor_id INTEGER NOT NULL,
 expires_at REAL NOT NULL, payload TEXT, report TEXT NOT NULL, result TEXT,
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS part_import_owner ON part_imports(tenant_id,actor_id);

CREATE TABLE IF NOT EXISTS fulfillment_records (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('pack','depart')),quantity INTEGER NOT NULL CHECK(quantity>0),
 destination TEXT NOT NULL,reference TEXT NOT NULL,method TEXT NOT NULL,notes TEXT NOT NULL,serials TEXT NOT NULL,
 actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS fulfillment_ticket ON fulfillment_records(tenant_id,ticket_id,kind);
CREATE TABLE IF NOT EXISTS fulfillment_units (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,record_id INTEGER NOT NULL,unit_id TEXT NOT NULL,kind TEXT NOT NULL,
 PRIMARY KEY(tenant_id,unit_id,kind),
 FOREIGN KEY(tenant_id,ticket_id,record_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id)
);
CREATE TABLE IF NOT EXISTS fulfillment_checks (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,name TEXT NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('pack','depart')),actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS fulfillment_check_results (
 tenant_id INTEGER NOT NULL,check_id INTEGER PRIMARY KEY,actor_id INTEGER NOT NULL,reference TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,check_id) REFERENCES fulfillment_checks(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS fulfillment_records_no_update BEFORE UPDATE ON fulfillment_records BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_records_no_delete BEFORE DELETE ON fulfillment_records BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_units_no_update BEFORE UPDATE ON fulfillment_units BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_units_no_delete BEFORE DELETE ON fulfillment_units BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_checks_no_update BEFORE UPDATE ON fulfillment_checks BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_checks_no_delete BEFORE DELETE ON fulfillment_checks BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_check_results_no_update BEFORE UPDATE ON fulfillment_check_results BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_check_results_no_delete BEFORE DELETE ON fulfillment_check_results BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS intake_requests (
 id TEXT PRIMARY KEY,tenant_id INTEGER NOT NULL,
 item_type TEXT NOT NULL CHECK(item_type IN ('standard','custom')),
 source TEXT NOT NULL CHECK(source IN ('manual','po','erp','email','smartsheet','other')),
 source_reference TEXT NOT NULL,requester TEXT NOT NULL,destination TEXT NOT NULL,required_date TEXT NOT NULL,
 pn TEXT NOT NULL,revision TEXT NOT NULL,quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 10000),description TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'preparing' CHECK(status IN ('preparing','approved','ticketed','cancelled')),
 part_id TEXT,approved_by INTEGER,approved_at TEXT,ticket_id TEXT UNIQUE,
 version INTEGER NOT NULL DEFAULT 1,created_by INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,part_id) REFERENCES part_revisions(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,approved_by) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,created_by) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS intake_tenant_status ON intake_requests(tenant_id,status,required_date);
CREATE TRIGGER IF NOT EXISTS intake_no_delete BEFORE DELETE ON intake_requests BEGIN SELECT RAISE(ABORT,'Request history cannot be removed'); END;
CREATE TRIGGER IF NOT EXISTS intake_terminal_no_update BEFORE UPDATE ON intake_requests WHEN OLD.status IN ('ticketed','cancelled') BEGIN SELECT RAISE(ABORT,'Converted/cancelled requests are immutable'); END;
CREATE TABLE IF NOT EXISTS intake_events (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,request_id TEXT NOT NULL,actor_id INTEGER NOT NULL,
 action TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,request_id) REFERENCES intake_requests(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS intake_history ON intake_events(tenant_id,request_id,id);
CREATE TRIGGER IF NOT EXISTS intake_event_no_update BEFORE UPDATE ON intake_events BEGIN SELECT RAISE(ABORT,'Request evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS intake_event_no_delete BEFORE DELETE ON intake_events BEGIN SELECT RAISE(ABORT,'Request evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS workflow_defaults (
 tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),
 require_intake INTEGER NOT NULL CHECK(require_intake IN (0,1)),checks TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0)
);
CREATE TABLE IF NOT EXISTS ticket_workflow (
 tenant_id INTEGER NOT NULL,ticket_id TEXT PRIMARY KEY,policy_version INTEGER NOT NULL,
 require_intake INTEGER NOT NULL CHECK(require_intake IN (0,1)),checks TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS ticket_workflow_no_update BEFORE UPDATE ON ticket_workflow BEGIN SELECT RAISE(ABORT,'Ticket workflow snapshot is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ticket_workflow_no_delete BEFORE DELETE ON ticket_workflow BEGIN SELECT RAISE(ABORT,'Ticket workflow snapshot is immutable'); END;

-- Append-only reversals preserve the original physical-action evidence.
CREATE TABLE IF NOT EXISTS fulfillment_reversals (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,record_id INTEGER PRIMARY KEY,kind TEXT NOT NULL,
 actor_id INTEGER NOT NULL,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 500),created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,record_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
-- Original unit links remain untouched; subsequent movements allow repeat cycles.
CREATE TABLE IF NOT EXISTS fulfillment_unit_entries (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,record_id INTEGER NOT NULL,unit_id TEXT NOT NULL,kind TEXT NOT NULL,
 PRIMARY KEY(record_id,unit_id),
 FOREIGN KEY(tenant_id,ticket_id,record_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id)
);
CREATE VIEW IF NOT EXISTS fulfillment_active_records AS
 SELECT f.* FROM fulfillment_records f WHERE NOT EXISTS
 (SELECT 1 FROM fulfillment_reversals r WHERE r.record_id=f.id AND r.tenant_id=f.tenant_id);
CREATE VIEW IF NOT EXISTS fulfillment_active_units AS
 SELECT f.* FROM fulfillment_units f JOIN fulfillment_active_records r ON r.id=f.record_id AND r.tenant_id=f.tenant_id
 UNION ALL
 SELECT f.* FROM fulfillment_unit_entries f JOIN fulfillment_active_records r ON r.id=f.record_id AND r.tenant_id=f.tenant_id;
CREATE TRIGGER IF NOT EXISTS fulfillment_reversals_no_update BEFORE UPDATE ON fulfillment_reversals BEGIN SELECT RAISE(ABORT,'Correction evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_reversals_no_delete BEFORE DELETE ON fulfillment_reversals BEGIN SELECT RAISE(ABORT,'Correction evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_unit_entries_no_update BEFORE UPDATE ON fulfillment_unit_entries BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fulfillment_unit_entries_no_delete BEFORE DELETE ON fulfillment_unit_entries BEGIN SELECT RAISE(ABORT,'Delivery evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS customer_returns (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,departure_id INTEGER NOT NULL,
 kind TEXT NOT NULL DEFAULT 'depart' CHECK(kind='depart'),quantity INTEGER NOT NULL CHECK(quantity>0),
 reference TEXT NOT NULL,reason TEXT NOT NULL,location TEXT NOT NULL,serials TEXT NOT NULL,
 actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,departure_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS return_departure ON customer_returns(tenant_id,departure_id);
CREATE TABLE IF NOT EXISTS customer_return_units (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,return_id INTEGER NOT NULL,unit_id TEXT NOT NULL,
 PRIMARY KEY(tenant_id,unit_id),
 FOREIGN KEY(tenant_id,ticket_id,return_id) REFERENCES customer_returns(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id)
);
CREATE TABLE IF NOT EXISTS customer_return_reviews (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,return_id INTEGER PRIMARY KEY,
 decision TEXT NOT NULL CHECK(decision IN ('repair_required','replacement_required','scrap_authorized','further_review')),
 notes TEXT NOT NULL,actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,return_id) REFERENCES customer_returns(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TRIGGER IF NOT EXISTS customer_returns_no_update BEFORE UPDATE ON customer_returns BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS customer_returns_no_delete BEFORE DELETE ON customer_returns BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS customer_return_units_no_update BEFORE UPDATE ON customer_return_units BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS customer_return_units_no_delete BEFORE DELETE ON customer_return_units BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS customer_return_reviews_no_update BEFORE UPDATE ON customer_return_reviews BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS customer_return_reviews_no_delete BEFORE DELETE ON customer_return_reviews BEGIN SELECT RAISE(ABORT,'Return evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS return_repairs (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,return_id INTEGER PRIMARY KEY,repair_ticket_id TEXT NOT NULL UNIQUE,
 actor_id INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,ticket_id,return_id,repair_ticket_id),
 FOREIGN KEY(tenant_id,ticket_id,return_id) REFERENCES customer_returns(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,repair_ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE TABLE IF NOT EXISTS return_repair_units (
 tenant_id INTEGER NOT NULL,return_id INTEGER NOT NULL,
 original_unit_id TEXT NOT NULL,repair_unit_id TEXT PRIMARY KEY,
 ticket_id TEXT NOT NULL,repair_ticket_id TEXT NOT NULL,
 UNIQUE(return_id,original_unit_id),
 FOREIGN KEY(tenant_id,ticket_id,return_id,repair_ticket_id) REFERENCES return_repairs(tenant_id,ticket_id,return_id,repair_ticket_id),
 FOREIGN KEY(tenant_id,ticket_id,original_unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,repair_ticket_id,repair_unit_id) REFERENCES serial_units(tenant_id,ticket_id,id)
);
CREATE TABLE IF NOT EXISTS return_closure_events (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,return_id INTEGER NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('closed','reopened')),reason TEXT NOT NULL,actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,return_id) REFERENCES customer_returns(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS return_closure_latest ON return_closure_events(tenant_id,return_id,id);
CREATE TRIGGER IF NOT EXISTS return_repairs_no_update BEFORE UPDATE ON return_repairs BEGIN SELECT RAISE(ABORT,'Repair links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS return_repairs_no_delete BEFORE DELETE ON return_repairs BEGIN SELECT RAISE(ABORT,'Repair links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS return_repair_units_no_update BEFORE UPDATE ON return_repair_units BEGIN SELECT RAISE(ABORT,'Repair links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS return_repair_units_no_delete BEFORE DELETE ON return_repair_units BEGIN SELECT RAISE(ABORT,'Repair links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS return_closure_no_update BEFORE UPDATE ON return_closure_events BEGIN SELECT RAISE(ABORT,'Return closure evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS return_closure_no_delete BEFORE DELETE ON return_closure_events BEGIN SELECT RAISE(ABORT,'Return closure evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS work_settings (
 tenant_id INTEGER PRIMARY KEY REFERENCES tenants(id),required INTEGER NOT NULL CHECK(required IN (0,1)),version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS work_reasons (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,
 UNIQUE(tenant_id,name)
);
CREATE TABLE IF NOT EXISTS work_sessions (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,unit_id TEXT,operation_id INTEGER NOT NULL,
 station_id INTEGER NOT NULL,station_name TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN ('setup','production','machine')),
 hot_priority INTEGER,created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id,unit_id) REFERENCES serial_units(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS work_events (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,session_id INTEGER NOT NULL,
 action TEXT NOT NULL CHECK(action IN ('start','pause','resume','stop','finish')),actor_id INTEGER NOT NULL,
 reason TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,session_id) REFERENCES work_sessions(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS work_events_owner ON work_events(tenant_id,id);
CREATE INDEX IF NOT EXISTS work_events_session ON work_events(tenant_id,session_id,id);
CREATE INDEX IF NOT EXISTS work_target ON work_sessions(tenant_id,ticket_id,unit_id,operation_id);
CREATE TABLE IF NOT EXISTS work_time_corrections (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,event_id INTEGER NOT NULL,
 effective_at TEXT NOT NULL,reason TEXT NOT NULL,actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,event_id) REFERENCES work_events(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS work_correction_event ON work_time_corrections(tenant_id,event_id,id);
CREATE TRIGGER IF NOT EXISTS work_sessions_no_update BEFORE UPDATE ON work_sessions BEGIN SELECT RAISE(ABORT,'Work evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS work_sessions_no_delete BEFORE DELETE ON work_sessions BEGIN SELECT RAISE(ABORT,'Work evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS work_events_no_update BEFORE UPDATE ON work_events BEGIN SELECT RAISE(ABORT,'Work evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS work_events_no_delete BEFORE DELETE ON work_events BEGIN SELECT RAISE(ABORT,'Work evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS work_corrections_no_update BEFORE UPDATE ON work_time_corrections BEGIN SELECT RAISE(ABORT,'Time corrections are immutable'); END;
CREATE TRIGGER IF NOT EXISTS work_corrections_no_delete BEFORE DELETE ON work_time_corrections BEGIN SELECT RAISE(ABORT,'Time corrections are immutable'); END;

-- Deployment-wide numbering; allocated under the ticket creation write lock.
CREATE TABLE IF NOT EXISTS jobrunner_sequences (
 day TEXT PRIMARY KEY,
 sequence INTEGER NOT NULL CHECK(sequence >= 0)
);

CREATE TABLE IF NOT EXISTS outside_steps (
 tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,operation_id INTEGER PRIMARY KEY,
 vendor TEXT NOT NULL,service TEXT NOT NULL,instructions TEXT NOT NULL,inspection_requirements TEXT NOT NULL,due_date TEXT NOT NULL,reference TEXT NOT NULL,
 UNIQUE(tenant_id,ticket_id,operation_id),
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id)
);
CREATE TABLE IF NOT EXISTS outside_moves (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,operation_id INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('send','receive')),parent_id INTEGER,
 quantity INTEGER NOT NULL CHECK(quantity>0),unit_ids TEXT NOT NULL,actor_id INTEGER NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL,
 CHECK((kind='send' AND parent_id IS NULL) OR (kind='receive' AND parent_id IS NOT NULL)),
 UNIQUE(tenant_id,ticket_id,id),UNIQUE(tenant_id,ticket_id,operation_id,id),
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES outside_steps(tenant_id,ticket_id,operation_id),
 FOREIGN KEY(tenant_id,ticket_id,operation_id,parent_id) REFERENCES outside_moves(tenant_id,ticket_id,operation_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS outside_moves_operation ON outside_moves(tenant_id,operation_id,id);
CREATE TABLE IF NOT EXISTS outside_inspections (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,receipt_id INTEGER NOT NULL,
 result TEXT NOT NULL CHECK(result IN ('pass','fail')),actor_id INTEGER NOT NULL,notes TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,receipt_id) REFERENCES outside_moves(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS outside_inspection_receipt ON outside_inspections(tenant_id,receipt_id,id);
CREATE TRIGGER IF NOT EXISTS outside_steps_frozen_update BEFORE UPDATE ON outside_steps WHEN EXISTS(SELECT 1 FROM outside_moves WHERE operation_id=OLD.operation_id) BEGIN SELECT RAISE(ABORT,'Outside plans with custody evidence are immutable'); END;
CREATE TRIGGER IF NOT EXISTS outside_steps_frozen_delete BEFORE DELETE ON outside_steps WHEN EXISTS(SELECT 1 FROM outside_moves WHERE operation_id=OLD.operation_id) BEGIN SELECT RAISE(ABORT,'Outside plans with custody evidence are immutable'); END;
CREATE TRIGGER IF NOT EXISTS outside_moves_no_update BEFORE UPDATE ON outside_moves BEGIN SELECT RAISE(ABORT,'Outside processing evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS outside_moves_no_delete BEFORE DELETE ON outside_moves BEGIN SELECT RAISE(ABORT,'Outside processing evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS outside_inspections_no_update BEFORE UPDATE ON outside_inspections BEGIN SELECT RAISE(ABORT,'Outside processing evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS outside_inspections_no_delete BEFORE DELETE ON outside_inspections BEGIN SELECT RAISE(ABORT,'Outside processing evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS request_outside (
 tenant_id INTEGER NOT NULL,request_id TEXT PRIMARY KEY,
 vendor TEXT NOT NULL,service TEXT NOT NULL,instructions TEXT NOT NULL,
 inspection_requirements TEXT NOT NULL,due_date TEXT NOT NULL,reference TEXT NOT NULL,
 FOREIGN KEY(tenant_id,request_id) REFERENCES intake_requests(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS request_outside_frozen_insert BEFORE INSERT ON request_outside WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=NEW.tenant_id AND id=NEW.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled outside plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_outside_frozen_update BEFORE UPDATE ON request_outside WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled outside plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_outside_frozen_delete BEFORE DELETE ON request_outside WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled outside plans are immutable'); END;

CREATE TABLE IF NOT EXISTS materials (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 name TEXT NOT NULL,metal TEXT NOT NULL,gauge TEXT NOT NULL,finish TEXT NOT NULL,
 alloy TEXT NOT NULL,thickness_mm TEXT NOT NULL,stock_size TEXT NOT NULL,handling TEXT NOT NULL,required_service TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,id),UNIQUE(tenant_id,name)
);
CREATE TABLE IF NOT EXISTS request_materials (
 tenant_id INTEGER NOT NULL,request_id TEXT PRIMARY KEY,material_id INTEGER NOT NULL,material_version INTEGER NOT NULL,spec TEXT NOT NULL,planned_sheets TEXT NOT NULL,notes TEXT NOT NULL,
 FOREIGN KEY(tenant_id,request_id) REFERENCES intake_requests(tenant_id,id),
 FOREIGN KEY(tenant_id,material_id) REFERENCES materials(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS request_materials_no_update BEFORE UPDATE ON request_materials WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Material evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_materials_no_delete BEFORE DELETE ON request_materials WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Material evidence is immutable'); END;
CREATE TABLE IF NOT EXISTS ticket_materials (
 tenant_id INTEGER NOT NULL,ticket_id TEXT PRIMARY KEY,material_id INTEGER NOT NULL,material_version INTEGER NOT NULL,spec TEXT NOT NULL,planned_sheets TEXT NOT NULL,notes TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,material_id) REFERENCES materials(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS ticket_materials_no_update BEFORE UPDATE ON ticket_materials BEGIN SELECT RAISE(ABORT,'Material evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ticket_materials_no_delete BEFORE DELETE ON ticket_materials BEGIN SELECT RAISE(ABORT,'Material evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_materials_frozen_insert BEFORE INSERT ON request_materials WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=NEW.tenant_id AND id=NEW.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Material evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS vendors (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 name TEXT NOT NULL COLLATE NOCASE,services TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,name)
);

CREATE TABLE IF NOT EXISTS job_plans (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,data TEXT NOT NULL,
 actor_id INTEGER NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS job_plans_ticket ON job_plans(tenant_id,ticket_id,id);
CREATE TRIGGER IF NOT EXISTS job_plans_no_update BEFORE UPDATE ON job_plans BEGIN SELECT RAISE(ABORT,'Plan revisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS job_plans_no_delete BEFORE DELETE ON job_plans BEGIN SELECT RAISE(ABORT,'Plan revisions are immutable'); END;
CREATE TABLE IF NOT EXISTS first_piece_results (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL,ticket_id TEXT NOT NULL,operation_id INTEGER NOT NULL,
 plan_id INTEGER NOT NULL REFERENCES job_plans(id),station_id INTEGER NOT NULL,result TEXT NOT NULL CHECK(result IN ('pass','fail')),
 notes TEXT NOT NULL,actor_id INTEGER NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id,operation_id) REFERENCES operations(tenant_id,ticket_id,id),
 FOREIGN KEY(tenant_id,station_id) REFERENCES stations(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id)
);
CREATE INDEX IF NOT EXISTS first_piece_operation ON first_piece_results(tenant_id,ticket_id,operation_id,id);
CREATE TRIGGER IF NOT EXISTS first_piece_no_update BEFORE UPDATE ON first_piece_results BEGIN SELECT RAISE(ABORT,'First-piece evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS first_piece_no_delete BEFORE DELETE ON first_piece_results BEGIN SELECT RAISE(ABORT,'First-piece evidence is immutable'); END;

CREATE TABLE IF NOT EXISTS request_job_plans (
 tenant_id INTEGER NOT NULL, request_id TEXT PRIMARY KEY, data TEXT NOT NULL,
 FOREIGN KEY(tenant_id,request_id) REFERENCES intake_requests(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS request_job_plan_frozen_insert BEFORE INSERT ON request_job_plans WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=NEW.tenant_id AND id=NEW.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_job_plan_frozen_update BEFORE UPDATE ON request_job_plans WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled plans are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_job_plan_frozen_delete BEFORE DELETE ON request_job_plans WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Converted or cancelled plans are immutable'); END;

CREATE TABLE IF NOT EXISTS material_choices (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 field TEXT NOT NULL CHECK(field IN ('metal','alloy','gauge','thickness_mm','finish','stock_size','required_service')),
 value TEXT NOT NULL, metal TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,field,value,metal)
);
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'metal',metal,'' FROM materials WHERE metal<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'alloy',alloy,metal FROM materials WHERE alloy<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'gauge',gauge,metal FROM materials WHERE gauge<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'thickness_mm',thickness_mm,metal FROM materials WHERE thickness_mm<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'finish',finish,metal FROM materials WHERE finish<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'stock_size',stock_size,'' FROM materials WHERE stock_size<>'';
INSERT OR IGNORE INTO material_choices(tenant_id,field,value,metal) SELECT tenant_id,'required_service',required_service,'' FROM materials WHERE required_service<>'';

CREATE TABLE IF NOT EXISTS custom_field_definitions (
 id INTEGER PRIMARY KEY,tenant_id INTEGER NOT NULL REFERENCES tenants(id),label TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('text','number','date','select','boolean')),options TEXT NOT NULL,
 required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,label)
);
CREATE TABLE IF NOT EXISTS request_custom_fields (
 tenant_id INTEGER NOT NULL,request_id TEXT PRIMARY KEY,data TEXT NOT NULL,
 FOREIGN KEY(tenant_id,request_id) REFERENCES intake_requests(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS ticket_custom_fields (
 tenant_id INTEGER NOT NULL,ticket_id TEXT PRIMARY KEY,data TEXT NOT NULL,
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id)
);
CREATE TRIGGER IF NOT EXISTS ticket_custom_fields_no_update BEFORE UPDATE ON ticket_custom_fields BEGIN SELECT RAISE(ABORT,'Job custom field snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ticket_custom_fields_no_delete BEFORE DELETE ON ticket_custom_fields BEGIN SELECT RAISE(ABORT,'Job custom field snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_custom_fields_frozen_insert BEFORE INSERT ON request_custom_fields WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=NEW.tenant_id AND id=NEW.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Closed request fields are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_custom_fields_frozen_update BEFORE UPDATE ON request_custom_fields WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Closed request fields are immutable'); END;
CREATE TRIGGER IF NOT EXISTS request_custom_fields_frozen_delete BEFORE DELETE ON request_custom_fields WHEN EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=OLD.tenant_id AND id=OLD.request_id AND status IN ('ticketed','cancelled')) BEGIN SELECT RAISE(ABORT,'Closed request fields are immutable'); END;

-- Organization onboarding and hashed, expiring invitation credentials.
CREATE TABLE IF NOT EXISTS onboarding_steps (
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), step TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('todo','later','reviewed')), PRIMARY KEY(tenant_id,step)
);
CREATE TABLE IF NOT EXISTS employee_invitations (
 id INTEGER PRIMARY KEY, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 username TEXT NOT NULL, roles TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE,
 expires_at REAL NOT NULL, created_by INTEGER NOT NULL REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','revoked'))
);
CREATE INDEX IF NOT EXISTS invitations_tenant ON employee_invitations(tenant_id,id);

CREATE TABLE IF NOT EXISTS shipping_destinations (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 name TEXT NOT NULL, address TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,name)
);
CREATE TABLE IF NOT EXISTS shipping_rules (
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), source TEXT NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('stock','order')), method TEXT NOT NULL CHECK(method IN ('fedex','ltl')),
 label_owner TEXT NOT NULL CHECK(label_owner IN ('planner','packing','either')),
 PRIMARY KEY(tenant_id,source)
);
CREATE TABLE IF NOT EXISTS shipping_plans (
 tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, destination_id INTEGER NOT NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('stock','order')), method TEXT NOT NULL CHECK(method IN ('fedex','ltl')),
 label_owner TEXT NOT NULL CHECK(label_owner IN ('planner','packing','either')), source TEXT NOT NULL, source_reference TEXT NOT NULL,
 PRIMARY KEY(tenant_id,ticket_id), FOREIGN KEY(tenant_id,ticket_id) REFERENCES tickets(tenant_id,id),
 FOREIGN KEY(tenant_id,destination_id) REFERENCES shipping_destinations(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS shipping_loads (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL REFERENCES tenants(id), destination_id INTEGER NOT NULL,
 destination_name TEXT NOT NULL, destination_address TEXT NOT NULL,
 method TEXT NOT NULL CHECK(method IN ('fedex','ltl')), label_owner TEXT NOT NULL CHECK(label_owner IN ('planner','packing','either')),
 state TEXT NOT NULL DEFAULT 'building' CHECK(state IN ('building','requested','ready','departed')),
 version INTEGER NOT NULL DEFAULT 0, manifest_version INTEGER NOT NULL DEFAULT 0,
 carrier TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,destination_id) REFERENCES shipping_destinations(tenant_id,id)
);
CREATE TABLE IF NOT EXISTS shipping_boxes (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, pack_record_id INTEGER NOT NULL,
 kind TEXT NOT NULL DEFAULT 'pack' CHECK(kind='pack'), load_id INTEGER, weight_kg TEXT NOT NULL, dimensions_cm TEXT NOT NULL,
 voided INTEGER NOT NULL DEFAULT 0 CHECK(voided IN (0,1)), created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id) REFERENCES shipping_plans(tenant_id,ticket_id),
 FOREIGN KEY(tenant_id,ticket_id,pack_record_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind),
 FOREIGN KEY(tenant_id,load_id) REFERENCES shipping_loads(tenant_id,id)
);
CREATE UNIQUE INDEX IF NOT EXISTS shipping_active_pack ON shipping_boxes(tenant_id,pack_record_id) WHERE voided=0;
CREATE INDEX IF NOT EXISTS shipping_box_ticket ON shipping_boxes(tenant_id,ticket_id);
CREATE INDEX IF NOT EXISTS shipping_box_load ON shipping_boxes(tenant_id,load_id);
CREATE TABLE IF NOT EXISTS shipping_labels (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL, load_id INTEGER NOT NULL, manifest_version INTEGER NOT NULL,
 filename TEXT NOT NULL, content BLOB, object_key TEXT, object_size INTEGER, sha256 TEXT NOT NULL,
 actor_id INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 UNIQUE(tenant_id,id), FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id), FOREIGN KEY(tenant_id,load_id) REFERENCES shipping_loads(tenant_id,id),
 CHECK((content IS NOT NULL AND object_key IS NULL AND object_size IS NULL) OR (content IS NULL AND object_key IS NOT NULL AND object_size>0))
);
CREATE INDEX IF NOT EXISTS shipping_label_load ON shipping_labels(tenant_id,load_id,id);
CREATE TABLE IF NOT EXISTS shipping_departures (
 tenant_id INTEGER NOT NULL, load_id INTEGER NOT NULL, ticket_id TEXT NOT NULL, record_id INTEGER NOT NULL,
 kind TEXT NOT NULL DEFAULT 'depart' CHECK(kind='depart'), PRIMARY KEY(tenant_id,record_id),
 FOREIGN KEY(tenant_id,load_id) REFERENCES shipping_loads(tenant_id,id),
 FOREIGN KEY(tenant_id,ticket_id,record_id,kind) REFERENCES fulfillment_records(tenant_id,ticket_id,id,kind)
);
CREATE TABLE IF NOT EXISTS shipping_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id INTEGER NOT NULL REFERENCES tenants(id), load_id INTEGER,
 actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(tenant_id,actor_id) REFERENCES memberships(tenant_id,user_id),
 FOREIGN KEY(tenant_id,load_id) REFERENCES shipping_loads(tenant_id,id)
);
CREATE INDEX IF NOT EXISTS shipping_events_load ON shipping_events(tenant_id,load_id,id);

CREATE TRIGGER IF NOT EXISTS shipping_labels_no_update BEFORE UPDATE ON shipping_labels BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS shipping_labels_no_delete BEFORE DELETE ON shipping_labels BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS shipping_events_no_update BEFORE UPDATE ON shipping_events BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS shipping_events_no_delete BEFORE DELETE ON shipping_events BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS shipping_departures_no_update BEFORE UPDATE ON shipping_departures BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE TRIGGER IF NOT EXISTS shipping_departures_no_delete BEFORE DELETE ON shipping_departures BEGIN SELECT RAISE(ABORT,'Shipping evidence is immutable'); END;

CREATE INDEX IF NOT EXISTS shipping_plans_owner_lookup ON shipping_plans(tenant_id,destination_id);

CREATE INDEX IF NOT EXISTS shipping_loads_owner_lookup ON shipping_loads(tenant_id,destination_id);

CREATE INDEX IF NOT EXISTS shipping_labels_owner_lookup ON shipping_labels(tenant_id,actor_id);

CREATE INDEX IF NOT EXISTS shipping_events_owner_lookup ON shipping_events(tenant_id,actor_id);

CREATE INDEX IF NOT EXISTS shipping_departures_owner_lookup ON shipping_departures(tenant_id,load_id);
