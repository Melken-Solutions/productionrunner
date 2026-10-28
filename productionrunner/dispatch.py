"""Tenant stations, route scheduling and immutable coverage attribution."""
import json
import sqlite3
from flask import abort,g,redirect,render_template,request,url_for


def released(conn,tenant_id,ticket_id):
    row=conn.execute('SELECT released FROM ticket_dispatch WHERE tenant_id=? AND ticket_id=?',(tenant_id,ticket_id)).fetchone()
    return row is None or bool(row[0])  # Existing travelers retain their original execution behavior.


def assignment(conn,tenant_id,operation_id):
    return conn.execute('SELECT a.*,s.name AS station_name,s.active,u.username FROM operation_assignments a JOIN stations s ON s.id=a.station_id AND s.tenant_id=a.tenant_id LEFT JOIN users u ON u.id=a.operator_id WHERE a.tenant_id=? AND a.operation_id=?',(tenant_id,operation_id)).fetchone()


def require_dispatch(conn,tenant_id,ticket_id,operation_id):
    if not released(conn,tenant_id,ticket_id):
        abort(409,'This ticket is awaiting planner release. Assign its stations and release it first.')
    assigned=assignment(conn,tenant_id,operation_id)
    if assigned and not assigned['active']:
        abort(409,'Assigned station is inactive. Ask the planner to reassign this operation.')


