"""Server-only immutable object storage. Authorization stays in tenant-scoped routes."""
import hashlib
from http.client import HTTPException
import os
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

from flask import abort, current_app

LIMITS = {'pdf': (10*1024*1024, 'application/pdf'), 'dxf': (20*1024*1024, 'application/octet-stream')}


class StorageError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class SupabaseStorage:
    def __init__(self, url, key, bucket):
        if not re.fullmatch(r'https://[a-z0-9]{20}\.supabase\.co', url):
            raise ValueError('Storage requires the exact HTTPS Supabase project origin.')
        if not key or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', bucket):
            raise ValueError('Storage requires a server secret and valid bucket.')
        self.base, self.key, self.bucket = url+'/storage/v1/object/', key, bucket
        self.opener = build_opener(NoRedirect())

    def put(self, key, data, mime):
        self._request('POST', self.bucket+'/'+key, data, mime, 4096)

    def get(self, key, limit):
        return self._request('GET', 'authenticated/'+self.bucket+'/'+key, None, 'application/octet-stream', limit)

    def _request(self, method, path, data, mime, limit):
        req = Request(self.base+path, data=data, method=method,
                      headers={'apikey':self.key, 'Content-Type':mime, 'x-upsert':'false'})
        try:
            with self.opener.open(req, timeout=30) as response:
                result = response.read(limit+1)
                if len(result)>limit:
                    raise StorageError('Storage response exceeded its expected size.')
                return result
        except (HTTPError, URLError, OSError, HTTPException):
            # Provider bodies/URLs/headers can contain sensitive details; never forward them.
            raise StorageError('Private file storage is temporarily unavailable.') from None


def configure(app):
    backend=os.environ.get('PR_FILE_STORAGE','database')
    if backend not in {'database','supabase'}:
        raise ValueError('PR_FILE_STORAGE must be database or supabase.')
    if backend=='supabase':
        from .database import is_postgres
        if not is_postgres(app.config['DATABASE']):
            raise ValueError('Supabase file storage requires PostgreSQL; local SQLite keeps embedded files.')
        app.extensions['file_storage']=SupabaseStorage(os.environ.get('PR_SUPABASE_URL',''),
            os.environ.get('PR_SUPABASE_SECRET_KEY',''),os.environ.get('PR_STORAGE_BUCKET',''))


def prepare(tenant, kind, data):
    """Upload before opening a database write transaction; return explicit file metadata.

    Failed DB commits may leave unreferenced objects. Never blindly delete them on
    ambiguous failures. Reconcile against immutable catalog references before cleanup.
    """
    if data is None:return (None,None,None)
    limit,mime=LIMITS[kind]
    if not data or len(data)>limit:abort(400,'Invalid file size.')
    store=current_app.extensions.get('file_storage')
    if store is None:return (data,None,None)
    key=f'{int(tenant)}/{secrets.token_hex(24)}.{kind}'
    try:store.put(key,data,mime)
    except StorageError:abort(503,'File storage is unavailable. No catalog changes were saved; try again.')
    return (None,key,len(data))


def read(tenant, kind, record, field):
    key=record['object_key']
    if key is None:
        data=record[field]
    else:
        if not re.fullmatch(str(int(tenant))+r'/[0-9a-f]{48}\.'+kind,key):
            abort(503,'File reference failed verification.')
        size=record['object_size']
        if not isinstance(size,int) or not 0<size<=LIMITS[kind][0]:abort(503,'File size failed verification.')
        store=current_app.extensions.get('file_storage')
        if store is None:abort(503,'Private file storage is not configured.')
        try:data=store.get(key,size)
        except StorageError:abort(503,'File storage is unavailable. Try again later.')
        if len(data)!=size:abort(503,'File size failed verification.')
    if data is None or hashlib.sha256(data).hexdigest()!=record['sha256']:
        abort(503,'File content failed verification.')
    return data
