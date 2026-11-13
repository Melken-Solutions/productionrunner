"""Organization-owned ERP requirements, references and manual clearance evidence."""
import csv,io,json,secrets
from flask import abort,g,request,render_template,redirect,url_for,Response

FIELDS={'production_order':'Production order number','sales_order':'Sales order number','sales_line':'Sales order line item','customer_po':'Customer PO','support_reference':'Supporting sheet / row reference'}
SOURCES=('manual','po','erp','email','smartsheet','other')


def settings(conn,tenant):
    row=conn.execute("SELECT id,detail FROM auth_events WHERE tenant_id=? AND action='erp_settings_saved' ORDER BY id DESC LIMIT 1",(tenant,)).fetchone()
    return dict(json.loads(row['detail'])['settings'],version=row['id']) if row else dict(version=0,name='ERP',required_sources=[],paths=[])


def saved(conn,tenant,rid):
    row=conn.execute("SELECT detail FROM intake_events WHERE tenant_id=? AND request_id=? AND action='erp_details_saved' ORDER BY id DESC LIMIT 1",(tenant,rid)).fetchone()
    return json.loads(row['detail']) if row else dict(path_id='',values={})


def collect(form,previous=None):
    if form.get('erp_present')!='yes':return previous or dict(path_id='',values={})
    result=dict(path_id=form.get('erp_path',''),values={k:form.get('erp_'+k,'').strip() for k in FIELDS})
    if any(len(v)>200 or any(ord(c)<32 for c in v) for v in result['values'].values()):abort(400,'ERP references must be at most 200 characters without control characters.')
    return result


def path_for(config,details,source):
    path=next((p for p in config['paths'] if p['id']==details.get('path_id')),None)
    if details.get('path_id') and (not path or not path['active']):abort(409,'Select an active ERP request path.')
    if path and path['source']!=source:abort(400,'Request origin must match the selected ERP path.')
    return path


def validate(conn,tenant,row,stage='approval'):
    cfg=settings(conn,tenant);d=saved(conn,tenant,row['id']);p=path_for(cfg,d,row['source'])
    if not p and row['source'] in cfg['required_sources']:abort(409,'Choose an ERP request path before approval. Drafts can remain incomplete.')
    if p:
        missing=[v['label'] for k,v in p['fields'].items() if v['required']==stage or stage=='release' and v['required']=='approval' if not d['values'].get(k)]
        if missing:abort(409,'Missing ERP references: '+', '.join(missing))
    return dict(erp_name=cfg['name'],config_version=cfg['version'],path=p,values=d['values']) if p else None


def save(conn,tenant,user,rid,data,stamp):
    conn.execute("INSERT INTO intake_events(tenant_id,request_id,actor_id,action,detail,created_at) VALUES(?,?,?,'erp_details_saved',?,?)",(tenant,rid,user,json.dumps(data),stamp))


def snapshot(conn,tenant,user,req,tid,stamp):
    data=validate(conn,tenant,req)
    if data:conn.execute("INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,'erp_job_details',?,?)",(tenant,tid,user,json.dumps(data),stamp))


def job_details(conn,tenant,tid):
    row=conn.execute("SELECT e.*,u.username FROM events e JOIN users u ON u.id=e.actor_id WHERE e.tenant_id=? AND e.ticket_id=? AND e.action IN ('erp_job_details','erp_verified') ORDER BY e.id DESC LIMIT 1",(tenant,tid)).fetchone()
    return dict(json.loads(row['detail']),verified=row['action']=='erp_verified',verified_by=row['username'],verified_at=row['created_at']) if row else None


