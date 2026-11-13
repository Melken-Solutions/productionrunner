import sqlite3

import pytest
from werkzeug.security import check_password_hash
from test_workflow import app, post, rows, sign_in
from test_admin import admin_client


def remove(client, user=4, **values):
    return post(client, f'/admin/users/{user}/delete', **dict(dict(version='0', confirm_username='viewer', reason='Account no longer needed'), **values))


def test_delete_invalidates_and_retains_history(app):
    admin = admin_client(app)
    old = app.test_client()
    sign_in(old, 'viewer')
    before = rows(app, 'SELECT * FROM auth_events WHERE user_id=4')
    assert b'Review permanent deletion' in admin.get('/admin/users/4').data
    assert remove(admin).status_code == 302
    assert old.get('/').location == '/login'
    assert admin.get('/admin/users/4').status_code == 404
    assert b'>viewer</td>' not in admin.get('/admin/users').data
    assert rows(app, 'SELECT roles FROM memberships WHERE user_id=4')[0][0] == ''
    assert not check_password_hash(rows(app, 'SELECT password_hash FROM users WHERE id=4')[0][0], 'test-password-123')
    events = rows(app, 'SELECT * FROM auth_events WHERE user_id=4')
    assert [dict(r) for r in events[:len(before)]] == [dict(r) for r in before]
    assert events[-1]['action'] == 'user_deleted'
    assert events[-1]['actor'] == 'user:5'
    assert remove(admin).status_code == 404
    assert post(admin, '/admin/users/4/access', action='roles', roles='viewer', version='1', reason='Restore').status_code == 404
    result = app.test_cli_runner().invoke(args=['set-membership','viewer','--tenant','alpha','--roles','viewer'])
    assert result.exit_code != 0
    # Even an out-of-band role restoration cannot resurrect the deleted login.
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("UPDATE memberships SET roles='viewer' WHERE user_id=4")
    old.get('/login')
    assert post(old, '/login', username='viewer', tenant='alpha', password='test-password-123').status_code == 200


def test_delete_guards_and_scope(app):
    admin = admin_client(app)
    assert remove(admin, confirm_username='wrong').status_code == 400
    assert remove(admin, version='9').status_code == 409
    assert remove(admin, user=5, confirm_username='admin').status_code == 409
    assert remove(admin, user=999).status_code == 404
    assert admin.post('/admin/users/4/delete', data={}).status_code == 400
    for name in ['planner', 'operator', 'viewer', 'inspector']:
        client = app.test_client()
        sign_in(client, name)
        assert remove(client).status_code == 403
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO users VALUES(99,'external','original-hash')")
        conn.execute("INSERT INTO memberships VALUES(2,99,'admin')")
        conn.execute("INSERT INTO memberships VALUES(2,4,'viewer')")
    assert remove(admin, user=99, confirm_username='external').status_code == 404
    other = app.test_client()
    sign_in(other, 'viewer', tenant='beta')
    assert remove(admin).status_code == 302
    assert other.get('/').status_code == 200
    assert check_password_hash(rows(app, 'SELECT password_hash FROM users WHERE id=4')[0][0], 'test-password-123')
    sign_in(other, 'viewer', tenant='beta')


def test_delete_revoked_tv_disables_pin(app):
    admin = admin_client(app)
    assert post(admin, '/admin/users/4/pin', action='reset', pin='123456', pin_confirm='123456', version='0', reason='Test').status_code == 302
    assert post(admin, '/admin/users/4/access', action='roles', roles='display', version='1', reason='Test').status_code == 302
    assert post(admin, '/admin/users/4/access', action='revoke', version='2', reason='Test').status_code == 302
    assert remove(admin, version='3').status_code == 302
    assert rows(app, 'SELECT pin_hash FROM pin_credentials WHERE user_id=4')[0][0] is None


def test_deletion_atomic_on_audit_failure(app):
    admin = admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_delete_audit BEFORE INSERT ON auth_events WHEN NEW.action='user_deleted' BEGIN SELECT RAISE(ABORT,'test failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        remove(admin)
    assert rows(app, 'SELECT roles FROM memberships WHERE user_id=4')[0][0] == 'viewer'
    assert check_password_hash(rows(app, 'SELECT password_hash FROM users WHERE id=4')[0][0], 'test-password-123')
