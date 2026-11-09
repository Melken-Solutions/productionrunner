"""Planner uploads into the immutable, tenant-scoped part revision library."""
import hashlib
import secrets
from flask import abort, request
from .parts import MAX_PDF, MAX_DXF, matching_part
from .object_storage import prepare


def save(conn, tenant, user, item, stamp):
    """Called inside the authorized request transaction; return uploaded evidence."""
    files = {}
    for field, limit, suffix in [('drawing', MAX_PDF, '.pdf'), ('dxf', MAX_DXF, '.dxf')]:
        upload = request.files.get(field)
        if not upload or not upload.filename:
            continue
        data = upload.stream.read(limit + 1)
        if len(data) > limit:
            abort(413, f'{suffix.upper()} must be {limit // (1024*1024)} MB or smaller.')
        if not data or not upload.filename.lower().endswith(suffix) or (field == 'drawing' and not data.startswith(b'%PDF-')):
            abort(400, f'Choose a valid {suffix.upper()} file.')
        files[field] = data
    if not files:
        return None
    part = matching_part(conn, tenant, item['pn'], item['revision'])
    if part and 'drawing' in files:
        abort(409, 'This PN/revision already has a drawing. Choose a new revision to upload a changed drawing.')
    if not part and 'drawing' not in files:
        abort(400, 'Upload a drawing PDF with the DXF for this new PN/revision.')
    if part and 'dxf' in files and conn.execute('SELECT 1 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?', (tenant, part['id'])).fetchone():
        abort(409, 'This PN/revision already has a DXF. Choose a new revision to change it.')
    part_id = part['id'] if part else 'PART-' + secrets.token_hex(12)
    evidence = dict(part_id=part_id, pn=item['pn'], revision=item['revision'])
    if not part:
        data = files['drawing']; stored = prepare(tenant, 'pdf', data)
        digest = hashlib.sha256(data).hexdigest()
        conn.execute('INSERT INTO part_revisions(id,tenant_id,pn,revision,description,drawing_pdf,sha256,created_by,created_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                     (part_id,tenant,item['pn'],item['revision'],item['description'][:500],stored[0],digest,user,stamp,stored[1],stored[2]))
        conn.execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)', (tenant,part_id,user,'part_revision_added',stamp))
        evidence['pdf_sha256'] = digest
    if 'dxf' in files:
        data = files['dxf']; stored = prepare(tenant, 'dxf', data)
        digest = hashlib.sha256(data).hexdigest()
        conn.execute('INSERT INTO part_dxf(tenant_id,part_revision_id,content,sha256,uploaded_by,uploaded_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?)',
                     (tenant,part_id,stored[0],digest,user,stamp,stored[1],stored[2]))
        conn.execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)', (tenant,part_id,user,'dxf_uploaded',stamp))
        evidence['dxf_sha256'] = digest
    return evidence
