import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from productionrunner import create_app
from test_workflow import app, sign_in, post, create_ticket, rows


def prepared_units(app, serials='S1\nS2'):
    client = app.test_client()
    sign_in(client, 'planner')
    response = create_ticket(client, tracking='serial', serials=serials)
    assert response.status_code == 302
    ticket_id = response.location.split('/')[-1]
    units = rows(app, 'SELECT * FROM serial_units WHERE ticket_id=? ORDER BY serial', (ticket_id,))
    operations = rows(app, 'SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence', (ticket_id,))
    return client, ticket_id, units, operations


def complete(client, unit, operation, version):
    return post(client, '/units/' + unit['id'] + '/complete', operation_id=str(operation['id']), version=str(version), confirm='yes')


def inspect(client, unit, result='pass', version=3):
    return post(client, '/units/' + unit['id'] + '/inspect', result=result, notes='Measured within tolerance' if result == 'pass' else 'Out of tolerance', version=str(version))


def test_units_travel_and_progress_independently(app):
    client, ticket, units, ops = prepared_units(app)
    for unit in units:
        path = '/units/' + unit['id']
        assert client.get('/scan', query_string={'code': ' ' + unit['id'] + '\r\n'}).location == path
        assert post(client, path + '/issue-traveler').location == path + '/traveler'
        page = client.get(path + '/traveler')
        assert b'Quantity: 1' in page.data and unit['serial'].encode() in page.data
        assert client.get(path + '/barcode.svg').mimetype == 'image/svg+xml'
    assert b'Ticket cover sheet' in client.get('/tickets/' + ticket + '/traveler').data
    sign_in(client, 'operator')
    assert complete(client, units[0], ops[0], 1).status_code == 302
    assert complete(client, units[0], ops[1], 2).status_code == 302
    assert [r['state'] for r in rows(app, 'SELECT state FROM serial_units ORDER BY serial')] == ['awaiting_inspection', 'ready']
    assert b'2 / 2' in client.get('/tickets/' + ticket).data
    assert b'0 / 2' in client.get('/tickets/' + ticket).data
    assert units[0]['id'].encode() not in client.get('/units/' + units[1]['id']).data
    assert inspect(client, units[0]).status_code == 403
    sign_in(client, 'inspector')
    assert inspect(client, units[0]).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'in_progress'
    sign_in(client, 'operator')
    for version, op in enumerate(ops, 1):
        assert complete(client, units[1], op, version).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'awaiting_inspection'
    sign_in(client, 'inspector')
    assert inspect(client, units[1]).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'accepted'
    assert len(rows(app, 'SELECT * FROM unit_inspections')) == 2
    assert len(rows(app, 'SELECT * FROM unit_event_links')) == 8
    assert not rows(app, 'SELECT * FROM inspections')
    assert not rows(app, 'SELECT * FROM operations WHERE completed_by IS NOT NULL')
    assert not rows(app, 'PRAGMA foreign_key_check')
    restarted = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': app.config['DATABASE']})
    fresh = restarted.test_client()
    sign_in(fresh, 'viewer')
    assert b'This unit is accepted' in fresh.get('/units/' + units[0]['id']).data


def test_failed_unit_does_not_block_siblings(app):
    client, ticket, units, ops = prepared_units(app)
    sign_in(client, 'operator')
    for version, op in enumerate(ops, 1):
        assert complete(client, units[0], op, version).status_code == 302
    sign_in(client, 'inspector')
    assert inspect(client, units[0], result='fail').status_code == 302
    assert inspect(client, units[0], version=4).status_code == 409
    sign_in(client, 'operator')
    assert complete(client, units[0], ops[0], 4).status_code == 409
    for version, op in enumerate(ops, 1):
        assert complete(client, units[1], op, version).status_code == 302
    sign_in(client, 'inspector')
    assert inspect(client, units[1]).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'held'
    assert [r[0] for r in rows(app, 'SELECT state FROM serial_units ORDER BY serial')] == ['held', 'accepted']


def test_route_stale_forms_batch_bypass_and_role_checks(app):
    client, ticket, units, ops = prepared_units(app)
    sign_in(client, 'viewer')
    assert complete(client, units[0], ops[0], 1).status_code == 403
    assert inspect(client, units[0], version=1).status_code == 403
    assert post(client, '/units/' + units[0]['id'] + '/issue-traveler').status_code == 403
    sign_in(client, 'operator')
    assert complete(client, units[0], ops[1], 1).status_code == 409
    assert inspect(client, units[0], version=1).status_code == 409
    assert post(client, '/tickets/' + ticket + '/complete', version='1', operation_id=str(ops[0]['id']), confirm='yes').status_code == 409
    assert post(client, '/tickets/' + ticket + '/inspect', version='1', result='pass', notes='Bypass').status_code == 409
    assert complete(client, units[0], ops[0], 1).status_code == 302
    assert complete(client, units[0], ops[0], 1).status_code == 409
    assert len(rows(app, 'SELECT * FROM unit_completions')) == 1


