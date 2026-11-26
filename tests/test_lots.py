import json,sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app,post,rows,sign_in
from test_request_job_plan import setup as plan_setup,payload
from test_intake import receive,decision,convert
from productionrunner.fulfillment import progress
from productionrunner.database import connect


def setup(app,size=50,total=580):
    c,plan=plan_setup(app);plan['steps'][0]['first_piece']=False
    rid=receive(c,quantity=str(total),required_date='2026-11-13',**payload(plan)).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    response=convert(app,c,rid,tracking='lot',lot_size=str(size))
    assert response.status_code==302
    tid=response.location.split('/')[-1]
    assert post(c,f'/tickets/{tid}/release',version=tv(app,tid),reason='Reviewed all production requirements',plan_review='yes').status_code==302
    units=rows(app,'SELECT * FROM serial_units ORDER BY serial')
    ops=rows(app,'SELECT * FROM operations ORDER BY sequence')
    return c,tid,units,ops


def tv(app,tid):return str(rows(app,'SELECT version FROM tickets WHERE id=?',(tid,))[0][0])
def uv(app,uid):return str(rows(app,'SELECT version FROM serial_units WHERE id=?',(uid,))[0][0])
def qty(app,uid):return rows(app,'SELECT quantity FROM production_lots WHERE unit_id=?',(uid,))[0][0]


def complete(app,c,u,op,**kw):
    return post(c,f'/units/{u}/complete',**(dict(version=uv(app,u),operation_id=str(op['id']),confirm='yes',completed_quantity=str(qty(app,u)))|kw))


def accept(app,c,u,ops):
    sign_in(c,'operator')
    for op in ops:assert complete(app,c,u,op).status_code==302
    sign_in(c,'inspector')
    assert post(c,f'/units/{u}/inspect',version=uv(app,u),result='pass',notes='Dimensions checked',inspected_quantity=str(qty(app,u))).status_code==302


def move(app,c,tid,u,kind):
    return post(c,f'/tickets/{tid}/delivery/{kind}',version=tv(app,tid),unit_id=[u],confirm='yes',notes='Physical lot movement',destination='Warehouse',reference='SHIP-1',method='external')


def stats(app,tid):
    with connect(app.config['DATABASE']) as db:
        return progress(db,1,db.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone())


def action(app,c,uid,name,**kw):
    return post(c,f'/lots/{uid}/action',**(dict(action=name,version=uv(app,uid),reason='Segregate and track physical run')|kw))


def test_580_order_partial_station_inspection_delivery(app):
    c,tid,units,ops=setup(app)
    assert sum(qty(app,u['id']) for u in units)==580 and len(units)==12
    u=units[0]['id'];sign_in(c,'operator')
    assert complete(app,c,u,ops[1]).status_code==409
    assert complete(app,c,u,ops[0],completed_quantity='49').status_code==400
    assert complete(app,c,u,ops[0]).status_code==302
    assert complete(app,c,u,ops[0]).status_code==409
    assert move(app,c,tid,u,'pack').status_code==409
    assert complete(app,c,u,ops[1]).status_code==302
    assert post(c,f'/units/{u}/inspect',version=uv(app,u),result='pass',notes='Cannot inspect own work',inspected_quantity='50').status_code==403
    sign_in(c,'inspector')
    assert post(c,f'/units/{u}/inspect',version=uv(app,u),result='pass',notes='Verified',inspected_quantity='49').status_code==400
    assert post(c,f'/units/{u}/inspect',version=uv(app,u),result='pass',notes='Verified',inspected_quantity='50').status_code==302
    sign_in(c,'operator')
    assert move(app,c,tid,u,'pack').status_code==302
    assert move(app,c,tid,u,'depart').status_code==302
    state=stats(app,tid)
    assert (state['accepted'],state['departed'],state['remaining'],state['closed'])==(50,50,530,False)
    assert move(app,c,tid,u,'depart').status_code==409
    assert complete(app,c,units[1]['id'],ops[0]).status_code==302
    page=c.get(f'/tickets/{tid}/lots')
    assert page.status_code==200 and b'530' in page.data and b'2026-11-13' in page.data
    board=c.get('/tv/data').json
    assert board['rows'][0]['quantity']==580
    assert 'Departed: 50' in board['rows'][0]['operation']
    assert b'Quantity: 50' in c.get(f'/units/{u}/traveler').data


