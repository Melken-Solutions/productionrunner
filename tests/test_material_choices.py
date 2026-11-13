import sqlite3
from test_workflow import app,sign_in,post,rows
from test_materials import STOCK


def admin(app):
 c=app.test_client();sign_in(c,'admin');return c


def add(c,field,value,metal=''):
 return post(c,'/admin/materials/choices',field=field,value=value,metal=metal,reason='Approve vocabulary')


def test_choices_required_generated_name_and_metal_scoping(app):
 c=admin(app)
 assert post(c,'/admin/materials',**STOCK,_provision_choices=False).status_code==400
 for field,value,metal in [('metal','Brass',''),('gauge','14 ga','Brass'),('finish','Satin','Brass'),('required_service','Clear coating','')]:assert add(c,field,value,metal).status_code==302
 assert post(c,'/admin/materials',**(STOCK|dict(name='')),_provision_choices=False).status_code==302
 assert rows(app,'SELECT name FROM materials')[0][0]=='14 ga · Brass · Satin · Clear coating'
 assert add(c,'metal','Stainless steel').status_code==302
 assert post(c,'/admin/materials',**(STOCK|dict(name='Wrong scope',metal='Stainless steel')),_provision_choices=False).status_code==400
 assert add(c,'thickness_mm','-1','Brass').status_code==400
 assert add(c,'gauge','20 ga','Unknown metal').status_code==400
 page=c.get('/admin/materials').data
 assert b'<select name="metal"' in page and b'<select name="finish"' in page
 assert b'name="metal" value=' not in page


def test_retirement_preserves_existing_specs_and_blocks_new_use(app):
 c=admin(app);assert post(c,'/admin/materials',**STOCK).status_code==302
 choice=rows(app,"SELECT * FROM material_choices WHERE field='finish'")[0]
 assert post(c,'/admin/materials/choices',choice_id=choice['id'],version='1',active='0',reason='Retire finish').status_code==302
 assert post(c,'/admin/materials',**(STOCK|dict(name='New stock')),_provision_choices=False).status_code==400
 material=rows(app,'SELECT * FROM materials')[0]
 assert post(c,'/admin/materials',**(STOCK|dict(material_id=material['id'],version='1',handling='Protect face')),_provision_choices=False).status_code==302
 assert post(c,'/admin/materials/choices',choice_id=choice['id'],version='1',active='1',reason='Stale').status_code==409
 assert rows(app,'SELECT finish FROM materials')[0][0]=='Satin'
 assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
 assert rows(app,'SELECT active FROM material_choices WHERE id=?',(choice['id'],))[0][0]==0


def test_choices_tenant_roles_csrf_and_audit(app):
 c=admin(app);assert add(c,'metal','Brass').status_code==302
 cid=rows(app,'SELECT id FROM material_choices')[0][0]
 assert c.post('/admin/materials/choices',data={'field':'metal','value':'Steel'}).status_code==400
 sign_in(c,'planner');assert add(c,'metal','Steel').status_code==403
 sign_in(c,'admin')
 with sqlite3.connect(app.config['DATABASE']) as conn:
  tenant=conn.execute("INSERT INTO tenants(slug,name) VALUES('other-choice','Other')").lastrowid
  other=conn.execute("INSERT INTO material_choices(tenant_id,field,value) VALUES(?,'metal','Secret metal')",(tenant,)).lastrowid
 assert b'Secret metal' not in c.get('/admin/materials/choices').data
 assert post(c,'/admin/materials/choices',choice_id=other,version='1',active='0',reason='Wrong organization').status_code==404
 assert post(c,'/admin/materials',**(STOCK|dict(metal='Secret metal')),_provision_choices=False).status_code==400
 assert rows(app,"SELECT * FROM auth_events WHERE action='material_choice_changed'")
