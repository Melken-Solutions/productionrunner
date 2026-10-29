import json
import sqlite3
import pytest
from test_workflow import app, post, rows, sign_in
from test_intake import planner, receive, decision, convert, rv, FIELDS
from test_parts import add_part


def setup(app):
 c=planner(app);add_part(c)
 sign_in(c,'admin')
 for name in ('Laser 1','Drill press','Packing'):
  assert post(c,'/admin/stations',name=name,reason='Configure test stations').status_code==302
 sign_in(c,'planner')
 ids=[r['id'] for r in rows(app,'SELECT id FROM stations ORDER BY id')]
 plan=dict(steps=[dict(name='Cut',instructions='Cut to drawing',criteria='Verify contour',station_id=ids[0],first_piece=True,outside=False),dict(name='Drill',instructions='Drill holes',criteria='Check hole diameter',station_id=ids[1],first_piece=False,outside=False)],dxf_required=False)
 for stage in ('inspection','packing','shipping'):plan[stage]=dict(station_id=ids[-1],criteria='Verify '+stage)
 return c,plan


def payload(plan):return dict(request_plan_present='yes',request_plan_json=json.dumps(plan))


def test_initial_plan_carries_route_assignments_checks_and_cannot_be_overridden(app):
 c,plan=setup(app)
 assert b'Job Plan' in c.get('/requests/new').data
 r=receive(c,**payload(plan));assert r.status_code==302;rid=r.location.split('/')[-1]
 assert b'Check hole diameter' in c.get(r.location).data
 assert decision(app,c,rid).status_code==302
 page=c.get('/tickets/new',query_string={'request_id':rid})
 assert b'name="route"' not in page.data
 result=convert(app,c,rid,route='WRONG | bypass',instructions='WRONG')
 assert result.status_code==302
 assert [r['name'] for r in rows(app,'SELECT name FROM operations ORDER BY sequence')]==['Cut','Drill']
 assert [r['station_id'] for r in rows(app,'SELECT station_id FROM operation_assignments ORDER BY operation_id')]==[s['station_id'] for s in plan['steps']]
 saved=json.loads(rows(app,'SELECT data FROM job_plans')[0][0])
 assert list(saved['operations'].values())[0]['first_piece'] is True
 assert saved['packing']['criteria']=='Verify packing'
 assert rows(app,'SELECT released FROM ticket_dispatch')[0][0]==0
 assert rows(app,"SELECT * FROM events WHERE action='job_plan_from_request'")
 with sqlite3.connect(app.config['DATABASE']) as conn:
  with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM request_job_plans')


def test_draft_incomplete_and_changed_plan_invalidates_approval(app):
 c,plan=setup(app);plan['steps'][0]['criteria']=''
 rid=receive(c,**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 plan['steps'][0]['criteria']='Measure contour'
 def edit():return post(c,f'/requests/{rid}/edit',**(FIELDS|payload(plan)|dict(version=rv(app,rid),reason='Plan correction')))
 assert edit().status_code==302
 assert decision(app,c,rid).status_code==302
 plan['steps'].reverse()
 assert edit().status_code==302
 assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
 assert convert(app,c,rid).status_code==409
 assert decision(app,c,rid).status_code==302
 assert convert(app,c,rid).status_code==302
 assert rows(app,'SELECT name FROM operations ORDER BY sequence')[0][0]=='Drill'


def test_invalid_station_and_required_dxf_block_approval(app):
 c,plan=setup(app);plan['steps'][0]['station_id']=99999
 assert receive(c,**payload(plan)).status_code==400
 assert not rows(app,'SELECT * FROM intake_requests')
 plan['steps'][0]['station_id']=rows(app,'SELECT id FROM stations ORDER BY id')[0][0]
 plan['dxf_required']=True
 rid=receive(c,**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 plan['dxf_required']=False
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|payload(plan)|dict(version=rv(app,rid),reason='No DXF needed'))).status_code==302
 assert decision(app,c,rid).status_code==302
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('UPDATE stations SET active=0 WHERE id=?',(plan['steps'][0]['station_id'],))
 assert convert(app,c,rid).status_code==409


def test_outside_step_copies_at_planned_position(app):
 c,plan=setup(app)
 plan['steps'].append(dict(name='Clear coating',instructions='Vendor clear coats',criteria='Finish intact',station_id=0,outside=True,first_piece=False))
 outside=dict(outside_required='yes',outside_vendor='Coater',outside_service='Clear coating',outside_instructions='Protect threads',outside_inspection_requirements='Verify coverage',outside_due_date='2026-12-01')
 rid=receive(c,**payload(plan),**outside).location.split('/')[-1]
 assert decision(app,c,rid).status_code==302
 assert convert(app,c,rid,outside_sequence='1').status_code==302
 assert rows(app,'SELECT sequence FROM operations o JOIN outside_steps s ON s.operation_id=o.id')[0][0]==3
 assert len(rows(app,'SELECT * FROM operation_assignments'))==2


def test_outside_processing_follows_internal_work(app):
 c,plan=setup(app)
 plan['steps'].insert(0,dict(name='Coating',instructions='Coat',criteria='Check finish',station_id=0,outside=True,first_piece=False))
 outside=dict(outside_required='yes',outside_vendor='Coater',outside_service='Clear coating',outside_instructions='Protect threads',outside_inspection_requirements='Verify coverage',outside_due_date='2026-12-01')
 rid=receive(c,**payload(plan),**outside).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 plan['steps'].append(plan['steps'].pop(0))
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|payload(plan)|outside|dict(version=rv(app,rid),reason='Outside after internal work'))).status_code==302
 assert decision(app,c,rid).status_code==302
