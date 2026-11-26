import json
import sqlite3

from test_workflow import app, post, rows, sign_in
from test_admin import admin_client


def stations(app):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        a=conn.execute("INSERT INTO stations(tenant_id,name) VALUES(1,'Laser 1')").lastrowid
        b=conn.execute("INSERT INTO stations(tenant_id,name) VALUES(1,'Timesaver')").lastrowid
    return a,b


def assign(client, station='', user=2, version='0'):
    return post(client,f'/admin/users/{user}/primary-station',station_id=str(station),version=version,reason='Primary work area')


def selected(client):
    with client.session_transaction() as session:
        return session.get('shop_station_1')


def test_primary_switch_and_next_login(app):
    a,b=stations(app);admin=admin_client(app)
    assert assign(admin,a).status_code==302
    assert b'Save primary station' in admin.get('/admin/users/2').data
    operator=app.test_client();sign_in(operator,'operator')
    page=operator.get('/shop-floor')
    assert page.status_code==200 and selected(operator)==str(a)
    assert b'Laser 1 \xc2\xb7 Primary' in page.data and b'Change station' in page.data
    assert operator.get('/shop-floor?station='+str(b)).status_code==200
    assert selected(operator)==str(b)
    operator.get('/shop-floor');assert selected(operator)==str(b)
    assert assign(admin,b,version='1').status_code==302
    # Existing session stays where the operator chose; admin changes do not log them out.
    operator.get('/shop-floor');assert selected(operator)==str(b)
    operator.get('/shop-floor?station=');assert selected(operator)==''
    operator.get('/shop-floor');assert selected(operator)==''
    sign_in(operator,'operator');operator.get('/shop-floor');assert selected(operator)==str(b)
    event=rows(app,"SELECT * FROM auth_events WHERE action='primary_station_changed' ORDER BY id DESC")[0]
    assert event['actor']=='user:5' and json.loads(event['detail'])['station_id']==b
    assert assign(admin,version='2').status_code==302
    sign_in(operator,'operator');page=operator.get('/shop-floor')
    assert selected(operator)=='' and b'Choose your station' in page.data


def test_station_scope_roles_stale_and_inactive(app):
    a,b=stations(app);admin=admin_client(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        foreign=conn.execute("INSERT INTO stations(tenant_id,name) VALUES(2,'Other org')").lastrowid
        conn.execute('UPDATE stations SET active=0 WHERE id=?',(b,))
    for invalid in [foreign,b,'bad',-1]:
        assert assign(admin,invalid).status_code==400
    assert assign(admin,a,user=4).status_code==409
    assert assign(admin,a,user=999).status_code==404
    assert assign(admin,a,version='9').status_code==409
    assert admin.post('/admin/users/2/primary-station',data={}).status_code==400
    for name in ['operator','planner','viewer']:
        c=app.test_client();sign_in(c,name);assert assign(c,a).status_code==403
    assert assign(admin,a).status_code==302
    operator=app.test_client();sign_in(operator,'operator');operator.get('/shop-floor')
    assert operator.get('/shop-floor?station='+str(foreign)).status_code==404
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('UPDATE stations SET active=0 WHERE id=?',(a,))
    assert operator.get('/shop-floor').status_code==200 and selected(operator)==''
    assert assign(admin,a,version='1').status_code==400


def test_shared_browser_does_not_inherit_station(app):
    a,b=stations(app);admin=admin_client(app);assert assign(admin,a).status_code==302
    c=app.test_client();sign_in(c,'operator');c.get('/shop-floor?station='+str(b))
    sign_in(c,'admin');page=c.get('/shop-floor')
    assert selected(c)=='' and b'Choose your station' in page.data
    script=c.get('/static/station_queue.js').data
    assert b'localStorage' not in script
