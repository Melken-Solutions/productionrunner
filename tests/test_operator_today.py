from test_workflow import app,post,rows,sign_in
from test_lots import setup,tv
from test_primary_station import assign,admin_client


def test_operator_lands_on_today_with_station_and_daily_actions(app):
    c,tid,units,ops=setup(app,total=100)
    station=rows(app,'SELECT station_id FROM operation_assignments ORDER BY operation_id')[0][0]
    assert post(c,f'/tickets/{tid}/calendar',version=tv(app,tid),day='2026-11-13',reason='Plan production').status_code==302
    assert assign(admin_client(app),station).status_code==302
    c.get('/login')
    result=post(c,'/login',username='operator',tenant='alpha',password='test-password-123')
    assert result.location=='/today'
    page=c.get('/today?day=2026-11-13')
    assert page.status_code==200 and b'<h1>Today</h1>' in page.data
    assert b'Planned today' in page.data and b'HOT jobs' in page.data and b'Working now' in page.data
    import re
    def planned(body):
        return re.search(rb'<section class="station-group"><h2>Planned today.*?</section>',body,re.S).group()
    assert planned(page.data).count(b'data-traveler=')==2
    assert b'data-traveler=' not in planned(c.get('/today?day=2026-11-14').data)
    assert b'New production request' not in page.data
    assert b'/today?station=' in page.data
    assert c.get('/today?day=invalid').status_code==400
    sign_in(c,'viewer');assert c.get('/today').status_code==403
    sign_in(c,'planner');assert b'New production request' in c.get('/today').data
