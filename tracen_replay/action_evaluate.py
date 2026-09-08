"""One-to-one receipt evaluation within an explicitly labeled source interval."""


def evaluate(reference,report):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recordings.')
    if not reference.get('actions') or not reference.get('kinds'):raise ValueError('An explicit nonempty action reference is required.')
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
        precision=len(matched)/len(predictions) if predictions else 0,recall=len(matched)/len(reference['actions']),
        missed=missed,extra=extra,matches=matched,passed=not missed and not extra,
        independently_reviewed=reference.get('independently_reviewed',False),scope=reference['scope'])
