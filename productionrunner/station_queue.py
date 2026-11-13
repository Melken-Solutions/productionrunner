"""One station/traveler projection shared by tablet queues and station TVs."""
from collections import defaultdict
from .dispatch import released
from .fulfillment import progress
from .job_plan import current as current_plan, first_piece_passed
from .lots import pending_checks
from .outside_processing import step as outside_step
from .shipment_targets import target_for
from .work_tracking import open_sessions


def travelers(conn, tenant, station):
    tickets=conn.execute("SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id) ORDER BY p.priority IS NULL,p.priority,p.hot_since,CASE WHEN p.priority IS NOT NULL THEN t.id END,t.created_at DESC,t.id",(tenant,)).fetchall()
    operations=defaultdict(list)
    for op in conn.execute('SELECT o.*,a.station_id,s.name AS station_name,u.username AS assigned_operator FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id LEFT JOIN users u ON u.id=a.operator_id WHERE o.tenant_id=? ORDER BY o.sequence',(tenant,)):
        operations[op['ticket_id']].append(op)
    units=defaultdict(list)
    for unit in conn.execute('SELECT s.*,COALESCE(l.quantity,1) AS quantity,COALESCE(l.scrapped,0) AS scrapped FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? ORDER BY s.serial',(tenant,)):
        units[unit['ticket_id']].append(unit)
    done=defaultdict(set)
    for row in conn.execute('SELECT unit_id,operation_id FROM unit_completions WHERE tenant_id=?',(tenant,)):
        done[row['unit_id']].add(row['operation_id'])
    moves=defaultdict(set)
    for row in conn.execute('SELECT unit_id,kind FROM fulfillment_active_units WHERE tenant_id=?',(tenant,)):
        moves[row['unit_id']].add(row['kind'])
    sessions={(s['ticket_id'],s['unit_id'],s['operation_id']):s for s in open_sessions(conn,tenant)}
    output=[]
    for ticket in tickets:
        delivery=progress(conn,tenant,ticket)
        if delivery['closed']:continue
        plan=current_plan(conn,tenant,ticket['id']);ops=operations[ticket['id']]
        live=released(conn,tenant,ticket['id']);shipment=target_for(conn,tenant,ticket)
        req=conn.execute('SELECT required_date FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone()
        common=dict(ticket_id=ticket['id'],pn=ticket['pn'],revision=ticket['revision'],order_quantity=ticket['quantity'],hot=ticket['hot_priority'],accepted=delivery['accepted'],due=req['required_date'] if req else '',shipment=shipment)
        def add(unit, stage, label, status, amount, op=None, note=''):
            output.append(dict(common,code=unit['id'] or ticket['id'],unit_id=unit['id'],traveler=unit.get('serial') or ticket['id'],quantity=amount,stage=stage,operation_id=op['id'] if op else None,operation=label,status=status,note=note,operator=op['assigned_operator'] or 'Shared / unassigned' if op else 'Authorized inspector' if stage=='inspection' else 'Operator / planner'))
        for raw in units[ticket['id']] or [dict(id=None,state=ticket['state'],quantity=ticket['quantity'],scrapped=0)]:
            unit=dict(raw)
            if unit['scrapped'] or 'depart' in moves[unit['id']]:continue
            amount=unit['quantity'];uid=unit['id']
            pending=[op for op in ops if op['id'] not in done[uid]] if uid else [op for op in ops if op['completed_by'] is None]
            current=pending[0] if pending else None
            if unit['state']!='accepted':
                for op in pending:
                    active=sessions.get((ticket['id'],uid,op['id']))
                    effective_station=active['station_id'] if active else op['station_id']
                    if effective_station is not None and effective_station!=station:continue
                    if effective_station is None and (op!=current or outside_step(conn,tenant,op['id'])):continue
                    if not live:status,note='planning','Waiting for planner release'
                    elif unit['state']=='held' and op==current:status,note='held','Segregate material; disposition required'
                    elif op!=current:status,note='upcoming','Waiting for '+(current['station_name'] or current['name'])
                    elif outside_step(conn,tenant,op['id']):status,note='blocked','Outside processing / vendor handoff'
                    elif not first_piece_passed(conn,tenant,ticket['id'],op['id']):status,note='setup','First-piece approval required before production'
                    else:status,note='ready','Unassigned — pick up at this station' if effective_station is None else 'Ready at this station'
                    active=sessions.get((ticket['id'],uid,op['id']))
                    if active and status in {'ready','setup'}:
                        note=('Setup · ' if status=='setup' else '')+active['current_operator']
                        status='paused' if active['action']=='pause' else 'running'
                    add(unit,'operation',f"{op['sequence']}. {op['name']}",status,amount,op,note)
                if not current and live:
                    focus=plan['data']['inspection']['station_id'] if plan else ops[-1]['station_id'] if ops else None
                    if focus==station:add(unit,'inspection','Independent inspection','held' if unit['state']=='held' else 'inspection',amount,note='Independent inspector required')
            elif plan and live:
                if uid:
                    candidates=[('shipping','Physical departure',amount)] if 'pack' in moves[uid] else [('packing','Packing',amount)]
                else:candidates=[('packing','Packing',delivery['available']),('shipping','Physical departure',delivery['waiting'])]
                for stage,label,count in candidates:
                    if count<=0 or plan['data'][stage]['station_id']!=station:continue
                    blocked=pending_checks(conn,tenant,ticket['id'],[uid] if uid else [],'pack' if stage=='packing' else 'depart')
                    add(unit,stage,label,'blocked' if blocked else 'ready',count,note='Business checkpoints pending' if blocked else 'Complete packaging, label and departure checks in the traveler')
    return output


def board_rows(entries):
    grouped={}
    for item in entries:
        key=(item['ticket_id'],item['stage'],item['operation_id'])
        if key not in grouped:
            grouped[key]=dict(ticket_id=item['ticket_id'],pn=item['pn'],revision=item['revision'],quantity=item['order_quantity'],hot=item['hot'],accepted=item['accepted'],operation=item['operation'],operator=item['operator'],due=item['due'],shipment=item['shipment'],**dict.fromkeys(['ready','upcoming','held','inspection','planning','running','paused','setup','blocked'],0))
        row=grouped[key];status=item['status'];amount=item['quantity']
        bucket='ready' if status in {'running','paused'} else 'upcoming' if status in {'setup','blocked'} else status
        row[bucket]+=amount
        if status in {'running','paused','setup','blocked'}:row[status]+=amount
        if status=='setup' and 'First-piece' not in row['operation']:row['operation']+=' · First-piece approval required'
    order={tid:i for i,tid in enumerate(dict.fromkeys(r['ticket_id'] for r in entries))}
    return sorted(grouped.values(),key=lambda r:(order[r['ticket_id']],int(r['operation'].split('.')[0]) if r['operation'].split('.')[0].isdigit() else float('inf'),r['operation']))
