"""Audited per-login TV presentation settings; display accounts remain read-only."""
import json
from flask import g,request,abort,render_template,redirect,url_for
DEFAULTS=dict(orientation='auto',layout='two',text_size='standard',cards=4,seconds=20,show_operator=False,show_start=False)


def settings(conn,tenant,user):
    row=conn.execute("SELECT id,detail FROM auth_events WHERE tenant_id=? AND user_id=? AND action='tv_settings_saved' ORDER BY id DESC LIMIT 1",(tenant,user)).fetchone()
    return dict(DEFAULTS,**json.loads(row['detail'])['settings'],version=row['id']) if row else dict(DEFAULTS,version=0)


def parse(form):
    data={k:form.get(k,DEFAULTS[k]) for k in ('orientation','layout','text_size')}
    if data['orientation'] not in {'auto','portrait','landscape'} or data['layout'] not in {'single','two','priority'} or data['text_size'] not in {'large','standard','compact'}:abort(400,'Choose valid TV layout presets.')
    try:data.update(cards=int(form.get('cards',4)),seconds=int(form.get('seconds',20)))
    except (ValueError,TypeError):abort(400,'Enter whole numbers for cards and page duration.')
    if not 1<=data['cards']<=10 or not 10<=data['seconds']<=60:abort(400,'Choose 1–10 cards per column and 10–60 seconds per page.')
    data.update({k:form.get(k)=='yes' for k in ('show_operator','show_start')})
    return data


def selected_user(conn):
    value=request.values.get('screen',str(g.user['id']))
    try:user=int(value)
    except ValueError:abort(400,'Choose a TV login.')
    if user!=g.user['id'] and 'admin' not in g.roles:abort(403,'Only admins may preview another screen.')
    member=conn.execute('SELECT u.id,u.username,m.roles FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=? AND m.user_id=?',(g.tenant['id'],user)).fetchone()
    from .admin import deleted_member
    if not member or not member['roles'] or deleted_member(conn,g.tenant['id'],user):abort(404,'Active login unavailable.')
    if user!=g.user['id'] and member['roles']!='display':abort(400,'Choose a dedicated display login.')
    return member


def board_settings(conn):
    user=selected_user(conn)
    if request.args.get('preview')=='yes':
        if 'admin' not in g.roles:abort(403,'Only admins may preview settings.')
        return dict(parse(request.args),version=-1)
    return settings(conn,g.tenant['id'],user['id'])


def register(app,db,roles_required,now):
    @app.route('/admin/tv-settings',methods=['GET','POST'])
    @roles_required('admin')
    def tv_settings_admin():
        member=selected_user(db())
        if request.method=='POST':
            data=parse(request.form)
            with db():
                db().execute('BEGIN IMMEDIATE')
                from .intake import actor
                actor(db(),g.tenant['id'],g.user['id'],('admin',))
                member=selected_user(db())
                current=settings(db(),g.tenant['id'],member['id'])
                if request.form.get('version')!=str(current['version']):abort(409,'Screen settings changed. Reload before saving.')
                db().execute("INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,'tv_settings_saved',?,?,?)",(g.tenant['id'],member['id'],'user:'+str(g.user['id']),json.dumps(dict(settings=data,before=current)),now()))
            return redirect(url_for('tv_settings_admin',screen=member['id'],saved='yes'))
        screens=db().execute("SELECT u.id,u.username FROM memberships m JOIN users u ON u.id=m.user_id WHERE m.tenant_id=? AND m.roles='display' ORDER BY u.username",(g.tenant['id'],)).fetchall()
        return render_template('tv_settings.html',member=member,settings=settings(db(),g.tenant['id'],member['id']),screens=screens)
