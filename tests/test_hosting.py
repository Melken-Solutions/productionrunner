import sqlite3
from pathlib import Path

import pytest
from productionrunner import create_app
from test_workflow import app, rows


def production_env(monkeypatch, tmp_path):
    for key, value in dict(PR_ENV='production', PR_DATA_DIR=str(tmp_path/'data'),
                           PR_SECRET_KEY='test-only-key-'*4, PR_HTTPS='1',
                           PR_TRUSTED_HOSTS='pilot.example.com').items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize('key,value', [('PR_DATA_DIR',''),('PR_SECRET_KEY','short'),('PR_HTTPS','0'),
                                      ('PR_TRUSTED_HOSTS',''),('PR_TRUSTED_HOSTS','https://bad'),('PR_ENV','typo')])
def test_invalid_hosting_configuration(monkeypatch, tmp_path, key, value):
    production_env(monkeypatch, tmp_path)
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        create_app()


def test_production_storage_hosts_and_cookie_settings(monkeypatch, tmp_path):
    production_env(monkeypatch, tmp_path)
    a = create_app()
    assert a.config['SESSION_COOKIE_SECURE']
    assert a.config['DATABASE'] == str(tmp_path/'data'/'productionrunner.sqlite3')
    assert not (tmp_path/'data'/'session-secret').exists()
    c = a.test_client()
    assert c.get('/health/ready', base_url='https://evil.example').status_code == 400
    assert c.get('/health/ready', base_url='https://pilot.example.com').status_code == 503
    assert not Path(a.config['DATABASE']).exists()
    assert a.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    response = c.get('/health/ready', base_url='https://pilot.example.com')
    assert response.json == {'status':'ready'}
    assert 'Set-Cookie' not in response.headers
    assert c.get('/login', base_url='https://pilot.example.com').status_code == 200
    assert create_app().config['SECRET_KEY'] == a.config['SECRET_KEY']
    with pytest.raises(ValueError):
        create_app({'DEBUG':True})


def test_readiness_schema_and_no_tenant_details(app):
    c = app.test_client()
    assert c.get('/health/ready').json == {'status':'ready'}
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('DROP TABLE organization_branding')
    result = c.get('/health/ready')
    assert result.status_code == 503 and result.json == {'status':'not_ready'}
    assert b'Alpha' not in result.data


def test_backup_preserves_records_and_refuses_overwrite(app, tmp_path):
    target = tmp_path/'snapshot.sqlite3'
    result = app.test_cli_runner().invoke(args=['backup-db',str(target)])
    assert result.exit_code == 0, result.output
    assert target.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(target) as conn:
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 5
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    before = target.read_bytes()
    assert app.test_cli_runner().invoke(args=['backup-db',str(target)]).exit_code != 0
    assert target.read_bytes() == before
    assert len(rows(app,'SELECT * FROM users')) == 5


def test_backup_failure_does_not_create_source(app, tmp_path):
    app.config['DATABASE'] = str(tmp_path/'absent.sqlite3')
    target = tmp_path/'snapshot.sqlite3'
    assert app.test_cli_runner().invoke(args=['backup-db',str(target)]).exit_code != 0
    assert not target.exists() and not Path(app.config['DATABASE']).exists()
