import re
import sqlite3
from test_workflow import app, sign_in, post, rows


def invite(c,username='newperson',roles=None):
    response=post(c,'/admin/invitations',username=username,roles=roles or ['planner','operator'])
    assert response.status_code==200
    return re.search(rb'/join#([^"<]+)',response.data)[1].decode()


def test_invitation_activation_single_use_and_credentials(app):
    admin=app.test_client();sign_in(admin,'admin');token=invite(admin)
    entry=rows(app,'SELECT * FROM employee_invitations')[0]
    assert token not in entry['token_hash']
    assert token not in str([tuple(r) for r in rows(app,'SELECT * FROM auth_events')])
    assert not rows(app,"SELECT * FROM users WHERE username='newperson'")
    c=app.test_client();c.get('/join')
    assert b'Alpha' in post(c,'/join',token=token).data
    assert post(c,'/join',token=token,action='activate',password='short',password_confirm='short').status_code==400
    assert post(c,'/join',token=token,action='activate',password='my-new-password',password_confirm='my-new-password',pin='123456',pin_confirm='123456',roles='admin').status_code==200
    assert rows(app,"SELECT roles FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.username='newperson'")[0][0]=='operator,planner'
    assert post(c,'/join',token=token).status_code==410
    assert post(c,'/login',tenant='alpha',username='newperson',password='my-new-password').status_code==302
    assert post(c,'/logout').status_code==302
    c.get('/login')
    assert post(c,'/login',tenant='alpha',username='newperson',method='pin',pin='123456').status_code==302


def test_expiry_revocation_scoping_and_roles(app):
    c=app.test_client();sign_in(c,'admin');token=invite(c)
    assert post(c,'/admin/invitations',username='newperson',roles=['operator']).status_code==409
    assert post(c,'/admin/invitations',username='planner',roles=['operator']).status_code==409
    assert post(c,'/admin/invitations',username='badrole',roles=['developer']).status_code==400
    assert post(c,'/admin/invitations',username='badmix',roles=['display','admin']).status_code==400
    with sqlite3.connect(app.config['DATABASE']) as conn:
        conn.execute("INSERT INTO tenants(id,slug,name) VALUES(2,'beta','Beta')")
        conn.execute("INSERT INTO memberships SELECT 2,id,'admin' FROM users WHERE username='admin'")
    sign_in(c,'admin','beta')
    assert b'newperson' not in c.get('/admin/invitations').data
    assert post(c,'/admin/invitations',action='revoke',invitation_id='1').status_code==404
    sign_in(c,'admin');assert post(c,'/admin/invitations',action='revoke',invitation_id='1').status_code==200
    guest=app.test_client();guest.get('/join');assert post(guest,'/join',token=token).status_code==410
    token=invite(c,'expired');app.config['AUTH_CLOCK']=lambda:99999999999
    assert post(guest,'/join',token=token).status_code==410
    sign_in(c,'planner');assert c.get('/admin/invitations').status_code==403
    assert c.get('/admin/getting-started').status_code==403
    assert guest.post('/join',data={'token':token}).status_code==400


def test_checklist_persistence_optional_steps_and_no_production_mutation(app):
    c=app.test_client();sign_in(c,'admin')
    assert b'0 of 8' in c.get('/admin/getting-started').data
    assert post(c,'/admin/getting-started',step='profile',status='later').status_code==400
    assert post(c,'/admin/getting-started',step='profile',status='reviewed').status_code==302
    assert post(c,'/admin/getting-started',step='brand',status='later').status_code==302
    assert b'1 of 8' in c.get('/admin/getting-started').data
    assert not rows(app,'SELECT * FROM tickets')
    assert rows(app,"SELECT * FROM auth_events WHERE action='onboarding_step_updated'")
    assert post(c,'/admin/getting-started',step='profile',status='todo').status_code==302
    assert b'0 of 8' in c.get('/admin/getting-started').data
