"""One-to-one receipt evaluation within an explicitly labeled source interval."""

ACTION_KINDS=('training','rest','outing','race')


def evaluate(reference,report):
    if reference['source_sha256']!=report['source']['sha256']:raise ValueError('Different source recordings.')
    complete=reference.get('reference_complete')
    if 'reference_complete' in reference and type(complete) is not bool:
        raise ValueError('reference_complete must be an explicit boolean when supplied.')
    if not isinstance(reference.get('actions'),list):
        raise ValueError('Explicit actions are required.')
    kinds=reference.get('kinds')
    if (not isinstance(kinds,list) or not kinds
            or any(k not in ACTION_KINDS for k in kinds)
            or len(set(kinds))!=len(kinds)):
        raise ValueError('Declare unique supported training/rest/outing/race kinds.')
    start,end=reference.get('start_ms'),reference.get('end_ms')
    if type(start) is not int or type(end) is not int or not 0<=start<end:
        raise ValueError('Invalid evaluation interval.')
    duration=report['source'].get('duration_ms')
    if 'duration_ms' in report['source']:
        if type(duration) is not int or duration<=0:raise ValueError('Invalid source duration.')
        if end>duration:raise ValueError('Reference exceeds source duration.')
    negative=reference.get('no_completed_actions') is True
    if not reference['actions'] and not (negative and reference.get('independently_reviewed') is True):
        raise ValueError('Empty actions require an explicitly reviewed negative reference.')
    if negative and reference['actions']:raise ValueError('Negative reference contains completed actions.')
    for action in reference['actions']:
        if (not isinstance(action,dict) or action.get('kind') not in kinds
                or type(action.get('start_ms')) is not int or type(action.get('end_ms')) is not int
                or not start<=action['start_ms']<action['end_ms']<=end):
            raise ValueError('Action annotation lies outside the declared evaluation scope.')
    data=report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:raise ValueError('Only gameplay-only results are eligible.')
    receipts=data.get('turn_action_receipts')
    if not isinstance(receipts,list):raise ValueError('Explicit predicted action rows are required.')
    for index,action in enumerate(receipts):
        if (not isinstance(action,dict) or action.get('kind') not in ACTION_KINDS
                or type(action.get('source_timestamp_ms')) is not int
                or action['source_timestamp_ms']<0
                or (duration is not None and action['source_timestamp_ms']>=duration)):
            raise ValueError(f'Invalid predicted action row {index}: supported kind and integer source-bounded timestamp required.')
    predictions=[a for a in receipts if a['kind'] in kinds and start<=a['source_timestamp_ms']<end]
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
        kinds=list(kinds),start_ms=start,end_ms=end,
        independently_reviewed=reference.get('independently_reviewed',False),scope=reference['scope'])
