import base64
import json
import sqlite3
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from test_workflow import app, post, rows, sign_in, create_ticket
from test_admin import admin_client
from productionrunner.branding import foreground

URL = '/admin/organization/branding'


def logo():
    output = BytesIO()
    Image.new('RGB', (200, 60), '#123456').save(output, 'PNG')
    output.seek(0)
    return output


def save(client, **overrides):
    data = dict(action='save', version='0', display_name='Alpha Works', primary_color='#eeeeee',
                accent_color='#003366', reason='Company branding')
    data.update(overrides)
    return post(client, URL, **data)


def test_save_logo_and_render_every_surface(app):
    c = admin_client(app)
    assert save(c, logo=(logo(), 'logo.png')).status_code == 302
    assert c.get('/branding/logo').mimetype == 'image/png'
    assert c.get('/branding/logo').data.startswith(b'\x89PNG')
    for path in ('/', '/admin/organization', '/tv/board'):
        page = c.get(path)
        assert page.status_code == 200
        assert b'Alpha Works' in page.data and b'/branding/logo' in page.data
        assert b'--brand-primary:#eeeeee' in page.data
        assert "style-src 'self' 'nonce-" in page.headers['Content-Security-Policy']
    assert c.get('/tv/data').json['organization'] == 'Alpha Works'
    sign_in(c, 'planner')
    ticket = create_ticket(c).location
    page = c.get(ticket + '/traveler')
    assert b'Alpha Works' in page.data and b'class="barcode"' in page.data
    serial = create_ticket(c, tracking='serial', serials='S1\nS2').location
    unit = rows(app, 'SELECT id FROM serial_units WHERE ticket_id=?', (serial.split('/')[-1],))[0][0]
    assert b'Alpha Works' in c.get('/units/' + unit + '/traveler').data
    sign_in(c, 'operator')
    assert b'Alpha Works' in c.get('/shop-floor').data
    event = rows(app, "SELECT * FROM auth_events WHERE action='branding_updated'")[0]
    assert event['actor'] == 'user:5'
    assert json.loads(event['detail'])['after']['logo_sha256']
    assert rows(app, 'SELECT name,slug FROM tenants')[0][:] == ('Alpha', 'alpha')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert rows(app, 'SELECT version FROM organization_branding')[0][0] == 1


def test_preview_does_not_write_and_carries_logo(app):
    c = admin_client(app)
    page = save(c, action='preview', logo=(logo(), 'logo.png'), reason='')
    assert page.status_code == 200 and b'Preview only' in page.data
    assert b'data:image/png;base64,' in page.data
    assert not rows(app, 'SELECT * FROM organization_branding')
    assert not rows(app, "SELECT * FROM auth_events WHERE action LIKE 'branding_%'")
    encoded = base64.b64encode(logo().getvalue()).decode()
    assert save(c, preview_logo=encoded).status_code == 302
    assert c.get('/branding/logo').status_code == 200


def test_reset_remove_and_stale_forms(app):
    c = admin_client(app)
    assert save(c, logo=(logo(), 'logo.png')).status_code == 302
    assert save(c).status_code == 409
    assert save(c, version='1', remove_logo='yes').status_code == 302
    assert c.get('/branding/logo').status_code == 404
    assert save(c, version='2', action='reset').status_code == 302
    page = c.get('/')
    assert b'Alpha Works' not in page.data and b'--brand-primary:#102c3a' in page.data
    assert rows(app, 'SELECT version FROM organization_branding')[0][0] == 3
    assert rows(app, "SELECT * FROM auth_events WHERE action='branding_reset'")


@pytest.mark.parametrize('invalid', [dict(primary_color='red'), dict(accent_color='#fff;}</style>'),
    dict(display_name='x'*161), dict(reason=''), dict(preview_logo='%%%'),
    dict(logo=(b'<svg onload="bad()"></svg>', 'fake.png'))])
def test_invalid_input_atomic(app, invalid):
    c = admin_client(app)
    if 'logo' in invalid:
        data, name = invalid['logo']; invalid = dict(logo=(BytesIO(data), name))
    assert save(c, **invalid).status_code == 400
    assert not rows(app, 'SELECT * FROM organization_branding')


def test_roles_tenant_isolation_and_display_assets(app):
    c = admin_client(app)
    assert save(c, logo=(logo(), 'logo.png'), tenant_id='2').status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships VALUES(2,4,'admin')")
        conn.execute("UPDATE memberships SET roles='display' WHERE tenant_id=1 AND user_id=3")
    sign_in(c, 'viewer', tenant='beta')
    assert b'Alpha Works' not in c.get(URL).data
    assert c.get('/branding/logo?tenant_id=1').status_code == 404
    sign_in(c, 'inspector')
    assert c.get('/branding/logo').status_code == 200
    assert c.get('/tv/board').status_code == 200
    assert save(c).status_code == 403
    for name in ('operator', 'planner', 'viewer'):
        sign_in(c, name)
        assert c.get(URL).status_code == 403
        assert save(c).status_code == 403
    guest = app.test_client()
    assert guest.get('/branding/logo').status_code == 302
    assert b'Alpha Works' not in guest.get('/login').data


def test_csrf_concurrency_and_audit_rollback(app):
    clients = [admin_client(app), admin_client(app)]
    assert clients[0].post(URL, data={'action':'reset'}).status_code == 400
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda c: save(c).status_code, clients)) == [302, 409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_brand_audit BEFORE INSERT ON auth_events WHEN NEW.action='branding_updated' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        save(clients[0], version='1', display_name='Rollback')
    assert rows(app, 'SELECT display_name,version FROM organization_branding')[0][:] == ('Alpha Works', 1)


def test_escaped_name_size_limits_and_contrast(app):
    c = admin_client(app)
    assert save(c, display_name='<script>alert(1)</script>').status_code == 302
    assert b'&lt;script&gt;' in c.get('/').data
    assert save(c, version='1', logo=(BytesIO(b'x'*(2*1024*1024+1)), 'big.png')).status_code == 413
    oversized = BytesIO(); Image.new('RGB', (2049, 2)).save(oversized, 'PNG'); oversized.seek(0)
    assert save(c, version='1', logo=(oversized, 'wide.png')).status_code == 400
    assert foreground('#ffffff') == '#000000'
    assert foreground('#000000') == '#ffffff'
    assert foreground('#eeeeee') == '#000000'
