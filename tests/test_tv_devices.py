from test_workflow import app,post,rows,sign_in


def pair(app):
    tv=app.test_client();tv.get('/tv/setup')
    code=post(tv,'/tv/pair/start').json['code']
    admin=app.test_client();sign_in(admin,'admin')
    assert post(admin,'/admin/tv-devices',code=code,name='Shop floor').status_code==302
    assert post(tv,'/tv/pair/status').json['url']=='/tv/board'
    d=dict(rows(app,'SELECT * FROM tv_devices')[0])
    return tv,admin,d


def test_pair_remote_controls_and_revocation(app):
    tv,admin,d=pair(app)
    assert tv.get('/tv/setup').location.endswith('/tv/board')
    assert tv.get('/tv').location.endswith('/tv/board')
    assert tv.get('/admin/tv-devices').status_code==403
    assert tv.get('/account/security').status_code in (403,404)
    assert b'Boards</a>' not in tv.get('/tv/board').data
    assert tv.get('/tv/data?seen_version=1').json['device']['version']==1
    assert rows(app,'SELECT applied_version FROM tv_devices')[0][0]==1
    assert post(admin,'/admin/tv-devices/'+d['id'],action='reload',version=1).status_code==302
    assert tv.get('/tv/data?seen_version=1').json['device']['version']==2
    assert rows(app,'SELECT applied_version FROM tv_devices')[0][0]==1
    assert post(admin,'/admin/tv-devices/'+d['id'],action='revoke',version=1).status_code==409
    assert tv.get('/tv/data?seen_version=2').status_code==200
    assert rows(app,'SELECT applied_version FROM tv_devices')[0][0]==2
    assert post(admin,'/admin/tv-devices/'+d['id'],action='revoke',version=2).status_code==302
    assert tv.get('/tv/data').status_code==302
    assert b'Pair this screen' in tv.get('/tv/setup').data
    assert len(rows(app,"SELECT * FROM auth_events WHERE action LIKE 'tv_device_%'"))==3


def test_browser_binding_expiry_csrf_and_single_use(app):
    clock=[1000];app.config['AUTH_CLOCK']=lambda:clock[0]
    tv=app.test_client();tv.get('/tv/setup')
    assert tv.post('/tv/pair/start').status_code==400
    code=post(tv,'/tv/pair/start').json['code']
    assert post(tv,'/tv/pair/start').json['code']==code
    with tv.session_transaction() as s: old=dict(s)
    stranger=app.test_client();stranger.get('/tv/setup')
    assert post(stranger,'/tv/pair/status').json['state']=='expired'
    admin=app.test_client();sign_in(admin,'admin')
    assert post(admin,'/admin/tv-devices',code=code,name='TV').status_code==302
    assert post(admin,'/admin/tv-devices',code=code,name='Duplicate').status_code==400
    assert 'url' in post(tv,'/tv/pair/status').json
    replay=app.test_client()
    with replay.session_transaction() as s:s.update(old)
    assert post(replay,'/tv/pair/status').json['state']=='expired'
    fresh=app.test_client();fresh.get('/tv/setup');code2=post(fresh,'/tv/pair/start').json['code']
    clock[0]+=601
    assert post(fresh,'/tv/pair/status').json['state']=='expired'
    assert post(admin,'/admin/tv-devices',code=code2,name='Expired').status_code==400


def test_roles_tenants_and_station_lock(app):
    tv,admin,d=pair(app)
    with app.app_context():
        from productionrunner import db
        from flask import g
        g.app_db=app.config['DATABASE']
        with db():
            station=db().execute("INSERT INTO stations(tenant_id,name,active) VALUES(1,'Laser 1',1)").lastrowid
    assert post(admin,'/admin/tv-devices/'+d['id'],action='save',version=1,name='Laser TV',station=station).status_code==302
    assert tv.get('/tv/data?station=999').json['station']=='Laser 1'
    assert tv.get('/tv/data?screen=999').status_code==403
    assert post(admin,'/admin/tv-devices/'+d['id'],action='save',version=2,name='Bad',station=999).status_code==400
    sign_in(admin,'planner');assert admin.get('/admin/tv-devices').status_code==403
    runner=app.test_cli_runner();assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
    sign_in(admin,'admin',tenant='beta')
    assert post(admin,'/admin/tv-devices/'+d['id'],action='reload',version=2).status_code==404
    assert b'Laser TV' not in admin.get('/admin/tv-devices').data


def test_pairing_rate_limits(app):
    c=app.test_client();sign_in(c,'admin')
    for _ in range(20):assert post(c,'/admin/tv-devices',code='BAD',name='TV').status_code==400
    assert post(c,'/admin/tv-devices',code='BAD',name='TV').status_code==429


def test_access_changes_and_malformed_values(app):
    tv,admin,d=pair(app)
    assert tv.get('/tv/data?seen_version=oops').status_code==400
    assert post(admin,'/admin/tv-devices/'+d['id'],action='save',version=1,name='TV',station='oops').status_code==400
    assert admin.post('/admin/tv-devices/'+d['id'],data={'action':'revoke','version':'1'}).status_code==400
    runner=app.test_cli_runner()
    username=rows(app,'SELECT username FROM users WHERE id=?',(d['user_id'],))[0][0]
    assert runner.invoke(args=['set-membership',username,'--tenant','alpha','--roles','admin']).exit_code==0
    assert tv.get('/admin/tv-devices').status_code==302
    assert tv.get('/tv/data').status_code==302
