"""Controlled job plans connecting production to inspection and physical departure."""
import json
from flask import abort,g,request,render_template,redirect,url_for
from .intake import actor
from .dispatch import released,assignment

STAGES=('inspection','packing','shipping')


def current(conn,tenant,tid):
    row=conn.execute('SELECT * FROM job_plans WHERE tenant_id=? AND ticket_id=? ORDER BY id DESC LIMIT 1',(tenant,tid)).fetchone()
    return dict(row,data=json.loads(row['data'])) if row else None


def first_piece_passed(conn,tenant,tid,op):
    p=current(conn,tenant,tid)
    if not p or not p['data']['operations'].get(str(op),{}).get('first_piece'):return True
    a=assignment(conn,tenant,op)
    r=conn.execute('SELECT * FROM first_piece_results WHERE tenant_id=? AND ticket_id=? AND operation_id=? ORDER BY id DESC LIMIT 1',(tenant,tid,op)).fetchone()
    return bool(r and r['result']=='pass' and r['plan_id']==p['id'] and a and r['station_id']==a['station_id'])


def require_first_piece(conn,tenant,tid,op):
    if not first_piece_passed(conn,tenant,tid,op):abort(409,'First-piece approval is required at the assigned station before production. Open the Job Plan.')


def evidence(conn,tenant,tid,key,event,unit=None,passing=True):
    p=current(conn,tenant,tid)
    if not p:return
    step=p['data']['operations'].get(str(key)) if isinstance(key,int) else p['data'][key]
    if not step:return
    if isinstance(key,int):require_first_piece(conn,tenant,tid,key)
    elif not conn.execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1',(tenant,step['station_id'])).fetchone():abort(409,'The planned final-stage station is inactive. Contact Admin.')
    value=request.form.get('plan_evidence','').strip()
    if not 1<=len(value)<=4000:abort(400,'Record Job Plan check results, measurements and acceptance evidence.')
    event(tid,'job_plan_check_recorded',json.dumps(dict(plan_id=p['id'],step=key,unit_id=unit,criteria=step['criteria'],evidence=value,passing=passing,station=step.get('station_name'))))


def release_check(conn,tenant,ticket,event):
    p=current(conn,tenant,ticket['id'])
    if not p:abort(409,'Complete the Job Plan before releasing production.')
    if request.form.get('plan_review')!='yes':abort(400,'Confirm the complete Job Plan, document revisions and material before release.')
    for stage in STAGES:
        station=conn.execute('SELECT * FROM stations WHERE tenant_id=? AND id=? AND active=1',(tenant,p['data'][stage]['station_id'])).fetchone()
        if not station or station['name']!=p['data'][stage]['station_name']:abort(409,'A final-stage station changed. Save and review the Job Plan again.')
    from .parts import ticket_drawing
    part=ticket_drawing(conn,tenant,ticket['id'])
    if not part:abort(409,'Attach the approved drawing before release.')
    dxf=conn.execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(tenant,part['id'])).fetchone()
    if p['data']['dxf_required'] and not dxf:abort(409,'This Job Plan requires a DXF before release.')
    from .materials import ticket_material
    ops=[dict(r) for r in conn.execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(tenant,ticket['id']))]
    assignments=[dict(r) for r in conn.execute('SELECT a.*,s.name FROM operation_assignments a JOIN stations s ON s.id=a.station_id AND s.tenant_id=a.tenant_id WHERE a.tenant_id=? AND a.ticket_id=?',(tenant,ticket['id']))]
    vendors=[dict(r) for r in conn.execute('SELECT * FROM outside_steps WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id']))]
    if any(p['data']['operations'].get(str(v['operation_id']),{}).get('first_piece') for v in vendors):abort(409,'Outside operations cannot use internal first-piece approval. Save the Job Plan again.')
    event(ticket['id'],'job_plan_released',json.dumps(dict(plan_id=p['id'],plan=p['data'],drawing=dict(part),dxf_sha256=dxf['sha256'] if dxf else None,material=ticket_material(conn,tenant,ticket['id']),operations=ops,assignments=assignments,outside=vendors,reviewer=g.user['id'])))


def station_in_use(conn,tenant,station):
    from .fulfillment import progress
    tickets=conn.execute("SELECT t.* FROM tickets t WHERE t.tenant_id=? AND EXISTS(SELECT 1 FROM job_plans p WHERE p.tenant_id=t.tenant_id AND p.ticket_id=t.id) AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id)",(tenant,)).fetchall()
    return any(any(current(conn,tenant,t['id'])['data'][stage]['station_id']==station for stage in STAGES) and not progress(conn,tenant,t)['closed'] for t in tickets)


