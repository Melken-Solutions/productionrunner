import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows,create_ticket
from test_intake import receive,decision,rv,FIELDS
from test_parts import add_part
from test_request_outside import PLAN

STOCK=dict(name='14 ga Brass satin',metal='Brass',gauge='14 ga',finish='Satin',required_service='Clear coating',reason='Approved stock')

def setup(app,**changes):
    c=app.test_client();sign_in(c,'admin')
    assert post(c,'/admin/materials',**(STOCK|changes)).status_code==302
    mid=rows(app,'SELECT id FROM materials')[0][0]
    sign_in(c,'planner');add_part(c)
    return c,str(mid)

def test_required_coating_and_immutable_job_snapshot(app):
    c,mid=setup(app)
    assert b'Sheet material' in c.get('/requests/new').data
    rid=receive(c,material_id=mid).location.split('/')[-1]
    assert decision(app,c,rid).status_code==409
    data=FIELDS|PLAN|dict(material_id=mid,outside_service='Clear coating',version=rv(app,rid),reason='Vendor added',planned_sheets='1.5',material_notes='Protect visible face')
    assert post(c,'/requests/'+rid+'/edit',**data).status_code==302
    assert decision(app,c,rid).status_code==302
    assert create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='1').status_code==409
    assert not rows(app,'SELECT * FROM tickets')
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='2')
    assert response.status_code==302
    assert b'Protect visible face' in c.get(response.location).data
    assert b'14 ga Brass satin' in c.get(response.location+'/traveler').data
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM ticket_materials')
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM request_materials')
    assert not rows(app,'PRAGMA foreign_key_check')

def test_catalog_updates_require_review_preserve_existing_spec(app):
    c,mid=setup(app,required_service='')
    rid=receive(c,material_id=mid).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    sign_in(c,'admin')
    assert post(c,'/admin/materials',**(STOCK|dict(required_service='',material_id=mid,version='1',handling='Use protective film'))).status_code==302
    assert post(c,'/admin/materials',**(STOCK|dict(material_id=mid,version='1'))).status_code==409
    sign_in(c,'planner')
    assert create_ticket(c,request_id=rid,request_version=rv(app,rid)).status_code==409
    assert post(c,'/requests/'+rid+'/edit',**(FIELDS|dict(material_id=mid,version=rv(app,rid),reason='Review changed stock'))).status_code==302
    assert decision(app,c,rid).status_code==302
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid));assert response.status_code==302
    sign_in(c,'admin')
    assert post(c,'/admin/materials',**(STOCK|dict(material_id=mid,version='2',active='0'))).status_code==302
    assert b'Use protective film' in c.get(response.location).data
    assert b'14 ga Brass satin' in c.get('/admin/materials').data

def test_material_roles_tenants_validation_and_atomicity(app):
    c,mid=setup(app,required_service='')
    assert c.get('/admin/materials').status_code==403
    assert receive(c).status_code==400
    for value in ('nan','inf','-1','0','100001'):
        assert receive(c,material_id=mid,planned_sheets=value).status_code==400
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships SELECT 2,id,'planner' FROM users WHERE username='planner'")
    sign_in(c,'planner','beta')
    assert receive(c,material_id=mid).status_code==400
    assert b'14 ga Brass satin' not in c.get('/requests/new').data
    sign_in(c,'planner')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_material_audit BEFORE INSERT ON intake_events BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):receive(c,material_id=mid)
    assert not rows(app,'SELECT * FROM request_materials')
    assert not rows(app,'SELECT * FROM intake_requests')

def test_admin_stations_are_available_and_assigned_in_route_order(app):
    c=app.test_client();sign_in(c,'admin')
    for station in ('Laser 1','Laser 2','Timesaver'):
        assert post(c,'/admin/stations',name=station,reason='Configure test shop').status_code==302
    sign_in(c,'planner');add_part(c)
    rid=receive(c).location.split('/')[-1];assert decision(app,c,rid).status_code==302
    page=c.get('/tickets/new',query_string={'request_id':rid})
    assert b'Laser 1' in page.data and b'Laser 2' in page.data and b'Timesaver' in page.data
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid),route='Laser 2 | Cut to drawing\nTimesaver | Deburr',legacy=False)
    assert response.status_code==302
    assigned=rows(app,'SELECT o.sequence,s.name FROM operation_assignments a JOIN operations o ON o.id=a.operation_id JOIN stations s ON s.id=a.station_id ORDER BY o.sequence')
    assert [tuple(r) for r in assigned]==[(1,'Laser 2'),(2,'Timesaver')]
    ticket=rows(app,'SELECT * FROM tickets')[0]
    from test_dispatch import release
    assert release(app,c,response.location).status_code==302


def test_required_vendor_step_cannot_be_removed_or_changed(app):
    c,mid=setup(app)
    rid=receive(c,material_id=mid,**(PLAN|dict(outside_service='Clear coating'))).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid),outside_sequence='2',legacy=False)
    assert response.status_code==302
    ticket=rows(app,'SELECT * FROM tickets')[0];op=rows(app,'SELECT * FROM outside_steps')[0]
    url=response.location+'/outside/'+str(op['operation_id'])+'/plan'
    assert post(c,url,version=str(ticket['version']),mode='internal',reason='Try remove').status_code==409
    assert post(c,url,version=str(ticket['version']),mode='outside',vendor='Other',service='Powder coat',instructions='Coat',inspection_requirements='Check',due_date='2026-12-01',reason='Try replace').status_code==409
    assert rows(app,'SELECT service FROM outside_steps')[0][0]=='Clear coating'
