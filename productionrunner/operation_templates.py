"""Reusable operation defaults, copied into each request rather than live-linked."""
import json,secrets,re
from flask import abort,g,request,render_template,redirect,url_for
KINDS=('production','inspection','packing','shipping')
STARTERS=[
 ('laser','Laser cut','production','Verify material type and sheet condition. Confirm drawing revision matches the work order and DXF matches the drawing. Cut all features completely. Verify specified dimensions. Identify and segregate nonconforming parts.','Material matches drawing requirements. Geometry, holes and cutouts are complete and correctly located. Critical dimensions meet drawing tolerances. Edges have no excessive dross, slag or burn marks. Confirm this lot’s completed quantity and account for held or scrapped parts. Acceptable for downstream fabrication.',True),
 ('deburr','Deburr — Timesaver','production','Deburr using the approved Timesaver setup. Protect the specified surface finish and verify edges safely after processing.','No burrs or sharp edges remain. No surface damage from deburring. Dimensions remain within drawing tolerances. Perform safe visual and tactile inspection using the approved method.',False),
 ('stamp','Stamping','production','Apply the specified marking using the approved tool template. Verify the correct tool, template revision and marking information before stamping.','Marking is present and legible. Content, location and orientation match the tool template. Depth is acceptable with no distortion, dents, cracks or surface damage. Apply these checks only where stamping is required.',False),
 ('inspection','Final independent inspection','inspection','','Part matches the approved drawing and revision. Critical dimensions meet specified tolerances. Required deburring is complete with no sharp edges. Where required, marking is correct, legible and properly located. No unacceptable damage or defects. Record results and disposition; acceptance requires an authorized independent inspector.',False),
 ('packing','Packing','packing','','Pack only accepted parts. Confirm PN, revision and this box’s quantity. Protect surfaces and prevent transit damage using the specified packaging. Include required documentation. Verify box identification and cleanliness.',False),
 ('shipping','Shipping / departure','shipping','','Confirm the ship-to destination, boxes and quantities. Verify current, accurate and legible labels and required documentation. Confirm required approvals are complete. Record actual physical departure when the shipment leaves the building.',False),
]

# These are suggestions, not equipment qualifications or automatic route assignments.
STATION_HINTS = {
    'laser': r'laser', 'deburr': r'time\s*saver|deburr', 'stamp': r'stamp|mark|bend',
    'drill': r'drill', 'countersink': r'drill', 'counterbore': r'drill', 'tap': r'drill|tap',
    'bend': r'bend|brak[e]?\s*press|press\s*brake',
    'inspection': r'inspect', 'packing': r'pack', 'shipping': r'pack|ship',
}
STARTERS += [
 ('drill','Drill holes','production','Drill the identified holes in accordance with the released drawing and work order. Use the approved tooling and setup. Protect finished surfaces. Identify and segregate nonconforming parts.','Verify hole quantity, locations, diameters and depth against the drawing callouts. Check for burrs, breakout and surface damage. Record completed quantity and any nonconforming parts.',False),
 ('countersink','Countersink holes','production','Countersink the identified holes in accordance with the released drawing. Confirm the hole callout, tooling and setup before machining. Protect finished surfaces and segregate nonconforming parts.','Verify hole locations and quantity, countersink angle and diameter or depth against the drawing. Check seating where specified, burrs and surface condition. Do not infer unspecified tolerances.',False),
 ('counterbore','Counterbore holes','production','Counterbore the identified holes per the released drawing using approved tooling and setup. Confirm the drawing callout before machining. Segregate nonconforming parts.','Verify counterbore quantity, location, diameter and depth against the drawing. Check shoulder condition, burrs and surface damage. Record completed quantity and nonconforming parts.',False),
 ('tap','Tap threads','production','Tap the identified holes to the thread specification on the released drawing. Confirm tap, pilot hole and depth requirements before work. Use the approved setup and protect finished surfaces.','Verify thread size, pitch, location and usable depth against the drawing. Check with the specified inspection method or gauge. Check burrs and damaged threads; segregate nonconforming parts.',False),
 ('bend','Form bends','production','Form bends in the specified sequence using approved tooling and the released drawing. Confirm material, grain direction where specified, bend orientation and setup. Protect the specified finish.','Verify bend angles, flange dimensions, orientation and bend radii against drawing requirements. Check for cracks, distortion, tooling marks and finish damage. Record completed quantity and nonconforming parts.',False),
]

