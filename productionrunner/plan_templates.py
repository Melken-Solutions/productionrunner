"""Approved, immutable manufacturing defaults for one organization and part revision."""
import json
from flask import abort, g, jsonify, redirect, render_template, request, url_for
from . import request_job_plan
from .intake import actor, get_request, log
from .materials import request_material, validate_request as validate_material
from .request_outside import get_plan
from .vendors import validate_request as validate_vendor, validate as validate_vendor_values


def latest(conn, tenant, part):
    row=conn.execute('SELECT * FROM part_plan_templates WHERE tenant_id=? AND part_id=? ORDER BY revision DESC LIMIT 1',(tenant,part)).fetchone()
    return dict(row,content=json.loads(row['data'])) if row else None


def check_source(conn, tenant, rid, plan, previous=None):
    """Check client-supplied provenance; it never confers approval on the new job."""
    ident=plan.get('template_id') if plan else None
    if not ident:return
    row=conn.execute('SELECT t.*,p.pn,p.revision AS part_revision FROM part_plan_templates t JOIN part_revisions p ON p.tenant_id=t.tenant_id AND p.id=t.part_id WHERE t.tenant_id=? AND t.id=?',(tenant,ident)).fetchone()
    req=get_request(conn,tenant,rid)
    if not row or row['pn']!=req['pn'] or row['part_revision']!=req['revision']:
        abort(400,'The selected template does not match this organization, part number and revision.')
    if not previous or previous.get('template_id')!=ident:
        current=latest(conn,tenant,row['part_id'])
        if current['id']!=ident or current['status']!='approved':
            abort(409,'The part template changed or was withdrawn. Reload the latest template and review this request.')
        problem=availability(conn,tenant,current)
        if problem:abort(409,problem)


def availability(conn, tenant, template):
    content=template['content']
    plan=content['plan']
    for step in plan['steps']+[plan[k] for k in request_job_plan.STAGES]:
        if not step.get('outside') and not conn.execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1',(tenant,step['station_id'])).fetchone():
            return 'A template station is inactive. Review and publish an updated template.'
    material=content['material']
    if material:
        row=conn.execute('SELECT active,version FROM materials WHERE tenant_id=? AND id=?',(tenant,material['material_id'])).fetchone()
        if not row or not row['active'] or row['version']!=material['material_version']:
            return 'The template material changed or is inactive. Review and publish an updated template.'
    elif conn.execute('SELECT 1 FROM materials WHERE tenant_id=?',(tenant,)).fetchone():
        return 'Choose a material and publish an updated template.'
    outside=content['outside']
    if outside:
        validate_vendor_values(conn,tenant,outside['vendor'],outside['service'])
    return None


def register(app, db, roles_required, now):
    @app.context_processor
    def context():
        return dict(part_plan_template=lambda part:latest(db(),g.tenant['id'],part), template_origin=lambda ident:db().execute('SELECT part_id,revision FROM part_plan_templates WHERE tenant_id=? AND id=?',(g.tenant['id'],ident)).fetchone())

    def part(part_id):
        row=db().execute('SELECT * FROM part_revisions WHERE tenant_id=? AND id=?',(g.tenant['id'],part_id)).fetchone()
        if not row:abort(404)
        return row

    @app.get('/parts/<part_id>/plan-template')
    @roles_required('planner','admin')
    def part_plan_template_data(part_id):
        part(part_id)
        with db():
            db().execute('BEGIN')
            template=latest(db(),g.tenant['id'],part_id)
            if not template or template['status']!='approved':
                return jsonify(template=None)
            problem=availability(db(),g.tenant['id'],template)
            if problem:return jsonify(error=problem),409
            return jsonify(template=dict(id=template['id'],revision=template['revision'],**template['content']))

    @app.get('/parts/<part_id>/plan-templates')
    @roles_required('planner','admin')
    def part_plan_templates(part_id):
        selected=part(part_id)
        history=db().execute('SELECT t.*,u.username FROM part_plan_templates t JOIN users u ON u.id=t.actor_id WHERE t.tenant_id=? AND t.part_id=? ORDER BY t.revision DESC',(g.tenant['id'],part_id)).fetchall()
        current=latest(db(),g.tenant['id'],part_id)
        return render_template('plan_templates.html',part=selected,history=history,current=current)

    @app.post('/requests/<rid>/plan-template')
    @roles_required('planner','admin')
    def publish_plan_template(rid):
        why=request.form.get('reason','').strip()
        if not 1<=len(why)<=500 or request.form.get('confirm')!='yes':
            abort(400,'Review the manufacturing defaults and record an approval reason.')
        with db():
            db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'])
            req=get_request(db(),g.tenant['id'],rid)
            if req['status'] not in ('approved','ticketed') or str(req['version'])!=request.form.get('version'):
                abort(409,'Approve the request first, then reload it before publishing its plan.')
            plan=request_job_plan.validate(db(),g.tenant['id'],rid)
            if not plan:abort(409,'This request does not have a complete Job Plan.')
            material=validate_material(db(),g.tenant['id'],rid)
            validate_vendor(db(),g.tenant['id'],rid)
            previous=latest(db(),g.tenant['id'],req['part_id'])
            if request.form.get('template_version','0')!=str(previous['revision'] if previous else 0):
                abort(409,'Another user updated this part template. Reload and review the latest revision.')
            plan=dict(plan);plan.pop('template_id',None)
            content=dict(plan=plan,material={k:material[k] for k in ('material_id','material_version','spec','notes')} if material else None,
                         outside={k:v for k,v in (get_plan(db(),g.tenant['id'],rid) or {}).items() if k not in ('due_date','reference')} or None)
            rev=previous['revision']+1 if previous else 1
            cur=db().execute("INSERT INTO part_plan_templates(tenant_id,part_id,revision,status,data,source_request_id,actor_id,reason,created_at) VALUES(?,?,?,'approved',?,?,?,?,?)",
                             (g.tenant['id'],req['part_id'],rev,json.dumps(content),rid,g.user['id'],why,now()))
            log(db(),g.tenant['id'],g.user['id'],rid,'part_plan_template_approved',dict(template_id=cur.lastrowid,revision=rev,reason=why),now())
        return redirect(url_for('part_plan_templates',part_id=req['part_id']))

    @app.post('/parts/<part_id>/plan-template/withdraw')
    @roles_required('planner','admin')
    def withdraw_plan_template(part_id):
        why=request.form.get('reason','').strip()
        if not 1<=len(why)<=500:abort(400,'Record a withdrawal reason.')
        with db():
            db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id']);part(part_id)
            old=latest(db(),g.tenant['id'],part_id)
            if not old or old['status']!='approved' or str(old['revision'])!=request.form.get('version'):
                abort(409,'Template changed. Reload before withdrawing.')
            db().execute("INSERT INTO part_plan_templates(tenant_id,part_id,revision,status,data,source_request_id,actor_id,reason,created_at) VALUES(?,?,?,'withdrawn',?,?,?,?,?)",
                         (g.tenant['id'],part_id,old['revision']+1,old['data'],old['source_request_id'],g.user['id'],why,now()))
        return redirect(url_for('part_plan_templates',part_id=part_id))
