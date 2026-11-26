import sqlite3
import pytest
from werkzeug.security import check_password_hash
from test_workflow import app, post, rows, sign_in
from test_developer_admin import developer

OLD = 'test-password-123'
NEW = 'replacement-password-456'


def change(client, **overrides):
    data = dict(action='password', current_password=OLD, new_credential=NEW, confirm_credential=NEW)
    data.update(overrides)
    return post(client, '/account/security', **data)


@pytest.mark.parametrize('role', ['admin', 'planner', 'operator', 'inspector', 'viewer', 'display'])
def test_all_organization_roles_can_change_password(app, role):
    if role == 'display':
        with sqlite3.connect(app.config['DATABASE']) as conn:
            conn.execute("UPDATE memberships SET roles='display' WHERE user_id=4")
        username = 'viewer'
    else:
        username = role
    client, other = app.test_client(), app.test_client()
    sign_in(client, username); sign_in(other, username)
    page = client.get('/account/security')
    assert page.status_code == 200 and b'Account security' in page.data
    assert change(client).status_code == 302
    assert other.get('/account/security').location == '/login'
    assert client.get('/account/security').location == '/login'
    assert post(client, '/login', tenant='alpha', username=username, password=OLD).status_code == 200
    assert post(client, '/login', tenant='alpha', username=username, password=NEW).status_code == 302
    events = rows(app, "SELECT detail FROM auth_events WHERE action='password_changed'")
    assert len(events) == 1 and NEW not in events[0][0]


def test_password_change_revokes_all_tenants_and_pin_sessions(app):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships VALUES(2,1,'planner')")
    assert app.test_cli_runner().invoke(args=['set-pin','planner','--tenant','alpha','--reason','Test'],input='654321\n654321\n').exit_code == 0
    client, other, pin = app.test_client(), app.test_client(), app.test_client()
    sign_in(client,'planner'); sign_in(other,'planner',tenant='beta')
    pin.get('/login')
    assert post(pin,'/login',tenant='alpha',username='planner',method='pin',pin='654321').status_code == 302
    assert change(client).status_code == 302
    for stale in [other,pin]:
        assert stale.get('/account/security').location == '/login'


def test_pin_enroll_change_disable_and_scope(app):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships VALUES(2,1,'planner')")
    client, other = app.test_client(), app.test_client()
    sign_in(client,'planner'); sign_in(other,'planner',tenant='beta')
    assert change(client,action='pin',new_credential='123456',confirm_credential='123456').status_code == 302
    assert other.get('/account/security').status_code == 200
    client.get('/login')
    assert post(client,'/login',tenant='alpha',username='planner',method='pin',pin='123456').status_code == 302
    assert change(client,action='pin',new_credential='654321',confirm_credential='654321').status_code == 302
    client.get('/login')
    assert post(client,'/login',tenant='alpha',username='planner',method='pin',pin='123456').status_code == 200
    client.get('/login')
    assert post(client,'/login',tenant='alpha',username='planner',method='pin',pin='654321').status_code == 302
    assert change(client,action='disable_pin').status_code == 302
    client.get('/login')
    assert post(client,'/login',tenant='alpha',username='planner',method='pin',pin='654321').status_code == 200
    assert rows(app,'SELECT pin_hash FROM pin_credentials WHERE user_id=1')[0][0] is None


def test_reauthentication_throttle_and_csrf(app):
    client=app.test_client();sign_in(client,'planner')
    assert client.post('/account/security',data={'action':'password'}).status_code == 400
    for _ in range(5):
        assert change(client,current_password='wrong-password').status_code == 400
    assert change(client).status_code == 429
    assert check_password_hash(rows(app,'SELECT password_hash FROM users WHERE id=1')[0][0],OLD)
    assert len(rows(app,"SELECT * FROM auth_events WHERE action='credential_change_failed'")) == 5
    app.config['AUTH_CLOCK'] = lambda: 9999999999
    assert change(client).status_code == 302


@pytest.mark.parametrize('overrides', [dict(new_credential='short',confirm_credential='short'),dict(confirm_credential='different'),dict(new_credential=OLD,confirm_credential=OLD),dict(action='pin',new_credential='abcdef',confirm_credential='abcdef'),dict(action='pin',new_credential='１２３４５６',confirm_credential='１２３４５６'),dict(action='unknown')])
def test_invalid_changes_preserve_credentials(app,overrides):
    client=app.test_client();sign_in(client,'planner')
    assert change(client,**overrides).status_code == 400
    assert check_password_hash(rows(app,'SELECT password_hash FROM users WHERE id=1')[0][0],OLD)
    assert not rows(app,"SELECT * FROM auth_events WHERE action IN ('password_changed','pin_changed')")


def test_platform_password_change_and_separation(app,developer):
    other=app.test_client();other.get('/developer/login')
    assert post(other,'/developer/login',username='developer',password='developer-test-password').status_code == 302
    page=developer.get('/account/security')
    assert b'Change password' in page.data and b'Set or change PIN' not in page.data
    assert change(developer,action='pin').status_code == 400
    assert change(developer,current_password='wrong-password').status_code == 400
    assert change(developer,current_password='developer-test-password').status_code == 302
    assert other.get('/developer').location == '/developer/login'
    assert developer.get('/account/security').location == '/login'
    assert post(developer,'/developer/login',username='developer',password=NEW).status_code == 302
    assert check_password_hash(rows(app,'SELECT password_hash FROM users WHERE id=1')[0][0],OLD)
    assert len(rows(app,"SELECT * FROM platform_events WHERE action='password_changed'")) == 1


@pytest.mark.parametrize('platform',[False,True])
def test_trusted_recovery_invalidates_sessions(app,developer,platform):
    client=developer if platform else app.test_client()
    if not platform: sign_in(client,'planner')
    args=['reset-password','developer' if platform else 'planner','--reason','Verified owner recovery']
    if platform:args.append('--platform')
    result=app.test_cli_runner().invoke(args=args,input=NEW+'\n'+NEW+'\n')
    assert result.exit_code == 0, result.output
    assert NEW not in result.output
    assert client.get('/account/security').location == '/login'
    table='developer_accounts' if platform else 'users'
    assert check_password_hash(rows(app,f'SELECT password_hash FROM {table} WHERE id=1')[0][0],NEW)


def test_anonymous_cannot_change_credentials(app):
    client=app.test_client();client.get('/login')
    assert change(client).location == '/login'
