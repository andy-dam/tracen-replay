"""Supply validated wrapped receipts before deriving outcome boundaries.

The saved readings remain immutable. Recovery must already have passed the
source-bound cache loader; event integration rechecks its relationship to the
current rows. Parser-derived outcome spans are not independent evidence that a
previously unparsed receipt belongs to a different scene.
"""
from copy import deepcopy

from .hint_card_events import apply, _field


def _stage(readings, candidates, *, source_sha256):
    """Return copied recovery rows, eligible candidates, and a staging audit."""
    result = list(readings)
    lookup = {(row['source_timestamp_ms'], row['evidence']): i for i, row in enumerate(readings)}
    if len(lookup) != len(readings):
        raise ValueError('Hint recovery requires unique source observations.')
    eligible = []
    # Event integration indexes the filtered candidate list. Keep its mapping
    # to the immutable input list explicit when a source episode is rejected.
    audit = {'accepted': [], 'rejected': [], 'eligible_input_indices': []}
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or candidate.get('observation_kind') != 'wrapped_hint_receipt':
            eligible.append(candidate)
            audit['eligible_input_indices'].append(index)
            continue
        times = candidate.get('source_timestamps_ms')
        if (not isinstance(times, list) or len(times) < 2
                or any(type(value) is not int or value < 0 for value in times)
                or times != sorted(set(times))):
            audit['rejected'].append({'index': index, 'reason': 'invalid_source_span'})
            continue
        scoped = [row for row in readings if times[0] <= row['source_timestamp_ms'] <= times[-1]]
        contexts = {row.get(key) for row in scoped for key in ('context_title', 'context_title_candidate')
                    if isinstance(row.get(key), str) and row[key].strip()}
        if (not scoped or any(row.get('screen') != 'event_outcome' for row in scoped)
                or len(contexts) > 1):
            audit['rejected'].append({'index': index, 'reason': 'observed_scene_boundary'})
            continue
        # Existing accepted hints remain part of conflict detection. Repeated
        # observations of the same hint are one field, not competing awards.
        fields = {}
        existing = []
        seen = set()
        for row in scoped:
            for effect in row.get('effects', []):
                if effect.get('kind') != 'skill_hint_change':
                    continue
                key = (effect.get('name'), effect.get('amount'))
                if key not in seen:
                    existing.append(deepcopy(effect))
                    seen.add(key)
                fields.setdefault(_field(effect), []).append(row['evidence'])
        temporary = dict(id='pending-hint-receipt', kind='outcome',
                         first_seen_ms=times[0], last_seen_ms=times[-1],
                         context_title=next(iter(contexts), None), effects=existing,
                         field_evidence=fields, conflicting_readings=[], deltas={})
        checked = apply([temporary], readings, [candidate], source_sha256=source_sha256)
        if not checked['accepted']:
            audit['rejected'].append({'index': index, 'reason': 'unsupported_receipt',
                                      'validation': checked['rejected']})
            continue
        # A verified receipt explains its own wrapped lines, but it cannot
        # erase a separate narrative line that was an observed boundary.
        parts = {(o.get('timestamp_ms'), o.get('evidence')): list(o.get('receipt_parts', {}).values())
                 for o in candidate.get('observations', [])}
        narrative_boundary = False
        for row in scoped:
            if row.get('effects') or row.get('facts', {}).get('effect_candidates'):
                continue
            receipt_lines = parts.get((row['source_timestamp_ms'], row['evidence']), [])
            lines = row.get('ocr', {}).get('neural', [])
            if not isinstance(lines, list):
                narrative_boundary = True
                continue
            for line in lines:
                if (not isinstance(line, dict) or not isinstance(line.get('box'), (list, tuple))
                        or len(line['box']) != 4
                        or any(type(value) not in (int, float) for value in line['box'])
                        or type(line.get('confidence')) not in (int, float)
                        or not isinstance(line.get('text'), str)):
                    narrative_boundary = True
                    continue
                if (line.get('confidence', 0) >= 95
                        and 790 < (line['box'][1] + line['box'][3]) / 2 < 950
                        and len(line.get('text', '')) > 25
                        and not any(line.get('text') == part.get('text')
                                    and line.get('box') == part.get('box') for part in receipt_lines)):
                    narrative_boundary = True
        if narrative_boundary:
            audit['rejected'].append({'index': index, 'reason': 'observed_narrative_boundary'})
            continue
        recovered = [effect for effect in temporary['effects']
                     if candidate in effect.get('hint_card_evidence', [])]
        if len(recovered) != 1:
            audit['rejected'].append({'index': index, 'reason': 'ambiguous_recovered_field'})
            continue
        effect = recovered[0]
        changed = []
        for observation in candidate['observations']:
            position = lookup[(observation['timestamp_ms'], observation['evidence'])]
            row = result[position]
            if any(item.get('kind') == effect['kind'] and item.get('name') == effect['name']
                   and item.get('amount') == effect['amount'] for item in row.get('effects', [])):
                continue
            row = deepcopy(row)
            row.setdefault('effects', []).append(deepcopy(effect))
            result[position] = row
            changed.append(observation['evidence'])
        eligible.append(candidate)
        audit['eligible_input_indices'].append(index)
        audit['accepted'].append({'index': index, 'name': effect['name'], 'amount': effect['amount'],
                                  'source_timestamps_ms': times, 'enriched_readings': changed})
    return result, eligible, audit


def prepare(readings, candidates, *, source_sha256):
    """Stage only recoveries that survive integration into reconstructed events.

    Removing an unsupported recovery can restore a boundary. Reconstruct from
    the immutable rows after each rejection, until every staged observation is
    accepted. Each retry removes at least one candidate, so this is bounded by
    the input count and cannot leave effects from rejected candidates behind.
    """
    from .transactions import outcome_events

    remaining = list(enumerate(candidates))
    rejected = []
    while True:
        staged, eligible, audit = _stage(
            readings, [candidate for _, candidate in remaining], source_sha256=source_sha256)
        original_indices = [remaining[index][0] for index in audit['eligible_input_indices']]
        rejected.extend(dict(entry, index=remaining[entry['index']][0]) for entry in audit['rejected'])
        if not any(isinstance(candidate, dict) and candidate.get('observation_kind') == 'wrapped_hint_receipt'
                   for candidate in eligible):
            return staged, eligible, dict(accepted=[],
                rejected=sorted(rejected, key=lambda entry: entry['index']),
                eligible_input_indices=original_indices)
        integrated = apply(outcome_events(staged), readings, eligible, source_sha256=source_sha256)
        wrapped_rejected = any(
            isinstance(eligible[entry['index']], dict)
            and eligible[entry['index']].get('observation_kind') == 'wrapped_hint_receipt'
            for entry in integrated['rejected'])
        if not wrapped_rejected:
            return staged, eligible, dict(
                accepted=[dict(entry, index=remaining[entry['index']][0]) for entry in audit['accepted']],
                rejected=sorted(rejected, key=lambda entry: entry['index']),
                eligible_input_indices=original_indices)
        # Reject the complete conflicting batch, including legacy candidates
        # when they disagree with wrapped evidence in the same outcome.
        excluded = {entry['index'] for entry in integrated['rejected']}
        rejected.extend(dict(entry, index=original_indices[entry['index']])
                        for entry in integrated['rejected'])
        remaining = [(original_indices[index], candidate) for index, candidate in enumerate(eligible)
                     if index not in excluded]
