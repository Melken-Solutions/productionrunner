import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, post, rows, sign_in
from test_admin import admin_client


@pytest.fixture
def developer(app):
    result=app.test_cli_runner().invoke(args=['add-developer','developer','--password','developer-test-password','--reason','Testing'])
    assert result.exit_code==0, result.output
    client=app.test_client()
    client.get('/developer/login')
    assert post(client,'/developer/login',username='developer',password='developer-test-password').location=='/developer'
    return client


def create_org(client, **overrides):
    data=dict(name='Beta Works',slug='beta',username='beta-admin',password='beta-test-password',password_confirm='beta-test-password',reason='New organization')
    data.update(overrides)
    return post(client,'/developer/organizations',**data)


def assign(client, user=4, tenant=1, version=0, action='grant'):
    return post(client,f'/developer/organizations/{tenant}/members/{user}',version=str(version),action=action,reason='Admin assignment')


def test_local_admin_cannot_reach_or_create_developer_access(app,developer):
    client=admin_client(app)
    for path in ['/developer','/developer/organizations/1']:
        assert client.get(path).status_code==403
    assert create_org(client).status_code==403
    assert assign(client).status_code==403
    assert post(client,'/admin/users/4/access',action='roles',roles=['developer','admin'],version='0',reason='Escalate').status_code==400
    assert post(client,'/developer/login',username='admin',password='test-password-123').status_code==200
    assert rows(app,'SELECT COUNT(*) FROM developer_accounts')[0][0]==1


def test_create_organization_and_initial_admin_atomic(app,developer):
    assert b'DEVELOPER ADMIN' in developer.get('/developer').data
    response=create_org(developer)
    assert response.status_code==302
    assert b'Beta Works' in developer.get(response.location).data
    org=rows(app,"SELECT * FROM tenants WHERE slug='beta'")[0]
    user=rows(app,"SELECT * FROM users WHERE username='beta-admin'")[0]
    assert rows(app,'SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?',(org['id'],user['id']))[0][0]=='admin'
    local=app.test_client();local.get('/login')
    assert post(local,'/login',tenant='beta',username='beta-admin',password='beta-test-password').status_code==302
    assert local.get('/admin').status_code==200
    assert local.get('/developer').status_code==403
    assert create_org(developer,slug='gamma').status_code==409  # Existing global username is never claimed.
    assert not rows(app,"SELECT * FROM tenants WHERE slug='gamma'")
    assert create_org(developer,username='second').status_code==409
    assert not rows(app,"SELECT * FROM users WHERE username='second'")
    assert all('beta-test-password' not in row[0] for row in rows(app,'SELECT detail FROM platform_events'))


def test_developer_does_not_bypass_production_roles(app,developer):
    assert developer.get('/').location=='/login'
    assert developer.get('/admin/users').location=='/login'
    assert post(developer,'/tickets/new').location=='/login'
    assert not rows(app,'SELECT * FROM tickets')


def test_assignment_last_admin_versions_and_session_invalidation(app,developer):
    local=app.test_client();sign_in(local,'viewer')
    assert assign(developer,user=5,action='remove').status_code==409
    assert assign(developer).status_code==302
    assert local.get('/').location=='/login'
    assert assign(developer,action='remove').status_code==409
    assert assign(developer,user=5,action='remove').status_code==302
    assert assign(developer,version=1,action='remove').status_code==409
    assert rows(app,'SELECT roles FROM memberships WHERE user_id=4')[0][0]=='admin,viewer'
    assert rows(app,"SELECT COUNT(*) FROM auth_events WHERE action='local_admin_changed'")[0][0]==2


def test_cross_tenant_member_id_is_rejected(app,developer):
    response=create_org(developer)
    org=int(response.location.rsplit('/',1)[1])
    assert assign(developer,tenant=org,user=4).status_code==404
    assert assign(developer,tenant=999).status_code==404
    assert developer.get('/developer/organizations/999').status_code==404


