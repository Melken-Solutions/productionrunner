from io import BytesIO
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_workflow import app, sign_in, post, rows
from test_parts import PDF, add_part

HEADER = 'pn,revision,description,drawing_file,dxf_file\n'
DXF = b'0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n'


def client_for(app, name='planner', tenant='alpha'):
    client = app.test_client()
    sign_in(client, name, tenant)
    return client


def preview(client, csv='PN-001,A,Test part,a.pdf,a.dxf\n', files=None, keep=False):
    if files is None:
        files = [(PDF, 'a.pdf'), (DXF, 'a.dxf')]
    return post(client, '/parts/import', mapping=(BytesIO((HEADER+csv).encode()), 'map.csv'), files=[(BytesIO(data), name) for data, name in files], keep='1' if keep else '0')


def test_preview_confirm_bytes_audit_keep_and_repeat(app):
    c = client_for(app)
    response = preview(c, keep=True)
    assert response.status_code == 302
    assert not rows(app, 'SELECT * FROM part_revisions')
    assert b'Create revision' in c.get(response.location).data
    assert post(c, response.location, action='confirm').status_code == 302
    part = rows(app, 'SELECT * FROM part_revisions')[0]
    assert part['drawing_pdf'] == PDF
    assert rows(app, 'SELECT content FROM part_dxf')[0][0] == DXF
    assert len(rows(app, 'SELECT * FROM storage_file_controls WHERE keep_forever=1')) == 2
    assert len(rows(app, "SELECT * FROM auth_events WHERE action='parts_imported'")) == 1
    assert len(rows(app, "SELECT * FROM auth_events WHERE action='storage_keep_updated'")) == 2
    assert rows(app, 'SELECT payload FROM part_imports')[0][0] is None
    assert b'Import complete' in c.get(response.location).data
    assert post(c, response.location, action='confirm').status_code == 302
    assert len(rows(app, 'SELECT * FROM part_events')) == 2
    assert not rows(app, 'PRAGMA foreign_key_check')
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert c.get('/parts/'+part['id']+'/drawing').data == PDF


def test_identical_retry_and_first_dxf_do_not_change_existing_retention(app):
    c = client_for(app)
    add_part(c)
    p = preview(c, keep=True)
    assert b'Add first DXF' in c.get(p.location).data
    post(c, p.location, action='confirm')
    assert len(rows(app, 'SELECT * FROM part_revisions')) == 1
    assert [r['kind'] for r in rows(app, 'SELECT * FROM storage_file_controls')] == ['dxf']
    p = preview(c, keep=True)
    assert b'Skip identical files' in c.get(p.location).data
    post(c, p.location, action='confirm')
    assert len(rows(app, 'SELECT * FROM part_events')) == 2


@pytest.mark.parametrize('csv,files,reason', [
    ('PN-001,A,Test part,a.pdf,\n', [(b'%PDF-changed','a.pdf')], b'Drawing conflict'),
    ('PN-001,A,Changed,a.pdf,\n', [(PDF,'a.pdf')], b'description differs'),
    ('NEW,A,,missing.pdf,\n', [(PDF,'missing.pdf.dxf')], b'not listed'),
    ('NEW,A,,,a.dxf\n', [(DXF,'a.dxf')], b'require a PDF'),
    ('NEW,A,,a.pdf,\nNEW,A,,a.pdf,\n', [(PDF,'a.pdf')], b'Duplicate PN'),
])
def test_bad_rows_block_entire_batch(app, csv, files, reason):
    c = client_for(app)
    add_part(c)
    response = preview(c, csv, files)
    assert response.status_code in (200,400)
    assert reason in response.data
    assert not rows(app, 'SELECT * FROM part_imports')
    assert len(rows(app, 'SELECT * FROM part_revisions')) == 1


def test_later_conflict_prevents_all_writes_and_cancel_cleans_staging(app):
    c = client_for(app)
    p = preview(c, 'PN-001,A,Test part,a.pdf,\nNEW,A,,a.pdf,\n', [(PDF,'a.pdf')])
    add_part(c)
    assert post(c, p.location, action='confirm').status_code == 409
    assert len(rows(app, 'SELECT * FROM part_revisions')) == 1
    assert post(c, p.location, action='cancel').status_code == 302
    assert not rows(app, 'SELECT * FROM part_imports')


