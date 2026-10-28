"""Append-only station sessions, attributed intervals and audited time corrections."""
import csv
import io
import json
from datetime import date, datetime, timedelta, timezone
from flask import abort, current_app, g, redirect, render_template, request, Response, url_for
from .intake import actor
from .dispatch import assignment, require_dispatch

REASONS=('Machine issue','Missing material','Drawing / program issue','Tooling / setup','Quality hold','Waiting for help','Break','Shift end','Reassignment','HOT priority change')
MODES={'setup':'Setup (attended)','production':'Production (attended)','machine':'Machine run (unattended)'}


def instant(value):
    try:
        result=datetime.fromisoformat(value)
        if not result.tzinfo:raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError,TypeError):abort(400,'Use an ISO timestamp with timezone, for example 2026-10-28T14:30:00+00:00.')


def clock():return current_app.config['WORK_CLOCK']()


def required(conn,tenant):
    row=conn.execute('SELECT required FROM work_settings WHERE tenant_id=?',(tenant,)).fetchone()
    return bool(row and row[0])


def open_sessions(conn,tenant):
    return conn.execute("SELECT s.*,e.action,e.actor_id,u.username AS current_operator,e.id AS last_event_id FROM work_sessions s JOIN work_events e ON e.id=(SELECT MAX(id) FROM work_events WHERE tenant_id=s.tenant_id AND session_id=s.id) JOIN users u ON u.id=e.actor_id WHERE s.tenant_id=? AND e.action IN ('start','resume','pause')",(tenant,)).fetchall()


def contributed(conn,tenant,tid,uid,user):
    # Custody handlers cannot independently inspect their own outside work.
    for move in conn.execute('SELECT unit_ids FROM outside_moves WHERE tenant_id=? AND ticket_id=? AND actor_id=?',(tenant,tid,user)):
        if uid is None or uid in json.loads(move['unit_ids']):return True
    return conn.execute("SELECT 1 FROM work_sessions s JOIN work_events e ON e.tenant_id=s.tenant_id AND e.session_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? AND s.unit_id IS ? AND e.actor_id=? AND e.action IN ('start','resume')",(tenant,tid,uid,user)).fetchone() is not None


def ledger(conn,tenant):
    return conn.execute('SELECT s.*,t.pn,o.name AS operation_name,e.id AS event_id,e.action,e.actor_id,u.username,e.reason,e.created_at AS recorded_at,COALESCE(c.effective_at,e.created_at) AS effective_at,COALESCE(c.id,0) AS correction_version FROM work_sessions s JOIN tickets t ON t.id=s.ticket_id AND t.tenant_id=s.tenant_id JOIN operations o ON o.id=s.operation_id AND o.tenant_id=s.tenant_id JOIN work_events e ON e.session_id=s.id AND e.tenant_id=s.tenant_id JOIN users u ON u.id=e.actor_id LEFT JOIN work_time_corrections c ON c.id=(SELECT MAX(id) FROM work_time_corrections WHERE tenant_id=e.tenant_id AND event_id=e.id) WHERE s.tenant_id=? ORDER BY s.id,e.id',(tenant,)).fetchall()


def intervals(conn,tenant,stamp):
    groups={}
    for row in ledger(conn,tenant):groups.setdefault(row['id'],[]).append(row)
    result=[]
    for events in groups.values():
        for n,row in enumerate(events):
            if row['action'] not in {'start','resume','pause'}:continue
            end=instant(events[n+1]['effective_at']) if n+1<len(events) else instant(stamp)
            start=instant(row['effective_at'])
            if end<start:abort(409,'Session timestamps are not chronological.')
            result.append(dict(row,start=start,end=end,seconds=(end-start).total_seconds(),category='pause' if row['action']=='pause' else row['mode'],open=n+1==len(events)))
    return result


