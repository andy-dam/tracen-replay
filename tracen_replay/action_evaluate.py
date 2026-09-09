"""One-to-one receipt evaluation within an explicitly labeled source interval."""


def evaluate(reference,report):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recordings.')
    complete=reference.get('reference_complete')
    if 'reference_complete' in reference and type(complete) is not bool:
        raise ValueError('reference_complete must be an explicit boolean when supplied.')
    if not isinstance(reference.get('actions'),list) or not reference.get('kinds'):
        raise ValueError('Explicit actions and scoped kinds are required.')
    if not 0<=reference['start_ms']<reference['end_ms']:raise ValueError('Invalid evaluation interval.')
    negative=reference.get('no_completed_actions') is True
    if not reference['actions'] and not (negative and reference.get('independently_reviewed') is True):
        raise ValueError('Empty actions require an explicitly reviewed negative reference.')
    if negative and reference['actions']:raise ValueError('Negative reference contains completed actions.')
    for action in reference['actions']:
        if action['kind'] not in reference['kinds'] or not reference['start_ms']<=action['start_ms']<action['end_ms']<=reference['end_ms']:
            raise ValueError('Action annotation lies outside the declared evaluation scope.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Only gameplay-only results are eligible.')
    predictions=[a for a in data['turn_action_receipts'] if a['kind'] in reference['kinds'] and reference['start_ms']<=a['source_timestamp_ms']<reference['end_ms']]
    used=set();matched=[];missed=[]
    for expected in reference['actions']:
        candidates=[(i,a) for i,a in enumerate(predictions) if i not in used and a['kind']==expected['kind']
            and expected['start_ms']<=a['source_timestamp_ms']<=expected['end_ms']
            and ('training_option' not in expected or a.get('training_option')==expected['training_option'])]
        if candidates:
            index,actual=candidates[0];used.add(index);matched.append(dict(expected=expected,actual=actual))
        else:missed.append(expected)
    extra=[a for i,a in enumerate(predictions) if i not in used]
    return dict(matched=len(matched),expected=len(reference['actions']),predicted=len(predictions),
        precision=len(matched)/len(predictions) if predictions else None,
        recall=len(matched)/len(reference['actions']) if reference['actions'] else None,
        missed=missed,extra=extra,matches=matched,passed=complete is not False and not missed and not extra,
        reference_complete=complete,
        completeness='complete' if complete is True else 'incomplete' if complete is False else 'legacy_unspecified',
        score_blockers=['incomplete_reference'] if complete is False else [],
        independently_reviewed=reference.get('independently_reviewed',False),scope=reference['scope'])
