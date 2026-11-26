from test_workflow import app,post,rows,sign_in
from test_lots import setup,accept,move,complete,uv


def test_exclusive_580_piece_progress_and_station_scope(app):
    c,tid,units,ops=setup(app,total=580,size=50)
    accept(app,c,units[0]['id'],ops);sign_in(c,'operator')
    assert move(app,c,tid,units[0]['id'],'pack').status_code==302
    assert move(app,c,tid,units[0]['id'],'depart').status_code==302
    for unit in units[1:3]:assert complete(app,c,unit['id'],ops[0]).status_code==302
    p=c.get('/tv/data').json['rows'][0]['progress']
    assert p['consistent'] and p['ordered']==580
    assert (p['shipped'],p['underway'],p['not_started'])==(50,100,430)
    assert p['held']==p['inspection']==p['accepted']==p['packed']==0
    assert c.get('/tv/data').json['rows'][0]['activity']=='waiting' # no open timing session
    station=rows(app,'SELECT station_id FROM operation_assignments ORDER BY operation_id')[0][0]
    feed=c.get('/tv/data?station='+str(station)).json
    assert all(r['progress']==p for r in feed['rows'])
    assert post(c,f"/lots/{units[3]['id']}/action",version=uv(app,units[3]['id']),action='hold',reason='Material review').status_code==302
    p=c.get('/tv/data').json['rows'][0]['progress'];assert p['held']==50 and p['not_started']==380 and p['consistent']
    sign_in(c,'admin')
    assert post(c,f"/lots/{units[3]['id']}/action",version=uv(app,units[3]['id']),action='scrap',reason='Replace defective material').status_code==302
    p=c.get('/tv/data').json['rows'][0]['progress'];assert p['held']==0 and p['not_started']==430 and p['consistent']


def test_active_paused_stopped_and_split_progress(app):
    from test_work_tracking import act
    c,tid,units,ops=setup(app,total=100,size=100);uid=units[0]['id'];sign_in(c,'operator')
    assert act(app,c,uid,'start').status_code==302
    row=c.get('/tv/data').json['rows'][0]
    assert row['activity']=='working' and row['progress']['underway']==100
    station=rows(app,'SELECT station_id FROM operation_assignments WHERE operation_id=?',(ops[1]['id'],))[0][0]
    assert all(r['activity']=='waiting' for r in c.get('/tv/data?station='+str(station)).json['rows'])
    assert act(app,c,uid,'pause').status_code==302
    assert c.get('/tv/data').json['rows'][0]['activity']=='paused'
    assert act(app,c,uid,'stop').status_code==302
    assert post(c,f'/lots/{uid}/action',version=uv(app,uid),action='split',quantity='40',reason='Partial run').status_code==302
    row=c.get('/tv/data').json['rows'][0]
    assert row['activity']=='waiting' and row['progress']['underway']==100 and row['progress']['consistent']


def test_activity_settings_and_progress_flags(app):
    c=app.test_client();sign_in(c,'admin')
    assert post(c,'/admin/tv-settings',version='0',layout='activity',orientation='auto',text_size='standard',cards='4',seconds='20',show_progress='yes',pin_hot='yes').status_code==302
    config=c.get('/tv/data').json['settings']
    assert config['layout']=='activity' and config['pin_hot'] and config['show_progress']
    page=c.get('/tv/board').data;assert b'id="pinned-job"' in page
