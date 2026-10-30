"""Opt-in PostgreSQL compatibility runs against a disposable local test database."""
import os
import sqlite3
import pytest


@pytest.fixture(autouse=True)
def postgres_test_connections(monkeypatch):
    url=os.environ.get('PR_TEST_POSTGRES_URL')
    if not url:return
    original=sqlite3.connect
    from productionrunner.database import Postgres,is_postgres
    class TestPostgres(Postgres):
        def execute(self,sql,params=()):
            if sql.upper().replace(' ','')=='PRAGMAFOREIGN_KEYS=ON':
                assert self.raw.execute('SHOW session_replication_role').fetchone()[0]=='origin'
                return None
            if postgres_failure_trigger(self,sql):return None
            import re
            match=re.fullmatch(r'DROP TRIGGER (\w+)',sql,re.I)
            if match:
                table=self.raw.execute("SELECT tgrelid::regclass::text FROM pg_trigger WHERE tgname=%s",(match[1],)).fetchone()[0]
                sql+=' ON '+table
            sql=re.sub(r"X'([0-9a-fA-F]*)'",lambda m:"decode('"+m[1]+"','hex')",sql)
            return super().execute(sql,params)
    monkeypatch.setattr(sqlite3,'connect',lambda value,*a,**kw: TestPostgres(value) if is_postgres(value) else original(value,*a,**kw))

# Failure-injection triggers use different DDL on the two database engines.
def postgres_failure_trigger(conn,sql):
    import re
    match=re.fullmatch(r"CREATE TRIGGER (\w+) BEFORE (INSERT|UPDATE|DELETE) ON (\w+)(?: WHEN (.+?))? BEGIN SELECT RAISE\(ABORT,\s*'([^']+)'\); END;?",sql,re.I)
    if not match:return False
    name,action,table,condition,message=match.groups()
    condition=condition or 'TRUE'
    conn.raw.execute(f"CREATE FUNCTION {name}_fn() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF {condition} THEN RAISE EXCEPTION '{message}' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
    conn.raw.execute(f'CREATE TRIGGER {name} BEFORE {action} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}_fn()')
    return True
