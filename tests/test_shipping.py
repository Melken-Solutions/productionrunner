import io
import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows
from test_fulfillment import accepted,move


def setup(app):
    c,tid=accepted(app)
    sign_in(c,'admin')
    assert post(c,'/admin/shipping',action='destination',name='Main warehouse',address='123 Test Road',reason='Test setup').status_code==302
    assert post(c,'/admin/shipping',action='rule',source='manual',purpose='stock',method='ltl',label_owner='planner',reason='Test setup').status_code==302
    assert post(c,f'/tickets/{tid}/shipping',destination_id='1',purpose='stock',method='ltl',label_owner='planner',reason='Reviewed request').status_code==302
    sign_in(c,'operator')
    return c,tid


def box(app,c,tid):
    assert move(app,c,tid,'pack').status_code==302
    pack=rows(app,"SELECT id FROM fulfillment_records WHERE kind='pack' ORDER BY id DESC")[0][0]
    assert post(c,f'/tickets/{tid}/shipping/boxes',pack_record_id=str(pack),weight_kg='2.5',dimensions_cm='40 x 30 x 20').status_code==302
    return rows(app,'SELECT id FROM shipping_boxes ORDER BY id DESC')[0][0]


def action(app,c,lid,name,**kwargs):
    v=rows(app,'SELECT version FROM shipping_loads WHERE id=?',(lid,))[0][0]
    if name=='request':
        kwargs.setdefault('gross_weight_kg','100')
        kwargs.setdefault('pallet_dimensions_cm','120 x 100 x 100')
    return post(c,f'/shipping/{lid}/action',version=str(v),action=name,**kwargs)


def upload(app,c,lid):
    v=rows(app,'SELECT version FROM shipping_loads WHERE id=?',(lid,))[0][0]
    return post(c,f'/shipping/{lid}/labels',version=str(v),carrier='Carrier test',reference='PRO-123',labels=(io.BytesIO(b'%PDF-1.4\nfixture\n%%EOF'),'labels.pdf'))


def depart(app,c,lid):
    v=rows(app,'SELECT version FROM shipping_loads WHERE id=?',(lid,))[0][0]
    return post(c,f'/shipping/{lid}/depart',version=str(v),confirm='yes',notes='Driver picked up all boxes')


def test_full_handoff_partial_quantities_and_replay(app):
    c,tid=setup(app);bid=box(app,c,tid)
    assert post(c,'/shipping',box_id=str(bid)).status_code==302
    assert action(app,c,1,'request').status_code==302
    assert upload(app,c,1).status_code==403
    assert depart(app,c,1).status_code==409
    assert move(app,c,tid,'depart').status_code==409
    sign_in(c,'planner');assert upload(app,c,1).status_code==302
    label=rows(app,'SELECT id FROM shipping_labels')[0][0]
    assert c.get(f'/shipping/1/labels/{label}').status_code==200
    assert rows(app,"SELECT COUNT(*) FROM fulfillment_records WHERE kind='depart'")[0][0]==0
    sign_in(c,'operator');assert depart(app,c,1).status_code==302
    assert depart(app,c,1).status_code==409
    assert rows(app,"SELECT SUM(quantity) FROM fulfillment_active_records WHERE kind='depart'")[0][0]==1
    assert b'Partially departed' in c.get(f'/tickets/{tid}/delivery').data
    assert b'BOX-' in c.get(f'/shipping/boxes/{bid}/print').data
    assert b'Physical departure recorded' in c.get('/shipping/1').data
    assert not rows(app,'PRAGMA foreign_key_check')


def test_labels_superseded_reopen_and_stale_posts(app):
    c,tid=setup(app);bid=box(app,c,tid)
    post(c,'/shipping',box_id=str(bid));action(app,c,1,'request')
    sign_in(c,'planner');assert upload(app,c,1).status_code==302
    old=rows(app,'SELECT id FROM shipping_labels')[0][0]
    assert upload(app,c,1).status_code==302
    assert c.get(f'/shipping/1/labels/{old}').status_code==409
    assert post(c,'/shipping/1/action',version='0',action='reopen',reason='Wrong box').status_code==409
    assert action(app,c,1,'reopen',reason='Add another box').status_code==302
    assert c.get('/shipping/1/labels/2').status_code==409
    assert depart(app,c,1).status_code==409
    assert action(app,c,1,'request').status_code==302
    assert depart(app,c,1).status_code==409
    assert upload(app,c,1).status_code==302


