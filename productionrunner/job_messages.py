"""In-app job conversations; communication never changes production state."""
from flask import abort,g,request,render_template,redirect,url_for,session,jsonify
from .intake import actor

from .message_snippets import CATEGORIES, available, selected
REPLIES=['Checking now','Please pause work and wait for guidance','Please review the updated drawing','Please contact me','Issue resolved at the station']
ROLES=('operator','planner','admin')


def threads(conn,tenant,user,roles,station=None):
    sql="""SELECT t.*,j.pn,j.revision,s.name AS station_name,u.username AS creator,
    (SELECT MAX(m.id) FROM job_messages m WHERE m.tenant_id=t.tenant_id AND m.thread_id=t.id) AS latest,
    COALESCE(r.last_message_id,0) AS seen
    FROM job_threads t JOIN tickets j ON j.tenant_id=t.tenant_id AND j.id=t.ticket_id
    JOIN users u ON u.id=t.created_by LEFT JOIN stations s ON s.tenant_id=t.tenant_id AND s.id=t.station_id
    LEFT JOIN job_message_reads r ON r.tenant_id=t.tenant_id AND r.thread_id=t.id AND r.user_id=?
    WHERE t.tenant_id=?"""
    args=[user,tenant]
    if not {'admin','planner'} & set(roles):
        sql+=' AND (t.created_by=? OR t.station_id=?)';args += [user,station]
    return conn.execute(sql+" ORDER BY CASE t.status WHEN 'new' THEN 0 WHEN 'acknowledged' THEN 1 ELSE 2 END,latest DESC",args).fetchall()


