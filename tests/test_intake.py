import sqlite3
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app,sign_in,post,rows,create_ticket
from test_parts import add_part,PDF
from test_storage_management import report

FIELDS=dict(item_type='standard',source='erp',source_reference='SAP-ORDER-123',requester='Customer A',destination='Warehouse B',required_date='2026-12-01',pn='PN-001',revision='A',quantity='2',description='Make brackets per approved drawing')


def planner(app):
 c=app.test_client();sign_in(c,'planner');return c


def receive(c,**kw):
 return post(c,'/requests/new',**(FIELDS|kw))


def rv(app,rid):return str(rows(app,'SELECT version FROM intake_requests WHERE id=?',(rid,))[0][0])


def decision(app,c,rid,**kw):
 return post(c,f'/requests/{rid}/decision',**(dict(action='approve',version=rv(app,rid),reason='Reviewed required documents',confirm='yes')|kw))


def convert(app,c,rid,**kw):
 return create_ticket(c,legacy=False,request_id=rid,request_version=rv(app,rid),**kw)


def test_standard_request_approval_to_ticket_preserves_source_and_fixed_fields(app):
 c=planner(app);part=add_part(c).location.split('/')[-1]
 r=receive(c);assert r.status_code==302;rid=r.location.split('/')[-1]
 assert not rows(app,'SELECT * FROM tickets')
 assert b'Documents available' in c.get(r.location).data
 assert convert(app,c,rid).status_code==409
 assert decision(app,c,rid).status_code==302
 page=c.get('/tickets/new',query_string={'request_id':rid})
 assert page.status_code==200 and b'SAP-ORDER' not in page.data and rid.encode() in page.data
 ticket=convert(app,c,rid,pn='WRONG',revision='Z',quantity='99',part_revision_id='OTHER')
 assert ticket.status_code==302
 record=rows(app,'SELECT * FROM tickets')[0]
 assert (record['pn'],record['revision'],record['quantity'])==('PN-001','A',2)
 assert rows(app,'SELECT part_revision_id FROM ticket_drawings')[0][0]==part
 assert rows(app,'SELECT released FROM ticket_dispatch')[0][0]==0
 assert b'SAP-ORDER-123' in c.get(ticket.location).data
 assert b'Warehouse B' in c.get(ticket.location+'/delivery').data
 assert rows(app,'SELECT status FROM intake_requests')[0][0]=='ticketed'
 assert convert(app,c,rid).status_code==409
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|dict(version=rv(app,rid),reason='No rewrite'))).status_code==409
 assert not rows(app,'PRAGMA foreign_key_check')
 assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
 assert b'SAP-ORDER-123' in c.get(ticket.location).data


def test_custom_waits_for_pdf_and_dxf_then_approval(app):
 c=planner(app);rid=receive(c,item_type='custom').location.split('/')[-1]
 assert b'Waiting for drawings' in c.get('/requests/'+rid).data
 assert decision(app,c,rid).status_code==409
 part=add_part(c).location.split('/')[-1]
 assert decision(app,c,rid).status_code==409
 assert post(c,f'/parts/{part}/dxf/upload',dxf=(BytesIO(b'DXF'),'part.dxf')).status_code==302
 assert decision(app,c,rid,confirm='').status_code==400
 assert decision(app,c,rid).status_code==302
 audit=rows(app,"SELECT detail FROM intake_events WHERE action='documents_approved'")[0][0]
 assert 'pdf_sha256' in audit and 'dxf_sha256' in audit
 assert convert(app,c,rid).status_code==302


def test_external_standard_requires_ack_custom_rejects_link(app):
 c=planner(app);add_part(c,link='https://example.com/drawing.pdf')
 rid=receive(c).location.split('/')[-1]
 assert decision(app,c,rid).status_code==400
 assert decision(app,c,rid,external_ack='yes').status_code==302
 rid2=receive(c,item_type='custom').location.split('/')[-1]
 assert decision(app,c,rid2,external_ack='yes').status_code==409


