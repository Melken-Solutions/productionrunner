import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from werkzeug.security import check_password_hash

from productionrunner import create_app
from test_workflow import app, sign_in, post, rows, create_ticket


def enroll(app, user='operator', tenant='alpha', pin='619284', disable=False):
    args = ['set-pin', user, '--tenant', tenant, '--reason', 'Authorized test enrollment']
    if disable:
        args.append('--disable')
    return app.test_cli_runner().invoke(args=args, input=pin + '\n' + pin + '\n')


def pin_login(client, user='operator', tenant='alpha', pin='619284'):
    client.get('/login')
    return post(client, '/login', username=user, tenant=tenant, method='pin', pin=pin)


def test_pin_workflow_and_independent_inspection(app):
    for user in ['planner', 'operator', 'inspector']:
        assert enroll(app, user).exit_code == 0
    client = app.test_client()
    assert pin_login(client, 'planner').status_code == 302
    path = create_ticket(client).location
    assert pin_login(client).status_code == 302
    for version, op in enumerate(rows(app, 'SELECT id FROM operations ORDER BY sequence'), 1):
        assert post(client, path + '/complete', operation_id=str(op['id']), version=str(version), confirm='yes').status_code == 302
    assert post(client, path + '/inspect', version='3', result='pass', notes='Self').status_code == 403
    assert pin_login(client, 'inspector').status_code == 302
    assert post(client, path + '/inspect', version='3', result='pass', notes='Independent').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'accepted'


def test_reset_disable_and_tenant_scope(app):
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'operator', '--tenant', 'beta', '--roles', 'viewer']).exit_code == 0
    assert enroll(app).exit_code == 0
    assert enroll(app, tenant='beta', pin='287493').exit_code == 0
    alpha, beta = app.test_client(), app.test_client()
    assert pin_login(alpha, pin='287493').status_code == 200
    assert pin_login(alpha).status_code == 302
    assert pin_login(beta, tenant='beta', pin='287493').status_code == 302
    assert create_ticket(beta).status_code == 403
    assert enroll(app, pin='593826').exit_code == 0
    assert alpha.get('/').location == '/login'
    assert beta.get('/').status_code == 200
    assert pin_login(alpha).status_code == 200
    assert pin_login(alpha, pin='593826').status_code == 302
    assert enroll(app, disable=True).exit_code == 0
    assert alpha.get('/').location == '/login'
    assert pin_login(alpha, pin='593826').status_code == 200
    assert beta.get('/').status_code == 200
    actions = [r[0] for r in rows(app, 'SELECT action FROM auth_events')]
    assert 'pin_enrolled' in actions and 'pin_reset' in actions and 'pin_disabled' in actions


def test_rate_limit_shared_with_password_persists_and_expires(app):
    clock = [1000.0]
    app.config['AUTH_CLOCK'] = lambda: clock[0]
    assert enroll(app).exit_code == 0
    client = app.test_client()
    for _ in range(5):
        assert pin_login(client, pin='000000').status_code == 200
    assert pin_login(client).status_code == 429
    assert post(client, '/login', username='operator', tenant='alpha', password='test-password-123').status_code == 429
    restarted = create_app({'TESTING': True, 'SECRET_KEY': 'test-only', 'DATABASE': app.config['DATABASE'], 'AUTH_CLOCK': lambda: clock[0]})
    assert pin_login(restarted.test_client()).status_code == 429
    clock[0] += 901
    assert pin_login(client).status_code == 302
    assert rows(app, 'SELECT COUNT(*) FROM login_limits')[0][0] == 0


def test_concurrent_failures_do_not_bypass_limit(app):
    assert enroll(app).exit_code == 0
    clients = [app.test_client() for _ in range(8)]
    for client in clients:
        client.get('/login')
    def fail(client):
        return post(client, '/login', username='operator', tenant='alpha', method='pin', pin='000000').status_code
    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(fail, clients))
    assert codes.count(200) == 5
    assert codes.count(429) == 3
    assert len(rows(app, "SELECT * FROM auth_events WHERE action='login_failed'")) == 5


def test_source_limit_covers_unknown_accounts_and_ignores_forwarded_header(app):
    client = app.test_client()
    client.get('/login')
    for i in range(50):
        with client.session_transaction() as session:
            csrf = session['csrf']
        response = client.post('/login', data=dict(csrf=csrf, username=f'unknown-{i}', tenant='alpha', method='pin', pin='123456'),
                               headers={'X-Forwarded-For': f'10.0.0.{i}'})
        assert response.status_code == 200
    assert pin_login(client, user='different').status_code == 429


