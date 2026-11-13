import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_workflow import app, post, rows, sign_in, prepared, complete_route
from test_serials import prepared_units, complete, inspect


def held_batch(app):
    client, path = prepared(app)
    complete_route(app, client, path)
    sign_in(client, 'inspector')
    assert post(client, path + '/inspect', version='3', result='fail', notes='Outside tolerance').status_code == 302
    sign_in(client, 'admin')
    return client, path


def authorize(client, path, version=4, **overrides):
    data = dict(version=str(version), reason='Correct failed dimension', instructions='Deburr and finish to drawing', criteria='Measure against drawing tolerance')
    data.update(overrides)
    return post(client, path + '/rework', **data)


def repair(client, path, version=1):
    return post(client, path + '/complete', version=str(version), confirm='yes', notes='Deburred to drawing')


def reinspect(client, path, result='pass', version=2):
    return post(client, path + '/inspect', version=str(version), result=result, notes='Measured against criteria')


def cancel(client, path, version=1):
    return post(client, path + '/cancel', version=str(version), reason='Customer cancelled', confirm='yes')


def test_batch_rework_preserves_original_and_requires_independence(app):
    client, path = held_batch(app)
    response = authorize(client, path)
    assert response.status_code == 302
    case = response.location
    assert b'Authorize' not in client.get(path).data  # Only one active case.
    assert b'Acceptance criteria' in client.get(case).data
    assert repair(client, case).status_code == 403
    sign_in(client, 'inspector')
    assert reinspect(client, case, version=1).status_code == 409
    sign_in(client, 'operator')
    assert repair(client, case).status_code == 302
    assert repair(client, case).status_code == 409
    assert reinspect(client, case).status_code == 403
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'held'
    sign_in(client, 'inspector')
    assert reinspect(client, case).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'accepted'
    assert rows(app, 'SELECT result FROM inspections')[0][0] == 'fail'
    assert reinspect(client, case, version=3).status_code == 409
    assert len(rows(app, "SELECT * FROM events WHERE action LIKE 'rework_%'")) == 3
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE rework_cases SET inspection_notes='overwritten'")


def test_failed_rework_can_have_another_round_without_overwriting(app):
    client, path = held_batch(app)
    first = authorize(client, path).location
    sign_in(client, 'operator')
    assert repair(client, first).status_code == 302
    sign_in(client, 'inspector')
    assert reinspect(client, first, 'fail').status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'held'
    sign_in(client, 'admin')
    second = authorize(client, path, version=7)
    assert second.status_code == 302 and second.location != first
    assert [r[0] for r in rows(app, 'SELECT result FROM rework_cases ORDER BY id')] == ['fail', None]


def test_serial_rework_changes_only_target_and_aggregates_parent(app):
    client, ticket, units, ops = prepared_units(app)
    sign_in(client, 'operator')
    for unit in units:
        for version, op in enumerate(ops, 1):
            assert complete(client, unit, op, version).status_code == 302
    sign_in(client, 'inspector')
    assert inspect(client, units[0], 'fail').status_code == 302
    assert inspect(client, units[1]).status_code == 302
    sign_in(client, 'admin')
    path = '/tickets/' + ticket
    assert authorize(client, path, version=7).status_code == 404
    case = authorize(client, path, version=7, unit_id=units[0]['id']).location
    assert case
    assert b'Authorize rework' not in client.get(path).data
    assert b'Rework history' in client.get('/units/' + units[0]['id']).data
    sign_in(client, 'operator')
    assert repair(client, case).status_code == 302
    sign_in(client, 'inspector')
    assert reinspect(client, case).status_code == 302
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'accepted'
    assert [r[0] for r in rows(app, 'SELECT state FROM serial_units ORDER BY serial')] == ['accepted', 'accepted']
    assert rows(app, 'SELECT version FROM serial_units WHERE id=?', (units[1]['id'],))[0][0] == 4
    assert rows(app, 'SELECT result FROM unit_inspections WHERE unit_id=?', (units[0]['id'],))[0][0] == 'fail'
    assert len(rows(app, 'SELECT * FROM unit_event_links WHERE unit_id=?', (units[0]['id'],))) == 6


def test_cancel_blocks_batch_and_labels_queue_and_print(app):
    client, path = prepared(app)
    assert cancel(client, path).status_code == 403
    sign_in(client, 'admin')
    assert cancel(client, path, version=9).status_code == 409
    assert post(client, path + '/cancel', version='1', reason='Customer cancelled').status_code == 400
    assert cancel(client, path).status_code == 302
    assert cancel(client, path, version=2).status_code == 409
    for suffix in ['', '/traveler']:
        assert b'CANCELLED' in client.get(path + suffix).data
    assert b'cancelled' in client.get('/').data
    sign_in(client, 'operator')
    assert post(client, path + '/complete', version='2', operation_id='1', confirm='yes').status_code == 409
    sign_in(client, 'inspector')
    assert post(client, path + '/inspect', version='2', result='pass', notes='No').status_code == 409


