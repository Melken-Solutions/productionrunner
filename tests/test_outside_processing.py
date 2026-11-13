import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, sign_in, post, rows, create_ticket
from test_dispatch import version, release, assign, board


def setup(app, serial=False, route='Coat | Powder coat\nPack | Package'):
    c=app.test_client();sign_in(c,'admin')
    assert post(c,'/admin/stations',name='Inspection and dispatch',reason='Final-stage fixture station').status_code==302
    sign_in(c,'planner')
    path=create_ticket(c,legacy=False,route=route,**({'tracking':'serial','serials':'S1\nS2'} if serial else {})).location
    ops=rows(app,'SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence',(path.split('/')[-1],))
    return c,path,ops


def plan(app,c,path,op,**kw):
    data=dict(version=version(app,path),vendor='Coating Shop',service='Powder coat',instructions='Use approved drawing',inspection_requirements='Verify thickness and finish',due_date='2026-01-01',reference='PO-12')
    data.update(kw)
    return post(c,path+f"/outside/{op['id']}/plan",**data)


def act(app,c,path,op,action,**kw):
    data=dict(version=version(app,path),quantity='1',notes='Verified physical evidence')
    data.update(kw)
    return post(c,path+f"/outside/{op['id']}/{action}",**data)


def ready(app,serial=False,route='Coat | Powder coat'):
    c,path,ops=setup(app,serial,route)
    for op in ops: assert plan(app,c,path,op).status_code==302
    assert release(app,c,path).status_code==302
    return c,path,ops


def last(app,kind):return str(rows(app,'SELECT id FROM outside_moves WHERE kind=? ORDER BY id DESC',(kind,))[0][0])


def test_planner_setup_release_and_ui(app):
    c,path,ops=setup(app)
    sign_in(c,'operator');assert plan(app,c,path,ops[0]).status_code==403
    sign_in(c,'planner');assert plan(app,c,path,ops[0],due_date='bad').status_code==400
    assert plan(app,c,path,ops[0]).status_code==302
    assert release(app,c,path).status_code==409
    assert plan(app,c,path,ops[1]).status_code==302
    assert release(app,c,path).status_code==302
    assert plan(app,c,path,ops[0]).status_code==409
    for url in [path,path+'/outside',path+'/assignments','/']:
        assert c.get(url).status_code==200
    assert b'Coating Shop' in c.get(path+'/outside').data
    sign_in(c,'operator')
    assert c.get('/shop-floor/work/'+path.split('/')[-1]).status_code==200


def test_batch_partial_returns_inspection_and_delivery_separation(app):
    c,path,ops=ready(app);op=ops[0]
    assert act(app,c,path,op,'send',quantity='2',notes='UPS TRACK-1').status_code==302
    sid=last(app,'send')
    assert rows(app,'SELECT * FROM fulfillment_records')==[]
    assert 'At outside vendor' in board(app)[0]['operation']
    assert 'OVERDUE' in board(app)[0]['operation']
    sign_in(c,'operator')
    assert post(c,path+'/complete',version=version(app,path),operation_id=op['id'],confirm='yes').status_code==409
    assert post(c,'/work/'+path.split('/')[-1],version=version(app,path),action='start',mode='production').status_code==409
    assert act(app,c,path,op,'receive',shipment_id=sid).status_code==302
    rid=last(app,'receive')
    assert act(app,c,path,op,'inspect',receipt_id=rid,result='pass').status_code==403
    sign_in(c,'inspector');assert act(app,c,path,op,'inspect',receipt_id=rid,result='fail').status_code==302
    assert rows(app,'SELECT completed_by FROM operations')[0][0] is None
    assert act(app,c,path,op,'inspect',receipt_id=rid,result='pass',notes='Reinspection: verified finish against criteria').status_code==302
    assert rows(app,'SELECT state FROM tickets')[0][0]=='in_progress'
    assert act(app,c,path,op,'inspect',receipt_id=rid,result='pass').status_code==409
    sign_in(c,'operator');assert act(app,c,path,op,'receive',shipment_id=sid,quantity='2').status_code==409
    assert act(app,c,path,op,'receive',shipment_id=sid).status_code==302
    sign_in(c,'inspector');assert act(app,c,path,op,'inspect',receipt_id=last(app,'receive'),result='pass').status_code==302
    assert rows(app,'SELECT state FROM tickets')[0][0]=='awaiting_inspection'
    assert len(rows(app,'SELECT * FROM outside_inspections'))==3
    assert rows(app,'SELECT * FROM fulfillment_records')==[]
    assert post(c,path+'/inspect',version=version(app,path),result='pass',notes='Final inspection').status_code==302
    assert rows(app,'SELECT state FROM tickets')[0][0]=='accepted'


def test_serial_partial_progress_exact_custody_and_route_order(app):
    c,path,ops=ready(app,True,'Coat | Coat\nWeld | Weld')
    assert act(app,c,path,ops[1],'send',serials='S1').status_code==409
    assert act(app,c,path,ops[0],'send',serials='S1\nS2').status_code==302
    sid=last(app,'send')
    assert act(app,c,path,ops[0],'send',serials='S1').status_code==409
    assert act(app,c,path,ops[0],'receive',shipment_id=sid,serials='UNKNOWN').status_code==400
    assert act(app,c,path,ops[0],'receive',shipment_id=sid,serials='S1').status_code==302
    rid=last(app,'receive')
    assert act(app,c,path,ops[0],'receive',shipment_id=sid,serials='S1').status_code==409
    sign_in(c,'inspector');assert act(app,c,path,ops[0],'inspect',receipt_id=rid,result='pass').status_code==302
    assert len(rows(app,'SELECT * FROM unit_completions'))==1
    sign_in(c,'planner');assert act(app,c,path,ops[1],'send',serials='S1').status_code==302
    assert act(app,c,path,ops[1],'send',serials='S2').status_code==409
    u=rows(app,"SELECT * FROM serial_units WHERE serial='S2'")[0]
    sign_in(c,'operator');assert post(c,'/units/'+u['id']+'/complete',version=u['version'],operation_id=ops[0]['id'],confirm='yes').status_code==409
    assert len(rows(app,'SELECT * FROM unit_event_links'))>=4


