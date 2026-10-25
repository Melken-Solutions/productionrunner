"""ProductionRunner: provisional, locally runnable vertical slice."""
import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

import click
from barcode import Code128
from flask import Flask, abort, g, redirect, render_template, request, session, url_for, Response
from werkzeug.security import check_password_hash, generate_password_hash


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
            if not g.user:
                return redirect(url_for('login'))
            if roles and not set(roles).intersection(g.roles):
                abort(403, 'Your role cannot perform this action.')
            return fn(*args, **kwargs)
        return wrapped
    return decorate


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    secret_path = Path(app.instance_path) / 'session-secret'
    if not secret_path.exists():
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
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=64 * 1024)
    if config:
        app.config.update(config)

    @app.before_request
    def prepare():
        g.app_db = app.config['DATABASE']
        g.user = db().execute('SELECT * FROM users WHERE id=?', (session.get('user_id'),)).fetchone()
        g.roles = g.user['roles'].split(',') if g.user else []
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
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; img-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    def error_page(error):
        return render_template('error.html', error=error), error.code

    def event(ticket_id, action, detail=''):
        db().execute('INSERT INTO events(ticket_id,actor_id,action,detail,created_at) VALUES(?,?,?,?,?)',
                     (ticket_id, g.user['id'], action, detail, now()))

    def ticket_or_404(ticket_id):
        ticket = db().execute('SELECT * FROM tickets WHERE id=?', (ticket_id,)).fetchone()
        if not ticket:
            abort(404, 'Unknown JobRunner traveler.')
        return ticket

    def lock_ticket(ticket_id):
        db().execute('BEGIN IMMEDIATE')
        ticket = ticket_or_404(ticket_id)
        if request.form.get('version') != str(ticket['version']):
            abort(409, 'This ticket changed. Reload before submitting again.')
        return ticket

    @app.cli.command('init-db')
    def init_db():
        """Create missing tables without deleting existing data."""
        with sqlite3.connect(app.config['DATABASE']) as connection:
            connection.executescript(Path(app.root_path, 'schema.sql').read_text())
        click.echo('Database initialized (existing data preserved).')

    @app.cli.command('add-user')
    @click.argument('username')
    @click.option('--roles', required=True, help='Comma-separated planner,operator,inspector,admin,viewer')
    @click.password_option()
    def add_user(username, roles, password):
        """Provision an individual account; no default passwords."""
        assigned = set(roles.split(','))
        if not assigned or not assigned <= {'planner', 'operator', 'inspector', 'admin', 'viewer'}:
            raise click.ClickException('Unknown role.')
        if len(password) < 12:
            raise click.ClickException('Use at least 12 characters.')
        try:
            with sqlite3.connect(app.config['DATABASE']) as connection:
                connection.execute('INSERT INTO users(username,password_hash,roles) VALUES(?,?,?)',
                                   (username, generate_password_hash(password), ','.join(sorted(assigned))))
        except sqlite3.IntegrityError:
            raise click.ClickException('Username already exists.') from None
        click.echo(f'Created {username}.')

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = None
        if request.method == 'POST':
            user = db().execute('SELECT * FROM users WHERE username=?', (request.form.get('username', ''),)).fetchone()
            if user and check_password_hash(user['password_hash'], request.form.get('password', '')):
                session.clear()
                session.update(user_id=user['id'], csrf=secrets.token_hex(32))
                session.permanent = True
                return redirect(url_for('index'))
            error = 'Username or password is incorrect.'
        return render_template('login.html', error=error)

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    @app.get('/')
    @roles_required()
    def index():
        tickets = db().execute('SELECT * FROM tickets ORDER BY created_at DESC').fetchall()
        return render_template('index.html', tickets=tickets)

    @app.get('/scan')
    @roles_required()
    def scan():
        code = request.args.get('code', '').strip()
        ticket_or_404(code)
        return redirect(url_for('ticket_detail', ticket_id=code))

    @app.route('/tickets/new', methods=['GET', 'POST'])
    @roles_required('planner')
    def new_ticket():
        if request.method == 'GET':
            return render_template('new.html')
        pn = request.form.get('pn', '').strip()
        revision = request.form.get('revision', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', pn):
            abort(400, 'Enter exactly one part number (letters, numbers, dot, underscore or hyphen).')
        if not revision or len(revision) > 40:
            abort(400, 'A revision of up to 40 characters is required.')
        try:
            quantity = int(request.form.get('quantity', ''))
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
        ticket_id = 'JR-' + secrets.token_hex(6).upper()
        with db():
            db().execute('INSERT INTO tickets VALUES(?,?,?,?,?,?,?,?,?,?,1)',
                         (ticket_id, pn, revision, quantity, tracking, json.dumps(serials), instructions, 'ready', g.user['id'], now()))
            for sequence, line in enumerate(route, 1):
                name, _, detail = line.partition('|')
                if not name.strip():
                    abort(400, 'Each operation needs a name.')
                db().execute('INSERT INTO operations(ticket_id,sequence,name,instructions) VALUES(?,?,?,?)',
                             (ticket_id, sequence, name.strip(), detail.strip() or instructions))
            event(ticket_id, 'ticket_created', f'{pn} rev {revision}; quantity {quantity}; {tracking}')
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.get('/tickets/<ticket_id>')
    @roles_required()
    def ticket_detail(ticket_id):
        ticket = ticket_or_404(ticket_id)
        operations = db().execute('SELECT o.*, u.username FROM operations o LEFT JOIN users u ON u.id=o.completed_by WHERE ticket_id=? ORDER BY sequence', (ticket_id,)).fetchall()
        events = db().execute('SELECT e.*, u.username FROM events e JOIN users u ON u.id=e.actor_id WHERE ticket_id=? ORDER BY e.id', (ticket_id,)).fetchall()
        next_operation = next((o for o in operations if o['completed_by'] is None), None)
        independent = all(o['completed_by'] != g.user['id'] for o in operations)
        return render_template('ticket.html', ticket=ticket, operations=operations, events=events,
                               next_operation=next_operation, independent=independent, serials=json.loads(ticket['serials']))

    @app.post('/tickets/<ticket_id>/complete')
    @roles_required('operator')
    def complete(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            if ticket['state'] not in {'ready', 'in_progress'}:
                abort(409, 'This ticket is not available for production.')
            operation = db().execute('SELECT * FROM operations WHERE ticket_id=? AND completed_by IS NULL ORDER BY sequence LIMIT 1', (ticket_id,)).fetchone()
            if not operation or request.form.get('operation_id') != str(operation['id']):
                abort(409, 'Only the next routing operation can be completed.')
            if request.form.get('confirm') != 'yes':
                abort(400, 'Confirm that the entire batch completed this operation.')
            db().execute('UPDATE operations SET completed_by=?, completed_at=? WHERE id=?', (g.user['id'], now(), operation['id']))
            remaining = db().execute('SELECT COUNT(*) FROM operations WHERE ticket_id=? AND completed_by IS NULL', (ticket_id,)).fetchone()[0]
            state = 'in_progress' if remaining else 'awaiting_inspection'
            db().execute('UPDATE tickets SET state=?,version=version+1 WHERE id=?', (state, ticket_id))
            event(ticket_id, 'operation_completed', f"{operation['sequence']}: {operation['name']}")
        return redirect(url_for('ticket_detail', ticket_id=ticket_id))

    @app.post('/tickets/<ticket_id>/inspect')
    @roles_required('inspector')
    def inspect(ticket_id):
        with db():
            ticket = lock_ticket(ticket_id)
            if ticket['state'] != 'awaiting_inspection':
                abort(409, 'Complete all production operations before inspection.')
            if db().execute('SELECT 1 FROM operations WHERE ticket_id=? AND completed_by=?', (ticket_id, g.user['id'])).fetchone():
                abort(403, 'Independent inspection requires someone who performed none of this ticket’s production operations.')
            result, notes = request.form.get('result'), request.form.get('notes', '').strip()
            if result not in {'pass', 'fail'} or not notes or len(notes) > 4000:
                abort(400, 'Choose pass or fail and record inspection evidence (up to 4000 characters).')
            db().execute('INSERT INTO inspections(ticket_id,inspector_id,result,notes,created_at) VALUES(?,?,?,?,?)',
                         (ticket_id, g.user['id'], result, notes, now()))
            db().execute('UPDATE tickets SET state=?,version=version+1 WHERE id=?', ('accepted' if result == 'pass' else 'held', ticket_id))
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
        operations = db().execute('SELECT * FROM operations WHERE ticket_id=? ORDER BY sequence', (ticket_id,)).fetchall()
        return render_template('traveler.html', ticket=ticket, operations=operations, serials=json.loads(ticket['serials']))

    @app.get('/tickets/<ticket_id>/barcode.svg')
    @roles_required()
    def barcode(ticket_id):
        ticket_or_404(ticket_id)
        return Response(Code128(ticket_id).render({'module_width': 0.3, 'module_height': 18, 'quiet_zone': 6.5}), mimetype='image/svg+xml')

    return app
