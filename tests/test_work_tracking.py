import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import pytest
from test_workflow import app,post,sign_in,rows
from test_dispatch import setup_job,assign,release
from test_fulfillment import version


def setup(app,serial=False):
    c,path,ops=setup_job(app,serial=serial)
    for op in ops:assert assign(app,c,path,op,1).status_code==302
    assert release(app,c,path).status_code==302
    tid=path.split('/')[-1]
    app.config['WORK_CLOCK']=lambda:datetime(2030,1,1,12,tzinfo=timezone.utc).isoformat()
    sign_in(c,'operator')
    code=rows(app,'SELECT id FROM serial_units WHERE ticket_id=? ORDER BY serial',(tid,))[0][0] if serial else tid
    return c,tid,code,ops


def advance(app,minutes):
    stamp=datetime.fromisoformat(app.config['WORK_CLOCK']())+timedelta(minutes=minutes)
    app.config['WORK_CLOCK']=lambda:stamp.isoformat()


def v(app,code):
    return str(rows(app,'SELECT version FROM '+('serial_units' if code.startswith('JR-U-') else 'tickets')+' WHERE id=?',(code,))[0][0])


def act(app,c,code,action,**kw):
    latest=rows(app,'SELECT MAX(id) FROM work_events')
    data=dict(action=action,version=v(app,code),session_version=str(latest[0][0] or ''),mode='production',reason='Machine issue')
    data.update(kw)
    return post(c,'/work/'+code,**data)


def finish(app,c,code,op,**kw):
    unit=code.startswith('JR-U-')
    data=dict(version=v(app,code),operation_id=str(op['id']),confirm='yes',completed_quantity='1' if unit else '2')
    data.update(kw)
    return post(c,('/units/' if unit else '/tickets/')+code+'/complete',**data)


def intervals(app):
    from productionrunner.work_tracking import intervals
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory=sqlite3.Row
        return intervals(conn,1,app.config['WORK_CLOCK']())


def test_start_pause_resume_stop_finish_reports_and_corrections(app):
    c,tid,code,ops=setup(app)
    assert act(app,c,code,'start').status_code==302
    advance(app,10);assert act(app,c,code,'pause').status_code==302
    assert finish(app,c,code,ops[0]).status_code==409
    advance(app,5);assert act(app,c,code,'resume').status_code==302
    advance(app,20);assert act(app,c,code,'stop',reason='Shift end').status_code==302
    assert finish(app,c,code,ops[0]).status_code==409
    assert act(app,c,code,'start',mode='setup').status_code==302
    advance(app,3);assert finish(app,c,code,ops[0],completed_quantity='1').status_code==400
    assert finish(app,c,code,ops[0]).status_code==302
    assert [int(r['seconds']/60) for r in intervals(app)]==[10,5,20,3]
    sign_in(c,'admin')
    page=c.get('/admin/reports/work?from=2030-01-01&to=2030-01-01')
    assert page.status_code==200 and b'30.00 minutes' in page.data and b'5.00 minutes' in page.data
    export=c.get('/admin/reports/work?from=2030-01-01&to=2030-01-01&format=csv')
    assert export.status_code==200 and b'Machine issue' in export.data and b'production' in export.data
    start=rows(app,"SELECT * FROM work_events WHERE action='start' ORDER BY id")[0]
    assert post(c,'/admin/work/sessions/1',action='correct',event_id=start['id'],version='0',effective_at='2030-01-01T12:02:00+00:00',reason='Missed initial setup record').status_code==302
    assert int(intervals(app)[0]['seconds']/60)==8
    assert rows(app,'SELECT created_at FROM work_events WHERE id=?',(start['id'],))[0][0]==start['created_at']
    assert post(c,'/admin/work/sessions/1',action='correct',event_id=start['id'],version='0',effective_at='2030-01-01T12:03:00+00:00',reason='Stale').status_code==409
    assert post(c,'/admin/work/sessions/1',action='correct',event_id=start['id'],version='1',effective_at='2030-01-01T12:20:00+00:00',reason='Out of sequence').status_code==409
    assert len(rows(app,'SELECT * FROM work_time_corrections'))==1