def test_box_replay_and_plan_freeze(app):
    c,tid=setup(app);bid=box(app,c,tid)
    pack=rows(app,'SELECT pack_record_id FROM shipping_boxes')[0][0]
    assert post(c,f'/tickets/{tid}/shipping/boxes',pack_record_id=str(pack),weight_kg='2',dimensions_cm='10 x 10 x 10').status_code==409
    sign_in(c,'planner')
    assert post(c,f'/tickets/{tid}/shipping',destination_id='1',purpose='order',method='fedex',label_owner='either',reason='Change').status_code==409
    assert post(c,f'/tickets/{tid}/delivery/records/{pack}/reverse',version=str(rows(app,'SELECT version FROM tickets WHERE id=?',(tid,))[0][0]),reason='Correction',confirm='yes').status_code==409


def test_destination_mismatch_and_cross_tenant_access(app):
    c,tid=setup(app);first=box(app,c,tid);second=box(app,c,tid)
    assert post(c,'/shipping',box_id=str(first)).status_code==302
    assert action(app,c,1,'add',box_id=str(second)).status_code==302
    assert action(app,c,1,'add',box_id=str(second)).status_code==409
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships VALUES(2,1,'planner')")
    outsider=app.test_client();sign_in(outsider,'planner',tenant='beta')
    for path in ['/shipping/1',f'/shipping/boxes/{first}/print',f'/tickets/{tid}/shipping']:
        assert outsider.get(path).status_code==404
    assert post(outsider,'/shipping',box_id=str(first)).status_code==409
    sign_in(c,'viewer')
    assert c.get('/shipping').status_code==403
    assert c.get('/admin/shipping').status_code==403


def test_storage_failure_does_not_publish_labels(app):
    c,tid=setup(app);bid=box(app,c,tid)
    post(c,'/shipping',box_id=str(bid));action(app,c,1,'request');sign_in(c,'planner')
    from productionrunner.object_storage import StorageError
    class Failed:
        def put(self,*args):raise StorageError('provider secret must not appear')
    app.extensions['file_storage']=Failed()
    response=upload(app,c,1)
    assert response.status_code==503 and b'provider secret' not in response.data
    assert rows(app,'SELECT state FROM shipping_loads')[0][0]=='requested'
    assert not rows(app,'SELECT * FROM shipping_labels')


def test_label_file_validation(app):
    c,tid=setup(app);bid=box(app,c,tid)
    post(c,'/shipping',box_id=str(bid));action(app,c,1,'request');sign_in(c,'planner')
    v=str(rows(app,'SELECT version FROM shipping_loads')[0][0])
    assert post(c,'/shipping/1/labels',version=v,carrier='test',reference='123',labels=(io.BytesIO(b'not pdf'),'bad.pdf')).status_code==400
    assert not rows(app,'SELECT * FROM shipping_labels')


def second_job(app,c,destination='1',method='ltl'):
    tid='SECOND-SHIPPING-JOB'
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tickets(id,tenant_id,pn,revision,quantity,tracking,serials,instructions,state,created_by,created_at) VALUES(?,1,'SECOND-PN','B',1,'batch','','Test','accepted',1,'2026-10-30')",(tid,))
    sign_in(c,'planner')
    assert post(c,f'/tickets/{tid}/shipping',destination_id=destination,purpose='stock',method=method,label_owner='planner',reason='Second order').status_code==302
    sign_in(c,'operator')
    return tid,box(app,c,tid)


def test_mixed_order_pallet_and_atomic_checkpoint_block(app):
    c,tid=setup(app);first=box(app,c,tid)
    second_tid,second=second_job(app,c)
    post(c,'/shipping',box_id=str(first))
    assert action(app,c,1,'add',box_id=str(second)).status_code==302
    action(app,c,1,'request');sign_in(c,'planner');upload(app,c,1)
    from test_fulfillment import checkpoint
    assert checkpoint(app,c,second_tid,stage='depart').status_code==302
    assert depart(app,c,1).status_code==409
    assert not rows(app,"SELECT * FROM fulfillment_records WHERE kind='depart'")
    check=rows(app,'SELECT id FROM fulfillment_checks WHERE ticket_id=?',(second_tid,))[0][0]
    assert checkpoint(app,c,second_tid,action='complete',check_id=str(check),reference='SAP confirmed').status_code==302
    assert depart(app,c,1).status_code==302
    assert rows(app,'SELECT COUNT(*) FROM shipping_departures')[0][0]==2
    assert b'Departed / closed' in c.get(f'/tickets/{second_tid}/delivery').data
    assert b'Partially departed' in c.get(f'/tickets/{tid}/delivery').data


def test_different_destinations_cannot_share_pallet(app):
    c,tid=setup(app);first=box(app,c,tid)
    sign_in(c,'admin');post(c,'/admin/shipping',action='destination',name='Other warehouse',address='456 Different Road',reason='Test')
    _,second=second_job(app,c,destination='2')
    post(c,'/shipping',box_id=str(first))
    assert action(app,c,1,'add',box_id=str(second)).status_code==409
    assert rows(app,'SELECT load_id FROM shipping_boxes WHERE id=?',(second,))[0][0] is None


