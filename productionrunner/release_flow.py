"""One human review, followed by the existing guarded approval/release actions."""
from flask import g,render_template,request,url_for,jsonify,abort
from werkzeug.exceptions import HTTPException
from .intake import get_request,document_state
from . import request_job_plan,materials,vendors,delivery_schedule,custom_fields,erp
from .workflow_defaults import settings as workflow_settings


def register(app,db,roles_required):
    @app.get('/requests/<rid>/release-state')
    @roles_required('planner')
    def release_state(rid):
        row=get_request(db(),g.tenant['id'],rid)
        ticket=db().execute('SELECT id,version FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],row['ticket_id'])).fetchone() if row['ticket_id'] else None
        details=erp.job_details(db(),g.tenant['id'],ticket['id']) if ticket else None
        return jsonify(status=row['status'],version=row['version'],erp_config_version=erp.settings(db(),g.tenant['id'])['version'],ticket=(dict(ticket,erp_config_version=details['config_version'] if details else None) if ticket else None))

    @app.get('/requests/<rid>/release-review')
    @roles_required('planner')
    def release_review(rid):
        row=get_request(db(),g.tenant['id'],rid)
        if row['status'] not in {'preparing','approved'}:
            abort(409,'This request already has a ticket or is cancelled. Open its job to review the current release status.')
        tenant=g.tenant['id'];issues=[]
        plan=request_job_plan.get(db(),tenant,rid)
        docs=document_state(db(),tenant,row)
        def check(label,fn):
            try:fn()
            except HTTPException as error:issues.append(dict(label=label,message=error.description))
        if not plan:issues.append(dict(label='Job Plan',message='Create a complete Job Plan before using guided release.'))
        check('Job Plan',lambda:request_job_plan.validate(db(),tenant,rid))
        check('Material',lambda:materials.validate_request(db(),tenant,rid))
        check('Outside vendor',lambda:vendors.validate_request(db(),tenant,rid))
        check('Delivery schedule',lambda:delivery_schedule.validate_request(db(),tenant,row))
        check('Shop details',lambda:custom_fields.validate(db(),tenant,rid))
        check('ERP references',lambda:erp.validate(db(),tenant,row,'release'))
        for problem in docs['missing']:issues.append(dict(label='Documents',message=problem))
        if not row['requester'] or not row['destination']:issues.append(dict(label='Order details',message='Enter the customer and destination.'))
        from .request_outside import get_plan
        outside=get_plan(db(),tenant,rid)
        if outside and not outside['due_date']:issues.append(dict(label='Outside return',message='Enter the new vendor return date.'))
        part=docs['part']
        if part:
            from .catalog import active
            check('Catalog revision',lambda:active(db(),tenant,part['id']))
        from .plan_templates import latest
        template=latest(db(),tenant,part['id']) if part else None
        details=erp.saved(db(),tenant,rid);cfg=erp.settings(db(),tenant)
        path=next((p for p in cfg['paths'] if p['id']==details['path_id']),None)
        workflow=workflow_settings(db(),tenant)
        payload=dict(erp_config_version=cfg['version'],rid=rid,version=row['version'],status=row['status'],pn=row['pn'],revision=row['revision'],workflow_version=workflow['version'],template_version=template['revision'] if template else 0,erp=details,erp_checks=path['checks'] if path else [],has_erp=bool(path))
        return render_template('release_review.html',entry=row,docs=docs,issues=issues,plan=plan,workflow=workflow,path=path,payload=payload,template=template)
