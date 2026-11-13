import sqlite3
from test_workflow import app,sign_in,post,rows,create_ticket
from test_intake import receive,decision,rv
from test_parts import add_part


def test_planner_has_one_entry_and_old_link_redirects(app):
    c=app.test_client();sign_in(c,'planner')
    page=c.get('/')
    assert b'New production request' in page.data
    assert b'>Create ticket</a>' not in page.data and b'>Receive request</a>' not in page.data
    assert c.get('/tickets/new').location=='/requests/new'
    assert b'New production request' in c.get('/requests/new').data
    assert create_ticket(c,auto_request=False).status_code==409
    assert rows(app,'SELECT * FROM tickets')==[]


def test_old_disabled_setting_cannot_bypass_request_and_new_snapshot_is_required(app):
    c=app.test_client();sign_in(c,'planner')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO workflow_defaults VALUES(1,0,'[]',1)")
    assert create_ticket(c,auto_request=False,workflow_version='1').status_code==409
    response=create_ticket(c,workflow_version='1')
    assert response.status_code==302
    assert rows(app,'SELECT require_intake FROM ticket_workflow')[0][0]==1
    # Reading effective policy does not rewrite historical configuration.
    assert rows(app,'SELECT require_intake FROM workflow_defaults')[0][0]==0
    sign_in(c,'admin')
    assert b'name="require_intake"' not in c.get('/admin/workflow').data
    assert post(c,'/admin/workflow',action='save',version='1',require_intake='0',pack_checks='Verify packing',depart_checks='',reason='Add checkpoint').status_code==302
    assert rows(app,'SELECT require_intake FROM workflow_defaults')[0][0]==1


def test_approved_request_is_single_use_and_only_source_of_identity(app):
    c=app.test_client();sign_in(c,'planner');add_part(c)
    rid=receive(c).location.split('/')[-1]
    assert b'Create JobRunner from approved request' not in c.get('/requests/'+rid).data
    assert c.get('/tickets/new',query_string={'request_id':rid}).status_code==409
    assert decision(app,c,rid).status_code==302
    assert b'Create JobRunner from approved request' in c.get('/requests/'+rid).data
    response=create_ticket(c,request_id=rid,request_version=rv(app,rid),pn='FORGED',quantity='999',revision='ZZ')
    assert response.status_code==302
    t=rows(app,'SELECT * FROM tickets')[0]
    assert (t['pn'],t['revision'],t['quantity'])==('PN-001','A',2)
    assert rows(app,'SELECT ticket_id FROM intake_requests')[0][0]==t['id']
    assert create_ticket(c,request_id=rid,request_version=rv(app,rid)).status_code==409
    assert c.get(response.location).status_code==200


def test_repair_creation_also_requires_request_without_configured_defaults(app):
    from test_return_repairs import returned_job,repair
    c,tid,rid=returned_job(app)
    assert not rows(app,'SELECT * FROM workflow_defaults')
    assert repair(app,c,tid,rid,auto_request=False).status_code==409
    assert not rows(app,'SELECT * FROM return_repairs')
    assert len(rows(app,'SELECT * FROM tickets'))==1