def require_release(conn,tenant,ticket):
    d=job_details(conn,tenant,ticket['id'])
    if not d:
        req=conn.execute('SELECT source FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone()
        source=req['source'] if req else 'manual'
        if source in settings(conn,tenant)['required_sources']:abort(409,'Open ERP references & clearance and select a request path before release.')
        return None
    missing=[v['label'] for k,v in d['path']['fields'].items() if v['required']!='optional' and not d['values'].get(k)]
    if missing:abort(409,'Complete ERP references before release: '+', '.join(missing))
    if not d['verified']:abort(409,'A planner must verify the configured ERP clear-to-build checks before release.')
    context=dict(pn=ticket['pn'],revision=ticket['revision'],quantity=ticket['quantity'])
    if d.get('item')!=context:abort(409,'The job changed after ERP clearance. Verify it again.')
    return d


def release_snapshot(conn,tenant,ticket,event,stamp):
    d=require_release(conn,tenant,ticket)
    if not d:return
    stage=d['path']['confirmation_stage']
    if stage!='none':
        if conn.execute('SELECT COUNT(*) FROM fulfillment_checks WHERE tenant_id=? AND ticket_id=?',(tenant,ticket['id'])).fetchone()[0]>=20:abort(409,'The job already has 20 delivery checkpoints. Review the workflow before adding an ERP gate.')
        cur=conn.execute('INSERT INTO fulfillment_checks(tenant_id,ticket_id,name,stage,actor_id,created_at) VALUES(?,?,?,?,?,?)',(tenant,ticket['id'],d['erp_name']+' confirmation: '+d['path']['confirmation_instructions'],stage,g.user['id'],stamp))
        d['confirmation_check_id']=cur.lastrowid
    event(ticket['id'],'erp_release_snapshot',json.dumps(d))


def duplicates(conn,tenant,values,exclude=None):
    number=values.get('production_order','').strip().casefold()
    if not number:return []
    matches=[]
    for r in conn.execute("SELECT id,pn,status,ticket_id FROM intake_requests WHERE tenant_id=? AND status!='cancelled'",(tenant,)):
        details=job_details(conn,tenant,r['ticket_id']) if r['ticket_id'] else None
        current_values=details['values'] if details else saved(conn,tenant,r['id'])['values']
        if r['id']!=exclude and current_values.get('production_order','').strip().casefold()==number:matches.append(dict(r))
    return matches


def register(app,db,roles_required,now,lock_ticket,event):
    from .intake import actor
    @app.context_processor
    def context():
        return dict(erp_settings=lambda:settings(db(),g.tenant['id']),erp_request=lambda rid:saved(db(),g.tenant['id'],rid),erp_job=lambda tid:job_details(db(),g.tenant['id'],tid),erp_fields=FIELDS)

    @app.route('/admin/erp',methods=['GET','POST'])
    @roles_required('admin')
    def erp_admin():
        cfg=settings(db(),g.tenant['id'])
        if request.method=='POST':
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',));cfg=settings(db(),g.tenant['id'])
                if request.form.get('version')!=str(cfg['version']):abort(409,'ERP settings changed. Reload before saving.')
                reason=request.form.get('reason','').strip()
                if not 1<=len(reason)<=500:abort(400,'Choose a reason for this configuration change.')
                if request.form.get('action')=='organization':
                    name=request.form.get('erp_name','').strip();sources=request.form.getlist('required_sources')
                    if not 1<=len(name)<=80 or not set(sources)<=set(SOURCES):abort(400,'Enter an ERP name and valid origins.')
                    cfg.update(name=name,required_sources=sources)
                elif request.form.get('action')=='path':
                    ident=request.form.get('path_id') or secrets.token_hex(6)
                    old=next((p for p in cfg['paths'] if p['id']==ident),None)
                    if request.form.get('path_id') and not old:abort(404)
                    if not old and len(cfg['paths'])>=20:abort(400,'At most 20 request paths are supported.')
                    p=dict(id=ident,name=request.form.get('name','').strip(),source=request.form.get('source'),active=request.form.get('active')=='1',fields={},checks=[s.strip() for s in request.form.get('checks','').splitlines() if s.strip()],confirmation_stage=request.form.get('confirmation_stage','none'),confirmation_instructions=request.form.get('confirmation_instructions','').strip(),operation_codes=request.form.get('operation_codes','').strip(),reason_codes=request.form.get('reason_codes','').strip())
                    if not 1<=len(p['name'])<=100 or p['source'] not in SOURCES or len(p['checks'])>20 or any(len(c)>300 for c in p['checks']) or p['confirmation_stage'] not in {'none','pack','depart'} or len(p['confirmation_instructions'])>200 or (p['confirmation_stage']!='none' and not p['confirmation_instructions']) or len(p['operation_codes'])>4000 or len(p['reason_codes'])>4000:abort(400,'Check path name, origin, clearance checks and confirmation settings.')
                    for key,label in FIELDS.items():
                        value=dict(label=request.form.get(key+'_label',label).strip(),required=request.form.get(key+'_required','optional'))
                        if not 1<=len(value['label'])<=100 or value['required'] not in {'optional','approval','release'}:abort(400,'Each reference needs a label and requirement stage.')
                        p['fields'][key]=value
                    cfg['paths']=[p if x['id']==ident else x for x in cfg['paths']]+([] if old else [p])
                else:abort(400,'Choose a settings action.')
                cfg.pop('version',None)
                db().execute("INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,'erp_settings_saved',?,?,?)",(g.tenant['id'],g.user['id'],'user:'+str(g.user['id']),json.dumps(dict(settings=cfg,reason=reason)),now()))
            return redirect(url_for('erp_admin'))
        return render_template('erp_admin.html',cfg=cfg,sources=SOURCES)

    @app.route('/tickets/<ticket_id>/erp',methods=['GET','POST'])
    @roles_required('planner','admin')
    def erp_review(ticket_id):
        ticket=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not ticket:abort(404)
        from .dispatch import released
        data=job_details(db(),g.tenant['id'],ticket_id);cfg=settings(db(),g.tenant['id'])
        if request.method=='POST':
            with db():
                ticket=lock_ticket(ticket_id);actor(db(),g.tenant['id'],g.user['id'])
                if released(db(),g.tenant['id'],ticket_id):abort(409,'Released ERP requirements are locked.')
                data=job_details(db(),g.tenant['id'],ticket_id)
                values=collect(request.form)['values']
                if not data:
                    cfg=settings(db(),g.tenant['id'])
                    if request.form.get('config_version')!=str(cfg['version']):abort(409,'Settings changed; reload the checklist.')
                    p=next((p for p in cfg['paths'] if p['id']==request.form.get('erp_path') and p['active']),None)
                    if not p:abort(400,'Choose an active request path.')
                    req=db().execute('SELECT source FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
                    if p['source']!=(req['source'] if req else 'manual'):abort(400,'Choose a path matching the job request origin.')
                    data=dict(erp_name=cfg['name'],config_version=cfg['version'],path=p)
                data['values']=values
                missing=[v['label'] for k,v in data['path']['fields'].items() if v['required']!='optional' and not values.get(k)]
                if missing:abort(400,'Complete required references: '+', '.join(missing))
                if request.form.get('erp_confirm')!='yes' or set(request.form.getlist('erp_checks'))!={str(i) for i in range(len(data['path']['checks']))}:abort(400,'Confirm each ERP clearance check and the manual verification statement.')
                why=request.form.get('reason','').strip()
                if not 1<=len(why)<=500:abort(400,'Choose a verification reason.')
                for key in ('verified','verified_by','verified_at'):data.pop(key,None)
                data.update(item=dict(pn=ticket['pn'],revision=ticket['revision'],quantity=ticket['quantity']),reason=why)
                event(ticket_id,'erp_verified',json.dumps(data))
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id))
            return redirect(url_for('erp_review',ticket_id=ticket_id))
        origin=db().execute('SELECT id,source FROM intake_requests WHERE tenant_id=? AND ticket_id=?',(g.tenant['id'],ticket_id)).fetchone()
        cfg=dict(cfg,paths=[p for p in cfg['paths'] if p['source']==(origin['source'] if origin else 'manual')])
        selected_path=next((p for p in cfg['paths'] if p['id']==request.args.get('path') and p['active']),None)
        return render_template('erp_review.html',selected_path=selected_path,ticket=ticket,data=data,cfg=cfg,locked=released(db(),g.tenant['id'],ticket_id),duplicate_requests=duplicates(db(),g.tenant['id'],data['values'] if data else {},origin['id'] if origin else None))

    @app.get('/tickets/<ticket_id>/erp/export.csv')
    @roles_required('planner','admin')
    def erp_export(ticket_id):
        t=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],ticket_id)).fetchone()
        if not t:abort(404)
        d=job_details(db(),g.tenant['id'],ticket_id)
        if not d:abort(404,'No ERP references recorded.')
        from .fulfillment import progress
        totals=progress(db(),g.tenant['id'],t)
        out=io.StringIO();w=csv.writer(out);w.writerow(['JobRunner','PN','Revision','Ordered','Accepted','Shipped','ERP','Path',*FIELDS.values(),'Verified by','Verified at'])
        values=[t['id'],t['pn'],t['revision'],t['quantity'],totals['accepted'],totals['departed'],d['erp_name'],d['path']['name'],*[d['values'].get(k,'') for k in FIELDS],d['verified_by'] if d['verified'] else '',d['verified_at'] if d['verified'] else '']
        w.writerow(["'"+str(v) if str(v).lstrip().startswith(('=','+','-','@')) else v for v in values])
        return Response(out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=erp-job-summary.csv'})
