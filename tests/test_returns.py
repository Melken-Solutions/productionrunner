import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_workflow import app, post, sign_in, rows
from test_fulfillment import accepted, move, version
from test_delivery_corrections import reverse, last
from test_serials import prepared_units, complete, inspect


def shipped(app):
    c,tid=accepted(app)
    assert move(app,c,tid,'pack',quantity='2').status_code==302
    assert move(app,c,tid,'depart',quantity='2').status_code==302
    return c,tid,last(app)


def receive(app,c,tid,departure,**kw):
    data=dict(version=version(app,tid),departure_id=str(departure),quantity='1',reference='RMA-101',reason='Damaged on arrival',location='Quarantine rack A',confirm='yes')
    data.update(kw)
    return post(c,f'/tickets/{tid}/returns',**data)


def review(app,c,tid,rid,**kw):
    data=dict(version=version(app,tid),decision='repair_required',notes='Planner to open a repair job; keep segregated')
    data.update(kw)
    return post(c,f'/tickets/{tid}/returns/{rid}/review',**data)


def test_batch_return_and_disposition_preserve_outbound_and_inspection(app):
    c,tid,d=shipped(app)
    original=rows(app,'SELECT * FROM fulfillment_records')
    assert receive(app,c,tid,d).status_code==302
    rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    assert b'Awaiting disposition' in c.get('/returns').data
    assert b'Customer returns' in c.get('/fulfillment').data
    assert b'Departed / closed' in c.get(f'/tickets/{tid}/delivery').data
    assert rows(app,'SELECT * FROM fulfillment_records')==original
    assert rows(app,'SELECT state FROM tickets')[0][0]=='accepted'
    assert review(app,c,tid,rid).status_code==403
    sign_in(c,'planner')
    assert reverse(app,c,tid,d).status_code==409
    assert review(app,c,tid,rid).status_code==302
    assert review(app,c,tid,rid).status_code==409
    assert b'Repair required' in c.get('/returns').data
    assert b'Disposition recorded' in c.get('/returns').data
    assert receive(app,c,tid,d,quantity='2').status_code==409
    assert receive(app,c,tid,d).status_code==302
    assert receive(app,c,tid,d).status_code==409
    assert move(app,c,tid,'pack').status_code==409
    assert not rows(app,'PRAGMA foreign_key_check')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code==0


def test_serial_return_matches_departure_and_is_not_returnable_twice(app):
    c,tid,units,ops=prepared_units(app)
    for unit in units:
        sign_in(c,'operator')
        for n,op in enumerate(ops,1):assert complete(c,unit,op,n).status_code==302
        sign_in(c,'inspector');assert inspect(c,unit,result='pass').status_code==302
    sign_in(c,'operator')
    assert move(app,c,tid,'pack',unit_id=[u['id'] for u in units]).status_code==302
    assert move(app,c,tid,'depart',unit_id=[units[0]['id']]).status_code==302
    d=last(app)
    assert receive(app,c,tid,d,unit_id=[units[1]['id']]).status_code==409
    assert receive(app,c,tid,d,unit_id=[units[0]['id'],units[0]['id']]).status_code==400
    assert receive(app,c,tid,d,unit_id=[units[0]['id']]).status_code==302
    assert receive(app,c,tid,d,unit_id=[units[0]['id']]).status_code==409
    assert move(app,c,tid,'depart',unit_id=[units[0]['id']]).status_code==409
    assert move(app,c,tid,'depart',unit_id=[units[1]['id']]).status_code==302
    sign_in(c,'admin');rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    assert review(app,c,tid,rid,decision='replacement_required').status_code==302
    assert len(rows(app,"SELECT * FROM unit_event_links l JOIN events e ON e.id=l.event_id WHERE e.action IN ('unit_return_received','unit_return_disposition_recorded')"))==2