def validate_timeline(conn,tenant,stamp):
    groups={}
    for row in ledger(conn,tenant):groups.setdefault(row['id'],[]).append(row)
    reservations=[]
    for events in groups.values():
        times=[instant(e['effective_at']) for e in events]
        if times!=sorted(times) or any(t>instant(stamp) for t in times):abort(409,'Corrected times must stay chronological and cannot be in the future.')
        reservations.append((events[0]['station_id'],times[0],times[-1] if events[-1]['action'] in {'stop','finish'} else instant(stamp),events[0]['id']))
    def no_overlap(items):
        ends={}
        for resource,start,end,sid in sorted(items,key=lambda x:(x[0],x[1],x[2])):
            if start==end:continue
            if resource in ends and start<ends[resource]:abort(409,'Corrected time overlaps another station reservation or attended operator session.')
            ends[resource]=end
    no_overlap(reservations)
    no_overlap([(r['actor_id'],r['start'],r['end'],r['id']) for r in intervals(conn,tenant,stamp) if r['category'] in {'setup','production'}])


def append_event(conn,tenant,session,user,action,reason,stamp,event):
    conn.execute('INSERT INTO work_events(tenant_id,session_id,action,actor_id,reason,created_at) VALUES(?,?,?,?,?,?)',(tenant,session['id'],action,user,reason,stamp))
    detail=json.dumps(dict(session_id=session['id'],station_id=session['station_id'],station=session['station_name'],operation_id=session['operation_id'],unit_id=session['unit_id'],mode=session['mode'],reason=reason))
    event(session['ticket_id'],'work_'+action,detail)


def finish_work(conn,tenant,ticket,operation,user,stamp,event,unit=None):
    actor(conn,tenant,user,('operator',))
    uid=unit['id'] if unit else None
    started=conn.execute('SELECT 1 FROM work_sessions WHERE tenant_id=? AND ticket_id=? AND unit_id IS ? AND operation_id=?',(tenant,ticket['id'],uid,operation['id'])).fetchone()
    if not started and not required(conn,tenant):return
    active=next((s for s in open_sessions(conn,tenant) if s['ticket_id']==ticket['id'] and s['unit_id']==uid and s['operation_id']==operation['id']),None)
    if not active or active['action']=='pause' or active['actor_id']!=user:abort(409,'Start or resume your station work session before finishing this operation.')
    quantity=1 if unit else ticket['quantity']
    if request.form.get('completed_quantity')!=str(quantity):abort(400,f'Confirm the completed quantity: {quantity}. Batch operations finish the whole batch.')
    append_event(conn,tenant,active,user,'finish',f'Completed quantity {quantity}',clock(),event)
    validate_timeline(conn,tenant,clock())


