"""Shared, conservative occurrence evaluation over normalized observations.

Adapters supply only scored payload fields and field-level source evidence.
Matching never uses numeric agreement. Explicit occurrence keys identify one
atomic source occurrence, not a whole event containing several receipt lines.
The original evaluators and historical scores remain separate during migration.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math
import unicodedata


SCHEMA = 'tracen-replay/observation-evaluation-v1'
CATEGORIES = {'action', 'effect', 'state', 'purchase', 'context'}
PHASES = {'preview', 'committed', 'applied', 'observed', 'context'}
SIGNED_KINDS = {'stat_change', 'performance_change', 'energy_change',
                'friendship_change', 'fan_change', 'skill_hint_change',
                'stat_cap_change', 'performance_cap_change'}
KIND_ALIASES = {'friendship_state': 'friendship_status',
                'training_bonus_change': 'training_modifier_change',
                # Receipt readers historically emitted a specialized kind
                # for the same acquired-condition observation represented by
                # the source schema as condition_change(value=acquired).
                'condition_acquired': 'condition_change'}

_HYPE_VALUE_ALIASES = {
    'mild': 'mild_hype',
    'mild hype': 'mild_hype',
    'mild_hype': 'mild_hype',
    'maxed': 'maxed',
    'maxed out': 'maxed',
    'maxed_out': 'maxed',
    'maximum': 'maxed',
}

_TYPOGRAPHIC_QUOTE_ALIASES = str.maketrans({
    '\u2018': "'", '\u2019': "'", '\u201b': "'", '\u2032': "'",
    '\u201c': '"', '\u201d': '"', '\u201f': '"', '\u2033': '"',
})


def canonical(payload):
    """Normalize explicit semantics without fuzzy names or invented values."""
    result = deepcopy(payload)
    original_kind = result.get('kind')
    kind = KIND_ALIASES.get(original_kind, original_kind)
    if 'kind' in result:
        result['kind'] = kind
    if original_kind == 'condition_acquired':
        # The specialized kind is itself an explicit assertion of the
        # acquired status.  Supply only that fixed enum; an explicit value in
        # a malformed row remains visible for comparison.
        result.setdefault('value', 'acquired')
    if result.get('field') == 'vocals':
        result['field'] = 'vocal'
    if kind == 'friendship_status' and result.get('value') in ('max', 'maxed out'):
        result['value'] = 'maximum'
    if kind == 'hype_status' and isinstance(result.get('value'), str):
        value = ' '.join(result['value'].split()).casefold()
        result['value'] = _HYPE_VALUE_ALIASES.get(value, result['value'])
    if kind == 'training_modifier_change' and 'amount' not in result:
        # Older typed preview facts used ``value`` for the numeric modifier;
        # the canonical source/reference schema uses ``amount``.  This is a
        # field-name alias only, never a state delta or a computed balance.
        value = result.get('value')
        if type(value) in (int, float):
            result['amount'] = value
            result.pop('value')
    amount = result.get('amount')
    if kind in SIGNED_KINDS and type(amount) in (int, float):
        direction = result.get('direction')
        if direction in ('down', 'decrease'):
            result['amount'] = -abs(amount)
            result.pop('direction')
        elif direction in ('up', 'increase'):
            if amount < 0:
                raise ValueError('Negative amount contradicts increase direction')
            result.pop('direction')
    return result


def _flatten(value, path=''):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError('Payload keys must be strings')
            escaped = key.replace('~', '~0').replace('/', '~1')
            result.update(_flatten(item, path + '/' + escaped))
        return result
    if type(value) is float and not math.isfinite(value):
        raise ValueError('Nonfinite numeric observation')
    if isinstance(value, list):
        for item in value:
            _flatten(item)
    return {path: value}


def _validate_uncertainty_fields(value):
    """Validate field-scoped uncertainty as JSON-pointer paths.

    A producer may know that one part of an observation is disputed while
    retaining other fields as readable.  The list is deliberately structural
    metadata: it must contain nonempty JSON pointers, but pointers need not be
    present in the payload because an unresolved field may have been omitted
    from the canonical payload entirely.
    """
    if not isinstance(value, list) or not value:
        raise ValueError('uncertainty_fields must be a nonempty list')
    if any(not isinstance(pointer, str) for pointer in value):
        raise ValueError('uncertainty_fields must contain JSON-pointer strings')
    if len(set(value)) != len(value):
        raise ValueError('uncertainty_fields must not contain duplicates')
    for pointer in value:
        if not pointer or pointer == '/' or not pointer.startswith('/'):
            raise ValueError('uncertainty_fields must contain nonempty JSON-pointer paths')
        # RFC 6901 only reserves ~0 and ~1.  Rejecting malformed escapes keeps
        # a typo from silently scoping uncertainty to the wrong field.
        for index, character in enumerate(pointer):
            if character == '~' and (index + 1 >= len(pointer) or pointer[index + 1] not in '01'):
                raise ValueError('uncertainty_fields contains an invalid JSON-pointer escape')


def _pointer_related(left, right):
    """Return whether two JSON pointers identify an ancestor/descendant pair."""
    if left == right:
        return True
    if left == '':
        return True
    return right.startswith(left.rstrip('/') + '/') or left.startswith(right.rstrip('/') + '/')


def _uncertainty_applies(actual, source):
    """Check whether a prediction's scoped uncertainty affects this source row."""
    if actual.get('uncertain') is not True:
        return False
    fields = actual.get('uncertainty_fields')
    # Historical rows have only a row-level uncertain flag. Preserve their
    # conservative ambiguity semantics until a producer supplies a scope.
    if fields is None:
        return True
    expected_paths = _flatten(source.get('payload', {}))
    return any(_pointer_related(pointer, expected)
               for pointer in fields for expected in expected_paths)


