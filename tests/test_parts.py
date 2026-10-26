from io import BytesIO
import sqlite3

import pytest

from test_workflow import app, sign_in, post, create_ticket, rows

PDF = b'%PDF-1.4\n% Test fixture header; not a rendered drawing\n%%EOF'


def add_part(client, pn='PN-001', revision='A', link=None, data=PDF):
    fields = dict(pn=pn, revision=revision, description='Test part')
    if link is not None:
        fields['drawing_url'] = link
    else:
        fields['drawing'] = (BytesIO(data), 'drawing.pdf')
    return post(client, '/parts/new', **fields)


def planner(app):
    client = app.test_client()
    sign_in(client, 'planner')
    return client


def test_upload_select_and_auto_match_ticket_and_serial(app):
    client = planner(app)
    response = add_part(client)
    assert response.status_code == 302
    part = response.location.split('/')[-1]
    assert b'PN-001' in client.get('/parts?q=PN-001').data
    assert b'No matching' in client.get('/parts?q=NONEXISTENT').data
    assert part.encode() in client.get('/tickets/new').data
    drawing = client.get('/parts/' + part + '/drawing')
    assert drawing.data == PDF and drawing.mimetype == 'application/pdf'
    assert drawing.headers['Content-Security-Policy'] == "sandbox; default-src 'none'"
    assert drawing.headers['X-Content-Type-Options'] == 'nosniff'
    ticket = create_ticket(client, part_revision_id=part, pn='IGNORED', revision='IGNORED', tracking='serial', serials='S1\nS2')
    assert ticket.status_code == 302
    record = rows(app, 'SELECT pn,revision FROM tickets')[0]
    assert tuple(record) == ('PN-001', 'A')
    assert b'Open drawing PDF' in client.get(ticket.location).data
    unit = rows(app, 'SELECT id FROM serial_units')[0][0]
    assert b'Open drawing PDF' in client.get('/units/' + unit).data
    # Manual entry of an exact catalog PN/revision also auto-matches.
    assert create_ticket(client).status_code == 302
    assert len(rows(app, 'SELECT * FROM ticket_drawings')) == 2
    assert len(rows(app, "SELECT * FROM events WHERE action='drawing_attached'")) == 2
    assert not rows(app, 'PRAGMA foreign_key_check')


def test_external_link_is_not_fetched_and_shows_warning(app):
    client = planner(app)
    response = add_part(client, link='https://drawings.example.invalid/PN-001/rev-A.pdf')
    assert response.status_code == 302
    page = client.get(response.location)
    assert b'file at that URL may change' in page.data
    assert b'https://drawings.example.invalid/PN-001/rev-A.pdf' in page.data
    assert client.get(response.location + '/drawing').location == response.location
    assert create_ticket(client).status_code == 302


@pytest.mark.parametrize('link', ['javascript:alert(1)', 'file:///tmp/file.pdf', 'http://example.com/file.pdf',
                                  'https://user:secret@example.com/a.pdf', 'https://', 'https://example.com/\nfile.pdf',
                                  'https://example.com:abc/a.pdf', 'https://example.com\\evil'])
def test_invalid_links_rejected(app, link):
    client = planner(app)
    assert add_part(client, link=link).status_code == 400
    assert not rows(app, 'SELECT * FROM part_revisions')
    assert not rows(app, 'SELECT * FROM part_events')


def test_upload_limits_and_exclusive_source(app):
    client = planner(app)
    assert add_part(client, data=b'<html>bad</html>').status_code == 400
    assert add_part(client, data=b'%PDF-' + b'x' * (10 * 1024 * 1024)).status_code == 413
    assert post(client, '/parts/new', pn='PN', revision='A').status_code == 400
    assert post(client, '/parts/new', pn='PN', revision='A', drawing=(BytesIO(PDF), 'a.pdf'), drawing_url='https://example.com/a.pdf').status_code == 400
    # Ordinary endpoints retain their original smaller request cap.
    assert create_ticket(client, instructions='x' * 70000).status_code == 413
    assert not rows(app, 'SELECT * FROM part_revisions')


