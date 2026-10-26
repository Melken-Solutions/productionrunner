import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from werkzeug.security import check_password_hash

from test_workflow import app, post, rows, sign_in


def admin_client(app):
    client = app.test_client()
    sign_in(client, 'admin')
    return client


def change(client, user=4, version=0, **data):
    return post(client, f'/admin/users/{user}/access', version=str(version), reason='Staffing change', **data)


def test_create_and_manage_user(app):
    client = admin_client(app)
    assert b'User management' in client.get('/admin/users').data
    result = post(client, '/admin/users', username='new-worker', password='new-test-password',
                  password_confirm='new-test-password', roles=['operator'], reason='New hire')
    assert result.status_code == 302
    user = rows(app, "SELECT * FROM users WHERE username='new-worker'")[0]
    assert check_password_hash(user['password_hash'], 'new-test-password')
    assert b'PIN sign-in' in client.get(result.location).data
    assert change(client, user=user['id'], action='roles', roles=['operator', 'inspector']).status_code == 302
    audit = rows(app, "SELECT * FROM auth_events WHERE user_id=?", (user['id'],))
    assert [e['action'] for e in audit] == ['user_created', 'roles_changed']
    assert all(e['actor'] == 'user:5' and 'new-test-password' not in e['detail'] for e in audit)


@pytest.mark.parametrize('role', ['viewer', 'planner', 'operator', 'inspector'])
def test_admin_only(app, role):
    client = app.test_client()
    sign_in(client, role)
    assert client.get('/admin/users').status_code == 403
    assert change(client, action='revoke').status_code == 403
    assert post(client, '/admin/users/4/pin', action='disable', version='0', reason='Test').status_code == 403
    assert post(client, '/admin/users').status_code == 403


def test_last_admin_and_stale_changes(app):
    client = admin_client(app)
    assert change(client, user=5, action='revoke').status_code == 409
    assert change(client, user=5, action='roles', roles=['viewer']).status_code == 409
    assert change(client, action='roles', roles=['operator']).status_code == 302
    assert change(client, action='revoke').status_code == 409
    assert client.post('/admin/users/4/access', data={'action': 'revoke'}).status_code == 400
    assert change(client, user=999, action='revoke').status_code == 404


def test_reactivation_never_resurrects_old_session(app):
    client = admin_client(app)
    old = app.test_client()
    sign_in(old, 'viewer')
    assert change(client, action='revoke').status_code == 302
    assert change(client, version=1, action='roles', roles=['viewer']).status_code == 302
    assert old.get('/').location == '/login'
    sign_in(old, 'viewer')
    assert old.get('/').status_code == 200


def test_pin_management_invalidates_pin_only(app):
    client = admin_client(app)
    def pin(action, version, **data):
        return post(client, '/admin/users/4/pin', action=action, version=str(version), reason='Credential change', **data)
    assert pin('reset', 0, pin='123', pin_confirm='123').status_code == 400
    assert pin('reset', 0, pin='123456', pin_confirm='123456').status_code == 302
    old = app.test_client()
    old.get('/login')
    assert post(old, '/login', tenant='alpha', username='viewer', method='pin', pin='123456').status_code == 302
    password_client = app.test_client()
    sign_in(password_client, 'viewer')
    assert pin('reset', 1, pin='654321', pin_confirm='654321').status_code == 302
    assert old.get('/').location == '/login'
    assert password_client.get('/').status_code == 200
    assert pin('disable', 2).status_code == 302
    assert rows(app, 'SELECT pin_hash FROM pin_credentials')[0][0] is None
    assert all('654321' not in r['detail'] for r in rows(app, 'SELECT detail FROM auth_events'))


def test_cross_tenant_and_global_identity_are_protected(app):
    client = admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO users VALUES(99,'external','original-hash')")
        conn.execute("INSERT INTO memberships VALUES(2,99,'admin')")
    assert client.get('/admin/users/99').status_code == 404
    assert change(client, user=99, action='revoke').status_code == 404
    assert post(client, '/admin/users', username='external', password='new-test-password', password_confirm='new-test-password', roles='admin', reason='Test').status_code == 409
    assert rows(app, 'SELECT password_hash FROM users WHERE id=99')[0][0] == 'original-hash'
    assert not rows(app, 'SELECT * FROM memberships WHERE tenant_id=1 AND user_id=99')


def test_concurrent_admin_removal_keeps_one(app):
    first = admin_client(app)
    assert change(first, action='roles', roles=['admin']).status_code == 302
    second = app.test_client()
    sign_in(second, 'viewer')
    def submit(args):
        client, user, version = args
        return change(client, user=user, version=version, action='revoke').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [(first, 5, 0), (second, 4, 1)])) == [302, 409]
    assert len(rows(app, "SELECT * FROM memberships WHERE roles='admin'")) == 1


def test_admin_mutation_rolls_back_on_audit_failure(app):
    client = admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_admin_audit BEFORE INSERT ON auth_events WHEN NEW.action='access_revoked' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        change(client, action='revoke')
    assert rows(app, 'SELECT roles FROM memberships WHERE user_id=4')[0][0] == 'viewer'
    assert not rows(app, 'SELECT * FROM membership_controls')


def test_cli_cannot_remove_last_admin_and_invalidates_sessions(app):
    client = app.test_client()
    sign_in(client, 'viewer')
    runner = app.test_cli_runner()
    assert runner.invoke(args=['set-membership', 'admin', '--tenant', 'alpha', '--revoke']).exit_code != 0
    assert runner.invoke(args=['set-membership', 'viewer', '--tenant', 'alpha', '--revoke']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'viewer', '--tenant', 'alpha', '--roles', 'viewer']).exit_code == 0
    assert client.get('/').location == '/login'
