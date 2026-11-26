import re,json,sqlite3
import pytest
from test_workflow import app,prepared,post,rows,sign_in
from test_lots import setup
from productionrunner.database import connect


def delete(c,path,**overrides):
 page=c.get(path+'/delete');assert page.status_code==200
 data={name:re.search(rb'name="'+name.encode()+rb'" value="([^"]+)"',page.data).group(1).decode() for name in ['version','impact']}
 data.update(confirm_id=path.split('/')[-1],confirm='yes',reason='Test or training job');data.update(overrides)
 return post(c,path+'/delete',**data)


@pytest.mark.parametrize('role',['admin','planner'])
def test_permanent_delete_preserves_library_and_request_audit(app,role):
 c,path=prepared(app);tid=path.split('/')[-1];sign_in(c,role)
 parts=len(rows(app,'SELECT * FROM part_revisions'))
 assert delete(c,path).status_code==200
 assert not rows(app,'SELECT * FROM tickets WHERE id=?',(tid,))
 assert not rows(app,'SELECT * FROM operations WHERE ticket_id=?',(tid,))
 assert c.get(path).status_code==404
 assert len(rows(app,'SELECT * FROM part_revisions'))==parts
 req=rows(app,'SELECT * FROM intake_requests')[0]
 assert req['status']=='cancelled' and req['ticket_id'] is None
 assert json.loads(rows(app,"SELECT detail FROM auth_events WHERE action='job_deleted'")[0][0])['ticket_id']==tid
 with connect(app.config['DATABASE']) as db:
  with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM auth_events WHERE action='job_deleted'")


def test_confirmation_roles_and_tenant_scope(app):
 c,path=prepared(app)
 assert delete(c,path,confirm_id='WRONG').status_code==400
 sign_in(c,'operator');assert c.get(path+'/delete').status_code==403
 sign_in(c,'planner');runner=app.test_cli_runner()
 assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code==0
 sign_in(c,'planner',tenant='beta');assert c.get(path+'/delete').status_code==404
 assert len(rows(app,'SELECT * FROM tickets'))==1


def test_lot_job_purge_removes_all_dependents_and_restores_guards(app):
 c,tid,units,ops=setup(app,size=50,total=100);sign_in(c,'planner')
 assert delete(c,'/tickets/'+tid).status_code==200
 for table in ['serial_units','production_lots','lot_jobs','job_plans','ticket_drawings','events']:
  assert not rows(app,'SELECT * FROM '+table)
 # A new job still has immutable history after the purge.
 c,path=prepared(app)
 with connect(app.config['DATABASE']) as db:
  with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM events')


def test_active_work_blocks_deletion(app):
 from test_work_tracking import setup as work_setup,act
 c,tid,code,ops=work_setup(app);assert act(app,c,code,'start').status_code==302
 sign_in(c,'planner');assert b'Stop the active' in c.get('/tickets/'+tid+'/delete').data
 assert post(c,'/tickets/'+tid+'/delete').status_code==409
 assert rows(app,'SELECT * FROM tickets WHERE id=?',(tid,))


def test_failed_purge_rolls_back_job_and_immutable_guards(app):
 c,path=prepared(app);tid=path.split('/')[-1]
 with connect(app.config['DATABASE']) as db:
  db.execute('CREATE TABLE deletion_test_dependency(ticket_id TEXT REFERENCES tickets(id))')
  db.execute('INSERT INTO deletion_test_dependency VALUES(?)',(tid,))
 assert delete(c,path).status_code==409
 assert rows(app,'SELECT * FROM tickets WHERE id=?',(tid,))
 assert rows(app,'SELECT * FROM events WHERE ticket_id=?',(tid,))
 with connect(app.config['DATABASE']) as db:
  with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM events')
 assert not rows(app,"SELECT * FROM auth_events WHERE action='job_deleted'")


def test_postgres_runtime_can_only_use_scoped_purge(app):
 from productionrunner.database import is_postgres
 if not is_postgres(app.config['DATABASE']):pytest.skip('PostgreSQL runtime privilege test')
 c,path=prepared(app)
 app.config['DATABASE']+='?options=-c%20role%3Dproductionrunner_runtime'
 sign_in(c,'planner');assert delete(c,path).status_code==200
 with connect(app.config['DATABASE']) as db:
  with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM auth_events')