def test_permissions_tenants_input_and_reversed_departures(app):
    c,tid,d=shipped(app)
    for user in ('viewer','inspector'):
        sign_in(c,user);assert receive(app,c,tid,d).status_code==403
    sign_in(c,'admin')
    assert c.post(f'/tickets/{tid}/returns',data={}).status_code==400
    for kw in (dict(reference=''),dict(reason=''),dict(location=''),dict(confirm=''),dict(quantity='no'),dict(location='x'*501)):
        assert receive(app,c,tid,d,**kw).status_code==400
    assert receive(app,c,tid,d,quantity='0').status_code==409
    assert receive(app,c,tid,d,unit_id=['unknown']).status_code==400
    assert receive(app,c,tid,99999).status_code==404
    assert reverse(app,c,tid,d).status_code==302
    assert receive(app,c,tid,d).status_code==404
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
    sign_in(c,'admin','beta')
    assert receive(app,c,tid,d).status_code==404
    assert c.get(f'/tickets/{tid}/returns').status_code==404
    assert tid.encode() not in c.get('/returns').data


def test_return_and_reversal_race_and_atomic_receipt(app):
    c,tid,d=shipped(app);sign_in(c,'admin');c2=app.test_client();sign_in(c2,'planner');v=version(app,tid)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_return BEFORE INSERT ON events WHEN NEW.action='return_received' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):receive(app,c,tid,d)
    assert not rows(app,'SELECT * FROM customer_returns')
    assert version(app,tid)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_return')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(receive,app,c,tid,d,version=v)
        b=pool.submit(reverse,app,c2,tid,d,version=v)
        assert sorted([a.result().status_code,b.result().status_code])==[302,409]
    assert not (rows(app,'SELECT * FROM customer_returns') and rows(app,'SELECT * FROM fulfillment_reversals'))


def test_review_validation_rollback_immutability_and_concurrency(app):
    c,tid,d=shipped(app);receive(app,c,tid,d);rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    sign_in(c,'admin');v=version(app,tid)
    assert review(app,c,tid,rid,decision='release').status_code==400
    assert review(app,c,tid,rid,notes='').status_code==400
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_review BEFORE INSERT ON events WHEN NEW.action='return_disposition_recorded' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):review(app,c,tid,rid)
    assert not rows(app,'SELECT * FROM customer_return_reviews')
    assert version(app,tid)==v
    with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute('DROP TRIGGER fail_review')
    c2=app.test_client();sign_in(c2,'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda client:review(app,client,tid,rid,version=v).status_code,[c,c2]))
    assert sorted(results)==[302,409]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ('DELETE FROM customer_returns','UPDATE customer_returns SET quantity=99','DELETE FROM customer_return_reviews','UPDATE customer_return_reviews SET notes=\'changed\''):
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)


def test_returns_protect_documents_even_after_disposition(app):
    from test_storage_management import setup,report
    setup(app)
    c,tid,d=shipped(app)
    assert all(f['status']=='Eligible in preview' for f in report(app)['files'])
    receive(app,c,tid,d);rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    assert all(f['status']=='Protected by returned material' for f in report(app)['files'])
    sign_in(c,'admin');review(app,c,tid,rid,decision='scrap_authorized')
    assert all(f['status']=='Protected by returned material' for f in report(app)['files'])


def test_same_tenant_wrong_departure_and_cross_tenant_return_history(app):
    c,tid,d=shipped(app)
    c2,tid2,d2=shipped(app)
    assert receive(app,c,tid,d2).status_code==404
    assert receive(app,c,tid,d).status_code==302
    rid=rows(app,'SELECT id FROM customer_returns')[0][0]
    sign_in(c,'admin')
    assert review(app,c,tid2,rid).status_code==404
    assert b'Correct this entry' not in c.get(f'/tickets/{tid}/delivery').data
    runner=app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code==0
    assert runner.invoke(args=['set-membership','admin','--tenant','beta','--roles','admin']).exit_code==0
    sign_in(c,'admin','beta')
    assert review(app,c,tid,rid).status_code==404
    assert b'RMA-101' not in c.get('/returns').data
