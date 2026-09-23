"""Keep a result panel resumed after a playback dialog in the same race.

Matching rewards alone cannot identify a race. Continuation requires repeated
per-field identities on both sides and an observed, uninterrupted dialog
sequence. The dialog interrupts item visibility even when race identity persists.
"""

from copy import deepcopy
import re

from .source_clock import elapsed


IDENTITY_FIELDS = ('race_name', 'placing', 'fans', 'fans_gained', 'course')
OPTIONAL_IDENTITY_FIELDS = ('race_grade',)
ANCHOR_FIELDS = ('placing', 'fans', 'fans_gained')
COURSE_FIELDS = ('venue', 'surface', 'distance_m', 'distance_category', 'direction')
_RACE_GRADE_RE = re.compile(r'^(?:DEBUT|G[123]|OP|PRE[- ]?OP|EX)$', re.I)
HASH_FIELDS = ('source_frame_sha256', 'gameplay_sha256', 'evidence_sha256',
               'source_sha256')
MAX_SOURCE_STEP_MS = 1000
MAX_TRANSITION_MS = 500
MAX_DIALOG_SPAN_MS = 10000


def _time(row):
    value = row.get('source_timestamp_ms') if isinstance(row, dict) else None
    return value if type(value) is int and value >= 0 else None


def _evidence_paths(row):
    value = row.get('evidence') if isinstance(row, dict) else None
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _valid_scalar(field, value):
    if field == 'race_name':
        return isinstance(value, str) and bool(value.strip())
    if field == 'race_grade':
        if not isinstance(value, str):
            return False
        value = re.sub(r'\s+', ' ', value.strip()).upper()
        if value == 'PRE OP':
            value = 'PRE-OP'
        return _RACE_GRADE_RE.fullmatch(value) is not None
    if field == 'placing':
        return type(value) is int and value >= 1
    if field in ('fans', 'fans_gained'):
        return type(value) is int and value >= 0
    return False


def _valid_course(value):
    if not isinstance(value, dict):
        return False
    if any(value.get(key) is None for key in COURSE_FIELDS):
        return False
    if not all(isinstance(value[key], str) and value[key].strip()
               for key in ('venue', 'surface', 'distance_category', 'direction')):
        return False
    return type(value['distance_m']) is int and value['distance_m'] > 0


def _ocr_geometry(row):
    """Copy OCR text/boxes when present without making them an identity rule."""
    lines = []

    def visit(value):
        if isinstance(value, dict):
            text = value.get('text')
            box = value.get('box')
            if (isinstance(text, str) and isinstance(box, (list, tuple))
                    and len(box) == 4):
                lines.append({key: deepcopy(value[key]) for key in
                              ('text', 'confidence', 'conf', 'box')
                              if key in value})
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(nested)

    if isinstance(row, dict):
        visit(row.get('ocr'))
    return lines


def _source_observation(row):
    record = dict(source_timestamp_ms=_time(row),
                  evidence=deepcopy(row.get('evidence')),
                  screen=row.get('screen'))
    facts = row.get('facts') if isinstance(row, dict) else None
    if isinstance(facts, dict) and facts.get('race_identity_refinement'):
        record['race_identity_refinement'] = deepcopy(facts['race_identity_refinement'])
    for key in HASH_FIELDS:
        source = row.get(key) if isinstance(row, dict) else None
        if source is None and isinstance(facts, dict):
            source = facts.get(key)
        if isinstance(source, str) and source:
            record[key] = source
    geometry = _ocr_geometry(row)
    if geometry:
        record['ocr_geometry'] = geometry
    if isinstance(row, dict):
        for key in ('field_evidence', 'alignment_boxes'):
            if key in row:
                record[key] = deepcopy(row[key])
    return record


def _field_observation(row, field, value):
    record = _source_observation(row)
    record['value'] = deepcopy(value)
    return record


