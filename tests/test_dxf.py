from io import BytesIO
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_workflow import app, sign_in, post, create_ticket, rows
from test_parts import add_part, planner, PDF

DXF = b'0\r\nSECTION\r\n2\r\nENTITIES\r\n0\r\nENDSEC\r\n0\r\nEOF\r\n'


def upload(client, part, data=DXF, filename='laser.dxf'):
    return post(client, '/parts/' + part + '/dxf/upload', dxf=(BytesIO(data), filename))


def download(client, part):
    return post(client, '/parts/' + part + '/dxf/download')


def test_new_revision_with_dxf_operator_download_from_serial(app):
    client = planner(app)
    result = post(client, '/parts/new', pn='PN-001', revision='A', description='Laser part',
                  drawing=(BytesIO(PDF), 'drawing.pdf'), dxf=(BytesIO(DXF), 'source.dxf'))
    assert result.status_code == 302
    part = result.location.split('/')[-1]
    ticket = create_ticket(client, part_revision_id=part, tracking='serial', serials='S1\nS2')
    assert b'Download DXF for laser' in client.get(ticket.location).data
    unit = rows(app, 'SELECT id FROM serial_units')[0][0]
    sign_in(client, 'operator')
    assert b'Download DXF for laser' in client.get('/units/' + unit).data
    response = download(client, part)
    assert response.status_code == 200 and response.data == DXF
    assert response.mimetype == 'application/octet-stream'
    assert 'attachment;' in response.headers['Content-Disposition']
    assert 'PN-001-rev-A.dxf' in response.headers['Content-Disposition']
    assert response.headers['Cache-Control'] == 'no-store'
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'ready'
    assert not rows(app, 'SELECT * FROM unit_completions')
    assert len(rows(app, "SELECT * FROM part_events WHERE action='dxf_download_requested'")) == 1


def test_existing_revision_attach_once_and_new_revision_keeps_old_file(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    ticket = create_ticket(client).location
    assert download(client, part).status_code == 404
    assert upload(client, part).status_code == 302
    assert b'Download DXF for laser' in client.get(ticket).data
    assert upload(client, part, b'replacement').status_code == 409
    second = add_part(client, revision='B').location.split('/')[-1]
    assert upload(client, second, b'NEW-DXF').status_code == 302
    assert download(client, part).data == DXF
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ['DELETE FROM part_dxf', "UPDATE part_dxf SET content=X'00'"]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert download(client, part).data == DXF


@pytest.mark.parametrize('data,filename,status', [(b'', 'empty.dxf', 400), (DXF, 'bad.exe', 400),
                                                (b'x' * (20*1024*1024+1), 'big.dxf', 413)])
def test_invalid_dxf_rejected(app, data, filename, status):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    assert upload(client, part, data, filename).status_code == status
    assert not rows(app, 'SELECT * FROM part_dxf')


def test_binary_bytes_preserved_with_external_drawing(app):
    client = planner(app)
    part = add_part(client, link='https://example.invalid/drawing.pdf').location.split('/')[-1]
    data = b'AutoCAD Binary DXF\r\n\x1a\x00\x00\xff\x80'
    assert upload(client, part, data, 'LASER.DXF').status_code == 302
    assert download(client, part).data == data


def test_role_tenant_and_csrf_boundaries(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    sign_in(client, 'operator')
    assert upload(client, part).status_code == 403
    sign_in(client, 'admin')
    assert upload(client, part).status_code == 302
    assert client.post('/parts/' + part + '/dxf/download').status_code == 400
    assert client.post('/parts/' + part + '/dxf/upload').status_code == 400
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'admin', '--tenant', 'beta', '--roles', 'admin']).exit_code == 0
    sign_in(client, 'admin', 'beta')
    assert download(client, part).status_code == 404
    assert upload(client, part).status_code == 404
    assert not rows(app, "SELECT * FROM part_events WHERE action='dxf_download_requested'")


def test_upload_audit_failure_rolls_back(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_dxf BEFORE INSERT ON part_events WHEN NEW.action='dxf_uploaded' BEGIN SELECT RAISE(ABORT,'test'); END")
    assert upload(client, part).status_code == 409
    assert not rows(app, 'SELECT * FROM part_dxf')


def test_concurrent_upload_cannot_replace_first_file(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    clients = [app.test_client(), app.test_client()]
    for c in clients:
        sign_in(c, 'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: upload(c, part).status_code, clients))
    assert sorted(results) == [302, 409]
    assert len(rows(app, "SELECT * FROM part_events WHERE action='dxf_uploaded'")) == 1
