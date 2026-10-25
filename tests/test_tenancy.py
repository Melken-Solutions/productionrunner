"""Isolation, membership revocation and legacy migration acceptance tests."""
import sqlite3
from pathlib import Path

import pytest

from productionrunner import create_app
from test_workflow import app, sign_in, post, create_ticket, rows, prepared


@pytest.fixture
def two_tenants(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    for user in ['planner', 'operator', 'inspector']:
        assert runner.invoke(args=['add-user', 'beta-' + user, '--tenant', 'beta', '--roles', user,
                                   '--password', 'test-password-123']).exit_code == 0
    return app


@pytest.mark.parametrize('tenant,prefix', [('alpha', ''), ('beta', 'beta-')])
def test_each_tenant_completes_batch(two_tenants, tenant, prefix):
    app = two_tenants
    client = app.test_client()
    sign_in(client, prefix + 'planner', tenant)
    response = create_ticket(client, pn='SAME-PN', tenant_id='999')
    assert response.status_code == 302
    path = response.location
    assert post(client, path + '/issue-traveler').status_code == 302
    assert tenant.title().encode() in client.get(path + '/traveler').data
    assert client.get(path + '/barcode.svg').status_code == 200
    assert client.get('/scan', query_string={'code': path.split('/')[-1]}).location == path
    sign_in(client, prefix + 'operator', tenant)
    for version, op in enumerate(rows(app, 'SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence', (path.split('/')[-1],)), 1):
        assert post(client, path + '/complete', version=str(version), operation_id=str(op['id']), confirm='yes').status_code == 302
    sign_in(client, prefix + 'inspector', tenant)
    assert post(client, path + '/inspect', version='3', result='pass', notes='Within tolerance').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0]['state'] == 'accepted'
    assert len(rows(app, 'SELECT * FROM events')) == 5
    assert not rows(app, 'PRAGMA foreign_key_check')


@pytest.mark.parametrize('owner,outsider,prefix', [('alpha', 'beta', 'beta-'), ('beta', 'alpha', '')])
def test_copied_ids_cannot_read_or_mutate_other_tenant(two_tenants, owner, outsider, prefix):
    app = two_tenants
    client = app.test_client()
    sign_in(client, ('beta-' if owner == 'beta' else '') + 'planner', owner)
    path = create_ticket(client, pn='PRIVATE-PN').location
    ticket_id = path.split('/')[-1]
    op_id = rows(app, 'SELECT id FROM operations ORDER BY sequence')[0]['id']
    for role in ['planner', 'operator', 'inspector']:
        sign_in(client, prefix + role, outsider)
        assert b'PRIVATE-PN' not in client.get('/').data
        for suffix in ['', '/traveler', '/barcode.svg']:
            assert client.get(path + suffix).status_code == 404
        assert client.get('/scan', query_string={'code': ticket_id}).status_code == 404
    for role, suffix, data in [
        ('planner', '/issue-traveler', {}),
        ('operator', '/complete', dict(version='1', operation_id=str(op_id), confirm='yes')),
        ('inspector', '/inspect', dict(version='1', result='pass', notes='Forged')),
    ]:
        sign_in(client, prefix + role, outsider)
        assert post(client, path + suffix, tenant_id=owner, **data).status_code == 404
    assert rows(app, 'SELECT version FROM tickets')[0]['version'] == 1
    assert len(rows(app, 'SELECT * FROM events')) == 1
    assert not rows(app, 'SELECT * FROM inspections')


def test_membership_roles_are_scoped_and_revocation_is_immediate(two_tenants):
    app = two_tenants
    runner = app.test_cli_runner()
    assert runner.invoke(args=['set-membership', 'planner', '--tenant', 'beta', '--roles', 'viewer']).exit_code == 0
    client = app.test_client()
    sign_in(client, 'planner', 'beta')
    assert create_ticket(client).status_code == 403
    sign_in(client, 'planner', 'alpha')
    path = create_ticket(client).location
    assert runner.invoke(args=['set-membership', 'planner', '--tenant', 'alpha', '--revoke']).exit_code == 0
    assert client.get(path).location == '/login'
    assert post(client, path + '/issue-traveler').location == '/login'
    assert len(rows(app, 'SELECT * FROM events')) == 1
    assert b'incorrect' in post(client, '/login', tenant='alpha', username='planner', password='test-password-123').data
    sign_in(client, 'planner', 'beta')
    assert client.get(path).status_code == 404