def register(app,db,roles_required,now):
    def station():
        value=session.get('shop_station_'+str(g.tenant['id']),'')
        return int(value) if str(value).isdigit() else None

    def inbox():return threads(db(),g.tenant['id'],g.user['id'],g.roles,station())

    def unread_count():
        if not g.tenant or not set(g.roles)&set(ROLES):return 0
        return sum(1 for row in inbox() if row['latest'] and row['latest']>row['seen'])

    @app.context_processor
    def message_context():return dict(job_message_count=unread_count)

    def ticket(tid):
        row=db().execute('SELECT * FROM tickets WHERE tenant_id=? AND id=?',(g.tenant['id'],tid)).fetchone()
        if not row:abort(404,'Job unavailable.')
        return row

    def seen(thread,last):
        db().execute('INSERT INTO job_message_reads(tenant_id,ticket_id,thread_id,user_id,last_message_id) VALUES(?,?,?,?,?) ON CONFLICT(tenant_id,thread_id,user_id) DO UPDATE SET last_message_id=excluded.last_message_id',(g.tenant['id'],thread['ticket_id'],thread['id'],g.user['id'],last))

    @app.get('/messages')
    @roles_required(*ROLES)
    def job_inbox():return render_template('job_inbox.html',threads=inbox())

    @app.get('/messages/unread')
    @roles_required(*ROLES)
    def job_unread():return jsonify(count=unread_count())

    @app.route('/tickets/<ticket_id>/messages/new',methods=['GET','POST'])
    @roles_required(*ROLES)
    def new_job_message(ticket_id):
        job=ticket(ticket_id)
        stations=db().execute('SELECT id,name FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall()
        unit=request.values.get('unit','');picked=request.values.get('station',str(station() or ''))
        if unit and not db().execute('SELECT 1 FROM serial_units WHERE tenant_id=? AND ticket_id=? AND id=?',(g.tenant['id'],ticket_id,unit)).fetchone():abort(404,'Unknown unit traveler.')
        if picked and picked not in {str(s['id']) for s in stations}:abort(400,'Choose an active station.')
        if request.method=='POST':
            category=request.form.get('category');note=request.form.get('note','').strip()
            if 'snippet_id' not in request.form and (category not in CATEGORIES or len(note)>1800 or (category=='Other' and not note)):abort(400,'Choose a request reason; Other requires a note.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],ROLES);ticket(ticket_id)
                if 'snippet_id' in request.form:category,body=selected(db(),g.tenant['id'],'request')
                else:body=category+(': '+note if note else '')
                row=db().execute('INSERT INTO job_threads(tenant_id,ticket_id,unit_id,station_id,category,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(g.tenant['id'],ticket_id,unit or None,int(picked) if picked else None,category,g.user['id'],now()))
                thread_id=row.lastrowid
                message=db().execute('INSERT INTO job_messages(tenant_id,ticket_id,thread_id,actor_id,body,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],ticket_id,thread_id,g.user['id'],body,now()))
                seen(dict(id=thread_id,ticket_id=ticket_id),message.lastrowid)
            return redirect(url_for('job_conversation',thread_id=thread_id))
        return render_template('new_job_message.html',ticket=job,snippets=available(db(),g.tenant['id'],'request'),stations=stations,unit=unit,selected_station=picked)

    @app.route('/messages/<int:thread_id>',methods=['GET','POST'])
    @roles_required(*ROLES)
    def job_conversation(thread_id):
        with db():
            db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],ROLES)
            thread=db().execute('SELECT t.*,s.name AS station_name,u.serial FROM job_threads t LEFT JOIN stations s ON s.tenant_id=t.tenant_id AND s.id=t.station_id LEFT JOIN serial_units u ON u.tenant_id=t.tenant_id AND u.id=t.unit_id WHERE t.tenant_id=? AND t.id=?',(g.tenant['id'],thread_id)).fetchone()
            if not thread:abort(404,'Conversation unavailable.')
            job=ticket(thread['ticket_id'])
            if request.method=='POST':
                if request.form.get('version')!=str(thread['version']):abort(409,'New activity arrived. Reopen this conversation before responding.')
                action=request.form.get('action');status=thread['status']
                if action in {'acknowledge','resolve'}:
                    if not {'admin','planner'} & set(g.roles):abort(403,'A planner or admin must acknowledge or resolve this request.')
                    status='acknowledged' if action=='acknowledge' else 'resolved'
                    body='Request acknowledged.' if action=='acknowledge' else 'Request marked resolved.'
                elif action=='reply':
                    if 'snippet_id' in request.form:
                        _,body=selected(db(),g.tenant['id'],'reply')
                    else:  # Existing open forms can still finish safely.
                        choice=request.form.get('quick_reply','');note=request.form.get('note','').strip()
                        if choice and choice not in REPLIES:abort(400,'Choose a listed reply.')
                        body=(choice+('\n' if choice and note else '')+note).strip()
                        if not 1<=len(body)<=2000:abort(400,'Choose a quick reply or enter up to 2,000 characters.')
                    if status=='resolved':status='new'
                    elif status=='new' and {'admin','planner'} & set(g.roles):status='acknowledged'
                else:abort(400,'Choose a conversation action.')
                db().execute('INSERT INTO job_messages(tenant_id,ticket_id,thread_id,actor_id,body,created_at) VALUES(?,?,?,?,?,?)',(g.tenant['id'],thread['ticket_id'],thread_id,g.user['id'],body,now()))
                db().execute('UPDATE job_threads SET status=?,version=version+1 WHERE tenant_id=? AND id=?',(status,g.tenant['id'],thread_id))
            messages=db().execute('SELECT m.*,u.username FROM job_messages m JOIN users u ON u.id=m.actor_id WHERE m.tenant_id=? AND m.thread_id=? ORDER BY m.id',(g.tenant['id'],thread_id)).fetchall()
            if messages:seen(thread,messages[-1]['id'])
        if request.method=='POST':return redirect(url_for('job_conversation',thread_id=thread_id))
        return render_template('job_conversation.html',thread=thread,ticket=job,messages=messages,snippets=available(db(),g.tenant['id'],'reply'))
