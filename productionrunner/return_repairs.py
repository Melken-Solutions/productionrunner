"""Linked repair jobs retain normal production and outbound controls."""
import json
from .jobrunner_ids import next_ticket_id
from flask import abort, g, request, render_template, redirect, url_for
from .intake import actor, approved_request, link_ticket
from .parts import ticket_drawing, matching_part
from .serials import create_units
from .workflow_defaults import settings, validate_ticket, snapshot
from .fulfillment import progress


def closure(conn, tenant, rid):
    return conn.execute('SELECT * FROM return_closure_events WHERE tenant_id=? AND return_id=? ORDER BY id DESC LIMIT 1',(tenant,rid)).fetchone()


def closure_event(conn, tenant, user, link, action, reason, stamp, event):
    conn.execute('INSERT INTO return_closure_events(tenant_id,ticket_id,return_id,action,reason,actor_id,created_at) VALUES(?,?,?,?,?,?,?)',(tenant,link['ticket_id'],link['return_id'],action,reason,user,stamp))
    detail=json.dumps(dict(return_id=link['return_id'],repair_ticket_id=link['repair_ticket_id'],reason=reason))
    for tid in (link['ticket_id'],link['repair_ticket_id']):
        event(tid,'return_'+action,detail)
    for unit in conn.execute('SELECT original_unit_id,repair_unit_id FROM return_repair_units WHERE tenant_id=? AND return_id=?',(tenant,link['return_id'])):
        for tid,uid in ((link['ticket_id'],unit['original_unit_id']),(link['repair_ticket_id'],unit['repair_unit_id'])):
            event(tid,'unit_return_'+action,detail)
            eid=conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(tenant,tid,uid,eid))
    conn.execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(tenant,link['ticket_id']))


def reopen_after_correction(conn,tenant,user,repair_id,stamp,event):
    link=conn.execute('SELECT * FROM return_repairs WHERE tenant_id=? AND repair_ticket_id=?',(tenant,repair_id)).fetchone()
    if not link:return
    last=closure(conn,tenant,link['return_id'])
    ticket=conn.execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(tenant,repair_id)).fetchone()
    if last and last['action']=='closed' and not progress(conn,tenant,ticket)['closed']:
        closure_event(conn,tenant,user,link,'reopened','Repair departure was corrected; return requires completion again.',stamp,event)


