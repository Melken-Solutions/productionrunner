"""Read-only retention projection and audited tenant policies; no deletion worker."""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from flask import abort, g, redirect, render_template, request, session, url_for
from .admin import epoch
from .pin_auth import audit

DEFAULT = dict(pdf_days=None, dxf_days=None, grace_days=30, version=0)


def instant(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def policy(conn, tenant):
    row = conn.execute('SELECT * FROM storage_policies WHERE tenant_id=?', (tenant,)).fetchone()
    return dict(row) if row else DEFAULT.copy()


def inventory(conn, tenant, settings, clock):
    # Read lengths/hashes, never load document bytes just to render the dashboard.
    files = [dict(r) for r in conn.execute("SELECT p.id,p.pn,p.revision,'pdf' AS kind,p.created_at,p.sha256,length(p.drawing_pdf) AS size FROM part_revisions p WHERE p.tenant_id=? AND p.drawing_pdf IS NOT NULL UNION ALL SELECT p.id,p.pn,p.revision,'dxf',d.uploaded_at,d.sha256,length(d.content) FROM part_dxf d JOIN part_revisions p ON p.tenant_id=d.tenant_id AND p.id=d.part_revision_id WHERE d.tenant_id=?", (tenant, tenant))]
    references = defaultdict(list)
    for row in conn.execute('SELECT p.id AS part_id,t.id,t.state,c.created_at AS cancelled_at FROM part_revisions p JOIN tickets t ON t.tenant_id=p.tenant_id AND (EXISTS(SELECT 1 FROM ticket_drawings m WHERE m.tenant_id=t.tenant_id AND m.ticket_id=t.id AND m.part_revision_id=p.id) OR (t.pn=p.pn AND t.revision=p.revision)) LEFT JOIN ticket_cancellations c ON c.tenant_id=t.tenant_id AND c.ticket_id=t.id WHERE p.tenant_id=?', (tenant,)):
        references[row['part_id']].append(row)
    closures = defaultdict(list)
    for r in conn.execute("SELECT ticket_id,created_at FROM events WHERE tenant_id=? AND action IN ('inspection_pass','unit_inspection_pass','rework_inspection_pass')", (tenant,)):
        closures[r['ticket_id']].append(instant(r['created_at']))
    unsafe_units = {r[0] for r in conn.execute("SELECT DISTINCT ticket_id FROM serial_units WHERE tenant_id=? AND state!='accepted'", (tenant,))}
    keeps = {(r['part_revision_id'],r['kind']):dict(r) for r in conn.execute('SELECT * FROM storage_file_controls WHERE tenant_id=?', (tenant,))}
    duplicates = defaultdict(list)
    for f in files:
        keep = keeps.get((f['id'],f['kind']), {})
        f.update(keep=bool(keep.get('keep_forever')), control_version=keep.get('version',0), status='', due=None, expires=None)
        linked = references[f['id']]
        f['references'] = len(linked)
        duplicates[(f['kind'], f['sha256'], f['size'])].append(f)
        if f['keep']:
            f['status']='Keep permanently'; continue
        if any(t['state']=='held' or (not t['cancelled_at'] and (t['state']!='accepted' or t['id'] in unsafe_units)) for t in linked):
            f['status']='Protected by active / held work'; continue
        days = settings[f['kind']+'_days']
        if days is None:
            f['status']='Keep indefinitely'; continue
        anchors = [instant(f['created_at'])]
        for t in linked:
            dates = [instant(t['cancelled_at'])] if t['cancelled_at'] else closures[t['id']]
            anchors.append(max(dates) if dates and all(dates) else None)
        if not all(anchors):
            f['status']='Protected: closure/date needs review'; continue
        # Never age a late-added DXF from a job closure preceding its own upload.
        anchor=max(anchors)
        f['expires']=anchor+timedelta(days=days)
        f['due']=f['expires']+timedelta(days=settings['grace_days'])
        f['status']='Eligible in preview' if clock>=f['due'] else ('Grace period in preview' if clock>=f['expires'] else 'Retained until expiry')
    total=sum(f['size'] for f in files)
    duplicate_bytes=sum(sum(f['size'] for f in group[1:]) for group in duplicates.values())
    return dict(files=sorted(files,key=lambda f:(f['pn'],f['revision'],f['kind'])), total=total,
                unused=sum(f['references']==0 for f in files), duplicate_bytes=duplicate_bytes,
                eligible_bytes=sum(f['size'] for f in files if f['status']=='Eligible in preview'),
                external=conn.execute('SELECT COUNT(*) FROM part_revisions WHERE tenant_id=? AND drawing_url IS NOT NULL',(tenant,)).fetchone()[0])


def register_storage(app, db, roles_required):
    def actor():
        member=db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?',(g.tenant['id'],g.user['id'])).fetchone()
        if not member or 'admin' not in member['roles'].split(',') or epoch(db(),g.tenant['id'],g.user['id'])!=session.get('membership_epoch',0):
            abort(403,'Your access changed. Sign in again.')

    def reason():
        value=request.form.get('reason','').strip()
        if not value or len(value)>500:abort(400,'Provide a reason of 1–500 characters.')
        return value

    def record(action, detail):
        audit(db(),g.tenant['id'],g.user['id'],action,f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(detail))

    @app.route('/admin/storage',methods=['GET','POST'])
    @roles_required('admin')
    def storage_admin():
        proposed=None
        if request.method=='POST':
            action=request.form.get('action')
            if action not in {'preview','save'}:abort(400,'Choose preview or save.')
            proposed={}
            for kind in ('pdf','dxf'):
                choice=request.form.get(kind+'_choice')
                value=request.form.get(kind+'_custom','') if choice=='custom' else choice
                if value=='forever':proposed[kind+'_days']=None
                else:
                    try:days=int(value)
                    except (ValueError,TypeError):abort(400,'Choose retention in whole days.')
                    if not 1<=days<=36500:abort(400,'Retention must be 1–36500 days.')
                    proposed[kind+'_days']=days
            try:grace=int(request.form.get('grace_days',''))
            except ValueError:abort(400,'Choose a grace period in whole days.')
            if not 7<=grace<=365:abort(400,'Grace period must be 7–365 days.')
            proposed['grace_days']=grace
            if action=='save':
                detail=reason()
                with db():
                    db().execute('BEGIN IMMEDIATE');actor()
                    old=policy(db(),g.tenant['id'])
                    if request.form.get('version')!=str(old['version']):abort(409,'Policy changed. Reload before saving.')
                    before={k:old[k] for k in proposed}
                    if before!=proposed:
                        db().execute('INSERT INTO storage_policies(tenant_id,pdf_days,dxf_days,grace_days,version) VALUES(?,?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET pdf_days=excluded.pdf_days,dxf_days=excluded.dxf_days,grace_days=excluded.grace_days,version=version+1',(g.tenant['id'],proposed['pdf_days'],proposed['dxf_days'],grace))
                        record('storage_policy_updated',dict(reason=detail,before=before,after=proposed))
                return redirect(url_for('storage_admin',saved='1'))
        with db():
            db().execute('BEGIN')
            settings=policy(db(),g.tenant['id'])
            if proposed:settings.update(proposed);settings['version']=request.form.get('version','')
            report=inventory(db(),g.tenant['id'],settings,datetime.fromtimestamp(app.config['AUTH_CLOCK'](),timezone.utc))
        return render_template('storage_admin.html',settings=settings,report=report,preview=proposed is not None,saved=request.args.get('saved')=='1')

    @app.post('/admin/storage/<part_id>/<kind>')
    @roles_required('admin')
    def storage_keep(part_id,kind):
        if kind not in {'pdf','dxf'}:abort(404)
        value=request.form.get('keep')
        if value not in {'0','1'}:abort(400,'Choose whether to keep permanently.')
        detail=reason()
        with db():
            db().execute('BEGIN IMMEDIATE');actor()
            file=db().execute('SELECT drawing_pdf IS NOT NULL AS pdf,EXISTS(SELECT 1 FROM part_dxf d WHERE d.tenant_id=p.tenant_id AND d.part_revision_id=p.id) AS dxf FROM part_revisions p WHERE p.tenant_id=? AND p.id=?',(g.tenant['id'],part_id)).fetchone()
            if not file or not file[kind]:abort(404)
            old=db().execute('SELECT * FROM storage_file_controls WHERE tenant_id=? AND part_revision_id=? AND kind=?',(g.tenant['id'],part_id,kind)).fetchone()
            if request.form.get('version')!=str(old['version'] if old else 0):abort(409,'File setting changed. Reload before saving.')
            before=old['keep_forever'] if old else 0
            if before!=int(value):
                db().execute('INSERT INTO storage_file_controls VALUES(?,?,?,?,1) ON CONFLICT(tenant_id,part_revision_id,kind) DO UPDATE SET keep_forever=excluded.keep_forever,version=version+1',(g.tenant['id'],part_id,kind,int(value)))
                record('storage_keep_updated',dict(reason=detail,part_id=part_id,kind=kind,before=before,after=int(value)))
        return redirect(url_for('storage_admin',saved='1'))