def test_void_box_releases_packing_for_correction(app):
    c,tid=setup(app);bid=box(app,c,tid);sign_in(c,'planner')
    assert post(c,f'/shipping/boxes/{bid}/void',reason='Incorrect box dimensions').status_code==302
    assert c.get(f'/shipping/boxes/{bid}/print').status_code==404
    pack=rows(app,'SELECT pack_record_id FROM shipping_boxes WHERE id=?',(bid,))[0][0]
    assert post(c,f'/tickets/{tid}/shipping/boxes',pack_record_id=str(pack),weight_kg='3',dimensions_cm='50 x 40 x 30').status_code==302
    assert rows(app,'SELECT COUNT(*) FROM shipping_boxes WHERE voided=0')[0][0]==1


def test_serial_box_dispatch_preserves_individual_unit_progress(app):
    from test_serials import prepared_units,complete,inspect
    c,tid,units,ops=prepared_units(app)
    sign_in(c,'operator')
    for version,op in enumerate(ops,1):assert complete(c,units[0],op,version).status_code==302
    sign_in(c,'inspector');assert inspect(c,units[0]).status_code==302
    sign_in(c,'admin');post(c,'/admin/shipping',action='destination',name='Customer',address='123 Test Road',reason='Test')
    assert post(c,f'/tickets/{tid}/shipping',destination_id='1',purpose='order',method='fedex',label_owner='planner',reason='MTO').status_code==302
    sign_in(c,'operator');assert move(app,c,tid,'pack',unit_id=[units[0]['id']]).status_code==302
    pack=rows(app,"SELECT id FROM fulfillment_records WHERE kind='pack'")[0][0]
    assert post(c,f'/tickets/{tid}/shipping/boxes',pack_record_id=str(pack),weight_kg='1',dimensions_cm='10 x 20 x 30').status_code==302
    post(c,'/shipping',box_id='1');action(app,c,1,'request');sign_in(c,'planner');upload(app,c,1)
    assert depart(app,c,1).status_code==302
    departed=rows(app,"SELECT unit_id FROM fulfillment_active_units WHERE kind='depart'")
    assert [r[0] for r in departed]==[units[0]['id']]
    assert rows(app,'SELECT state FROM serial_units WHERE id=?',(units[1]['id'],))[0][0]=='ready'


def test_fake_private_storage_label_download_integrity(app):
    c,tid=setup(app);bid=box(app,c,tid);post(c,'/shipping',box_id=str(bid));action(app,c,1,'request');sign_in(c,'planner')
    class Store:
        def __init__(self):self.files={}
        def put(self,key,data,mime):self.files[key]=data
        def get(self,key,limit):return self.files[key]
    store=Store();app.extensions['file_storage']=store
    assert upload(app,c,1).status_code==302
    label=rows(app,'SELECT * FROM shipping_labels')[0]
    assert label['content'] is None and label['object_key'].startswith('1/')
    assert c.get(f"/shipping/1/labels/{label['id']}").status_code==200
    store.files[label['object_key']]=b'corrupt'
    assert c.get(f"/shipping/1/labels/{label['id']}").status_code==503


def test_shipping_audit_is_immutable(app):
    c,tid=setup(app)
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(app.config['DATABASE']) as conn:conn.execute("UPDATE shipping_events SET detail='changed'")


def test_pallet_request_requires_gross_weight_including_boxes(app):
    c,tid=setup(app);bid=box(app,c,tid);post(c,'/shipping',box_id=str(bid))
    assert action(app,c,1,'request',gross_weight_kg='1').status_code==400
    assert rows(app,'SELECT state FROM shipping_loads')[0][0]=='building'
    assert action(app,c,1,'request',gross_weight_kg='10').status_code==302
    assert b'10 kg gross' in c.get('/shipping/1').data


def test_concurrent_dispatch_records_one_departure(app):
    from concurrent.futures import ThreadPoolExecutor
    c,tid=setup(app);bid=box(app,c,tid);post(c,'/shipping',box_id=str(bid));action(app,c,1,'request');sign_in(c,'planner');upload(app,c,1)
    clients=[app.test_client(),app.test_client()]
    for client in clients:sign_in(client,'operator')
    version=str(rows(app,'SELECT version FROM shipping_loads WHERE id=1')[0][0])
    def submit(client):return post(client,'/shipping/1/depart',version=version,confirm='yes',notes='Physical pickup').status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(submit,clients))
    assert sorted(statuses)==[302,409]
    assert rows(app,"SELECT COUNT(*) FROM fulfillment_records WHERE kind='depart'")[0][0]==1
