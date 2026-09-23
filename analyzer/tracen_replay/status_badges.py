"""Read gameplay status badges as observations, never as changes or awards."""

from copy import deepcopy
import re

from .layout import place


def read_badges(lines):
    def region(line, box, minimum):
        if not isinstance(line, dict) or line.get('confidence', 0) < minimum:
            return False
        points = line.get('box', [])
        if len(points) != 4:
            return False
        x, y, xx, yy = points
        left, top, right, bottom = box
        return xx > x and yy > y and left <= (x+xx)/2 <= right and top <= (y+yy)/2 <= bottom

    # The energy row and its mood badge are pinned to the top, centred; the
    # hype badge sits in the panel at the top left.
    result = []
    energy = [line for line in lines if region(line, place((375, 115, 455, 165), 'tc'), 95)
              and line.get('text', '').strip().casefold() == 'energy']
    moods = [line for line in lines if region(line, place((705, 112, 825, 165), 'tc'), 97)
             and line.get('text', '').strip().casefold() in ('awful', 'bad', 'normal', 'good', 'great')]
    values = {line['text'].strip().casefold() for line in moods}
    if len(energy) == 1 and len(values) == 1:
        result.append(dict(kind='mood_status', value=next(iter(values)),
                           proof={'basis': 'gameplay_energy_row_mood_badge', 'anchor': deepcopy(energy[0]),
                                  'readings': deepcopy(moods)}))
    headers = [line for line in lines if region(line, place((150, 150, 300, 188), 'tl'), 97)
               and line.get('text', '').strip().casefold() == 'hype level']
    hype_lines = [line for line in lines if region(line, place((150, 195, 290, 260), 'tl'), 90)
                  and line.get('text', '').strip().casefold() == 'hype']
    candidates = []
    value_box = place((150, 185, 295, 224), 'tl')
    for line in lines:
        if not region(line, value_box, 95):
            continue
        text = line.get('text', '').strip()
        combined = re.fullmatch(r'([A-Za-z]+(?: [A-Za-z]+)?)\s+Hype', text, re.IGNORECASE)
        if combined:
            candidates.append((combined[1].casefold(), line))
        elif headers and re.fullmatch(r'[A-Za-z]+', text) and text.casefold() != 'hype':
            candidates.append((text.casefold(), line))
    values = {value for value, _ in candidates}
    if len(headers) == 1 and len(values) == 1:
        result.append(dict(kind='hype_status', value=next(iter(values)),
                           proof={'basis': 'gameplay_labeled_hype_badge', 'anchor': deepcopy(headers[0]),
                                  'readings': [deepcopy(line) for _, line in candidates],
                                  'hype_label': deepcopy(hype_lines)}))
    return result


def build_observations(readings, maximum_gap_ms=500):
    """Coalesce contiguous equal badge observations with explicit frame proofs."""
    observations, active = [], {}
    ordered = sorted(enumerate(readings), key=lambda pair: pair[1].get('source_timestamp_ms', -1))
    for index, row in ordered:
        timestamp, evidence = row.get('source_timestamp_ms'), row.get('evidence')
        if type(timestamp) is not int or timestamp < 0 or not isinstance(evidence, str) or not evidence:
            active.clear()
            continue
        row_facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        if 'status_badges' not in row_facts:
            # A bounded reread row (dense result or receipt frames) is never
            # parsed for status badges.  It carries no evidence for or
            # against a badge, so it neither extends nor breaks a run of
            # equal badge observations on the surrounding parsed frames.
            continue
        facts = row_facts.get('status_badges') or []
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        context = {}
        calendar = stats.get('calendar_text')
        if isinstance(calendar, str) and calendar.strip():
            context['calendar_text'] = calendar.strip()
        countdown = stats.get('turns_remaining_to_goal')
        if type(countdown) is int and countdown >= 0:
            context['turns_remaining_to_goal'] = countdown
        known = set()
        for fact in facts:
            proof = fact.get('proof', {})
            lines = [proof.get('anchor')] + proof.get('readings', []) + proof.get('hype_label', [])
            verified = read_badges([line for line in lines if isinstance(line, dict)])
            if not any(item['kind'] == fact.get('kind') and item['value'] == fact.get('value') for item in verified):
                continue
            kind, value = fact['kind'], fact['value']
            known.add(kind)
            current = active.get(kind)
            if (current is None or current['payload']['value'] != value
                    or current['observed_context'] != context
                    or timestamp-current['end_ms'] > maximum_gap_ms):
                current = dict(id=f'/gameplay_tracking/status_observations/{len(observations)}',
                    category='effect', phase='observed', payload={'kind': kind, 'value': value},
                    start_ms=timestamp, end_ms=timestamp, evidence=[], source_observations=[], uncertain=False,
                    observed_context=deepcopy(context))
                current['source_ref'] = current['id']
                observations.append(current)
                active[kind] = current
            current['end_ms'] = timestamp
            if evidence not in current['evidence']:
                current['evidence'].append(evidence)
            current['source_observations'].append(dict(source_timestamp_ms=timestamp, evidence=evidence,
                                                       source_ref=f'/gameplay_tracking/readings/{index}', proof=deepcopy(proof)))
        for kind in list(active):
            if kind not in known:
                del active[kind]
    return observations
