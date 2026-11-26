import json
from unittest.mock import patch
from test_workflow import app,post,rows,sign_in
from test_work_tracking import setup,act,finish,v
from productionrunner.database import connect
from productionrunner.station_queue import travelers


def test_display_survives_old_cookie_but_operator_does_not(app):
    with connect(app.config['DATABASE']) as db:db.execute("UPDATE memberships SET roles='display' WHERE user_id=4")
    for username,persistent in [('viewer',True),('operator',False)]:
        c=app.test_client();sign_in(c,username)
        with c.session_transaction() as s:data=dict(s)
        serializer=app.session_interface.get_signing_serializer(app)
        import time
        with patch('itsdangerous.timed.time.time',return_value=time.time()-86400*14):old=serializer.dumps(data)
        c.set_cookie('session',old)
        response=c.get('/tv/data')
        assert (response.status_code==200)==persistent
        if persistent:
            assert 'Expires=' in response.headers['Set-Cookie']
            with connect(app.config['DATABASE']) as db:db.execute("UPDATE memberships SET roles='viewer' WHERE user_id=4")
            assert c.get('/tv/data').status_code==302


def test_unassigned_pickup_and_actual_station_snapshot(app):
    c,tid,code,ops=setup(app)
    with connect(app.config['DATABASE']) as db:
        db.execute('DELETE FROM operation_assignments WHERE operation_id=?',(ops[0]['id'],))
    c.get('/shop-floor?station=2')
    assert c.get('/shop-floor/work/'+code).location=='/work/'+code
    assert b'Pick up at station' in c.get('/work/'+code).data
    assert act(app,c,code,'start',station_id='99999').status_code==400
    assert act(app,c,code,'start',station_id='2').status_code==302
    with connect(app.config['DATABASE']) as db:
        assert any(r['status']=='running' for r in travelers(db,1,2))
        assert not any(r['operation_id']==ops[0]['id'] for r in travelers(db,1,1))
    assert act(app,c,code,'pause').status_code==302
    assert act(app,c,code,'resume').status_code==302
    admin=app.test_client();sign_in(admin,'admin')
    assert post(admin,'/admin/stations/2',version='1',name='Assembly',active='0',reason='Deactivation attempt').status_code==409
    assert finish(app,c,code,ops[0],verification_result='checks_passed',plan_evidence='').status_code==302
    snap=rows(app,'SELECT * FROM completion_assignments')[0]
    assert snap['station_id']==2 and snap['actual_user_id']==2
    assert snap['assigned_user_id'] is None
    assert not rows(app,'SELECT * FROM operation_assignments WHERE operation_id=?',(ops[0]['id'],))
    evidence=json.loads(rows(app,"SELECT detail FROM events WHERE action='job_plan_check_recorded'")[0][0])
    assert 'operator confirmed' in evidence['evidence']


def test_verification_choice_and_required_measurement_details(app):
    c,tid,code,ops=setup(app);assert act(app,c,code,'start').status_code==302
    page=c.get('/work/'+code).data
    assert b'<select name="completed_quantity"' in page and b'name="verification_result"' in page
    for choice in ['','made_up','measurements','recorded','other']:
        assert finish(app,c,code,ops[0],verification_result=choice,plan_evidence='').status_code==400
    assert not rows(app,'SELECT * FROM completion_assignments')
    assert finish(app,c,code,ops[0],verification_result='measurements',plan_evidence='25.02 mm; caliper C-12').status_code==302


def test_planner_can_release_explicitly_authorized_unassigned_work(app):
    from test_dispatch import setup_job,prepare_plan,version
    c,path,ops=setup_job(app);prepare_plan(app,c,path)
    assert post(c,path+'/release',version=version(app,path),reason='Approved flexible station pickup',plan_review='yes',allow_unassigned='yes').status_code==302
    sign_in(c,'operator');code=path.split('/')[-1]
    c.get('/shop-floor?station=1')
    assert c.get('/shop-floor/work/'+code).location=='/work/'+code
    assert act(app,c,code,'start',station_id='1').status_code==302
