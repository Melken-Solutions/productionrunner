from io import BytesIO
import sqlite3
from test_workflow import app, sign_in, post, rows
from test_intake import planner, receive, rv, decision, FIELDS
from test_parts import PDF, add_part


def uploads():
    return dict(drawing=(BytesIO(PDF),'part.pdf'),dxf=(BytesIO(b'0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF'),'part.dxf'))


def test_planner_creates_request_with_both_files(app):
    c=planner(app);r=receive(c,item_type='custom',**uploads())
    assert r.status_code==302
    assert len(rows(app,'SELECT * FROM part_revisions'))==1
    assert len(rows(app,'SELECT * FROM part_dxf'))==1
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
    assert rows(app,"SELECT action FROM intake_events WHERE action='documents_uploaded'")
    assert b'Drawing and DXF are available' in c.get(r.location).data


def test_request_upload_matches_part_and_invalidates_approval(app):
    c=planner(app);add_part(c);rid=receive(c).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert post(c,f'/requests/{rid}/documents',version=rv(app,rid),dxf=(BytesIO(b'DXF'),'p.dxf')).status_code==302
    r=rows(app,'SELECT * FROM intake_requests')[0]
    assert r['status']=='preparing' and r['approved_by'] is None
    assert len(rows(app,'SELECT * FROM part_revisions'))==1
    assert post(c,f'/requests/{rid}/documents',version=rv(app,rid),dxf=(BytesIO(b'changed'),'p.dxf')).status_code==409


def test_invalid_or_duplicate_upload_rolls_back_request(app):
    c=planner(app)
    assert receive(c,drawing=(BytesIO(b'not pdf'),'x.pdf')).status_code==400
    assert receive(c,dxf=(BytesIO(b'DXF'),'p.dxf')).status_code==400
    assert not rows(app,'SELECT * FROM intake_requests')
    add_part(c)
    assert receive(c,**uploads()).status_code==409
    assert not rows(app,'SELECT * FROM intake_requests')
    assert not rows(app,'SELECT * FROM part_dxf')


def test_document_upload_authorization_and_stale_request(app):
    c=planner(app);rid=receive(c).location.split('/')[-1]
    sign_in(c,'operator')
    assert post(c,f'/requests/{rid}/documents',version=rv(app,rid),**uploads()).status_code==403
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        uid=conn.execute("SELECT id FROM users WHERE username='planner'").fetchone()[0]
        conn.execute("INSERT INTO memberships VALUES(2,?,'planner')",(uid,))
    sign_in(c,'planner',tenant='beta')
    assert post(c,f'/requests/{rid}/documents',version=rv(app,rid),**uploads()).status_code==404
    sign_in(c,'planner')
    assert post(c,f'/requests/{rid}/documents',version='999',**uploads()).status_code==409
    assert not rows(app,'SELECT * FROM part_revisions')


def test_edit_upload_only_revokes_document_approval(app):
    c=planner(app);add_part(c);rid=receive(c).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert post(c,f'/requests/{rid}/edit',**FIELDS,version=rv(app,rid),reason='Add laser file',dxf=(BytesIO(b'DXF'),'p.dxf')).status_code==302
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
    assert len(rows(app,'SELECT * FROM part_dxf'))==1
