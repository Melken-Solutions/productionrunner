"""Tenant part/revision library with immutable PDF snapshots or external links."""
import hashlib
from io import BytesIO
import re
import secrets
from urllib.parse import urlsplit

import sqlite3
from flask import abort, g, redirect, render_template, request, send_file, url_for, session
from werkzeug.utils import secure_filename

from .object_storage import prepare as prepare_file, read as read_file

MAX_PDF = 10 * 1024 * 1024
MAX_DXF = 20 * 1024 * 1024
FIELDS = 'id,tenant_id,pn,revision,description,drawing_url,sha256,created_by,created_at'


def find_part(conn, tenant_id, part_id):
    part = conn.execute(f'SELECT {FIELDS} FROM part_revisions WHERE tenant_id=? AND id=?', (tenant_id, part_id)).fetchone()
    if not part:
        abort(404, 'Unknown part revision.')
    return part


def matching_part(conn, tenant_id, pn, revision):
    return conn.execute(f'SELECT {FIELDS} FROM part_revisions WHERE tenant_id=? AND pn=? AND revision=?', (tenant_id, pn, revision)).fetchone()


def ticket_drawing(conn, tenant_id, ticket_id):
    return conn.execute('SELECT p.id,p.pn,p.revision,p.drawing_url,p.sha256 FROM ticket_drawings d '
                        'JOIN part_revisions p ON p.id=d.part_revision_id AND p.tenant_id=d.tenant_id '
                        'WHERE d.tenant_id=? AND d.ticket_id=?', (tenant_id, ticket_id)).fetchone()


