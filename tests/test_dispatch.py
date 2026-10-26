import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app,post,rows,sign_in,create_ticket
from productionrunner.tv_boards import board_data


def setup_job(app,serial=False):
    client=app.test_client();sign_in(client,'admin')
    for name in ['Laser 1','Assembly']:
        assert post(client,'/admin/stations',name=name,reason='Station setup').status_code==302
    sign_in(client,'planner')
    response=create_ticket(client,legacy=False,**(dict(tracking='serial',serials='S1\nS2') if serial else {}))
    path=response.location
    ops=rows(app,'SELECT * FROM operations ORDER BY sequence')
    return client,path,ops


def version(app,path):
    return str(rows(app,'SELECT version FROM tickets WHERE id=?',(path.split('/')[-1],))[0][0])


def assign(app,client,path,op,station,operator='',v=None):
    return post(client,path+'/assignments/'+str(op['id']),version=v or version(app,path),station_id=str(station),operator_id=str(operator),reason='Schedule work')


def release(app,client,path):
    return post(client,path+'/release',version=version(app,path),reason='Ready for production')


def board(app,station=None):
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.row_factory=sqlite3.Row
        return board_data(conn,1,station)


def test_new_ticket_requires_complete_station_assignment_before_release(app):
    client,path,ops=setup_job(app)
    assert b'PLANNING' in client.get(path).data
    assert release(app,client,path).status_code==409
    sign_in(client,'operator')
    assert post(client,path+'/complete',version='1',operation_id=str(ops[0]['id']),confirm='yes').status_code==409
    assert b'Complete operation' not in client.get('/shop-floor/work/'+path.split('/')[-1]).data
    sign_in(client,'planner')
    assert assign(app,client,path,ops[0],1).status_code==302
    assert release(app,client,path).status_code==409
    assert assign(app,client,path,ops[1],2).status_code==302
    assert release(app,client,path).status_code==302
    assert release(app,client,path).status_code==409
    assert board(app,1)[0]['ready']==2
    assert board(app,2)[0]['upcoming']==2


def test_covering_operator_snapshot_and_independence(app):
    client,path,ops=setup_job(app)
    runner=app.test_cli_runner()
    assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--roles','operator']).exit_code==0
    assert assign(app,client,path,ops[0],1,operator=4).status_code==302
    assert assign(app,client,path,ops[1],2).status_code==302
    assert release(app,client,path).status_code==302
    sign_in(client,'operator')
    for op in ops:
        assert post(client,path+'/complete',version=version(app,path),operation_id=str(op['id']),confirm='yes').status_code==302
    snap=rows(app,'SELECT * FROM completion_assignments ORDER BY id')[0]
    assert snap['station_name']=='Laser 1' and snap['assigned_username']=='viewer' and snap['actual_user_id']==2
    assert post(client,path+'/inspect',version=version(app,path),result='pass',notes='self').status_code==403
    assert board(app,2)[0]['inspection']==2
    sign_in(client,'planner')
    assert assign(app,client,path,ops[0],2).status_code==409
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("UPDATE completion_assignments SET station_name='overwrite'")


def test_serial_progress_reassignment_keeps_completed_snapshot(app):
    client,path,ops=setup_job(app,serial=True)
    for op,station in zip(ops,[1,2]):assert assign(app,client,path,op,station).status_code==302
    assert release(app,client,path).status_code==302
    units=rows(app,'SELECT * FROM serial_units ORDER BY serial')
    sign_in(client,'operator')
    assert post(client,'/units/'+units[0]['id']+'/complete',version=str(units[0]['version']),operation_id=str(ops[0]['id']),confirm='yes').status_code==302
    assert board(app,1)[0]['ready']==1
    assert board(app,2)[0]['ready']==1 and board(app,2)[0]['upcoming']==1
    sign_in(client,'planner')
    assert assign(app,client,path,ops[0],2).status_code==302
    assert rows(app,'SELECT station_name FROM completion_assignments')[0][0]=='Laser 1'
    assert not board(app,1)
    assert sum(r['ready'] for r in board(app,2))==2
    sign_in(client,'operator')
    assert post(client,'/units/'+units[1]['id']+'/complete',version=str(units[1]['version']),operation_id=str(ops[0]['id']),confirm='yes').status_code==409
    latest=rows(app,'SELECT * FROM serial_units WHERE id=?',(units[1]['id'],))[0]
    assert post(client,'/units/'+latest['id']+'/complete',version=str(latest['version']),operation_id=str(ops[0]['id']),confirm='yes').status_code==302
    assert [r[0] for r in rows(app,'SELECT station_name FROM completion_assignments ORDER BY id')]==['Laser 1','Assembly']


def test_station_hold_and_cancelled_removed(app):
    client,path,ops=setup_job(app)
    for op,station in zip(ops,[1,2]):assert assign(app,client,path,op,station).status_code==302
    assert release(app,client,path).status_code==302
    sign_in(client,'operator')
    for op in ops:assert post(client,path+'/complete',version=version(app,path),operation_id=str(op['id']),confirm='yes').status_code==302
    sign_in(client,'inspector')
    assert post(client,path+'/inspect',version=version(app,path),result='fail',notes='Out of tolerance').status_code==302
    assert board(app,2)[0]['held']==2 and board(app)[0]['held']==2
    sign_in(client,'admin')
    assert post(client,path+'/cancel',version=version(app,path),confirm='yes',reason='Cancelled').status_code==302
    assert board(app)==[]


