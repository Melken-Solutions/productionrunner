from io import BytesIO
import sqlite3
import pytest
from test_workflow import app,sign_in,post,rows
from test_parts import add_part,PDF
from test_intake import receive


def setup(app):
    c=app.test_client();sign_in(c,'planner');pid=add_part(c).location.split('/')[-1];return c,pid


def edit(c,pid,**kw):
    return post(c,f'/parts/{pid}/manage',**(dict(action='edit',version='0',pn='CORRECTED',revision='B',description='Correct description',reason='Correct catalog entry')|kw))


def test_unused_edit_replace_and_stale_version(app):
    c,pid=setup(app)
    r=edit(c,pid,drawing=(BytesIO(PDF+b' updated'),'new.pdf'),dxf=(BytesIO(b'new dxf'),'new.dxf'))
    assert r.status_code==302
    part=rows(app,'SELECT * FROM part_revisions WHERE id=?',(pid,))[0]
    assert part['pn']=='CORRECTED' and part['revision']=='B'
    assert c.get(f'/parts/{pid}/drawing').data==PDF+b' updated'
    assert edit(c,pid).status_code==409
    assert edit(c,pid,version='1',dxf=(BytesIO(b'replaced'),'replace.dxf')).status_code==302
    assert post(c,f'/parts/{pid}/dxf/download').data==b'replaced'
    assert len(rows(app,'SELECT * FROM part_catalog_changes'))==2


def test_used_revision_rejects_edit_in_app_and_database(app):
    c,pid=setup(app);assert receive(c).status_code==302
    assert edit(c,pid).status_code==409
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("UPDATE part_revisions SET description='bad' WHERE id=?",(pid,))
    assert b'Create replacement revision' in c.get(f'/parts/{pid}/manage').data


def test_archive_restore_hides_new_work_but_keeps_files(app):
    c,pid=setup(app)
    assert post(c,f'/parts/{pid}/manage',action='archive',version='0',reason='Duplicate').status_code==302
    assert pid.encode() not in c.get('/parts').data
    assert pid.encode() in c.get('/parts?view=removed').data
    assert pid.encode() not in c.get('/requests/new').data
    assert c.get(f'/parts/{pid}/drawing').status_code==200
    assert receive(c).status_code==409
    assert post(c,f'/parts/{pid}/manage',action='restore',version='1',reason='Needed again').status_code==302
    assert receive(c).status_code==302


def test_permissions_and_target_identity_protection(app):
    c,pid=setup(app)
    sign_in(c,'operator');assert edit(c,pid).status_code==403
    sign_in(c,'planner')
    assert receive(c,pn='CORRECTED',revision='B').status_code==302
    with sqlite3.connect(app.config['DATABASE']) as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("UPDATE part_revisions SET pn='CORRECTED',revision='B' WHERE id=?",(pid,))
    assert edit(c,pid).status_code==409
    assert edit(c,pid,drawing=(BytesIO(b'bad'),'bad.pdf')).status_code==400
    assert not rows(app,'SELECT * FROM part_catalog_changes')


def test_other_organization_cannot_manage_entry(app):
    c,pid=setup(app)
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        uid=conn.execute("SELECT id FROM users WHERE username='planner'").fetchone()[0]
        conn.execute("INSERT INTO memberships VALUES(2,?,'planner')",(uid,))
    sign_in(c,'planner',tenant='beta')
    assert c.get(f'/parts/{pid}/manage').status_code==404
    assert edit(c,pid).status_code==404
    assert post(c,f'/parts/{pid}/manage',action='archive',version='0',reason='Wrong tenant').status_code==404
    assert not rows(app,'SELECT * FROM part_catalog_changes')