def test_coverage_preserves_all_contributors_for_inspection(app):
    c,tid,code,ops=setup(app)
    assert act(app,c,code,'start').status_code==302
    advance(app,10);act(app,c,code,'pause')
    runner=app.test_cli_runner();assert runner.invoke(args=['set-membership','planner','--tenant','alpha','--roles','planner,operator']).exit_code==0
    sign_in(c,'planner');advance(app,5);assert act(app,c,code,'resume').status_code==302
    advance(app,10);assert finish(app,c,code,ops[0]).status_code==302
    assert act(app,c,code,'start').status_code==302
    advance(app,5);assert finish(app,c,code,ops[1]).status_code==302
    sign_in(c,'operator')
    assert post(c,f'/tickets/{tid}/inspect',version=version(app,tid),result='pass',notes='I did not finish but worked earlier').status_code==403
    sign_in(c,'inspector');assert post(c,f'/tickets/{tid}/inspect',version=version(app,tid),result='pass',notes='Independent').status_code==302
    assert {r['username'] for r in intervals(app) if r['category']=='production'}=={'operator','planner'}


def test_serial_timing_and_finish_preserves_sibling_state(app):
    c,tid,code,ops=setup(app,serial=True)
    assert act(app,c,code,'start').status_code==302
    advance(app,1);assert finish(app,c,code,ops[0]).status_code==302
    assert act(app,c,code,'start').status_code==302
    advance(app,1);assert finish(app,c,code,ops[1]).status_code==302
    assert post(c,f'/units/{code}/inspect',version=v(app,code),result='pass',notes='Self').status_code==403
    sign_in(c,'inspector');assert post(c,f'/units/{code}/inspect',version=v(app,code),result='pass',notes='Independent').status_code==302
    assert rows(app,'SELECT state FROM serial_units WHERE ticket_id=? AND id!=?',(tid,code))[0][0]=='ready'
    assert len(rows(app,"SELECT * FROM unit_event_links l JOIN events e ON e.id=l.event_id WHERE e.action='work_start'"))==2


def test_station_reservation_and_operator_overlap_allow_unattended(app):
    c,tid,code,ops=setup(app)
    # A second ticket assigned to station 2.
    from test_workflow import create_ticket
    sign_in(c,'planner');path=create_ticket(c,legacy=False).location;tid2=path.split('/')[-1]
    ops2=rows(app,'SELECT * FROM operations WHERE ticket_id=?',(tid2,))
    for op in ops2:assign(app,c,path,op,2)
    release(app,c,path);sign_in(c,'operator')
    act(app,c,code,'start');assert act(app,c,tid2,'start').status_code==409
    assert act(app,c,tid2,'start',mode='machine').status_code==302
    advance(app,5);assert act(app,c,code,'pause',session_version='1').status_code==302
    sign_in(c,'planner');assert assign(app,c,'/tickets/'+tid,ops[0],2).status_code==409
    sign_in(c,'operator');assert act(app,c,code,'resume').status_code==302
    advance(app,5)
    assert sum(r['seconds'] for r in intervals(app) if r['category']=='production')==600
    assert sum(r['seconds'] for r in intervals(app) if r['category']=='machine')==600


def test_required_policy_reasons_permissions_tenant_and_admin_stop(app):
    c,tid,code,ops=setup(app);sign_in(c,'admin')
    assert post(c,'/admin/work',action='settings',version='0',required='1',reason='Start measuring').status_code==302
    assert post(c,'/admin/work',action='reason',name='Waiting for crane',reason='Shop reason').status_code==302
    sign_in(c,'operator');assert finish(app,c,code,ops[0]).status_code==409
    assert c.get('/admin/reports/work').status_code==403
    assert c.post('/work/'+code,data={}).status_code==400
    assert act(app,c,code,'start').status_code==302
    assert act(app,c,code,'pause',reason='unlisted').status_code==400
    advance(app,5);assert act(app,c,code,'pause',reason='Waiting for crane').status_code==302
    sign_in(c,'admin');latest=rows(app,'SELECT MAX(id) FROM work_events')[0][0]
    assert post(c,'/admin/work/sessions/1',action='stop',version=str(latest),reason='Operator left shift').status_code==302
    runner=app.test_cli_runner();runner.invoke(args=['add-tenant','beta','--name','Beta']);runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin,operator'])
    sign_in(c,'admin','beta')
    assert c.get('/work/'+code).status_code==404
    assert c.get('/admin/work/sessions/1').status_code==404
    assert tid.encode() not in c.get('/admin/reports/work?from=2030-01-01&to=2030-01-01').data