def _support_is_repeated(observations):
    times = {item['source_timestamp_ms'] for item in observations
             if _time(item) is not None}
    evidence = set()
    for item in observations:
        value = item.get('evidence')
        if isinstance(value, str) and value:
            evidence.add(value)
        elif isinstance(value, (list, tuple)):
            evidence.update(path for path in value
                            if isinstance(path, str) and path)
    return len(times) >= 2 and len(evidence) >= 2


def _settled(observations, key=lambda item: item['value']):
    """The trailing run of one value, when the earlier values were the fan counter on its way up to it.

    The result panel's fan total counts up over its first frames; the
    settled total is the panel's, and the frames before it are not another
    identity. Anything that is not a counter rolling up to the last value
    leaves the observations as they are.
    """
    ordered = sorted((item for item in observations if _time(item) is not None), key=_time)
    if not ordered:
        return observations
    final = key(ordered[-1])
    run = []
    for item in reversed(ordered):
        if key(item) != final:
            break
        run.append(item)
    earlier = ordered[:len(ordered) - len(run)]
    if not earlier:
        return observations

    def rolling(value):
        if isinstance(value, tuple) and isinstance(final, tuple) and len(value) == len(final):
            return all(x == y or (type(x) is int and type(y) is int and x < y) for x, y in zip(value, final))
        return type(value) is int and type(final) is int and value < final
    return list(reversed(run)) if all(rolling(key(item)) for item in earlier) else observations


def _settled_rows(rows):
    """The result rows from the moment the fan counter settled, when it counted up before."""
    def anchors(row):
        facts = row.get('facts', {}) if isinstance(row, dict) else {}
        if not isinstance(facts, dict) or not all(_valid_scalar(field, facts.get(field)) for field in ANCHOR_FIELDS):
            return None
        return tuple(facts[field] for field in ANCHOR_FIELDS)
    observations = [dict(source_timestamp_ms=_time(row), value=anchors(row), row=row)
                    for row in rows if _time(row) is not None and anchors(row) is not None]
    settled = _settled(observations)
    if settled is observations or not settled:
        return rows
    start = min(item['source_timestamp_ms'] for item in settled)
    return [row for row in rows if _time(row) is not None and _time(row) >= start]


def _repeated_value(observations):
    """The one value a repeated set of observations agrees on, or None."""
    if not _support_is_repeated(observations):
        return None
    values = []
    for item in observations:
        if item['value'] not in values:
            values.append(item['value'])
    return values[0] if len(values) == 1 else None


def _anchor_support(rows):
    observations = []
    for row in rows:
        facts = row.get('facts', {}) if isinstance(row, dict) else {}
        if not isinstance(facts, dict):
            return None
        if not all(_valid_scalar(field, facts.get(field))
                   for field in ANCHOR_FIELDS):
            continue
        observations.append(dict(
            source_timestamp_ms=_time(row),
            evidence=deepcopy(row.get('evidence')),
            value=tuple(facts[field] for field in ANCHOR_FIELDS)))
    if not _support_is_repeated(observations):
        return None
    values = {item['value'] for item in observations}
    return observations if len(values) == 1 else None


def _field_observations(rows, field):
    observations = []
    for row in rows:
        facts = row.get('facts', {}) if isinstance(row, dict) else None
        if not isinstance(facts, dict):
            continue
        value = facts.get(field)
        if field == 'course' and _valid_course(value):
            observations.append(_field_observation(row, field, value))
        elif field != 'course' and _valid_scalar(field, value):
            observations.append(_field_observation(row, field, value))
    return observations