def test_split_preserves_prior_work_and_independence(app):
    c,tid,units,ops=setup(app,size=100)
    u=units[0]['id'];sign_in(c,'operator')
    assert complete(app,c,u,ops[0]).status_code==302
    assert action(app,c,u,'split',quantity='40').status_code==302
    child=rows(app,'SELECT unit_id FROM production_lots WHERE parent_id=?',(u,))[0][0]
    assert qty(app,u)==60 and qty(app,child)==40
    assert len(rows(app,'SELECT * FROM unit_completions WHERE operation_id=?',(ops[0]['id'],)))==2
    assert complete(app,c,child,ops[1]).status_code==302
    assert post(c,f'/units/{child}/inspect',version=uv(app,child),result='pass',notes='Own work',inspected_quantity='40').status_code==403
    assert sum(r['quantity'] for r in rows(app,'SELECT quantity FROM production_lots WHERE scrapped=0'))==580
    assert action(app,c,u,'split',quantity='60').status_code==400


def test_hold_scrap_replacement_and_remaining_route(app):
    c,tid,units,ops=setup(app)
    u=units[0]['id'];sign_in(c,'operator')
    assert action(app,c,u,'split',quantity='5').status_code==302
    small=rows(app,'SELECT unit_id FROM production_lots WHERE parent_id=?',(u,))[0][0]
    assert action(app,c,small,'hold').status_code==302
    assert complete(app,c,small,ops[0]).status_code==409
    assert action(app,c,small,'scrap').status_code==403
    sign_in(c,'admin');assert action(app,c,small,'scrap').status_code==302
    assert action(app,c,small,'scrap').status_code==409
    assert sum(r['quantity'] for r in rows(app,'SELECT quantity FROM production_lots WHERE scrapped=0'))==580
    assert rows(app,'SELECT SUM(quantity) FROM production_lots WHERE scrapped=1')[0][0]==5
    sign_in(c,'operator');assert complete(app,c,u,ops[0]).status_code==302
    assert stats(app,tid)['remaining']==580


def test_concurrent_completion_records_quantity_once(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    sign_in(c,'operator');other=app.test_client();sign_in(other,'operator')
    data=dict(version=uv(app,u),operation_id=str(ops[0]['id']),confirm='yes',completed_quantity='50')
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda client:post(client,f'/units/{u}/complete',**data).status_code,[c,other]))
    assert sorted(result)==[302,409]
    assert len(rows(app,'SELECT * FROM unit_completions'))==1


