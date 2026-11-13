from test_workflow import app,post,rows,sign_in
from test_request_job_plan import setup,payload
from test_intake import receive,decision,convert,rv,FIELDS
from test_lots import tv,accept,move
from test_shipment_targets import target
from productionrunner.database import connect
from productionrunner.delivery_schedule import request_schedule,ticket_schedule


def create_request(app,quantities=('50','100'),dates=('2026-11-11','2026-11-13'),total=150):
    c,plan=setup(app);plan['steps'][0]['first_piece']=False
    response=receive(c,quantity=str(total),required_date='2026-11-13',delivery_mode='staged',shipment_quantity=list(quantities),shipment_date=list(dates),**payload(plan))
    return c,plan,response


def test_initial_schedule_advances_with_actual_departures_and_reversal(app):
    c,plan,response=create_request(app);assert response.status_code==302
    rid=response.location.split('/')[-1]
    assert b'150 / 150 scheduled' in c.get(response.location).data
    assert decision(app,c,rid).status_code==302
    assert convert(app,c,rid,tracking='batch').status_code==400
    response=convert(app,c,rid,tracking='lot',lot_size='50');assert response.status_code==302
    tid=response.location.split('/')[-1]
    assert target(app,tid)['ship_date']=='2026-11-11'
    assert post(c,f'/tickets/{tid}/release',version=tv(app,tid),reason='Approved',plan_review='yes').status_code==302
    units=rows(app,'SELECT * FROM serial_units ORDER BY serial');ops=rows(app,'SELECT * FROM operations ORDER BY sequence')
    for n,u in enumerate(units):
        accept(app,c,u['id'],ops);sign_in(c,'operator')
        assert move(app,c,tid,u['id'],'pack').status_code==302
        assert move(app,c,tid,u['id'],'depart').status_code==302
        t=target(app,tid)
        if n==0:assert t['shipment_number']==2 and t['remaining']==100 and t['ship_date']=='2026-11-13'
        if n==1:assert t['remaining']==50 and t['shipped']==50
        if n==2:assert t['met'] and t['order_remaining']==0
    sign_in(c,'planner')
    record=rows(app,"SELECT id FROM fulfillment_records WHERE kind='depart' ORDER BY id DESC")[0]['id']
    assert post(c,f'/tickets/{tid}/delivery/records/{record}/reverse',version=tv(app,tid),reason='Correction',confirm='yes').status_code==302
    assert target(app,tid)['remaining']==50


def test_draft_balance_blocks_approval_and_edit_invalidates_approval(app):
    c,plan,response=create_request(app,quantities=('50',),dates=('2026-11-11',))
    rid=response.location.split('/')[-1]
    assert decision(app,c,rid).status_code==409
    data=FIELDS|payload(plan)|dict(quantity='150',required_date='2026-11-13',delivery_mode='staged',shipment_quantity=['50','100'],shipment_date=['2026-11-11','2026-11-13'],reason='Schedule delivery')
    assert post(c,f'/requests/{rid}/edit',version=rv(app,rid),**data).status_code==302
    assert decision(app,c,rid).status_code==302
    data['shipment_date']=['2026-11-10','2026-11-13']
    assert post(c,f'/requests/{rid}/edit',version=rv(app,rid),**data).status_code==302
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
    assert convert(app,c,rid,tracking='lot').status_code==409
    assert b'2026-11-10' in c.get(f'/requests/{rid}/edit').data


def test_schedule_rejects_invalid_totals_dates_and_omitted_edit_does_not_erase(app):
    c,plan,response=create_request(app);rid=response.location.split('/')[-1]
    for quantities,dates in [(['151'],['2026-11-11']),(['-1'],['2026-11-11']),(['1.5'],['2026-11-11']),(['50'],['bad']),(['50'],['2026-11-14']),(['50','100'],['2026-11-13','2026-11-11']),(['50'],[])]:
        assert receive(c,quantity='150',required_date='2026-11-13',delivery_mode='staged',shipment_quantity=quantities,shipment_date=dates).status_code==400
    data=FIELDS|payload(plan)|dict(quantity='149',required_date='2026-11-13',reason='Correct total')
    assert post(c,f'/requests/{rid}/edit',version=rv(app,rid),**data).status_code==400
    with connect(app.config['DATABASE']) as db:
        assert len(request_schedule(db,1,rid)['shipments'])==2
        assert request_schedule(db,2,rid)['mode']=='together'


def test_published_schedule_edit_is_complete_authorized_and_audited(app):
    c,plan,response=create_request(app);rid=response.location.split('/')[-1]
    decision(app,c,rid);tid=convert(app,c,rid,tracking='lot',lot_size='50').location.split('/')[-1]
    url=f'/tickets/{tid}/delivery-schedule'
    data=dict(delivery_mode='staged',shipment_quantity=['75','75'],shipment_date=['2026-11-11','2026-11-13'],reason_choice='planning:Adjust schedule or priority')
    assert post(c,url,version=tv(app,tid),**data).status_code==302
    assert target(app,tid)['quantity']==75
    for path in (f'/tickets/{tid}/delivery',f'/tickets/{tid}/lots'):
        page=c.get(path)
        assert page.status_code==200 and b'Edit remaining delivery schedule' in page.data
        assert b'value="75"' in page.data
    assert len(rows(app,"SELECT * FROM events WHERE action='delivery_schedule_saved'"))==2
    assert post(c,url,version='0',**data).status_code==409
    assert post(c,url,version=tv(app,tid),**(data|dict(shipment_quantity=['50','50']))).status_code==409
    assert post(c,f'/tickets/{tid}/shipment-target',version=tv(app,tid),quantity='50',ship_date='2026-11-11',reason='Override').status_code==409
    sign_in(c,'operator');assert post(c,url,version=tv(app,tid),**data).status_code==403
    with connect(app.config['DATABASE']) as db:assert ticket_schedule(db,2,tid) is None
