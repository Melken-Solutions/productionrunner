import sqlite3
import pytest
from test_workflow import app, post, rows, sign_in, prepared, create_ticket
from test_serials import prepared_units, complete, inspect


def operator(app):
    client=app.test_client();sign_in(client,'operator');return client


def test_batch_confirmation_is_read_only_and_completion_returns_to_touch_view(app):
    client,path=prepared(app);code=path.split('/')[-1];sign_in(client,'operator')
    assert client.get('/shop-floor/scan?code=  '+code+'%0D%0A').location=='/shop-floor/work/'+code
    view=client.get('/shop-floor/work/'+code)
    assert b'Complete operation' in view.data and b'Cut to size' in view.data
    op=rows(app,'SELECT id FROM operations ORDER BY sequence')[0][0]
    confirm=f'/shop-floor/work/{code}/confirm?version=1&operation_id={op}'
    assert b'All 2 units' in client.get(confirm).data
    assert not rows(app,'SELECT * FROM operations WHERE completed_by IS NOT NULL')
    response=post(client,path+'/complete',version='1',operation_id=str(op),confirm='yes',shop_floor='1')
    assert response.location=='/shop-floor/work/'+code
    assert b'Assemble' in client.get(response.location).data
    assert client.get(confirm).status_code==409
    assert post(client,path+'/complete',version='1',operation_id=str(op),confirm='yes',shop_floor='1').status_code==409
    assert len(rows(app,"SELECT * FROM events WHERE action='operation_completed'"))==1


def test_serial_selection_and_independent_progress(app):
    client,ticket,units,ops=prepared_units(app);sign_in(client,'operator')
    parent=client.get('/shop-floor/work/'+ticket).data
    assert b'Choose the individual unit' in parent and b'Complete operation' not in parent
    assert client.get(f'/shop-floor/work/{ticket}/confirm?version=1&operation_id={ops[0]["id"]}').status_code==409
    code=units[0]['id'];view='/shop-floor/work/'+code
    assert client.get('/shop-floor/scan?code='+code).location==view
    assert b'S1' in client.get(view+'/confirm?version=1&operation_id='+str(ops[0]['id'])).data
    result=post(client,'/units/'+code+'/complete',version='1',operation_id=str(ops[0]['id']),confirm='yes',shop_floor='1')
    assert result.location==view
    assert rows(app,'SELECT state FROM serial_units WHERE id=?',(units[1]['id'],))[0][0]=='ready'
    assert complete(client,units[0],ops[1],2).status_code==302
    assert b'Production complete' in client.get(view).data
    sign_in(client,'inspector');assert inspect(client,units[0],result='fail').status_code==302
    sign_in(client,'operator')
    assert b'HOLD' in client.get(view).data and b'Complete operation' not in client.get(view).data
    assert b'Complete operation' in client.get('/shop-floor/work/'+units[1]['id']).data


def test_cancel_after_confirmation_prevents_write(app):
    client,path=prepared(app);code=path.split('/')[-1];op=rows(app,'SELECT id FROM operations ORDER BY sequence')[0][0]
    sign_in(client,'operator')
    assert client.get(f'/shop-floor/work/{code}/confirm?version=1&operation_id={op}').status_code==200
    admin=app.test_client();sign_in(admin,'admin')
    assert post(admin,path+'/cancel',version='1',reason='Cancelled',confirm='yes').status_code==302
    assert post(client,path+'/complete',version='1',operation_id=str(op),confirm='yes',shop_floor='1').status_code==409
    page=client.get('/shop-floor/work/'+code).data
    assert b'CANCELLED' in page and b'Complete operation' not in page
    assert code.encode() not in client.get('/shop-floor').data
    assert client.get('/shop-floor/scan?code='+code).status_code==302


@pytest.mark.parametrize('role',['viewer','planner','inspector','admin'])
def test_touch_views_require_operator(app,role):
    client,path=prepared(app);sign_in(client,role)
    for route in ['/shop-floor','/shop-floor/scan?code='+path.split('/')[-1],'/shop-floor/work/'+path.split('/')[-1],'/shop-floor/work/'+path.split('/')[-1]+'/confirm']:
        assert client.get(route).status_code==403


def test_touch_views_tenant_isolated_and_unknown_scan_safe(app):
    client,path=prepared(app);code=path.split('/')[-1]
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','operator','--tenant','beta','--roles','operator']).exit_code==0
    sign_in(client,'operator',tenant='beta')
    assert code.encode() not in client.get('/shop-floor').data
    for route in ['/shop-floor/scan?code='+code,'/shop-floor/work/'+code,'/shop-floor/work/'+code+'/confirm','/shop-floor/scan?code=JR-U-UNKNOWN']:
        assert client.get(route).status_code==404
    assert client.get('/shop-floor?page=bad').status_code==400


def test_queue_hot_order_and_pagination(app):
    client,path=prepared(app)
    second=create_ticket(client,pn='HOT-PN').location
    assert post(client,second+'/priority',version='1',action='hot',priority='1').status_code==302
    sign_in(client,'operator')
    html=client.get('/shop-floor').data
    assert html.index(b'HOT-PN') < html.index(b'PN-001')
    assert b'Next operation' not in html  # No accidental inline completion in queue.
    assert client.get('/shop-floor?page=2').status_code==200


def test_confirmation_requires_exact_operation_and_version_and_csrf(app):
    client,path=prepared(app);sign_in(client,'operator');code=path.split('/')[-1]
    ops=rows(app,'SELECT id FROM operations ORDER BY sequence')
    assert client.get(f'/shop-floor/work/{code}/confirm').status_code==409
    assert client.get(f'/shop-floor/work/{code}/confirm?version=1&operation_id={ops[1][0]}').status_code==409
    assert client.post(path+'/complete',data=dict(version='1',operation_id=ops[0][0],confirm='yes',shop_floor='1')).status_code==400
    assert not rows(app,'SELECT * FROM operations WHERE completed_by IS NOT NULL')
