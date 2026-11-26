"""Opt-in restore/import rehearsal. Requires a disposable local PostgreSQL DB."""
import os
import sqlite3
from urllib.parse import urlsplit
import pytest
from productionrunner import create_app
from productionrunner.database import migrate,connect
from productionrunner.data_migration import import_sqlite
from test_workflow import app,prepared,complete_route,sign_in,post,rows


@pytest.fixture
def destination():
    url=os.environ.get('PR_MIGRATION_TEST_URL')
    if not url:pytest.skip('Set PR_MIGRATION_TEST_URL to a disposable local database')
    parsed=urlsplit(url)
    assert parsed.hostname in {'localhost','127.0.0.1'} and parsed.path.endswith('_test')
    import psycopg
    with psycopg.connect(url,autocommit=True) as conn:conn.execute('DROP SCHEMA IF EXISTS productionrunner CASCADE')
    migrate(url);migrate(url)
    return url


def test_verified_import_preserves_complete_job_and_auth(app,destination):
    c,path=prepared(app);complete_route(app,c,path);sign_in(c,'inspector')
    version=rows(app,'SELECT version FROM tickets')[0][0]
    assert post(c,path+'/inspect',version=str(version),result='pass',notes='Verified dimensions').status_code==302
    counts=import_sqlite(app.config['DATABASE'],destination)
    assert counts['tickets']==1 and counts['events']>5
    pg=create_app({'TESTING':True,'SECRET_KEY':'test-only','DATABASE':destination})
    pc=pg.test_client();sign_in(pc,'planner')
    assert pc.get(path).status_code==200
    assert pc.get('/health/ready').json=={'status':'ready'}
    with connect(destination) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("UPDATE events SET detail='rewrite'")
        created=conn.execute('INSERT INTO tenants(slug,name) VALUES(?,?)',('beta','Beta'))
        assert created.lastrowid>1
    with pytest.raises(ValueError,match='not empty'):import_sqlite(app.config['DATABASE'],destination)


def test_runtime_role_cannot_disable_history_or_change_schema(app,destination):
    import psycopg
    prepared(app);import_sqlite(app.config['DATABASE'],destination)
    with psycopg.connect(destination,autocommit=True) as conn:
        conn.execute('SET ROLE productionrunner_runtime')
        assert conn.execute('SELECT count(*) FROM productionrunner.tickets').fetchone()[0]==1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('ALTER TABLE productionrunner.events DISABLE TRIGGER USER')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('CREATE TABLE productionrunner.forbidden(id integer)')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute('DELETE FROM productionrunner.schema_migrations')
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute("UPDATE productionrunner.events SET detail='rewrite'")
    runtime=destination+'?options=-c%20role%3Dproductionrunner_runtime'
    a=create_app({'TESTING':True,'SECRET_KEY':'test-only','DATABASE':runtime})
    c=a.test_client();sign_in(c,'planner')
    assert c.get('/').status_code==200
