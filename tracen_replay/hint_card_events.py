"""Attach validated hint-card observations without reinterpreting source pixels.

The recognition loader owns image, model and capture validation. This stage
checks recording identity and correspondence to the supplied parsed rows. It
never invokes OCR, guesses a card name, or creates a new event boundary.
"""
from copy import deepcopy
import re


def _field(effect):
    return 'skill_hint_change||'+effect['name']


def _logical_name(name):
    # OCR may render a rank as a separated O. This is used only to detect
    # conflicts, never to award a rank or to merge differently spelled names.
    return re.sub(r'(?:\s*[○◯◎]|\s+O)$', '', name).strip()


def _rank(name):
    if name.endswith('◎'):
        return 'double_circle'
    if name.endswith(('○', '◯')):
        return 'single_circle'
    return None


def _same_line(line, proof):
    return (isinstance(line, dict) and line.get('text') == proof.get('text')
            and line.get('box') == proof.get('box'))


def _supported(candidate, rows, source_sha256):
    if (not isinstance(candidate, dict) or candidate.get('kind') != 'skill_hint_change'
            or candidate.get('source_sha256') != source_sha256
            or not isinstance(candidate.get('name'), str) or not candidate['name'].strip()
            or type(candidate.get('amount')) is not int or not 0 < candidate['amount'] <= 99):
        return None
    observations = candidate.get('observations')
    identity = candidate.get('identity_proof')
    if not isinstance(observations, list) or not isinstance(identity, dict):
        return None
    context = identity.get('same_context')
    if not isinstance(context, str) or not context:
        return None
    support = []
    seen_times = set()
    seen_paths = set()
    for observation in observations:
        if not isinstance(observation, dict):
            return None
        time, evidence = observation.get('timestamp_ms'), observation.get('evidence')
        if (type(time) is not int or time < 0 or not isinstance(evidence, str)
                or time in seen_times or evidence in seen_paths):
            return None
        seen_times.add(time)
        seen_paths.add(evidence)
        row = rows.get((time, evidence))
        card, receipt = observation.get('card'), observation.get('receipt')
        if (row is None or row.get('screen') != 'event_outcome'
                or (row.get('context_title') or row.get('context_title_candidate')) != context
                or not isinstance(card, dict) or not isinstance(receipt, dict)
                or card.get('text') != candidate['name']
                or receipt.get('amount') != candidate['amount']):
            return None
        lines = row.get('ocr', {}).get('neural', [])
        if sum(_same_line(line, card) for line in lines) != 1:
            return None
        if sum(_same_line(line, receipt) for line in lines) != 1:
            return None
        prefix = observation.get('prefix_amount_proof')
        if prefix is not None:
            if (not isinstance(prefix, dict) or type(prefix.get('amount')) is not int
                    or prefix['amount'] != candidate['amount']):
                return None
            support.append(observation)
    times = sorted(seen_times)
    if (len(support) < 2 or candidate.get('source_timestamps_ms') != times
            or any(b-a > 250 for a,b in zip(times,times[1:]))):
        return None
    # Even native samples between the supporting frames must keep the same
    # screen/context. A source-selected candidate cannot bridge another event.
    for (time, _), row in rows.items():
        if times[0] <= time <= times[-1] and (
                row.get('screen') != 'event_outcome'
                or (row.get('context_title') or row.get('context_title_candidate')) != context):
            return None
    suffix = identity.get('suffix')
    if suffix not in (None, 'single_circle', 'double_circle'):
        return None
    if suffix is not None and sum(o['card'].get('suffix') == suffix for o in observations) < 2:
        return None
    written = re.search(r'[○◯◎]$', candidate['name'])
    if written and suffix is not None and (written[0] == '◎') != (suffix == 'double_circle'):
        return None
    return context, times, seen_paths


