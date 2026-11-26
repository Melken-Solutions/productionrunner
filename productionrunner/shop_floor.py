"""Touch-first read/confirmation views; execution uses the existing guarded endpoints."""
from .dispatch import released
from flask import abort, g, redirect, render_template, request, url_for, session


def register_shop_floor(app, db, roles_required):
    def page_number():
        try:
            page = int(request.args.get('page', '1'))
        except ValueError:
            abort(400, 'Invalid page.')
        if not 1 <= page <= 100000:
            abort(400, 'Invalid page.')
        return page

    def ticket_for(ticket_id):
        ticket = db().execute('SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND t.id=?', (g.tenant['id'],ticket_id)).fetchone()
        if not ticket:
            abort(404, 'Unknown traveler. Scan again or return to Shop Floor.')
        return ticket

    def context(code):
        unit = None
        if code.startswith('JR-U-'):
            unit = db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND id=?',(g.tenant['id'],code)).fetchone()
            if not unit:
                abort(404,'Unknown unit traveler.')
            ticket = ticket_for(unit['ticket_id'])
            operations = db().execute('SELECT o.id,o.sequence,o.name,o.instructions,c.actor_id AS completed_by,c.completed_at FROM operations o LEFT JOIN unit_completions c ON c.tenant_id=o.tenant_id AND c.operation_id=o.id AND c.unit_id=? WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',(code,g.tenant['id'],ticket['id'])).fetchall()
        else:
            ticket = ticket_for(code)
            operations = db().execute('SELECT * FROM operations WHERE tenant_id=? AND ticket_id=? ORDER BY sequence',(g.tenant['id'],ticket['id'])).fetchall()
        has_units = not unit and db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone() is not None
        cancelled = db().execute('SELECT * FROM ticket_cancellations WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket['id'])).fetchone()
        target = unit if unit else ticket
        next_op = next((o for o in operations if o['completed_by'] is None),None)
        return dict(ticket=ticket,unit=unit,target=target,operations=operations,next_operation=next_op,
                    has_units=has_units,cancelled=cancelled,code=code,
                    can_complete=released(db(),g.tenant['id'],ticket['id']) and not cancelled and not has_units and target['state'] in {'ready','in_progress'} and next_op is not None)

    @app.get('/shop-floor')
    @roles_required('operator','admin')
    def shop_floor(today_view=False):
        from datetime import date, datetime, timezone
        try:
            day=date.fromisoformat(request.args.get('day',datetime.now(timezone.utc).date().isoformat())).isoformat()
        except ValueError:
            abort(400, 'Choose a valid date.')
        page = page_number()
        stations=db().execute('SELECT id,name FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall()
        key='shop_station_'+str(g.tenant['id'])
        from .admin import primary_station
        primary=primary_station(db(), g.tenant['id'], g.user['id']) if 'operator' in g.roles else None
        default=str(primary['id']) if primary else ''
        selected=request.args.get('station',session.get(key,default))
        if selected and selected not in {str(s['id']) for s in stations}:
            if 'station' in request.args:abort(404,'Unknown station.')
            selected=default
        session[key]=selected
        station_rows={}
        station_travelers=[]
        if selected:
            from .station_queue import travelers
            # Match the TV's read snapshot; never combine states from two writes.
            with db():
                db().execute('BEGIN')
                station_travelers=travelers(db(),g.tenant['id'],int(selected))
            groups=[('Working now',{'running','paused'}),('Ready at this station',{'ready','setup','inspection'}),('Upcoming — finish earlier steps first',{'upcoming'}),('Waiting / held',{'planning','held','blocked'})]
            if today_view:
                from .production_calendar import assignments
                planned=assignments(db(),g.tenant['id'])
                for row in station_travelers:
                    row['planned_day']=planned.get((row['ticket_id'],row['unit_id']),planned.get((row['ticket_id'],None),{})).get('day')
                all_statuses={'running','paused','ready','setup','inspection','upcoming','planning','held','blocked'}
                groups=[('Working now',{'running','paused'}),('Planned today',all_statuses),('Earlier scheduled work',all_statuses),('HOT jobs',all_statuses),('Ready at this station',{'ready','setup','inspection'}),('Waiting / upcoming',{'upcoming','planning','held','blocked'})]
            return render_template('station_queue.html',today_view=today_view,day=day,stations=stations,selected_station=selected,primary_station=primary,groups=groups,entries=station_travelers)
        tickets = db().execute("SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND (t.state!='accepted' OR ?=1) AND NOT EXISTS(SELECT 1 FROM ticket_cancellations x WHERE x.tenant_id=t.tenant_id AND x.ticket_id=t.id) ORDER BY (p.priority IS NULL),p.priority,p.hot_since,CASE WHEN p.priority IS NOT NULL THEN t.id END,t.created_at DESC,t.id",(g.tenant['id'],bool(selected))).fetchall()
        from .production_calendar import ordered
        tickets=ordered(db(),g.tenant['id'],tickets)
        if selected:tickets=[t for t in tickets if t['id'] in station_rows]
        tickets=tickets[(page-1)*50:page*50+1]
        return render_template('shop_queue.html',today_view=today_view,day=day,tickets=tickets[:50],has_more=len(tickets)>50,page=page,stations=stations,selected_station=selected,primary_station=primary,station_rows=station_rows)

    @app.get('/shop-floor/scan')
    @roles_required('operator','admin')
    def shop_scan():
        code = request.args.get('code','').strip()
        context(code)
        return redirect(url_for('shop_work',code=code))

    @app.get('/shop-floor/work/<code>')
    @roles_required('operator','admin')
    def shop_work(code):
        data = context(code)
        from .dispatch import assignment
        from .outside_processing import step as outside_step
        op=data['next_operation']
        selected=session.get('shop_station_'+str(g.tenant['id']))
        if selected and not data['has_units'] and not data['cancelled'] and data['target']['state']!='held':
            from .station_queue import travelers
            matches=[r for r in travelers(db(),g.tenant['id'],int(selected)) if r['code']==code]
            available=next((r for r in matches if r['status'] in {'ready','running','paused','setup','inspection'}),None)
            if not available:
                assigned=assignment(db(),g.tenant['id'],op['id']) if op else None
                return render_template('station_handoff.html',**data,next_station=assigned['station_id'] if assigned else None,message='Not ready at this station',detail=matches[0]['note'] if matches else 'Next station: '+assigned['station_name']+'. Keep the traveler with the material.' if assigned else 'Production is complete. Keep the traveler with the material for inspection, packing or shipping.')
        if data['can_complete'] and (assignment(db(),g.tenant['id'],op['id']) or db().execute('SELECT 1 FROM stations WHERE tenant_id=? AND active=1',(g.tenant['id'],)).fetchone()) and not outside_step(db(),g.tenant['id'],op['id']):
            return redirect(url_for('station_work',code=code))
        page = page_number()
        units=[]
        if data['has_units']:
            units=db().execute('SELECT * FROM serial_units WHERE tenant_id=? AND ticket_id=? ORDER BY serial LIMIT 51 OFFSET ?',(g.tenant['id'],data['ticket']['id'],(page-1)*50)).fetchall()
        if 'operator' not in g.roles:data['can_complete']=False
        return render_template('shop_work.html',**data,units=units[:50],page=page,has_more=len(units)>50)

    @app.get('/shop-floor/work/<code>/confirm')
    @roles_required('operator')
    def shop_confirm(code):
        data=context(code)
        if not data['can_complete']:
            abort(409,'This traveler is not available for production. Reopen its live status.')
        if request.args.get('version') != str(data['target']['version']) or request.args.get('operation_id') != str(data['next_operation']['id']):
            abort(409,'Work changed. Reopen the traveler before confirming.')
        return render_template('shop_confirm.html',**data)