def test_profile_versions_shared_with_local_admin(app,developer):
    local=admin_client(app)
    from test_organization import save
    assert save(local).status_code==302
    data=dict(version='0',name='Developer rename',reason='Profile correction')
    assert post(developer,'/developer/organizations/1/profile',**data).status_code==409
    data['version']='1'
    assert post(developer,'/developer/organizations/1/profile',**data).status_code==302
    assert rows(app,'SELECT name FROM tenants WHERE id=1')[0][0]=='Developer rename'
    assert save(local,version='1').status_code==409
    assert b'Developer rename' in local.get('/admin/organization').data


def test_platform_audit_atomic_and_immutable(app,developer):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_platform BEFORE INSERT ON platform_events WHEN NEW.action='organization_created' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        create_org(developer)
    assert not rows(app,"SELECT * FROM tenants WHERE slug='beta'")
    assert not rows(app,"SELECT * FROM users WHERE username='beta-admin'")
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('DELETE FROM platform_events')


def test_developer_login_csrf_throttling_and_logout(app,developer):
    client=app.test_client();client.get('/developer/login')
    assert client.post('/developer/login',data={'username':'developer','password':'developer-test-password'}).status_code==400
    for _ in range(5):
        assert post(client,'/developer/login',username='developer',password='wrong').status_code==200
    assert post(client,'/developer/login',username='developer',password='developer-test-password').status_code==429
    assert developer.post('/developer/organizations',data={}).status_code==400
    assert post(developer,'/logout').status_code==302
    assert developer.get('/developer').location=='/developer/login'


def test_developer_revoke_restore_does_not_resurrect_session(app,developer):
    runner=app.test_cli_runner()
    assert runner.invoke(args=['set-developer-access','developer','--inactive','--reason','Test']).exit_code!=0
    assert runner.invoke(args=['add-developer','backup','--password','backup-test-password','--reason','Test']).exit_code==0
    assert runner.invoke(args=['set-developer-access','developer','--inactive','--reason','Test']).exit_code==0
    assert runner.invoke(args=['set-developer-access','developer','--active','--reason','Test']).exit_code==0
    assert developer.get('/developer').location=='/developer/login'


def test_local_pin_reset_cannot_change_developer_identity(app,developer):
    client=admin_client(app)
    assert post(client,'/admin/users/5/pin',action='reset',pin='123456',pin_confirm='123456',version='0',reason='Test').status_code==302
    assert developer.get('/developer').status_code==200
    assert rows(app,'SELECT version FROM developer_accounts')[0][0]==1


def test_concurrent_initial_organization_creation(app,developer):
    other=app.test_client();other.get('/developer/login')
    assert post(other,'/developer/login',username='developer',password='developer-test-password').status_code==302
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda c:create_org(c).status_code,[developer,other]))==[302,409]
    assert len(rows(app,"SELECT * FROM tenants WHERE slug='beta'"))==1


def test_additional_local_admin_and_scoped_developer_history(app,developer):
    assert create_org(developer).status_code==302
    response=post(developer,'/developer/organizations/1/admins',username='alpha-extra',password='extra-test-password',password_confirm='extra-test-password',reason='Additional local owner')
    assert response.status_code==302
    local=admin_client(app)
    history=local.get('/security-history').data
    assert b'Additional local owner' in history
    assert b'Beta Works' not in history
    assert b'developer_login' not in history
    new_id=rows(app,"SELECT id FROM users WHERE username='alpha-extra'")[0][0]
    assert rows(app,'SELECT roles FROM memberships WHERE tenant_id=1 AND user_id=?',(new_id,))[0][0]=='admin'


def test_invalid_organization_creation_has_no_side_effects(app,developer):
    for values in [dict(slug='BAD SLUG'),dict(name=''),dict(password_confirm='mismatch'),dict(reason=''),dict(username='bad username')]:
        assert create_org(developer,**values).status_code==400
    assert rows(app,'SELECT COUNT(*) FROM tenants')[0][0]==1
    assert not rows(app,"SELECT * FROM users WHERE username='beta-admin'")
