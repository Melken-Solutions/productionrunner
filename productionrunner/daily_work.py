"""Action-oriented views over existing production and shipment ledgers."""
from collections import defaultdict
from datetime import datetime, timezone, date
from flask import g, render_template, url_for, abort, jsonify, request, session
from werkzeug.exceptions import HTTPException
from .fulfillment import progress
from .dispatch import released
from .job_plan import current
from .shipment_targets import target_for


def quantity_view(conn, tenant, ticket):
    """Exclusive current-stage buckets, plus separately labelled cumulative step counts."""
    tid=ticket['id'];delivery=progress(conn,tenant,ticket)
    ops=conn.execute('SELECT o.*,s.name AS station_name FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',(tenant,tid)).fetchall()
    units=conn.execute('SELECT s.*,COALESCE(l.quantity,1) AS quantity,COALESCE(l.scrapped,0) AS scrapped FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=?',(tenant,tid)).fetchall()
    done=defaultdict(set)
    for r in conn.execute('SELECT unit_id,operation_id FROM unit_completions WHERE tenant_id=? AND ticket_id=?',(tenant,tid)):done[r['unit_id']].add(r['operation_id'])
    buckets=defaultdict(int);through=defaultdict(int);scrapped=0
    for u in units or [dict(id=None,quantity=ticket['quantity'],state=ticket['state'],scrapped=0)]:
        if u['scrapped']:scrapped+=u['quantity'];continue
        completed=done[u['id']] if u['id'] else {op['id'] for op in ops if op['completed_by'] is not None}
        for op in ops:
            if op['id'] in completed:through[op['id']]+=u['quantity']
        if u['state']=='accepted':continue
        op=next((o for o in ops if o['id'] not in completed),None)
        label='On hold' if u['state']=='held' else ('Awaiting release' if not released(conn,tenant,tid) else f"{op['sequence']}. {op['name']}" if op else 'Awaiting independent inspection')
        buckets[label]+=u['quantity']
    buckets['Accepted · awaiting packing']=delivery['available']
    buckets['Packed · awaiting departure']=delivery['waiting']
    buckets['Shipped']=delivery['departed']
    return dict(delivery=delivery,buckets=[dict(label=k,quantity=v) for k,v in buckets.items() if v],stages=[dict(op,good=through[op['id']]) for op in ops],scrapped=scrapped,total=sum(buckets.values()))