@pytest.mark.parametrize('same_unit', [False, True])
def test_concurrent_unit_completion(app, same_unit):
    _, ticket, units, ops = prepared_units(app)
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        sign_in(client, 'operator')
    def submit(i):
        return complete(clients[i], units[0 if same_unit else i], ops[0], 1).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(submit, [0, 1]))
    assert codes == ([302, 409] if same_unit else [302, 302])
    assert len(rows(app, 'SELECT * FROM unit_completions')) == (1 if same_unit else 2)


def test_independence_is_per_unit_and_membership_still_applies(app):
    client, _, units, ops = prepared_units(app)
    runner = app.test_cli_runner()
    assert runner.invoke(args=['set-membership', 'planner', '--tenant', 'alpha', '--roles', 'planner,operator']).exit_code == 0
    sign_in(client, 'planner')  # Role changes invalidate existing sessions.
    for version, op in enumerate(ops, 1):
        assert complete(client, units[0], op, version).status_code == 302
    sign_in(client, 'operator')
    for version, op in enumerate(ops, 1):
        assert complete(client, units[1], op, version).status_code == 302
    assert inspect(client, units[0]).status_code == 302
    assert inspect(client, units[1]).status_code == 403


def test_foreign_tenant_cannot_read_scan_print_or_mutate_unit(app):
    client, _, units, ops = prepared_units(app)
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'operator', '--tenant', 'beta', '--roles', 'planner,operator,inspector']).exit_code == 0
    sign_in(client, 'operator', 'beta')
    path = '/units/' + units[0]['id']
    for suffix in ['', '/traveler', '/barcode.svg']:
        assert client.get(path + suffix).status_code == 404
    assert client.get('/scan', query_string={'code': units[0]['id']}).status_code == 404
    assert post(client, path + '/issue-traveler').status_code == 404
    assert complete(client, units[0], ops[0], 1).status_code == 404
    assert inspect(client, units[0], version=1).status_code == 404
    assert not rows(app, 'SELECT * FROM unit_event_links')


def test_unit_event_atomicity_immutability_and_owned_operation(app):
    client, _, units, ops = prepared_units(app)
    other = create_ticket(client).location.split('/')[-1]
    foreign_op = rows(app, 'SELECT id FROM operations WHERE ticket_id=?', (other,))[0][0]
    sign_in(client, 'operator')
    assert complete(client, units[0], {'id': foreign_op}, 1).status_code == 409
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO unit_completions VALUES(?,?,?,?,?,?)', (1, units[0]['ticket_id'], units[0]['id'], foreign_op, 2, 'now'))
        conn.execute("CREATE TRIGGER reject_link BEFORE INSERT ON unit_event_links BEGIN SELECT RAISE(ABORT,'Test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        complete(client, units[0], ops[0], 1)
    assert not rows(app, 'SELECT * FROM unit_completions')
    assert rows(app, 'SELECT version FROM serial_units WHERE id=?', (units[0]['id'],))[0][0] == 1
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('DROP TRIGGER reject_link')
    assert complete(client, units[0], ops[0], 1).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ['DELETE FROM unit_event_links', "UPDATE unit_event_links SET unit_id='changed'"]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_serial_text_escaped_and_unique_per_ticket(app):
    client, ticket, units, _ = prepared_units(app, '<script>x</script>\nS2')
    page = client.get('/units/' + units[0]['id'])
    assert b'&lt;script&gt;' in page.data and b'<script>' not in page.data
    assert len(units) == 2
    duplicate = create_ticket(client, tracking='serial', serials='S1\nS1')
    assert duplicate.status_code == 400
    # Current policy retains per-ticket serial uniqueness, not a global inventory registry.
    assert create_ticket(client, tracking='serial', serials='<script>x</script>\nS2').status_code == 302


def test_upgrade_keeps_legacy_serial_inventory_as_batch(tmp_path):
    database = tmp_path / 'old.db'
    schema = Path(__file__).parents[1].joinpath('productionrunner/schema.sql').read_text().split('CREATE UNIQUE INDEX IF NOT EXISTS operations_owner_id')[0]
    with sqlite3.connect(database) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO users VALUES(1,'planner','unused')")
        conn.execute("INSERT INTO tenants VALUES(1,'alpha','Alpha')")
        conn.execute("INSERT INTO memberships VALUES(1,1,'operator,inspector')")
        conn.execute("INSERT INTO tickets VALUES('JR-OLD',1,'PN','A',2,'serial','[\"S1\",\"S2\"]','Work','ready',1,'then',1)")
        conn.execute("INSERT INTO operations VALUES(1,1,'JR-OLD',1,'Cut','Cut',NULL,NULL)")
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(database)})
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert not rows(app, 'SELECT * FROM serial_units')
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(user_id=1, tenant_id=1, csrf='test')
    assert b'Legacy serial inventory' in client.get('/tickets/JR-OLD').data
    assert post(client, '/tickets/JR-OLD/complete', version='1', operation_id='1', confirm='yes').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'awaiting_inspection'