def register_return_repairs(app,db,roles_required,now,lock_ticket,event):
    @app.context_processor
    def repair_context():
        return dict(repair_origin=lambda ticket:db().execute('SELECT * FROM return_repairs WHERE tenant_id=? AND repair_ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone())

    def entry(tid,rid):
        row=db().execute('SELECT r.*,v.decision FROM customer_returns r JOIN customer_return_reviews v ON v.return_id=r.id AND v.tenant_id=r.tenant_id WHERE r.tenant_id=? AND r.ticket_id=? AND r.id=?',(g.tenant['id'],tid,rid)).fetchone()
        if not row:abort(404,'A reviewed return is required.')
        if row['decision']!='repair_required':abort(409,'Only repair-required returns can create repair jobs.')
        return row

    @app.route('/tickets/<ticket_id>/returns/<int:return_id>/repair',methods=['GET','POST'])
    @roles_required('planner')
    def create_return_repair(ticket_id,return_id):
        original=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not original:abort(404)
        returned=entry(ticket_id,return_id)
        part=ticket_drawing(db(),g.tenant['id'],ticket_id) or matching_part(db(),g.tenant['id'],original['pn'],original['revision'])
        if request.method=='POST':
            with db():
                original=lock_ticket(ticket_id)
                actor(db(),g.tenant['id'],g.user['id'],('planner',))
                returned=entry(ticket_id,return_id)
                if db().execute('SELECT 1 FROM return_repairs WHERE return_id=?',(return_id,)).fetchone():abort(409,'This return already has a repair job.')
                part=ticket_drawing(db(),g.tenant['id'],ticket_id) or matching_part(db(),g.tenant['id'],original['pn'],original['revision'])
                if not part:abort(409,'Add the original PN/revision drawing to the parts catalog before planning repair.')
                if request.form.get('confirm')!='yes':abort(400,'Review the drawing and confirm repair authorization.')
                req=approved_request(db(),g.tenant['id'],request.form['request_id']) if request.form.get('request_id') else None
                policy=validate_ticket(db(),g.tenant['id'],request.form.get('workflow_version'),req)
                if req and (req['pn'],req['revision'],req['quantity'],req['part_id'])!=(original['pn'],original['revision'],returned['quantity'],part['id']):
                    abort(409,'Approved request must match the repair PN, revision, returned quantity and drawing.')
                instructions=request.form.get('instructions','').strip()
                route=[s.strip() for s in request.form.get('route','').splitlines() if s.strip()]
                reason=request.form.get('reason','').strip()
                if not 1<=len(reason)<=500 or not 1<=len(instructions)<=4000 or not 1<=len(route)<=20 or any(len(s)>500 or not s.partition('|')[0].strip() for s in route):
                    abort(400,'Provide a reason, repair acceptance criteria and 1–20 named routing steps.')
                serials=json.loads(returned['serials'])
                tracking='serial' if serials else 'batch'
                stamp=now();tid=next_ticket_id(db(),stamp)
                db().execute('INSERT INTO tickets(id,tenant_id,pn,revision,quantity,tracking,serials,instructions,state,created_by,created_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)',(tid,g.tenant['id'],original['pn'],original['revision'],returned['quantity'],tracking,json.dumps(serials),instructions,'ready',g.user['id'],stamp))
                db().execute('INSERT INTO ticket_dispatch VALUES(?,?,0)',(g.tenant['id'],tid))
                snapshot(db(),g.tenant['id'],tid,g.user['id'],policy,stamp,event)
                for seq,line in enumerate(route,1):
                    name,_,detail=line.partition('|')
                    db().execute('INSERT INTO operations(tenant_id,ticket_id,sequence,name,instructions) VALUES(?,?,?,?,?)',(g.tenant['id'],tid,seq,name.strip(),detail.strip() or instructions))
                from .request_outside import apply as apply_requested_outside
                apply_requested_outside(db(),g.tenant['id'],req['id'],tid,request.form.get('outside_sequence'),event)
                create_units(db(),g.tenant['id'],tid,serials)
                db().execute('INSERT INTO ticket_drawings VALUES(?,?,?)',(g.tenant['id'],tid,part['id']))
                db().execute('INSERT INTO return_repairs VALUES(?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,return_id,tid,g.user['id'],reason,stamp))
                for unit in db().execute('SELECT s.id,s.serial FROM customer_return_units r JOIN serial_units s ON s.id=r.unit_id AND s.tenant_id=r.tenant_id WHERE r.tenant_id=? AND r.return_id=?',(g.tenant['id'],return_id)).fetchall():
                    new=db().execute('SELECT id FROM serial_units WHERE ticket_id=? AND serial=?',(tid,unit['serial'])).fetchone()
                    db().execute('INSERT INTO return_repair_units VALUES(?,?,?,?,?,?)',(g.tenant['id'],return_id,unit['id'],new['id'],ticket_id,tid))
                dxf=db().execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(g.tenant['id'],part['id'])).fetchone()
                detail=json.dumps(dict(return_id=return_id,original_ticket_id=ticket_id,repair_ticket_id=tid,quantity=returned['quantity'],serials=serials,part_id=part['id'],drawing_sha256=part['sha256'],drawing_url=part['drawing_url'],dxf_sha256=dxf['sha256'] if dxf else None,reason=reason))
                event(tid,'ticket_created',detail);event(tid,'return_repair_created',detail);event(ticket_id,'return_repair_created',detail)
                if req:
                    link_ticket(db(),g.tenant['id'],g.user['id'],req,tid,stamp)
                    event(tid,'request_linked',json.dumps(dict(request_id=req['id'])))
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            return redirect(url_for('route_assignments',ticket_id=tid))
        requests=db().execute("SELECT id,source_reference FROM intake_requests WHERE tenant_id=? AND pn=? AND revision=? AND quantity=? AND status='approved'",(g.tenant['id'],original['pn'],original['revision'],returned['quantity'])).fetchall()
        return render_template('return_repair.html',ticket=original,returned=returned,part=part,workflow=settings(db(),g.tenant['id']),requests=requests)

    @app.post('/tickets/<ticket_id>/returns/<int:return_id>/close')
    @roles_required('planner','admin')
    def close_repaired_return(ticket_id,return_id):
        with db():
            lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'])
            link=db().execute('SELECT * FROM return_repairs WHERE tenant_id=? AND ticket_id=? AND return_id=?',(g.tenant['id'],ticket_id,return_id)).fetchone()
            if not link:abort(404)
            repair=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],link['repair_ticket_id'])).fetchone()
            last=closure(db(),g.tenant['id'],return_id)
            if last and last['action']=='closed':abort(409,'Return already closed.')
            if request.form.get('repair_version')!=str(repair['version']):abort(409,'Repair changed. Reload before closing.')
            if repair['state']!='accepted' or not progress(db(),g.tenant['id'],repair)['closed']:abort(409,'All repaired units must pass inspection and physically depart before closing the return.')
            reason=request.form.get('reason','').strip()
            if not 1<=len(reason)<=500 or request.form.get('confirm')!='yes':abort(400,'Provide closure evidence and confirm completion.')
            closure_event(db(),g.tenant['id'],g.user['id'],link,'closed',reason,now(),event)
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],repair['id']))
        return redirect(url_for('ticket_returns',ticket_id=ticket_id))
