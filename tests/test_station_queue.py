from test_workflow import app,post,rows,sign_in
from test_lots import setup,complete,uv
from test_dispatch import board
from productionrunner.database import connect
from productionrunner.station_queue import travelers


def queue(app,station):
    with connect(app.config['DATABASE']) as db:return travelers(db,1,station)


def test_tv_and_tablet_follow_same_lot_between_stations(app):
    c,tid,units,ops=setup(app,size=50,total=100)
    stations=rows(app,'SELECT station_id FROM operation_assignments ORDER BY operation_id')
    laser,drill=stations[0][0],stations[1][0]
    assert sum(r['quantity'] for r in queue(app,laser) if r['status']=='ready')==100
    assert sum(r['ready'] for r in board(app,laser))==100
    assert sum(r['upcoming'] for r in board(app,drill))==100
    sign_in(c,'operator');c.get('/shop-floor?station='+str(drill))
    assert b'Not ready at this station' in c.get('/shop-floor/work/'+units[0]['id']).data
    assert complete(app,c,units[0]['id'],ops[0]).status_code==302
    q=queue(app,drill)
    assert [(r['code'],r['status'],r['quantity']) for r in q]==[(units[0]['id'],'ready',50),(units[1]['id'],'upcoming',50)]
    assert board(app,drill)[0]['ready']==50 and board(app,drill)[0]['upcoming']==50
    assert c.get('/shop-floor/work/'+units[0]['id']).location=='/work/'+units[0]['id']
    page=c.get('/shop-floor').data
    assert b'data-status="ready"' in page and b'data-status="upcoming"' in page
    assert c.get('/tv/data?station='+str(drill)).json['rows'][0]['ready']==50


def test_station_work_status_and_tenant_isolation(app):
    c,tid,units,ops=setup(app,size=50,total=100)
    station=rows(app,'SELECT station_id FROM operation_assignments ORDER BY operation_id')[0][0]
    sign_in(c,'operator');uid=units[0]['id']
    assert post(c,'/work/'+uid,action='start',mode='production',version=uv(app,uid)).status_code==302
    q=queue(app,station);assert q[0]['status']=='running' and q[1]['status']=='ready'
    assert board(app,station)[0]['running']==50
    sid=rows(app,'SELECT MAX(id) FROM work_events')[0][0]
    assert post(c,'/work/'+uid,action='pause',reason='Break',version=uv(app,uid),session_version=str(sid)).status_code==302
    assert queue(app,station)[0]['status']=='paused' and board(app,station)[0]['paused']==50
    with connect(app.config['DATABASE']) as db:assert travelers(db,2,station)==[]