def _equal(left, right):
    if type(left) is bool or type(right) is bool:
        return type(left) is type(right) and left == right
    if isinstance(left, str) and isinstance(right, str):
        normalize = lambda s: ' '.join(
            unicodedata.normalize('NFC', s).translate(_TYPOGRAPHIC_QUOTE_ALIASES).split()
        ).casefold()
        return normalize(left) == normalize(right)
    if isinstance(left, list) or isinstance(right, list):
        return isinstance(left, list) and isinstance(right, list) and len(left) == len(right) and all(_equal(a,b) for a,b in zip(left,right))
    if isinstance(left, dict) or isinstance(right, dict):
        return isinstance(left, dict) and isinstance(right, dict) and left.keys() == right.keys() and all(_equal(left[k],right[k]) for k in left)
    return left == right


def _observations(document):
    rows = document.get('observations')
    if not isinstance(rows, list):
        raise ValueError('Explicit observation list required')
    result = {}
    for source in rows:
        row = deepcopy(source)
        identity = row.get('id')
        if not isinstance(identity, str) or not identity or identity in result:
            raise ValueError('Observation IDs must be unique nonempty strings')
        if row.get('category') not in CATEGORIES or row.get('phase') not in PHASES:
            raise ValueError('Explicit supported category and phase required')
        start, end = row.get('start_ms'), row.get('end_ms')
        if type(start) is not int or type(end) is not int or not 0 <= start <= end:
            raise ValueError('Invalid observation interval')
        if not isinstance(row.get('payload'), dict) or not row['payload']:
            raise ValueError('A nonempty scored payload is required')
        proof = row.get('evidence', [])
        if not isinstance(proof, list) or any(not isinstance(p, str) or not p for p in proof):
            raise ValueError('Evidence must be a list of nonempty source identifiers')
        for key in ('cause_id', 'occurrence_key'):
            if row.get(key) is not None and (not isinstance(row[key], str) or not row[key]):
                raise ValueError(f'{key} must be a nonempty string or null')
        if type(row.get('uncertain', False)) is not bool:
            raise ValueError('uncertain must be boolean')
        if 'uncertainty_fields' in row:
            _validate_uncertainty_fields(row['uncertainty_fields'])
        if row.get('status', 'observed') not in ('observed', 'unobservable', 'ambiguous', 'ungraded'):
            raise ValueError('Invalid source observability status')
        row['payload'] = canonical(row['payload'])
        _flatten(row['payload'])
        result[identity] = row
    return result