def snapshot(conn,tenant_id,ticket_id,operation_id,actor_id,timestamp,unit_id=None):
    a=assignment(conn,tenant_id,operation_id)
    conn.execute('INSERT INTO completion_assignments(tenant_id,ticket_id,operation_id,unit_id,station_id,station_name,assigned_user_id,assigned_username,actual_user_id,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (tenant_id,ticket_id,operation_id,unit_id,a['station_id'] if a else None,a['station_name'] if a else None,a['operator_id'] if a else None,a['username'] if a else None,actor_id,timestamp))
    return f"Station: {a['station_name'] if a else 'unassigned legacy work'}; assigned operator: {(a['username'] or 'unassigned') if a else 'unassigned'}; actual operator ID: {actor_id}"


def register_dispatch(app,db,roles_required,now,lock_ticket,event):
    def check_actor(allowed):
        from flask import session
        from .admin import epoch
        row=db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?',(g.tenant['id'],g.user['id'])).fetchone()
        if not row or not set(row[0].split(',')).intersection(allowed) or epoch(db(),g.tenant['id'],g.user['id'])!=session.get('membership_epoch',0):
            abort(403,'Your access changed. Sign in again.')

    def reason():
        value=request.form.get('reason','').strip()
        if not 1<=len(value)<=500:
            abort(400,'Record a reason of 1–500 characters.')
        return value

    def station_event(station_id,action,detail):
        db().execute('INSERT INTO station_events(tenant_id,station_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],station_id,g.user['id'],action,detail,now()))

    def route_attribution(op,unit=None):
        saved=db().execute('SELECT c.station_name,c.assigned_username AS username,u.username AS actual_username FROM completion_assignments c JOIN users u ON u.id=c.actual_user_id WHERE c.tenant_id=? AND c.operation_id=? AND c.unit_id IS ?', (g.tenant['id'],op['id'],unit['id'] if unit else None)).fetchone()
        return saved or assignment(db(),g.tenant['id'],op['id'])

    @app.context_processor
    def dispatch_context():
        return dict(route_attribution=route_attribution,dispatch_released=lambda ticket:released(db(),g.tenant['id'],ticket['id']),
                    operation_assignment=lambda op:assignment(db(),g.tenant['id'],op['id']))

    @app.get('/admin/stations')
    @roles_required('admin')
    def stations_admin():
        stations=db().execute('SELECT * FROM stations WHERE tenant_id=? ORDER BY name',(g.tenant['id'],)).fetchall()
        entries=db().execute('SELECT e.*,u.username FROM station_events e JOIN users u ON u.id=e.actor_id WHERE e.tenant_id=? ORDER BY e.id DESC LIMIT 100',(g.tenant['id'],)).fetchall()
        return render_template('stations.html',stations=stations,entries=entries)

    @app.post('/admin/stations')
    @roles_required('admin')
    def create_station():
        name=request.form.get('name','').strip();why=reason()
        if not 1<=len(name)<=100 or any(ord(c)<32 for c in name):
            abort(400,'Station name must contain 1–100 printable characters.')
        with db():
            db().execute('BEGIN IMMEDIATE')
            check_actor({'admin'})
            try:
                result=db().execute('INSERT INTO stations(tenant_id,name) VALUES(?,?)',(g.tenant['id'],name))
            except sqlite3.IntegrityError:
                abort(409,'A station with that name already exists.')
            station_event(result.lastrowid,'station_created',name+': '+why)
        return redirect(url_for('stations_admin'))

    @app.post('/admin/stations/<int:station_id>')
    @roles_required('admin')
    def edit_station(station_id):
        name=request.form.get('name','').strip();why=reason()
        active=request.form.get('active')
        if not 1<=len(name)<=100 or any(ord(c)<32 for c in name) or active not in {'0','1'}:
            abort(400,'Provide a valid name and active status.')
        with db():
            db().execute('BEGIN IMMEDIATE')
            check_actor({'admin'})
            old=db().execute('SELECT * FROM stations WHERE tenant_id=? AND id=?',(g.tenant['id'],station_id)).fetchone()
            if not old: abort(404,'Unknown station.')
            if request.form.get('version')!=str(old['version']): abort(409,'Station changed. Reload first.')
            if active=='0' and db().execute("SELECT 1 FROM operation_assignments a JOIN tickets t ON t.id=a.ticket_id AND t.tenant_id=a.tenant_id WHERE a.tenant_id=? AND a.station_id=? AND t.state!='accepted' AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id)",(g.tenant['id'],station_id)).fetchone():
                abort(409,'Station is assigned to open work. Finish/cancel that work or reassign unfinished operations before deactivation.')
            try:
                db().execute('UPDATE stations SET name=?,active=?,version=version+1 WHERE tenant_id=? AND id=?',(name,int(active),g.tenant['id'],station_id))
            except sqlite3.IntegrityError: abort(409,'Station name already exists.')
            station_event(station_id,'station_changed',json.dumps(dict(before=dict(old),name=name,active=active,reason=why)))
        return redirect(url_for('stations_admin'))

    @app.get('/tickets/<ticket_id>/assignments')
    @roles_required('planner','admin')
    def route_assignments(ticket_id):
        ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not ticket: abort(404,'Unknown ticket.')
        operations=db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],ticket_id)).fetchall()
        stations=db().execute('SELECT * FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall()
        operators=db().execute("SELECT u.id,u.username FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=? AND instr(','||m.roles||',',',operator,')>0 ORDER BY u.username",(g.tenant['id'],)).fetchall()
        return render_template('assignments.html',ticket=ticket,operations=operations,stations=stations,operators=operators)

    @app.post('/tickets/<ticket_id>/assignments/<int:operation_id>')
    @roles_required('planner','admin')
    def assign_operation(ticket_id,operation_id):
        why=reason()
        with db():
            ticket=lock_ticket(ticket_id)
            check_actor({'planner','admin'})
            from .work_tracking import open_sessions
            if any(s['operation_id']==operation_id for s in open_sessions(db(),g.tenant['id'])):
                abort(409,'Stop station sessions before reassigning this operation.')
            op=db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? AND id=?',(g.tenant['id'],ticket_id,operation_id)).fetchone()
            if not op: abort(404,'Unknown operation.')
            count=db().execute('SELECT COUNT(*) FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()[0]
            done=db().execute('SELECT COUNT(*) FROM unit_completions WHERE tenant_id=? AND operation_id=?',(g.tenant['id'],operation_id)).fetchone()[0]
            if op['completed_by'] or (count and done==count): abort(409,'This operation is completed. Its assignment history is preserved.')
            s=db().execute('SELECT * FROM stations WHERE tenant_id=? AND id=? AND active=1',(g.tenant['id'],request.form.get('station_id'))).fetchone()
            if not s: abort(400,'Choose an active station in this organization.')
            operator_id=request.form.get('operator_id') or None
            if operator_id and not db().execute("SELECT 1 FROM memberships WHERE tenant_id=? AND user_id=? AND instr(','||roles||',',',operator,')>0",(g.tenant['id'],operator_id)).fetchone():
                abort(400,'Choose an active authorized operator in this organization, or leave unassigned.')
            old=assignment(db(),g.tenant['id'],operation_id)
            db().execute('INSERT INTO operation_assignments VALUES(?,?,?,?,?) ON CONFLICT(operation_id) DO UPDATE SET station_id=excluded.station_id,operator_id=excluded.operator_id',(g.tenant['id'],ticket_id,operation_id,s['id'],operator_id))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            db().execute("UPDATE serial_units SET version=version+1 WHERE tenant_id=? AND ticket_id=? AND state IN ('ready','in_progress')",(g.tenant['id'],ticket_id))
            event(ticket_id,'operation_assigned',json.dumps(dict(operation=op['name'],before=dict(old) if old else None,station=s['name'],operator_id=operator_id,reason=why)))
        return redirect(url_for('route_assignments',ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/release')
    @roles_required('planner','admin')
    def release_ticket(ticket_id):
        why=reason()
        with db():
            ticket=lock_ticket(ticket_id)
            check_actor({'planner','admin'})
            if released(db(),g.tenant['id'],ticket_id): abort(409,'Ticket is already released or is an existing legacy traveler.')
            missing=db().execute('SELECT 1 FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id WHERE o.tenant_id=? AND o.ticket_id=? AND (s.id IS NULL OR s.active=0)',(g.tenant['id'],ticket_id)).fetchone()
            if missing: abort(409,'Assign an active station to every routing step before release.')
            db().execute('UPDATE ticket_dispatch SET released=1 WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            event(ticket_id,'ticket_released',why)
        return redirect(url_for('ticket_detail',ticket_id=ticket_id))
