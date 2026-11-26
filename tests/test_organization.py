import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_workflow import app, post, rows, sign_in
from test_admin import admin_client


def save(client, **overrides):
    data = dict(version='0', name='Alpha Manufacturing', contact_name='Shop contact',
                contact_email='shop@example.com', phone='555-0100', address='123 Shop Road\nBuilding A', reason='Update company profile')
    data.update(overrides)
    return post(client, '/admin/organization', **data)


def test_portal_and_audited_profile_update(app):
    client = admin_client(app)
    portal = client.get('/admin').data
    for label in [b'Organization management', b'User management', b'Production controls', b'Security history']:
        assert label in portal
    assert b'alpha' in client.get('/admin/organization').data
    assert save(client).status_code == 302
    page = client.get('/admin/organization?saved=1').data
    assert b'Alpha Manufacturing' in page and b'shop@example.com' in page
    organization = rows(app, 'SELECT * FROM tenants')[0]
    assert organization['name'] == 'Alpha Manufacturing' and organization['slug'] == 'alpha'
    audit = rows(app, "SELECT * FROM auth_events WHERE action='organization_updated'")[0]
    assert audit['actor'] == 'user:5' and audit['tenant_id'] == 1
    changes = json.loads(audit['detail'])
    assert changes['changes']['name'] == {'before': 'Alpha', 'after': 'Alpha Manufacturing'}
    assert changes['reason'] == 'Update company profile'
    sign_in(client, 'admin')  # Same organization code still works.
    assert save(client, version='1').status_code == 302  # No-op does not manufacture another event.
    assert len(rows(app, "SELECT * FROM auth_events WHERE action='organization_updated'")) == 1
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert rows(app, 'SELECT contact_email FROM organization_profiles')[0][0] == 'shop@example.com'


@pytest.mark.parametrize('role', ['planner', 'operator', 'inspector', 'viewer'])
def test_profile_requires_admin(app, role):
    client = app.test_client()
    sign_in(client, role)
    assert client.get('/admin').status_code == 403
    assert client.get('/admin/organization').status_code == 403
    assert save(client).status_code == 403
    assert not rows(app, 'SELECT * FROM organization_profiles')


@pytest.mark.parametrize('invalid', [dict(name=' '), dict(name='x'*161), dict(contact_email='bad-address'), dict(phone='x'*61), dict(address='x'*1001), dict(reason=''), dict(contact_name='bad\x00name')])
def test_profile_validation_is_atomic(app, invalid):
    client = admin_client(app)
    assert save(client, **invalid).status_code == 400
    assert rows(app, 'SELECT name FROM tenants')[0][0] == 'Alpha'
    assert not rows(app, 'SELECT * FROM organization_profiles')


def test_cannot_target_another_tenant_or_change_signin_code(app):
    client = admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships VALUES(2,4,'admin')")
        conn.execute("INSERT INTO organization_profiles(tenant_id,contact_email) VALUES(2,'private@beta.example')")
    assert b'private@beta.example' not in client.get('/admin/organization?tenant_id=2').data
    assert save(client, tenant_id='2', slug='hijacked').status_code == 302
    assert rows(app, 'SELECT name,slug FROM tenants WHERE id=2')[0][:] == ('Beta', 'beta')
    assert rows(app, 'SELECT slug FROM tenants WHERE id=1')[0][0] == 'alpha'
    assert client.get('/admin/organizations/2').status_code == 404
    sign_in(client, 'viewer', tenant='beta')
    assert b'private@beta.example' in client.get('/admin/organization').data
    assert b'shop@example.com' not in client.get('/admin/organization').data


def test_concurrent_profile_edits_and_csrf(app):
    clients = [admin_client(app), admin_client(app)]
    assert clients[0].post('/admin/organization', data={'name':'Unauthorized'}).status_code == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda c: save(c).status_code, clients)) == [302, 409]
    assert len(rows(app, "SELECT * FROM auth_events WHERE action='organization_updated'")) == 1


def test_profile_rollback_if_audit_fails(app):
    client = admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_org_audit BEFORE INSERT ON auth_events WHEN NEW.action='organization_updated' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        save(client)
    assert rows(app, 'SELECT name FROM tenants')[0][0] == 'Alpha'
    assert not rows(app, 'SELECT * FROM organization_profiles')


def test_profile_content_is_escaped(app):
    client = admin_client(app)
    assert save(client, name='<script>bad</script>', address='<img src=x onerror=bad()>').status_code == 302
    html = client.get('/admin/organization').data
    assert b'<script>' not in html and b'&lt;script&gt;' in html
    assert b'<img src=x' not in html