def apply(events, readings, candidates, *, source_sha256):
    """Mutate matching outcome events; return an explicit integration audit.

Inputs must already have passed the source-bound recognition loader. An
otherwise valid observation that crosses an outcome boundary is retained as
unapplied. Existing hints with evidence outside the proven receipt episode are
not replaced. Raw readings and input candidates remain unchanged.
"""
    audit = {'accepted': [], 'rejected': []}
    if not isinstance(source_sha256, str) or not re.fullmatch(r'[0-9a-f]{64}', source_sha256):
        raise ValueError('Hint-card integration requires the report source SHA-256.')
    rows = {(row['source_timestamp_ms'], row['evidence']): row for row in readings}
    if len(rows) != len(readings):
        raise ValueError('Hint-card integration requires unique source observations.')
    prepared = []
    for index, candidate in enumerate(candidates):
        supported = _supported(candidate, rows, source_sha256)
        if supported is None:
            audit['rejected'].append({'index': index, 'reason': 'candidate_does_not_match_source_rows'})
            continue
        context, times, paths = supported
        targets = [event for event in events if event.get('kind') == 'outcome'
                   and event.get('context_title') == context
                   and event['first_seen_ms'] <= times[0] <= times[-1] <= event['last_seen_ms']]
        if len(targets) != 1:
            audit['rejected'].append({'index': index, 'reason': 'no_unique_existing_outcome'})
            continue
        event = targets[0]
        suffix = candidate['identity_proof'].get('suffix')
        name = candidate['name']
        if suffix is not None:
            name = re.sub(r'\s*[○◯◎]$', '', name) + {'single_circle': ' ○', 'double_circle': ' ◎'}[suffix]
        prepared.append((index, candidate, event, paths, name, suffix))
    # Inspect the whole batch before changing any event. Two individually
    # supported episodes can still disagree within one reconstructed outcome;
    # choosing whichever arrived first would hide that uncertainty.
    conflicts = set()
    groups = {}
    for index, candidate, event, paths, name, suffix in prepared:
        groups.setdefault((id(event), _logical_name(name)), []).append(
            (index, candidate['amount'], suffix or _rank(name)))
    for group in groups.values():
        if len({amount for _, amount, _ in group}) > 1 or len({rank for _, _, rank in group if rank}) > 1:
            conflicts.update(index for index, _, _ in group)
    for index, candidate, event, paths, name, suffix in prepared:
        if index in conflicts:
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_candidates'})
            continue
        hints = [e for e in event['effects'] if e['kind'] == 'skill_hint_change']
        same_skill = [e for e in hints if _logical_name(e['name']) == _logical_name(name)]
        if any(e.get('amount') != candidate['amount'] for e in same_skill):
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_amount'})
            continue
        if any(_rank(e['name']) and _rank(name) and _rank(e['name']) != _rank(name) for e in same_skill):
            audit['rejected'].append({'index': index, 'reason': 'conflicting_hint_rank'})
            continue
        receipts = {o['receipt']['text'] for o in candidate['observations']}
        replace = [e for e in hints if e.get('amount') == candidate['amount']
                   and e.get('raw_text') in receipts
                   and event['field_evidence'].get(_field(e))
                   and set(event['field_evidence'][_field(e)]) <= paths]
        exact = [e for e in hints if e['name'] == name]
        affected = {_field(e) for e in replace+exact+same_skill}
        if len(exact) > 1 or any(c.get('field') in affected for c in event.get('conflicting_readings', [])):
            audit['rejected'].append({'index': index, 'reason': 'existing_hint_conflict'})
            continue
        retained = exact[0] if exact else None
        if retained is None:
            retained = dict(kind='skill_hint_change', name=name, amount=candidate['amount'],
                            raw_text=candidate['observations'][0]['receipt']['text'])
            event['effects'].append(retained)
        retained['identity_basis'] = 'validated_repeated_hint_card_and_receipt_prefix'
        retained['circle_variant_verified'] = suffix is not None
        card_evidence = retained.setdefault('hint_card_evidence', [])
        if candidate not in card_evidence:
            card_evidence.append(deepcopy(candidate))
        alternatives = retained.setdefault('observed_name_candidates', [name])
        for observed in candidate.get('raw_receipt_name_candidates', []):
            if observed not in alternatives:
                alternatives.append(observed)
        proofs = event['field_evidence'].setdefault(_field(retained), [])
        for observation in candidate['observations']:
            if observation['evidence'] not in proofs:
                proofs.append(observation['evidence'])
        removed = []
        for old in replace:
            if old is retained:
                continue
            archived = deepcopy(old)
            archived['field_evidence'] = event['field_evidence'].pop(_field(old), [])
            removed.append(archived)
            event['effects'].remove(old)
        if removed:
            retained.setdefault('replaced_receipt_readings', []).extend(removed)
        audit['accepted'].append({'index': index, 'event_id': event['id'], 'name': name,
                                  'amount': candidate['amount']})
    return audit
