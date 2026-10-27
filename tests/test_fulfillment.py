import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, sign_in, post, create_ticket, rows, complete_route
from test_serials import prepared_units, complete, inspect


def version(app, tid):
    return str(rows(app,'SELECT version FROM tickets WHERE id=?',(tid,))[0][0])


def accepted(app):
    c=app.test_client();sign_in(c,'planner')
    path=create_ticket(c).location;tid=path.split('/')[-1]
    complete_route(app,c,path);sign_in(c,'inspector')
    assert post(c,path+'/inspect',version='3',result='pass',notes='Passed').status_code==302
    sign_in(c,'operator')
    return c,tid


def move(app,c,tid,kind,**kwargs):
    data=dict(version=version(app,tid),quantity='1',notes='Physical evidence',confirm='yes',destination='Customer A',reference='DEL-123',method='external')
    data.update(kwargs)
    return post(c,f'/tickets/{tid}/delivery/{kind}',**data)


def checkpoint(app,c,tid,**kwargs):
    data=dict(version=version(app,tid),action='add',name='ERP production confirmation',stage='pack')
    data.update(kwargs)
    return post(c,f'/tickets/{tid}/delivery/checks',**data)


def test_batch_partial_departure_closes_only_when_all_leave(app):
    c,tid=accepted(app)
    assert b'Awaiting packing' in c.get(f'/tickets/{tid}').data
    assert b'Accepted / awaiting packing' in c.get('/tv/data').data
    assert move(app,c,tid,'depart').status_code==409
    assert move(app,c,tid,'pack',quantity='3').status_code==409
    assert move(app,c,tid,'pack',quantity='2').status_code==302
    stale=version(app,tid)
    assert move(app,c,tid,'depart').status_code==302
    assert b'Partially departed' in c.get('/fulfillment').data
    assert move(app,c,tid,'depart',version=stale).status_code==409
    assert move(app,c,tid,'depart').status_code==302
    assert tid.encode() not in c.get('/fulfillment').data
    assert tid.encode() in c.get('/fulfillment?view=closed').data
    assert tid.encode() not in c.get('/tv/data').data
    assert move(app,c,tid,'depart').status_code==409
    assert rows(app,'SELECT state FROM tickets')[0][0]=='accepted'
    assert len(rows(app,"SELECT * FROM events WHERE action='delivery_closed'"))==1
    assert not rows(app,'PRAGMA foreign_key_check')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
    assert b'Departed / closed' in c.get(f'/tickets/{tid}/delivery').data


def test_serial_exact_units_can_leave_while_sibling_held(app):
    c,tid,units,ops=prepared_units(app)
    for unit in units:
        sign_in(c,'operator')
        for n,op in enumerate(ops,1):assert complete(c,unit,op,n).status_code==302
        sign_in(c,'inspector')
        assert inspect(c,unit,result='pass' if unit==units[0] else 'fail').status_code==302
    sign_in(c,'operator')
    assert move(app,c,tid,'pack',unit_id=[units[1]['id']]).status_code==409
    assert move(app,c,tid,'pack',unit_id=[units[0]['id'],units[0]['id']]).status_code==400
    assert move(app,c,tid,'pack',unit_id=[units[0]['id']]).status_code==302
    assert move(app,c,tid,'pack',unit_id=[units[0]['id']]).status_code==409
    assert move(app,c,tid,'depart',unit_id=[units[0]['id']]).status_code==302
    assert b'Partially departed' in c.get('/fulfillment').data
    assert rows(app,'SELECT serials FROM fulfillment_records WHERE kind=\'depart\'')[0][0]=='["S1"]'
    sign_in(c,'admin')
    assert post(c,f'/tickets/{tid}/cancel',version=version(app,tid),reason='Cannot erase delivery',confirm='yes').status_code==409


def test_checkpoints_block_stage_and_record_actor_reference(app):
    c,tid=accepted(app);sign_in(c,'planner')
    assert checkpoint(app,c,tid).status_code==302
    check=rows(app,'SELECT id FROM fulfillment_checks')[0][0]
    assert move(app,c,tid,'pack').status_code==409
    sign_in(c,'operator')
    assert checkpoint(app,c,tid,action='complete',check_id=check,reference='SAP-123').status_code==403
    sign_in(c,'planner')
    assert checkpoint(app,c,tid,action='complete',check_id=check,reference='SAP-123').status_code==302
    assert checkpoint(app,c,tid,name='ERP delivery ready',stage='depart').status_code==302
    assert move(app,c,tid,'pack',quantity='2').status_code==302
    assert checkpoint(app,c,tid).status_code==409
    assert move(app,c,tid,'depart').status_code==409
    check=rows(app,'SELECT MAX(id) FROM fulfillment_checks')[0][0]
    assert checkpoint(app,c,tid,action='complete',check_id=check,reference='DOC-456').status_code==302
    assert move(app,c,tid,'depart').status_code==302
    assert checkpoint(app,c,tid,stage='depart').status_code==409
    assert b'SAP-123' in c.get(f'/tickets/{tid}/delivery').data


def test_unaccepted_cancelled_and_input_guards(app):
    c=app.test_client();sign_in(c,'planner');tid=create_ticket(c).location.split('/')[-1]
    assert move(app,c,tid,'pack').status_code==409
    assert checkpoint(app,c,tid).status_code==302
    check=rows(app,'SELECT id FROM fulfillment_checks')[0][0]
    assert checkpoint(app,c,tid,action='complete',check_id=check,reference='not yet').status_code==409
    sign_in(c,'admin')
    assert post(c,f'/tickets/{tid}/cancel',version=version(app,tid),reason='Cancel',confirm='yes').status_code==302
    assert move(app,c,tid,'pack').status_code==409
    c,tid=accepted(app)
    for fields in (dict(confirm=''),dict(notes=''),dict(quantity='bad')):
        assert move(app,c,tid,'pack',**fields).status_code==400
    assert move(app,c,tid,'pack',quantity='0').status_code==409
    assert move(app,c,tid,'depart',destination='').status_code==400
    assert move(app,c,tid,'depart',method='bad').status_code==400


def test_permissions_csrf_and_tenant_isolation(app):
    c,tid=accepted(app)
    assert c.post(f'/tickets/{tid}/delivery/pack',data={'quantity':'1'}).status_code==400
    for user in ('viewer','inspector'):
        sign_in(c,user)
        assert c.get(f'/tickets/{tid}/delivery').status_code==200
        assert move(app,c,tid,'pack').status_code==403
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code==0
    sign_in(c,'planner','beta')
    assert c.get(f'/tickets/{tid}/delivery').status_code==404
    assert move(app,c,tid,'pack').status_code==404
    assert tid.encode() not in c.get('/fulfillment').data


def test_concurrency_audit_rollback_and_immutable_ledger(app):
    c,tid=accepted(app);c2=app.test_client();sign_in(c2,'operator');v=version(app,tid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda client:move(app,client,tid,'pack',quantity='2',version=v).status_code,[c,c2]))
    assert sorted(results)==[302,409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_depart BEFORE INSERT ON events WHEN NEW.action='units_departed' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):move(app,c,tid,'depart')
    assert not rows(app,"SELECT * FROM fulfillment_records WHERE kind='depart'")
    assert version(app,tid)==str(int(v)+1)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ('DELETE FROM fulfillment_records','UPDATE fulfillment_records SET quantity=99'):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
