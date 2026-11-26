import json
import sqlite3
import pytest
from test_workflow import app, post, rows, sign_in
from test_request_job_plan import setup, payload
from test_intake import receive, decision, rv, FIELDS, convert
from test_parts import add_part


def approved(app):
    client,plan=setup(app)
    rid=receive(client,**payload(plan)).location.split('/')[-1]
    assert decision(app,client,rid).status_code==302
    part=rows(app,'SELECT part_id FROM intake_requests WHERE id=?',(rid,))[0][0]
    return client,plan,rid,part


def publish(app,c,rid,revision=0,**kw):
    return post(c,f'/requests/{rid}/plan-template',**(dict(version=rv(app,rid),template_version=str(revision),reason='Approved standard manufacturing process',confirm='yes')|kw))


def test_repeat_request_loads_plan_with_provenance_and_independent_snapshot(app):
    c,plan,rid,part=approved(app)
    assert publish(app,c,rid).status_code==302
    t=c.get(f'/parts/{part}/plan-template').json['template']
    assert t['plan']==plan and t['revision']==1
    assert b'Manufacturing template' in c.get('/parts/'+part).data
    assert b'Approved defaults' in c.get(f'/parts/{part}/plan-templates').data
    assert b'plan-templates.js' in c.get('/requests/new').data
    copy=receive(c,quantity='7',**payload(dict(t['plan'],template_id=t['id'])))
    new=copy.location.split('/')[-1]
    assert rows(app,'SELECT status FROM intake_requests WHERE id=?',(new,))[0][0]=='preparing'
    assert decision(app,c,new).status_code==302
    ticket=convert(app,c,new)
    assert ticket.status_code==302
    snapshot=json.loads(rows(app,'SELECT data FROM job_plans')[0][0])
    assert snapshot['template_id']==t['id']
    assert publish(app,c,rid,1).status_code==302
    assert json.loads(rows(app,'SELECT data FROM job_plans')[0][0])==snapshot
    assert json.loads(rows(app,'SELECT data FROM request_job_plans WHERE request_id=?',(new,))[0][0])['template_id']==t['id']


def test_publication_requires_approval_confirmation_role_and_current_versions(app):
    c,plan=setup(app)
    rid=receive(c,**payload(plan)).location.split('/')[-1]
    assert publish(app,c,rid).status_code==409
    assert decision(app,c,rid).status_code==302
    assert publish(app,c,rid,confirm='').status_code==400
    assert publish(app,c,rid,version='999').status_code==409
    assert publish(app,c,rid).status_code==302
    assert publish(app,c,rid).status_code==409
    sign_in(c,'operator')
    assert publish(app,c,rid,1).status_code==403
    assert len(rows(app,'SELECT * FROM part_plan_templates'))==1


def test_templates_do_not_cross_part_revision_or_tenant(app):
    c,plan,rid,part=approved(app);assert publish(app,c,rid).status_code==302
    ident=rows(app,'SELECT id FROM part_plan_templates')[0][0]
    assert receive(c,revision='B',**payload(dict(plan,template_id=ident))).status_code==400
    other=add_part(c,revision='B').location.split('/')[-1]
    assert c.get(f'/parts/{other}/plan-template').json['template'] is None
    # Same username can be a planner in another organization; tenant lookup still applies.
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code==0
    sign_in(c,'planner',tenant='beta')
    assert c.get(f'/parts/{part}/plan-template').status_code==404
    assert c.get(f'/parts/{part}/plan-templates').status_code==404
    assert publish(app,c,rid,1).status_code==404