def test_station_scope_roles_and_deactivation(app):
    client,path,ops=setup_job(app)
    assert post(client,'/admin/stations',name='Forbidden',reason='Test').status_code==403
    assert assign(app,client,path,ops[0],1,operator=3).status_code==400
    assert assign(app,client,path,ops[0],1).status_code==302
    sign_in(client,'admin')
    assert post(client,'/admin/stations/1',name='Laser 1',active='0',version='1',reason='Retire').status_code==409
    assert post(client,'/admin/stations/2',name='Assembly',active='0',version='1',reason='Retire').status_code==302
    sign_in(client,'planner')
    assert assign(app,client,path,ops[1],2).status_code==400
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO stations(id,tenant_id,name) VALUES(99,2,'Private station')")
    assert assign(app,client,path,ops[0],99).status_code==400
    assert client.get('/tv/board?station=99').status_code==404
    assert client.get('/tv/data?station=99').status_code==404
    assert b'Private station' not in client.get('/tv').data


def test_display_account_cannot_mutate_or_read_travelers(app):
    client,path,ops=setup_job(app)
    runner=app.test_cli_runner()
    assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--roles','display']).exit_code==0
    assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--roles','display,operator']).exit_code!=0
    sign_in(client,'viewer')
    assert client.get('/tv').status_code==200 and client.get('/tv/data').status_code==200
    for url in [path,'/parts','/shop-floor','/admin','/security-history']:
        assert client.get(url).status_code==403
    assert post(client,path+'/complete',version='1',operation_id=str(ops[0]['id']),confirm='yes').status_code==403
    assert post(client,'/admin/stations',name='No',reason='Test').status_code==403
    assert client.post('/tv/data').status_code in {400,403,405}
    assert post(client,'/logout').status_code==302
    assert client.get('/tv/data').location=='/login'


def test_concurrent_assignment_and_audit_rollback(app):
    first,path,ops=setup_job(app)
    second=app.test_client();sign_in(second,'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda c:assign(app,c,path,ops[0],1,v='1').status_code,[first,second]))==[302,409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_assignment BEFORE INSERT ON events WHEN NEW.action='operation_assigned' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):assign(app,first,path,ops[1],2)
    assert len(rows(app,'SELECT * FROM operation_assignments'))==1


def test_completion_snapshot_failure_rolls_back_work(app):
    client,path,ops=setup_job(app)
    for op,station in zip(ops,[1,2]):assert assign(app,client,path,op,station).status_code==302
    assert release(app,client,path).status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_snapshot BEFORE INSERT ON completion_assignments BEGIN SELECT RAISE(ABORT,'test'); END")
    sign_in(client,'operator')
    with pytest.raises(sqlite3.IntegrityError):post(client,path+'/complete',version=version(app,path),operation_id=str(ops[0]['id']),confirm='yes')
    assert not rows(app,'SELECT * FROM operations WHERE completed_by IS NOT NULL')


def test_station_rename_retains_completed_attribution_and_no_backfill(app):
    client,path,ops=setup_job(app)
    for op,station in zip(ops,[1,2]):assert assign(app,client,path,op,station).status_code==302
    assert release(app,client,path).status_code==302
    sign_in(client,'operator')
    assert post(client,path+'/complete',version=version(app,path),operation_id=str(ops[0]['id']),confirm='yes').status_code==302
    sign_in(client,'admin')
    assert post(client,'/admin/stations/1',name='Laser renamed',active='1',version='1',reason='Rename').status_code==302
    assert rows(app,'SELECT station_name FROM completion_assignments')[0][0]=='Laser 1'
    assert b'Station: Laser 1' in client.get(path).data
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
    assert len(rows(app,'SELECT * FROM completion_assignments'))==1


def test_partial_serial_hold_does_not_hide_ready_sibling(app):
    client,path,ops=setup_job(app,serial=True)
    for op,station in zip(ops,[1,2]):assert assign(app,client,path,op,station).status_code==302
    assert release(app,client,path).status_code==302
    unit=rows(app,'SELECT * FROM serial_units ORDER BY serial')[0]
    sign_in(client,'operator')
    for op in ops:
        v=rows(app,'SELECT version FROM serial_units WHERE id=?',(unit['id'],))[0][0]
        assert post(client,'/units/'+unit['id']+'/complete',version=str(v),operation_id=str(op['id']),confirm='yes').status_code==302
    sign_in(client,'inspector')
    v=rows(app,'SELECT version FROM serial_units WHERE id=?',(unit['id'],))[0][0]
    assert post(client,'/units/'+unit['id']+'/inspect',version=str(v),result='fail',notes='Defect').status_code==302
    assert board(app,1)[0]['ready']==1
    assert board(app,2)[0]['held']==1 and board(app,2)[0]['upcoming']==1
    whole=board(app)[0]
    assert whole['held']==1 and whole['ready']==1 and whole['accepted']==0


def test_whole_board_hot_order_and_display_revocation(app):
    client,path,ops=setup_job(app)
    second=create_ticket(client,legacy=False,pn='HOT-SECOND').location
    assert post(client,second+'/priority',version='1',action='hot',priority='1').status_code==302
    assert board(app)[0]['pn']=='HOT-SECOND'
    assert board(app)[0]['planning']==2
    runner=app.test_cli_runner()
    assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--roles','display']).exit_code==0
    tv=app.test_client();tv.get('/login')
    assert post(tv,'/login',tenant='alpha',username='viewer',password='test-password-123').location=='/tv'
    response=tv.get('/tv/data')
    assert response.status_code==200 and response.json['generated_at'] and response.headers['Cache-Control']=='no-store'
    assert runner.invoke(args=['set-membership','viewer','--tenant','alpha','--revoke']).exit_code==0
    assert tv.get('/tv/data').location=='/login'
