import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, post, sign_in, rows
from test_returns import shipped, receive, review
from test_parts import add_part
from test_fulfillment import version, move
from test_delivery_corrections import reverse, last
from test_dispatch import assign,release
from test_serials import prepared_units,complete,inspect


def returned_job(app):
    c=app.test_client();sign_in(c,'planner');add_part(c)
    c,tid,d=shipped(app);receive(app,c,tid,d,quantity='2')
    rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    sign_in(c,'planner');assert review(app,c,tid,rid).status_code==302
    return c,tid,rid


def repair(app,c,tid,rid,**kw):
    data=dict(version=version(app,tid),workflow_version='0',confirm='yes',reason='Repair approved',instructions='Restore edges and verify drawing tolerances',route='Repair | Straighten edges')
    data.update(kw)
    return post(c,f'/tickets/{tid}/returns/{rid}/repair',**data)


def repair_id(app,rid):return rows(app,'SELECT repair_ticket_id FROM return_repairs WHERE return_id=?',(rid,))[0][0]


def close(app,c,tid,rid,**kw):
    rt=repair_id(app,rid)
    data=dict(version=version(app,tid),repair_version=version(app,rt),reason='Repaired goods delivered under SHIP-R1',confirm='yes')
    data.update(kw)
    return post(c,f'/tickets/{tid}/returns/{rid}/close',**data)


def plan(app,c,rt):
    sign_in(c,'admin');assert post(c,'/admin/stations',name='Repair bench',reason='Repair work').status_code==302
    station=rows(app,'SELECT MAX(id) FROM stations')[0][0]
    ops=rows(app,'SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence',(rt,))
    sign_in(c,'planner')
    assert release(app,c,'/tickets/'+rt).status_code==409
    for op in ops:assert assign(app,c,'/tickets/'+rt,op,station).status_code==302
    assert release(app,c,'/tickets/'+rt).status_code==302
    return ops


def finish_batch(app,c,rt):
    ops=plan(app,c,rt);sign_in(c,'operator')
    for op in ops:assert post(c,f'/tickets/{rt}/complete',version=version(app,rt),operation_id=op['id'],confirm='yes').status_code==302
    assert post(c,f'/tickets/{rt}/inspect',version=version(app,rt),result='pass',notes='Self').status_code==403
    sign_in(c,'inspector');assert post(c,f'/tickets/{rt}/inspect',version=version(app,rt),result='pass',notes='Independent repair inspection').status_code==302
    sign_in(c,'operator');assert move(app,c,rt,'pack',quantity='2').status_code==302
    assert move(app,c,rt,'depart',quantity='1').status_code==302


def test_batch_repair_end_to_end_closure_and_correction_reopening(app):
    from test_storage_management import report
    c,tid,rid=returned_job(app)
    original=rows(app,'SELECT * FROM inspections WHERE ticket_id=?',(tid,))
    assert repair(app,c,tid,rid,pn='WRONG',quantity='99').status_code==302
    rt=repair_id(app,rid)
    assert rows(app,'SELECT pn,quantity FROM tickets WHERE id=?',(rt,))[0][:]==('PN-001',2)
    assert rows(app,'SELECT part_revision_id FROM ticket_drawings WHERE ticket_id=?',(rt,))[0][0]==rows(app,'SELECT part_revision_id FROM ticket_drawings WHERE ticket_id=?',(tid,))[0][0]
    assert b'RETURN REPAIR' in c.get('/tickets/'+rt).data
    assert close(app,c,tid,rid).status_code==409
    finish_batch(app,c,rt)
    sign_in(c,'planner');assert close(app,c,tid,rid).status_code==409
    assert move(app,c,rt,'depart').status_code==302
    assert close(app,c,tid,rid).status_code==302
    assert close(app,c,tid,rid).status_code==409
    assert tid.encode() not in c.get('/returns').data
    assert tid.encode() in c.get('/returns?view=closed').data
    assert all(f['status']=='Eligible in preview' for f in report(app)['files'])
    assert rows(app,'SELECT * FROM inspections WHERE ticket_id=?',(tid,))==original
    assert reverse(app,c,rt,last(app)).status_code==302
    assert tid.encode() in c.get('/returns').data
    assert rows(app,'SELECT action FROM return_closure_events ORDER BY id')[-1][0]=='reopened'
    assert all('Protected' in f['status'] for f in report(app)['files'])
    assert move(app,c,rt,'depart').status_code==302
    assert close(app,c,tid,rid).status_code==302
    assert not rows(app,'PRAGMA foreign_key_check')


def test_repair_requires_planner_disposition_drawing_and_matching_policy(app):
    c,tid,rid=returned_job(app)
    for who in ('operator','inspector','viewer','admin'):
        sign_in(c,who);assert repair(app,c,tid,rid).status_code==403
    sign_in(c,'planner')
    for fields in (dict(confirm=''),dict(reason=''),dict(route='| no name'),dict(instructions='')):
        assert repair(app,c,tid,rid,**fields).status_code==400
    assert c.post(f'/tickets/{tid}/returns/{rid}/repair',data={}).status_code==400
    from test_workflow_defaults import save
    sign_in(c,'admin');assert save(c,require_intake='1').status_code==302
    sign_in(c,'planner')
    assert repair(app,c,tid,rid).status_code==409
    assert repair(app,c,tid,rid,workflow_version='1').status_code==409
    from test_intake import receive as intake,decision
    req=intake(c).location.split('/')[-1];assert decision(app,c,req).status_code==302
    assert repair(app,c,tid,rid,workflow_version='1',request_id=req).status_code==302
    rt=repair_id(app,rid)
    assert len(rows(app,'SELECT * FROM fulfillment_checks WHERE ticket_id=?',(rt,)))==2
    assert rows(app,'SELECT ticket_id FROM intake_requests WHERE id=?',(req,))[0][0]==rt


