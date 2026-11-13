import re
from test_workflow import app,post,rows,sign_in
from test_request_job_plan import setup,payload
from test_intake import receive,decision,convert,rv,FIELDS
from productionrunner.database import connect
from productionrunner.production_calendar import assignments,ordered,request_start
from productionrunner.station_queue import travelers


def make(app,c,plan,start,required='2026-12-01'):
    rid=receive(c,scheduled_start=start,required_date=required,**payload(plan)).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    result=convert(app,c,rid,tracking='lot',lot_size='1');assert result.status_code==302
    tid=rows(app,'SELECT ticket_id FROM intake_requests WHERE id=?',(rid,))[0][0]
    return rid,tid


def test_start_is_saved_snapshotted_and_rescheduled_with_traveler_override(app):
    c,plan=setup(app);rid,tid=make(app,c,plan,'2026-11-20')
    with connect(app.config['DATABASE']) as db:
        assert request_start(db,1,rid)=='2026-11-20'
        assert assignments(db,1)[(tid,None)]['day']=='2026-11-20'
        assert not assignments(db,2)
    assert b'value="2026-11-20"' not in c.get('/requests/new').data
    assert b'Start: 2026-11-20' in c.get('/').data
    unit=rows(app,'SELECT id FROM serial_units WHERE ticket_id=?',(tid,))[0][0]
    assert post(c,f'/tickets/{tid}/calendar',version=1,unit_id=unit,day='2026-11-21',reason='Stage next lot').status_code==302
    station=plan['steps'][0]['station_id']
    sign_in(c,'operator')
    page=c.get(f'/today?station={station}&day=2026-11-20').data
    section=re.search(rb'<section class="station-group"><h2>Planned today.*?</section>',page,re.S).group()
    assert section.count(b'data-traveler=')==1
    page=c.get(f'/today?station={station}&day=2026-11-22').data
    section=re.search(rb'<section class="station-group"><h2>Earlier scheduled work.*?</section>',page,re.S).group()
    assert section.count(b'data-traveler=')==2
    sign_in(c,'planner')
    assert b'1 units planned for today' in c.get('/today?day=2026-11-20').data
    assert b'2 units from earlier planned starts' in c.get('/today?day=2026-11-22').data
    assert post(c,f'/tickets/{tid}/calendar',version=2,day='2026-12-02',reason='Recovery schedule after missed deadline').status_code==302


def test_start_validation_edit_and_clear(app):
    c,plan=setup(app)
    for day in ['bad','2026-02-30','2026-12-02']:
        assert receive(c,scheduled_start=day,**payload(plan)).status_code==400
    assert not rows(app,'SELECT * FROM intake_requests')
    rid=receive(c,scheduled_start='2026-11-20',**payload(plan)).location.split('/')[-1]
    assert decision(app,c,rid).status_code==302
    assert post(c,f'/requests/{rid}/edit',**(FIELDS|payload(plan)|dict(scheduled_start='',version=rv(app,rid),reason='Remove schedule'))).status_code==302
    assert rows(app,'SELECT status FROM intake_requests')[0][0]=='preparing'
    with connect(app.config['DATABASE']) as db:assert request_start(db,1,rid)==''


def test_queue_orders_hot_then_start_then_required(app):
    c,plan=setup(app)
    _,later=make(app,c,plan,'2026-11-22')
    _,early=make(app,c,plan,'2026-11-20','2026-11-30')
    _,due=make(app,c,plan,'2026-11-20','2026-11-25')
    _,unscheduled=make(app,c,plan,'')
    with connect(app.config['DATABASE']) as db:
        tickets=db.execute('SELECT t.*,p.priority AS hot_priority FROM tickets t LEFT JOIN ticket_priorities p ON p.ticket_id=t.id AND p.tenant_id=t.tenant_id WHERE t.tenant_id=1').fetchall()
        assert [t['id'] for t in ordered(db,1,tickets)]==[due,early,later,unscheduled]
        assert list(dict.fromkeys(t['ticket_id'] for t in travelers(db,1,plan['steps'][0]['station_id'])))==[due,early,later,unscheduled]
    assert post(c,f'/tickets/{later}/priority',version=1,priority='1',action='hot',reason='Urgent').status_code==302
    with connect(app.config['DATABASE']) as db:assert travelers(db,1,plan['steps'][0]['station_id'])[0]['ticket_id']==later