def test_edit_invalidates_approval_stale_conversion_and_reopen(app):
 c=planner(app);part=add_part(c).location.split('/')[-1];rid=receive(c).location.split('/')[-1]
 decision(app,c,rid);old=rv(app,rid)
 assert post(c,f'/requests/{rid}/edit',**(FIELDS|dict(quantity='3',version=old,reason='Quantity correction'))).status_code==302
 assert rows(app,'SELECT status,approved_by,part_id FROM intake_requests')[0][:]==('preparing',None,None)
 assert decision(app,c,rid,version=old).status_code==409
 assert decision(app,c,rid).status_code==302
 assert create_ticket(c,request_id=rid,request_version=old).status_code==409
 # A newly attached DXF changes the reviewed package and requires reapproval.
 post(c,f'/parts/{part}/dxf/upload',dxf=(BytesIO(b'DXF'),'part.dxf'))
 assert convert(app,c,rid).status_code==409
 assert decision(app,c,rid,action='reopen').status_code==302
 assert decision(app,c,rid).status_code==302
 assert convert(app,c,rid).status_code==302


def test_permissions_owner_isolation_csrf_and_revocation(app):
 c=planner(app);add_part(c);rid=receive(c).location.split('/')[-1]
 assert c.post(f'/requests/{rid}/decision',data={'action':'approve'}).status_code==400
 for name in ('operator','inspector','viewer'):
  sign_in(c,name);assert c.get('/requests/'+rid).status_code==200
  assert receive(c).status_code==403
  assert decision(app,c,rid).status_code==403
 sign_in(c,'admin');assert decision(app,c,rid).status_code==302
 assert convert(app,c,rid).status_code==403  # Existing planner-only ticket policy remains.
 runner=app.test_cli_runner();runner.invoke(args=['add-tenant','beta','--name','Beta']);runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner'])
 sign_in(c,'planner','beta')
 assert rid.encode() not in c.get('/requests?view=all').data
 assert c.get('/requests/'+rid).status_code==404
 assert decision(app,c,rid).status_code==404
 assert c.get('/tickets/new',query_string={'request_id':rid}).status_code==404
 sign_in(c,'planner')
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute("UPDATE memberships SET roles='viewer' WHERE tenant_id=1 AND user_id=(SELECT id FROM users WHERE username='planner')")
 assert convert(app,c,rid).status_code==403


def test_cancellation_and_retention_of_open_request(app):
 c=planner(app);add_part(c);rid=receive(c).location.split('/')[-1]
 assert all(f['status']=='Protected by open manufacturing request' for f in report(app)['files'])
 assert decision(app,c,rid,action='cancel').status_code==302
 assert rid.encode() not in c.get('/requests').data
 assert rid.encode() in c.get('/requests?view=all').data
 assert convert(app,c,rid).status_code==409
 assert all(f['status']=='Eligible in preview' for f in report(app)['files'])


@pytest.mark.parametrize('changes',[dict(pn='two parts'),dict(revision=''),dict(quantity='0'),dict(source='unknown'),dict(source_reference=''),dict(item_type='bad'),dict(required_date='2026-02-30'),dict(description='')])
def test_validation_creates_nothing(app,changes):
 c=planner(app);assert receive(c,**changes).status_code==400
 assert not rows(app,'SELECT * FROM intake_requests')
 assert not rows(app,'SELECT * FROM intake_events')


def test_atomic_conversion_concurrency_and_failed_audit(app):
 c=planner(app);add_part(c);rid=receive(c).location.split('/')[-1];decision(app,c,rid)
 with sqlite3.connect(app.config['DATABASE']) as conn:
  conn.execute("CREATE TRIGGER fail_link BEFORE INSERT ON events WHEN NEW.action='request_linked' BEGIN SELECT RAISE(ABORT,'test'); END")
 with pytest.raises(sqlite3.IntegrityError):convert(app,c,rid)
 assert not rows(app,'SELECT * FROM tickets')
 assert rows(app,'SELECT status FROM intake_requests')[0][0]=='approved'
 with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_link')
 c2=planner(app);v=rv(app,rid)
 with ThreadPoolExecutor(max_workers=2) as pool:
  results=list(pool.map(lambda client:create_ticket(client,legacy=False,request_id=rid,request_version=v).status_code,[c,c2]))
 assert sorted(results)==[302,409]
 assert len(rows(app,'SELECT * FROM tickets'))==1
 with sqlite3.connect(app.config['DATABASE']) as conn:
  for sql in ('DELETE FROM intake_requests','UPDATE intake_requests SET quantity=8','DELETE FROM intake_events','UPDATE intake_events SET detail=\'changed\''):
   with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