def test_per_lot_erp_checkpoint_does_not_wait_for_580(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    assert post(c,f'/tickets/{tid}/delivery/checks',version=tv(app,tid),action='add',name='SAP partial confirmation',stage='pack').status_code==302
    accept(app,c,u,ops)
    sign_in(c,'operator');assert move(app,c,tid,u,'pack').status_code==409
    sign_in(c,'planner')
    cid=rows(app,'SELECT id FROM fulfillment_checks')[0][0]
    assert post(c,f'/tickets/{tid}/delivery/checks',version=tv(app,tid),action='complete',check_id=str(cid),unit_id=[u],reference='SAP-CONF-50').status_code==302
    assert move(app,c,tid,u,'pack').status_code==302
    assert move(app,c,tid,u,'depart').status_code==302
    assert stats(app,tid)['departed']==50
    accept(app,c,units[1]['id'],ops)
    sign_in(c,'operator');assert move(app,c,tid,units[1]['id'],'pack').status_code==409


def test_lot_manifest_counts_pieces_not_travelers(app):
    from test_shipping import action as shipment_action,upload
    c,tid,units,ops=setup(app);u=units[0]['id'];accept(app,c,u,ops)
    sign_in(c,'admin')
    assert post(c,'/admin/shipping',action='destination',name='Warehouse',address='123 Road',reason='Setup').status_code==302
    assert post(c,f'/tickets/{tid}/shipping',destination_id='1',purpose='stock',method='ltl',label_owner='planner',reason='Reviewed').status_code==302
    assert move(app,c,tid,u,'pack').status_code==302
    pack=rows(app,"SELECT id FROM fulfillment_records WHERE kind='pack'")[0][0]
    assert post(c,f'/tickets/{tid}/shipping/boxes',pack_record_id=str(pack),weight_kg='25',dimensions_cm='40 x 30 x 20').status_code==302
    bid=rows(app,'SELECT id FROM shipping_boxes')[0][0]
    assert post(c,'/shipping',box_id=str(bid)).status_code==302
    assert shipment_action(app,c,1,'request').status_code==302
    assert upload(app,c,1).status_code==302
    v=rows(app,'SELECT version FROM shipping_loads')[0][0]
    assert post(c,'/shipping/1/depart',version=str(v),confirm='yes',notes='Driver departed').status_code==302
    assert stats(app,tid)['departed']==50


def test_lot_tenant_boundary(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    runner=app.test_cli_runner()
    runner.invoke(args=['add-tenant','beta','--name','Beta'])
    runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner'])
    sign_in(c,'planner',tenant='beta')
    assert c.get(f'/tickets/{tid}/lots').status_code==404
    assert action(app,c,u,'split',quantity='1').status_code==404


def test_timed_lot_completion_and_split_contributor_history(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    sign_in(c,'admin')
    assert post(c,'/admin/work',action='settings',version='0',required='1',reason='Time each run').status_code==302
    sign_in(c,'operator')
    assert complete(app,c,u,ops[0]).status_code==409
    assert post(c,f'/work/{u}',version=uv(app,u),action='start',mode='production').status_code==302
    assert action(app,c,u,'split',quantity='10').status_code==409
    assert complete(app,c,u,ops[0]).status_code==302
    assert 'Completed quantity 50' in rows(app,"SELECT reason FROM work_events WHERE action='finish'")[0][0]
    assert action(app,c,u,'split',quantity='10').status_code==302
    child=rows(app,'SELECT unit_id FROM production_lots WHERE parent_id=?',(u,))[0][0]
    from productionrunner.work_tracking import contributed
    with connect(app.config['DATABASE']) as db:
        user=db.execute("SELECT id FROM users WHERE username='operator'").fetchone()[0]
        assert contributed(db,1,tid,child,user)


def test_early_hold_rework_does_not_skip_remaining_operations(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    sign_in(c,'operator');assert complete(app,c,u,ops[0]).status_code==302
    assert action(app,c,u,'hold').status_code==302
    sign_in(c,'admin')
    assert post(c,f'/tickets/{tid}/rework',version=tv(app,tid),unit_id=u,reason='Defect',instructions='Repair cut',criteria='Dimensions').status_code==302
    case=rows(app,'SELECT * FROM rework_cases')[0]
    sign_in(c,'operator')
    assert post(c,f"/rework/{case['id']}/complete",version=str(case['version']),notes='Repaired',confirm='yes').status_code==302
    sign_in(c,'inspector');v=rows(app,'SELECT version FROM rework_cases')[0][0]
    assert post(c,f"/rework/{case['id']}/inspect",version=str(v),result='pass',notes='Repair verified').status_code==302
    assert rows(app,'SELECT state FROM serial_units WHERE id=?',(u,))[0][0]=='in_progress'
    assert stats(app,tid)['accepted']==0


def test_complete_order_closes_only_after_all_lot_departures(app):
    c,tid,units,ops=setup(app,size=50,total=80)
    for i,u in enumerate(units):
        accept(app,c,u['id'],ops);sign_in(c,'operator')
        assert move(app,c,tid,u['id'],'pack').status_code==302
        assert move(app,c,tid,u['id'],'depart').status_code==302
        assert stats(app,tid)['closed']==(i==len(units)-1)
    assert stats(app,tid)['departed']==80


def test_weighted_vendor_send_receive_and_inspection(app):
    from test_outside_processing import act,last
    c,plan=plan_setup(app);plan['steps'][0]['first_piece']=False
    plan['steps'].append(dict(name='Coat',instructions='Clear coating',criteria='Coverage',station_id=0,first_piece=False,outside=True))
    outside=dict(outside_required='yes',outside_vendor='Coater',outside_service='Clear coating',outside_instructions='Protect finish',outside_inspection_requirements='Check coverage',outside_due_date='2026-11-13')
    rid=receive(c,quantity='80',**payload(plan),**outside).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    result=convert(app,c,rid,tracking='lot',lot_size='50');tid=result.location.split('/')[-1]
    assert post(c,f'/tickets/{tid}/release',version=tv(app,tid),reason='Reviewed',plan_review='yes').status_code==302
    u=rows(app,'SELECT * FROM serial_units ORDER BY serial')[0]
    ops=rows(app,'SELECT * FROM operations ORDER BY sequence')
    sign_in(c,'operator')
    for op in ops[:-1]:assert complete(app,c,u['id'],op).status_code==302
    assert act(app,c,result.location,ops[-1],'send',serials=u['serial']).status_code==302
    assert rows(app,"SELECT quantity FROM outside_moves WHERE kind='send'")[0][0]==50
    assert action(app,c,u['id'],'split',quantity='10').status_code==409
    assert act(app,c,result.location,ops[-1],'receive',serials=u['serial'],shipment_id=last(app,'send')).status_code==302
    sign_in(c,'inspector')
    assert act(app,c,result.location,ops[-1],'inspect',receipt_id=last(app,'receive'),result='pass').status_code==302
    assert rows(app,'SELECT state FROM serial_units WHERE id=?',(u['id'],))[0][0]=='awaiting_inspection'
    assert stats(app,tid)['accepted']==0


def test_returned_lot_repair_preserves_fifty_piece_quantity(app):
    from test_returns import receive as receive_return,review
    from test_return_repairs import repair,repair_id
    c,tid,units,ops=setup(app);u=units[0]['id'];accept(app,c,u,ops)
    sign_in(c,'operator')
    assert move(app,c,tid,u,'pack').status_code==302
    assert move(app,c,tid,u,'depart').status_code==302
    departure=rows(app,"SELECT id FROM fulfillment_records WHERE kind='depart'")[0][0]
    assert receive_return(app,c,tid,departure,unit_id=[u]).status_code==302
    returned=rows(app,'SELECT id,quantity FROM customer_returns')[0]
    assert returned['quantity']==50
    sign_in(c,'planner');assert review(app,c,tid,returned['id']).status_code==302
    assert repair(app,c,tid,returned['id']).status_code==302
    rt=repair_id(app,returned['id'])
    child=rows(app,'SELECT unit_id,quantity FROM production_lots WHERE ticket_id=?',(rt,))[0]
    assert child['quantity']==50
    assert stats(app,rt)['remaining']==50


def test_database_preserves_lot_identity_and_quantity_bounds(app):
    c,tid,units,ops=setup(app);u=units[0]['id']
    with sqlite3.connect(app.config['DATABASE']) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE production_lots SET quantity=quantity+1 WHERE unit_id=?',(u,))
    with sqlite3.connect(app.config['DATABASE']) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM production_lots WHERE unit_id=?',(u,))
