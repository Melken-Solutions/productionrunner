from test_workflow import app, post, rows, sign_in
from test_lots import setup, tv, accept, move
from productionrunner.shipment_targets import target_for
from productionrunner.database import connect


def target(app, tid):
    with connect(app.config['DATABASE']) as db:
        return target_for(db, 1, db.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone())


def plan(app,c,tid,**kw):
    return post(c,f'/tickets/{tid}/shipment-target',**(dict(version=tv(app,tid),quantity='50',ship_date='2026-11-13',reason_choice='planning:Adjust schedule or priority')|kw))


def test_target_tracks_partial_departure_and_next_run(app):
    c,tid,units,ops=setup(app)
    assert post(c,f'/tickets/{tid}/priority',version=tv(app,tid),action='hot',priority='1').status_code==302
    assert plan(app,c,tid).status_code==302
    assert target(app,tid)['remaining']==50
    assert target(app,tid)['hot_priority']==1
    for path in [f'/tickets/{tid}/lots',f'/tickets/{tid}/delivery',f'/units/{units[0]["id"]}', '/', '/fulfillment']:
        page=c.get(path)
        assert page.status_code==200
        assert b'Ship 50 more by 2026-11-13' in page.data
    accept(app,c,units[0]['id'],ops)
    assert target(app,tid)['accepted']==50 and not target(app,tid)['met']
    sign_in(c,'operator')
    assert move(app,c,tid,units[0]['id'],'pack').status_code==302
    assert target(app,tid)['packed']==50 and not target(app,tid)['met']
    assert move(app,c,tid,units[0]['id'],'depart').status_code==302
    assert target(app,tid)['met'] and target(app,tid)['order_remaining']==530
    sign_in(c,'planner')
    assert plan(app,c,tid,quantity='100').status_code==302
    assert target(app,tid)['shipped']==0 and target(app,tid)['remaining']==100
    assert len(rows(app,"SELECT * FROM events WHERE action='shipment_target_set'"))==2


def test_target_validation_and_authorization(app):
    c,tid,units,ops=setup(app)
    for values in [dict(quantity='0'),dict(quantity='581'),dict(quantity='1.5'),dict(ship_date='bad'),dict(reason_choice='')]:
        assert plan(app,c,tid,**values).status_code==400
    assert target(app,tid) is None
    assert plan(app,c,tid,version='0').status_code==409
    sign_in(c,'operator')
    assert plan(app,c,tid).status_code==403
    sign_in(c,'planner')
    assert post(c,f'/tickets/{tid}-unknown/shipment-target',version='1').status_code==404


def test_reversed_departure_reopens_target(app):
    c,tid,units,ops=setup(app)
    assert plan(app,c,tid).status_code==302
    accept(app,c,units[0]['id'],ops)
    sign_in(c,'operator')
    assert move(app,c,tid,units[0]['id'],'pack').status_code==302
    assert move(app,c,tid,units[0]['id'],'depart').status_code==302
    assert target(app,tid)['met']
    sign_in(c,'planner')
    record=rows(app,"SELECT id FROM fulfillment_records WHERE kind='depart'")[0]['id']
    assert post(c,f'/tickets/{tid}/delivery/records/{record}/reverse',version=tv(app,tid),reason='Incorrect departure',confirm='yes').status_code==302
    assert target(app,tid)['remaining']==50 and not target(app,tid)['met']


def test_shipment_target_stays_in_its_organization(app):
    c,tid,units,ops=setup(app)
    assert plan(app,c,tid).status_code==302
    with connect(app.config['DATABASE']) as db:
        db.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        uid=db.execute("SELECT id FROM users WHERE username='planner'").fetchone()[0]
        db.execute("INSERT INTO memberships(tenant_id,user_id,roles) VALUES(2,?,'planner')",(uid,))
        ticket=db.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone()
        assert target_for(db,2,ticket) is None
    sign_in(c,'planner',tenant='beta')
    assert c.get(f'/tickets/{tid}/delivery').status_code==404
    assert post(c,f'/tickets/{tid}/shipment-target',version='1',quantity='50',ship_date='2026-11-13',reason='Schedule').status_code==404