def register_job_plan(app,db,roles_required,now,lock_ticket,event):
    @app.context_processor
    def context():
        return dict(job_plan=lambda t:current(db(),g.tenant['id'],t['id']))

    @app.route('/tickets/<ticket_id>/plan',methods=['GET','POST'])
    @roles_required()
    def job_plan_detail(ticket_id):
        t=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not t:abort(404)
        if request.method=='POST':
            with db():
                t=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'],('planner','admin'))
                if released(db(),g.tenant['id'],ticket_id) or t['state']!='ready':abort(409,'Released plans are locked. Use the documented hold/rework process for production issues.')
                ops=db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],ticket_id)).fetchall()
                data={'operations':{},'dxf_required':request.form.get('dxf_required')=='yes'}
                for op in ops:
                    criteria=request.form.get('criteria_'+str(op['id']),'').strip()
                    if not 1<=len(criteria)<=2000:abort(400,'Provide acceptance criteria for every production operation.')
                    outside=db().execute('SELECT 1 FROM outside_steps WHERE tenant_id=? AND operation_id=?',(g.tenant['id'],op['id'])).fetchone()
                    first=request.form.get('first_'+str(op['id']))=='yes'
                    if outside and first:abort(400,'Use vendor return inspection for outside work, not internal first-piece approval.')
                    data['operations'][str(op['id'])]=dict(criteria=criteria,first_piece=first)
                for stage in STAGES:
                    station=db().execute('SELECT * FROM stations WHERE tenant_id=? AND id=? AND active=1',(g.tenant['id'],request.form.get(stage+'_station'))).fetchone()
                    criteria=request.form.get(stage+'_criteria','').strip()
                    if not station or not 1<=len(criteria)<=2000:abort(400,'Choose an active station and instructions/acceptance criteria for inspection, packing and shipping.')
                    data[stage]=dict(station_id=station['id'],station_name=station['name'],criteria=criteria)
                reason=request.form.get('reason','').strip()
                if not 1<=len(reason)<=500:abort(400,'Record the reason for this plan revision.')
                before=current(db(),g.tenant['id'],ticket_id)
                db().execute('INSERT INTO job_plans(tenant_id,ticket_id,data,actor_id,reason,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],ticket_id,json.dumps(data),g.user['id'],reason,now()))
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
                event(ticket_id,'job_plan_saved',json.dumps(dict(previous_plan=before['id'] if before else None,plan=data,reason=reason)))
            return redirect(url_for('job_plan_detail',ticket_id=ticket_id))
        ops=db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],ticket_id)).fetchall()
        history=db().execute('SELECT f.*,u.username FROM first_piece_results f JOIN users u ON u.id=f.actor_id WHERE f.tenant_id=? AND f.ticket_id=? ORDER BY f.id DESC',(g.tenant['id'],ticket_id)).fetchall()
        return render_template('job_plan.html',ticket=t,operations=ops,plan=current(db(),g.tenant['id'],ticket_id),stations=db().execute('SELECT * FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall(),first_history=history,first_ready=lambda op:first_piece_passed(db(),g.tenant['id'],ticket_id,op))

    @app.post('/tickets/<ticket_id>/plan/first-piece/<int:operation_id>')
    @roles_required('inspector')
    def first_piece_review(ticket_id,operation_id):
        with db():
            t=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'],('inspector',))
            p=current(db(),g.tenant['id'],ticket_id)
            if not released(db(),g.tenant['id'],ticket_id) or t['state'] not in {'ready','in_progress'} or not p or not p['data']['operations'].get(str(operation_id),{}).get('first_piece'):abort(409,'No first-piece review is available for this operation.')
            op=db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? AND id=?',(g.tenant['id'],ticket_id,operation_id)).fetchone()
            unit_count=db().execute('SELECT COUNT(*) FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()[0]
            done_count=db().execute('SELECT COUNT(*) FROM unit_completions WHERE tenant_id=? AND operation_id=?',(g.tenant['id'],operation_id)).fetchone()[0]
            if not op or op['completed_by'] or (unit_count and done_count==unit_count):abort(409,'Production already completed for this operation. Use the hold/rework process.')
            if db().execute('SELECT 1 FROM work_sessions s JOIN work_events e ON e.session_id=s.id AND e.tenant_id=s.tenant_id WHERE s.tenant_id=? AND s.ticket_id=? AND s.operation_id=? AND e.actor_id=?',(g.tenant['id'],ticket_id,operation_id,g.user['id'])).fetchone():abort(403,'A setup/work contributor cannot approve their own first piece.')
            a=assignment(db(),g.tenant['id'],operation_id)
            if not a or not a['active']:abort(409,'Assign an active station first.')
            result=request.form.get('result');notes=request.form.get('notes','').strip()
            if result not in {'pass','fail'} or not 1<=len(notes)<=4000:abort(400,'Record pass/fail and measured first-piece evidence.')
            db().execute('INSERT INTO first_piece_results(tenant_id,ticket_id,operation_id,plan_id,station_id,result,notes,actor_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,operation_id,p['id'],a['station_id'],result,notes,g.user['id'],now()))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            event(ticket_id,'first_piece_'+result,json.dumps(dict(operation_id=operation_id,station=a['station_name'],plan_id=p['id'],notes=notes)))
        return redirect(url_for('job_plan_detail',ticket_id=ticket_id))
