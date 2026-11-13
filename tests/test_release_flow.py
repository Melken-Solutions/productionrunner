from test_workflow import app, post, rows, sign_in
from test_request_job_plan import setup, payload
from test_intake import receive, decision, convert, rv
from test_plan_templates import publish


def test_guided_review_and_existing_guards(app):
    c,plan=setup(app)
    r=receive(c,**payload(plan),next_action='release',save_template='yes')
    assert '/release-review?template=yes' in r.location
    rid=r.location.split('/')[2]
    page=c.get(r.location)
    assert page.status_code==200
    assert b'Approve &amp; release to floor' in page.data
    assert b'Complete these before release' not in page.data
    assert c.get(f'/requests/{rid}/release-state').json['status']=='preparing'
    assert decision(app,c,rid).status_code==302
    assert publish(app,c,rid).status_code==302
    assert convert(app,c,rid,tracking='lot',lot_size='1').status_code==302
    state=c.get(f'/requests/{rid}/release-state').json
    tid=state['ticket']['id']
    assert post(c,f'/tickets/{tid}/release',version=1,reason='Reviewed',plan_review='yes').status_code==302
    assert rows(app,'SELECT released FROM ticket_dispatch')[0][0]==1
    assert len(rows(app,'SELECT * FROM part_plan_templates'))==1
    assert c.get(f'/requests/{rid}/release-review').status_code==409


def test_incomplete_plan_and_role_scope(app):
    c,plan=setup(app);plan['steps'][0]['criteria']=''
    rid=receive(c,**payload(plan)).location.split('/')[-1]
    page=c.get(f'/requests/{rid}/release-review')
    assert page.status_code==200 and b'Complete these before release' in page.data
    assert b'<button disabled>' in page.data
    assert decision(app,c,rid).status_code==409
    sign_in(c,'operator')
    assert c.get(f'/requests/{rid}/release-review').status_code==403
    assert c.get(f'/requests/{rid}/release-state').status_code==403
    sign_in(c,'planner')
    assert c.get('/requests/not-this-org/release-state').status_code==404
