import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app,sign_in,post,rows,create_ticket,complete_route
from test_intake import receive,decision,rv
from test_parts import add_part
from test_fulfillment import move,checkpoint


def client(app,name='admin'):
 c=app.test_client();sign_in(c,name);return c


def save(c,**kw):
 return post(c,'/admin/workflow',**(dict(action='save',version='0',require_intake='0',pack_checks='ERP production confirmation',depart_checks='ERP delivery reference',reason='Shop process')|kw))


def test_preview_is_unsaved_and_defaults_are_not_retroactive(app):
 c=client(app)
 assert b'Direct ticket' not in c.get('/admin/workflow').data  # Screen describes option, not a ticket.
 assert save(c,action='sap_example').status_code==200
 assert not rows(app,'SELECT * FROM workflow_defaults')
 sign_in(c,'planner');old=create_ticket(c).location.split('/')[-1]
 assert not rows(app,'SELECT * FROM fulfillment_checks')
 sign_in(c,'admin');assert save(c).status_code==302
 sign_in(c,'planner')
 assert create_ticket(c).status_code==409
 new=create_ticket(c,workflow_version='1').location.split('/')[-1]
 assert len(rows(app,'SELECT * FROM fulfillment_checks WHERE ticket_id=?',(new,)))==2
 assert not rows(app,'SELECT * FROM fulfillment_checks WHERE ticket_id=?',(old,))
 sign_in(c,'admin');assert save(c,version='1',pack_checks='',depart_checks='').status_code==302
 sign_in(c,'planner');third=create_ticket(c,workflow_version='2').location.split('/')[-1]
 assert len(rows(app,'SELECT * FROM fulfillment_checks WHERE ticket_id=?',(new,)))==2
 assert not rows(app,'SELECT * FROM fulfillment_checks WHERE ticket_id=?',(third,))
 versions=rows(app,'SELECT policy_version FROM ticket_workflow ORDER BY rowid')
 assert [r[0] for r in versions]==[0,1,2]
 assert len(rows(app,"SELECT * FROM auth_events WHERE action='workflow_defaults_updated'"))==2
 assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
 assert not rows(app,'PRAGMA foreign_key_check')


def test_required_approved_intake_and_inherited_gates(app):
 c=client(app);assert save(c,require_intake='1').status_code==302
 sign_in(c,'planner');assert create_ticket(c,auto_request=False,workflow_version='1').status_code==409
 add_part(c);rid=receive(c).location.split('/')[-1]
 assert create_ticket(c,workflow_version='1',request_id=rid,request_version=rv(app,rid)).status_code==409
 assert decision(app,c,rid).status_code==302
 ticket=create_ticket(c,workflow_version='1',request_id=rid,request_version=rv(app,rid)).location
 tid=ticket.split('/')[-1]
 assert json.loads(rows(app,'SELECT checks FROM ticket_workflow')[0][0])[0]['name']=='ERP production confirmation'
 complete_route(app,c,ticket);sign_in(c,'inspector')
 assert post(c,ticket+'/inspect',version='3',result='pass',notes='Passed').status_code==302
 sign_in(c,'planner');assert move(app,c,tid,'pack').status_code==409
 checks=rows(app,'SELECT * FROM fulfillment_checks ORDER BY id')
 assert checkpoint(app,c,tid,action='complete',check_id=checks[0]['id'],reference='SAP-CONFIRM-1').status_code==302
 assert move(app,c,tid,'pack',quantity='2').status_code==302
 assert move(app,c,tid,'depart').status_code==409
 assert checkpoint(app,c,tid,action='complete',check_id=checks[1]['id'],reference='SAP-DELIVERY-1').status_code==302
 assert move(app,c,tid,'depart',quantity='2').status_code==302
 assert b'workflow version 1' in c.get(ticket+'/delivery').data


@pytest.mark.parametrize('fields',[dict(pack_checks='x'*121),dict(pack_checks='Same',depart_checks='same'),dict(pack_checks='\n'.join('Check '+str(n) for n in range(21))),dict(reason=''),dict(require_intake='bad')])
def test_invalid_defaults_are_atomic(app,fields):
 c=client(app);assert save(c,**fields).status_code==400
 assert not rows(app,'SELECT * FROM workflow_defaults')
 assert not rows(app,"SELECT * FROM auth_events WHERE action='workflow_defaults_updated'")


def test_permissions_tenant_isolation_and_csrf(app):
 c=client(app)
 assert c.post('/admin/workflow',data={'action':'save'}).status_code==400
 for name in ('planner','operator','inspector','viewer'):
  sign_in(c,name);assert c.get('/admin/workflow').status_code==403;assert save(c).status_code==403
 sign_in(c,'admin');assert save(c).status_code==302
 runner=app.test_cli_runner();runner.invoke(args=['add-tenant','beta','--name','Beta']);runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']);runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner'])
 sign_in(c,'admin','beta');assert b'ERP production confirmation' not in c.get('/admin/workflow').data
 assert save(c,pack_checks='Beta only',depart_checks='').status_code==302
 sign_in(c,'planner','beta');tid=create_ticket(c,workflow_version='1').location.split('/')[-1]
 assert [r[0] for r in rows(app,'SELECT name FROM fulfillment_checks WHERE ticket_id=?',(tid,))]==['Beta only']
 assert rows(app,'SELECT tenant_id FROM ticket_workflow')[0][0]==2


def test_stale_admin_saves_and_concurrent_changes(app):
 c=client(app);c2=client(app)
 with ThreadPoolExecutor(max_workers=2) as pool:
  statuses=list(pool.map(lambda cc:save(cc).status_code,[c,c2]))
 assert sorted(statuses)==[302,409]
 assert save(c,version='1',action='preview').status_code==200
 assert save(c,version='1',pack_checks='Changed').status_code==302
 assert save(c,version='1').status_code==409
 sign_in(c,'planner');assert create_ticket(c,workflow_version='1').status_code==409
 assert not rows(app,'SELECT * FROM tickets')


def test_failed_audit_rolls_back_settings_and_ticket_snapshot(app):
 c=client(app)
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_workflow BEFORE INSERT ON auth_events WHEN NEW.action='workflow_defaults_updated' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):save(c)
 assert not rows(app,'SELECT * FROM workflow_defaults')
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_workflow')
 assert save(c).status_code==302
 sign_in(c,'planner')
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON events WHEN NEW.action='workflow_defaults_applied' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):create_ticket(c,workflow_version='1')
 for table in ('tickets','ticket_workflow','fulfillment_checks'):assert not rows(app,'SELECT * FROM '+table)
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_snapshot')
 assert create_ticket(c,workflow_version='1').status_code==302
 with sqlite3.connect(app.config['DATABASE']) as conn:
  for sql in ('DELETE FROM ticket_workflow','UPDATE ticket_workflow SET policy_version=8'):
   with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
