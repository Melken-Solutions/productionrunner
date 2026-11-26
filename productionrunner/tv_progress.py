"""Exclusive job-level piece counts for glanceable production boards."""
import json
from .fulfillment import progress


def load(conn,tenant,ticket_ids):
    """Fetch visible jobs in five queries, rather than five per job."""
    ids=list(ticket_ids)
    if not ids:return {}
    slots=','.join('?' for _ in ids);params=(tenant,*ids)
    queries={
        'ops':f'SELECT ticket_id,id,completed_by FROM operations WHERE tenant_id=? AND ticket_id IN ({slots})',
        'done':f'SELECT ticket_id,unit_id,operation_id FROM unit_completions WHERE tenant_id=? AND ticket_id IN ({slots})',
        'sessions':f'SELECT DISTINCT ticket_id,unit_id FROM work_sessions WHERE tenant_id=? AND ticket_id IN ({slots})',
        'outside':f"SELECT ticket_id,unit_ids FROM outside_moves WHERE tenant_id=? AND ticket_id IN ({slots}) AND kind='send'",
        'units':f'SELECT s.ticket_id,s.id,s.state,COALESCE(l.quantity,1) AS quantity,COALESCE(l.scrapped,0) AS scrapped,l.parent_id FROM serial_units s LEFT JOIN production_lots l ON l.tenant_id=s.tenant_id AND l.unit_id=s.id WHERE s.tenant_id=? AND s.ticket_id IN ({slots})',
    }
    result={tid:{key:[] for key in queries} for tid in ids}
    for key,sql in queries.items():
        for row in conn.execute(sql,params):result[row['ticket_id']][key].append(row)
    return result


def quantities(conn,tenant,ticket,delivery=None,data=None):
    tid=ticket['id'];delivery=delivery if delivery is not None else progress(conn,tenant,ticket)
    data=data if data is not None else load(conn,tenant,[tid])[tid]
    result=dict(ordered=ticket['quantity'],not_started=0,underway=0,inspection=0,held=0,accepted=delivery['available'],packed=delivery['waiting'],shipped=delivery['departed'])
    ops=data['ops'];done={}
    for r in data['done']:done.setdefault(r['unit_id'],set()).add(r['operation_id'])
    started={r['unit_id'] for r in data['sessions']}
    for r in data['outside']:
        ids=json.loads(r['unit_ids']);started.update(ids or [None])
    units=data['units']
    parents={u['id']:u['parent_id'] for u in units}
    def has_started(uid):
        if uid is None:return None in started
        while uid:
            if uid in started:return True
            uid=parents.get(uid)
        return False
    for u in units or [dict(id=None,state=ticket['state'],quantity=ticket['quantity'],scrapped=0)]:
        if u['scrapped'] or u['state']=='accepted':continue
        completed=done.get(u['id'],set()) if u['id'] else {o['id'] for o in ops if o['completed_by'] is not None}
        if u['state']=='held':bucket='held'
        elif ops and len(completed)==len(ops):bucket='inspection'
        elif completed or has_started(u['id']) or u['state']=='in_progress':bucket='underway'
        else:bucket='not_started'
        result[bucket]+=u['quantity']
    result['consistent']=all(v>=0 for k,v in result.items() if k!='ordered') and sum(v for k,v in result.items() if k!='ordered')==result['ordered']
    return result
