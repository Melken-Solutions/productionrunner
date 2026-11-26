from test_workflow import app,post,rows,sign_in


def screen(app,c):
    sign_in(c,'admin')
    assert post(c,'/admin/users',account_type='tv',username='shop-tv',password='screen-password-123',password_confirm='screen-password-123',reason='Shop TV').status_code==302
    return rows(app,"SELECT id FROM users WHERE username='shop-tv'")[0][0]


def test_settings_per_screen_live_feed_and_version_guards(app):
    c=app.test_client();uid=screen(app,c)
    data=dict(screen=uid,version='0',layout='priority',orientation='landscape',text_size='large',cards='3',seconds='15',show_start='yes')
    assert post(c,'/admin/tv-settings',**data).status_code==302
    feed=c.get('/tv/data?screen='+str(uid)).json
    assert feed['settings']['layout']=='priority' and feed['settings']['text_size']=='large'
    assert feed['settings']['show_start'] and not feed['settings']['show_operator']
    assert c.get('/tv/data').json['settings']['layout']=='two'
    assert post(c,'/admin/tv-settings',**data).status_code==409
    version=feed['settings']['version']
    assert post(c,'/admin/tv-settings',**(data|dict(version=version,cards='999'))).status_code==400
    assert len(rows(app,"SELECT * FROM auth_events WHERE action='tv_settings_saved'"))==1
    preview=c.get(f'/tv/data?screen={uid}&preview=yes&layout=single').json
    assert preview['settings']['layout']=='single'
    assert c.get('/tv/data?screen='+str(uid)).json['settings']['layout']=='priority'
    tv=app.test_client();tv.get('/login')
    assert post(tv,'/login',tenant='alpha',username='shop-tv',password='screen-password-123',method='password').status_code==302
    assert tv.get('/tv/data').json['settings']['layout']=='priority'
    assert tv.get('/admin/tv-settings').status_code==403
    assert tv.get('/tv/data?screen=999').status_code==403
    assert tv.get('/tv/data?preview=yes').status_code==403
    assert b'Screen settings' not in tv.get('/tv/board').data


def test_settings_tenant_and_role_boundaries(app):
    c=app.test_client();uid=screen(app,c)
    sign_in(c,'planner');assert c.get('/admin/tv-settings').status_code==403
    assert c.get(f'/tv/data?screen={uid}').status_code==403
    runner=app.test_cli_runner();assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
    sign_in(c,'admin',tenant='beta');assert c.get(f'/admin/tv-settings?screen={uid}').status_code==404
    assert c.get(f'/tv/data?screen={uid}').status_code==404


def test_summary_counts_jobs_once_not_operations(app):
    from test_lots import setup
    from test_lots import tv
    c,tid,units,ops=setup(app,total=100)
    assert post(c,f'/tickets/{tid}/priority',version=tv(app,tid),action='hot',priority='1').status_code==302
    feed=c.get('/tv/data').json
    assert feed['summary']['jobs']==1 and feed['summary']['hot']==1
    assert feed['summary']['shipping']==0
