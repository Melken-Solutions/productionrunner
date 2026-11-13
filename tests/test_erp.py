import json
from test_workflow import app,post,rows,sign_in
from test_request_job_plan import setup,payload
from test_intake import receive,decision,convert,rv,FIELDS
from test_lots import tv
from productionrunner.erp import settings,job_details
from productionrunner.database import connect


def cfg(app):
 with connect(app.config['DATABASE']) as c:return settings(c,1)


def configure(app,c,source='erp',**extra):
 sign_in(c,'admin')
 if not cfg(app)['version']:
  assert post(c,'/admin/erp',action='organization',version='0',erp_name='SAP',required_sources=['erp','smartsheet'],reason='Configure ERP').status_code==302
 data=dict(action='path',version=str(cfg(app)['version']),name='Standard production order',source=source,active='1',production_order_required='release',checks='Order matches PN and quantity\nERP is clear to build',confirmation_stage='depart',confirmation_instructions='Record SAP confirmation number',reason='Configure path')
 data.update(extra)
 assert post(c,'/admin/erp',**data).status_code==302
 path=cfg(app)['paths'][-1];sign_in(c,'planner');return path


def verify(app,c,tid,**extra):
 data=dict(version=tv(app,tid),erp_present='yes',erp_production_order='000123456',erp_confirm='yes',erp_checks=['0','1'],reason='Checked ERP release')
 data.update(extra);return post(c,f'/tickets/{tid}/erp',**data)


def test_standard_order_snapshot_release_and_confirmation(app):
 c,plan=setup(app);path=configure(app,c)
 response=receive(c,source='erp',source_reference='',erp_present='yes',erp_path=path['id'],**payload(plan));assert response.status_code==302;rid=response.location.split('/')[-1]
 assert decision(app,c,rid).status_code==302 # production order only needed before release
 tid=convert(app,c,rid).location.split('/')[-1]
 assert post(c,f'/tickets/{tid}/release',version=tv(app,tid),reason='Release',plan_review='yes').status_code==409
 assert verify(app,c,tid,erp_production_order='').status_code==400
 assert verify(app,c,tid,erp_checks=['0']).status_code==400
 assert verify(app,c,tid).status_code==302
 evidence=json.loads(rows(app,"SELECT detail FROM events WHERE action='erp_verified'")[0][0])
 assert 'verified_by' not in evidence and evidence['item']['pn']=='PN-001'
 assert b'000123456' in c.get(f'/tickets/{tid}/erp').data
 # Later organization edits do not rewrite a converted job's requirements.
 configure(app,c,name='Another path',sales_order_required='release')
 assert post(c,f'/tickets/{tid}/release',version=tv(app,tid),reason='Release',plan_review='yes').status_code==302
 assert rows(app,'SELECT stage FROM fulfillment_checks')[-1][0]=='depart'
 release=json.loads(rows(app,"SELECT detail FROM events WHERE action='erp_release_snapshot'")[0][0])
 assert release['values']['production_order']=='000123456' and release['verified_by']=='planner'
 assert release['path']['fields']['sales_order']['required']=='optional'
 assert verify(app,c,tid).status_code==409
 assert b'000123456' in c.get(f'/tickets/{tid}/erp/export.csv').data


def test_supporting_demand_requirements_and_edit_invalidates_approval(app):
 c,plan=setup(app);p=configure(app,c,source='smartsheet',sales_order_required='approval',sales_line_required='approval',support_reference_required='approval')
 data=dict(source='smartsheet',source_reference='',erp_present='yes',erp_path=p['id'])
 rid=receive(c,**data,**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 update=FIELDS|payload(plan)|data|dict(version=rv(app,rid),erp_sales_order='SO-100',erp_sales_line='10',erp_support_reference='Hot sheet / row 22',reason='Add references')
 assert post(c,f'/requests/{rid}/edit',**update).status_code==302
 assert decision(app,c,rid).status_code==302
 update.update(version=rv(app,rid),erp_sales_order='SO-101')
 assert post(c,f'/requests/{rid}/edit',**update).status_code==302
 assert rows(app,'SELECT status FROM intake_requests WHERE id=?',(rid,))[0][0]=='preparing'


def test_configuration_roles_conflicts_and_tenant_boundary(app):
 c,plan=setup(app);p=configure(app,c)
 assert c.get('/admin/erp').status_code==403
 sign_in(c,'admin')
 assert post(c,'/admin/erp',action='organization',version='0',erp_name='Bad',reason='Stale').status_code==409
 assert c.post('/admin/erp',data={'action':'organization'}).status_code==400
 sign_in(c,'planner')
 assert receive(c,source='erp',source_reference='',erp_present='yes',erp_path='foreign',**payload(plan)).status_code==409
 rid=receive(c,source='erp',source_reference='',erp_present='yes',erp_path=p['id'],erp_production_order='100',**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==302
 tid=convert(app,c,rid).location.split('/')[-1]
 sign_in(c,'operator');assert c.get(f'/tickets/{tid}/erp').status_code==403
 runner=app.test_cli_runner();assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
 assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
 sign_in(c,'admin',tenant='beta')
 assert b'Standard production order' not in c.get('/admin/erp').data
 assert c.get(f'/tickets/{tid}/erp/export.csv').status_code==404


def test_duplicate_reference_warning_and_missing_path_gate(app):
 c,plan=setup(app);p=configure(app,c)
 rid=receive(c,source='erp',source_reference='',**payload(plan)).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 args=dict(source='erp',source_reference='',erp_present='yes',erp_path=p['id'],erp_production_order='00099')
 first=receive(c,**args,**payload(plan)).location
 second=receive(c,**args,**payload(plan)).location
 assert first.encode() in c.get(second).data
 assert b'This production order also appears' in c.get(second).data
