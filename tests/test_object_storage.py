from io import BytesIO
import sqlite3
import pytest
from productionrunner.object_storage import StorageError, SupabaseStorage
from test_workflow import app, sign_in, post, rows
from test_parts import PDF, add_part, planner
from test_part_import import preview, DXF

class MemoryStorage:
    def __init__(self):self.objects={};self.reads=[];self.fail=False;self.on_put=None
    def put(self,key,data,mime):
        if self.fail:raise StorageError('secret provider details')
        if self.on_put:self.on_put()
        assert key not in self.objects
        self.objects[key]=data
    def get(self,key,limit):
        self.reads.append(key)
        if self.fail or key not in self.objects:raise StorageError('secret provider details')
        return self.objects[key]

@pytest.fixture
def store(app):
    storage=MemoryStorage();app.extensions['file_storage']=storage;return storage


def test_remote_pdf_dxf_roundtrip_inventory_and_immutability(app,store):
    c=planner(app)
    r=post(c,'/parts/new',pn='PN-001',revision='A',description='Remote',drawing=(BytesIO(PDF),'part.pdf'),dxf=(BytesIO(DXF),'part.dxf'))
    assert r.status_code==302
    p=rows(app,'SELECT * FROM part_revisions')[0];d=rows(app,'SELECT * FROM part_dxf')[0]
    assert p['drawing_pdf'] is None and d['content'] is None
    assert p['object_size']==len(PDF) and d['object_size']==len(DXF)
    assert c.get(r.location+'/drawing').data==PDF
    assert post(c,r.location+'/dxf/download').data==DXF
    sign_in(c,'admin')
    page=c.get('/admin/storage')
    assert page.status_code==200
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("UPDATE part_revisions SET object_key='changed'")
    sign_in(c,'operator')
    assert c.get(r.location+'/drawing').data==PDF
    assert add_part(c,pn='DENIED').status_code==403


def test_cross_tenant_and_unauthenticated_reads_never_contact_storage(app,store):
    c=planner(app);r=add_part(c)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        tenant=conn.execute("INSERT INTO tenants(slug,name) VALUES('beta','Beta')").lastrowid
        uid=conn.execute("SELECT id FROM users WHERE username='planner'").fetchone()[0]
        conn.execute("INSERT INTO memberships VALUES(?,?,'planner')",(tenant,uid))
    sign_in(c,'planner','beta')
    assert c.get(r.location+'/drawing').status_code==404
    assert post(c,r.location+'/dxf/download').status_code==404
    assert app.test_client().get(r.location+'/drawing').status_code==302
    assert store.reads==[]


def test_unavailable_and_corrupt_objects_fail_closed(app,store):
    c=planner(app);r=add_part(c);key=rows(app,'SELECT object_key FROM part_revisions')[0][0]
    store.objects[key]=b'x'*len(PDF)
    assert c.get(r.location+'/drawing').status_code==503
    store.fail=True
    response=c.get(r.location+'/drawing')
    assert response.status_code==503 and b'secret provider' not in response.data
    assert add_part(c,pn='FAIL').status_code==503
    assert len(rows(app,'SELECT * FROM part_revisions'))==1


def test_bulk_cloud_confirm_retries_and_rollback(app,store):
    c=planner(app);r=preview(c,keep=True)
    assert store.objects=={}
    assert post(c,r.location,action='confirm').status_code==302
    assert len(store.objects)==2
    p=rows(app,'SELECT * FROM part_revisions')[0]
    assert p['drawing_pdf'] is None
    assert c.get('/parts/'+p['id']+'/drawing').data==PDF
    assert post(c,r.location,action='confirm').status_code==302
    assert len(store.objects)==2
    assert len(rows(app,'SELECT * FROM storage_file_controls'))==2
    r=preview(c,csv='PN-002,A,Test part,a.pdf,a.dxf\n')
    store.fail=True
    assert post(c,r.location,action='confirm').status_code==503
    assert len(rows(app,'SELECT * FROM part_revisions'))==1
    assert rows(app,'SELECT result FROM part_imports WHERE token=?',(r.location.split('/')[-1],))[0][0] is None


