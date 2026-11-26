"""Readable identifiers allocated atomically with their ticket transaction."""
from datetime import datetime, timezone


def next_ticket_id(conn, timestamp):
    """Caller must hold BEGIN IMMEDIATE; rollback also rolls back allocation.

    IDs are global across organizations because tickets use a global primary key.
    Date is UTC, sequence is at least three digits, and existing IDs are reserved.
    """
    if not conn.in_transaction:
        raise RuntimeError('Ticket IDs require a write transaction.')
    day = datetime.fromisoformat(timestamp).astimezone(timezone.utc).strftime('%Y%m%d')
    conn.execute('INSERT OR IGNORE INTO jobrunner_sequences(day,sequence) VALUES(?,0)', (day,))
    while True:
        conn.execute('UPDATE jobrunner_sequences SET sequence=sequence+1 WHERE day=?', (day,))
        sequence = conn.execute('SELECT sequence FROM jobrunner_sequences WHERE day=?', (day,)).fetchone()[0]
        code = f'JR-{day}{sequence:03d}'
        if not conn.execute('SELECT 1 FROM tickets WHERE id=?', (code,)).fetchone():
            return code
