import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
import productionrunner
from productionrunner.jobrunner_ids import next_ticket_id
from test_workflow import app, sign_in, create_ticket, rows
from test_return_repairs import returned_job, repair, repair_id


STAMP = '2026-10-27T23:59:59+00:00'


def test_daily_sequence_and_expansion(app):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('BEGIN IMMEDIATE')
        assert next_ticket_id(conn, STAMP) == 'JR-20261027001'
        assert next_ticket_id(conn, STAMP) == 'JR-20261027002'
        assert next_ticket_id(conn, '2026-10-28T00:00:00+00:00') == 'JR-20261028001'
        # Equivalent instants with non-UTC offsets still use the UTC date.
        assert next_ticket_id(conn, '2026-10-27T20:00:00-04:00') == 'JR-20261028002'
        conn.execute("UPDATE jobrunner_sequences SET sequence=999 WHERE day='20261027'")
        assert next_ticket_id(conn, STAMP) == 'JR-202610271000'


def test_allocation_requires_transaction_and_rolls_back(app):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(RuntimeError):
            next_ticket_id(conn, STAMP)
        conn.execute('BEGIN IMMEDIATE')
        code = next_ticket_id(conn, STAMP)
        conn.rollback()
        conn.execute('BEGIN IMMEDIATE')
        assert next_ticket_id(conn, STAMP) == code


def test_concurrent_ticket_creation_and_scanning(app, monkeypatch):
    monkeypatch.setattr(productionrunner, 'now', lambda: STAMP)
    clients = [app.test_client() for _ in range(6)]
    for client in clients:
        sign_in(client, 'planner')
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda c: create_ticket(c, legacy=False), clients))
    assert all(r.status_code == 302 for r in responses)
    codes = {r.location.split('/')[-1] for r in responses}
    assert codes == {f'JR-20261027{i:03d}' for i in range(1, 7)}
    code = sorted(codes)[0]
    assert clients[0].get('/scan', query_string={'code': code}).location.endswith('/tickets/' + code)
    assert code.encode() in clients[0].get('/tickets/' + code + '/barcode.svg').data


def test_existing_ids_reserved_and_failed_ticket_does_not_consume_number(app, monkeypatch):
    monkeypatch.setattr(productionrunner, 'now', lambda: STAMP)
    c = app.test_client(); sign_in(c, 'planner')
    assert create_ticket(c, route=' | invalid').status_code == 400
    assert rows(app, 'SELECT * FROM jobrunner_sequences') == []
    first = create_ticket(c).location
    assert first.endswith('JR-20261027001')
    # Simulate an existing ID that predates the allocator; never rename it.
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('DELETE FROM jobrunner_sequences')
    assert create_ticket(c).location.endswith('JR-20261027002')
    assert c.get(first).status_code == 200
    assert len(rows(app, 'SELECT * FROM tickets')) == 2


def test_repair_ticket_uses_same_sequence(app, monkeypatch):
    monkeypatch.setattr(productionrunner, 'now', lambda: STAMP)
    # Registration captures the clock callback, so rebuild after freezing it.
    app = productionrunner.create_app(dict(app.config))
    c, tid, rid = returned_job(app)
    assert tid == 'JR-20261027001'
    assert repair(app, c, tid, rid).status_code == 302
    assert repair_id(app, rid) == 'JR-20261027002'
