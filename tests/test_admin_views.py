from test_workflow import app,sign_in,rows,post
from test_work_tracking import setup,act


def test_admin_can_switch_views_and_read_shop_work_without_operator_powers(app):
 c,tid,code,ops=setup(app);sign_in(c,'admin')
 for route in ['/','/admin','/requests','/shop-floor','/tv','/tv/board','/messages']:
  response=c.get(route)
  assert response.status_code==200,(route,response.status_code)
  assert b'aria-label="Switch view"' in response.data
 station=rows(app,'SELECT station_id FROM operation_assignments LIMIT 1')[0][0]
 assert c.get('/shop-floor?station='+str(station)).status_code==200
 assert c.get('/shop-floor/work/'+code).location=='/work/'+code
 page=c.get('/work/'+code).data
 assert b'Admin overview' in page and b'aria-label="Switch view"' in page
 assert b'>Start work</button>' not in page and b'name="completed_quantity"' not in page
 assert act(app,c,code,'start').status_code==403
 assert c.get('/shop-floor/work/'+code+'/confirm').status_code==403
 assert not rows(app,'SELECT * FROM work_sessions')


def test_switcher_does_not_appear_for_operator_or_display_and_admin_tenant_isolation(app):
 c,tid,code,ops=setup(app)
 assert b'aria-label="Switch view"' not in c.get('/shop-floor').data
 runner=app.test_cli_runner();assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--roles','display']).exit_code==0
 sign_in(c,'viewer');assert b'aria-label="Switch view"' not in c.get('/tv/board').data
 assert c.get('/shop-floor').status_code==403
 assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
 sign_in(c,'admin',tenant='beta')
 assert c.get('/work/'+code).status_code==404
 assert tid.encode() not in c.get('/shop-floor').data
