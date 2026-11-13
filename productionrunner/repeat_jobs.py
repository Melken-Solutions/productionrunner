"""Repeat manufacturing intent without copying execution or approval evidence."""
import hashlib
import json
import secrets
from datetime import date

from flask import abort, g, redirect, render_template, request, url_for
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from .intake import actor, log, EDIT_FIELDS
from . import request_job_plan, materials, request_outside, erp


def snapshot(conn, tenant, tid):
    ticket=conn.execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(tenant,tid)).fetchone()
    if not ticket:abort(404,'Unknown source job.')
    from .job_plan import current
    from .parts import matching_part
    source=conn.execute('SELECT * FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(tenant,tid)).fetchone()
    saved=current(conn,tenant,tid)
    cfg=saved['data'] if saved else {}
    warnings=[]
    if not saved:warnings.append('This job has no controlled Job Plan. Complete the copied route checks and final stages.')
    outside=list(conn.execute('SELECT * FROM outside_steps WHERE tenant_id=? AND ticket_id=?',(tenant,tid)))
    if len(outside)>1:abort(409,'This job has multiple outside steps. Build a new request and review its vendor routing instead.')
    outside_ids={o['operation_id'] for o in outside}
    plan=dict(steps=[],dxf_required=bool(cfg.get('dxf_required')))
    def station(sid):
        if sid and conn.execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1',(tenant,sid)).fetchone():return sid
        if sid:warnings.append('A previous station is inactive. Choose its replacement in the new Job Plan.')
        return 0
    for op in conn.execute('SELECT o.*,a.station_id FROM operations o LEFT JOIN operation_assignments a ON a.tenant_id=o.tenant_id AND a.operation_id=o.id WHERE o.tenant_id=? AND o.ticket_id=? ORDER BY o.sequence',(tenant,tid)):
        checks=cfg.get('operations',{}).get(str(op['id']),{})
        instructions=op['instructions']
        if len(instructions)>350:
            instructions='';warnings.append('A legacy operation has long instructions. Review the source job and enter concise instructions.')
        plan['steps'].append(dict(name=op['name'],instructions=instructions,criteria=checks.get('criteria',''),first_piece=bool(checks.get('first_piece')),outside=op['id'] in outside_ids,station_id=0 if op['id'] in outside_ids else station(op['station_id'])))
    for stage in request_job_plan.STAGES:
        value=cfg.get(stage,{})
        plan[stage]=dict(station_id=station(value.get('station_id')),criteria=value.get('criteria',''))
    material=materials.ticket_material(conn,tenant,tid)
    if material:
        live=conn.execute('SELECT active,version FROM materials WHERE tenant_id=? AND id=?',(tenant,material['material_id'])).fetchone()
        if not live or not live['active']:
            material=None;warnings.append('The previous material is inactive. Select an active material.')
        else:
            if live['version']!=material['material_version']:warnings.append('The material specification changed. Review and save the current specification before approval.')
            material=dict(material,planned_sheets='')
    vendor={k:outside[0][k] for k in request_outside.FIELDS} if outside else None
    if vendor:
        vendor.update(due_date='',reference='')
        warnings.append('Enter a new outside return date and review the vendor/service before approval.')
    part=matching_part(conn,tenant,ticket['pn'],ticket['revision'])
    if not part:warnings.append('The original PN/revision is no longer in the catalog. Choose the correct drawing revision.')
    elif conn.execute('SELECT 1 FROM part_catalog_controls WHERE tenant_id=? AND part_id=? AND archived=1',(tenant,part['id'])).fetchone():warnings.append('This drawing revision is archived. Choose an active revision before approval.')
    else:
        if conn.execute('SELECT 1 FROM part_revisions WHERE tenant_id=? AND pn=? AND revision<>?',(tenant,ticket['pn'],ticket['revision'])).fetchone():warnings.append('Other drawing revisions exist. Confirm that this is the correct revision for the new order.')
        from .plan_templates import latest
        template=latest(conn,tenant,part['id'])
        if template and template['id']!=cfg.get('template_id'):warnings.append('A different saved part-plan version exists. This draft copies the selected job; compare the current template before release.')
    if source and part:
        approved=conn.execute("SELECT detail FROM intake_events WHERE tenant_id=? AND request_id=? AND action='documents_approved' ORDER BY id DESC LIMIT 1",(tenant,source['id'])).fetchone()
        if approved:
            old=json.loads(approved['detail'])
            dxf=conn.execute('SELECT sha256 FROM part_dxf WHERE tenant_id=? AND part_revision_id=?',(tenant,part['id'])).fetchone()
            if old.get('pdf_sha256')!=part['sha256'] or old.get('drawing_url')!=part['drawing_url'] or old.get('dxf_sha256')!=(dxf['sha256'] if dxf else None):
                warnings.append('The drawing/DXF package differs from the source approval. Review the current files before approving this draft.')
    # No approved-template provenance is inherited: this is a one-off job copy.
    defaults=dict(item_type=source['item_type'] if source else 'standard',source=source['source'] if source else 'manual',source_reference='',requester=source['requester'] if source else '',destination=source['destination'] if source else '',required_date='',pn=ticket['pn'],revision=ticket['revision'],quantity=ticket['quantity'],description=source['description'] if source else ticket['instructions'] or ticket['pn'])
    result=dict(ticket=dict(ticket),defaults=defaults,plan=plan,material=material,outside=vendor,warnings=list(dict.fromkeys(warnings)))
    result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result


def register(app,db,roles_required,now):
    signer=URLSafeTimedSerializer(app.secret_key,salt='repeat-job')

    @app.context_processor
    def context():
        def origin(rid):
            row=db().execute("SELECT detail FROM intake_events WHERE tenant_id=? AND request_id=? AND action='repeated_from_job' ORDER BY id LIMIT 1",(g.tenant['id'],rid)).fetchone()
            return json.loads(row['detail']) if row else None
        return dict(repeat_origin=origin)

    @app.route('/tickets/<ticket_id>/repeat',methods=['GET','POST'])
    @roles_required('planner','admin')
    def repeat_job(ticket_id):
        if request.method=='GET':
            data=snapshot(db(),g.tenant['id'],ticket_id)
            token=signer.dumps(dict(tenant=g.tenant['id'],user=g.user['id'],ticket=ticket_id,fingerprint=data['fingerprint'],nonce=secrets.token_hex(16)))
            return render_template('repeat_job.html',**data,token=token,entry=None,previous_erp=erp.job_details(db(),g.tenant['id'],ticket_id))
        try:token=signer.loads(request.form.get('repeat_token',''),max_age=3600)
        except (BadSignature,SignatureExpired):abort(400,'Repeat-job review expired. Open the source job and try again.')
        if (token.get('tenant'),token.get('user'),token.get('ticket'))!=(g.tenant['id'],g.user['id'],ticket_id):abort(403)
        try:
            quantity=int(request.form.get('quantity',''))
            if not 1<=quantity<=10000:raise ValueError
            day=request.form.get('required_date','')
            if day and date.fromisoformat(day).isoformat()!=day:raise ValueError
        except ValueError:abort(400,'Enter a quantity from 1–10000 and a valid delivery date.')
        if request.form.get('confirm')!='yes':abort(400,'Confirm review of the source plan and new order details.')
        with db():
            db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'])
            # Repeated clicks on the same review create only one draft.
            prior=db().execute("SELECT request_id FROM intake_events WHERE tenant_id=? AND actor_id=? AND action='repeated_from_job' AND detail LIKE ?",(g.tenant['id'],g.user['id'],'%"repeat_nonce": "'+token['nonce']+'"%')).fetchone()
            if prior:return redirect(url_for('intake_edit',rid=prior['request_id']))
            data=snapshot(db(),g.tenant['id'],ticket_id)
            if data['fingerprint']!=token['fingerprint']:abort(409,'The source job or its catalog choices changed. Review it again before repeating.')
            rid='REQ-'+secrets.token_hex(6).upper()
            fields=dict(data['defaults'],quantity=quantity,required_date=day)
            erp_data=erp.collect(request.form)
            if erp_data.get('path_id'):
                cfg=erp.settings(db(),g.tenant['id'])
                chosen=next((p for p in cfg['paths'] if p['id']==erp_data['path_id']),None)
                if chosen:fields['source']=chosen['source']
                erp.path_for(cfg,erp_data,fields['source'])
            db().execute('INSERT INTO intake_requests(id,tenant_id,'+','.join(EDIT_FIELDS)+',created_by,created_at) VALUES('+','.join('?' for _ in range(len(EDIT_FIELDS)+4))+')',(rid,g.tenant['id'],*(fields[k] for k in EDIT_FIELDS),g.user['id'],now()))
            materials.save_request(db(),g.tenant['id'],rid,data['material'])
            if data['outside']:
                # An incomplete draft retains instructions, but never a prior order's dates/references.
                db().execute('INSERT INTO request_outside VALUES(?,?,?,?,?,?,?,?)',(g.tenant['id'],rid,*(data['outside'][k] for k in request_outside.FIELDS)))
            request_job_plan.save(db(),g.tenant['id'],rid,data['plan'])
            erp.save(db(),g.tenant['id'],g.user['id'],rid,erp_data,now())
            log(db(),g.tenant['id'],g.user['id'],rid,'repeated_from_job',dict(ticket_id=ticket_id,source_version=data['ticket']['version'],repeat_nonce=token['nonce'],warnings=data['warnings']),now())
        return redirect(url_for('intake_edit',rid=rid))
