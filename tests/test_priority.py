import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_workflow import app, sign_in, post, create_ticket, rows


def setup_queue(app):
    client = app.test_client()
    sign_in(client, 'planner')
    ids = [create_ticket(client, pn=pn).location.split('/')[-1] for pn in ['NORMAL', 'HOT-A', 'HOT-B']]
    return client, ids


def priority(client, ticket, rank='10', version='1', action='hot'):
    return post(client, '/tickets/' + ticket + '/priority', priority=rank, version=version, action=action)


def test_hot_order_rank_tie_age_and_clear(app):
    client, ids = setup_queue(app)
    assert priority(client, ids[1]).status_code == 302
    assert priority(client, ids[2]).status_code == 302
    page = client.get('/').data
    assert page.index(b'HOT-A') < page.index(b'HOT-B') < page.index(b'NORMAL')
    since = rows(app, 'SELECT hot_since FROM ticket_priorities WHERE ticket_id=?', (ids[2],))[0][0]
    assert priority(client, ids[2], '1', '2').status_code == 302
    page = client.get('/').data
    assert page.index(b'HOT-B') < page.index(b'HOT-A') < page.index(b'NORMAL')
    assert rows(app, 'SELECT hot_since FROM ticket_priorities WHERE ticket_id=?', (ids[2],))[0][0] == since
    assert priority(client, ids[2], version='3', action='normal').status_code == 302
    assert client.get('/').data.index(b'HOT-A') < client.get('/').data.index(b'HOT-B')
    events = rows(app, "SELECT detail FROM events WHERE action='priority_changed'")
    assert [r[0] for r in events] == ['Normal → HOT 10', 'Normal → HOT 10', 'HOT 10 → HOT 1', 'HOT 1 → Normal']


@pytest.mark.parametrize('rank', ['0', '100', '-1', '1.5', '', 'abc'])
def test_invalid_priority_does_not_write(app, rank):
    client, ids = setup_queue(app)
    assert priority(client, ids[0], rank).status_code == 400
    assert not rows(app, 'SELECT * FROM ticket_priorities')
    assert len(rows(app, 'SELECT * FROM events')) == 3


def test_permissions_tenant_csrf_and_stale_change(app):
    client, ids = setup_queue(app)
    for user in ['operator', 'inspector', 'viewer']:
        sign_in(client, user)
        assert priority(client, ids[0]).status_code == 403
        assert b'Mark HOT' not in client.get('/').data
    sign_in(client, 'admin')
    assert client.post('/tickets/' + ids[0] + '/priority', data={'action': 'hot'}).status_code == 400
    assert priority(client, ids[0]).status_code == 302
    assert priority(client, ids[0], '1').status_code == 409
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'admin', '--tenant', 'beta', '--roles', 'admin']).exit_code == 0
    sign_in(client, 'admin', 'beta')
    assert priority(client, ids[0], '1', '2').status_code == 404
    assert b'NORMAL' not in client.get('/').data


def test_priority_change_is_atomic_and_concurrent_safe(app):
    client, ids = setup_queue(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER reject_priority BEFORE INSERT ON events WHEN NEW.action='priority_changed' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        priority(client, ids[0])
    assert not rows(app, 'SELECT * FROM ticket_priorities')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute('DROP TRIGGER reject_priority')
    clients = [app.test_client(), app.test_client()]
    for c in clients:
        sign_in(c, 'planner')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda c: priority(c, ids[0]).status_code, clients))
    assert sorted(results) == [302, 409]
    assert len(rows(app, "SELECT * FROM events WHERE action='priority_changed'")) == 1


def test_serial_unit_inherits_priority_without_changing_progress(app):
    client = app.test_client()
    sign_in(client, 'planner')
    ticket = create_ticket(client, tracking='serial', serials='S1\nS2').location.split('/')[-1]
    unit = rows(app, 'SELECT id FROM serial_units')[0][0]
    assert priority(client, ticket, '2').status_code == 302
    assert b'HOT 2' in client.get('/tickets/' + ticket).data
    assert b'HOT 2' in client.get('/units/' + unit).data
    assert all(r['state'] == 'ready' and r['version'] == 1 for r in rows(app, 'SELECT * FROM serial_units'))
    assert app.test_cli_runner().invoke(args=['init-db']).exit_code == 0
    assert rows(app, 'SELECT priority FROM ticket_priorities')[0][0] == 2