def test_old_embedded_files_remain_readable_with_cloud_enabled(app,store):
    app.extensions.pop('file_storage');c=planner(app);r=add_part(c)
    app.extensions['file_storage']=store
    assert c.get(r.location+'/drawing').data==PDF
    assert store.reads==[]


def test_transport_rejects_redirects_and_bounds_download(monkeypatch):
    from urllib.error import HTTPError
    storage=SupabaseStorage('https://'+'a'*20+'.supabase.co','server-secret','files')
    class Opener:
        def open(self,req,timeout):
            assert req.get_header('Apikey')=='server-secret'
            raise HTTPError(req.full_url,302,'redirect',{},None)
    storage.opener=Opener()
    with pytest.raises(StorageError,match='temporarily unavailable'):storage.get('1/test.pdf',10)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,size):return b'x'*size
    storage.opener.open=lambda *a,**k:Response()
    with pytest.raises(StorageError,match='exceeded'):storage.get('1/test.pdf',10)


def test_permission_revoked_during_upload_prevents_catalog_commit(app,store):
    c=planner(app)
    def revoke():
        with sqlite3.connect(app.config['DATABASE']) as conn:
            conn.execute("UPDATE memberships SET roles='viewer' WHERE user_id=(SELECT id FROM users WHERE username='planner')")
    store.on_put=revoke
    assert add_part(c).status_code==403
    assert not rows(app,'SELECT * FROM part_revisions')
    # An unreferenced object is safer than deleting on an ambiguous commit failure.
    assert len(store.objects)==1


def test_live_staging_storage_routes(app):
    """Opt-in: synthetic local DB + actual staging bucket; delete only our probe objects."""
    import os,json
    from pathlib import Path
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    path=os.environ.get('PR_LIVE_STORAGE_CONFIG')
    if not path:pytest.skip('Requires explicit staging storage credentials')
    assert '127.0.0.1' in app.config['DATABASE'] and app.config['DATABASE'].endswith('_test')
    cfg=json.loads(Path(path).read_text())
    assert cfg['PR_SUPABASE_URL']=='https://imhplsizgijerkqemxjy.supabase.co'
    storage=SupabaseStorage(cfg['PR_SUPABASE_URL'],cfg['PR_SUPABASE_SECRET_KEY'],cfg['PR_STORAGE_BUCKET'])
    keys=[];original=storage.put
    def put(key,data,mime):
        keys.append(key) # Include ambiguous failed uploads in exact-key cleanup.
        original(key,data,mime)
    storage.put=put
    app.extensions['file_storage']=storage
    try:
        c=planner(app)
        response=post(c,'/parts/new',pn='LIVE-PROBE',revision='A',description='Synthetic connectivity check',drawing=(BytesIO(PDF),'probe.pdf'),dxf=(BytesIO(DXF),'probe.dxf'))
        assert response.status_code==302
        assert c.get(response.location+'/drawing').data==PDF
        assert post(c,response.location+'/dxf/download').data==DXF
        batch=preview(c,csv='LIVE-BULK,A,Probe bulk,a.pdf,a.dxf\n')
        assert post(c,batch.location,action='confirm').status_code==302
        part=rows(app,"SELECT id FROM part_revisions WHERE pn='LIVE-BULK'")[0][0]
        assert c.get('/parts/'+part+'/drawing').data==PDF
        assert post(c,'/parts/'+part+'/dxf/download').data==DXF
        assert all(r[0] is None for r in rows(app,'SELECT drawing_pdf FROM part_revisions'))
        with pytest.raises(HTTPError):
            urlopen(cfg['PR_SUPABASE_URL']+'/storage/v1/object/public/'+cfg['PR_STORAGE_BUCKET']+'/'+keys[0],timeout=15)
    finally:
        if keys:
            req=Request(cfg['PR_SUPABASE_URL']+'/storage/v1/object/'+cfg['PR_STORAGE_BUCKET'],data=json.dumps({'prefixes':keys}).encode(),method='DELETE',headers={'apikey':cfg['PR_SUPABASE_SECRET_KEY'],'Content-Type':'application/json'})
            with urlopen(req,timeout=30) as response:assert response.status==200