def test_revoked_membership_cannot_use_pin(app):
    assert enroll(app).exit_code == 0
    client = app.test_client()
    assert pin_login(client).status_code == 302
    assert app.test_cli_runner().invoke(args=['set-membership', 'operator', '--tenant', 'alpha', '--revoke']).exit_code == 0
    assert client.get('/').location == '/login'
    assert pin_login(client).status_code == 200


@pytest.mark.parametrize('pin', ['12345', '1234567890123', '１２３４５６', 'abcdef'])
def test_invalid_pins_are_rejected_without_audit_or_credentials(app, pin):
    assert enroll(app, pin=pin).exit_code != 0
    assert not rows(app, 'SELECT * FROM pin_credentials')
    assert not rows(app, 'SELECT * FROM auth_events')


def test_pin_is_hashed_and_audit_is_immutable_and_scoped(app):
    result = enroll(app)
    assert result.exit_code == 0 and '619284' not in result.output
    stored = rows(app, 'SELECT pin_hash FROM pin_credentials')[0][0]
    assert stored != '619284' and check_password_hash(stored, '619284')
    assert '619284' not in str([dict(r) for r in rows(app, 'SELECT * FROM auth_events')])
    client = app.test_client()
    sign_in(client, 'viewer')
    assert client.get('/security-history').status_code == 403
    sign_in(client, 'admin')
    page = client.get('/security-history')
    assert page.status_code == 200 and b'pin_enrolled' in page.data and b'local-os:' in page.data
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'admin', '--tenant', 'beta', '--roles', 'admin']).exit_code == 0
    sign_in(client, 'admin', 'beta')
    assert b'pin_enrolled' not in client.get('/security-history').data
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for statement in ['DELETE FROM auth_events', "UPDATE auth_events SET detail='changed'"]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(statement)


def test_pin_login_csrf_and_session_rotation(app):
    assert enroll(app).exit_code == 0
    client = app.test_client()
    client.get('/login')
    with client.session_transaction() as session:
        old = session['csrf']
    assert client.post('/login', data={'method': 'pin', 'pin': '619284'}).status_code == 400
    assert pin_login(client).status_code == 302
    with client.session_transaction() as session:
        assert session['csrf'] != old
        assert 'pin_hash' not in session and 'password_hash' not in session


def test_additive_upgrade_preserves_existing_tenant_workflow(tmp_path):
    database = tmp_path / 'existing.db'
    schema = Path(__file__).parents[1].joinpath('productionrunner/schema.sql').read_text().split('CREATE TABLE IF NOT EXISTS pin_credentials')[0]
    with sqlite3.connect(database) as conn:
        conn.executescript(schema)
        conn.execute("INSERT INTO tenants VALUES(1,'old','Existing Organization')")
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(database)})
    for _ in range(2):
        assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert rows(app, 'SELECT name FROM tenants')[0][0] == 'Existing Organization'
    assert rows(app, 'SELECT COUNT(*) FROM pin_credentials')[0][0] == 0


def test_reset_is_atomic_with_audit(app):
    assert enroll(app).exit_code == 0
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_reset BEFORE INSERT ON auth_events WHEN NEW.action='pin_reset' BEGIN SELECT RAISE(ABORT, 'Test audit failure'); END")
    assert enroll(app, pin='593826').exit_code != 0
    stored = rows(app, 'SELECT pin_hash,version FROM pin_credentials')[0]
    assert stored['version'] == 1 and check_password_hash(stored['pin_hash'], '619284')
    assert len(rows(app, 'SELECT * FROM auth_events')) == 1


def test_revocation_clears_session_before_membership_regrant(app):
    assert enroll(app).exit_code == 0
    client = app.test_client()
    assert pin_login(client).status_code == 302
    runner = app.test_cli_runner()
    assert runner.invoke(args=['set-membership', 'operator', '--tenant', 'alpha', '--revoke']).exit_code == 0
    assert client.get('/').location == '/login'
    assert runner.invoke(args=['set-membership', 'operator', '--tenant', 'alpha', '--roles', 'operator']).exit_code == 0
    assert client.get('/').location == '/login'