def _identity_proof(rows):
    """Build one identity from split fields, retaining every field witness.

    A complete row is not required.  Every required field must still occur in
    at least two distinct source frames/evidence paths across the uninterrupted
    result sequence, and the placing/fan anchors must repeat on each side of
    the dialog.  Missing fields remain missing rather than being synthesized.
    """
    ordered = sorted((row for row in rows if isinstance(row, dict) and _time(row) is not None),
                     key=_time)
    if not ordered:
        return None
    all_identity_fields = IDENTITY_FIELDS + OPTIONAL_IDENTITY_FIELDS
    field_records = {field: [] for field in all_identity_fields}
    partial_courses = []
    conditions = []
    for row in ordered:
        facts = row.get('facts', {})
        if not isinstance(facts, dict):
            return None
        for field in all_identity_fields:
            value = facts.get(field)
            if value is None:
                continue
            if field == 'course':
                if not isinstance(value, dict):
                    return None
                if _valid_course(value):
                    field_records[field].append(_field_observation(row, field, value))
                else:
                    partial_courses.append((row, value))
                continue
            if not _valid_scalar(field, value):
                # Optional result metadata must remain field-local unknown;
                # one malformed grade cannot split an otherwise evidenced
                # result panel continuation.
                if field in OPTIONAL_IDENTITY_FIELDS:
                    continue
                return None
            field_records[field].append(_field_observation(row, field, value))
        condition = facts.get('course_condition')
        if condition is not None:
            if not isinstance(condition, str) or not condition.strip():
                return None
            conditions.append(condition)
    identity = {}
    for field in IDENTITY_FIELDS:
        observations = field_records[field]
        if not _support_is_repeated(observations):
            return None
        values = [item['value'] for item in observations]
        unique = []
        for value in values:
            if value not in unique:
                unique.append(value)
        if len(unique) != 1:
            return None
        identity[field] = deepcopy(unique[0])
    # A grade is useful result metadata, but it is not required to bridge a
    # playback dialog because older or partially occluded result rows may not
    # expose the header token.  Preserve it in the continuity proof only when
    # the observed grade is valid, consistent, and repeated across source
    # frames; otherwise the race aggregation leaves it field-local unknown.
    for field in OPTIONAL_IDENTITY_FIELDS:
        observations = field_records[field]
        if not observations or not _support_is_repeated(observations):
            continue
        values = [item['value'] for item in observations]
        unique = []
        for value in values:
            if value not in unique:
                unique.append(value)
        if len(unique) == 1:
            identity[field] = deepcopy(unique[0])
    for row, partial in partial_courses:
        for key, value in partial.items():
            if value is not None and key in identity['course'] and value != identity['course'][key]:
                return None
    unique_conditions = list(dict.fromkeys(conditions))
    if len(unique_conditions) > 1:
        return None
    if unique_conditions:
        identity['course_condition'] = unique_conditions[0]
    return dict(identity=identity, field_observations=field_records)


def _latest_panel_rows(group):
    rows = group.get('rows')
    if not isinstance(rows, list):
        return None
    continuations = group.get('result_panel_continuations', [])
    if continuations:
        start = continuations[-1]['resumed_result_ms']
        rows = [row for row in rows if _time(row) is not None and _time(row) >= start]
    return rows


def _continuous_panel(rows, readings):
    """Do not borrow identity fields through an unobserved panel transition."""
    if not rows or any(_time(row) is None for row in rows):
        return False
    start, end = min(map(_time, rows)), max(map(_time, rows))
    source = [row for row in readings if _time(row) is not None
              and start <= _time(row) <= end]
    if not source or any(row.get('screen') != 'race_result' for row in source):
        return False
    times = sorted({_time(row) for row in source})
    if times[0] != start or times[-1] != end:
        return False
    return all(elapsed(a, b) <= MAX_SOURCE_STEP_MS for a, b in zip(times, times[1:]))


