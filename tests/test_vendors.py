import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows,create_ticket
from test_intake import receive,decision,rv,FIELDS
from test_parts import add_part
from test_request_outside import PLAN

VENDOR=dict(name='Finish Shop',services='Clear coating\nPowder coating',reason='Approve supplier')

def configured(app):
 c=app.test_client();sign_in(c,'admin')
 assert post(c,'/admin/vendors',**VENDOR).status_code==302
 vid=str(rows(app,'SELECT id FROM vendors')[0][0])
 sign_in(c,'planner');add_part(c)
 return c,vid

def test_supported_services_approval_conversion_and_history(app):
 c,vid=configured(app)
 assert b'Approved outside vendor' in c.get('/requests/new').data
 for change in ({'outside_vendor':'Unknown'},{'outside_service':'Welding'},{'outside_service':'Powder coat'}):
  assert receive(c,**(PLAN|change)).status_code==409
 assert not rows(app,'SELECT * FROM intake_requests')
 rid=receive(c,**(PLAN|dict(outside_service='Clear coating'))).location.split('/')[-1]
 assert decision(app,c,rid).status_code==302
 response=create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='2',legacy=False)
 assert response.status_code==302
 assert b'Clear coating' in c.get(response.location+'/outside').data
 sign_in(c,'admin')
 assert post(c,'/admin/vendors',**(VENDOR|dict(vendor_id=vid,version='1',active='0'))).status_code==302
 assert rows(app,'SELECT vendor,service FROM outside_steps')[0][:]==('Finish Shop','Clear coating')
 sign_in(c,'planner')
 assert post(c,response.location+'/release',version='1',reason='Try release').status_code==409
 assert not rows(app,'PRAGMA foreign_key_check')

def test_catalog_change_rechecks_pending_approval_and_conversion(app):
 c,vid=configured(app)
 rid=receive(c,**(PLAN|dict(outside_service='Clear coating'))).location.split('/')[-1]
 assert decision(app,c,rid).status_code==302
 sign_in(c,'admin')
 assert post(c,'/admin/vendors',**(VENDOR|dict(vendor_id=vid,version='1',services='Powder coating'))).status_code==302
 assert post(c,'/admin/vendors',**(VENDOR|dict(vendor_id=vid,version='1'))).status_code==409
 sign_in(c,'planner')
 assert create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='2').status_code==409
 assert not rows(app,'SELECT * FROM tickets')
 assert post(c,'/requests/'+rid+'/edit',**(FIELDS|PLAN|dict(outside_service='Powder coating',version=rv(app,rid),reason='Change process'))).status_code==302
 assert decision(app,c,rid).status_code==302

def test_vendor_access_validation_and_audit_atomicity(app):
 c,vid=configured(app)
 assert c.get('/admin/vendors').status_code==403
 assert post(c,'/admin/vendors',**VENDOR).status_code==403
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
  conn.execute("INSERT INTO memberships SELECT 2,id,'admin' FROM users WHERE username='admin'")
 sign_in(c,'admin','beta')
 assert b'Finish Shop' not in c.get('/admin/vendors').data
 assert post(c,'/admin/vendors',**(VENDOR|dict(vendor_id=vid,version='1'))).status_code==404
 sign_in(c,'admin')
 assert c.post('/admin/vendors',data=VENDOR).status_code==400
 for services in ('','Clear coating\nclear coating','X'*201):
  assert post(c,'/admin/vendors',**(VENDOR|dict(services=services))).status_code==400
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_vendor_audit BEFORE INSERT ON auth_events WHEN NEW.action='vendor_updated' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):post(c,'/admin/vendors',**(VENDOR|dict(vendor_id=vid,version='1',active='0')))
 assert rows(app,'SELECT active,version FROM vendors')[0][:]==(1,1)

def test_direct_ticket_plan_cannot_bypass_catalog(app):
 c,vid=configured(app)
 response=create_ticket(c,legacy=False)
 assert response.status_code==302
 op=rows(app,'SELECT id FROM operations ORDER BY sequence')[0][0]
 data=dict(version='1',vendor='Finish Shop',service='Welding',instructions='Weld',inspection_requirements='Inspect',due_date='2026-12-01')
 assert post(c,response.location+f'/outside/{op}/plan',**data).status_code==409
 assert not rows(app,'SELECT * FROM outside_steps')
 assert post(c,response.location+f'/outside/{op}/plan',**(data|dict(service='Clear coating'))).status_code==302
