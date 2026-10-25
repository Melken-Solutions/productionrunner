PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
 roles TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tickets (
 id TEXT PRIMARY KEY, pn TEXT NOT NULL, revision TEXT NOT NULL,
 quantity INTEGER NOT NULL CHECK(quantity > 0), tracking TEXT NOT NULL CHECK(tracking IN ('batch','serial')),
 serials TEXT NOT NULL, instructions TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('ready','in_progress','awaiting_inspection','accepted','held')),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS operations (
 id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id),
 sequence INTEGER NOT NULL, name TEXT NOT NULL, instructions TEXT NOT NULL,
 completed_by INTEGER REFERENCES users(id), completed_at TEXT,
 UNIQUE(ticket_id, sequence)
);
CREATE TABLE IF NOT EXISTS inspections (
 id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL UNIQUE REFERENCES tickets(id),
 inspector_id INTEGER NOT NULL REFERENCES users(id), result TEXT NOT NULL CHECK(result IN ('pass','fail')),
 notes TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, ticket_id TEXT NOT NULL REFERENCES tickets(id),
 actor_id INTEGER NOT NULL REFERENCES users(id), action TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'Audit events are immutable'); END;
