"""Read-only tenant TV boards. Counts follow each unit's next incomplete operation."""
from collections import defaultdict
from flask import abort,g,jsonify,render_template,request,redirect,url_for
from .dispatch import released
from .fulfillment import progress
from .outside_processing import step as outside_step, status as outside_status


def board_data(conn,tenant_id,station_id=None):
    tickets=conn.execute("SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.tenant_id=t.tenant_id AND p.ticket_id=t.id WHERE t.tenant_id=? AND NOT EXISTS(SELECT 1 FROM ticket_cancellations c WHERE c.tenant_id=t.tenant_id AND c.ticket_id=t.id) ORDER BY p.priority IS NULL,p.priority,p.hot_since,CASE WHEN p.priority IS NOT NULL THEN t.id END,t.created_at DESC,t.id",(tenant_id,)).fetchall()
    operations=defaultdict(list)
    for op in conn.execute('SELECT o.*,a.station_id,s.name AS station_name,u.username AS assigned_operator FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id LEFT JOIN stations s ON s.tenant_id=a.tenant_id AND s.id=a.station_id LEFT JOIN users u ON u.id=a.operator_id WHERE o.tenant_id=? ORDER BY o.sequence',(tenant_id,)):
        operations[op['ticket_id']].append(op)
    units=defaultdict(list)
    for unit in conn.execute('SELECT * FROM serial_units WHERE tenant_id=?',(tenant_id,)):
        units[unit['ticket_id']].append(unit)
    completions=defaultdict(set)
    for done in conn.execute('SELECT unit_id,operation_id FROM unit_completions WHERE tenant_id=?',(tenant_id,)):
        completions[done['unit_id']].add(done['operation_id'])
    dispatch={r['ticket_id']:r['released'] for r in conn.execute('SELECT * FROM ticket_dispatch WHERE tenant_id=?',(tenant_id,))}
    output=[]
    for ticket in tickets:
        from .job_plan import current as current_plan, first_piece_passed
        plan=current_plan(conn,tenant_id,ticket['id'])
        delivery=progress(conn,tenant_id,ticket)
        if delivery['closed']:continue
        ops=operations[ticket['id']]
        is_released=dispatch.get(ticket['id'],1)
        targets=units[ticket['id']] or [dict(id=None,state=ticket['state'])]
        per_op={op['id']:defaultdict(int) for op in ops}
        stages=defaultdict(int);held=0;ready=0;inspection=0;accepted=0;final_held=0
        for target in targets:
            amount=1 if target['id'] else ticket['quantity']
            if target['state']=='accepted':
                accepted+=amount;continue
            pending=[op for op in ops if (op['id'] not in completions[target['id']] if target['id'] else op['completed_by'] is None)]
            current=pending[0] if pending else None
            piece_pending=bool(current and not first_piece_passed(conn,tenant_id,ticket['id'],current['id']))
            if target['state']=='held':
                held+=amount
                # An inspection hold stays attributed to the final production station until disposition.
                focus=current or (ops[-1] if ops else None)
                if plan and not current:final_held+=amount
                elif focus: per_op[focus['id']]['held']+=amount
                stages['HOLD · '+((focus['station_name'] or 'Unassigned') if focus else 'No route')]+=amount
            elif not is_released:
                stages['Awaiting planner release']+=amount
            elif current and outside_step(conn,tenant_id,current['id']):
                stages[outside_status(conn,tenant_id,current['id'],target['id'])]+=amount
            elif current and piece_pending:
                stages[(current['station_name'] or 'Unassigned')+' · First-piece approval required']+=amount
            elif current:
                ready+=amount
                stages[(current['station_name'] or 'Unassigned')+' · '+current['name']]+=amount
            else:
                inspection+=amount;stages['Independent inspection']+=amount
                if ops and not plan: per_op[ops[-1]['id']]['inspection']+=amount
            for op in pending:
                if not is_released: per_op[op['id']]['planning']+=amount
                elif target['state']=='held':
                    if op!=current: per_op[op['id']]['upcoming']+=amount
                elif op==current: per_op[op['id']]['upcoming' if piece_pending else 'ready']+=amount
                else: per_op[op['id']]['upcoming']+=amount
        common=dict(ticket_id=ticket['id'],pn=ticket['pn'],revision=ticket['revision'],quantity=ticket['quantity'],hot=ticket['hot_priority'],accepted=accepted)
        if station_id is None:
            if delivery['accepted']:
                stages['Accepted / awaiting packing']+=delivery['available']
                stages['Packed / awaiting departure']+=delivery['waiting']
                stages['Departed']+=delivery['departed']
                stages={name:count for name,count in stages.items() if count}
            output.append(dict(common,operation=' / '.join(f'{name}: {count}' for name,count in stages.items()),operator='',ready=ready,upcoming=0,held=held,inspection=inspection,planning=ticket['quantity']-accepted if not is_released else 0))
        else:
            for op in ops:
                counts=per_op[op['id']]
                if op['station_id']==station_id and any(counts.values()):
                    output.append(dict(common,operation=f"{op['sequence']}. {op['name']}"+(' · First-piece approval required' if is_released and not first_piece_passed(conn,tenant_id,ticket['id'],op['id']) else ''),operator=op['assigned_operator'] or 'Shared / unassigned',**{k:counts[k] for k in ['ready','upcoming','held','inspection','planning']}))
            if plan and is_released:
                pending_checks=[r[0] for r in conn.execute('SELECT c.stage FROM fulfillment_checks c WHERE c.tenant_id=? AND c.ticket_id=? AND NOT EXISTS(SELECT 1 FROM fulfillment_check_results r WHERE r.tenant_id=c.tenant_id AND r.check_id=c.id)',(tenant_id,ticket['id']))]
                for stage,label,amount in [('inspection','Independent inspection',inspection),('packing','Packing',delivery['available']),('shipping','Physical departure',delivery['waiting'])]:
                    if plan['data'][stage]['station_id']!=station_id:continue
                    hold=final_held if stage=='inspection' else 0
                    if not amount and not hold:continue
                    blocked=(stage=='packing' and 'pack' in pending_checks) or (stage=='shipping' and bool(pending_checks))
                    output.append(dict(common,operation=label+(' · business checkpoints pending' if blocked else ''),operator='Authorized '+('inspector' if stage=='inspection' else 'operator / planner'),ready=amount if stage!='inspection' and not blocked else 0,upcoming=amount if blocked else 0,held=hold,inspection=amount if stage=='inspection' else 0,planning=0))
    return output


