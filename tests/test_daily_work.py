import json
from test_workflow import app,post,rows,sign_in,prepared
from test_lots import setup,accept,move,tv,complete
from productionrunner.daily_work import quantity_view
from productionrunner.database import connect


def test_quantity_progress_and_daily_actions(app):
 c,tid,units,ops=setup(app,total=580)
 accept(app,c,units[0]['id'],ops);sign_in(c,'operator')
 assert move(app,c,tid,units[0]['id'],'pack').status_code==302
 assert move(app,c,tid,units[0]['id'],'depart').status_code==302
 assert complete(app,c,units[1]['id'],ops[0]).status_code==302
 with connect(app.config['DATABASE']) as conn:
  ticket=conn.execute('SELECT * FROM tickets WHERE id=?',(tid,)).fetchone();q=quantity_view(conn,1,ticket)
 assert q['total']==580 and q['delivery']['departed']==50 and q['delivery']['remaining']==530
 assert sum(b['quantity'] for b in q['buckets'])==580
 assert q['stages'][0]['good']==100 and q['stages'][1]['good']==50
 assert c.get(f'/tickets/{tid}/progress').status_code==200
 assert c.get('/today').status_code==403
 sign_in(c,'planner');assert c.get('/today').status_code==200
 assert b'Release readiness' not in c.get(f'/tickets/{tid}/plan').data # released requirements locked


def test_calendar_job_default_traveler_override_and_stale_edit(app):
 c,tid,units,ops=setup(app,total=100)
 url=f'/tickets/{tid}/calendar'
 old=tv(app,tid)
 assert post(c,url,version=old,day='2026-11-16',reason='Plan run').status_code==302
 assert post(c,url,version=old,day='2026-11-17',reason='Stale').status_code==409
 assert post(c,url,version=tv(app,tid),day='2026-11-17',unit_id=units[0]['id'],reason='Split run').status_code==302
 page=c.get('/calendar?start=2026-11-16');assert page.status_code==200
 assert b'LOT-001' in page.data and b'LOT-002' in page.data
 from productionrunner.production_calendar import assignments
 with connect(app.config['DATABASE']) as conn:
  a=assignments(conn,1)
 assert a[(tid,None)]['day']=='2026-11-16' and a[(tid,units[0]['id'])]['day']=='2026-11-17'
 assert rows(app,'SELECT required_date FROM intake_requests')[0][0]=='2026-11-13'
 assert post(c,url,version=tv(app,tid),day='not-a-date',reason='Bad').status_code==400
 assert post(c,url,version=tv(app,tid),day='2026-11-17',unit_id='foreign',reason='Bad').status_code==404
 sign_in(c,'operator');assert post(c,url,version=tv(app,tid),day='2026-11-17',reason='Bad').status_code==403


def test_message_claiming_and_readiness(app):
 c,path=prepared(app);sign_in(c,'operator')
 thread=post(c,path+'/messages/new',snippet_id='builtin:need-help').location
 assert post(c,thread,version='1',action='claim').status_code==403
 sign_in(c,'planner');assert post(c,thread,version='1',action='claim').status_code==302
 owner=rows(app,'SELECT owner_id FROM job_threads')[0][0];assert owner
 assert b'Handling this: planner' in c.get(thread).data
 admin=app.test_client();sign_in(admin,'admin')
 assert post(admin,thread,version='1',action='claim').status_code==409
 assert post(admin,thread,version='2',action='claim').status_code==409
 assert post(admin,thread,version='2',action='unclaim').status_code==302
 assert rows(app,'SELECT owner_id FROM job_threads')[0][0] is None
 assert c.get('/today').status_code==200
 assert c.get('/connection-status').json['ok']


def test_daily_pages_are_tenant_scoped(app):
 c,path=prepared(app)
 runner=app.test_cli_runner()
 assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code==0
 sign_in(c,'planner',tenant='beta')
 assert c.get(path+'/progress').status_code==404
 assert c.get('/today').status_code==200
 assert path.split('/')[-1].encode() not in c.get('/calendar').data


def test_readiness_and_planned_today(app):
 from test_request_job_plan import setup as plan_setup,payload
 from test_intake import receive,decision,convert
 c,plan=plan_setup(app)
 rid=receive(c,**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==302
 tid=convert(app,c,rid).location.split('/')[-1]
 page=c.get(f'/tickets/{tid}/plan')
 assert page.status_code==200 and b'Release readiness' in page.data and b'Approved drawing' in page.data
 assert post(c,f'/tickets/{tid}/calendar',version=tv(app,tid),day='2026-11-16',reason='Plan day').status_code==302
 page=c.get('/today?day=2026-11-16')
 assert page.status_code==200 and b'units planned for today' in page.data
 assert b'units planned for today' not in c.get('/today?day=2026-11-17').data
 assert c.get('/today?day=bad').status_code==400


def test_calendar_rejects_departed_travelers(app):
 c,tid,units,ops=setup(app,total=100)
 accept(app,c,units[0]['id'],ops);sign_in(c,'operator')
 assert move(app,c,tid,units[0]['id'],'pack').status_code==302
 assert move(app,c,tid,units[0]['id'],'depart').status_code==302
 sign_in(c,'planner')
 assert post(c,f'/tickets/{tid}/calendar',version=tv(app,tid),unit_id=units[0]['id'],day='2026-11-16',reason='Already shipped').status_code==409