def _same_identity(left, right):
    return all(_equal(left['payload'].get(key), right['payload'].get(key))
               for key in ('kind', 'field', 'name', 'channel', 'training_option')
               if left['payload'].get(key) is not None)


def _eligible(left, right, *, ownership=True):
    if (left['category'], left['phase']) != (right['category'], right['phase']):
        return False
    if left.get('occurrence_key') is not None and right.get('occurrence_key') is not None and left['occurrence_key'] != right['occurrence_key']:
        return False
    if ownership and left.get('cause_id') is not None and right.get('cause_id') is not None:
        if left['cause_id'] != right['cause_id']:
            return False
    overlap = max(left['start_ms'], right['start_ms']) <= min(left['end_ms'], right['end_ms'])
    return overlap or bool(set(left.get('evidence', [])) & set(right.get('evidence', [])))


def _maximum(graph, forbidden=None):
    """Maximum bipartite assignment; values are deliberately absent."""
    assigned = {}

    def augment(source, visited):
        for prediction in sorted(graph[source]):
            if (source, prediction) == forbidden or prediction in visited:
                continue
            visited.add(prediction)
            if prediction not in assigned or augment(assigned[prediction], visited):
                assigned[prediction] = source
                return True
        return False

    for source in sorted(graph):
        augment(source, set())
    return {source: prediction for prediction, source in assigned.items()}


def _forced(graph):
    """Only joins present in every maximum assignment are accepted."""
    assignment = _maximum(graph)
    return {s: p for s, p in assignment.items()
            if len(_maximum(graph, (s, p))) < len(assignment)}


def _compare(source, prediction):
    wanted, actual = _flatten(source['payload']), _flatten(prediction['payload'])
    if source.get('cause_id') is not None:
        wanted['/cause_id'] = source['cause_id']
        actual['/cause_id'] = prediction.get('cause_id')
    fields = []
    for path, expected in wanted.items():
        got = actual.get(path)
        status = ('unobservable' if expected is None else
                  'missed' if got is None or got == 'unknown' else
                  'correct' if _equal(expected, got) else 'incorrect')
        fields.append({'field': path, 'expected': expected, 'actual': got, 'status': status})
    return fields