def test_roles_csrf_tenant_and_owner_boundaries(app):
    c = client_for(app)
    p = preview(c)
    assert c.post(p.location, data={'action':'confirm'}).status_code == 400
    for role in ('operator','inspector','viewer'):
        other = client_for(app, role)
        assert other.get('/parts/import').status_code == 403
        assert post(other, p.location, action='confirm').status_code == 403
    admin = client_for(app, 'admin')
    assert admin.get(p.location).status_code == 404
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant','beta','--name','Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership','planner','--tenant','beta','--roles','planner']).exit_code == 0
    beta = client_for(app, tenant='beta')
    assert beta.get(p.location).status_code == 404
    assert post(beta,p.location,action='confirm').status_code == 404
    bp = preview(beta)
    assert post(beta,bp.location,action='confirm').status_code == 302
    assert post(c,p.location,action='confirm').status_code == 302
    assert len(rows(app,'SELECT * FROM part_revisions')) == 2


def test_expiry_replacement_and_cleanup(app):
    c = client_for(app)
    p = preview(c)
    p2 = preview(c)
    assert c.get(p.location).status_code == 404
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('UPDATE part_imports SET expires_at=0')
    assert post(c,p2.location,action='confirm').status_code == 410
    assert app.test_cli_runner().invoke(args=['clear-expired-imports']).exit_code == 0
    assert not rows(app,'SELECT * FROM part_imports')


def test_limits_names_and_csv_validation(app):
    c = client_for(app)
    for data,name in [(b'bad','a.pdf'),(b'','a.dxf'),(PDF,'../a.pdf'),(PDF,'a.txt')]:
        assert preview(c, files=[(data,name)]).status_code == 400
    assert preview(c,files=[(PDF,'a.pdf'),(PDF,'a.pdf')]).status_code == 400
    assert preview(c,files=[(b'%PDF-'+b'x'*(10*1024*1024),'a.pdf')]).status_code == 413
    assert preview(c,'PN,A,,a.pdf,\n'*201,[(PDF,'a.pdf')]).status_code == 400
    assert post(c,'/parts/import',mapping=(BytesIO(b'wrong\n'),'map.csv')).status_code == 400
    assert b'pn,revision,description,drawing_file,dxf_file' in c.get('/parts/import/template.csv').data


def test_audit_failure_rolls_back_entire_batch(app):
    c = client_for(app)
    p = preview(c,keep=True)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_import BEFORE INSERT ON auth_events WHEN NEW.action='parts_imported' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        post(c,p.location,action='confirm')
    for table in ('part_revisions','part_dxf','part_events','storage_file_controls'):
        assert not rows(app,'SELECT * FROM '+table)
    assert rows(app,'SELECT payload FROM part_imports')[0][0] is not None


def test_concurrent_confirm_imports_once(app):
    c = client_for(app)
    p = preview(c)
    clients = [client_for(app),client_for(app)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda client:post(client,p.location,action='confirm').status_code,clients))
    assert results == [302,302]
    assert len(rows(app,'SELECT * FROM part_revisions')) == 1
    assert len(rows(app,"SELECT * FROM auth_events WHERE action='parts_imported'")) == 1


def test_dxf_conflict_external_drawing_and_missing_file(app):
    c = client_for(app)
    p = preview(c)
    post(c,p.location,action='confirm')
    conflict = preview(c, files=[(PDF,'a.pdf'),(b'different','a.dxf')])
    assert b'DXF conflict' in conflict.data
    add_part(c,pn='LINK',link='https://example.com/drawing.pdf')
    conflict = preview(c,'LINK,A,Test part,a.pdf,\n',[(PDF,'a.pdf')])
    assert b'Drawing conflict' in conflict.data
    missing = preview(c,'NEW,A,,missing.pdf,\n',[])
    assert b'Missing or wrong file type' in missing.data
    assert len(rows(app,'SELECT * FROM part_revisions')) == 2


def test_revoked_uploader_cannot_confirm(app):
    c = client_for(app)
    p = preview(c)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("UPDATE memberships SET roles='viewer' WHERE user_id=(SELECT id FROM users WHERE username='planner')")
    assert post(c,p.location,action='confirm').status_code == 403
    assert not rows(app,'SELECT * FROM part_revisions')
