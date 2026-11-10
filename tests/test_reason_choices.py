import json
from io import BytesIO
from test_workflow import app,sign_in,post,rows
from test_intake import receive
from test_parts import PDF


def admin(app):
    c=app.test_client();sign_in(c,'admin');return c


def test_choice_and_optional_details_saved_in_audit(app):
    c=admin(app)
    r=post(c,'/admin/stations',name='Laser 1',reason_choice='catalog:Add approved item',reason_notes='Second production line')
    assert r.status_code==302
    detail=rows(app,'SELECT detail FROM station_events')[0][0]
    assert 'Add approved item' in detail and 'Second production line' in detail


def test_other_requires_details_and_unknown_choices_rejected(app):
    c=admin(app)
    for choice,note in [('catalog:Other',''),('catalog:Forged reason',''),('unknown:Initial setup',''),('catalog:Add approved item','x'*401)]:
        assert post(c,'/admin/stations',name='Laser',reason_choice=choice,reason_notes=note).status_code==400
    assert not rows(app,'SELECT * FROM stations')
    assert post(c,'/admin/stations',name='Laser',reason_choice='catalog:Other',reason_notes='Temporary evaluation station').status_code==302


def test_choice_cannot_bypass_roles_or_missing_required_reason(app):
    c=admin(app)
    assert post(c,'/admin/stations',name='Laser',reason_choice='').status_code==400
    sign_in(c,'operator')
    assert post(c,'/admin/stations',name='Laser',reason_choice='catalog:Add approved item').status_code==403


def test_preview_reason_control_is_optional(app):
    c=admin(app)
    h=c.get('/admin/organization/branding').data.decode()
    assert 'name="reason_choice" >' in h
    assert 'reason_notes' in h


def test_planner_upload_size_matches_displayed_limits(app):
    c=app.test_client();sign_in(c,'planner')
    assert receive(c,drawing=(BytesIO(PDF+b' '*100000),'drawing.pdf')).status_code==302


def test_add_interruption_from_choices(app):
    c=admin(app)
    assert post(c,'/admin/work',action='reason',interruption_choice='Machine breakdown',reason_choice='change:Update requirements').status_code==302
    assert rows(app,'SELECT name FROM work_reasons')[0][0]=='Machine breakdown'
    assert post(c,'/admin/work',action='reason',interruption_choice='Other',interruption_other='',reason_choice='change:Update requirements').status_code==400
    assert post(c,'/admin/work',action='reason',interruption_choice='Other',interruption_other='Waiting for crane',reason_choice='change:Update requirements').status_code==302