def _bridge(previous, following, readings):
    before, after = previous['last_seen_ms'], following['first_seen_ms']
    if (not type(before) is int or not type(after) is int
            or not 0 <= before < after
            or after - before > MAX_DIALOG_SPAN_MS):
        return None
    previous_rows = _latest_panel_rows(previous)
    following_rows = _latest_panel_rows(following)
    if not isinstance(previous_rows, list) or not isinstance(following_rows, list):
        return None
    # The fan total counts up over the panel's first frames; the panel's
    # identity is the settled total, so the frames before it settled are
    # not another panel to compare the return against.
    previous_rows = _settled_rows(previous_rows)
    if not all(_continuous_panel(side, readings) for side in (previous_rows, following_rows)):
        return None
    if _anchor_support(previous_rows) is None or _anchor_support(following_rows) is None:
        return None
    # A field must be independently repeated on both sides.  This keeps a
    # single lucky OCR frame after playback from supplying an identity field;
    # fields split across several frames are still supported when each side
    # has two source witnesses. The course line, the most fragile read of
    # the header, may stand on one witness on the returned panel when it
    # equals the course the panel before showed on two.
    for side in (previous_rows, following_rows):
        for field in IDENTITY_FIELDS:
            observations = _field_observations(side, field)
            if _support_is_repeated(observations):
                continue
            if (side is following_rows and field == 'course' and observations
                    and all(item['value'] == previous_course for item in observations
                            for previous_course in [_repeated_value(_field_observations(previous_rows, 'course'))])):
                continue
            return None
    proof = _identity_proof(previous_rows + following_rows)
    if proof is None:
        return None
    identity = proof['identity']
    # An observed condition on only one panel does not confirm that the return
    # has the same condition. Preserve the earlier conservative distinction.
    conditions = [{row.get('facts', {}).get('course_condition') for row in side
                   if row.get('facts', {}).get('course_condition') is not None}
                  for side in (previous_rows, following_rows)]
    if conditions[0] != conditions[1]:
        return None
    gap = [r for r in readings if isinstance(r, dict) and _time(r) is not None
           and before < _time(r) < after]
    gap = sorted((r for r in gap if isinstance(r, dict)
                  and _time(r) is not None), key=_time)
    if not gap or any(r.get('screen') not in ('unknown', 'playback_confirmation')
                      for r in gap):
        return None
    times = sorted({before, after, *(_time(r) for r in gap)})
    if any(elapsed(a, b) > MAX_SOURCE_STEP_MS for a, b in zip(times, times[1:])):
        return None
    modal = [r for r in gap if r.get('screen') == 'playback_confirmation']
    modal_times = {_time(r) for r in modal}
    modal_evidence = {path for r in modal for path in _evidence_paths(r)}
    if len(modal_times) < 2 or len(modal_evidence) < 2:
        return None
    first = min(_time(r) for r in modal)
    last = max(_time(r) for r in modal)
    if elapsed(before, first) > MAX_TRANSITION_MS or elapsed(last, after) > MAX_TRANSITION_MS:
        return None
    # An unknown screen inside the confirmed dialog could be playback starting;
    # only short transition frames at its edges are permitted.
    if any(first <= _time(r) <= last and r.get('screen') != 'playback_confirmation'
           for r in gap):
        return None
    return dict(
        basis='matching_result_identity_across_observed_playback_dialog',
        previous_result_ms=before, resumed_result_ms=after,
        identity=deepcopy(identity),
        field_observations=deepcopy(proof['field_observations']),
        anchor_fields=list(ANCHOR_FIELDS),
        modal_observations=[_source_observation(r) for r in modal],
        observations=[_source_observation(r) for r in gap],
    ), gap


def join_dialog_returns(groups, readings):
    """Join existing raw result groups; never create a race from a dialog."""
    result = []
    for group in groups:
        bridge = _bridge(result[-1], group, readings) if result else None
        if bridge:
            proof, gap = bridge
            previous = result[-1]
            previous.setdefault('result_panel_continuations', []).append(proof)
            previous.setdefault('_visibility_interruptions', []).extend(gap)
            previous['rows'].extend(group['rows'])
            previous['last_seen_ms'] = group['last_seen_ms']
        else:
            result.append(group)
    for index, group in enumerate(result, 1):
        group['id'] = f'race-{index:03d}'
    return result
