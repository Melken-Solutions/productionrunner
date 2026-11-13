import json
import re
import sqlite3
from test_workflow import app,post,rows,sign_in
from test_lots import setup


def repeat(c,tid,**kw):
    page=c.get('/tickets/'+tid+'/repeat')
    assert page.status_code==200
    token=re.search(rb'name="repeat_token" value="([^"]+)"',page.data).group(1).decode()
    values=dict(repeat_token=token,quantity='7',required_date='2026-12-01',confirm='yes')|kw
    return post(c,'/tickets/'+tid+'/repeat',**values),values


def test_repeat_creates_fresh_draft_and_retains_original(app):
    c,tid,units,ops=setup(app,total=100)
    before=[dict(r) for r in rows(app,'SELECT * FROM tickets')]
    result,values=repeat(c,tid)
    assert result.status_code==302
    rid=result.location.split('/')[-2]
    req=rows(app,'SELECT * FROM intake_requests WHERE id=?',(rid,))[0]
    assert req['quantity']==7 and req['required_date']=='2026-12-01'
    assert req['status']=='preparing' and req['approved_by'] is None and req['ticket_id'] is None
    assert req['source_reference']==''
    assert [dict(r) for r in rows(app,'SELECT * FROM tickets')]==before
    assert len(rows(app,'SELECT * FROM serial_units'))==len(units)
    plan=json.loads(rows(app,'SELECT data FROM request_job_plans WHERE request_id=?',(rid,))[0][0])
    assert [s['name'] for s in plan['steps']]==[o['name'] for o in ops]
    assert 'template_id' not in plan
    assert b'Repeated from' in c.get(result.location).data
    assert b'Run this job again' in c.get('/tickets/'+tid,follow_redirects=True).data
    again=post(c,'/tickets/'+tid+'/repeat',**values)
    assert again.location==result.location
    assert len(rows(app,"SELECT * FROM intake_events WHERE action='repeated_from_job'"))==1
    from test_intake import decision,convert
    assert decision(app,c,rid).status_code==302
    new=convert(app,c,rid)
    assert new.status_code==302 and new.location!='/tickets/'+tid
    assert b'Repeated from' in c.get(new.location,follow_redirects=True).data


def test_repeat_stale_scope_validation_and_permissions(app):
    c,tid,units,ops=setup(app,total=100)
    for values in [dict(quantity='0'),dict(quantity='10001'),dict(required_date='bad'),dict(confirm='')]:
        assert repeat(c,tid,**values)[0].status_code==400
    page=c.get('/tickets/'+tid+'/repeat')
    token=re.search(rb'name="repeat_token" value="([^"]+)"',page.data).group(1).decode()
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('UPDATE tickets SET version=version+1 WHERE id=?',(tid,))
    assert post(c,'/tickets/'+tid+'/repeat',repeat_token=token,quantity='1',confirm='yes').status_code==409
    assert post(c,'/tickets/'+tid+'/repeat',repeat_token='bad',quantity='1',confirm='yes').status_code==400
    assert c.post('/tickets/'+tid+'/repeat',data={}).status_code==400
    for name in ['operator','viewer','inspector']:
        sign_in(c,name);assert c.get('/tickets/'+tid+'/repeat').status_code==403
    sign_in(c,'planner')
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code==0
    sign_in(c,'planner',tenant='beta')
    assert c.get('/tickets/'+tid+'/repeat').status_code==404
    assert post(c,'/tickets/'+tid+'/repeat',repeat_token=token,quantity='1',confirm='yes').status_code==403


def test_repeat_inactive_station_becomes_review_warning(app):
    c,tid,units,ops=setup(app,total=100)
    sid=rows(app,'SELECT station_id FROM operation_assignments WHERE operation_id=?',(ops[0]['id'],))[0][0]
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('UPDATE stations SET active=0 WHERE id=?',(sid,))
    result,_=repeat(c,tid)
    assert result.status_code==302
    assert b'station is inactive' in c.get(result.location).data
    rid=result.location.split('/')[-2]
    plan=json.loads(rows(app,'SELECT data FROM request_job_plans WHERE request_id=?',(rid,))[0][0])
    assert plan['steps'][0]['station_id']==0


def test_repeat_new_erp_references_and_outside_dates(app):
    from test_request_job_plan import setup as plan_setup,payload
    from test_erp import configure
    from test_intake import receive,decision,convert
    c,plan=plan_setup(app);path=configure(app,c)
    plan['steps'].append(dict(name='Clear coat',instructions='Coat per drawing',criteria='Verify finish',station_id=0,outside=True,first_piece=False))
    outside=dict(outside_required='yes',outside_vendor='Finisher',outside_service='Clear coat',outside_instructions='Protect threads',outside_inspection_requirements='Check coating',outside_due_date='2026-12-01',outside_reference='OLD-VENDOR-PO')
    rid=receive(c,source='erp',source_reference='OLD-REF',erp_present='yes',erp_path=path['id'],erp_production_order='OLD-ORDER',**payload(plan),**outside).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    tid=convert(app,c,rid).location.split('/')[-1]
    result,_=repeat(c,tid,erp_present='yes',erp_path=path['id'],erp_production_order='NEW-ORDER')
    assert result.status_code==302
    new=result.location.split('/')[-2]
    from productionrunner.database import connect
    from productionrunner.erp import saved
    with connect(app.config['DATABASE']) as conn:assert saved(conn,1,new)['values']['production_order']=='NEW-ORDER'
    copied=rows(app,'SELECT * FROM request_outside WHERE request_id=?',(new,))[0]
    assert copied['instructions']=='Protect threads' and copied['due_date']==copied['reference']==''
    assert decision(app,c,new).status_code==409
    assert rows(app,'SELECT status FROM intake_requests WHERE id=?',(new,))[0][0]=='preparing'


def test_repeat_audit_failure_rolls_back_draft(app):
    import pytest
    c,tid,units,ops=setup(app,total=100)
    original=len(rows(app,'SELECT * FROM intake_requests'))
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_repeat BEFORE INSERT ON intake_events WHEN NEW.action='repeated_from_job' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):repeat(c,tid)
    assert len(rows(app,'SELECT * FROM intake_requests'))==original