def register_work_tracking(app,db,roles_required,now,event):
    app.config.setdefault('WORK_CLOCK',now)

    def reasons():return list(REASONS)+[r[0] for r in db().execute('SELECT name FROM work_reasons WHERE tenant_id=? ORDER BY name',(g.tenant['id'],)) if r[0] not in REASONS]

    def target(code):
        uid=code if code.startswith('JR-U-') else None
        unit=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?',(g.tenant['id'],uid)).fetchone() if uid else None
        if uid and not unit:abort(404)
        tid=unit['ticket_id'] if unit else code
        ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],tid)).fetchone()
        if not ticket:abort(404)
        from .production_controls import require_active
        require_active(db(),g.tenant['id'],tid)
        if not unit and db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],tid)).fetchone():abort(409,'Open an individual serial traveler.')
        op=db().execute('SELECT o.* FROM operations o WHERE o.tenant_id=? AND o.ticket_id=? AND '+('NOT EXISTS(SELECT 1 FROM unit_completions c WHERE c.tenant_id=o.tenant_id AND c.operation_id=o.id AND c.unit_id=?)' if unit else 'o.completed_by IS NULL')+' ORDER BY o.sequence LIMIT 1',(g.tenant['id'],tid,uid) if unit else (g.tenant['id'],tid)).fetchone()
        return ticket,unit,op

    def emit(session):
        def write(tid,action,detail):
            event(tid,action,detail)
            if session['unit_id']:
                eid=db().execute('SELECT last_insert_rowid()').fetchone()[0]
                db().execute('INSERT INTO unit_event_links VALUES(?,?,?,?)',(g.tenant['id'],tid,session['unit_id'],eid))
        return write

    @app.route('/work/<code>',methods=['GET','POST'])
    @roles_required('operator')
    def station_work(code):
        if request.method=='POST':
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('operator',))
                ticket,unit,op=target(code)
                current=unit if unit else ticket
                if request.form.get('version')!=str(current['version']):abort(409,'Traveler changed. Reload.')
                if not op or current['state'] not in {'ready','in_progress'}:abort(409,'No production operation is available.')
                from .outside_processing import require_internal
                require_internal(db(),g.tenant['id'],op['id'])
                require_dispatch(db(),g.tenant['id'],ticket['id'],op['id'])
                a=assignment(db(),g.tenant['id'],op['id'])
                if not a:abort(409,'Assign this operation to a station first.')
                sessions=open_sessions(db(),g.tenant['id']);uid=unit['id'] if unit else None
                active=next((s for s in sessions if s['ticket_id']==ticket['id'] and s['unit_id']==uid and s['operation_id']==op['id']),None)
                action=request.form.get('action');reason='';stamp=clock()
                if action in {'pause','stop'}:
                    reason=request.form.get('reason','')
                    if reason not in reasons():abort(400,'Choose an interruption reason.')
                if action=='start':
                    if active:abort(409,'This operation already has a station session.')
                    if any(s['station_id']==a['station_id'] for s in sessions):abort(409,'This station is reserved. Stop its other session first.')
                    mode=request.form.get('mode')
                    if mode not in MODES:abort(400,'Choose setup, attended production or unattended machine run.')
                    if mode!='machine' and any(s['action']!='pause' and s['mode']!='machine' and s['actor_id']==g.user['id'] for s in sessions):abort(409,'Pause or stop your other attended session first.')
                    hot=db().execute('SELECT priority FROM ticket_priorities WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone()
                    row=db().execute('INSERT INTO work_sessions(tenant_id,ticket_id,unit_id,operation_id,station_id,station_name,mode,hot_priority,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(g.tenant['id'],ticket['id'],uid,op['id'],a['station_id'],a['station_name'],mode,hot[0] if hot else None,stamp))
                    active=db().execute('SELECT * FROM work_sessions WHERE id=?',(row.lastrowid,)).fetchone()
                else:
                    if not active or request.form.get('session_version')!=str(active['last_event_id']):abort(409,'Work session changed. Reload.')
                    if action=='resume':
                        if active['action']!='pause':abort(409,'Only paused work can resume.')
                        if active['mode']!='machine' and any(s['action']!='pause' and s['mode']!='machine' and s['actor_id']==g.user['id'] for s in sessions):abort(409,'Pause or stop your other attended session first.')
                    elif action in {'pause','stop'}:
                        if active['actor_id']!=g.user['id']:abort(403,'Only the current operator can pause/stop. An admin can stop abandoned sessions.')
                        if action=='pause' and active['action']=='pause':abort(409,'Already paused.')
                    else:abort(400,'Choose Start, Pause, Resume or Stop. Use Finish to complete the operation.')
                append_event(db(),g.tenant['id'],active,g.user['id'],action,reason,stamp,emit(active))
                validate_timeline(db(),g.tenant['id'],stamp)
                if unit:
                    from .serials import update_unit_state
                    update_unit_state(db(),unit,'in_progress')
                else:
                    db().execute("UPDATE tickets SET state='in_progress',version=version+1 WHERE tenant_id=? AND id=?",(g.tenant['id'],ticket['id']))
            return redirect(url_for('station_work',code=code))
        ticket,unit,op=target(code)
        active=next((s for s in open_sessions(db(),g.tenant['id']) if s['ticket_id']==ticket['id'] and s['unit_id']==(unit['id'] if unit else None)),None)
        return render_template('station_work.html',ticket=ticket,unit=unit,target=unit if unit else ticket,op=op,active=active,reasons=reasons(),modes=MODES,code=code,assigned=assignment(db(),g.tenant['id'],op['id']) if op else None)

    @app.route('/admin/work',methods=['GET','POST'])
    @roles_required('admin')
    def work_admin():
        if request.method=='POST':
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                why=request.form.get('reason','').strip()
                if not 1<=len(why)<=500:abort(400,'Record a reason.')
                if request.form.get('action')=='reason':
                    name=request.form.get('name','').strip()
                    if not 1<=len(name)<=120 or name in reasons():abort(400,'Enter a new reason of 1–120 characters.')
                    db().execute('INSERT INTO work_reasons(tenant_id,name) VALUES(?,?)',(g.tenant['id'],name))
                elif request.form.get('action')=='settings':
                    row=db().execute('SELECT * FROM work_settings WHERE tenant_id=?',(g.tenant['id'],)).fetchone()
                    if request.form.get('version')!=str(row['version'] if row else 0):abort(409,'Settings changed.')
                    value=request.form.get('required','0')
                    if value not in {'0','1'}:abort(400)
                    db().execute('INSERT INTO work_settings VALUES(?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET required=excluded.required,version=work_settings.version+1',(g.tenant['id'],int(value)))
                else:abort(400)
                from .pin_auth import audit
                audit(db(),g.tenant['id'],g.user['id'],'work_settings_changed',f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps({k:v for k,v in request.form.items() if k!='csrf'}))
            return redirect(url_for('work_admin'))
        row=db().execute('SELECT * FROM work_settings WHERE tenant_id=?',(g.tenant['id'],)).fetchone()
        return render_template('work_admin.html',settings=row,reasons=reasons(),sessions=open_sessions(db(),g.tenant['id']))

    @app.route('/admin/work/sessions/<int:sid>',methods=['GET','POST'])
    @roles_required('admin')
    def work_session_admin(sid):
        session=db().execute('SELECT * FROM work_sessions WHERE tenant_id=? AND id=?',(g.tenant['id'],sid)).fetchone()
        if not session:abort(404)
        if request.method=='POST':
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                why=request.form.get('reason','').strip()
                if not 1<=len(why)<=500:abort(400,'Record a correction reason.')
                active=next((s for s in open_sessions(db(),g.tenant['id']) if s['id']==sid),None)
                if request.form.get('action')=='stop':
                    if not active or request.form.get('version')!=str(active['last_event_id']):abort(409,'Session changed.')
                    append_event(db(),g.tenant['id'],active,g.user['id'],'stop','Admin stop: '+why,clock(),emit(active))
                elif request.form.get('action')=='correct':
                    if active:abort(409,'Stop the session before correcting timestamps.')
                    entry=next((e for e in ledger(db(),g.tenant['id']) if e['id']==sid and str(e['event_id'])==request.form.get('event_id')),None)
                    if not entry:abort(404)
                    if request.form.get('version')!=str(entry['correction_version']):abort(409,'Time correction changed. Reload.')
                    effective=instant(request.form.get('effective_at',''))
                    created=db().execute('SELECT created_at FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],session['ticket_id'])).fetchone()[0]
                    if effective<instant(created):abort(400,'Work cannot precede ticket creation.')
                    db().execute('INSERT INTO work_time_corrections(tenant_id,event_id,effective_at,reason,actor_id,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],entry['event_id'],effective.isoformat(),why,g.user['id'],now()))
                    validate_timeline(db(),g.tenant['id'],clock())
                    emit(session)(session['ticket_id'],'work_time_corrected',json.dumps(dict(event_id=entry['event_id'],before=entry['effective_at'],after=effective.isoformat(),reason=why)))
                else:abort(400)
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],session['ticket_id']))
                if session['unit_id']:db().execute('UPDATE serial_units SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],session['unit_id']))
            return redirect(url_for('work_session_admin',sid=sid))
        entries=[e for e in ledger(db(),g.tenant['id']) if e['id']==sid]
        corrections=db().execute('SELECT c.*,u.username FROM work_time_corrections c JOIN work_events e ON e.id=c.event_id AND e.tenant_id=c.tenant_id JOIN users u ON u.id=c.actor_id WHERE c.tenant_id=? AND e.session_id=? ORDER BY c.id',(g.tenant['id'],sid)).fetchall()
        return render_template('work_session.html',work=session,entries=entries,corrections=corrections,active=next((s for s in open_sessions(db(),g.tenant['id']) if s['id']==sid),None))

    @app.get('/admin/reports/work')
    @roles_required('admin')
    def work_report():
        today=instant(clock()).date()
        try:
            start=datetime.combine(date.fromisoformat(request.args.get('from',str(today-timedelta(days=30)))),datetime.min.time(),timezone.utc)
            end=datetime.combine(date.fromisoformat(request.args.get('to',str(today))),datetime.min.time(),timezone.utc)+timedelta(days=1)
        except ValueError:abort(400,'Use YYYY-MM-DD dates.')
        if end<=start or end-start>timedelta(days=366):abort(400,'Choose up to 366 days in chronological order.')
        selected=[]
        for r in intervals(db(),g.tenant['id'],clock()):
            if request.args.get('station') and str(r['station_id'])!=request.args['station']:continue
            if request.args.get('operator') and str(r['actor_id'])!=request.args['operator']:continue
            a,b=max(start,r['start']),min(end,r['end'])
            if a>=b:continue
            selected.append(dict(r,start=a,end=b,seconds=(b-a).total_seconds()))
        totals={k:sum(r['seconds'] for r in selected if r['category']==k)/60 for k in ('setup','production','machine','pause')}
        reasons_summary={}
        for r in selected:
            if r['category']=='pause':reasons_summary[r['reason']]=reasons_summary.get(r['reason'],0)+r['seconds']/60
        station_totals={}
        for r in selected:
            summary=station_totals.setdefault(r['station_name'],dict(setup=0,production=0,machine=0,pause=0))
            summary[r['category']]+=r['seconds']/60
        stop_totals={}
        for e in ledger(db(),g.tenant['id']):
            if e['action']!='stop' or not start<=instant(e['effective_at'])<end:continue
            if request.args.get('station') and str(e['station_id'])!=request.args['station']:continue
            if request.args.get('operator') and str(e['actor_id'])!=request.args['operator']:continue
            stop_totals[e['reason']]=stop_totals.get(e['reason'],0)+1
        if request.args.get('format')=='csv':
            stream=io.StringIO();w=csv.writer(stream);w.writerow(['Session','Ticket','Unit','Station','Operation','Actor','Category','Reason','Start UTC','End UTC','Minutes','HOT at start','Live interval'])
            def safe(v):
                v=str(v if v is not None else '')
                return "'"+v if v.lstrip().startswith(('=','+','-','@')) else v
            for r in selected:w.writerow([safe(v) for v in (r['id'],r['ticket_id'],r['unit_id'],r['station_name'],r['operation_name'],r['username'],r['category'],r['reason'],r['start'].isoformat(),r['end'].isoformat(),round(r['seconds']/60,3),r['hot_priority'],r['open'])])
            return Response(stream.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=station-work.csv'})
        return render_template('work_report.html',rows=selected,totals=totals,reason_totals=reasons_summary,station_totals=station_totals,stop_totals=stop_totals,start=start.date(),end=(end-timedelta(days=1)).date(),stations=db().execute('SELECT id,name FROM stations WHERE tenant_id=? ORDER BY name',(g.tenant['id'],)).fetchall(),operators=db().execute('SELECT u.id,u.username FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=? ORDER BY u.username',(g.tenant['id'],)).fetchall())