def current(conn,tenant,include_inactive=False):
    result={key:dict(key=key,version=0,active=True,name=name,kind=kind,instructions=instruction,criteria=criteria,station_id=0,first_piece=False,dxf_required=dxf) for key,name,kind,instruction,criteria,dxf in STARTERS}
    for row in conn.execute('SELECT * FROM operation_templates t WHERE tenant_id=? AND version=(SELECT MAX(version) FROM operation_templates x WHERE x.tenant_id=t.tenant_id AND x.template_key=t.template_key)',(tenant,)):
        result[row['template_key']]=dict(json.loads(row['data']),key=row['template_key'],version=row['version'],active=bool(row['active']))
    stations=conn.execute('SELECT id,name FROM stations WHERE tenant_id=? AND active=1',(tenant,)).fetchall()
    for key,item in result.items():
        if 'station_ids' not in item:
            if item.get('station_id'):
                item['station_ids']=[item['station_id']]
            elif item['version']==0 and key in STATION_HINTS:
                item['station_ids']=[s['id'] for s in stations if re.search(STATION_HINTS[key],s['name'],re.I)]
            else:
                item['station_ids']=[]
    return sorted((v for v in result.values() if include_inactive or v['active']),key=lambda x:(KINDS.index(x['kind']),x['name']))

def register(app,db,roles_required,now):
    @app.context_processor
    def context():
        return dict(operation_library=lambda:current(db(),g.tenant['id']))

    @app.route('/admin/operation-templates',methods=['GET','POST'])
    @roles_required('admin')
    def operation_template_admin():
        if request.method=='POST':
            from .intake import actor
            key=request.form.get('key','');why=request.form.get('reason','').strip()
            if request.form.get('action') not in {'save','retire','restore'}:abort(400,'Choose save, retire or restore.')
            if not 1<=len(why)<=500:abort(400,'Record a reason for this template change.')
            with db():
                db().execute('BEGIN IMMEDIATE');actor(db(),g.tenant['id'],g.user['id'],('admin',))
                previous=next((t for t in current(db(),g.tenant['id'],True) if t['key']==key),None)
                if key and not previous:abort(404)
                if previous and request.form.get('version')!=str(previous['version']):abort(409,'Template changed. Reload before saving.')
                if request.form.get('action') in ('retire','restore'):
                    if not previous:abort(404)
                    data={k:v for k,v in previous.items() if k not in ('key','version','active')};active=request.form['action']=='restore'
                else:
                    data={k:request.form.get(k,'').strip() for k in ('name','kind','instructions','criteria')}
                    if not 1<=len(data['name'])<=120 or '|' in data['name'] or data['kind'] not in KINDS or not 1<=len(data['criteria'])<=2000 or len(data['instructions'])>350 or (data['kind']=='production' and not data['instructions']):abort(400,'Provide a name, operation type, instructions and acceptance checks within the displayed limits.')
                    try:sid=int(request.form.get('station_id') or 0)
                    except ValueError:abort(400,'Choose a valid station.')
                    if sid and not db().execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1',(g.tenant['id'],sid)).fetchone():abort(400,'Choose an active station in your organization.')
                    try:station_ids=sorted(set(int(v) for v in request.form.getlist('station_ids')))
                    except ValueError:abort(400,'Choose valid stations.')
                    for station in station_ids:
                        if not db().execute('SELECT 1 FROM stations WHERE tenant_id=? AND id=? AND active=1',(g.tenant['id'],station)).fetchone():abort(400,'Choose active stations in your organization.')
                    if sid and station_ids and sid not in station_ids:abort(400,'The suggested station must be one of the selected stations.')
                    if not station_ids and sid:station_ids=[sid]
                    data.update(station_ids=station_ids,station_id=sid,first_piece=data['kind']=='production' and request.form.get('first_piece')=='yes',dxf_required=data['kind']=='production' and request.form.get('dxf_required')=='yes');active=True
                db().execute('INSERT INTO operation_templates(tenant_id,template_key,version,active,data,actor_id,reason,created_at) VALUES(?,?,?,?,?,?,?,?)',(g.tenant['id'],key or secrets.token_hex(12),(previous['version']+1) if previous else 1,int(active),json.dumps(data),g.user['id'],why,now()))
            return redirect(url_for('operation_template_admin'))
        history=db().execute('SELECT t.*,u.username FROM operation_templates t JOIN users u ON u.id=t.actor_id WHERE t.tenant_id=? ORDER BY t.created_at DESC',(g.tenant['id'],)).fetchall()
        history=[dict(r,content=json.loads(r['data'])) for r in history]
        return render_template('operation_templates.html',templates=current(db(),g.tenant['id'],True),kinds=KINDS,stations=db().execute('SELECT * FROM stations WHERE tenant_id=? AND active=1 ORDER BY name',(g.tenant['id'],)).fetchall(),history=history)
