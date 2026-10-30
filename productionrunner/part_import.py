"""Bounded, tenant-owned bulk import previews with atomic confirmation."""
import base64
import csv
import hashlib
import io
import json
import re
import secrets

from flask import abort, g, redirect, render_template, request, session, url_for, Response
from .admin import epoch
from .parts import MAX_PDF, MAX_DXF
from .object_storage import prepare as prepare_file
from .pin_auth import audit

COLUMNS = ['pn', 'revision', 'description', 'drawing_file', 'dxf_file']
MAX_TOTAL = 50 * 1024 * 1024


def read_batch():
    mapping = request.files.get('mapping')
    if not mapping:
        abort(400, 'Choose a CSV mapping file.')
    raw = mapping.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        abort(413, 'CSV must be 256 KB or smaller.')
    try:
        reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')), strict=True)
        if reader.fieldnames != COLUMNS:
            abort(400, 'CSV headers must be: ' + ','.join(COLUMNS))
        rows = []
        for row in reader:
            if len(rows) >= 200:
                abort(400, 'Use at most 200 rows per batch.')
            if None in row or any(value is None for value in row.values()):
                abort(400, 'Every CSV row must have five columns.')
            rows.append({key: value.strip() for key, value in row.items()})
    except (UnicodeError, csv.Error):
        abort(400, 'Use a valid UTF-8 CSV file.')
    if not rows:
        abort(400, 'CSV must contain at least one part.')
    files, total = {}, 0
    for upload in request.files.getlist('files'):
        name = upload.filename
        if not name:
            continue
        if name in files or '/' in name or '\\' in name or len(name) > 255:
            abort(400, 'Use unique filenames without directory paths (up to 255 characters).')
        kind = name.rsplit('.', 1)[-1].lower()
        if kind not in {'pdf', 'dxf'}:
            abort(400, 'Only PDF and DXF files are supported.')
        limit = MAX_PDF if kind == 'pdf' else MAX_DXF
        data = upload.read(limit + 1)
        total += len(data)
        if len(data) > limit or total > MAX_TOTAL:
            abort(413, 'Limits: PDF 10 MB, DXF 20 MB, total file content 50 MB.')
        if not data or (kind == 'pdf' and not data.startswith(b'%PDF-')):
            abort(400, 'Files must be nonempty; PDF files must have a PDF header.')
        files[name] = dict(kind=kind, sha=hashlib.sha256(data).hexdigest(), data=base64.b64encode(data).decode('ascii'))
    used = {row[key] for row in rows for key in ('drawing_file', 'dxf_file') if row[key]}
    if set(files) - used:
        abort(400, 'Some uploaded files are not listed in the CSV. Remove them or add their mapping.')
    return dict(rows=rows, files=files, keep=request.form.get('keep') == '1')


def review(conn, tenant, batch):
    result, seen = [], set()
    for number, row in enumerate(batch['rows'], 2):
        item = dict(row, line=number, errors=[], part_id=None, create=False, add_dxf=False)
        key = (row['pn'], row['revision'])
        if key in seen:
            item['errors'].append('Duplicate PN/revision in CSV.')
        seen.add(key)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', row['pn']) or not row['revision'] or len(row['revision']) > 40 or len(row['description']) > 500:
            item['errors'].append('Invalid PN, missing/long revision, or description over 500 characters.')
        if not row['drawing_file'] and not row['dxf_file']:
            item['errors'].append('Map at least one file.')
        for field, kind in [('drawing_file', 'pdf'), ('dxf_file', 'dxf')]:
            name = row[field]
            if name and (name not in batch['files'] or batch['files'][name]['kind'] != kind):
                item['errors'].append(f'Missing or wrong file type: {name}')
        existing = conn.execute('SELECT id,description,drawing_url,sha256 FROM part_revisions WHERE tenant_id=? AND pn=? AND revision=?', (tenant, *key)).fetchone()
        if not item['errors']:
            if existing:
                item['part_id'] = existing['id']
                if row['description'] and row['description'] != existing['description']:
                    item['errors'].append('Existing description differs; descriptions are immutable.')
                if row['drawing_file'] and (existing['drawing_url'] or existing['sha256'] != batch['files'][row['drawing_file']]['sha']):
                    item['errors'].append('Drawing conflict: use a new revision. Existing drawing will not be overwritten.')
                if row['dxf_file']:
                    dxf = conn.execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?', (tenant, existing['id'])).fetchone()
                    if dxf and dxf['sha256'] != batch['files'][row['dxf_file']]['sha']:
                        item['errors'].append('DXF conflict: use a new revision.')
                    item['add_dxf'] = not dxf
            else:
                if not row['drawing_file']:
                    item['errors'].append('New revisions require a PDF drawing; DXF is optional.')
                item['create'] = True
                item['add_dxf'] = bool(row['dxf_file'])
        item['status'] = 'Blocked' if item['errors'] else ('Create revision' if item['create'] else ('Add first DXF; retain existing drawing' if item['add_dxf'] else 'Skip identical files'))
        result.append(item)
    return result


