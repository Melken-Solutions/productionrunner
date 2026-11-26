"""Tenant-owned branding with validated raster logos and audited optimistic edits."""
import base64
import binascii
import hashlib
import json
import re
import secrets
from io import BytesIO

from flask import Response, abort, g, redirect, render_template, request, session, url_for
from PIL import Image, UnidentifiedImageError

from .admin import epoch
from .pin_auth import audit

DEFAULTS = dict(display_name='', primary_color='#102c3a', accent_color='#007477', logo=None, version=0)
MAX_LOGO = 2 * 1024 * 1024


def foreground(color):
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    return '#000000' if (luminance + .05) / .05 >= 1.05 / (luminance + .05) else '#ffffff'


def normalize_logo(data):
    if len(data) > MAX_LOGO:
        abort(413, 'Logo must be 2 MB or smaller.')
    try:
        with Image.open(BytesIO(data), formats=['PNG', 'JPEG']) as image:
            if image.width > 2048 or image.height > 2048 or getattr(image, 'n_frames', 1) != 1:
                abort(400, 'Use a still PNG/JPEG logo no larger than 2048 × 2048 pixels.')
            image.load()
            image.thumbnail((800, 400))
            # Rebuild pixels to discard metadata, embedded profiles and trailing content.
            clean = Image.new('RGBA', image.size)
            clean.paste(image.convert('RGBA'))
            output = BytesIO()
            clean.save(output, format='PNG')
            return output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        abort(400, 'Upload a valid PNG or JPEG logo.')


def register_branding(app, db, roles_required):
    def current():
        row = db().execute('SELECT * FROM organization_branding WHERE tenant_id=?', (g.tenant['id'],)).fetchone()
        return dict(row) if row else DEFAULTS.copy()

    @app.before_request
    def branding_nonce():
        g.brand_nonce = secrets.token_hex(16)

    @app.context_processor
    def branding_context():
        value = getattr(g, 'brand_preview', None)
        if value is None:
            value = current() if g.tenant else DEFAULTS.copy()
        name = value['display_name'] or (g.tenant['name'] if g.tenant else 'ProductionRunner')
        logo_url = None
        if value['logo']:
            logo_url = ('data:image/png;base64,' + base64.b64encode(value['logo']).decode()
                        if hasattr(g, 'brand_preview') else url_for('brand_logo'))
        return dict(branding=value, brand_name=name, brand_logo_url=logo_url,
                    brand_foreground=foreground)

    @app.after_request
    def branding_policy(response):
        # Scope inline styles to our generated theme; user-supplied CSS is never accepted.
        policy = response.headers.get('Content-Security-Policy')
        if not policy:
            extra = ' data:' if request.endpoint == 'admin_branding' else ''
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; style-src 'self' 'nonce-" + getattr(g, 'brand_nonce', '') +
                "'; img-src 'self'" + extra + "; script-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'self'")
        return response

    @app.get('/branding/logo')
    @roles_required()
    def brand_logo():
        logo = current()['logo']
        if not logo:
            abort(404)
        return Response(logo, mimetype='image/png')

    @app.route('/admin/organization/branding', methods=['GET', 'POST'])
    @roles_required('admin')
    def admin_branding():
        value = current()
        preview = False
        if request.method == 'POST':
            action = request.form.get('action')
            if action not in {'preview', 'save', 'reset'}:
                abort(400, 'Choose preview, save or reset.')
            proposed = DEFAULTS.copy() if action == 'reset' else value.copy()
            if action != 'reset':
                proposed['display_name'] = request.form.get('display_name', '').strip()
                if len(proposed['display_name']) > 160 or any(ord(c) < 32 for c in proposed['display_name']):
                    abort(400, 'Display name must be at most 160 characters without control characters.')
                for key in ('primary_color', 'accent_color'):
                    color = request.form.get(key, '').lower()
                    if not re.fullmatch(r'#[0-9a-f]{6}', color):
                        abort(400, 'Use a six-digit hex color.')
                    proposed[key] = color
                upload = request.files.get('logo')
                if request.form.get('remove_logo') == 'yes':
                    proposed['logo'] = None
                elif upload and upload.filename:
                    proposed['logo'] = normalize_logo(upload.stream.read(MAX_LOGO + 1))
                elif request.form.get('preview_logo'):
                    try:
                        data = base64.b64decode(request.form['preview_logo'], validate=True)
                    except (ValueError, binascii.Error):
                        abort(400, 'Invalid preview logo. Upload it again.')
                    proposed['logo'] = normalize_logo(data)
            if action == 'preview':
                proposed['version'] = request.form.get('version', '')
                g.brand_preview = proposed
                value, preview = proposed, True
            else:
                reason = request.form.get('reason', '').strip()
                if not reason or len(reason) > 500:
                    abort(400, 'Provide a reason of 1–500 characters.')
                with db():
                    db().execute('BEGIN IMMEDIATE')
                    member = db().execute('SELECT roles FROM memberships WHERE tenant_id=? AND user_id=?',
                                          (g.tenant['id'], g.user['id'])).fetchone()
                    if not member or 'admin' not in member['roles'].split(',') or epoch(db(), g.tenant['id'], g.user['id']) != session.get('membership_epoch', 0):
                        abort(403, 'Your access changed. Sign in again.')
                    old = current()
                    if request.form.get('version') != str(old['version']):
                        abort(409, 'Branding changed. Reload before saving again.')
                    def summary(v):
                        return {**{k: v[k] for k in ('display_name', 'primary_color', 'accent_color')},
                                'logo_sha256': hashlib.sha256(v['logo']).hexdigest() if v['logo'] else None}
                    before, after = summary(old), summary(proposed)
                    if before != after:
                        db().execute('INSERT INTO organization_branding(tenant_id,display_name,primary_color,accent_color,logo,version) '
                                     'VALUES(?,?,?,?,?,1) ON CONFLICT(tenant_id) DO UPDATE SET display_name=excluded.display_name,'
                                     'primary_color=excluded.primary_color,accent_color=excluded.accent_color,logo=excluded.logo,version=version+1',
                                     (g.tenant['id'], proposed['display_name'], proposed['primary_color'], proposed['accent_color'], proposed['logo']))
                        audit(db(), g.tenant['id'], g.user['id'], 'branding_reset' if action == 'reset' else 'branding_updated',
                              f"user:{g.user['id']}", app.config['AUTH_CLOCK'](), json.dumps(dict(reason=reason, before=before, after=after)))
                return redirect(url_for('admin_branding', saved='1'))
        return render_template('admin_branding.html', settings=value, preview=preview,
                               preview_logo=base64.b64encode(value['logo']).decode() if preview and value['logo'] else '',
                               saved=request.args.get('saved') == '1')
