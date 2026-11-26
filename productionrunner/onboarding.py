"""Resumable organization setup and single-use employee invitations."""
import hashlib
import json
import re
import secrets
import sqlite3
from flask import abort, g, render_template, request, redirect, url_for, session
from werkzeug.security import generate_password_hash
from .tenancy import ROLES
from .intake import actor
from .pin_auth import audit

STEPS = (
 ('profile','Your company','Confirm your company name and contact details.','admin_organization',False),
 ('brand','Make it yours','Add your logo and shop colors.','admin_branding',True),
 ('stations','Your shop floor','Review stations or apply the sheet-metal starter.','shop_setup',False),
 ('materials','Approved materials','Review metals, gauges and finishes.','materials_admin',False),
 ('vendors','Outside partners','Add vendors if your work leaves the building.','vendors_admin',True),
 ('team','Bring your team','Invite people and choose their roles.','invitations',True),
 ('parts','Parts and drawings','Add parts, drawings and production files.','import_parts',True),
 ('request','Your first request','Have a planner create and review a production request.','index',False),
)

def register(app,db,roles_required):
    def record(action,detail,user=None):
        audit(db(),g.tenant['id'],user or g.user['id'],action,f"user:{g.user['id']}",app.config['AUTH_CLOCK'](),json.dumps(detail))

    @app.route('/admin/getting-started',methods=['GET','POST'])
    @roles_required('admin')
    def onboarding():
        if request.method=='POST':
            step=request.form.get('step');status=request.form.get('status')
            definition=next((s for s in STEPS if s[0]==step),None)
            if not definition or status not in {'reviewed','later','todo'} or (status=='later' and not definition[4]):
                abort(400,'Choose a valid setup step and status.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                db().execute('INSERT INTO onboarding_steps(tenant_id,step,status) VALUES(?,?,?) ON CONFLICT(tenant_id,step) DO UPDATE SET status=excluded.status',(g.tenant['id'],step,status))
                record('onboarding_step_updated',dict(step=step,status=status))
            return redirect(url_for('onboarding'))
        states={r['step']:r['status'] for r in db().execute('SELECT * FROM onboarding_steps WHERE tenant_id=?',(g.tenant['id'],))}
        return render_template('onboarding.html',steps=STEPS,states=states,completed=sum(v=='reviewed' for v in states.values()))

    @app.route('/admin/invitations',methods=['GET','POST'])
    @roles_required('admin')
    def invitations():
        link=None
        if request.method=='POST':
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                if request.form.get('action')=='revoke':
                    found=db().execute('SELECT * FROM employee_invitations WHERE id=? AND tenant_id=?',(request.form.get('invitation_id'),g.tenant['id'])).fetchone()
                    if not found:abort(404)
                    if found['status']!='pending':abort(409,'This invitation has already been used or revoked.')
                    db().execute("UPDATE employee_invitations SET status='revoked' WHERE id=?",(found['id'],))
                    record('invitation_revoked',dict(invitation_id=found['id']))
                else:
                    username=request.form.get('username','').strip();roles=set(request.form.getlist('roles'))
                    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._@-]{0,79}',username):abort(400,'Enter a valid username of up to 80 characters.')
                    if not roles or not roles<=ROLES or ('display' in roles and len(roles)>1):abort(400,'Choose valid roles; display must be used alone.')
                    if db().execute('SELECT 1 FROM users WHERE username=?',(username,)).fetchone():abort(409,'Username unavailable. Existing accounts must be managed through People & access.')
                    now=app.config['AUTH_CLOCK']()
                    if db().execute("SELECT 1 FROM employee_invitations WHERE username=? AND status='pending' AND expires_at>?",(username,now)).fetchone():abort(409,'An invitation for that username is pending. Revoke it before replacing it.')
                    token=secrets.token_urlsafe(32)
                    cur=db().execute('INSERT INTO employee_invitations(tenant_id,username,roles,token_hash,expires_at,created_by) VALUES(?,?,?,?,?,?)',(g.tenant['id'],username,','.join(sorted(roles)),hashlib.sha256(token.encode()).hexdigest(),now+7*86400,g.user['id']))
                    record('invitation_created',dict(invitation_id=cur.lastrowid,username=username,roles=sorted(roles)))
                    # The secret is returned once, never stored in the database or audit history.
                    link=(app.config['PR_PUBLIC_ORIGIN']+url_for('activate_invitation') if app.config.get('PR_PUBLIC_ORIGIN') else url_for('activate_invitation',_external=True))+'#'+token
        entries=db().execute('SELECT * FROM employee_invitations WHERE tenant_id=? ORDER BY id DESC',(g.tenant['id'],)).fetchall()
        return render_template('invitations.html',entries=entries,roles=sorted(ROLES),link=link,clock=app.config['AUTH_CLOCK']())

    @app.route('/join',methods=['GET','POST'])
    def activate_invitation():
        # Fragment tokens are submitted in the body, keeping them out of access logs/referrers.
        if request.method=='GET':return render_template('activate_invitation.html',invitation=None)
        token=request.form.get('token','')
        digest=hashlib.sha256(token.encode()).hexdigest()
        with db():
            db().execute('BEGIN IMMEDIATE')
            invite=db().execute("SELECT i.*,t.name,t.slug FROM employee_invitations i JOIN tenants t ON t.id=i.tenant_id WHERE token_hash=? AND status='pending' AND expires_at>?",(digest,app.config['AUTH_CLOCK']())).fetchone()
            if not invite:abort(410,'This invitation is invalid, expired or already used. Ask your administrator for a new invitation.')
            if request.form.get('action')!='activate':return render_template('activate_invitation.html',invitation=invite,token=token)
            password=request.form.get('password','');pin=request.form.get('pin','')
            if not 12<=len(password)<=256 or password!=request.form.get('password_confirm'):abort(400,'Passwords must match and contain 12–256 characters.')
            pin_allowed=bool(set(invite['roles'].split(',')) & {'operator','inspector'})
            if pin and (not pin_allowed or not re.fullmatch(r'[0-9]{6,12}',pin) or pin!=request.form.get('pin_confirm')):abort(400,'PINs must match and contain 6–12 digits; only production roles can enroll here.')
            try:cur=db().execute('INSERT INTO users(username,password_hash) VALUES(?,?)',(invite['username'],generate_password_hash(password)))
            except sqlite3.IntegrityError:abort(409,'That username is no longer available. Ask your administrator for a new invitation.')
            uid=cur.lastrowid
            db().execute('INSERT INTO memberships VALUES(?,?,?)',(invite['tenant_id'],uid,invite['roles']))
            if pin:db().execute('INSERT INTO pin_credentials(tenant_id,user_id,pin_hash,version) VALUES(?,?,?,1)',(invite['tenant_id'],uid,generate_password_hash(pin)))
            db().execute("UPDATE employee_invitations SET status='accepted' WHERE id=?",(invite['id'],))
            audit(db(),invite['tenant_id'],uid,'invitation_accepted',f'user:{uid}',app.config['AUTH_CLOCK'](),json.dumps(dict(invitation_id=invite['id'],roles=invite['roles'],pin_enrolled=bool(pin))))
        session.clear();session['csrf']=secrets.token_hex(32)
        return render_template('activation_complete.html',invitation=invite)
