"""Cross-check action receipts against independently read career calendar dates."""
import re
from collections import Counter

MONTHS=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')


def date_key(text):
    match=re.fullmatch(r'(Junior|Classic|Senior) Year (Early|Late) ([A-Z][a-z]{2})',text or '')
    if not match or match[3] not in MONTHS:return None
    return ('Junior','Classic','Senior').index(match[1])*24+MONTHS.index(match[3])*2+(match[2]=='Late')


def audit(readings,actions):
    dates={};phases={}
    for row in readings:
        text=row.get('stats',{}).get('calendar_text');key=date_key(text)
        if key is not None:dates.setdefault(key,dict(date=text,rows=[]))['rows'].append(row)
        elif text in ('Junior Year Pre-Debut','Finale Underway'):phases.setdefault(text,[]).append(row)
    dates={key:value for key,value in dates.items() if len(value['rows'])>=2}
    keys=sorted(dates);windows=[];chronology=[]
    for index,key in enumerate(keys):
        rows=dates[key]['rows'];start=min(r['source_timestamp_ms'] for r in rows)
        if index+1<len(keys):end=min(r['source_timestamp_ms'] for r in dates[keys[index+1]]['rows'])
        else:
            following=[r['source_timestamp_ms'] for r in phases.get('Finale Underway',[]) if r['source_timestamp_ms']>start]
            end=min(following) if following else max(r['source_timestamp_ms'] for r in rows)+1
        if end<=start:chronology.append(dates[key]['date'])
        matched=[a for a in actions if start<=a['source_timestamp_ms']<end]
        windows.append(dict(date=dates[key]['date'],start_ms=start,end_ms=end,
                            evidence=[rows[0]['evidence'],rows[-1]['evidence']],actions=matched,
                            status='one_action' if len(matched)==1 else 'missing_action' if not matched else 'multiple_actions'))
    missing_dates=[n for n in range(keys[0],keys[-1]+1) if n not in dates] if keys else []
    phase_actions={}
    if keys and phases.get('Junior Year Pre-Debut'):
        start=min(r['source_timestamp_ms'] for r in phases['Junior Year Pre-Debut']);end=windows[0]['start_ms']
        phase_actions['pre_debut']=[a for a in actions if start<=a['source_timestamp_ms']<end]
    if phases.get('Finale Underway'):
        start=min(r['source_timestamp_ms'] for r in phases['Finale Underway'])
        phase_actions['finale']=[a for a in actions if a['source_timestamp_ms']>=start]
    return dict(observed_dates=len(windows),date_action_statuses=dict(Counter(w['status'] for w in windows)),
                missing_calendar_ordinals=missing_dates,chronology_errors=chronology,windows=windows,phase_actions=phase_actions,
                action_counts=dict(Counter(a['kind'] for a in actions)),
                calendar_action_coverage_complete=bool(windows) and not missing_dates and not chronology and all(w['status']=='one_action' for w in windows),
                scope='One receipt per repeatedly observed half-month date; pre-debut and finale are listed separately because their calendar labels do not uniquely identify turns.',
                independently_reviewed=False)
