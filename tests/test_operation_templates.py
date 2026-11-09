import json,sqlite3
import pytest
from test_workflow import app,sign_in,post,rows
from productionrunner.operation_templates import current
from productionrunner.request_job_plan import parse,save,get
from test_intake import receive


def template(c,**kw):
    return post(c,'/admin/operation-templates',**(dict(key='laser',version='0',action='save',name='Laser cut',kind='production',instructions='Use the released drawing and approved laser setup.',criteria='Measure specified critical features.',station_id='',reason='Approved shop defaults')|kw))


def test_admin_versions_retirement_and_planner_access(app):
    c=app.test_client();sign_in(c,'planner')
    assert template(c).status_code==403
    assert b'Laser cut' in c.get('/requests/new').data
    sign_in(c,'admin');assert template(c).status_code==302
    assert template(c).status_code==409
    assert template(c,action='retire',version='1').status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory=sqlite3.Row
        assert 'laser' not in [t['key'] for t in current(conn,1)]
        with pytest.raises(sqlite3.IntegrityError):conn.execute('DELETE FROM operation_templates')
    assert template(c,action='restore',version='2').status_code==302
    assert len(rows(app,'SELECT * FROM operation_templates'))==3
    assert c.get('/admin/operation-templates').status_code==200


def test_tenant_station_validation_and_isolation(app):
    c=app.test_client();sign_in(c,'admin')
    assert template(c,station_id='999').status_code==400
    assert template(c).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory=sqlite3.Row
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        uid=conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        conn.execute("INSERT INTO memberships VALUES(2,?,'admin')",(uid,))
        assert next(t for t in current(conn,2) if t['key']=='laser')['version']==0
    sign_in(c,'admin',tenant='beta');assert template(c,version='1').status_code==409


def test_request_snapshot_does_not_follow_template_edits(app):
    c=app.test_client();sign_in(c,'admin');assert template(c).status_code==302
    sign_in(c,'planner');rid=receive(c).location.split('/')[-1]
    plan=dict(steps=[dict(name='Laser cut',instructions='Original copied instructions',criteria='Original checks',station_id=0,operation_template='laser',operation_template_version=1)])
    cleaned=parse(dict(request_plan_present='yes',request_plan_json=json.dumps(plan)))
    with sqlite3.connect(app.config['DATABASE']) as conn:save(conn,1,rid,cleaned)
    sign_in(c,'admin');assert template(c,version='1',instructions='Revised template instructions').status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory=sqlite3.Row
        stored=get(conn,1,rid)
        assert stored['steps'][0]['instructions']=='Original copied instructions'
        assert stored['steps'][0]['operation_template_version']==1
        bad=dict(cleaned,steps=[dict(cleaned['steps'][0],operation_template='not-this-tenant')])
        from werkzeug.exceptions import BadRequest
        with pytest.raises(BadRequest):save(conn,1,rid,bad)