def register_import(app, db, roles_required, now):
    def actor():
        member = db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?', (g.tenant['id'], g.user['id'])).fetchone()
        if not member or not {'admin', 'planner'} & set(member['roles'].split(',')) or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
            abort(403, 'Your access changed. Sign in again.')

    def owned(token):
        row = db().execute('SELECT * FROM part_imports WHERE token=? AND tenant_id=? AND actor_id=?', (token, g.tenant['id'], g.user['id'])).fetchone()
        if not row:
            abort(404)
        if row['expires_at'] <= app.config['AUTH_CLOCK']():
            abort(410, 'Preview expired. Upload the batch again.')
        return row

    @app.get('/parts/import/template.csv')
    @roles_required('planner', 'admin')
    def import_template():
        return Response(','.join(COLUMNS) + '\r\nBRKT-100,A,Mounting bracket,BRKT-100_A.pdf,BRKT-100_A.dxf\r\n', mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=parts-import-template.csv'})

    @app.route('/parts/import', methods=['GET', 'POST'])
    @roles_required('planner', 'admin')
    def import_parts():
        if request.method == 'GET':
            return render_template('part_import.html')
        batch = read_batch()
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor()
            report = review(db(), g.tenant['id'], batch)
            if any(row['errors'] for row in report):
                return render_template('part_import.html', report=report, blocked=True)
            token = secrets.token_hex(24)
            # One pending batch per person/organization bounds retained upload bytes.
            db().execute('DELETE FROM part_imports WHERE tenant_id=? AND (expires_at<=? OR (actor_id=? AND payload IS NOT NULL))', (g.tenant['id'], app.config['AUTH_CLOCK'](), g.user['id']))
            db().execute('INSERT INTO part_imports VALUES(?,?,?,?,?,?,?)', (token, g.tenant['id'], g.user['id'], app.config['AUTH_CLOCK']()+3600, json.dumps(batch), json.dumps(report), None))
        return redirect(url_for('import_preview', token=token))

    @app.route('/parts/import/<token>', methods=['GET', 'POST'])
    @roles_required('planner', 'admin')
    def import_preview(token):
        if request.method == 'GET':
            stage = owned(token)
            return render_template('part_import.html', report=json.loads(stage['report']), token=token, result=json.loads(stage['result']) if stage['result'] else None, keep=json.loads(stage['payload'])['keep'] if stage['payload'] else False)
        # Upload outside the write lock, then recheck the preview and actor under lock.
        staged_files = {}
        if request.form.get('action') == 'confirm':
            actor()
            before = owned(token)
            if not before['result']:
                pending = json.loads(before['payload'])
                checked = review(db(), g.tenant['id'], pending)
                if checked != json.loads(before['report']):
                    abort(409, 'The library changed since preview. Upload again.')
                for row in checked:
                    for flag, field, kind in [('create','drawing_file','pdf'),('add_dxf','dxf_file','dxf')]:
                        if row[flag] and row[field] not in staged_files:
                            file = pending['files'][row[field]]
                            staged_files[row[field]] = prepare_file(g.tenant['id'], kind, base64.b64decode(file['data']))
        with db():
            db().execute('BEGIN IMMEDIATE')
            actor()
            stage = owned(token)
            if stage['result']:
                return redirect(url_for('import_preview', token=token))
            if request.form.get('action') == 'cancel':
                db().execute('DELETE FROM part_imports WHERE token=?', (token,))
                return redirect(url_for('import_parts'))
            if request.form.get('action') != 'confirm':
                abort(400, 'Confirm or cancel the preview.')
            batch = json.loads(stage['payload'])
            report = review(db(), g.tenant['id'], batch)
            if report != json.loads(stage['report']):
                abort(409, 'The library changed since preview. Upload again to review the current results. Nothing was imported.')
            counts = dict(created=0, dxf_added=0, skipped=0)
            for row in report:
                part_id = row['part_id'] or 'PART-' + secrets.token_hex(12)
                added = []
                if row['create']:
                    pdf = batch['files'][row['drawing_file']]
                    stored = staged_files[row['drawing_file']]
                    db().execute('INSERT INTO part_revisions(id,tenant_id,pn,revision,description,drawing_pdf,drawing_url,sha256,created_by,created_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (part_id, g.tenant['id'], row['pn'], row['revision'], row['description'], stored[0], None, pdf['sha'], g.user['id'], now(), stored[1], stored[2]))
                    added.append(('pdf', 'part_revision_added'))
                    counts['created'] += 1
                if row['add_dxf']:
                    dxf = batch['files'][row['dxf_file']]
                    stored = staged_files[row['dxf_file']]
                    db().execute('INSERT INTO part_dxf(tenant_id,part_revision_id,content,sha256,uploaded_by,uploaded_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?)', (g.tenant['id'], part_id, stored[0], dxf['sha'], g.user['id'], now(), stored[1], stored[2]))
                    added.append(('dxf', 'dxf_uploaded'))
                    counts['dxf_added'] += 1
                if not added:
                    counts['skipped'] += 1
                for kind, action in added:
                    db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)', (g.tenant['id'], part_id, g.user['id'], action, now()))
                    if batch['keep']:
                        db().execute('INSERT INTO storage_file_controls VALUES(?,?,?,?,1)', (g.tenant['id'], part_id, kind, 1))
                        audit(db(), g.tenant['id'], g.user['id'], 'storage_keep_updated', f"user:{g.user['id']}", app.config['AUTH_CLOCK'](), json.dumps(dict(reason='Bulk import: keep new master files permanently', part_id=part_id, kind=kind, before=0, after=1, import_id=token)))
            audit(db(), g.tenant['id'], g.user['id'], 'parts_imported', f"user:{g.user['id']}", app.config['AUTH_CLOCK'](), json.dumps(dict(import_id=token, **counts, keep_new_files=batch['keep'], rows=[dict(pn=r['pn'], revision=r['revision'], status=r['status']) for r in report])))
            db().execute('UPDATE part_imports SET payload=NULL,result=? WHERE token=?', (json.dumps(counts), token))
        return redirect(url_for('import_preview', token=token))

    @app.cli.command('clear-expired-imports')
    def clear_expired_imports():
        """Remove expired temporary previews, never catalog files or audit history."""
        g.app_db = app.config['DATABASE']
        with db():
            db().execute('DELETE FROM part_imports WHERE expires_at<=?', (app.config['AUTH_CLOCK'](),))
