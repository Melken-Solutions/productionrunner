import json
import sqlite3
import pytest
from test_workflow import app,post,rows,sign_in
from test_dispatch import setup_job,assign,version,prepare_plan,release


def ready(app,serial=False,first=False):
 c,path,ops=setup_job(app,serial)
 for op in ops:assert assign(app,c,path,op,1).status_code==302
 data=dict(version=version(app,path),reason='Approve door strike process',inspection_station='1',packing_station='2',shipping_station='2',inspection_criteria='Measure bend and hole locations to drawing',packing_criteria='Protect satin surface; count pieces',shipping_criteria='Verify destination and delivery reference')
 for op in ops:data['criteria_'+str(op['id'])]='Check dimensions to released drawing'
 if first:data['first_'+str(ops[0]['id'])]='yes'
 assert post(c,path+'/plan',**data).status_code==302
 return c,path,ops,data


def test_plan_required_and_release_review_snapshot(app):
 c,path,ops=setup_job(app)
 for op in ops:assert assign(app,c,path,op,1).status_code==302
 assert post(c,path+'/release',version=version(app,path),reason='Try skip plan',plan_review='yes').status_code==409
 prepare_plan(app,c,path)
 assert post(c,path+'/release',version=version(app,path),reason='No acknowledgement').status_code==400
 assert release(app,c,path).status_code==302
 evidence=json.loads(rows(app,"SELECT detail FROM events WHERE action='job_plan_released'")[0][0])
 assert evidence['drawing']['pn']=='PN-001'
 assert len(evidence['operations'])==2 and evidence['plan']['shipping']['station_name']=='Laser 1'
 assert post(c,path+'/plan',version=version(app,path),reason='Silent rewrite').status_code==409
 with sqlite3.connect(app.config['DATABASE']) as conn:
  with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM job_plans')
 assert b'Shipping / physical departure' in c.get(path+'/plan').data


def test_batch_evidence_and_first_piece_gate(app):
 c,path,ops,data=ready(app,first=True);assert release(app,c,path).status_code==302
 sign_in(c,'operator')
 url=path+'/complete';args=dict(version=version(app,path),operation_id=str(ops[0]['id']),confirm='yes')
 assert post(c,url,**args).status_code==409
 sign_in(c,'inspector')
 review=path+'/plan/first-piece/'+str(ops[0]['id'])
 assert post(c,review,version=version(app,path),result='fail',notes='Bend setup wrong').status_code==302
 sign_in(c,'operator');args['version']=version(app,path)
 assert post(c,url,**args).status_code==409
 sign_in(c,'inspector')
 assert post(c,review,version=version(app,path),result='pass',notes='Measured first piece; all features within drawing tolerance').status_code==302
 sign_in(c,'operator');args['version']=version(app,path)
 assert post(c,url,**args,plan_evidence='').status_code==400
 assert post(c,url,**args).status_code==302
 assert post(c,url,version=version(app,path),operation_id=str(ops[1]['id']),confirm='yes').status_code==302
 sign_in(c,'inspector')
 assert post(c,path+'/inspect',version=version(app,path),result='pass',notes='All dimensions good',plan_evidence='').status_code==400
 assert post(c,path+'/inspect',version=version(app,path),result='pass',notes='All dimensions good').status_code==302
 sign_in(c,'operator')
 assert post(c,path+'/delivery/pack',version=version(app,path),quantity='2',confirm='yes',notes='Protected carton',plan_evidence='').status_code==400
 assert post(c,path+'/delivery/pack',version=version(app,path),quantity='2',confirm='yes',notes='Protected carton').status_code==302
 assert post(c,path+'/delivery/depart',version=version(app,path),quantity='2',confirm='yes',notes='Collected',destination='Warehouse',reference='DEL-1',method='internal').status_code==302
 checks=[json.loads(r[0]) for r in rows(app,"SELECT detail FROM events WHERE action='job_plan_check_recorded'")]
 assert [x['step'] for x in checks]==[ops[0]['id'],ops[1]['id'],'inspection','packing','shipping']
 assert checks[-1]['station']=='Assembly'


def test_serial_evidence_is_per_unit_and_station_reassignment_resets_first_piece(app):
 c,path,ops,data=ready(app,serial=True,first=True);assert release(app,c,path).status_code==302
 sign_in(c,'inspector')
 assert post(c,path+'/plan/first-piece/'+str(ops[0]['id']),version=version(app,path),result='pass',notes='Setup verified').status_code==302
 sign_in(c,'planner');assert assign(app,c,path,ops[0],2).status_code==302
 sign_in(c,'operator');unit=rows(app,'SELECT * FROM serial_units ORDER BY id')[0]
 url='/units/'+unit['id']+'/complete';args=dict(version=str(unit['version']),operation_id=str(ops[0]['id']),confirm='yes')
 assert post(c,url,**args).status_code==409
 sign_in(c,'inspector')
 assert post(c,path+'/plan/first-piece/'+str(ops[0]['id']),version=version(app,path),result='pass',notes='New station verified').status_code==302
 sign_in(c,'operator')
 assert post(c,url,**args,plan_evidence='').status_code==400
 assert post(c,url,**args).status_code==302
 record=json.loads(rows(app,"SELECT detail FROM events WHERE action='job_plan_check_recorded'")[0][0].split(': ',1)[1])
 assert record['unit_id']==unit['id']
 assert len(rows(app,'SELECT * FROM unit_completions'))==1


