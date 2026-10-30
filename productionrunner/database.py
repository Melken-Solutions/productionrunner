"""Small DB-API bridge: explicit PostgreSQL transactions, existing SQLite local mode."""
import re
import sqlite3
from pathlib import Path
from decimal import Decimal

WRITE_LOCK = 735140219

def is_postgres(value):
    return str(value).startswith(('postgresql://', 'postgres://'))

class Row:
    def __init__(self, names, values): self.names, self.values = names, values
    def keys(self): return self.names
    def __getitem__(self, key):
        try:return self.values[key if isinstance(key, (int,slice)) else self.names.index(key)]
        except ValueError:raise IndexError(key) from None
    def __iter__(self): return iter(self.values)
    def __len__(self): return len(self.values)
    def __eq__(self,other): return isinstance(other,Row) and self.names==other.names and self.values==other.values

def row_factory(cursor):
    names=[c.name for c in cursor.description] if cursor.description else []
    return lambda values: Row(names,tuple(int(v) if isinstance(v,Decimal) and v.is_finite() and v==v.to_integral_value() else v for v in values))

class Cursor:
    def __init__(self,cursor,lastrowid=None): self.cursor,self.lastrowid=cursor,lastrowid
    @property
    def rowcount(self): return self.cursor.rowcount
    def fetchone(self): return self.cursor.fetchone()
    def fetchall(self): return self.cursor.fetchall()
    def __iter__(self): return iter(self.cursor)

class Postgres:
    """Preserve MVP serialized writes across workers; reads remain concurrent.

    This intentionally uses one transaction-scoped advisory lock for writes,
    matching the original SQLite safety model. Replace with scoped locks only
    after concurrency tests cover each workflow; it is a pilot throughput limit.
    """
    def __init__(self,url):
        import psycopg
        self.raw=psycopg.connect(url,autocommit=True,prepare_threshold=None,row_factory=row_factory,connect_timeout=10)
        self.raw.execute('SET search_path TO productionrunner, pg_catalog')
        self.raw.execute("SET statement_timeout TO '30s'")
        self.raw.execute("SET idle_in_transaction_session_timeout TO '60s'")
        self.identities={r[0] for r in self.raw.execute("SELECT table_name FROM information_schema.columns WHERE table_schema='productionrunner' AND column_name='id' AND is_identity='YES'")}
    @property
    def in_transaction(self): return self.raw.info.transaction_status != 0
    def begin(self):
        if not self.in_transaction:
            self.raw.execute('BEGIN')
            self.raw.execute('SELECT pg_advisory_xact_lock(%s)',(WRITE_LOCK,))
    def execute(self,sql,params=()):
        import psycopg
        sql=sql.strip().rstrip(';')
        if sql.upper()=='BEGIN IMMEDIATE':
            self.begin();return Cursor(self.raw.cursor())
        if re.match(r'^(INSERT|UPDATE|DELETE)\b',sql,re.I):self.begin()
        ignore=bool(re.match(r'INSERT OR IGNORE',sql,re.I))
        if ignore:sql=re.sub('INSERT OR IGNORE','INSERT',sql,count=1,flags=re.I)+' ON CONFLICT DO NOTHING'
        match=re.match(r'INSERT INTO (\w+)',sql,re.I)
        if match and 'DO UPDATE SET' in sql.upper():
            table=match[1]
            left,right=re.split(r'(?i)(?<=DO UPDATE SET)',sql,maxsplit=1)
            # Qualify existing counters; excluded.* already names the incoming row.
            right=re.sub(r'(?<![.\w])(version|session_epoch|failures)(?=\s*\+)',lambda m:table+'.'+m[1],right)
            sql=left+right
        returning=bool(match and match[1] in self.identities and 'RETURNING' not in sql.upper())
        if returning:sql+=' RETURNING id'
        # Replace positional placeholders only outside SQL string literals.
        pieces=re.split("('(?:''|[^'])*')",sql)
        for i in range(0,len(pieces),2):
            pieces[i]=re.sub(r'\binstr\(', 'strpos(', pieces[i], flags=re.I)
            pieces[i]=re.sub(r'\bLIKE\b', 'ILIKE', pieces[i], flags=re.I)
            pieces[i]=re.sub(r'\bIS \?', 'IS NOT DISTINCT FROM ?', pieces[i],flags=re.I).replace('?','%s')
        sql=''.join(pieces)
        savepoint=self.in_transaction
        if savepoint:self.raw.execute('SAVEPOINT pr_statement')
        try:
            cur=self.raw.execute(sql,tuple(int(p) if isinstance(p,bool) else p for p in params) if params else None)
            last=cur.fetchone()[0] if returning and cur.rowcount else None
            if savepoint:self.raw.execute('RELEASE SAVEPOINT pr_statement')
            return Cursor(cur,last)
        except psycopg.IntegrityError as exc:
            if savepoint:
                self.raw.execute('ROLLBACK TO SAVEPOINT pr_statement')
                self.raw.execute('RELEASE SAVEPOINT pr_statement')
            raise sqlite3.IntegrityError(str(exc)) from exc
    def commit(self):self.raw.commit()
    def rollback(self):self.raw.rollback()
    def close(self):self.raw.close()
    def __enter__(self):return self
    def __exit__(self,kind,value,tb):self.rollback() if kind else self.commit()

def connect(value,**kwargs):
    if is_postgres(value):return Postgres(value)
    conn=sqlite3.connect(value,**kwargs)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn

def migrate(url):
    """Apply immutable, checksummed migration files atomically, under one lock."""
    import hashlib
    import psycopg
    root=Path(__file__).resolve().parent.parent/'supabase'/'migrations'
    with psycopg.connect(url,autocommit=True) as conn:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(%s)',(WRITE_LOCK,))
            conn.execute('CREATE SCHEMA IF NOT EXISTS productionrunner')
            conn.execute('REVOKE ALL ON SCHEMA productionrunner FROM PUBLIC')
            conn.execute('CREATE TABLE IF NOT EXISTS productionrunner.schema_migrations(name text PRIMARY KEY, sha256 text NOT NULL)')
            conn.execute('SET LOCAL search_path TO productionrunner, pg_catalog')
            for path in sorted(root.glob('*.sql')):
                data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
                old=conn.execute('SELECT sha256 FROM schema_migrations WHERE name=%s',(path.name,)).fetchone()
                if old:
                    if old[0]!=digest:raise ValueError('Previously applied migration changed: '+path.name)
                    continue
                conn.execute(data.decode(),prepare=False)
                conn.execute('INSERT INTO schema_migrations VALUES(%s,%s)',(path.name,digest))
