import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from werkzeug.security import generate_password_hash

from productionrunner import create_app


@pytest.fixture
def app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': str(tmp_path / 'test.sqlite3')})
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(1,'alpha','Alpha')")
        for name, roles in [('planner', 'planner'), ('operator', 'operator,inspector'), ('inspector', 'inspector'), ('viewer', 'viewer'), ('admin', 'admin')]:
            user = conn.execute('INSERT INTO users(username,password_hash) VALUES(?,?)',
                                (name, generate_password_hash('test-password-123')))
            conn.execute('INSERT INTO memberships VALUES(1,?,?)', (user.lastrowid, roles))
    return app


def sign_in(client, name, tenant="alpha"):
    client.get('/login')
    assert post(client, '/login', username=name, tenant=tenant, password='test-password-123').status_code == 302


def post(client, path, **data):
    with client.session_transaction() as session:
        data['csrf'] = session['csrf']
    return client.post(path, data=data)


def create_ticket(client, **overrides):
    data = dict(pn='PN-001', revision='A', quantity='2', tracking='batch', instructions='Check drawing tolerance ±0.1 mm', route='Cut | Cut to size\nAssemble | Fit the pieces')
    data.update(overrides)
    return post(client, '/tickets/new', **data)


def rows(app, query, args=()):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, args).fetchall()


def prepared(app):
    client = app.test_client()
    sign_in(client, 'planner')
    response = create_ticket(client)
    assert response.status_code == 302
    return client, response.location


def complete_route(app, client, path):
    sign_in(client, 'operator')
    operations = rows(app, 'SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence', (path.split('/')[-1],))
    for version, operation in enumerate(operations, 1):
        assert post(client, path + '/complete', version=str(version), operation_id=str(operation['id']), confirm='yes').status_code == 302


def test_full_workflow_persists_and_prints(app):
    client, path = prepared(app)
    assert post(client, path + '/issue-traveler').status_code == 302
    page = client.get(path + '/traveler')
    assert page.status_code == 200 and b'KEEP THIS TRAVELER WITH THE MATERIAL' in page.data
    barcode = client.get(path + '/barcode.svg')
    assert barcode.mimetype == 'image/svg+xml' and b'<svg' in barcode.data and path.split('/')[-1].encode() in barcode.data
    assert client.get('/scan', query_string={'code': '  ' + path.split('/')[-1] + '\r\n'}).location == path
    complete_route(app, client, path)
    assert b'Another inspector must inspect' in client.get(path).data
    denied = post(client, path + '/inspect', version='3', result='pass', notes='Self inspection')
    assert denied.status_code == 403
    sign_in(client, 'inspector')
    assert post(client, path + '/inspect', version='3', result='pass', notes='Measured 10.02 mm; within tolerance').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0]['state'] == 'accepted'
    assert len(rows(app, 'SELECT * FROM events')) == 5
    reopened = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': app.config['DATABASE']})
    new_client = reopened.test_client()
    sign_in(new_client, 'viewer')
    assert b'Measured 10.02 mm' in new_client.get(path).data


def test_failed_inspection_holds_batch_and_cannot_be_overwritten(app):
    client, path = prepared(app)
    complete_route(app, client, path)
    sign_in(client, 'inspector')
    assert post(client, path + '/inspect', version='3', result='fail', notes='Dimension outside tolerance').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0]['state'] == 'held'
    assert post(client, path + '/inspect', version='4', result='pass', notes='Override').status_code == 409
    assert len(rows(app, 'SELECT * FROM inspections')) == 1


@pytest.mark.parametrize('role', ['viewer', 'admin', 'inspector', 'operator'])
def test_only_planner_creates_tickets(app, role):
    client = app.test_client()
    sign_in(client, role)
    assert create_ticket(client).status_code == 403
    assert not rows(app, 'SELECT * FROM tickets')