def test_plan_scope_validation_staleness_and_audit_rollback(app):
 c,path,ops,data=ready(app)
 assert post(c,path+'/plan',**data).status_code==409
 sign_in(c,'operator')
 assert post(c,path+'/plan',version=version(app,path)).status_code==403
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
  conn.execute("INSERT INTO memberships SELECT 2,id,'planner' FROM users WHERE username='planner'")
 sign_in(c,'planner','beta');assert c.get(path+'/plan').status_code==404
 sign_in(c,'planner');data['version']=version(app,path);data['inspection_station']='9999'
 assert post(c,path+'/plan',**data).status_code==400
 data['inspection_station']='1'
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_plan_event BEFORE INSERT ON events WHEN NEW.action='job_plan_saved' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):post(c,path+'/plan',**data)
 assert len(rows(app,'SELECT * FROM job_plans'))==1
 assert not rows(app,'PRAGMA foreign_key_check')


def test_dxf_release_gate(app):
 c,path,ops,data=ready(app)
 data.update(version=version(app,path),dxf_required='yes')
 assert post(c,path+'/plan',**data).status_code==302
 assert release(app,c,path).status_code==409
 assert rows(app,'SELECT released FROM ticket_dispatch')[0][0]==0

def test_first_piece_allows_setup_but_blocks_run_and_self_approval(app):
 from test_work_tracking import act
 c,path,ops,data=ready(app,first=True);assert release(app,c,path).status_code==302
 code=path.split('/')[-1];sign_in(c,'operator')
 assert act(app,c,code,'start',mode='production').status_code==409
 assert act(app,c,code,'start',mode='machine').status_code==409
 assert act(app,c,code,'start',mode='setup').status_code==302
 assert post(c,path+'/plan/first-piece/'+str(ops[0]['id']),version=version(app,path),result='pass',notes='Self approve').status_code==403
 assert act(app,c,code,'stop').status_code==302
 sign_in(c,'inspector')
 assert post(c,path+'/plan/first-piece/'+str(ops[0]['id']),version=version(app,path),result='pass',notes='Independently checked setup').status_code==302
 sign_in(c,'operator');assert act(app,c,code,'start',mode='production').status_code==302


def test_final_station_board_and_deactivation_protection(app):
 from test_dispatch import board
 c,path,ops,data=ready(app);assert release(app,c,path).status_code==302
 sign_in(c,'operator')
 for op in ops:assert post(c,path+'/complete',version=version(app,path),operation_id=str(op['id']),confirm='yes').status_code==302
 assert any(row['operation']=='Independent inspection' and row['inspection']==2 for row in board(app,1))
 sign_in(c,'admin')
 assert post(c,'/admin/stations/2',version='1',name='Assembly',active='0',reason='Try disable planned packing').status_code==409
 sign_in(c,'inspector');assert post(c,path+'/inspect',version=version(app,path),result='pass',notes='Checked').status_code==302
 assert any(row['operation']=='Packing' and row['ready']==2 for row in board(app,2))
 sign_in(c,'operator');assert post(c,path+'/delivery/pack',version=version(app,path),quantity='2',confirm='yes',notes='Protected').status_code==302
 assert any(row['operation']=='Physical departure' and row['ready']==2 for row in board(app,2))

def test_partial_serial_station_change_can_get_new_first_piece_approval(app):
 from test_dispatch import board
 c,path,ops,data=ready(app,serial=True,first=True);assert release(app,c,path).status_code==302
 assert board(app,1)[0]['ready']==0 and 'First-piece' in board(app,1)[0]['operation']
 sign_in(c,'inspector');review=path+'/plan/first-piece/'+str(ops[0]['id'])
 assert post(c,review,version=version(app,path),result='pass',notes='First setup verified').status_code==302
 sign_in(c,'operator');units=rows(app,'SELECT * FROM serial_units ORDER BY id')
 assert post(c,'/units/'+units[0]['id']+'/complete',version=str(units[0]['version']),operation_id=str(ops[0]['id']),confirm='yes').status_code==302
 sign_in(c,'planner');assert assign(app,c,path,ops[0],2).status_code==302
 sign_in(c,'inspector');assert post(c,review,version=version(app,path),result='pass',notes='Replacement setup verified for remaining units').status_code==302
 sign_in(c,'operator');u=rows(app,'SELECT * FROM serial_units WHERE id=?',(units[1]['id'],))[0]
 assert post(c,'/units/'+u['id']+'/complete',version=str(u['version']),operation_id=str(ops[0]['id']),confirm='yes').status_code==302
 assert len(rows(app,'SELECT * FROM unit_completions'))==2