def evaluate(reference, prediction):
    """Grade normalized observations; never certify an incomplete reference."""
    if not reference.get('source_sha256') or reference['source_sha256'] != prediction.get('source_sha256'):
        raise ValueError('Same source recording is required')
    if prediction.get('auxiliary_log_used') is not False:
        raise ValueError('Explicit gameplay-only prediction required')
    complete = reference.get('reference_complete', False)
    if type(complete) is not bool:
        raise ValueError('reference_complete must be boolean')
    scope = reference.get('scope_ms')
    if not isinstance(scope, list) or len(scope) != 2 or any(type(v) is not int for v in scope) or not 0 <= scope[0] < scope[1]:
        raise ValueError('An explicit half-open reference scope is required')
    sources, predictions = _observations(reference), _observations(prediction)
    if any(not scope[0] <= row['start_ms'] < scope[1] for row in sources.values()):
        raise ValueError('Source occurrence starts outside reference scope')
    predictions = {i: row for i, row in predictions.items()
                   if row['start_ms'] < scope[1] and row['end_ms'] >= scope[0]}
    active = {i: row for i, row in sources.items() if row.get('status', 'observed') == 'observed'}
    assigned, reasons, used = {}, {}, set()
    # Prefer exact source evidence with structural identity before temporal
    # overlap. A nearby repeated menu view is not the labeled source frame.
    # Unselected views remain extras; amounts never choose the assignment.
    # Then use structural identity and explicit atomic occurrence identity.
    # The latter can expose a wrong name/kind without stealing another actor's
    # correctly named receipt merely because the frames overlap.
    for mode in ('source_identity', 'identity', 'occurrence', 'misattributed'):
        graph = {}
        for sid, source in active.items():
            if sid in assigned:
                continue
            candidates = set()
            for pid, actual in predictions.items():
                if pid in used or not _eligible(source, actual, ownership=mode != 'misattributed'):
                    continue
                same_key = source.get('occurrence_key') is not None and source['occurrence_key'] == actual.get('occurrence_key')
                same_proof = bool(set(source.get('evidence', [])) & set(actual.get('evidence', [])))
                if mode == 'source_identity' and same_proof and _same_identity(source, actual):
                    candidates.add(pid)
                elif mode == 'identity' and _same_identity(source, actual):
                    candidates.add(pid)
                elif mode == 'occurrence' and same_key:
                    candidates.add(pid)
                elif mode == 'misattributed' and _same_identity(source, actual) and source.get('cause_id') is not None and actual.get('cause_id') is not None and source['cause_id'] != actual['cause_id']:
                    candidates.add(pid)
            graph[sid] = candidates
        joins = _forced(graph)
        assigned.update(joins)
        used.update(joins.values())
        reasons.update({sid: mode for sid in joins})
    results, ambiguity_pool = [], set()
    for sid, source in sources.items():
        row = {'source_id': sid, 'category': source['category'], 'phase': source['phase'],
               'source_interval_ms': [source['start_ms'], source['end_ms']],
               'source_evidence': source.get('evidence', []), 'prediction_id': assigned.get(sid), 'fields': []}
        if sid not in active:
            row['status'] = source['status']
        elif sid in assigned:
            actual = predictions[assigned[sid]]
            row['fields'] = _compare(source, actual)
            statuses = {f['status'] for f in row['fields']}
            row['matching_basis'] = reasons[sid]
            row['status'] = ('ambiguous' if _uncertainty_applies(actual, source) else
                             'misattributed' if reasons[sid] == 'misattributed' else
                             'incorrect' if 'incorrect' in statuses else
                             'partial' if 'missed' in statuses else
                             'correct' if 'correct' in statuses else 'unobservable')
        else:
            candidates = [pid for pid, actual in predictions.items() if pid not in used
                          and _eligible(source, actual, ownership=False)
                          and (_same_identity(source, actual) or source.get('occurrence_key') is not None
                               and source['occurrence_key'] == actual.get('occurrence_key'))]
            row['status'] = 'ambiguous' if candidates else 'missed'
            row['candidate_ids'] = sorted(candidates)
            ambiguity_pool.update(candidates)
        results.append(row)
    ungraded_pool = {pid for pid, actual in predictions.items() if any(
        sid not in active and _eligible(source, actual, ownership=False) and _same_identity(source, actual)
        for sid, source in sources.items())}
    extras = [{'prediction_id': pid, 'status': 'ambiguous' if pid in ambiguity_pool else 'ungraded' if pid in ungraded_pool else 'extra' if complete else 'ungraded'}
              for pid in predictions if pid not in used]
    groups = defaultdict(list)
    for pid, row in predictions.items():
        if row.get('occurrence_key') is not None:
            groups[(row['category'], row['phase'], row['occurrence_key'])].append(pid)
    duplicates = [sorted(ids) for ids in groups.values() if len(ids) > 1]
    counts = dict(Counter(row['status'] for row in results))
    blockers = [] if complete else ['incomplete_reference']
    if not sources and reference.get('negative_scope_reviewed') is not True:
        blockers.append('empty_reference_without_reviewed_negative_scope')
    return {'schema_version': SCHEMA, 'source_sha256': reference['source_sha256'],
            'scope_ms': scope, 'reference_complete': complete, 'results': results,
            'status_counts': counts, 'unmatched_predictions': extras,
            'duplicate_occurrence_claims': duplicates,
            'score_blockers': blockers,
            'passed': not blockers and all(row['status'] == 'correct' for row in results) and not extras and not duplicates,
            'limitations': ['Occurrence matching does not establish exhaustive reference coverage.',
                            'An occurrence key must identify a source-supported atomic observation; it must not be inferred from matching amounts.',
                            'Numeric agreement is graded after assignment and is not a matching criterion.']}
