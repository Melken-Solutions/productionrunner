from test_workflow import app,prepared,post,rows,sign_in


def add(c,**extra):
 data=dict(title='Fixture check',body='Please check the fixture at this station.',category='Help needed',usage='both',active='1',reason='New choice')
 data.update(extra)
 return post(c,'/admin/message-snippets',**data)


def test_builtin_and_other_messages(app):
 c,path=prepared(app);sign_in(c,'operator')
 assert b'Other / type a note' in c.get(path+'/messages/new').data
 response=post(c,path+'/messages/new',snippet_id='builtin:drawing-mismatch',note='Forged text')
 assert response.status_code==302
 assert rows(app,'SELECT body FROM job_messages')[0][0]=='The drawing does not match this traveler. Please confirm the correct drawing and revision.'
 assert post(c,response.location,action='reply',version='1',snippet_id='builtin:understood').status_code==302
 assert post(c,path+'/messages/new',snippet_id='other',note='').status_code==400
 assert post(c,path+'/messages/new',snippet_id='other',note='Need a replacement fixture').status_code==302
 assert post(c,path+'/messages/new',snippet_id='builtin:understood').status_code==409


def test_custom_catalog_versioning_and_history(app):
 c,path=prepared(app);sign_in(c,'admin')
 assert add(c).status_code==302
 sid=rows(app,'SELECT id FROM message_snippets')[0][0]
 assert add(c).status_code==409
 sign_in(c,'operator')
 assert c.get('/admin/message-snippets').status_code==403
 assert add(c,title='Forbidden').status_code==403
 assert post(c,path+'/messages/new',snippet_id=f'custom:{sid}:1').status_code==302
 sign_in(c,'admin')
 assert add(c,id=str(sid),version='1',body='Updated fixture guidance').status_code==302
 assert add(c,id=str(sid),version='1').status_code==409
 sign_in(c,'operator')
 assert post(c,path+'/messages/new',snippet_id=f'custom:{sid}:1').status_code==409
 assert post(c,path+'/messages/new',snippet_id=f'custom:{sid}:2').status_code==302
 assert rows(app,'SELECT body FROM job_messages ORDER BY id')[0][0]=='Please check the fixture at this station.'
 sign_in(c,'admin')
 assert add(c,id=str(sid),version='2',active='0').status_code==302
 assert len(rows(app,"SELECT * FROM auth_events WHERE action='message_snippet_changed'"))==3
 sign_in(c,'operator')
 assert post(c,path+'/messages/new',snippet_id=f'custom:{sid}:3').status_code==409
 assert b'Fixture check' not in c.get(path+'/messages/new').data


def test_catalog_tenant_and_csrf_boundaries(app):
 c,path=prepared(app);sign_in(c,'admin');assert add(c).status_code==302
 sid=rows(app,'SELECT id FROM message_snippets')[0][0]
 assert c.post('/admin/message-snippets',data=dict(title='No CSRF')).status_code==400
 runner=app.test_cli_runner()
 assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
 sign_in(c,'admin',tenant='beta')
 assert b'Fixture check' not in c.get('/admin/message-snippets').data
 assert add(c,id=str(sid),version='1').status_code==404
