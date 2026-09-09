"""Conservative navigation hints, never automatic review decisions."""


def semantic(effect):
    return {k: v for k, v in effect.items() if k not in ('raw_text', 'confidence')}


def attach(queue, report):
    data = report['gameplay_tracking']
    readings = {}
    for reading in data.get('readings', []):
        readings.setdefault(reading['evidence'], []).append(reading)
    count = 0
    for finding in queue['findings']:
        finding.pop('recovery_suggestion', None)
        if finding['reason'] != 'obscured_receipt':
            continue
        lines = finding.get('details') or []
        # Covered names and malformed text need visual adjudication; no fuzzy repair.
        if not lines or any(x.get('recipient_name_occluded') for x in lines):
            continue
        events = [e for e in data.get('events', [])
                  if e['first_seen_ms'] <= finding['start_ms']
                  and finding['end_ms'] <= e['last_seen_ms']]
        if len(events) != 1 or events[0].get('conflicting_readings'):
            continue
        event = events[0]
        matches = []
        for line in lines:
            effects = [e for e in event.get('effects', [])
                       if e.get('raw_text') == line['text']]
            if len(effects) != 1:
                break
            effect = effects[0]
            key = '|'.join(str(effect.get(k, '')) for k in ('kind', 'field', 'name'))
            support = {}
            for path in event.get('field_evidence', {}).get(key, []):
                if path in finding['evidence']:
                    continue
                for reading in readings.get(path, []):
                    time = reading['source_timestamp_ms']
                    if not event['first_seen_ms'] <= time <= event['last_seen_ms']:
                        continue
                    if finding['start_ms'] <= time <= finding['end_ms']:
                        continue
                    # Conservatively exclude any obscured receipt in a support frame.
                    if reading.get('facts', {}).get('occluded_receipt_lines'):
                        continue
                    observed = reading.get('effects', [])
                    same_field = [e for e in observed if
                                  all(e.get(k) == effect.get(k) for k in ('kind', 'field', 'name'))]
                    if same_field and all(semantic(e) == semantic(effect) for e in same_field):
                        support.setdefault(time, path)
            if len(support) < 2:
                break
            matches.append(dict(effect=semantic(effect), evidence=[
                dict(source_timestamp_ms=t, path=p) for t, p in sorted(support.items())]))
        else:
            finding['recovery_suggestion'] = dict(event_id=event['id'], matches=matches,
                requires_visual_review=True,
                basis='Exact receipt text and matching typed effects in distinct alternate frames of one event.')
            count += 1
    queue['summary']['recovery_suggestions'] = count
    return queue