def readiness(conn,tenant,ticket):
    from .parts import ticket_drawing
    from .materials import ticket_material,require_service
    from .vendors import validate_ticket
    tid=ticket['id'];plan=current(conn,tenant,tid);part=ticket_drawing(conn,tenant,tid);checks=[]
    def add(label,ok,detail,endpoint='job_plan_detail',warning=False):
        checks.append(dict(label=label,ok=bool(ok),detail=detail,url=url_for(endpoint,ticket_id=tid),warning=warning))
    add('Controlled Job Plan',plan,'Save the route, acceptance criteria and final-stage requirements.')
    route=conn.execute('SELECT id FROM operations WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchall()
    add('Route acceptance criteria',plan and route and all(plan['data'].get('operations',{}).get(str(op['id']),{}).get('criteria') for op in route),'Save acceptance criteria for every operation in the current route.')
    external=conn.execute('SELECT operation_id FROM outside_steps WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchall()
    add('Outside inspection requirements',not plan or not any(plan['data'].get('operations',{}).get(str(op['operation_id']),{}).get('first_piece') for op in external),'Outside work uses vendor return inspection, not internal first-piece approval.')
    add('Approved drawing',part,'Attach the correct part drawing and revision.','ticket_detail')
    dxf=part and conn.execute('SELECT 1 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(tenant,part['id'])).fetchone()
    required=bool(plan and plan['data'].get('dxf_required'))
    add('DXF',not required or dxf,'Required DXF must be attached.' if required else 'Not required by the saved plan.','ticket_detail')
    material=ticket_material(conn,tenant,tid)
    add('Material',material,'Review the selected material.' if material else 'No material selected; verify whether this job needs one.','ticket_detail',True)
    for stage in ('inspection','packing','shipping'):
        cfg=plan['data'].get(stage,{}) if plan else {}
        station=conn.execute('SELECT name FROM stations WHERE tenant_id=? AND id=? AND active=1',(tenant,cfg.get('station_id'))).fetchone()
        add(stage.capitalize()+' station and checks',station and station['name']==cfg.get('station_name') and cfg.get('criteria'),'Choose an active station and save its acceptance criteria.')
    ops=conn.execute('SELECT o.id,o.name,a.station_id,s.active FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id WHERE o.tenant_id=? AND o.ticket_id=? AND NOT EXISTS(SELECT 1 FROM outside_steps x WHERE x.tenant_id=o.tenant_id AND x.operation_id=o.id)',(tenant,tid)).fetchall()
    for op in ops:
        add('Station: '+op['name'],op['station_id'] and op['active'],'Assign an active station.' if op['station_id'] else 'Assign a station, or explicitly authorize unassigned pickup when releasing.','route_assignments',not op['station_id'])
    for label,fn,endpoint in [('Material outside-process rule',require_service,'outside_detail'),('Approved outside vendors',validate_ticket,'outside_detail')]:
        try:fn(conn,tenant,tid);add(label,True,'Requirements satisfied or not applicable.',endpoint)
        except HTTPException as e:add(label,False,e.description,endpoint)
    req=conn.execute('SELECT required_date FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchone()
    add('Delivery date',req and req['required_date'],'Review the delivery target and staged shipment dates.','delivery_detail',True)
    return checks


def register(app,db,roles_required,now):
    @app.after_request
    def action_feedback(response):
        if request.method=='POST' and response.status_code==302 and request.endpoint not in {'login','logout'} and '/login' not in response.headers.get('Location',''):
            session['action_feedback']='Action completed. Review the updated details below.'
        return response

    @app.context_processor
    def feedback_context():
        return dict(action_feedback=session.pop('action_feedback',None),page_loaded=now()[11:19]+' UTC')

    @app.get('/connection-status')
    @roles_required()
    def connection_status():
        db().execute('SELECT 1').fetchone()
        response=jsonify(ok=True,checked_at=now());response.headers['Cache-Control']='no-store';return response

    @app.get('/tickets/<ticket_id>/progress')
    @roles_required()
    def job_progress(ticket_id):
        ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not ticket:abort(404)
        cancelled=db().execute('SELECT 1 FROM ticket_cancellations WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
        return render_template('job_progress.html',ticket=ticket,cancelled=cancelled,**quantity_view(db(),g.tenant['id'],ticket))

    @app.context_processor
    def context():return dict(release_readiness=lambda t:readiness(db(),g.tenant['id'],t))

    @app.get('/today')
    @roles_required('planner','admin')
    def today():
        tenant=g.tenant['id']
        try:day=date.fromisoformat(request.args.get('day',datetime.now(timezone.utc).date().isoformat())).isoformat()
        except ValueError:abort(400,'Choose a valid planning date.')
        groups={name:[] for name in ('Planned today','Due shipments','HOT jobs','Held work','Waiting on production checks','Requests needing preparation','Awaiting release','Outside returns','Ready to pack / ship')}
        tickets=db().execute('SELECT t.*,p.priority AS hot_priority,r.required_date FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id LEFT JOIN intake_requests r ON r.tenant_id=t.tenant_id AND r.ticket_id=t.id WHERE t.tenant_id=? AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id) ORDER BY p.priority IS NULL,p.priority,t.created_at',(tenant,)).fetchall()
        for req in db().execute("SELECT * FROM intake_requests WHERE tenant_id=? AND status IN ('preparing','approved') ORDER BY created_at",(tenant,)):
            groups['Requests needing preparation'].append(dict(title=req['pn']+' · '+str(req['id']),detail='Approve documents and create the production ticket.' if req['status']=='preparing' else 'Approved — ready to create the production ticket.',url=url_for('intake_detail',rid=req['id'])))
        from .production_calendar import assignments
        planned=assignments(db(),tenant)
        for ticket in tickets:
            d=progress(db(),tenant,ticket)
            if d['closed']:continue
            def add(group,detail,endpoint='job_progress'):
                groups[group].append(dict(title=ticket['pn']+' · '+ticket['id'],detail=detail,url=url_for(endpoint,ticket_id=ticket['id'])))
            travelers=db().execute("SELECT s.id,COALESCE(l.quantity,1) AS quantity FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? AND COALESCE(l.scrapped,0)=0 AND NOT EXISTS(SELECT 1 FROM fulfillment_active_units f WHERE f.tenant_id=s.tenant_id AND f.unit_id=s.id AND f.kind='depart')",(tenant,ticket['id'])).fetchall()
            has_units=db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone()
            planned_quantity=sum(u['quantity'] for u in (travelers if has_units else [dict(id=None,quantity=d['remaining'])]) if planned.get((ticket['id'],u['id']),planned.get((ticket['id'],None),{})).get('day')==day)
            if planned_quantity:add('Planned today',f'{planned_quantity} units planned for today. Check readiness before starting.')
            target=target_for(db(),tenant,ticket);due=target['ship_date'] if target and not target['met'] else ticket['required_date']
            if due and due<=day:add('Due shipments',f"{'Overdue' if due<day else 'Due today'} · {due} · {target['remaining'] if target and not target['met'] else d['remaining']} still to ship",'delivery_detail')
            if ticket['hot_priority'] is not None:add('HOT jobs',f"HOT {ticket['hot_priority']} · {d['departed']} / {ticket['quantity']} shipped")
            if ticket['state']=='held' or db().execute("SELECT 1 FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? AND s.state='held' AND COALESCE(l.scrapped,0)=0",(tenant,ticket['id'])).fetchone():add('Held work','Review hold and disposition before continuing.','ticket_detail')
            if d['pending']:add('Waiting on production checks',f"{d['pending']} business checkpoints still open.",'delivery_detail')
            from .job_plan import first_piece_passed
            for op in db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? AND completed_by IS NULL',(tenant,ticket['id'])):
                if released(db(),tenant,ticket['id']) and not first_piece_passed(db(),tenant,ticket['id'],op['id']):add('Waiting on production checks','First-piece approval: '+op['name'],'job_plan_detail')
            if not released(db(),tenant,ticket['id']):add('Awaiting release','Review readiness and approve the Job Plan.','job_plan_detail')
            if d['available'] or d['waiting']:add('Ready to pack / ship',f"{d['available']} accepted awaiting packing · {d['waiting']} packed awaiting departure; checks and labels may still be needed.",'delivery_detail')
            for outside in db().execute('SELECT x.*,COALESCE((SELECT SUM(CASE m.kind WHEN \'send\' THEN m.quantity ELSE -m.quantity END) FROM outside_moves m WHERE m.tenant_id=x.tenant_id AND m.operation_id=x.operation_id),0) AS outstanding FROM outside_steps x WHERE x.tenant_id=? AND x.ticket_id=?',(tenant,ticket['id'])):
                if outside['outstanding']>0 and outside['due_date']<=day:add('Outside returns',f"{outside['vendor']} · {outside['outstanding']} at vendor · due {outside['due_date']}",'outside_detail')
        from .job_messages import threads
        messages=[dict(t) for t in threads(db(),tenant,g.user['id'],g.roles) if t['status']!='resolved']
        for m in messages:
            opened=datetime.fromisoformat(m['created_at']);opened=opened.replace(tzinfo=timezone.utc) if opened.tzinfo is None else opened
            hours=max(0,int((datetime.now(timezone.utc)-opened).total_seconds()/3600))
            m['age']=f'{hours//24}d {hours%24}h' if hours>=24 else f'{hours}h'
        return render_template('today.html',groups=groups,messages=messages,day=day)