def test_cancel_blocks_units_and_open_rework(app):
    client, ticket, units, ops = prepared_units(app)
    sign_in(client, 'operator')
    for version, op in enumerate(ops, 1):
        assert complete(client, units[0], op, version).status_code == 302
    sign_in(client, 'inspector')
    assert inspect(client, units[0], 'fail').status_code == 302
    sign_in(client, 'admin')
    path = '/tickets/' + ticket
    case = authorize(client, path, unit_id=units[0]['id']).location
    assert cancel(client, path, version=5).status_code == 302
    assert b'CANCELLED' in client.get(case).data
    assert b'CANCELLED' in client.get('/units/' + units[1]['id'] + '/traveler').data
    sign_in(client, 'operator')
    assert repair(client, case).status_code == 409
    assert complete(client, units[1], ops[0], 1).status_code == 409


def test_cross_tenant_rework_and_cancellation_denied(app):
    client, path = held_batch(app)
    case = authorize(client, path).location
    runner = app.test_cli_runner()
    assert runner.invoke(args=['add-tenant', 'beta', '--name', 'Beta']).exit_code == 0
    assert runner.invoke(args=['set-membership', 'admin', '--tenant', 'beta', '--roles', 'admin,operator,inspector']).exit_code == 0
    sign_in(client, 'admin', tenant='beta')
    assert client.get(case).status_code == 404
    assert repair(client, case).status_code == 404
    assert reinspect(client, case).status_code == 404
    assert cancel(client, path, version=5).status_code == 404
    assert authorize(client, path, version=5).status_code == 404


def test_concurrent_rework_authorization_records_once(app):
    first, path = held_batch(app)
    second = app.test_client()
    sign_in(second, 'admin')
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda c: authorize(c, path).status_code, [first, second])) == [302, 409]
    assert len(rows(app, 'SELECT * FROM rework_cases')) == 1


def test_cancel_audit_failure_rolls_back(app):
    client, path = prepared(app)
    sign_in(client, 'admin')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_cancel BEFORE INSERT ON events WHEN NEW.action='ticket_cancelled' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        cancel(client, path)
    assert not rows(app, 'SELECT * FROM ticket_cancellations')
    assert rows(app, 'SELECT version FROM tickets')[0][0] == 1


def test_reinspection_failure_rolls_back_state_and_evidence(app):
    client, path = held_batch(app)
    case = authorize(client, path).location
    sign_in(client, 'operator')
    assert repair(client, case).status_code == 302
    sign_in(client, 'inspector')
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("CREATE TRIGGER fail_release BEFORE INSERT ON events WHEN NEW.action='rework_inspection_pass' BEGIN SELECT RAISE(ABORT,'test'); END")
    with pytest.raises(sqlite3.IntegrityError):
        reinspect(client, case)
    assert rows(app, 'SELECT state FROM tickets')[0][0] == 'held'
    assert rows(app, 'SELECT result FROM rework_cases')[0][0] is None
    assert rows(app, 'SELECT result FROM inspections')[0][0] == 'fail'


def test_prior_repair_contributor_cannot_inspect_later_round(app):
    client, path = held_batch(app)
    first = authorize(client, path).location
    runner = app.test_cli_runner()
    assert runner.invoke(args=['set-membership', 'viewer', '--tenant', 'alpha', '--roles', 'operator,inspector']).exit_code == 0
    sign_in(client, 'viewer')
    assert repair(client, first).status_code == 302
    sign_in(client, 'inspector')
    assert reinspect(client, first, 'fail').status_code == 302
    sign_in(client, 'admin')
    second = authorize(client, path, version=7).location
    sign_in(client, 'operator')
    assert repair(client, second).status_code == 302
    sign_in(client, 'viewer')
    assert reinspect(client, second).status_code == 403
    sign_in(client, 'inspector')
    assert reinspect(client, second).status_code == 302
    sign_in(client, 'admin')
    assert cancel(client, path, version=10).status_code == 409


def test_cancel_and_unit_completion_serialize(app):
    client, ticket, units, ops = prepared_units(app)
    sign_in(client, 'admin')
    operator = app.test_client()
    sign_in(operator, 'operator')
    path = '/tickets/' + ticket
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancelled = pool.submit(cancel, client, path)
        completed = pool.submit(complete, operator, units[0], ops[0], 1)
        results = (cancelled.result().status_code, completed.result().status_code)
    assert results in {(302, 409), (409, 302)}
    assert bool(rows(app, 'SELECT * FROM ticket_cancellations')) != bool(rows(app, 'SELECT * FROM unit_completions'))