def test_creation_races_and_audit_failure_leave_no_orphan_repair(app):
    c,tid,rid=returned_job(app);v=version(app,tid);count=len(rows(app,'SELECT * FROM tickets'))
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_repair BEFORE INSERT ON events WHEN NEW.action='return_repair_created' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):repair(app,c,tid,rid)
    assert not rows(app,'SELECT * FROM return_repairs')
    assert len(rows(app,'SELECT * FROM tickets'))==count
    assert version(app,tid)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_repair')
    c2=app.test_client();sign_in(c2,'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda client:repair(app,client,tid,rid,version=v).status_code,[c,c2]))
    assert sorted(results)==[302,409]
    assert repair(app,c,tid,rid).status_code==409
    sign_in(c,'admin');rt=repair_id(app,rid)
    assert post(c,f'/tickets/{rt}/cancel',version=version(app,rt),reason='Cancel',confirm='yes').status_code==409


def test_serial_repair_creates_new_travelers_for_exact_returned_units(app):
    c,tid,units,ops=prepared_units(app)
    sign_in(c,'planner');add_part(c)
    for unit in units:
        sign_in(c,'operator')
        for n,op in enumerate(ops,1):assert complete(c,unit,op,n).status_code==302
        sign_in(c,'inspector');assert inspect(c,unit,result='pass').status_code==302
    sign_in(c,'operator')
    move(app,c,tid,'pack',unit_id=[u['id'] for u in units]);move(app,c,tid,'depart',unit_id=[u['id'] for u in units])
    receive(app,c,tid,last(app),unit_id=[units[0]['id']]);rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    sign_in(c,'planner');review(app,c,tid,rid);assert repair(app,c,tid,rid,serials='WRONG',quantity='99').status_code==302
    rt=repair_id(app,rid);new=rows(app,'SELECT * FROM serial_units WHERE ticket_id=?',(rt,))[0]
    assert new['serial']=='S1' and new['id']!=units[0]['id']
    assert rows(app,'SELECT original_unit_id,repair_unit_id FROM return_repair_units')[0][:]==(units[0]['id'],new['id'])
    repair_ops=plan(app,c,rt)
    sign_in(c,'operator')
    for op in repair_ops:
        fresh=rows(app,'SELECT * FROM serial_units WHERE id=?',(new['id'],))[0]
        assert complete(c,new,op,fresh['version']).status_code==302
    uv=rows(app,'SELECT version FROM serial_units WHERE id=?',(new['id'],))[0][0]
    assert inspect(c,new,version=uv).status_code==403
    sign_in(c,'inspector');assert inspect(c,new,version=uv).status_code==302
    sign_in(c,'operator');assert move(app,c,rt,'pack',unit_id=[new['id']]).status_code==302
    assert move(app,c,rt,'depart',unit_id=[new['id']]).status_code==302
    sign_in(c,'planner');assert close(app,c,tid,rid).status_code==302
    assert len(rows(app,"SELECT * FROM unit_event_links l JOIN events e ON e.id=l.event_id WHERE e.action='unit_return_closed'"))==2
    assert rows(app,'SELECT state FROM serial_units WHERE id=?',(units[0]['id'],))[0][0]=='accepted'


def test_closed_return_audit_failure_and_cross_tenant_guards(app):
    c,tid,rid=returned_job(app);repair(app,c,tid,rid);rt=repair_id(app,rid)
    finish_batch(app,c,rt);move(app,c,rt,'depart');sign_in(c,'admin')
    v=version(app,tid)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_close BEFORE INSERT ON events WHEN NEW.action='return_closed' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):close(app,c,tid,rid)
    assert not rows(app,'SELECT * FROM return_closure_events')
    assert version(app,tid)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_close')
    assert close(app,c,tid,rid,repair_version='0').status_code==409
    assert close(app,c,tid,rid,reason='').status_code==400
    assert close(app,c,tid,rid).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ('DELETE FROM return_repairs','UPDATE return_repairs SET reason=\'bad\'','DELETE FROM return_closure_events'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    runner=app.test_cli_runner();runner.invoke(args=['add-tenant','beta','--name','Beta']);runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner'])
    sign_in(c,'planner','beta')
    assert repair(app,c,tid,rid).status_code==404
    assert close(app,c,tid,rid).status_code==404
    assert rt.encode() not in c.get('/returns?view=closed').data


def test_closure_and_departure_reversal_race_and_reopen_rollback(app):
    c,tid,rid=returned_job(app);repair(app,c,tid,rid);rt=repair_id(app,rid)
    finish_batch(app,c,rt);move(app,c,rt,'depart');departure=last(app)
    sign_in(c,'planner');c2=app.test_client();sign_in(c2,'admin')
    v=version(app,tid);rv=version(app,rt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(close,app,c,tid,rid,version=v,repair_version=rv)
        b=pool.submit(reverse,app,c2,rt,departure,version=rv)
        assert sorted([a.result().status_code,b.result().status_code])==[302,409]
    if rows(app,'SELECT * FROM fulfillment_reversals WHERE record_id=?',(departure,)):
        move(app,c,rt,'depart');departure=last(app);assert close(app,c,tid,rid).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_reopen BEFORE INSERT ON events WHEN NEW.action='return_reopened' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):reverse(app,c,rt,departure)
    assert not rows(app,'SELECT * FROM fulfillment_reversals WHERE record_id=?',(departure,))
    assert rows(app,'SELECT action FROM return_closure_events ORDER BY id')[-1][0]=='closed'
