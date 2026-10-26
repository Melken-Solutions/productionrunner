"""Tenant part/revision library with immutable PDF snapshots or external links."""
import hashlib
from io import BytesIO
import re
import secrets
from urllib.parse import urlsplit

import sqlite3
from flask import abort, g, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

MAX_PDF = 10 * 1024 * 1024
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
        return {'drawing_for': lambda ticket: ticket_drawing(db(), g.tenant['id'], ticket['id'])}

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
        part_id = 'PART-' + secrets.token_hex(12)
        try:
            with db():
                db().execute('INSERT INTO part_revisions VALUES(?,?,?,?,?,?,?,?,?,?)',
                             (part_id, g.tenant['id'], pn, revision, description, data, link or None, digest, g.user['id'], now()))
                db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',
                             (g.tenant['id'], part_id, g.user['id'], 'part_revision_added', now()))
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
        data = db().execute('SELECT drawing_pdf FROM part_revisions WHERE tenant_id=? AND id=?', (g.tenant['id'], part_id)).fetchone()[0]
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
