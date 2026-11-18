import json
from test_workflow import app,post,rows,sign_in
from test_lots import setup,accept,complete,uv
from productionrunner.quantity_reports import records
from productionrunner.database import connect


def test_pieces_not_steps_and_partial_lots_and_csv(app):
    c,tid,units,ops=setup(app,total=100,size=50)
    accept(app,c,units[0]['id'],ops)
    sign_in(c,'operator');assert complete(app,c,units[1]['id'],ops[0]).status_code==302
    with connect(app.config['DATABASE']) as db:
        assert sum(r['quantity'] for r in records(db,1,'accepted','2000','9999'))==50
        assert sum(r['quantity'] for r in records(db,1,'operations','2000','9999'))==150
        assert records(db,2,'operations','2000','9999')==[]
    assert c.get('/admin/reports/quantity').status_code==403
    sign_in(c,'admin');page=c.get('/admin/reports/quantity');assert page.status_code==200 and b'Accepted pieces: 50' in page.data
    csv=c.get('/admin/reports/quantity?format=csv');assert b',50,' in csv.data and b'Final inspection accepted' in csv.data
    assert b'Accepted pieces: 0' in c.get('/admin/reports/quantity?pn=UNKNOWN').data
    assert c.get('/admin/reports/quantity?from=2026-11-20&to=2026-11-01').status_code==400
    assert c.get('/admin/reports/quantity?metric=bad').status_code==400


def test_split_inherits_evidence_without_inflating_throughput(app):
    c,tid,units,ops=setup(app,total=100,size=100);uid=units[0]['id']
    sign_in(c,'operator');assert complete(app,c,uid,ops[0]).status_code==302
    assert post(c,f'/lots/{uid}/action',version=uv(app,uid),action='split',quantity='40',reason='Partial run').status_code==302
    with connect(app.config['DATABASE']) as db:
        data=records(db,1,'operations','2000','9999')
        assert sorted(r['quantity'] for r in data)==[40,60]
        assert sum(r['quantity'] for r in data)==100


def test_vendor_purchase_order_is_saved_per_shipment(app):
    from test_outside_processing import ready,act
    from productionrunner.outside_processing import ledger
    c,path,ops=ready(app)
    assert act(app,c,path,ops[0],'send',purchase_order='PO-NEW-41').status_code==302
    with connect(app.config['DATABASE']) as db:
        entries=ledger(db,1,ops[0]['id']);assert entries[0]['purchase_order']=='PO-NEW-41'
        assert ledger(db,2,ops[0]['id'])==[]
    assert b'Purchase Order Number: PO-NEW-41' in c.get(path+'/outside').data
    assert act(app,c,path,ops[0],'send',purchase_order='X'*201).status_code==400
    assert len(rows(app,'SELECT * FROM outside_moves'))==1


def test_rework_acceptance_counts_recovered_batch_once(app):
    from test_production_controls import held_batch,authorize,repair,reinspect
    c,path=held_batch(app);case=authorize(c,path).location
    sign_in(c,'operator');assert repair(c,case).status_code==302
    sign_in(c,'inspector');assert reinspect(c,case).status_code==302
    with connect(app.config['DATABASE']) as db:
        data=records(db,1,'accepted','2000','9999')
        assert len(data)==1 and data[0]['operation']=='Accepted after rework'
        assert data[0]['quantity']==rows(app,'SELECT quantity FROM tickets')[0][0]
