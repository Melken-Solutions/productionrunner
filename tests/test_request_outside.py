import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows,create_ticket
from test_intake import receive,decision,rv,FIELDS
from test_parts import add_part

PLAN=dict(outside_required='yes',outside_vendor='Finish Shop',outside_service='Powder coat',outside_instructions='Satin black per drawing',outside_inspection_requirements='Verify thickness and coverage',outside_due_date='2026-12-01',outside_reference='PO-100')


def prepared(app):
    c=app.test_client();sign_in(c,'planner');add_part(c)
    response=receive(c,**PLAN);assert response.status_code==302
    rid=response.location.split('/')[-1]
    return c,rid


def test_request_plan_carries_to_exact_routing_step(app):
    c,rid=prepared(app)
    assert b'Outside processing' in c.get('/requests/new').data
    assert b'Finish Shop' in c.get('/requests/'+rid).data
    assert b'Finish Shop' in c.get('/requests/'+rid+'/edit').data
    assert decision(app,c,rid).status_code==302
    assert b'Outside processing from request' in c.get('/tickets/new',query_string={'request_id':rid}).data
    for seq in ('','bad','0','3'):
        assert create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence=seq).status_code==400
        assert not rows(app,'SELECT * FROM tickets')
        assert not rows(app,'SELECT * FROM outside_steps')
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='2',route='Cut | Cut\nPowder coat | Finish',legacy=False)
    assert response.status_code==302
    plan=rows(app,'SELECT x.*,o.sequence FROM outside_steps x JOIN operations o ON o.id=x.operation_id')[0]
    assert (plan['vendor'],plan['sequence'],plan['reference'])==('Finish Shop',2,'PO-100')
    assert b'Outside processing' in c.get(response.location).data
    assert (response.location+'/outside').encode() in c.get('/').data
    assert not rows(app,'PRAGMA foreign_key_check')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM request_outside')


def test_request_plan_change_invalidates_approval_and_stale_conversion(app):
    c,rid=prepared(app);assert decision(app,c,rid).status_code==302
    old=rv(app,rid)
    assert post(c,'/requests/'+rid+'/edit',**(FIELDS|PLAN|dict(version=old,reason='Vendor changed',outside_vendor='Second Shop'))).status_code==302
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
    assert rows(app,'SELECT vendor FROM request_outside')[0][0]=='Second Shop'
    assert create_ticket(c,request_id=rid,request_version=old,outside_sequence='1').status_code==409
    assert decision(app,c,rid).status_code==302
    assert post(c,'/requests/'+rid+'/edit',**(FIELDS|dict(version=rv(app,rid),reason='Process now internal'))).status_code==302
    assert not rows(app,'SELECT * FROM request_outside')
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'


def test_invalid_plan_and_audit_failure_are_atomic(app):
    c=app.test_client();sign_in(c,'planner')
    for changes in ({'outside_vendor':''},{'outside_due_date':'bad'},{'outside_instructions':''}):
        assert receive(c,**(PLAN|changes)).status_code==400
    assert not rows(app,'SELECT * FROM intake_requests')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_request BEFORE INSERT ON intake_events BEGIN SELECT RAISE(ABORT,'Test audit'); END")
    with pytest.raises(sqlite3.IntegrityError):receive(c,**PLAN)
    assert not rows(app,'SELECT * FROM request_outside')
    assert not rows(app,'SELECT * FROM intake_requests')


def test_plan_access_is_tenant_scoped_and_operator_cannot_edit(app):
    c,rid=prepared(app);sign_in(c,'operator')
    assert post(c,'/requests/'+rid+'/edit',**(FIELDS|PLAN|dict(version='1',reason='Wrong role'))).status_code==403
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships SELECT 2,id,'planner' FROM users WHERE username='planner'")
    sign_in(c,'planner','beta')
    assert c.get('/requests/'+rid).status_code==404
    assert c.get('/requests/'+rid+'/edit').status_code==404
    assert post(c,'/requests/'+rid+'/edit',**(FIELDS|PLAN|dict(version='1',reason='Wrong tenant'))).status_code==404
    assert b'Finish Shop' not in c.get('/requests/new').data


def test_repair_conversion_preserves_requested_vendor_work(app):
    from test_return_repairs import returned_job,repair,repair_id
    c,tid,return_id=returned_job(app)
    rid=receive(c,**PLAN).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert repair(app,c,tid,return_id,request_id=rid,outside_sequence='').status_code==400
    assert repair(app,c,tid,return_id,request_id=rid,outside_sequence='1').status_code==302
    repaired=repair_id(app,return_id)
    assert rows(app,'SELECT vendor FROM outside_steps WHERE ticket_id=?',(repaired,))[0][0]=='Finish Shop'