def test_outside_permissions_stale_csrf_and_tenant_boundary(app):
    c,path,ops=ready(app);op=ops[0]
    assert c.post(path+f"/outside/{op['id']}/send",data={'quantity':'1'}).status_code==400
    stale=version(app,path)
    assert act(app,c,path,op,'send').status_code==302
    assert act(app,c,path,op,'send',version=stale).status_code==409
    sign_in(c,'viewer');assert act(app,c,path,op,'send').status_code==403
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        uid=conn.execute("SELECT id FROM users WHERE username='planner'").fetchone()[0]
        conn.execute("INSERT INTO memberships VALUES(2,?,'planner')",(uid,))
    sign_in(c,'planner','beta')
    assert c.get(path+'/outside').status_code==404
    assert act(app,c,path,op,'send',version='1').status_code==404


def test_concurrent_send_and_immutable_evidence(app):
    c,path,ops=ready(app);op=ops[0];v=version(app,path)
    clients=[app.test_client(),app.test_client()]
    for client in clients:sign_in(client,'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda client:act(app,client,path,op,'send',quantity='2',version=v),clients))
    assert sorted(r.status_code for r in responses)==[302,409]
    assert len(rows(app,'SELECT * FROM outside_moves'))==1
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for query in ['DELETE FROM outside_moves',"UPDATE outside_moves SET quantity=1", "UPDATE outside_steps SET vendor='Changed'"]:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(query)
    sign_in(c,'admin');assert post(c,path+'/cancel',version=version(app,path),confirm='yes',reason='Test').status_code==409


def test_audit_failure_rolls_back_custody(app):
    c,path,ops=ready(app);op=ops[0];v=version(app,path)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_outside BEFORE INSERT ON events WHEN NEW.action='outside_send' BEGIN SELECT RAISE(ABORT,'Test audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError):act(app,c,path,op,'send')
    assert rows(app,'SELECT * FROM outside_moves')==[]
    assert version(app,path)==v


def test_internal_previous_step_blocks_send_and_producer_inspection(app):
    c,path,ops=setup(app,route='Cut | Cut\nCoat | Coat')
    assert plan(app,c,path,ops[1]).status_code==302
    sign_in(c,'admin');assert post(c,'/admin/stations',name='Laser',reason='Setup').status_code==302
    sign_in(c,'planner');assert assign(app,c,path,ops[0],1).status_code==302
    assert release(app,c,path).status_code==302
    assert act(app,c,path,ops[1],'send',quantity='2').status_code==409
    sign_in(c,'operator');assert post(c,path+'/complete',version=version(app,path),operation_id=ops[0]['id'],confirm='yes').status_code==302
    sign_in(c,'planner');assert act(app,c,path,ops[1],'send',quantity='2').status_code==302
    assert act(app,c,path,ops[1],'receive',shipment_id=last(app,'send'),quantity='2').status_code==302
    sign_in(c,'operator');assert act(app,c,path,ops[1],'inspect',receipt_id=last(app,'receive'),result='pass').status_code==403


def test_inspection_audit_failure_atomic_and_serial_final_state(app):
    c,path,ops=ready(app,True);op=ops[0]
    assert act(app,c,path,op,'send',serials='S1\nS2').status_code==302
    assert act(app,c,path,op,'receive',shipment_id=last(app,'send'),serials='S1\nS2').status_code==302
    rid=last(app,'receive');v=version(app,path)
    sign_in(c,'inspector')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_inspection BEFORE INSERT ON events WHEN NEW.action='outside_inspection_pass' BEGIN SELECT RAISE(ABORT,'Test audit failure'); END")
    with pytest.raises(sqlite3.IntegrityError):act(app,c,path,op,'inspect',receipt_id=rid,result='pass')
    assert rows(app,'SELECT * FROM outside_inspections')==[]
    assert rows(app,'SELECT * FROM unit_completions')==[]
    assert version(app,path)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER reject_inspection')
    assert act(app,c,path,op,'inspect',receipt_id=rid,result='pass').status_code==302
    assert {u['state'] for u in rows(app,'SELECT * FROM serial_units')}=={'awaiting_inspection'}
    assert rows(app,'SELECT state FROM tickets')[0][0]=='awaiting_inspection'
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM outside_inspections')
    for u in rows(app,'SELECT * FROM serial_units'):
        assert post(c,'/units/'+u['id']+'/inspect',version=u['version'],result='pass',notes='Final inspection').status_code==302
    assert rows(app,'SELECT state FROM tickets')[0][0]=='accepted'


def test_planner_can_revert_before_release(app):
    c,path,ops=setup(app);op=ops[0]
    assert plan(app,c,path,op).status_code==302
    assert plan(app,c,path,op,mode='internal').status_code==302
    assert rows(app,'SELECT * FROM outside_steps')==[]
