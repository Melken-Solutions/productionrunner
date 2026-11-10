"""Consistent reason choices, normalized into existing human-readable audit fields."""
from flask import abort,request
from werkzeug.datastructures import ImmutableMultiDict
GROUPS={
 'change':['Initial setup','Correct information','Update requirements','Improve process','Periodic review','Remove obsolete configuration'],
 'access':['New team member','Role or responsibility change','Credential maintenance','Remove access','Correct account information'],
 'planning':['Update customer request','Change production route','Change material requirements','Adjust schedule or priority','Review and approve plan','Correct planning information','Customer cancellation','Withdraw approval'],
 'catalog':['Add approved item','Correct catalog information','Replace incorrect file','Update revision','Retire obsolete entry','Restore needed entry'],
 'shipping':['Add approved destination','Correct delivery address','Correct quantity or box information','Change packaging requirements','Correct labels or manifest','Review shipping requirements'],
 'quality':['Dimensional nonconformance','Surface damage or finish issue','Missing or incomplete feature','Incorrect material','Quantity discrepancy','Rework required','Production interruption','Split production run','Scrap authorization','Customer cancellation','Duplicate job'],
 'timing':['Missed start or stop','Incorrect time entry','Operator handoff','Equipment interruption','Administrative correction'],
}
INTERRUPTIONS=['Machine breakdown','Tooling change','Material shortage','Waiting for inspection','Drawing clarification','DXF issue','Quality hold','Operator unavailable','Maintenance','Power interruption']


def register(app):
    @app.context_processor
    def context():return dict(reason_groups=GROUPS,interruption_choices=INTERRUPTIONS)

    @app.before_request
    def normalize_reasons():
        if request.method!='POST':return
        original=request.form
        if 'reason_choice' not in original and 'interruption_choice' not in original:return
        data=original.copy()
        if 'reason_choice' in original:
            if len(original.getlist('reason_choice'))!=1:abort(400,'Choose one reason.')
            value=original.get('reason_choice','');note=original.get('reason_notes','').strip()
            if len(note)>400:abort(400,'Additional details must be 400 characters or fewer.')
            if not value:
                if note:abort(400,'Choose a reason for these details.')
                data['reason']=''
            else:
                group,separator,choice=value.partition(':')
                if not separator or group not in GROUPS or choice not in GROUPS[group]+['Other']:abort(400,'Choose a listed reason.')
                if choice=='Other' and not note:abort(400,'Explain the reason when choosing Other.')
                data['reason']=choice+(' — '+note if note else '')
        if 'interruption_choice' in original:
            selected=original.get('interruption_choice','')
            if selected=='Other':
                selected=original.get('interruption_other','').strip()
                if not 1<=len(selected)<=120:abort(400,'Enter the new interruption reason (up to 120 characters).')
            elif selected not in INTERRUPTIONS:abort(400,'Choose an interruption reason.')
            data['name']=selected
        request.form=ImmutableMultiDict(data)
