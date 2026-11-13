import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, sign_in, post, rows
from test_fulfillment import accepted, move, version, checkpoint
from test_serials import prepared_units, complete, inspect


def reverse(app,c,tid,rid,**kwargs):
    data=dict(version=version(app,tid),reason='Recorded in error; goods remain onsite',confirm='yes')
    data.update(kwargs)
    return post(c,f'/tickets/{tid}/delivery/records/{rid}/reverse',**data)


def last(app):
    return rows(app,'SELECT MAX(id) FROM fulfillment_records')[0][0]


def test_closed_batch_reopens_and_recloses_preserving_history(app):
    c,tid=accepted(app)
    assert move(app,c,tid,'pack',quantity='2').status_code==302
    pack=last(app)
    assert move(app,c,tid,'depart',quantity='2').status_code==302
    depart=last(app)
    original=rows(app,'SELECT * FROM fulfillment_records')
    sign_in(c,'planner')
    assert reverse(app,c,tid,pack).status_code==409
    assert reverse(app,c,tid,depart).status_code==302
    assert tid.encode() in c.get('/fulfillment').data
    assert tid.encode() in c.get('/tv/data').data
    assert b'Ready for dispatch' in c.get(f'/tickets/{tid}/delivery').data
    assert rows(app,'SELECT * FROM fulfillment_records')==original
    assert reverse(app,c,tid,depart).status_code==409
    assert move(app,c,tid,'depart',quantity='2').status_code==302
    assert tid.encode() not in c.get('/fulfillment').data
    assert len(rows(app,"SELECT * FROM events WHERE action='delivery_closed'"))==2
    assert len(rows(app,"SELECT * FROM events WHERE action='delivery_reopened'"))==1
    assert rows(app,'SELECT state FROM tickets WHERE id=?',(tid,))[0][0]=='accepted'
    assert b'Recorded in error' in c.get(f'/tickets/{tid}/delivery').data


def test_reverse_pack_restores_quantity_but_cannot_retroactively_add_gate(app):
    c,tid=accepted(app)
    assert move(app,c,tid,'pack',quantity='2').status_code==302
    sign_in(c,'admin')
    assert reverse(app,c,tid,last(app)).status_code==302
    assert b'Awaiting packing' in c.get(f'/tickets/{tid}/delivery').data
    assert checkpoint(app,c,tid).status_code==409
    assert move(app,c,tid,'depart').status_code==409
    assert move(app,c,tid,'pack',quantity='2').status_code==302


def test_serial_reversal_and_repeat_cycles_include_legacy_links(app):
    c,tid,units,ops=prepared_units(app)
    for unit in units:
        sign_in(c,'operator')
        for n,op in enumerate(ops,1): assert complete(c,unit,op,n).status_code==302
        sign_in(c,'inspector');assert inspect(c,unit,result='pass').status_code==302
    sign_in(c,'operator')
    assert move(app,c,tid,'pack',unit_id=[units[0]['id']]).status_code==302
    pack=last(app)
    # Exercise an original-version immutable link alongside new repeatable entries.
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('DROP TRIGGER fulfillment_unit_entries_no_delete')
        conn.execute('INSERT INTO fulfillment_units SELECT * FROM fulfillment_unit_entries')
        conn.execute('DELETE FROM fulfillment_unit_entries')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0
    assert move(app,c,tid,'depart',unit_id=[units[0]['id']]).status_code==302
    sign_in(c,'planner')
    assert reverse(app,c,tid,pack).status_code==409
    assert reverse(app,c,tid,last(app)).status_code==302
    assert reverse(app,c,tid,pack).status_code==302
    for _ in range(2):
        assert move(app,c,tid,'pack',unit_id=[units[0]['id']]).status_code==302
        pack=last(app)
        assert move(app,c,tid,'depart',unit_id=[units[0]['id']]).status_code==302
        assert reverse(app,c,tid,last(app)).status_code==302
        assert reverse(app,c,tid,pack).status_code==302
    assert len(rows(app,"SELECT * FROM unit_event_links l JOIN events e ON e.id=l.event_id WHERE e.action='unit_delivery_reversed'"))==6
    assert not rows(app,'SELECT * FROM fulfillment_active_units')
    assert not rows(app,'PRAGMA foreign_key_check')


def test_reversal_permissions_tenant_validation_and_csrf(app):
    c,tid=accepted(app);move(app,c,tid,'pack');rid=last(app)
    for user in ('operator','viewer','inspector'):
        sign_in(c,user);assert reverse(app,c,tid,rid).status_code==403
    sign_in(c,'admin')
    assert c.post(f'/tickets/{tid}/delivery/records/{rid}/reverse',data={}).status_code==400
    for data in (dict(reason=''),dict(reason='x'*501),dict(confirm='')):
        assert reverse(app,c,tid,rid,**data).status_code==400
    assert reverse(app,c,tid,99999).status_code==404
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
    sign_in(c,'admin','beta')
    assert reverse(app,c,tid,rid).status_code==404
    assert not rows(app,'SELECT * FROM fulfillment_reversals')


def test_concurrent_reverse_and_audit_failure_are_atomic(app):
    c,tid=accepted(app);move(app,c,tid,'pack');rid=last(app)
    sign_in(c,'admin');c2=app.test_client();sign_in(c2,'planner')
    v=version(app,tid)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_reverse BEFORE INSERT ON events WHEN NEW.action='delivery_record_reversed' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):reverse(app,c,tid,rid)
    assert not rows(app,'SELECT * FROM fulfillment_reversals')
    assert version(app,tid)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_reverse')
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(lambda client:reverse(app,client,tid,rid,version=v).status_code,[c,c2]))
    assert sorted(result)==[302,409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ('DELETE FROM fulfillment_reversals','UPDATE fulfillment_reversals SET reason=\'changed\''):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)
    assert reverse(app,c,tid,rid,version=v).status_code==409


def test_reopened_job_protects_drawings_again(app):
    from test_storage_management import setup, report
    setup(app)
    c,tid=accepted(app)
    move(app,c,tid,'pack',quantity='2');move(app,c,tid,'depart',quantity='2')
    assert all(f['status']=='Eligible in preview' for f in report(app)['files'])
    sign_in(c,'admin');assert reverse(app,c,tid,last(app)).status_code==302
    assert all(f['status']=='Protected by active / held work' for f in report(app)['files'])


def test_pack_reversal_races_departure_without_negative_totals(app):
    c,tid=accepted(app);move(app,c,tid,'pack',quantity='2');rid=last(app)
    sign_in(c,'admin');c2=app.test_client();sign_in(c2,'operator');v=version(app,tid)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(reverse,app,c,tid,rid,version=v)
        b=pool.submit(move,app,c2,tid,'depart',quantity='2',version=v)
        assert sorted([a.result().status_code,b.result().status_code])==[302,409]
    totals=dict(rows(app,'SELECT kind,SUM(quantity) FROM fulfillment_active_records GROUP BY kind'))
    assert totals.get('pack',0)>=totals.get('depart',0)
