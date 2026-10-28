"""Outside processing planned at intake and copied into a chosen routing step."""
import json
from datetime import date
from flask import abort

FIELDS=('vendor','service','instructions','inspection_requirements','due_date','reference')


def get_plan(conn,tenant,rid):
    row=conn.execute('SELECT * FROM request_outside WHERE tenant_id=? AND request_id=?',(tenant,rid)).fetchone()
    return {k:row[k] for k in FIELDS} if row else None


def parse(form):
    if form.get('outside_required')!='yes':return None
    plan={k:form.get('outside_'+k,'').strip() for k in FIELDS}
    for k in FIELDS:
        limit=4000 if k in {'instructions','inspection_requirements'} else 200
        if len(plan[k])>limit or (k!='reference' and not plan[k]):abort(400,'Complete the outside processing vendor, service, instructions, inspection requirements and expected return date.')
    try:plan['due_date']=date.fromisoformat(plan['due_date']).isoformat()
    except ValueError:abort(400,'Choose a valid outside processing return date.')
    return plan


def save(conn,tenant,rid,plan):
    conn.execute('DELETE FROM request_outside WHERE tenant_id=? AND request_id=?',(tenant,rid))
    if plan:conn.execute('INSERT INTO request_outside VALUES(?,?,?,?,?,?,?,?)',(tenant,rid,*(plan[k] for k in FIELDS)))


def apply(conn,tenant,rid,tid,sequence,event):
    plan=get_plan(conn,tenant,rid)
    if not plan:return
    try:seq=int(sequence)
    except (ValueError,TypeError):abort(400,'Choose the routing step number for the requested outside processing.')
    op=conn.execute('SELECT id FROM operations WHERE tenant_id=? AND ticket_id=? AND sequence=?',(tenant,tid,seq)).fetchone()
    if not op:abort(400,'The outside processing step must be a step in this JobRunner route.')
    conn.execute('INSERT INTO outside_steps VALUES(?,?,?,?,?,?,?,?,?)',(tenant,tid,op['id'],*(plan[k] for k in FIELDS)))
    event(tid,'outside_plan_from_request',json.dumps(dict(plan,request_id=rid,operation_id=op['id'])))
