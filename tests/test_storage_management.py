import sqlite3
from datetime import datetime,timezone,timedelta
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app,post,sign_in,rows,create_ticket,complete_route
from test_parts import add_part,PDF
from productionrunner.storage_management import inventory,DEFAULT


def setup(app):
 c=app.test_client();sign_in(c,'planner');part=add_part(c).location.split('/')[-1]
 post(c,f'/parts/{part}/dxf/upload',dxf=(BytesIO(b'dxf data'),'part.dxf'))
 sign_in(c,'admin');return c,part


def save(c,**kw):
 data=dict(action='save',version='0',pdf_choice='365',dxf_choice='1095',grace_days='30',reason='Storage policy')
 data.update(kw);return post(c,'/admin/storage',**data)


def report(app,settings=None,years=10):
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.row_factory=sqlite3.Row
  return inventory(conn,1,settings or dict(pdf_days=365,dxf_days=365,grace_days=30),datetime.now(timezone.utc)+timedelta(days=365*years))


def test_default_preview_save_reset_forever_and_bytes_preserved(app):
 c,part=setup(app)
 assert b'Keep indefinitely' in c.get('/admin/storage').data
 assert save(c,action='preview').status_code==200
 assert not rows(app,'SELECT * FROM storage_policies')
 assert save(c).status_code==302
 assert save(c).status_code==409
 assert save(c,version='1',pdf_choice='forever',dxf_choice='forever').status_code==302
 assert rows(app,'SELECT pdf_days,dxf_days FROM storage_policies')[0][:]==(None,None)
 assert c.get(f'/parts/{part}/drawing').data==PDF
 assert rows(app,'SELECT content FROM part_dxf')[0][0]==b'dxf data'
 assert len(rows(app,"SELECT * FROM auth_events WHERE action='storage_policy_updated'"))==2
 assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0


def test_active_held_and_matching_unlinked_jobs_protect(app):
 c,part=setup(app);sign_in(c,'planner');ticket=create_ticket(c).location
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute('DROP TRIGGER ticket_drawing_no_delete')
  conn.execute('DELETE FROM ticket_drawings')
 assert all(f['status']=='Protected by active / held work' for f in report(app)['files'])
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("UPDATE tickets SET state='held' WHERE id=?",(ticket.split('/')[-1],))
 assert all(f['status']=='Protected by active / held work' for f in report(app)['files'])


def test_accepted_closure_grace_boundaries_and_missing_evidence(app):
 c,part=setup(app);sign_in(c,'planner');ticket=create_ticket(c).location
 complete_route(app,c,ticket);sign_in(c,'inspector')
 assert post(c,ticket+'/inspect',version='3',result='pass',notes='Checked').status_code==302
 assert all(f['status']=='Protected by active / held work' for f in report(app)['files'])
 from test_fulfillment import move
 sign_in(c,'operator');tid=ticket.split('/')[-1]
 assert move(app,c,tid,'pack',quantity='2').status_code==302
 assert move(app,c,tid,'depart',quantity='2').status_code==302
 files=report(app)['files']; assert all(f['status']=='Eligible in preview' for f in files)
 assert all(f['references']==1 for f in files)
 due=files[0]['due']
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.row_factory=sqlite3.Row
  settings=dict(pdf_days=365,dxf_days=365,grace_days=30)
  assert next(f for f in inventory(conn,1,settings,due-timedelta(seconds=1))['files'] if f['kind']==files[0]['kind'])['status']=='Grace period in preview'
  result=inventory(conn,1,settings,due)
  matching=next(f for f in result['files'] if f['kind']==files[0]['kind'])
  assert matching['status']=='Eligible in preview'
 # No acceptance evidence must not be treated as old closed work.
 c2=app.test_client();sign_in(c2,'planner');new=create_ticket(c2).location.split('/')[-1]
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute("UPDATE tickets SET state='accepted' WHERE id=?",(new,))
 assert all(f['status']=='Protected by active / held work' for f in report(app)['files'])


def test_cancelled_jobs_age_from_cancellation(app):
 c,part=setup(app);sign_in(c,'planner');ticket=create_ticket(c).location;sign_in(c,'admin')
 assert post(c,ticket+'/cancel',version='1',reason='Test cancellation',confirm='yes').status_code==302
 assert all(f['status']=='Eligible in preview' for f in report(app)['files'])


def test_permanent_keep_audited_stale_and_release(app):
 c,part=setup(app);url=f'/admin/storage/{part}/pdf'
 assert post(c,url,version='0',keep='1',reason='Customer retention requirement').status_code==302
 assert next(f for f in report(app)['files'] if f['kind']=='pdf')['status']=='Keep permanently'
 assert post(c,url,version='0',keep='0',reason='Stale').status_code==409
 assert post(c,url,version='1',keep='0',reason='Policy applies').status_code==302
 assert len(rows(app,"SELECT * FROM auth_events WHERE action='storage_keep_updated'"))==2


def test_duplicates_and_external_links_are_tenant_scoped(app):
 c,part=setup(app);sign_in(c,'planner');add_part(c,pn='PN-002');add_part(c,pn='PN-003',link='https://example.com/drawing.pdf')
 r=report(app); assert r['duplicate_bytes']==len(PDF) and r['external']==1 and r['unused']==3
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
  conn.execute("INSERT INTO memberships VALUES(2,4,'admin')")
 sign_in(c,'viewer',tenant='beta')
 assert b'PN-001' not in c.get('/admin/storage?tenant_id=1').data
 assert post(c,f'/admin/storage/{part}/pdf',version='0',keep='1',reason='Wrong tenant').status_code==404
 assert save(c).status_code==302
 assert rows(app,'SELECT tenant_id FROM storage_policies')[0][0]==2


@pytest.mark.parametrize('invalid',[dict(pdf_choice='0'),dict(dxf_choice='custom',dxf_custom='36501'),dict(grace_days='0'),dict(reason=''),dict(pdf_choice='evil')])
def test_invalid_policy_is_atomic(app,invalid):
 c,part=setup(app);assert save(c,**invalid).status_code==400
 assert not rows(app,'SELECT * FROM storage_policies')


def test_permissions_csrf_concurrency_and_audit_failure(app):
 c,part=setup(app)
 assert c.post('/admin/storage',data={'action':'save'}).status_code==400
 for role in ('planner','operator','inspector','viewer'):
  sign_in(c,role);assert c.get('/admin/storage').status_code==403;assert save(c).status_code==403
 sign_in(c,'admin');c2=app.test_client();sign_in(c2,'admin')
 with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda client:save(client).status_code,[c,c2]))==[302,409]

 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_storage_audit BEFORE INSERT ON auth_events WHEN NEW.action='storage_policy_updated' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):save(c,version='1',pdf_choice='forever')
 assert rows(app,'SELECT pdf_days,version FROM storage_policies')[0][:]==(365,1)
