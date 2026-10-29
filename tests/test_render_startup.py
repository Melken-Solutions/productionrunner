import json
import pytest
from productionrunner.render_serve import configure_environment,SECRET_KEYS

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os
    monkeypatch.setattr(os,"environ",os.environ.copy())
    for key in SECRET_KEYS|{'PR_SECRET_FILE','PR_TRUSTED_HOSTS','PR_PUBLIC_ORIGIN','RENDER_EXTERNAL_HOSTNAME'}:
        monkeypatch.delenv(key,raising=False)


def test_secret_file_and_platform_hostname(tmp_path,monkeypatch):
    p=tmp_path/'secrets.json';p.write_text(json.dumps({'PR_SECRET_KEY':'test-secret','PR_DATABASE_URL':'test-database'}))
    monkeypatch.setenv('PR_SECRET_FILE',str(p))
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME','productionrunner-staging.onrender.com')
    configure_environment()
    import os
    assert os.environ['PR_SECRET_KEY']=='test-secret'
    assert os.environ['PR_TRUSTED_HOSTS']=='productionrunner-staging.onrender.com'
    assert os.environ['PR_PUBLIC_ORIGIN']=='https://productionrunner-staging.onrender.com'


@pytest.mark.parametrize('contents',['not JSON','{"PR_ENV":"development"}','{"PR_SECRET_KEY":123}','[]'])
def test_invalid_secret_file_fails_without_contents(tmp_path,monkeypatch,contents):
    p=tmp_path/'bad.json';p.write_text(contents);monkeypatch.setenv('PR_SECRET_FILE',str(p))
    with pytest.raises(ValueError,match='secret file is missing or invalid') as exc:configure_environment()
    assert contents not in str(exc.value)


def test_explicit_custom_origin_preserved(monkeypatch):
    monkeypatch.setenv('PR_TRUSTED_HOSTS','pilot.example.com')
    monkeypatch.setenv('PR_PUBLIC_ORIGIN','https://pilot.example.com')
    configure_environment()
    import os
    assert os.environ['PR_PUBLIC_ORIGIN']=='https://pilot.example.com'


@pytest.mark.parametrize('host',['','https://wrong.onrender.com','evil.com','x.onrender.com.evil.com'])
def test_invalid_platform_hostname_rejected(monkeypatch,host):
    monkeypatch.setenv('RENDER_EXTERNAL_HOSTNAME',host)
    with pytest.raises(ValueError):configure_environment()
