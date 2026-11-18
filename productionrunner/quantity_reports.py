"""Piece counts from recorded completions, never inferred from timing sessions."""
import csv,io
from datetime import date,datetime,timedelta,timezone
from flask import g,request,abort,render_template,Response


def records(conn,tenant,metric,start,end):
    if metric=='accepted':
        queries=[("SELECT t.id AS ticket_id,t.pn,t.revision,t.quantity,'' AS traveler,'Final inspection accepted' AS operation,i.created_at AS recorded_at,u.username FROM inspections i JOIN tickets t ON t.tenant_id=i.tenant_id AND t.id=i.ticket_id JOIN users u ON u.id=i.inspector_id WHERE i.tenant_id=? AND i.result='pass' AND i.created_at>=? AND i.created_at<? AND NOT EXISTS(SELECT 1 FROM serial_units s WHERE s.tenant_id=t.tenant_id AND s.ticket_id=t.id)"),
        ("SELECT t.id AS ticket_id,t.pn,t.revision,COALESCE(l.quantity,1) AS quantity,s.serial AS traveler,'Final inspection accepted' AS operation,i.created_at AS recorded_at,u.username FROM unit_inspections i JOIN tickets t ON t.tenant_id=i.tenant_id AND t.id=i.ticket_id JOIN serial_units s ON s.tenant_id=i.tenant_id AND s.id=i.unit_id LEFT JOIN production_lots l ON l.tenant_id=i.tenant_id AND l.unit_id=i.unit_id JOIN users u ON u.id=i.inspector_id WHERE i.tenant_id=? AND i.result='pass' AND i.created_at>=? AND i.created_at<?")]
    else:
        queries=[("SELECT t.id AS ticket_id,t.pn,t.revision,t.quantity,'' AS traveler,o.name AS operation,o.completed_at AS recorded_at,u.username FROM operations o JOIN tickets t ON t.tenant_id=o.tenant_id AND t.id=o.ticket_id JOIN users u ON u.id=o.completed_by WHERE o.tenant_id=? AND o.completed_at>=? AND o.completed_at<? AND NOT EXISTS(SELECT 1 FROM serial_units s WHERE s.tenant_id=t.tenant_id AND s.ticket_id=t.id)"),
        ("SELECT t.id AS ticket_id,t.pn,t.revision,COALESCE(l.quantity,1) AS quantity,s.serial AS traveler,o.name AS operation,c.completed_at AS recorded_at,u.username FROM unit_completions c JOIN tickets t ON t.tenant_id=c.tenant_id AND t.id=c.ticket_id JOIN operations o ON o.tenant_id=c.tenant_id AND o.id=c.operation_id JOIN serial_units s ON s.tenant_id=c.tenant_id AND s.id=c.unit_id LEFT JOIN production_lots l ON l.tenant_id=c.tenant_id AND l.unit_id=c.unit_id JOIN users u ON u.id=c.actor_id WHERE c.tenant_id=? AND c.completed_at>=? AND c.completed_at<?")]
    if metric=='accepted':
        queries.append("SELECT t.id AS ticket_id,t.pn,t.revision,CASE WHEN r.unit_id IS NULL THEN t.quantity ELSE COALESCE(l.quantity,1) END AS quantity,COALESCE(s.serial,'') AS traveler,'Accepted after rework' AS operation,r.inspected_at AS recorded_at,u.username FROM rework_cases r JOIN tickets t ON t.tenant_id=r.tenant_id AND t.id=r.ticket_id LEFT JOIN serial_units s ON s.tenant_id=r.tenant_id AND s.id=r.unit_id LEFT JOIN production_lots l ON l.tenant_id=r.tenant_id AND l.unit_id=r.unit_id JOIN users u ON u.id=r.inspector_id WHERE r.tenant_id=? AND r.result='pass' AND r.inspected_at>=? AND r.inspected_at<? AND NOT EXISTS(SELECT 1 FROM rework_cases x WHERE x.tenant_id=r.tenant_id AND x.ticket_id=r.ticket_id AND (x.unit_id=r.unit_id OR (x.unit_id IS NULL AND r.unit_id IS NULL)) AND x.result='pass' AND x.id<r.id) AND NOT EXISTS(SELECT 1 FROM inspections i WHERE r.unit_id IS NULL AND i.tenant_id=r.tenant_id AND i.ticket_id=r.ticket_id AND i.result='pass') AND NOT EXISTS(SELECT 1 FROM unit_inspections i WHERE i.tenant_id=r.tenant_id AND i.unit_id=r.unit_id AND i.result='pass')")
    return sorted([dict(r) for sql in queries for r in conn.execute(sql,(tenant,start,end))],key=lambda r:(r['recorded_at'],r['ticket_id'],r['traveler']))


def register(app,db,roles_required):
    @app.get('/admin/reports/quantity')
    @roles_required('admin')
    def quantity_report():
        today=datetime.now(timezone.utc).date()
        try:
            start=date.fromisoformat(request.args.get('from',str(today-timedelta(days=30))))
            through=date.fromisoformat(request.args.get('to',str(today)))
            end=through+timedelta(days=1)
        except (ValueError,OverflowError):abort(400,'Use valid report dates.')
        if end<=start or (end-start).days>366:abort(400,'Choose up to 366 days in chronological order.')
        metric=request.args.get('metric','accepted')
        if metric not in {'accepted','operations'}:abort(400,'Choose a valid quantity measure.')
        selected=records(db(),g.tenant['id'],metric,start.isoformat(),end.isoformat())
        pn=request.args.get('pn','').strip();operation=request.args.get('operation','').strip()
        if pn:selected=[r for r in selected if r['pn']==pn]
        choices=sorted({r['operation'] for r in selected})
        if metric=='operations' and operation:selected=[r for r in selected if r['operation']==operation]
        summary={}
        for r in selected:
            key=(r['pn'],r['revision'],r['operation'])
            summary[key]=summary.get(key,0)+r['quantity']
        if request.args.get('format')=='csv':
            output=io.StringIO();writer=csv.writer(output)
            writer.writerow(['Measure','JobRunner','PN','Revision','Traveler','Operation','Pieces','Recorded UTC','Recorded by'])
            def safe(v):
                v=str(v);return "'"+v if v.lstrip().startswith(('=','+','-','@')) else v
            for r in selected:writer.writerow([safe(v) for v in (metric,r['ticket_id'],r['pn'],r['revision'],r['traveler'],r['operation'],r['quantity'],r['recorded_at'],r['username'])])
            return Response(output.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=production-pieces.csv'})
        parts=[r[0] for r in db().execute('SELECT DISTINCT pn FROM tickets WHERE tenant_id=? ORDER BY pn',(g.tenant['id'],))]
        return render_template('quantity_report.html',rows=selected,summary=summary,total=sum(r['quantity'] for r in selected),start=start,end=through,metric=metric,parts=parts,operations=choices)