def test_concurrency_audit_rollback_and_immutable_time_evidence(app):
    c,tid,code,ops=setup(app);c2=app.test_client();sign_in(c2,'operator');version_before=v(app,code)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_start BEFORE INSERT ON events WHEN NEW.action='work_start' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):act(app,c,code,'start')
    assert not rows(app,'SELECT * FROM work_sessions')
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_start')
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda client:act(app,client,code,'start',version=version_before).status_code,[c,c2]))
    assert sorted(result)==[302,409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_finish BEFORE INSERT ON events WHEN NEW.action='operation_completed' BEGIN SELECT RAISE(ABORT,'test'); END")
    advance(app,5)
    with pytest.raises(sqlite3.IntegrityError):finish(app,c,code,ops[0])
    assert rows(app,'SELECT action FROM work_events ORDER BY id')[-1][0]=='start'
    assert rows(app,'SELECT completed_by FROM operations WHERE id=?',(ops[0]['id'],))[0][0] is None
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ('DELETE FROM work_events','UPDATE work_events SET action=\'stop\'','DELETE FROM work_sessions'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)


def test_date_clipping_and_correction_overlap_rejection(app):
    c,tid,code,ops=setup(app)
    act(app,c,code,'start');advance(app,10);act(app,c,code,'stop')
    advance(app,10);act(app,c,code,'start');advance(app,10);act(app,c,code,'stop')
    sign_in(c,'admin');start=rows(app,"SELECT * FROM work_events WHERE action='start' ORDER BY id DESC")[0]
    assert post(c,'/admin/work/sessions/2',action='correct',event_id=start['id'],version='0',effective_at='2030-01-01T12:05:00+00:00',reason='Overlapping').status_code==409
    assert not rows(app,'SELECT * FROM work_time_corrections')
    assert b'20.00 minutes' in c.get('/admin/reports/work?from=2030-01-01&to=2030-01-01').data
    assert b'No recorded intervals' in c.get('/admin/reports/work?from=2030-01-02&to=2030-01-02').data


def test_midnight_clipping_live_intervals_and_csv_safety(app):
    c,tid,code,ops=setup(app)
    app.config['WORK_CLOCK']=lambda:'2030-01-01T23:50:00+00:00'
    sign_in(c,'admin');assert post(c,'/admin/work',action='reason',name='=unsafe spreadsheet formula',reason='Test safe export').status_code==302
    sign_in(c,'operator');act(app,c,code,'start');advance(app,20)
    assert act(app,c,code,'pause',reason='=unsafe spreadsheet formula').status_code==302
    advance(app,5);sign_in(c,'admin')
    assert b'10.00 minutes' in c.get('/admin/reports/work?from=2030-01-01&to=2030-01-01').data
    page=c.get('/admin/reports/work?from=2030-01-02&to=2030-01-02')
    assert b'10.00 minutes' in page.data and b'5.00 minutes' in page.data and b'live' in page.data
    assert b"'=unsafe" in c.get('/admin/reports/work?from=2030-01-02&to=2030-01-02&format=csv').data
    assert c.get('/admin/reports/work?from=bad').status_code==400
    assert c.get('/admin/reports/work?from=2030-01-02&to=2030-01-01').status_code==400


def test_time_correction_audit_failure_rolls_back_and_cancel_requires_stop(app):
    c,tid,code,ops=setup(app);act(app,c,code,'start');advance(app,10)
    sign_in(c,'admin');assert post(c,f'/tickets/{tid}/cancel',version=version(app,tid),reason='Cancelled',confirm='yes').status_code==409
    assert post(c,'/admin/work/sessions/1',action='stop',version='1',reason='Abandoned timer').status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_time BEFORE INSERT ON events WHEN NEW.action='work_time_corrected' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):post(c,'/admin/work/sessions/1',action='correct',event_id='1',version='0',effective_at='2030-01-01T12:02:00+00:00',reason='Missed button')
    assert not rows(app,'SELECT * FROM work_time_corrections')
    assert intervals(app)[0]['seconds']==600


def test_different_operator_station_start_race(app):
    c,tid,code,ops=setup(app)
    runner=app.test_cli_runner();runner.invoke(args=['set-membership','planner','--tenant','alpha','--roles','planner,operator'])
    from test_workflow import create_ticket
    c2=app.test_client();sign_in(c2,'planner');path=create_ticket(c2,legacy=False).location;tid2=path.split('/')[-1]
    for op in rows(app,'SELECT * FROM operations WHERE ticket_id=?',(tid2,)):assign(app,c2,path,op,1)
    release(app,c2,path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(act,app,c,code,'start')
        b=pool.submit(act,app,c2,tid2,'start')
        assert sorted([a.result().status_code,b.result().status_code])==[302,409]
    assert len(rows(app,'SELECT * FROM work_sessions'))==1
