"""Audited work-day planning, independent of ship dates and production gates."""
import json
from datetime import date,timedelta
from flask import g,request,abort,redirect,url_for,render_template
from .intake import actor


def assignments(conn,tenant):
    result={}
    for r in conn.execute("SELECT e.* FROM events e WHERE e.tenant_id=? AND e.action='production_day_planned' ORDER BY e.id",(tenant,)):
        d=json.loads(r['detail']);result[(r['ticket_id'],d.get('unit_id'))]=dict(d,ticket_id=r['ticket_id'],created_at=r['created_at'])
    return result


def register(app,db,roles_required,lock_ticket,event):
    @app.context_processor
    def calendar_context():
        def planned_day(ticket,unit=None):
            if not hasattr(g,'work_day_assignments'):g.work_day_assignments=assignments(db(),g.tenant['id'])
            saved=g.work_day_assignments
            row=saved.get((ticket['id'],unit),saved.get((ticket['id'],None)))
            return row.get('day') if row else None
        return dict(planned_work_day=planned_day)

    @app.get('/calendar')
    @roles_required('planner','admin')
    def production_calendar():
        try:start=date.fromisoformat(request.args.get('start',date.today().isoformat()))
        except ValueError:abort(400,'Choose a valid calendar date.')
        days=[(start+timedelta(days=i)).isoformat() for i in range(7)]
        saved=assignments(db(),g.tenant['id']);jobs=[];scheduled=[]
        tickets=db().execute('SELECT * FROM tickets t WHERE tenant_id=? AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id) ORDER BY created_at DESC',(g.tenant['id'],)).fetchall()
        from .fulfillment import progress
        for t in tickets:
            delivery=progress(db(),g.tenant['id'],t)
            if delivery['closed']:continue
            units=db().execute('SELECT s.*,COALESCE(l.quantity,1) AS quantity FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? AND COALESCE(l.scrapped,0)=0 AND NOT EXISTS(SELECT 1 FROM fulfillment_active_units f WHERE f.tenant_id=s.tenant_id AND f.unit_id=s.id AND f.kind=\'depart\')',(g.tenant['id'],t['id'])).fetchall()
            jobs.append(dict(t,units=units,planned=saved.get((t['id'],None))))
            # A whole-job day is a default; traveler dates override it without double counting.
            for u in units or [dict(id=None,serial='Whole job',quantity=delivery['remaining'])]:
                a=saved.get((t['id'],u['id']),saved.get((t['id'],None)))
                if a and a.get('day') in days:scheduled.append(dict(a,pn=t['pn'],traveler=u['serial'],quantity=u['quantity']))
        return render_template('production_calendar.html',jobs=jobs,scheduled=scheduled,days=days,start=start.isoformat(),previous=(start-timedelta(days=7)).isoformat(),following=(start+timedelta(days=7)).isoformat())

    @app.post('/tickets/<ticket_id>/calendar')
    @roles_required('planner','admin')
    def plan_production_day(ticket_id):
        day=request.form.get('day','');unit=request.form.get('unit_id') or None
        if day:
            try:day=date.fromisoformat(day).isoformat()
            except ValueError:abort(400,'Choose a valid work day.')
        reason=request.form.get('reason','').strip()
        if not 1<=len(reason)<=500:abort(400,'Choose a scheduling reason.')
        with db():
            t=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'],('planner','admin'))
            from .production_controls import require_active
            from .fulfillment import progress
            require_active(db(),g.tenant['id'],ticket_id)
            if progress(db(),g.tenant['id'],t)['closed']:abort(409,'This job has already shipped.')
            if unit:
                u=db().execute('SELECT s.id,COALESCE(l.scrapped,0) AS scrapped FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id=? AND s.id=?',(g.tenant['id'],ticket_id,unit)).fetchone()
                if not u or u['scrapped']:abort(404,'Active traveler unavailable.')
                if db().execute("SELECT 1 FROM fulfillment_active_units WHERE tenant_id=? AND unit_id=? AND kind='depart'",(g.tenant['id'],unit)).fetchone():abort(409,'This traveler has already shipped.')
            event(ticket_id,'production_day_planned',json.dumps(dict(unit_id=unit,day=day,reason=reason)))
            db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
        return redirect(url_for('production_calendar',start=day or date.today().isoformat()))
