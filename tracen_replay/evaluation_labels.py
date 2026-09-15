"""Expand explicit source outcome groups without inventing missing facts.

Use after validating the source seal. No report is read here. Parent evidence,
original expected values and exact source pointers are retained for audit.
"""
import copy

STATS={'speed','stamina','power','guts','wit','skill_points'}
PERFORMANCE={'dance','passion','vocal','visual','composure'}


def typed_effect(outcome,parent_kind=None):
    result=copy.deepcopy(outcome)
    kind=result.get('kind')
    if not kind:
        if parent_kind and parent_kind!='event_outcome':kind=parent_kind
        elif result.get('field') in STATS:kind='stat_change'
        elif result.get('field') in PERFORMANCE:kind='performance_change'
        elif result.get('field')=='fans':
            kind='fan_change'
            result.pop('field')
    if kind:result['kind']=kind
    if kind=='friendship_change' and result.get('amount') is None and result.get('value') in ['maxed out','maximum']:
        result.update(kind='friendship_status',value='maximum')
    if isinstance(result.get('amount'),(int,float)):
        if result.get('direction')=='down':result['amount']=-abs(result['amount'])
        elif result.get('direction')=='up' and result['amount']<0:
            raise ValueError('Source amount/direction contradict each other')
    return result


def expand(labels):
    expanded=[]
    for original in labels:
        expected=original['expected']
        if original['category']=='effect' and isinstance(expected.get('outcomes'),list):
            if not expected['outcomes']:
                row=copy.deepcopy(original)
                row['adapter_ungraded_reason']='Empty source outcome list; no effects inferred.'
                expanded.append(row)
                continue
            for i,outcome in enumerate(expected['outcomes']):
                row=copy.deepcopy(original)
                row.update(id=original['id']+f'/outcome/{i}',source_label_id=original['id'],
                           original_expected=copy.deepcopy(expected),source_expected_pointer=f'/expected/outcomes/{i}')
                row['expected']=typed_effect(outcome,expected.get('kind'))
                if expected.get('name'):row['expected']['context_title']=expected['name']
                if expected.get('observation_basis'):row['expected']['observation_basis']=expected['observation_basis']
                if not row['expected'].get('kind'):row['adapter_ungraded_reason']='Grouped source effect has no supported type; retain as ungraded.'
                expanded.append(row)
        else:
            expanded.append(copy.deepcopy(original))
        # A purchase's explicit awarded effects are distinct from its debit.
        # Keep both; do not manufacture rewards from an advertised option.
        if original['category']=='purchase':
            receipt=expected.get('receipt') or {}
            for i,outcome in enumerate(receipt.get('effects',[])):
                row=copy.deepcopy(original)
                row.update(id=original['id']+f'/receipt-effect/{i}',category='effect',source_label_id=original['id'],
                           original_expected=copy.deepcopy(expected),source_expected_pointer=f'/expected/receipt/effects/{i}')
                row['expected']=typed_effect(outcome)
                row['expected']['observation_basis']='explicit source purchase receipt effect'
                t=receipt.get('timestamp_ms')
                if t is not None:row['first_seen_ms']=row['last_seen_ms']=t
                elif receipt.get('first_seen_ms') is not None and receipt.get('last_seen_ms') is not None:
                    row['first_seen_ms']=receipt['first_seen_ms']
                    row['last_seen_ms']=receipt['last_seen_ms']
                    if row['last_seen_ms'] < row['first_seen_ms']:
                        raise ValueError('Source receipt interval ends before it starts')
                else:row['adapter_timing_note']='Source did not separately timestamp the receipt effect; retain purchase occurrence span.'
                if not row['expected'].get('kind'):row['adapter_ungraded_reason']='Receipt effect type unsupported; retain as ungraded.'
                expanded.append(row)
    return expanded


import re

NUMERIC = {'speed', 'stamina', 'power', 'guts', 'wit', 'skill_points'}


def normalize(labels):
    out = []
    for original in expand(labels):
        row = copy.deepcopy(original)
        row.setdefault('source_label_id', original['id'])
        row.setdefault('original_expected', copy.deepcopy(original['expected']))
        e = row['expected']
        if row['category'] == 'state' and not e.get('channel'):
            values = e.get('values', {})
            channels = {k: e.get(k, values.get(k)) for k in ('stats', 'performance')}
            if not channels['stats'] and any(k in values for k in NUMERIC):
                channels['stats'] = {k: v for k, v in values.items() if k in NUMERIC}
            added = False
            for channel, fields in channels.items():
                if not isinstance(fields, dict) or not fields:
                    continue
                child = copy.deepcopy(row)
                child['id'] += '/' + channel
                child['expected'] = {'channel': channel, 'values': fields,
                                     'observation_basis': e.get('observation_basis'),
                                     'calendar_text': e.get('calendar_text', e.get('calendar', e.get('calendar_label'))),
                                     'turns_remaining_to_goal': e.get('turns_remaining_to_goal', e.get('turns_left'))}
                out.append(child)
                added = True
            if not added:
                row['adapter_ungraded_reason'] = 'Source state has no supported numeric channel; goal and scenario metadata need separate grading.'
                out.append(row)
            continue
        if row['category'] == 'action' and e.get('kind') == 'training':
            option = e.get('training_field', e.get('training_option', e.get('name', '')))
            match = re.match(r'^(speed|stamina|power|guts|wit)(?:\b|$)', option, re.I)
            if match:
                e['training_option'] = match.group(1).lower()
            else:
                row['adapter_ungraded_reason'] = 'Training field cannot be structurally extracted without guessing.'
        if row['category'] == 'effect' and e.get('kind') == 'other_observed_mechanical_outcome':
            row['adapter_ungraded_reason'] = 'Preserved source mechanical/context observation; no typed comparison adapter yet.'
        if row['category'] == 'purchase' and 'cost' not in e:
            cost = e.get('committed_cost')
            if isinstance(cost, dict) and cost.get('currency') == 'skill_points':
                e['cost'] = {'skill_points': cost['amount']}
                components = e.get('request', {}).get('components', [])
                if components:
                    e['bundles'] = [{'name': c['name'], 'bundle_cost_skill_points': c['displayed_cost']}
                                    for c in components]
            elif isinstance(cost, dict) and cost.get('currency') == 'performance_points':
                e['cost'] = copy.deepcopy(cost['amounts'])
            else:
                row['adapter_ungraded_reason'] = 'Purchase cost structure absent or unsupported; do not infer a debit.'
        out.append(row)
    return out