def test_withdrawal_blocks_new_reuse_and_preserves_existing_request(app):
    c,plan,rid,part=approved(app);assert publish(app,c,rid).status_code==302
    t=c.get(f'/parts/{part}/plan-template').json['template']
    new=receive(c,**payload(dict(plan,template_id=t['id']))).location.split('/')[-1]
    assert post(c,f'/parts/{part}/plan-template/withdraw',version='1',reason='Process needs review').status_code==302
    assert c.get(f'/parts/{part}/plan-template').json['template'] is None
    assert receive(c,**payload(dict(plan,template_id=t['id']))).status_code==409
    assert post(c,f'/requests/{new}/edit',**(FIELDS|payload(dict(plan,template_id=t['id']))|dict(version=rv(app,new),reason='Job-only review'))).status_code==302
    assert len(rows(app,'SELECT * FROM part_plan_templates'))==2
    assert publish(app,c,rid,2).status_code==302
    assert c.get(f'/parts/{part}/plan-template').json['template']['revision']==3


def test_inactive_station_blocks_loading_and_publication(app):
    c,plan,rid,part=approved(app);assert publish(app,c,rid).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE stations SET active=0 WHERE id=?',(plan['steps'][0]['station_id'],))
    assert c.get(f'/parts/{part}/plan-template').status_code==409
    assert publish(app,c,rid,1).status_code==409


def test_template_rows_are_immutable(app):
    c,plan,rid,part=approved(app);assert publish(app,c,rid).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE part_plan_templates SET reason='rewrite'")
    with sqlite3.connect(app.config['DATABASE']) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM part_plan_templates')


def test_outside_template_excludes_dates_order_and_quantity(app):
    c,plan=setup(app)
    plan['steps'].append(dict(name='Coat',instructions='Clear coat',criteria='Coverage',station_id=0,outside=True,first_piece=False))
    outside=dict(outside_required='yes',outside_vendor='Coater',outside_service='Clear coating',outside_instructions='Protect threads',outside_inspection_requirements='Check coverage',outside_due_date='2026-12-01',outside_reference='PO-PRIVATE')
    rid=receive(c,**payload(plan),**outside).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert publish(app,c,rid).status_code==302
    stored=json.loads(rows(app,'SELECT data FROM part_plan_templates')[0][0])
    assert stored['outside']['instructions']=='Protect threads'
    assert 'due_date' not in stored['outside'] and 'reference' not in stored['outside']
    assert 'quantity' not in stored and 'PO-PRIVATE' not in json.dumps(stored)


def test_material_defaults_and_changed_catalog_reject_stale_reuse(app):
    c,plan=setup(app)
    sign_in(c,'admin')
    stock=dict(name='14 ga SS Satin',metal='SS',gauge='14 ga',finish='Satin',required_service='',reason='Approved stock')
    assert post(c,'/admin/materials',**stock).status_code==302
    mid=rows(app,'SELECT id FROM materials')[0][0]
    sign_in(c,'planner')
    rid=receive(c,material_id=str(mid),planned_sheets='2.5',material_notes='Grain vertical',**payload(plan)).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert publish(app,c,rid).status_code==302
    part=rows(app,'SELECT part_id FROM intake_requests WHERE id=?',(rid,))[0][0]
    t=c.get(f'/parts/{part}/plan-template').json['template']
    assert t['material']['notes']=='Grain vertical'
    assert 'planned_sheets' not in t['material']
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE materials SET version=version+1 WHERE id=?',(mid,))
    assert c.get(f'/parts/{part}/plan-template').status_code==409
    assert receive(c,material_id=str(mid),**payload(dict(plan,template_id=t['id']))).status_code==409
    assert len(rows(app,'SELECT * FROM intake_requests'))==1


def test_concurrent_publish_creates_one_revision(app):
    from concurrent.futures import ThreadPoolExecutor
    c,plan,rid,part=approved(app)
    other=app.test_client();sign_in(other,'planner')
    data=dict(version=rv(app,rid),template_version='0',reason='Approved repeat process',confirm='yes')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda client:post(client,f'/requests/{rid}/plan-template',**data).status_code,[c,other]))
    assert sorted(results)==[302,409]
    assert len(rows(app,'SELECT * FROM part_plan_templates'))==1