def test_login_requires_membership_and_old_sessions_fail_closed(two_tenants):
    client = two_tenants.test_client()
    client.get('/login')
    for tenant in ['beta', 'unknown', '']:
        assert b'incorrect' in post(client, '/login', tenant=tenant, username='planner', password='test-password-123').data
    with client.session_transaction() as session:
        session['user_id'] = 1  # A pre-migration cookie has no organization context.
    assert client.get('/').location == '/login'


def test_database_rejects_cross_tenant_links_and_actors(two_tenants):
    app = two_tenants
    _, path = prepared(app)
    ticket = path.split('/')[-1]
    outsider = rows(app, "SELECT id FROM users WHERE username='beta-operator'")[0]['id']
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        for statement, args in [
            ('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(2,?, ?,?,?,?)', (ticket, outsider, 'forged', '', 'now')),
            ('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(1,?, ?,?,?,?)', (ticket, outsider, 'forged', '', 'now')),
            ('UPDATE operations SET completed_by=? WHERE ticket_id=?', (outsider, ticket)),
            ('UPDATE tickets SET created_by=? WHERE id=?', (outsider, ticket)),
        ]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement, args)


def legacy_app(tmp_path, corrupt=False):
    database = tmp_path / 'legacy.db'
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(database)})
    with sqlite3.connect(database) as conn:
        conn.executescript(Path(__file__).with_name('fixtures').joinpath('legacy-schema.sql').read_text())
        conn.execute("INSERT INTO users VALUES(1,'legacy','hash','planner,operator,inspector')")
        conn.execute("INSERT INTO tickets VALUES('JR-OLD','PN','A',1,'batch','[]','Work','accepted',1,'then',3)")
        conn.execute("INSERT INTO operations VALUES(1,'JR-OLD',1,'Cut','Cut',1,'then')")
        conn.execute("INSERT INTO inspections VALUES(1,'JR-OLD',1,'pass','Evidence','then')")
        conn.execute("INSERT INTO events VALUES(1,'JR-OLD',1,'inspection_pass','Evidence','then')")
        conn.commit()
        if corrupt:
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute('UPDATE operations SET completed_by=999')
    return app


def test_migration_preserves_records_backup_and_audit_guards(tmp_path):
    app = legacy_app(tmp_path)
    runner = app.test_cli_runner()
    assert runner.invoke(args=['init-db']).exit_code != 0
    backup = tmp_path / 'backup.db'
    args = ['migrate-tenancy', '--tenant', 'legacy-shop', '--name', 'Legacy Shop', '--backup', str(backup)]
    result = runner.invoke(args=args)
    assert result.exit_code == 0, result.output
    with sqlite3.connect(backup) as old:
        for table in ['tickets', 'operations', 'inspections', 'events']:
            fields = [r[1] for r in old.execute(f'PRAGMA table_info({table})')]
            assert [tuple(r) for r in rows(app, f"SELECT {','.join(fields)} FROM {table}")] == old.execute(f'SELECT * FROM {table}').fetchall()
            assert rows(app, f'SELECT tenant_id FROM {table}')[0][0] == 1
    assert rows(app, 'SELECT roles FROM memberships')[0][0] == 'planner,operator,inspector'
    assert not rows(app, 'PRAGMA foreign_key_check')
    assert runner.invoke(args=args).exit_code != 0
    assert runner.invoke(args=['init-db']).exit_code == 0
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE events SET detail='tampered'")


def test_migration_rolls_back_invalid_ownership(tmp_path):
    app = legacy_app(tmp_path, corrupt=True)
    backup = tmp_path / 'backup.db'
    result = app.test_cli_runner().invoke(args=['migrate-tenancy', '--tenant', 'shop', '--name', 'Shop', '--backup', str(backup)])
    assert result.exit_code != 0
    assert 'tenant_id' not in {r[1] for r in rows(app, 'PRAGMA table_info(tickets)')}
    assert rows(app, 'SELECT completed_by FROM operations')[0][0] == 999
    assert backup.exists()


def test_migration_refuses_existing_backup(tmp_path):
    app = legacy_app(tmp_path)
    backup = tmp_path / 'backup.db'
    backup.write_bytes(b'keep me')
    result = app.test_cli_runner().invoke(args=['migrate-tenancy', '--tenant', 'shop', '--name', 'Shop', '--backup', str(backup)])
    assert result.exit_code != 0
    assert backup.read_bytes() == b'keep me'
    assert 'tenant_id' not in {r[1] for r in rows(app, 'PRAGMA table_info(tickets)')}
