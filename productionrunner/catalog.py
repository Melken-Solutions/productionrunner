"""Controlled corrections for unused revisions and reversible catalog removal."""
import json,re
from flask import abort,g,request,render_template,redirect,url_for
from .parts import find_part,MAX_PDF,MAX_DXF
from .object_storage import prepare
import hashlib

def used(conn,tenant,part):
    return conn.execute("SELECT 1 FROM part_revisions P WHERE P.tenant_id=? AND P.id=? AND (EXISTS(SELECT 1 FROM ticket_drawings WHERE tenant_id=P.tenant_id AND part_revision_id=P.id) OR EXISTS(SELECT 1 FROM tickets WHERE tenant_id=P.tenant_id AND pn=P.pn AND revision=P.revision) OR EXISTS(SELECT 1 FROM intake_requests WHERE tenant_id=P.tenant_id AND (part_id=P.id OR (pn=P.pn AND revision=P.revision))) OR EXISTS(SELECT 1 FROM part_plan_templates WHERE tenant_id=P.tenant_id AND part_id=P.id))",(tenant,part['id'])).fetchone() is not None

def controls(conn,tenant,pid):
    return conn.execute('SELECT * FROM part_catalog_controls WHERE tenant_id=? AND part_id=?',(tenant,pid)).fetchone() or dict(archived=0,version=0)

def active(conn,tenant,pid):
    if controls(conn,tenant,pid)['archived']:abort(409,'This revision was removed from the catalog. Restore it before using it for new work.')

def register(app,db,roles_required,now):
    @app.context_processor
    def context():
        return dict(catalog_control=lambda pid:controls(db(),g.tenant['id'],pid))

    @app.route('/parts/<part_id>/manage',methods=['GET','POST'])
    @roles_required('planner','admin')
    def manage_part(part_id):
        part=find_part(db(),g.tenant['id'],part_id)
        if request.method=='GET':
            history=db().execute('SELECT c.*,u.username FROM part_catalog_changes c JOIN users u ON u.id=c.actor_id WHERE c.tenant_id=? AND c.part_id=? ORDER BY c.id DESC',(g.tenant['id'],part_id)).fetchall()
            history=[dict(r,detail=json.loads(r['detail'])) for r in history]
            return render_template('part_manage.html',part=part,control=controls(db(),g.tenant['id'],part_id),in_use=used(db(),g.tenant['id'],part),history=history)
        action=request.form.get('action')
        reason=request.form.get('reason','').strip()
        if not 1<=len(reason)<=500:abort(400,'Provide a reason (up to 500 characters).')
        uploads={}
        if action=='edit':
            pn=request.form.get('pn','').strip();rev=request.form.get('revision','').strip();description=request.form.get('description','').strip()
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',pn) or not 1<=len(rev)<=40 or len(description)>500:abort(400,'Provide a valid PN, revision and description.')
            if used(db(),g.tenant['id'],part):abort(409,'This revision is already in use. Create a new revision instead.')
            for name,limit,ext in [('drawing',MAX_PDF,'.pdf'),('dxf',MAX_DXF,'.dxf')]:
                f=request.files.get(name)
                if not f or not f.filename:continue
                data=f.stream.read(limit+1)
                if len(data)>limit:abort(413,'File exceeds the upload size limit.')
                if not data or not f.filename.lower().endswith(ext) or (name=='drawing' and not data.startswith(b'%PDF-')):abort(400,'Choose a valid PDF drawing or DXF file.')
                uploads[name]=(data,prepare(g.tenant['id'],'pdf' if name=='drawing' else 'dxf',data))
        with db():
            db().execute('BEGIN IMMEDIATE')
            from .intake import actor
            actor(db(),g.tenant['id'],g.user['id'])
            part=find_part(db(),g.tenant['id'],part_id);control=controls(db(),g.tenant['id'],part_id)
            if str(control['version'])!=request.form.get('version'):abort(409,'Catalog entry changed. Reload it.')
            detail=dict(reason=reason,before={k:part[k] for k in ('pn','revision','description','sha256','drawing_url')})
            if action=='edit':
                active(db(),g.tenant['id'],part_id)
                if used(db(),g.tenant['id'],part):abort(409,'This revision is now in use. Create a new revision instead.')
                if db().execute('SELECT 1 FROM part_revisions WHERE tenant_id=? AND pn=? AND revision=? AND id<>?',(g.tenant['id'],pn,rev,part_id)).fetchone():abort(409,'That PN/revision already exists.')
                if db().execute('SELECT 1 FROM intake_requests WHERE tenant_id=? AND pn=? AND revision=? UNION ALL SELECT 1 FROM tickets WHERE tenant_id=? AND pn=? AND revision=?',(g.tenant['id'],pn,rev,g.tenant['id'],pn,rev)).fetchone():abort(409,'The target PN/revision is already referenced by work. Create a separate revision instead.')
                db().execute('UPDATE part_revisions SET pn=?,revision=?,description=? WHERE tenant_id=? AND id=?',(pn,rev,description,g.tenant['id'],part_id))
                if 'drawing' in uploads:
                    data,s=uploads['drawing']
                    db().execute('UPDATE part_revisions SET drawing_pdf=?,drawing_url=NULL,sha256=?,object_key=?,object_size=? WHERE tenant_id=? AND id=?',(s[0],hashlib.sha256(data).hexdigest(),s[1],s[2],g.tenant['id'],part_id))
                if 'dxf' in uploads:
                    data,s=uploads['dxf'];digest=hashlib.sha256(data).hexdigest()
                    old=db().execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(g.tenant['id'],part_id)).fetchone()
                    detail['previous_dxf_sha256']=old['sha256'] if old else None
                    if old:db().execute('UPDATE part_dxf SET content=?,sha256=?,uploaded_by=?,uploaded_at=?,object_key=?,object_size=? WHERE tenant_id=? AND part_revision_id=?',(s[0],digest,g.user['id'],now(),s[1],s[2],g.tenant['id'],part_id))
                    else:db().execute('INSERT INTO part_dxf(tenant_id,part_revision_id,content,sha256,uploaded_by,uploaded_at,object_key,object_size) VALUES(?,?,?,?,?,?,?,?)',(g.tenant['id'],part_id,s[0],digest,g.user['id'],now(),s[1],s[2]))
                    detail['dxf_sha256']=digest
                updated=find_part(db(),g.tenant['id'],part_id)
                detail['after']={k:updated[k] for k in ('pn','revision','description','sha256','drawing_url')}
                archived=0
            elif action in ('archive','restore'):archived=int(action=='archive')
            else:abort(400,'Choose edit, remove or restore.')
            db().execute('INSERT INTO part_catalog_controls(tenant_id,part_id,archived,version) VALUES(?,?,?,?) ON CONFLICT(part_id) DO UPDATE SET archived=excluded.archived,version=excluded.version',(g.tenant['id'],part_id,archived,control['version']+1))
            db().execute('INSERT INTO part_catalog_changes(tenant_id,part_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],part_id,g.user['id'],action,json.dumps(detail),now()))
            db().execute('INSERT INTO part_events(tenant_id,part_revision_id,actor_id,action,created_at) VALUES(?,?,?,?,?)',(g.tenant['id'],part_id,g.user['id'],'catalog_'+action,now()))
        return redirect(url_for('manage_part',part_id=part_id))