def register_tv(app,db,roles_required,now):
    @app.before_request
    def restrict_display_account():
        if 'display' in getattr(g,'roles',[]) and request.endpoint not in {'static','login','logout','tv_boards','tv_board','tv_data','brand_logo'}:
            abort(403,'This display account can only view TV boards.')

    def selected_station():
        value=request.args.get('station')
        if not value:return None
        station=db().execute('SELECT * FROM stations WHERE tenant_id=? AND id=?',(g.tenant['id'],value)).fetchone()
        if not station:abort(404,'Unknown station.')
        return station

    @app.get('/tv')
    @roles_required()
    def tv_boards():
        stations=db().execute('SELECT * FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall()
        return render_template('tv_select.html',stations=stations)

    @app.get('/tv/board')
    @roles_required()
    def tv_board():
        station=selected_station()
        return render_template('tv_board.html',station=station)

    @app.get('/tv/data')
    @roles_required()
    def tv_data():
        station=selected_station()
        # One read transaction prevents a board from mixing two production states.
        with db():
            db().execute('BEGIN')
            rows=board_data(db(),g.tenant['id'],station['id'] if station else None)
        brand=db().execute('SELECT display_name FROM organization_branding WHERE tenant_id=?',(g.tenant['id'],)).fetchone()
        return jsonify(organization=(brand['display_name'] if brand and brand['display_name'] else g.tenant['name']),station=station['name'] if station else 'Whole shop',active=bool(station['active']) if station else True,generated_at=now(),rows=rows)
