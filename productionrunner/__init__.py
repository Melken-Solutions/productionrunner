"""ProductionRunner: provisional, locally runnable vertical slice."""
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from .jobrunner_ids import next_ticket_id

from barcode import Code128
from flask import Flask, abort, g, redirect, render_template, request, session, url_for, Response


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(g.app_db, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db


def roles_required(*roles):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not g.user or not g.tenant:
                return redirect(url_for('login'))
            if roles and not set(roles).intersection(g.roles):
                abort(403, 'Your role cannot perform this action.')
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def create_app(config=None):
    data_dir = os.environ.get('PR_DATA_DIR')
    if data_dir and not Path(data_dir).is_absolute():
        raise ValueError('PR_DATA_DIR must be an absolute path.')
    app = Flask(__name__, instance_relative_config=True, instance_path=data_dir or None)
    # Validate production prerequisites before creating any persistent state.
    from .hosting import configure, readiness, register_hosting
    if config:
        app.config.update(config)
    configure(app)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    secret_path = Path(app.instance_path) / 'session-secret'
    if not os.environ.get('PR_SECRET_KEY') and not secret_path.exists():
        try:
            fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as f:
                f.write(secrets.token_hex(32))
        except FileExistsError:
            pass
    app.config.update(SECRET_KEY=os.environ.get('PR_SECRET_KEY') or secret_path.read_text(),
                      DATABASE=str(Path(app.instance_path) / 'productionrunner.sqlite3'),
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('PR_HTTPS') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=31 * 1024 * 1024)
    if config:
        app.config.update(config)

    register_hosting(app)

    @app.before_request
    def prepare():
        # A rejected Host must not render tenant templates or touch the database.
        from werkzeug.exceptions import SecurityError
        if isinstance(request.routing_exception, SecurityError):
            return 'Invalid host.', 400
        if request.endpoint == 'hosting_readiness':
            return readiness(app)
        if request.endpoint == 'admin_branding':
            request.max_content_length = 4 * 1024 * 1024
        elif request.endpoint == 'import_parts':
            request.max_content_length = 51 * 1024 * 1024
        elif request.endpoint == 'upload_dxf':
            request.max_content_length = 21 * 1024 * 1024
        elif request.endpoint != 'new_part':
            request.max_content_length = 64 * 1024
        g.app_db = app.config['DATABASE']
        g.user = db().execute('SELECT * FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        g.tenant = db().execute(
            'SELECT t.*, m.roles FROM tenants t JOIN memberships m ON m.tenant_id=t.id '
            'WHERE t.id=? AND m.user_id=? AND m.roles != ? ',
            (session.get('tenant_id'), session.get('user_id'), '')).fetchone() if g.user else None
        from .admin import epoch
        if g.tenant and epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
            g.tenant = None
        g.roles = g.tenant['roles'].split(',') if g.tenant else []
        if g.user and not g.tenant:
            session.clear()
            g.user = None
        if 'csrf' not in session:
            session['csrf'] = secrets.token_hex(32)
        if request.method == 'POST' and not secrets.compare_digest(session['csrf'], request.form.get('csrf', '')):
            abort(400, 'Invalid form token. Reload the page and try again.')

    @app.teardown_appcontext
    def close_db(error=None):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    @app.after_request
    def headers(response):
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; style-src 'self'; img-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    @app.errorhandler(409)
    def error_page(error):
        return render_template('error.html', error=error), error.code

    def event(ticket_id, action, detail=''):
        db().execute('INSERT INTO events(tenant_id,ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?,?)',
                     (g.tenant['id'], ticket_id, g.user['id'], action, detail, now()))

    def ticket_or_404(ticket_id):
        ticket = db().execute('SELECT t.*, p.priority AS hot_priority, p.hot_since FROM tickets t LEFT JOIN ticket_priorities p ON p.ticket_id=t.id AND p.tenant_id=t.tenant_id WHERE t.id=? AND t.tenant_id=?', (ticket_id, g.tenant['id'])).fetchone()
        if not ticket:
            abort(404, 'Unknown JobRunner traveler.')
        return ticket

    def lock_ticket(ticket_id):
        db().execute('BEGIN IMMEDIATE')
        ticket = ticket_or_404(ticket_id)
        from .production_controls import require_active
        require_active(db(), g.tenant['id'], ticket_id)
        if request.form.get('version') != str(ticket['version']):
            abort(409, 'This ticket changed. Reload before submitting again.')
        return ticket

    from .workflow_defaults import register_workflow_defaults, settings as workflow_settings, validate_ticket as validate_workflow, snapshot as snapshot_workflow
    register_workflow_defaults(app, db, roles_required)
    from .intake import register_intake, approved_request, link_ticket, actor as intake_actor
    register_intake(app, db, roles_required, now)
    from .fulfillment import register_fulfillment
    register_fulfillment(app, db, roles_required, now, lock_ticket, event)
    from .returns import register_returns
    register_returns(app, db, roles_required, now, lock_ticket, event)
    from .return_repairs import register_return_repairs
    register_return_repairs(app, db, roles_required, now, lock_ticket, event)
    from .work_tracking import register_work_tracking, contributed
    register_work_tracking(app, db, roles_required, now, event)
    from .tenancy import register_commands
    register_commands(app)
    from .branding import register_branding
    register_branding(app, db, roles_required)
    from .storage_management import register_storage
    register_storage(app, db, roles_required)
    from .admin import register_admin
    register_admin(app, db, roles_required)
    from .dispatch import register_dispatch
    register_dispatch(app, db, roles_required, now, lock_ticket, event)
    from .tv_boards import register_tv
    register_tv(app, db, roles_required, now)
    from .shop_floor import register_shop_floor
    register_shop_floor(app, db, roles_required)
    from .developer_admin import register_developer
    register_developer(app, db, now)
    from .production_controls import register_controls
    register_controls(app, db, roles_required, now, lock_ticket)
    from .pin_auth import authenticate, register_pin_auth
    register_pin_auth(app, db, roles_required)
    from .serials import create_units, units_for, register_serials
    register_serials(app, db, roles_required, now)
    from .parts import register_parts, find_part, matching_part, FIELDS
    register_parts(app, db, roles_required, now, lock_ticket, event)
    from .part_import import register_import
    register_import(app, db, roles_required, now)

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            identity, status = authenticate(db(), app.config['AUTH_CLOCK'])
            if identity:
                session.clear()
                session.update(user_id=identity['id'], tenant_id=identity['tenant_id'],
                               auth_method=request.form.get('method', 'password'),
                               membership_epoch=identity['membership_epoch'], pin_version=identity['version'], csrf=secrets.token_hex(32))
                session.permanent = True
                member_roles = db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?', (identity['tenant_id'], identity['id'])).fetchone()[0].split(',')
                return redirect(url_for('tv_boards' if 'display' in member_roles else 'index'))
            error = 'Too many attempts. Try again in 15 minutes.' if status == 429 else 'Organization, username or credential is incorrect.'
            response = app.make_response((render_template('login.html', error=error), status))
            if status == 429:
                response.headers['Retry-After'] = '900'
            return response
        return render_template('login.html', error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @roles_required()
    def index():
        tickets = db().execute('SELECT t.*, p.priority AS hot_priority, p.hot_since FROM tickets t LEFT JOIN ticket_priorities p ON p.ticket_id=t.id AND p.tenant_id=t.tenant_id WHERE t.tenant_id=? ORDER BY EXISTS(SELECT 1 FROM ticket_cancellations x WHERE x.ticket_id=t.id AND x.tenant_id=t.tenant_id), (p.priority IS NULL), p.priority, p.hot_since, CASE WHEN p.priority IS NOT NULL THEN t.id END, t.created_at DESC, t.id', (g.tenant['id'],)).fetchall()
        return render_template('index.html', tickets=tickets)

    @app.post('/tickets/<ticket_id>/priority')
    @roles_required('planner', 'admin')
    def set_priority(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            action = request.form.get('action')
            if action not in {'hot', 'normal'}:
                abort(400, 'Choose HOT or normal priority.')
            priority = None
            if action == 'hot':
                raw = request.form.get('priority', '10')
                if not re.fullmatch(r'[0-9]{1,2}', raw) or not 1 <= int(raw) <= 99:
                    abort(400, 'HOT priority must be a number from 1 to 99 (1 is highest).')
                priority = int(raw)
            if priority != ticket['hot_priority']:
                if priority is None:
                    db().execute('DELETE FROM ticket_priorities WHERE tenant_id=? AND ticket_id=?', (g.tenant['id'], ticket_id))
                else:
                    db().execute('INSERT INTO ticket_priorities(tenant_id,ticket_id,priority,hot_since) VALUES(?,?,?,?) '
                                 'ON CONFLICT(ticket_id) DO UPDATE SET priority=excluded.priority',
                                 (g.tenant['id'], ticket_id, priority, now()))
                db().execute('UPDATE tickets SET version=version+1 WHERE tenant_id=? AND id=?', (g.tenant['id'], ticket_id))
                before = f"HOT {ticket['hot_priority']}" if ticket['hot_priority'] is not None else 'Normal'
                after = f'HOT {priority}' if priority is not None else 'Normal'
                event(ticket_id, 'priority_changed', f'{before} → {after}')
        return redirect(url_for('index'))

    @app.get('/scan')
    @roles_required()
    def scan():
        code = request.args.get('code', '').strip()
        if code.startswith('JR-U-'):
            unit = db().execute('SELECT id FROM serial_units WHERE tenant_id=? AND id=?', (g.tenant['id'], code)).fetchone()
            if not unit:
                abort(404, 'Unknown unit traveler.')
            return redirect(url_for('unit_detail', unit_id=code))
        ticket_or_404(code)
        return redirect(url_for('ticket_detail', ticket_id=code))

    @app.route('/tickets/new', methods=['GET', 'POST'])
    @roles_required('planner')
    def new_ticket():
        request_id = (request.args if request.method == 'GET' else request.form).get('request_id', '')
        intake_entry = approved_request(db(), g.tenant['id'], request_id) if request_id else None
        if request.method == 'GET':
            parts = db().execute(f'SELECT {FIELDS} FROM part_revisions WHERE tenant_id=? ORDER BY pn,revision', (g.tenant['id'],)).fetchall()
            return render_template('new.html', parts=parts, intake_entry=intake_entry, workflow=workflow_settings(db(), g.tenant['id']))
        pn = request.form.get('pn', '').strip()
        revision = request.form.get('revision', '').strip()
        part_id = intake_entry['part_id'] if intake_entry else request.form.get('part_revision_id', '')
        part = find_part(db(), g.tenant['id'], part_id) if part_id else matching_part(db(), g.tenant['id'], pn, revision)
        if part:
            pn, revision = part['pn'], part['revision']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', pn):
            abort(400, 'Enter exactly one part number (letters, numbers, dot, underscore or hyphen).')
        if not revision or len(revision) > 40:
            abort(400, 'A revision of up to 40 characters is required.')
        try:
            quantity = intake_entry['quantity'] if intake_entry else int(request.form.get('quantity', ''))
        except ValueError:
            abort(400, 'Quantity must be an integer.')
        if not 1 <= quantity <= 10000:
            abort(400, 'Quantity must be between 1 and 10000.')
        tracking = request.form.get('tracking', 'batch')
        serials = [line.strip() for line in request.form.get('serials', '').splitlines() if line.strip()]
        if tracking not in {'batch', 'serial'}:
            abort(400, 'Unknown tracking mode.')
        if tracking == 'batch' and serials:
            abort(400, 'Choose serial tracking when providing serial numbers.')
        if tracking == 'serial' and (len(serials) != quantity or len(set(serials)) != quantity or any(len(s) > 80 for s in serials)):
            abort(400, 'Provide exactly one unique serial number per unit (up to 80 characters each).')
        instructions = request.form.get('instructions', '').strip()
        route = [line.strip() for line in request.form.get('route', '').splitlines() if line.strip()]
        if not instructions or len(instructions) > 4000 or not 1 <= len(route) <= 20 or any(len(line) > 500 for line in route):
            abort(400, 'Provide work instructions and 1–20 routing steps, at most 500 characters per step.')
        with db():
            db().execute('BEGIN IMMEDIATE')
            intake_actor(db(), g.tenant['id'], g.user['id'], ('planner',))
            workflow = validate_workflow(db(), g.tenant['id'], request.form.get('workflow_version'), intake_entry)
            if intake_entry:
                if request.form.get('request_version') != str(intake_entry['version']):
                    abort(409, 'Request changed. Reopen the approved request.')
                intake_entry = approved_request(db(), g.tenant['id'], request_id, str(intake_entry['version']))
            stamp = now()
            ticket_id = next_ticket_id(db(), stamp)
            db().execute('INSERT INTO tickets(id,tenant_id,pn,revision,quantity,tracking,serials,instructions,state,created_by,created_at,version) VALUES(?,?,?,?,?,?,?,?,?,?,?,1)',
                         (ticket_id, g.tenant['id'], pn, revision, quantity, tracking, json.dumps(serials), instructions, 'ready', g.user['id'], stamp))
            db().execute('INSERT INTO ticket_dispatch VALUES(?,?,0)', (g.tenant['id'], ticket_id))
            snapshot_workflow(db(), g.tenant['id'], ticket_id, g.user['id'], workflow, now(), event)
            for sequence, line in enumerate(route, 1):
                name, _, detail = line.partition('|')
                if not name.strip():
                    abort(400, 'Each operation needs a name.')
                db().execute('INSERT INTO operations(tenant_id,ticket_id,sequence,name,instructions) VALUES(?,?,?,?,?)',
                             (g.tenant['id'], ticket_id, sequence, name.strip(), detail.strip() or instructions))
            if tracking == 'serial':
                create_units(db(), g.tenant['id'], ticket_id, serials)
            if part:
                db().execute('INSERT INTO ticket_drawings VALUES(?,?,?)', (g.tenant['id'], ticket_id, part['id']))
                event(ticket_id, 'drawing_attached', f"{pn} rev {revision}; {part['id']}")
            event(ticket_id, 'ticket_created', f'{pn} rev {revision}; quantity {quantity}; {tracking}')
            if intake_entry:
                link_ticket(db(), g.tenant['id'], g.user['id'], intake_entry, ticket_id, now())
                event(ticket_id, 'request_linked', json.dumps(dict(request_id=request_id, source=intake_entry['source'], source_reference=intake_entry['source_reference'], requester=intake_entry['requester'], destination=intake_entry['destination'], required_date=intake_entry['required_date'], document_approver_id=intake_entry['approved_by'])))
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.get('/tickets/<ticket_id>')
    @roles_required()
    def ticket_detail(ticket_id):
        ticket = ticket_or_404(ticket_id)
        operations = db().execute('SELECT o.*, u.username FROM operations o LEFT JOIN users u ON u.id=o.completed_by WHERE ticket_id=? AND tenant_id=? ORDER BY sequence', (ticket_id, g.tenant['id'])).fetchall()
        events = db().execute('SELECT e.*, u.username FROM events e JOIN users u ON u.id=e.actor_id WHERE ticket_id=? AND e.tenant_id=? ORDER BY e.id', (ticket_id, g.tenant['id'])).fetchall()
        next_operation = next((o for o in operations if o['completed_by'] is None), None)
        independent = all(o['completed_by'] != g.user['id'] for o in operations) and not contributed(db(),g.tenant['id'],ticket_id,None,g.user['id'])
        units = units_for(db(), g.tenant['id'], ticket_id)
        if units:
            return render_template('serial_ticket.html', ticket=ticket, units=units, operations=operations, events=events)
        return render_template('ticket.html', ticket=ticket, operations=operations, events=events,
                               next_operation=next_operation, independent=independent, serials=json.loads(ticket['serials']))

    @app.post('/tickets/<ticket_id>/complete')
    @roles_required('operator')
    def complete(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            if units_for(db(), g.tenant['id'], ticket_id):
                abort(409, 'Use the individual unit traveler for production and inspection.')
            if ticket['state'] not in {'ready', 'in_progress'}:
                abort(409, 'This ticket is not available for production.')
            operation = db().execute('SELECT * FROM operations WHERE ticket_id=? AND tenant_id=? AND completed_by IS NULL ORDER BY sequence LIMIT 1', (ticket_id, g.tenant['id'])).fetchone()
            if not operation or request.form.get('operation_id') != str(operation['id']):
                abort(409, 'Only the next routing operation can be completed.')
            from .dispatch import require_dispatch, snapshot
            require_dispatch(db(), g.tenant['id'], ticket_id, operation['id'])
            if request.form.get('confirm') != 'yes':
                abort(400, 'Confirm that the entire batch completed this operation.')
            from .work_tracking import finish_work
            finish_work(db(),g.tenant['id'],ticket,operation,g.user['id'],now(),event)
            db().execute('UPDATE operations SET completed_by=?, completed_at=? WHERE id=? AND tenant_id=?', (g.user['id'], now(), operation['id'], g.tenant['id']))
            remaining = db().execute('SELECT COUNT(*) FROM operations WHERE ticket_id=? AND tenant_id=? AND completed_by IS NULL', (ticket_id, g.tenant['id'])).fetchone()[0]
            state = 'in_progress' if remaining else 'awaiting_inspection'
            db().execute('UPDATE tickets SET state=?,version=version+1 WHERE id=? AND tenant_id=?', (state, ticket_id, g.tenant['id']))
            attribution = snapshot(db(), g.tenant['id'], ticket_id, operation['id'], g.user['id'], now())
            event(ticket_id, 'operation_completed', f"{operation['sequence']}: {operation['name']}. {attribution}")
        if request.form.get('shop_floor') == '1':
            return redirect(url_for('shop_work', code=ticket_id))
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/inspect')
    @roles_required('inspector')
    def inspect(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            if units_for(db(), g.tenant['id'], ticket_id):
                abort(409, 'Use the individual unit traveler for production and inspection.')
            if ticket['state'] != 'awaiting_inspection':
                abort(409, 'Complete all production operations before inspection.')
            from .work_tracking import contributed
            if contributed(db(),g.tenant['id'],ticket_id,None,g.user['id']):
                abort(403,'A station work contributor cannot inspect this batch.')
            if db().execute('SELECT 1 FROM operations WHERE ticket_id=? AND tenant_id=? AND completed_by=?', (ticket_id, g.tenant['id'], g.user['id'])).fetchone():
                abort(403, 'Independent inspection requires someone who performed none of this ticket’s production operations.')
            result, notes = request.form.get('result'), request.form.get('notes', '').strip()
            if result not in {'pass', 'fail'} or not notes or len(notes) > 4000:
                abort(400, 'Choose pass or fail and record inspection evidence (up to 4000 characters).')
            db().execute('INSERT INTO inspections(tenant_id,ticket_id,inspector_id,result,notes,created_at) VALUES(?,?,?,?,?,?)',
                         (g.tenant['id'], ticket_id, g.user['id'], result, notes, now()))
            db().execute('UPDATE tickets SET state=?,version=version+1 WHERE id=? AND tenant_id=?', ('accepted' if result == 'pass' else 'held', ticket_id, g.tenant['id']))
            event(ticket_id, 'inspection_' + result, notes)
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/issue-traveler')
    @roles_required('planner', 'operator')
    def issue_traveler(ticket_id):
        ticket_or_404(ticket_id)
        with db():
            event(ticket_id, 'traveler_requested', 'Print/reprint requested; physical printing is not verified.')
        return redirect(url_for('traveler', ticket_id=ticket_id))

    @app.get('/tickets/<ticket_id>/traveler')
    @roles_required()
    def traveler(ticket_id):
        ticket = ticket_or_404(ticket_id)
        operations = db().execute('SELECT * FROM operations WHERE ticket_id=? AND tenant_id=? ORDER BY sequence', (ticket_id, g.tenant['id'])).fetchall()
        return render_template('traveler.html', ticket=ticket, operations=operations, serials=json.loads(ticket['serials']),
                               units=units_for(db(), g.tenant['id'], ticket_id))

    @app.get('/tickets/<ticket_id>/barcode.svg')
    @roles_required()
    def barcode(ticket_id):
        ticket_or_404(ticket_id)
        return Response(Code128(ticket_id).render({'module_width': 0.3, 'module_height': 18, 'quiet_zone': 6.5}), mimetype='image/svg+xml')

    return app