def register_parts(app, db, roles_required, now, lock_ticket, event):
    @app.context_processor
    def drawing_context():
        return {'drawing_for': lambda ticket: ticket_drawing(db(), g.tenant['id'], ticket['id']),
                'dxf_for': lambda part_id: db().execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?', (g.tenant['id'], part_id)).fetchone()}

    def check_writer():
        from .admin import epoch
        db().execute('BEGIN IMMEDIATE')
        member = db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?', (g.tenant['id'],g.user['id'])).fetchone()
        if not member or not {'admin','planner'} & set(member['roles'].split(',')) or epoch(db(),g.tenant['id'],g.user['id']) != session.get('membership_epoch',0):
            abort(403,'Your access changed. Sign in again.')

    def read_dxf(required=False):
        upload = request.files.get('dxf')
        if not upload or not upload.filename:
            if required:
                abort(400, 'Choose a DXF file.')
            return None
        if not upload.filename.lower().endswith('.dxf'):
            abort(400, 'Choose a file with the .dxf extension.')
        data = upload.stream.read(MAX_DXF + 1)
        if len(data) > MAX_DXF:
            abort(413, 'DXF must be 20 MB or smaller.')
        if not data:
            abort(400, 'DXF file must not be empty.')
        return data

    def save_dxf(part_id, data, stored):
        db().execute('INSERT INTO part_dxf(tenant_id,part_revision_id,content,sha256,uploaded_by,uploaded_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?)',
                     (g.tenant['id'], part_id, stored[0], hashlib.sha256(data).hexdigest(), g.user['id'], now(), stored[1], stored[2]))
        db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',
                     (g.tenant['id'], part_id, g.user['id'], 'dxf_uploaded', now()))

    @app.post('/parts/<part_id>/dxf/upload')
    @roles_required('planner', 'admin')
    def upload_dxf(part_id):
        find_part(db(), g.tenant['id'], part_id)
        data = read_dxf(required=True)
        if db().execute('SELECT 1 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(g.tenant['id'],part_id)).fetchone():
            abort(409, 'This revision already has a DXF.')
        stored = prepare_file(g.tenant['id'], 'dxf', data)
        try:
            with db():
                check_writer()
                save_dxf(part_id, data, stored)
        except sqlite3.IntegrityError:
            abort(409, 'This revision already has a DXF. Add a new part revision to change it.')
        return redirect(url_for('part_detail', part_id=part_id))

    @app.post('/parts/<part_id>/dxf/download')
    @roles_required()
    def download_dxf(part_id):
        part = find_part(db(), g.tenant['id'], part_id)
        record = db().execute('SELECT content,object_key,object_size,sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',
                              (g.tenant['id'], part_id)).fetchone()
        if not record:
            abort(404, 'No DXF attached to this revision.')
        data = read_file(g.tenant['id'], 'dxf', record, 'content')
        with db():
            db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',
                         (g.tenant['id'], part_id, g.user['id'], 'dxf_download_requested', now()))
        response = send_file(BytesIO(data), mimetype='application/octet-stream', as_attachment=True,
                             download_name=secure_filename(f"{part['pn']}-rev-{part['revision']}.dxf"))
        response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response

    @app.get('/parts')
    @roles_required()
    def parts_library():
        query = '%' + request.args.get('q', '').strip()[:100] + '%'
        parts = db().execute(f'SELECT {FIELDS} FROM part_revisions WHERE tenant_id=? AND (pn LIKE ? OR description LIKE ?) ORDER BY pn,revision', (g.tenant['id'], query, query)).fetchall()
        return render_template('parts.html', parts=parts)

    @app.route('/parts/new', methods=['GET', 'POST'])
    @roles_required('planner', 'admin')
    def new_part():
        if request.method == 'GET':
            return render_template('part_new.html')
        pn, revision = request.form.get('pn', '').strip(), request.form.get('revision', '').strip()
        description = request.form.get('description', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', pn) or not revision or len(revision) > 40 or len(description) > 500:
            abort(400, 'Provide one valid PN, revision (up to 40 characters), and description (up to 500 characters).')
        upload = request.files.get('drawing')
        link = request.form.get('drawing_url', '').strip()
        has_upload = bool(upload and upload.filename)
        if has_upload == bool(link):
            abort(400, 'Provide exactly one PDF upload or HTTPS drawing link.')
        data, digest = None, None
        if has_upload:
            data = upload.stream.read(MAX_PDF + 1)
            if len(data) > MAX_PDF:
                abort(413, 'PDF must be 10 MB or smaller.')
            if not upload.filename.lower().endswith('.pdf') or not data.startswith(b'%PDF-'):
                abort(400, 'Upload a PDF file. Other file formats are not supported.')
            digest = hashlib.sha256(data).hexdigest()
        else:
            try:
                parsed = urlsplit(link)
                valid = parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                parsed.port  # Reject malformed ports.
            except ValueError:
                valid = False
            if not valid or len(link) > 2048 or any(ord(ch) < 33 for ch in link) or '\\' in link:
                abort(400, 'Use an HTTPS link without embedded credentials or whitespace.')
        dxf_data = read_dxf()
        part_id = 'PART-' + secrets.token_hex(12)
        if matching_part(db(),g.tenant['id'],pn,revision):
            abort(409, 'That PN/revision already exists.')
        pdf_stored = prepare_file(g.tenant['id'], 'pdf', data)
        dxf_stored = prepare_file(g.tenant['id'], 'dxf', dxf_data)
        try:
            with db():
                check_writer()
                db().execute('INSERT INTO part_revisions(id,tenant_id,pn,revision,description,drawing_pdf,drawing_url,sha256,created_by,created_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (part_id, g.tenant['id'], pn, revision, description, pdf_stored[0], link or None, digest, g.user['id'], now(), pdf_stored[1], pdf_stored[2]))
                db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',
                             (g.tenant['id'], part_id, g.user['id'], 'part_revision_added', now()))
                if dxf_data is not None:
                    save_dxf(part_id, dxf_data, dxf_stored)
        except sqlite3.IntegrityError:
            abort(409, 'That PN/revision already exists. Add a new revision to change its drawing.')
        return redirect(url_for('part_detail', part_id=part_id))

    @app.get('/parts/<part_id>')
    @roles_required()
    def part_detail(part_id):
        part = find_part(db(), g.tenant['id'], part_id)
        history = db().execute('SELECT e.*,u.username FROM part_events e JOIN users u ON u.id=e.actor_id '
                               'WHERE e.tenant_id=? AND e.part_revision_id=? ORDER BY e.id', (g.tenant['id'], part_id)).fetchall()
        return render_template('part.html', part=part, history=history)

    @app.get('/parts/<part_id>/drawing')
    @roles_required()
    def drawing(part_id):
        part = find_part(db(), g.tenant['id'], part_id)
        if part['drawing_url']:
            # Show the destination and its mutability warning before leaving the app.
            return redirect(url_for('part_detail', part_id=part_id))
        record = db().execute('SELECT drawing_pdf,object_key,object_size,sha256 FROM part_revisions WHERE tenant_id=? AND id=?', (g.tenant['id'], part_id)).fetchone()
        data = read_file(g.tenant['id'], 'pdf', record, 'drawing_pdf')
        response = send_file(BytesIO(data), mimetype='application/pdf', as_attachment=False,
                             download_name=secure_filename(f"{part['pn']}-rev-{part['revision']}.pdf"))
        response.headers['Content-Security-Policy'] = "sandbox; default-src 'none'"
        return response

    @app.post('/tickets/<ticket_id>/drawing')
    @roles_required('planner', 'admin')
    def attach_drawing(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            if ticket_drawing(db(), g.tenant['id'], ticket_id):
                abort(409, 'This ticket already has a fixed drawing reference.')
            part = matching_part(db(), g.tenant['id'], ticket['pn'], ticket['revision'])
            if not part:
                abort(404, 'Add the exact PN/revision to Parts & drawings first.')
            db().execute('INSERT INTO ticket_drawings VALUES(?,?,?)', (g.tenant['id'], ticket_id, part['id']))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id))
            event(ticket_id, 'drawing_attached', f"{part['pn']} rev {part['revision']}; {part['id']}")
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))
