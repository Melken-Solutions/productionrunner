from test_workflow import app,sign_in,rows,post
from test_work_tracking import setup,act,v


def test_operator_lands_on_station_workspace_and_filter_persists(app):
    c,tid,code,ops=setup(app)
    response=post(c,'/login',username='operator',tenant='alpha',password='test-password-123')
    assert response.location=='/shop-floor'
    station=rows(app,'SELECT station_id FROM operation_assignments LIMIT 1')[0][0]
    response=c.get('/shop-floor?station='+str(station))
    assert response.status_code==200 and tid.encode() in response.data
    assert b'Your station jobs' in c.get('/shop-floor').data
    with c.session_transaction() as session:assert session['shop_station_1']==str(station)
    assert c.get('/shop-floor?station=99999').status_code==404
    assert c.get('/shop-floor/work/'+code).location=='/work/'+code


def test_tablet_controls_follow_session_state(app):
    c,tid,code,ops=setup(app)
    page=c.get('/work/'+code).data
    assert b'Start work' in page and b'name="mode"' in page
    assert b'primary-nav' not in page
    assert b'Finish &amp; confirm quantity' not in page
    assert act(app,c,code,'start').status_code==302
    page=c.get('/work/'+code).data
    assert b'Finish &amp; confirm quantity' in page and b'Pause or stop work' in page
    assert act(app,c,code,'pause').status_code==302
    page=c.get('/work/'+code).data
    assert b'Resume work' in page and b'Finish &amp; confirm quantity' not in page
    assert act(app,c,code,'resume').status_code==302
    assert b'Finish &amp; confirm quantity' in c.get('/work/'+code).data
