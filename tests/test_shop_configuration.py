import json
import re
import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows
from test_intake import receive,decision,convert,rv,FIELDS
from test_parts import add_part


def admin(app):
 c=app.test_client();sign_in(c,'admin');return c


def define(c,label='Customer finish code',kind='select',**kw):
 return post(c,'/admin/custom-fields',**(dict(label=label,kind=kind,options='Black\nWhite',required='yes',reason='Shop configuration')|kw))


def revision(c,path='/requests/new'):
 return re.search(rb'name="custom_fields_revision" value="([^"]+)"',c.get(path).data)[1].decode()


def test_starter_review_scoping_idempotence_and_existing_preservation(app):
 c=admin(app)
 assert b'Laser 1' in c.get('/admin/shop-setup').data
 assert post(c,'/admin/shop-setup',groups=['stations','materials'],starter_version='sheet-metal-v1',reason='Start').status_code==400
 args=dict(groups=['stations','materials'],starter_version='sheet-metal-v1',reason='Reviewed shop setup',confirm='yes')
 assert post(c,'/admin/shop-setup',**args).status_code==302
 assert len(rows(app,'SELECT * FROM stations'))==7
 assert len(rows(app,'SELECT * FROM materials'))==6
 assert len(rows(app,'SELECT * FROM material_choices'))==10
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute("UPDATE stations SET active=0 WHERE name='Laser 1'")
 before=[tuple(r) for r in rows(app,'SELECT * FROM materials')]
 assert post(c,'/admin/shop-setup',**args).status_code==302
 assert len(rows(app,'SELECT * FROM stations'))==7
 assert rows(app,"SELECT active FROM stations WHERE name='Laser 1'")[0][0]==0
 assert before==[tuple(r) for r in rows(app,'SELECT * FROM materials')]
 assert rows(app,"SELECT * FROM auth_events WHERE action='shop_starter_applied'")
 assert not rows(app,'SELECT * FROM tickets')
 sign_in(c,'planner');assert post(c,'/admin/shop-setup',**args).status_code==403


def test_custom_request_validation_approval_and_job_snapshot(app):
 c=admin(app);assert define(c).status_code==302
 fid=rows(app,'SELECT id FROM custom_field_definitions')[0][0]
 sign_in(c,'planner');add_part(c)
 assert receive(c).status_code==409
 token=revision(c)
 assert receive(c,custom_fields_revision=token,**{'custom_'+str(fid):'Pink'}).status_code==400
 response=receive(c,custom_fields_revision=token);rid=response.location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 args=FIELDS|dict(version=rv(app,rid),reason='Confirmed finish',custom_fields_revision=token)|{'custom_'+str(fid):'Black'}
 assert post(c,f'/requests/{rid}/edit',**args).status_code==302
 assert decision(app,c,rid).status_code==302
 ticket=convert(app,c,rid);assert ticket.status_code==302
 tid=ticket.location.split('/')[-1]
 snap=json.loads(rows(app,'SELECT data FROM ticket_custom_fields WHERE ticket_id=?',(tid,))[0][0]);assert snap[0]['value']=='Black'
 assert b'Customer finish code' in c.get(ticket.location).data
 assert b'Black' in c.get(ticket.location+'/traveler').data
 sign_in(c,'admin')
 assert define(c,field_id=fid,version='1',label='New finish label',options='Blue\nWhite').status_code==302
 assert json.loads(rows(app,'SELECT data FROM ticket_custom_fields')[0][0])==snap
 assert b'Customer finish code' in c.get(ticket.location).data
 with sqlite3.connect(app.config['DATABASE']) as conn:
  with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM ticket_custom_fields')
  with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM request_custom_fields')


def test_definition_versions_retirement_and_invalid_values(app):
 c=admin(app);assert define(c,'Heat number','number').status_code==302
 fid=rows(app,'SELECT id FROM custom_field_definitions')[0][0];sign_in(c,'planner');add_part(c)
 token=revision(c)
 assert receive(c,custom_fields_revision=token,**{'custom_'+str(fid):'NaN'}).status_code==400
 rid=receive(c,custom_fields_revision=token).location.split('/')[-1]
 sign_in(c,'admin');assert define(c,'Heat number','number',field_id=fid,version='1',active='0').status_code==302
 assert define(c,'Heat number','number',field_id=fid,version='1').status_code==409
 sign_in(c,'planner')
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|dict(version=rv(app,rid),reason='Retired field',custom_fields_revision=token))).status_code==409
 token=revision(c,f'/requests/{rid}/edit')
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|dict(version=rv(app,rid),reason='Retired field',custom_fields_revision=token))).status_code==302
 assert decision(app,c,rid).status_code==302
 data=json.loads(rows(app,'SELECT data FROM request_custom_fields')[0][0]);assert data[0]['retired'] is True


def test_configuration_permissions_and_tenant_isolation(app):
 c=admin(app);assert define(c).status_code==302
 with sqlite3.connect(app.config['DATABASE']) as conn:
  tenant=conn.execute("INSERT INTO tenants(slug,name) VALUES('other-config','Other')").lastrowid
  fid=conn.execute("INSERT INTO custom_field_definitions(tenant_id,label,kind,options) VALUES(?,'Private field','text','[]')",(tenant,)).lastrowid
 assert b'Private field' not in c.get('/admin/custom-fields').data
 assert define(c,'Private field','text',field_id=fid,version='1').status_code==404
 assert c.post('/admin/custom-fields',data={'label':'No CSRF'}).status_code==400
 sign_in(c,'planner');assert define(c).status_code==403
 assert b'Private field' not in c.get('/requests/new').data