def test_route_order_duplicate_submission_and_inspection_gate(app):
    client, path = prepared(app)
    sign_in(client, 'inspector')
    assert post(client, path + '/inspect', version='1', result='pass', notes='Too early').status_code == 409
    sign_in(client, 'operator')
    ids = [o['id'] for o in rows(app, 'SELECT * FROM operations ORDER BY sequence')]
    assert post(client, path + '/complete', version='1', operation_id=str(ids[1]), confirm='yes').status_code == 409
    assert post(client, path + '/complete', version='1', operation_id=str(ids[0]), confirm='yes').status_code == 302
    assert post(client, path + '/complete', version='1', operation_id=str(ids[0]), confirm='yes').status_code == 409
    assert len(rows(app, 'SELECT * FROM events')) == 2


@pytest.mark.parametrize('changes', [dict(pn='PN-A,PN-B'), dict(pn=''), dict(quantity='0'), dict(quantity='1.5'), dict(quantity='10001'), dict(revision=''), dict(route=''), dict(route=' | no name'), dict(tracking='unknown'), dict(serials='S1'), dict(tracking='serial', serials='S1\nS1'), dict(tracking='serial', serials='S1')])
def test_invalid_ticket_is_atomic(app, changes):
    client = app.test_client()
    sign_in(client, 'planner')
    assert create_ticket(client, **changes).status_code == 400
    assert not rows(app, 'SELECT * FROM tickets')
    assert not rows(app, 'SELECT * FROM operations')
    assert not rows(app, 'SELECT * FROM events')


def test_serial_inventory_and_escaped_instructions(app):
    client = app.test_client()
    sign_in(client, 'planner')
    response = create_ticket(client, tracking='serial', serials='S1\nS2', instructions='<script>alert(1)</script>')
    assert response.status_code == 302
    page = client.get(response.location)
    assert b'S1, S2' in page.data and b'&lt;script&gt;' in page.data and b'<script>' not in page.data


def test_auth_csrf_and_permissions(app):
    client = app.test_client()
    assert client.get('/').location == '/login'
    client.get('/login')
    assert client.post('/login', data={'username': 'planner', 'password': 'test-password-123'}).status_code == 400
    assert b'incorrect' in post(client, '/login', username='planner', password='wrong').data
    sign_in(client, 'planner')
    assert client.post('/tickets/new').status_code == 400
    response = create_ticket(client)
    path = response.location
    assert post(client, path + '/complete', version='1', operation_id='1', confirm='yes').status_code == 403
    assert post(client, path + '/inspect', version='1', result='pass', notes='No').status_code == 403
    sign_in(client, 'viewer')
    assert post(client, path + '/issue-traveler').status_code == 403
    assert client.get('/scan?code=UNKNOWN').status_code == 404
    assert 'frame-ancestors' in client.get('/').headers['Content-Security-Policy']


def test_audit_events_immutable_and_init_preserves_data(app):
    prepared(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET detail='changed'")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM events')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert len(rows(app, 'SELECT * FROM tickets')) == 1


def test_concurrent_completion_records_once(app):
    _, path = prepared(app)
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        sign_in(client, 'operator')
    operation_id = str(rows(app, 'SELECT id FROM operations ORDER BY sequence')[0]['id'])
    def submit(client):
        return post(client, path + '/complete', version='1', operation_id=operation_id, confirm='yes').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, clients)) == [302, 409]
    assert len(rows(app, 'SELECT * FROM events')) == 2


def test_user_provisioning_rejects_invalid_roles_short_password_and_duplicate(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-user', 'new', '--tenant', 'alpha', '--roles', 'superuser', '--password', 'long-test-password']).exit_code != 0
    assert runner.invoke(args=['add-user', 'new', '--tenant', 'alpha', '--roles', 'operator', '--password', 'short']).exit_code != 0
    assert runner.invoke(args=['add-user', 'planner', '--tenant', 'alpha', '--roles', 'operator', '--password', 'long-test-password']).exit_code != 0