def test_revision_immutable_duplicate_and_new_revision_does_not_retarget(app):
    client = planner(app)
    a = add_part(client).location.split('/')[-1]
    ticket = create_ticket(client).location.split('/')[-1]
    assert add_part(client).status_code == 409
    assert add_part(client, revision='B', data=b'%PDF-1.4\nNEW\n%%EOF').status_code == 302
    assert rows(app, 'SELECT part_revision_id FROM ticket_drawings WHERE ticket_id=?', (ticket,))[0][0] == a
    assert client.get('/parts/' + a + '/drawing').data == PDF
    with sqlite3.connect(app.config['DATABASE']) as conn:
        for sql in ["UPDATE part_revisions SET revision='C'", 'DELETE FROM part_revisions',
                    'DELETE FROM ticket_drawings', "UPDATE ticket_drawings SET part_revision_id='wrong'",
                    'DELETE FROM part_events', "UPDATE part_events SET action='wrong'"]:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)


def test_existing_ticket_exact_match_explicit_attachment_and_stale_guard(app):
    client = planner(app)
    ticket = create_ticket(client).location.split('/')[-1]
    assert post(client, '/tickets/' + ticket + '/drawing', version='1').status_code == 404
    assert add_part(client, revision='B').status_code == 302
    assert post(client, '/tickets/' + ticket + '/drawing', version='1').status_code == 404
    assert add_part(client).status_code == 302
    assert not rows(app, 'SELECT * FROM ticket_drawings')
    assert post(client, '/tickets/' + ticket + '/drawing', version='0').status_code == 409
    assert post(client, '/tickets/' + ticket + '/drawing', version='1').status_code == 302
    assert post(client, '/tickets/' + ticket + '/drawing', version='2').status_code == 409
    assert len(rows(app, "SELECT * FROM events WHERE action='drawing_attached'")) == 1


def test_tenant_role_and_csrf_boundaries(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    ticket = create_ticket(client, pn='OTHER').location.split('/')[-1]
    for role in ['operator', 'inspector', 'viewer']:
        sign_in(client, role)
        assert client.get('/parts/' + part + '/drawing').status_code == 200
        assert add_part(client, pn='DENIED').status_code == 403
        assert post(client, '/tickets/' + ticket + '/drawing', version='1').status_code == 403
    sign_in(client, 'admin')
    assert add_part(client, pn='ADMIN-PART').status_code == 302
    assert client.post('/parts/new', data={'pn': 'NO-CSRF'}).status_code == 400
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'planner', '--tenant', 'beta', '--roles', 'planner']).exit_code == 0
    sign_in(client, 'planner', 'beta')
    assert part.encode() not in client.get('/parts').data
    assert part.encode() not in client.get('/tickets/new').data
    assert client.get('/parts/' + part).status_code == 404
    assert client.get('/parts/' + part + '/drawing').status_code == 404
    assert create_ticket(client, part_revision_id=part).status_code == 404
    assert post(client, '/tickets/' + ticket + '/drawing', version='1').status_code == 404
    assert add_part(client).status_code == 302  # Same PN/revision in another tenant.


def test_ticket_attachment_rolls_back_with_failed_audit(app):
    client = planner(app)
    ticket = create_ticket(client).location.split('/')[-1]
    assert add_part(client).status_code == 302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_drawing_event BEFORE INSERT ON events WHEN NEW.action='drawing_attached' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        post(client, '/tickets/' + ticket + '/drawing', version='1')
    assert not rows(app, 'SELECT * FROM ticket_drawings')
    assert rows(app, 'SELECT version FROM tickets')[0][0] == 1


def test_idempotent_upgrade_preserves_parts_and_drawing_bytes(app):
    client = planner(app)
    part = add_part(client).location.split('/')[-1]
    assert create_ticket(client).status_code == 302
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert client.get('/parts/' + part + '/drawing').data == PDF
    assert len(rows(app, 'SELECT * FROM ticket_drawings')) == 1
