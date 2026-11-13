"""Explicit, tenant-scoped permanent deletion; shared library records survive."""
import sqlite3
import hashlib
import json
from pathlib import Path
from flask import abort,g,request,render_template,session
from .database import is_postgres
from .intake import actor
from .work_tracking import open_sessions

SCOPE=json.loads(Path(__file__).with_name('job_deletion_scope.json').read_text())


def impact(conn,tenant,ticket):
    counts={table:conn.execute('SELECT COUNT(*) FROM '+table+' WHERE '+where,(tenant,ticket['id'])).fetchone()[0] for table,where in SCOPE.items()}
    blocked=[]
    if any(s['ticket_id']==ticket['id'] for s in open_sessions(conn,tenant)):
        blocked.append('Stop the active or paused station work sessions first.')
    if conn.execute('SELECT 1 FROM return_repairs WHERE tenant_id=? AND (ticket_id=? OR repair_ticket_id=?)',(tenant,ticket['id'],ticket['id'])).fetchone():
        blocked.append('This job is linked to a return/repair job. Keep that linked history together; deletion is unavailable.')
    if conn.execute('SELECT 1 FROM shipping_boxes WHERE tenant_id=? AND ticket_id=? AND load_id IS NOT NULL',(tenant,ticket['id'])).fetchone() or conn.execute('SELECT 1 FROM shipping_departures WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone():
        blocked.append('This job belongs to a shipping load. Remove its boxes from the load first; departed load history cannot be removed here.')
    digest=hashlib.sha256(json.dumps([dict(ticket),counts,blocked],sort_keys=True).encode()).hexdigest()
    return counts,blocked,digest


def purge_local(conn,tenant,ticket,user,reason,stamp):
    # SQLite DDL is transactional. Other writers cannot observe the temporary
    # trigger suspension; rollback restores both records and guards on failure.
    names=list(SCOPE)+['intake_requests']
    triggers=conn.execute('SELECT name,sql FROM sqlite_master WHERE type=\'trigger\' AND tbl_name IN ('+','.join('?' for _ in names)+')',names).fetchall()
    for t in triggers:conn.execute('DROP TRIGGER "'+t['name'].replace('"','""')+'"')
    detail=json.dumps(dict(ticket_id=ticket['id'],pn=ticket['pn'],revision=ticket['revision'],quantity=ticket['quantity'],state=ticket['state'],reason=reason))
    conn.execute("INSERT INTO intake_events(tenant_id,request_id,actor_id,action,detail,created_at) SELECT ?,id,?,'job_deleted',?,? FROM intake_requests WHERE tenant_id=? AND ticket_id=?",(tenant,user,detail,stamp,tenant,ticket['id']))
    conn.execute("UPDATE intake_requests SET ticket_id=NULL,status='cancelled',version=version+1 WHERE tenant_id=? AND ticket_id=?",(tenant,ticket['id']))
    for table,where in SCOPE.items():conn.execute('DELETE FROM '+table+' WHERE '+where,(tenant,ticket['id']))
    for t in triggers:conn.execute(t['sql'])
    conn.execute("INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,'job_deleted',?,?,?)",(tenant,user,'user:'+str(user),detail,stamp))


def register(app,db,roles_required,now):
    @app.route('/tickets/<ticket_id>/delete',methods=['GET','POST'])
    @roles_required('admin','planner')
    def delete_job(ticket_id):
        with db():
            if request.method=='POST':db().execute('BEGIN IMMEDIATE')
            actor(db(),g.tenant['id'],g.user['id'])
            ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
            if not ticket:abort(404,'Unknown JobRunner.')
            counts,blocked,digest=impact(db(),g.tenant['id'],ticket)
            if request.method=='POST':
                if blocked:abort(409,' '.join(blocked))
                if request.form.get('version')!=str(ticket['version']) or request.form.get('impact')!=digest:abort(409,'The job changed. Review its deletion again.')
                if request.form.get('confirm_id','').strip()!=ticket_id or request.form.get('confirm')!='yes':abort(400,'Enter the displayed JobRunner ID and confirm permanent deletion.')
                reason=request.form.get('reason','').strip()
                if not 1<=len(reason)<=500:abort(400,'Choose a deletion reason.')
                try:
                    if is_postgres(app.config['DATABASE']):
                        db().execute('SELECT productionrunner.delete_job(?,?,?,?,?,?)',(g.tenant['id'],ticket_id,g.user['id'],session.get('membership_epoch',0),ticket['version'],reason))
                    else:purge_local(db(),g.tenant['id'],ticket,g.user['id'],reason,now())
                except sqlite3.IntegrityError:
                    abort(409,'Another protected record depends on this job. Nothing was deleted. Review its linked records before trying again.')
                return render_template('job_deleted.html',ticket=ticket)
        return render_template('delete_job.html',ticket=ticket,counts=counts,blocked=blocked,impact=digest)
