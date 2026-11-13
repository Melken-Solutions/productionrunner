import re
from test_workflow import app,prepared,post,rows,sign_in
from test_job_deletion import delete


def test_operator_request_planner_reply_and_alerts(app):
 c,path=prepared(app);tid=path.split('/')[-1];initial=dict(rows(app,'SELECT * FROM tickets')[0]);sign_in(c,'operator')
 response=post(c,path+'/messages/new',category='Drawing question',note='')
 assert response.status_code==302;thread=response.location
 assert c.get('/messages/unread').json['count']==0
 planner=app.test_client();sign_in(planner,'planner')
 assert planner.get('/messages/unread').json['count']==1
 page=planner.get(thread);assert b'Drawing question' in page.data
 assert planner.get('/messages/unread').json['count']==0
 assert post(planner,thread,version='1',action='reply',quick_reply='Checking now',note='').status_code==302
 assert c.get('/messages/unread').json['count']==1
 page=c.get(thread);assert b'Checking now' in page.data and b'data-message-alert' in page.data
 assert b'aria-label="Shop Floor"' in page.data
 assert c.get('/messages/unread').json['count']==0
 assert post(c,thread,version='2',action='resolve').status_code==403
 assert post(planner,thread,version='2',action='resolve').status_code==302
 assert post(c,thread,version='2',action='reply',quick_reply='Please contact me').status_code==409
 assert post(c,thread,version='3',action='reply',quick_reply='Please contact me').status_code==302
 assert rows(app,'SELECT status FROM job_threads')[0][0]=='new'
 assert dict(rows(app,'SELECT * FROM tickets')[0])==initial
 assert delete(planner,path).status_code==200
 for table in ['job_threads','job_messages','job_message_reads']:assert not rows(app,'SELECT * FROM '+table)


def test_messages_enforce_roles_tenant_context_and_csrf(app):
 c,path=prepared(app);sign_in(c,'viewer');assert c.get('/messages').status_code==403
 sign_in(c,'operator')
 assert post(c,path+'/messages/new',category='Other',note='').status_code==400
 assert post(c,path+'/messages/new',category='Help needed',unit='JR-U-UNKNOWN').status_code==404
 assert c.post(path+'/messages/new',data=dict(category='Help needed')).status_code==400
 response=post(c,path+'/messages/new',category='Help needed',note='<script>alert(1)</script>');thread=response.location
 assert b'&lt;script&gt;' in c.get(thread).data
 runner=app.test_cli_runner();assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','operator','--tenant','beta','--roles','operator']).exit_code==0
 sign_in(c,'operator',tenant='beta')
 assert c.get('/messages/unread').json['count']==0
 assert c.get(thread).status_code==404
 assert post(c,thread,version='1',action='reply',quick_reply='Checking now').status_code==404
