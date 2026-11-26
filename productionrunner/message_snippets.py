"""Built-in and tenant-managed tap-to-send message text."""
import json
import sqlite3
from flask import abort,g,request,render_template,redirect,url_for
from .intake import actor

CATEGORIES=['Drawing question','DXF issue','Material issue','Quality concern','Quantity mismatch','Help needed','Ready for planner review','Other']
# Stable keys identify complete messages, not user-editable text posted by clients.
_BUILTINS=[
 ('drawing-mismatch','Drawing does not match','The drawing does not match this traveler. Please confirm the correct drawing and revision.','Drawing question','request'),
 ('drawing-unclear','Drawing needs clarification','The drawing is unclear. Please review the requirements with me.','Drawing question','request'),
 ('dxf-missing','DXF missing or will not load','The DXF is missing or will not load. Please check the production file.','DXF issue','request'),
 ('dxf-mismatch','DXF does not match drawing','The DXF does not match the drawing. Please review the correct production file.','DXF issue','request'),
 ('material-missing','Material unavailable','The required material is not available at this station. Please advise.','Material issue','request'),
 ('material-damaged','Material is damaged','The material is damaged or defective. Please review before work continues.','Material issue','request'),
 ('material-wrong','Material does not match','The material does not match the job requirements. Please confirm what to use.','Material issue','request'),
 ('quality-review','Part needs quality review','A part may not meet the requirements. Please arrange a quality review.','Quality concern','request'),
 ('quantity-mismatch','Quantity does not match','The quantity at this station does not match the traveler. Please help reconcile it.','Quantity mismatch','request'),
 ('need-help','Need help at station','I need assistance at this station. Please contact me.','Help needed','both'),
 ('ready-review','Ready for planner review','This job is ready for planner review. Please review the next step.','Ready for planner review','request'),
 ('checking','Checking now','I am checking this now and will reply with guidance.','Help needed','reply'),
 ('pause-wait','Please pause and wait','Please pause work and wait for further guidance.','Help needed','reply'),
 ('updated-drawing','Review updated drawing','Please review the updated drawing before continuing.','Drawing question','reply'),
 ('contact-me','Please contact me','Please contact me to discuss this job.','Help needed','reply'),
 ('understood','Understood','Understood. Thank you.','Help needed','reply'),
 ('still-waiting','Still waiting for help','I am still waiting for assistance with this request.','Help needed','reply'),
 ('resolved-station','Issue resolved at station','The issue has been resolved at the station. Please review this request for closure.','Help needed','reply'),
]
BUILTINS=[dict(key='builtin:'+key,title=title,body=body,category=category,usage=usage,builtin=True) for key,title,body,category,usage in _BUILTINS]


def available(conn,tenant,usage):
    result=[s for s in BUILTINS if s['usage'] in {usage,'both'}]
    for row in conn.execute("SELECT * FROM message_snippets WHERE tenant_id=? AND active=1 AND usage IN (?,'both') ORDER BY title,id",(tenant,usage)):
        result.append(dict(row,key=f"custom:{row['id']}:{row['version']}",builtin=False))
    return result


def selected(conn,tenant,usage):
    key=request.form.get('snippet_id','')
    if key=='other':
        note=request.form.get('note','').strip()
        if not 1<=len(note)<=1800:abort(400,'Type a message for Other, or choose a predefined snippet.')
        return 'Other',note
    choice=next((s for s in available(conn,tenant,usage) if s['key']==key),None)
    if not choice:abort(409,'Choose an available snippet. Its wording may have changed; reload to review it.')
    return choice['category'],choice['body']


def register(app,db,roles_required,now):
    @app.route('/admin/message-snippets',methods=['GET','POST'])
    @roles_required('admin')
    def message_snippets_admin():
        if request.method=='POST':
            title=request.form.get('title','').strip();body=request.form.get('body','').strip()
            category=request.form.get('category');usage=request.form.get('usage');active=request.form.get('active','1');reason=request.form.get('reason','').strip()
            if not 1<=len(title)<=80 or not 1<=len(body)<=1800 or category not in CATEGORIES or usage not in {'request','reply','both'} or active not in {'0','1'} or not 1<=len(reason)<=500:abort(400,'Enter a title, message, category, use, status and change reason.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                old=None;sid=request.form.get('id','')
                if sid:
                    if not sid.isascii() or not sid.isdigit() or len(sid)>10:abort(404,'Unknown snippet.')
                    old=db().execute('SELECT * FROM message_snippets WHERE tenant_id=? AND id=?',(g.tenant['id'],int(sid))).fetchone()
                    if not old:abort(404,'Unknown snippet.')
                    if request.form.get('version')!=str(old['version']):abort(409,'Snippet changed. Reload before editing.')
                try:
                    if old:db().execute('UPDATE message_snippets SET title=?,body=?,category=?,usage=?,active=?,version=version+1 WHERE tenant_id=? AND id=?',(title,body,category,usage,int(active),g.tenant['id'],int(sid)))
                    else:sid=db().execute('INSERT INTO message_snippets(tenant_id,title,body,category,usage,active) VALUES(?,?,?,?,?,?)',(g.tenant['id'],title,body,category,usage,int(active))).lastrowid
                except sqlite3.IntegrityError:abort(409,'A snippet with that title already exists. Edit the existing snippet or choose a different title.')
                detail=json.dumps(dict(snippet_id=int(sid),before=dict(old) if old else None,after=dict(title=title,body=body,category=category,usage=usage,active=int(active)),reason=reason))
                db().execute("INSERT INTO auth_events(tenant_id,user_id,action,actor,detail,created_at) VALUES(?,?,'message_snippet_changed',?,?,?)",(g.tenant['id'],g.user['id'],'user:'+str(g.user['id']),detail,now()))
            return redirect(url_for('message_snippets_admin'))
        return render_template('message_snippets_admin.html',snippets=db().execute('SELECT * FROM message_snippets WHERE tenant_id=? ORDER BY active DESC,title',(g.tenant['id'],)).fetchall(),builtins=BUILTINS,categories=CATEGORIES)
