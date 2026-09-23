"""Recover readable turn-opening state panels with bounded source probes.

The ordinary recording sample is deliberately sparse.  This module requests a
small, higher-rate window only when the existing turn ledger says that a
stats or performance opening is missing or partial *and* an existing reading
proves that the requested panel was visible before the committed action.

Every promoted value comes from one newly observed source timestamp.  The
module never builds a tuple from neighboring frames, reads a balance residual,
or treats a later state as an opening.  Recovery rows contain only state
values and provenance; action, event, award, and calendar fields are stripped
so callers can exclude them from event grouping while the turn ledger uses the
actual state readings.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .calendar_coverage import date_key
from .reconcile import FIELDS


SCHEMA = 'tracen-replay/boundary-state-recovery-v1'
PERFORMANCE_FIELDS = ('dance', 'passion', 'vocal', 'visual', 'composure')
CHANNEL_FIELDS = {'stats': FIELDS, 'performance': PERFORMANCE_FIELDS}
DEFAULT_RADIUS_MS = 200
DEFAULT_MAX_WINDOWS = 12
DEFAULT_MAX_DURATION_MS = 6000
DEFAULT_FPS = 60
_PHASE_BOUNDARIES = frozenset({'Junior Year Pre-Debut', 'Finale Underway'})
# The identity of a finale race window: its phase, with whatever countdown a
# row shows. The three finale races all read "1 turn left", so the label
# cannot tell them apart; the race advance that opened the window does, and
# the window's own bounds decide which rows are its own.
_ANY_COUNTDOWN = 'any_countdown'


def _proof(value):
    """Return unique non-empty evidence paths without inventing one."""
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return list(dict.fromkeys(value))
    return []


def _evidence_value(proofs):
    """Keep the usual scalar reading shape when one proof exists."""
    if len(proofs) == 1:
        return proofs[0]
    return list(proofs)


def _observed_values(row, channel):
    """Return only integer values actually present in one source row."""
    if channel == 'stats':
        container = row.get('stats')
        values = container.get('values') if isinstance(container, dict) else None
    else:
        facts = row.get('facts')
        values = facts.get('performance_points') if isinstance(facts, dict) else None
    if not isinstance(values, dict):
        return {}
    return {field: values[field] for field in CHANNEL_FIELDS[channel]
            if type(values.get(field)) is int}


def _valid_time(row):
    value = row.get('source_timestamp_ms') if isinstance(row, dict) else None
    return value if type(value) is int and value >= 0 else None


def _reading_index_from_ref(value):
    """Return a reading index only for the canonical local reading pointer."""
    prefix = '/gameplay_tracking/readings/'
    if not isinstance(value, str) or not value.startswith(prefix):
        return None
    suffix = value[len(prefix):]
    if not suffix.isdigit():
        return None
    return int(suffix)


def _source_acceptance_index(report):
    """Index explicit same-frame state proofs by their source reading.

    ``state_observations`` is the producer's accepted source-observation
    channel.  The index deliberately keeps every claim for a reading instead
    of choosing one, so duplicate or conflicting claims remain ambiguous and
    cannot become a singleton opening merely because one has convenient
    values.
    """
    data = report.get('gameplay_tracking', {}) if isinstance(report, dict) else {}
    observations = data.get('state_observations') if isinstance(data, dict) else None
    result = {}
    if not isinstance(observations, list):
        return result
    for observation in observations:
        if not isinstance(observation, dict):
            continue
        payload = observation.get('payload')
        channel = payload.get('channel') if isinstance(payload, dict) else None
        if channel not in CHANNEL_FIELDS:
            continue
        source_observations = observation.get('source_observations')
        if not isinstance(source_observations, list):
            continue
        for source_observation in source_observations:
            if not isinstance(source_observation, dict):
                continue
            index = _reading_index_from_ref(source_observation.get('source_ref'))
            if index is None:
                continue
            result.setdefault((index, channel), []).append((observation, source_observation))
    return result


def _source_acceptance_reason(report, readings, index, row, channel, *, start_ms,
                              action_time_ms, expected_boundary, acceptance_index):
    """Validate one direct singleton against its accepted source proof.

    A numeric row and a calendar label are useful discovery evidence, but they
    do not by themselves establish that the row is a trustworthy endpoint.
    Promotion therefore requires one explicit state-observation proof tied to
    this exact reading, timestamp, value source, and field set.  The checks are
    structural and source-local; they never compare a value with a target.
    """
    if channel not in CHANNEL_FIELDS:
        return 'invalid_source_channel'
    if not isinstance(index, int) or not 0 <= index < len(readings):
        return 'invalid_source_reading_index'
    if type(start_ms) is not int or type(action_time_ms) is not int:
        return 'invalid_owner_time_window'
    if readings[index] is not row:
        # A candidate supplied by a caller must still identify the actual row
        # at its canonical index.  This blocks fabricated values with a real
        # looking source pointer.
        return 'source_reading_identity_mismatch'
    time = _valid_time(row)
    if time is None or not start_ms <= time < action_time_ms:
        return 'outside_owner_pre_action_window'
    if expected_boundary is not None and not _identity_matches(boundary_identity(row), expected_boundary):
        return 'source_boundary_mismatch'
    source = report.get('source', {}) if isinstance(report, dict) else {}
    expected_source = source.get('sha256') if isinstance(source, dict) else None
    row_source = row.get('source_sha256', row.get('source_video_sha256'))
    if expected_source is not None and row_source is not None and row_source != expected_source:
        return 'source_hash_mismatch'
    evidence = _proof(row.get('evidence'))
    values = _observed_values(row, channel)
    if not evidence or not values:
        return 'missing_source_values_or_evidence'
    # A duplicate PTS cannot be made unambiguous by choosing one list index.
    if sum(_valid_time(candidate) == time for candidate in readings) != 1:
        return 'duplicate_source_timestamp'
    claims = acceptance_index.get((index, channel), [])
    if len(claims) != 1:
        return 'missing_or_ambiguous_state_observation_proof'
    observation, source_observation = claims[0]
    payload = observation.get('payload')
    if (observation.get('category') != 'state' or observation.get('phase') != 'observed'
            or observation.get('uncertain') is not False
            or observation.get('cross_frame_values_merged') is not False
            or observation.get('reconciliation_endpoint_inferred') is not False):
        return 'state_observation_not_accepted'
    if not isinstance(payload, dict) or payload.get('kind') != 'state' \
            or payload.get('channel') != channel or not isinstance(payload.get('values'), dict):
        return 'state_observation_shape_mismatch'
    fields = CHANNEL_FIELDS[channel]
    payload_values = payload['values']
    payload_ints = {field: payload_values[field] for field in fields
                    if type(payload_values.get(field)) is int and payload_values[field] >= 0}
    if payload_ints != values:
        return 'state_observation_field_mismatch'
    expected_value_source = 'stats.values' if channel == 'stats' else 'facts.performance_points'
    if (source_observation.get('source_ref') != f'/gameplay_tracking/readings/{index}'
            or source_observation.get('source_timestamp_ms') != time
            or source_observation.get('value_source') != expected_value_source):
        return 'source_observation_pointer_mismatch'
    source_evidence = _proof(source_observation.get('evidence'))
    if not source_evidence or not set(source_evidence).issubset(set(evidence)):
        return 'source_observation_evidence_mismatch'
    source_values = source_observation.get('values')
    if not isinstance(source_values, dict):
        return 'source_observation_values_missing'
    source_ints = {field: source_values[field] for field in fields
                   if type(source_values.get(field)) is int and source_values[field] >= 0}
    if source_ints != values:
        return 'source_observation_field_mismatch'
    # The accepted proof may be a stable run of identical frames, but every
    # member must remain within the same owner pre-action interval.  This
    # retains the opening timestamp as the actual source row while rejecting
    # a grouped proof that crosses a temporal boundary.
    all_source_observations = observation.get('source_observations')
    if not isinstance(all_source_observations, list) or not all_source_observations:
        return 'source_observation_members_missing'
    seen_times = set()
    for member in all_source_observations:
        if not isinstance(member, dict):
            return 'malformed_source_observation_member'
        member_index = _reading_index_from_ref(member.get('source_ref'))
        if member_index is None or not 0 <= member_index < len(readings):
            return 'source_observation_pointer_mismatch'
        member_row = readings[member_index]
        member_time = member.get('source_timestamp_ms')
        if type(member_time) is not int or not start_ms <= member_time < action_time_ms:
            return 'source_observation_temporal_ambiguity'
        if _valid_time(member_row) != member_time:
            return 'source_observation_timestamp_mismatch'
        if member_time in seen_times:
            return 'duplicate_source_observation_timestamp'
        seen_times.add(member_time)
        member_evidence = _proof(member.get('evidence'))
        member_row_evidence = _proof(member_row.get('evidence'))
        if not member_evidence or not set(member_evidence).issubset(set(member_row_evidence)):
            return 'source_observation_evidence_mismatch'
        if member.get('value_source') != expected_value_source:
            return 'source_observation_pointer_mismatch'
        member_values = member.get('values')
        if not isinstance(member_values, dict):
            return 'source_observation_values_missing'
        member_ints = {field: member_values[field] for field in fields
                       if type(member_values.get(field)) is int and member_values[field] >= 0}
        actual_member_values = _observed_values(member_row, channel)
        if member_ints != actual_member_values:
            return 'source_observation_field_mismatch'
        if member_ints != payload_ints:
            return 'conflicting_source_observation_values'
    return None


def boundary_identity(row):
    """Return the explicit calendar identity carried by one gameplay row.

    A numeric panel is useful for an endpoint only when the same gameplay
    boundary is visible in that source row.  The calendar label is the only
    identity accepted here: action context, OCR confidence, and proximity to
    an action are not substitutes for a boundary.  ``None`` means that the
    source row does not prove which turn it belongs to.
    """
    if not isinstance(row, dict):
        return None
    stats = row.get('stats')
    if not isinstance(stats, dict):
        return None
    text = stats.get('calendar_text')
    ordinal = date_key(text)
    if ordinal is not None:
        return ('dated', ordinal)
    if text in _PHASE_BOUNDARIES:
        remaining = stats.get('turns_remaining_to_goal')
        if type(remaining) is int and remaining >= 0:
            return (text, remaining)
        # An unnumbered phase is deliberately kept distinct from every
        # numbered segment.  It cannot prove ownership of a numbered turn.
        return (text, None)
    return None


def turn_boundary_identity(turn):
    """Return the identity required for an opening in one ledger turn."""
    if not isinstance(turn, dict):
        return None
    phase = turn.get('phase')
    value = turn.get('calendar_value')
    if phase == 'dated' and type(value) is int:
        return ('dated', value)
    if phase in _PHASE_BOUNDARIES:
        if type(value) is int and value >= 0:
            return (phase, value)
        if value is None and turn.get('window_kind') == 'phase_race_turn':
            return (phase, _ANY_COUNTDOWN)
    return None


def _identity_matches(identity, expected):
    """Whether a row's identity proves it belongs to a turn with this one.

    A dated or numbered turn needs the same date or countdown on the row. A
    finale race window needs only its phase: its rows are told apart from
    the neighbouring races' by the window's bounds, not by a countdown.
    """
    expected = _identity_tuple(expected)
    identity = _identity_tuple(identity)
    if expected is None or identity is None:
        return False
    if expected[1] == _ANY_COUNTDOWN:
        return identity[0] == expected[0]
    return identity == expected


def _ownership_basis(expected):
    expected = _identity_tuple(expected)
    if expected is None:
        return 'bounded_pre_action_source_window'
    if expected[1] == _ANY_COUNTDOWN:
        return 'same_phase_inside_finale_race_window_before_action'
    return 'same_calendar_boundary_before_action'


def _identity_json(identity):
    """Make a tuple identity safe and stable in JSON provenance."""
    return list(identity) if isinstance(identity, tuple) else identity


def _identity_tuple(identity):
    """Normalize an in-memory or JSON boundary identity for comparison."""
    if isinstance(identity, tuple) and len(identity) == 2:
        return identity
    if isinstance(identity, list) and len(identity) == 2:
        return tuple(identity)
    return None


def _turn_action_times(report):
    """Map ledger turn IDs to the first committed action observed for them."""
    ledger = report.get('turn_ledger') if isinstance(report, dict) else None
    timeline = ledger.get('timeline', []) if isinstance(ledger, dict) else []
    result = {}
    for entry in timeline:
        if not isinstance(entry, dict) or entry.get('kind') != 'committed_action':
            continue
        turn_id = entry.get('turn_id')
        time = entry.get('first_seen_ms')
        if not isinstance(turn_id, str) or type(time) is not int:
            continue
        result.setdefault(turn_id, []).append(time)
    return result


def _missing_channel_fields(turn, channel):
    """Return target fields when a turn opening is absent or incomplete."""
    state = turn.get('states', {}).get(channel) if isinstance(turn, dict) else None
    if not isinstance(state, dict):
        return None
    opening = state.get('opening')
    status = state.get('opening_status')
    if opening is None or status in ('not_observed_before_action', 'partially_observed'):
        return tuple(CHANNEL_FIELDS[channel])
    values = opening.get('values') if isinstance(opening, dict) else None
    if not isinstance(values, dict) or any(type(values.get(field)) is not int for field in CHANNEL_FIELDS[channel]):
        return tuple(CHANNEL_FIELDS[channel])
    return ()


def _candidate_for(readings, channel, start_ms, action_time_ms, *, expected_boundary=None,
                   expected_source_sha256=None, candidate_filter=None):
    """Choose one most-readable actual pre-action source row.

    The tie-breaker is earliest source time.  Once promoted, the ledger can
    select the first corroborated reading as the opening, which is safer than
    centering a probe on a later transition in the same turn.  When an
    ``expected_boundary`` is supplied, an explicit row calendar identity is
    required and must match it.  This prevents a complete panel in an action
    context, or a nearby stale panel from becoming an opening merely because
    it falls inside the time window.
    """
    expected_boundary = _identity_tuple(expected_boundary)
    candidates = []
    for index, row in enumerate(readings):
        if not isinstance(row, dict) or row.get('screen') == 'boundary_state_recovery':
            continue
        time = _valid_time(row)
        if time is None or not start_ms <= time < action_time_ms:
            continue
        row_source_sha256 = row.get('source_sha256', row.get('source_video_sha256'))
        if (expected_source_sha256 is not None and row_source_sha256 is not None
                and row_source_sha256 != expected_source_sha256):
            continue
        identity = boundary_identity(row)
        if expected_boundary is not None and not _identity_matches(identity, expected_boundary):
            continue
        evidence = _proof(row.get('evidence'))
        values = _observed_values(row, channel)
        if not evidence or not values:
            continue
        if candidate_filter is not None and not candidate_filter(
                index, row, channel, start_ms, action_time_ms, expected_boundary):
            continue
        candidates.append(dict(index=index, source_timestamp_ms=time, values=values,
                              observed_fields=tuple(field for field in CHANNEL_FIELDS[channel]
                                                     if field in values), evidence=evidence,
                              readability=len(values), boundary_identity=identity,
                              ownership_basis=_ownership_basis(expected_boundary)))
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: (
        candidate['readability'],
        -candidate['source_timestamp_ms'],
        -candidate['index']))


def _validate_radius(radius_ms):
    if type(radius_ms) is not int or not 150 <= radius_ms <= 200:
        raise ValueError('probe_radius_ms must be an integer between 150 and 200')


def _candidate_windows(report, readings, *, probe_radius_ms=DEFAULT_RADIUS_MS,
                       candidate_filter=None):
    """Build one bounded request per missing channel with visible evidence."""
    _validate_radius(probe_radius_ms)
    if not isinstance(report, dict) or not isinstance(report.get('source'), dict):
        raise ValueError('Boundary recovery requires report.source metadata')
    duration = report['source'].get('duration_ms')
    if type(duration) is not int or duration <= 0:
        raise ValueError('report.source.duration_ms must be a positive integer')
    ledger = report.get('turn_ledger')
    if not isinstance(ledger, dict) or not isinstance(ledger.get('turns'), list):
        raise ValueError('Boundary recovery requires report.turn_ledger.turns')
    action_times = _turn_action_times(report)
    requests = {}
    for turn in ledger['turns']:
        if not isinstance(turn, dict) or not isinstance(turn.get('id'), str):
            continue
        turn_id = turn['id']
        start_ms = turn.get('start_ms')
        if type(start_ms) is not int or start_ms < 0:
            continue
        actions = action_times.get(turn_id, [])
        # A missing action gives no safe pre-action boundary.  Multiple
        # actions are still usable up to the first action; new rows are state
        # only and therefore cannot be mistaken for either action.
        if not actions:
            continue
        action_time_ms = min(actions)
        if action_time_ms <= start_ms:
            continue
        expected_boundary = turn_boundary_identity(turn)
        # A source probe is useful only when the turn itself has an explicit
        # identity and a candidate row repeats that identity.  In particular,
        # a complete panel on a rejected action-context row cannot establish
        # opening ownership by itself.
        if expected_boundary is None:
            continue
        for channel in CHANNEL_FIELDS:
            missing_fields = _missing_channel_fields(turn, channel)
            if missing_fields is None or not missing_fields:
                continue
            candidate = _candidate_for(
                readings, channel, start_ms, action_time_ms,
                expected_boundary=expected_boundary,
                expected_source_sha256=report['source'].get('sha256'),
                candidate_filter=candidate_filter)
            # No source value means no evidence that a denser probe can help.
            # In particular, do not sample the initial UI-absent turns.
            if candidate is None:
                continue
            probe_start = max(start_ms, candidate['source_timestamp_ms'] - probe_radius_ms)
            probe_end = min(action_time_ms, candidate['source_timestamp_ms'] + probe_radius_ms)
            if not probe_start < probe_end:
                continue
            key = (turn_id, probe_start, probe_end)
            request = requests.setdefault(key, dict(
                owner_turn_id=turn_id,
                owner_start_ms=start_ms,
                owner_action_time_ms=action_time_ms,
                start_ms=probe_start,
                end_ms=probe_end,
                probe_radius_ms=probe_radius_ms,
                reason='missing_or_partial_turn_opening',
                action_count=len(actions),
                channels=[],
                priority=0,
                boundary_identity=_identity_json(expected_boundary),
                ownership_basis=_ownership_basis(expected_boundary),
                source_sha256=report['source'].get('sha256'),
            ))
            request['channels'].append(dict(
                channel=channel,
                requested_fields=list(missing_fields),
                candidate_index=candidate['index'],
                candidate_source_timestamp_ms=candidate['source_timestamp_ms'],
                candidate_observed_fields=list(candidate['observed_fields']),
                candidate_evidence=candidate['evidence'],
                candidate_readability=candidate['readability'],
                    candidate_boundary_identity=_identity_json(candidate['boundary_identity']),
                    ownership_basis=candidate['ownership_basis'],
                    source_sha256=request.get('source_sha256'),
            ))
            # Complete source panels are the most valuable bounded probes,
            # especially singleton snapshots such as independent-02 T29.
            request['priority'] = max(
                request['priority'],
                2 if candidate['readability'] == len(CHANNEL_FIELDS[channel]) else 1,
            )
    result = []
    for request in requests.values():
        # Same-turn/channel requests normally have one key.  Stable ordering
        # makes manifests and tests reproducible when two channels share a
        # source timestamp.
        request['channels'].sort(key=lambda item: item['channel'])
        request['id'] = 'boundary-' + request['owner_turn_id'] + '-' + str(request['start_ms'])
        result.append(request)
    return sorted(result, key=lambda item: (
        -item['priority'],
        -max(channel['candidate_readability'] for channel in item['channels']),
        item['owner_start_ms'],
        item['start_ms'],
        item['id']))


def _budget_windows(windows, max_windows, max_duration_ms):
    if type(max_windows) is not int or max_windows < 0:
        raise ValueError('max_windows must be a non-negative integer')
    if type(max_duration_ms) is not int or max_duration_ms < 0:
        raise ValueError('max_duration_ms must be a non-negative integer')
    selected, pending = [], []
    used = 0
    for window in windows:
        duration = window['end_ms'] - window['start_ms']
        if len(selected) >= max_windows or used + duration > max_duration_ms:
            pending.append(dict(window, pending_reason='source_probe_budget'))
            continue
        selected.append(deepcopy(window))
        used += duration
    # Consumers generally process readings chronologically even though the
    # budget prioritizes complete snapshots first.
    selected.sort(key=lambda item: (item['start_ms'], item['id']))
    return selected, pending, used


def candidate_windows(report, readings, *, probe_radius_ms=DEFAULT_RADIUS_MS):
    """Return all source-backed candidates before applying the probe budget."""
    return _candidate_windows(report, readings, probe_radius_ms=probe_radius_ms)


def endpoint_candidates(report, readings, *, probe_radius_ms=DEFAULT_RADIUS_MS,
                        candidate_filter=None):
    """Expose unmerged, source-bound opening evidence for an audit.

    This is intentionally an evidence view rather than a ledger mutation.  A
    candidate reports values from one existing reading and its own pointer;
    neighboring partial readings are listed only by the bounded recovery
    window.  Callers may use :func:`promote_existing_endpoints` for a direct
    single-reading opening, or :func:`promote` after a validated source probe
    when repeated source state is available.
    """
    result = []
    for window in _candidate_windows(report, readings, probe_radius_ms=probe_radius_ms,
                                     candidate_filter=candidate_filter):
        for request in window.get('channels', []):
            index = request.get('candidate_index')
            if type(index) is not int or not 0 <= index < len(readings):
                continue
            row = readings[index]
            channel = request.get('channel')
            values = _observed_values(row, channel)
            if not values:
                continue
            source_ref = f'/gameplay_tracking/readings/{index}/'
            source_ref += 'stats/values' if channel == 'stats' else 'facts/performance_points'
            result.append(dict(
                endpoint='opening',
                owner_turn_id=window['owner_turn_id'],
                owner_start_ms=window['owner_start_ms'],
                owner_action_time_ms=window['owner_action_time_ms'],
                channel=channel,
                requested_fields=list(request.get('requested_fields', [])),
                source_reading_index=index,
                source_timestamp_ms=request['candidate_source_timestamp_ms'],
                source_ref=source_ref,
                values=deepcopy(values),
                evidence=deepcopy(request.get('candidate_evidence', [])),
                boundary_identity=deepcopy(request.get('candidate_boundary_identity')),
                ownership_basis=request.get(
                    'ownership_basis', 'same_calendar_boundary_before_action'),
                window_id=window['id'],
                window_start_ms=window['start_ms'],
                window_end_ms=window['end_ms'],
                source_sha256=window.get('source_sha256'),
                merged_from_multiple_frames=False,
            ))
    return result


def _opening_state_from_candidate(candidate):
    """Convert one actual candidate into the ledger's standard state shape.

    The values and pointer remain tied to the one source reading.  Boundary
    and run ownership stay on the surrounding projection record so a caller
    can validate them before inserting this state into a ledger.
    """
    channel = candidate.get('channel')
    values_ref = candidate.get('source_ref')
    values = candidate.get('values')
    if channel not in CHANNEL_FIELDS or not isinstance(values_ref, str) or not values_ref:
        return None
    if not isinstance(values, dict) or not values:
        return None
    fields = CHANNEL_FIELDS[channel]
    if any(field not in fields or type(value) is not int for field, value in values.items()):
        return None
    source_ref = values_ref.rsplit('/', 1)[0]
    return dict(
        source_ref=source_ref,
        values_ref=values_ref,
        observed_at_ms=candidate.get('source_timestamp_ms'),
        values=deepcopy(values),
        evidence=deepcopy(candidate.get('evidence', [])),
        basis='source_bound_single_frame_before_action',
        exact_turn_boundary=(candidate.get('source_timestamp_ms') == candidate.get('owner_start_ms')),
    )


def promote_existing_endpoints(report, readings, *, probe_radius_ms=DEFAULT_RADIUS_MS,
                               candidates=None):
    """Build source-bound opening projections from already accepted readings.

    A dense source probe is useful when a panel is missing from the ordinary
    cache, but it must not be required when the ordinary producer already has
    one complete, source-bound reading.  This helper exposes that normal
    producer path without mutating either ``report`` or ``readings``.  The
    returned projection contains one standard ledger opening state per actual
    source row; it never joins fields from different rows.
    """
    source = report.get('source', {}) if isinstance(report, dict) else {}
    source_sha256 = source.get('sha256') if isinstance(source, dict) else None
    acceptance_index = _source_acceptance_index(report)

    def accepted_candidate(index, row, channel, start_ms, action_time_ms, expected_boundary):
        return _source_acceptance_reason(
            report, readings, index, row, channel,
            start_ms=start_ms, action_time_ms=action_time_ms,
            expected_boundary=expected_boundary, acceptance_index=acceptance_index) is None

    if candidates is None:
        candidates = endpoint_candidates(
            report, readings, probe_radius_ms=probe_radius_ms,
            candidate_filter=accepted_candidate)
    else:
        candidates = [candidate for candidate in candidates
                      if isinstance(candidate, dict)
                      and _source_acceptance_reason(
                          report, readings, candidate.get('source_reading_index'),
                          readings[candidate['source_reading_index']]
                          if isinstance(candidate.get('source_reading_index'), int)
                          and 0 <= candidate['source_reading_index'] < len(readings)
                          else None,
                          candidate.get('channel'),
                          start_ms=candidate.get('owner_start_ms'),
                          action_time_ms=candidate.get('owner_action_time_ms'),
                          expected_boundary=candidate.get('boundary_identity'),
                          acceptance_index=acceptance_index) is None]
    projections = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        state = _opening_state_from_candidate(candidate)
        if state is None:
            continue
        projection = deepcopy(candidate)
        projection['opening_state'] = state
        projection['observed_fields'] = sorted(state['values'])
        projection['complete'] = set(state['values']) == set(CHANNEL_FIELDS[candidate['channel']])
        projection['source_sha256'] = source_sha256
        projections.append(projection)
    return projections


def apply_opening_endpoint_projections(turn_ledger, projections, *, source_sha256=None):
    """Apply validated single-reading openings to a ledger copy.

    This is a pure projection helper for the normal producer/replay path.
    Callers can pass the returned ledger to their ledger and causal builders
    once those builders have been wired to this projection hook.  A previous
    turn's derived closing is refreshed only when it is
    explicitly the next-turn opening link.  The last turn's closing is never
    filled by an opening projection.
    """
    result = deepcopy(turn_ledger)
    turns = result.get('turns') if isinstance(result, dict) else None
    if not isinstance(turns, list):
        return result, [], [dict(reason='missing_turn_ledger')]
    by_id = {turn.get('id'): (index, turn) for index, turn in enumerate(turns)
             if isinstance(turn, dict) and isinstance(turn.get('id'), str)}
    accepted, rejected = [], []
    for projection in projections:
        if not isinstance(projection, dict):
            rejected.append(dict(reason='malformed_projection'))
            continue
        turn_id = projection.get('owner_turn_id')
        channel = projection.get('channel')
        state = projection.get('opening_state')
        index_turn = by_id.get(turn_id)
        if index_turn is None or channel not in CHANNEL_FIELDS or not isinstance(state, dict):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='unknown_owner_or_channel'))
            continue
        index, turn = index_turn
        expected_boundary = turn_boundary_identity(turn)
        declared_boundary = _identity_tuple(projection.get('boundary_identity'))
        declared_source = projection.get('source_sha256')
        if (expected_boundary is None or not _identity_matches(declared_boundary, expected_boundary)
                or (source_sha256 is not None and declared_source != source_sha256)):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='source_or_boundary_mismatch'))
            continue
        observed_at = state.get('observed_at_ms')
        owner_start = projection.get('owner_start_ms')
        owner_action = projection.get('owner_action_time_ms')
        turn_start = turn.get('start_ms')
        turn_end = turn.get('end_ms')
        if (type(observed_at) is not int or type(owner_start) is not int
                or type(owner_action) is not int or type(turn_start) is not int
                or type(turn_end) is not int or not owner_start <= observed_at < owner_action
                or observed_at < turn_start or observed_at >= turn_end):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='outside_owner_pre_action_window'))
            continue
        values = state.get('values')
        observed_field_list = projection.get('observed_fields')
        if not isinstance(observed_field_list, (list, tuple, set)):
            observed_field_list = values if isinstance(values, dict) else ()
        if (not isinstance(values, dict)
                or any(field not in CHANNEL_FIELDS[channel] or type(values.get(field)) is not int
                       for field in observed_field_list)):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='malformed_state_values'))
            continue
        if (not isinstance(state.get('source_ref'), str) or not state['source_ref']
                or not isinstance(state.get('values_ref'), str) or not state['values_ref']
                or not _proof(state.get('evidence'))):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='missing_source_proof'))
            continue
        state_container = turn.setdefault('states', {}).setdefault(channel, {})
        existing = state_container.get('opening')
        existing_values = existing.get('values') if isinstance(existing, dict) else None
        existing_fields = {field for field in CHANNEL_FIELDS[channel]
                           if isinstance(existing_values, dict)
                           and type(existing_values.get(field)) is int}
        observed_fields = {field for field in CHANNEL_FIELDS[channel] if type(values.get(field)) is int}
        overlapping_fields = existing_fields & observed_fields
        if (isinstance(existing_values, dict)
                and any(existing_values[field] != values[field] for field in overlapping_fields)):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='conflicting_overlapping_opening'))
            continue
        if existing_fields and not existing_fields.issubset(observed_fields):
            rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                 reason='projection_would_drop_observed_fields'))
            continue
        if existing_fields == observed_fields and isinstance(existing_values, dict):
            if existing_values == values:
                rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                     reason='opening_already_observed'))
            else:
                rejected.append(dict(owner_turn_id=turn_id, channel=channel,
                                     reason='conflicting_partial_opening'))
            continue
        state_container['opening'] = deepcopy(state)
        state_container['opening_status'] = (
            'observed' if observed_fields == set(CHANNEL_FIELDS[channel]) else 'partially_observed')
        accepted.append(dict(
            owner_turn_id=turn_id,
            channel=channel,
            source_timestamp_ms=observed_at,
            source_ref=state.get('source_ref'),
            values_ref=state.get('values_ref'),
            observed_fields=sorted(observed_fields),
            complete=observed_fields == set(CHANNEL_FIELDS[channel]),
            boundary_identity=deepcopy(projection.get('boundary_identity')),
            ownership_basis=projection.get('ownership_basis', 'same_calendar_boundary_before_action'),
            source_sha256=declared_source,
            merged_from_multiple_frames=False,
        ))
        if index > 0:
            previous = turns[index - 1]
            previous_state = previous.setdefault('states', {}).setdefault(channel, {})
            previous_basis = previous_state.get('closing_basis')
            if previous_basis in ('unavailable', 'next_turn_first_observed_state'):
                previous_state['closing'] = deepcopy(state_container['opening'])
                previous_state['closing_basis'] = 'next_turn_first_observed_state'
    return result, accepted, rejected


def _matching_channels(window, time):
    if not window['start_ms'] <= time < window['end_ms']:
        return []
    return window.get('channels', [])


def _new_state_row(time, evidence, channel_values, metadata):
    """Make a state-only reading that cannot create an event or action."""
    stats_values = channel_values.get('stats', {})
    performance_values = channel_values.get('performance', {})
    row = dict(
        screen='boundary_state_recovery',
        stats={'values': deepcopy(stats_values)} if stats_values else {},
        training_option=None,
        effects=[],
        facts={},
        completed_action=None,
        context_title=None,
        context_title_candidate=None,
        ocr={'neural': []},
        source_timestamp_ms=time,
        evidence=evidence,
    )
    if performance_values:
        row['facts']['performance_points'] = deepcopy(performance_values)
    row['facts']['boundary_state_recovery'] = deepcopy(metadata)
    return row


def promote(readings, fresh, windows):
    """Promote only new, in-owner actual state values from inspected frames.

    Existing readings are returned unchanged.  If overlapping windows expose
    different channels at one physical timestamp, those channels may share
    the row because they came from the same source frame.  A conflicting
    second interpretation of one channel at that timestamp is ignored rather
    than merged.
    """
    existing_times = {_valid_time(row) for row in readings if _valid_time(row) is not None}
    additions = {}
    rows_by_time = {}
    for row in fresh:
        if not isinstance(row, dict):
            continue
        time = _valid_time(row)
        if time is not None:
            rows_by_time.setdefault(time, []).append(row)
    for time in sorted(rows_by_time):
        if time in existing_times:
            continue
        source_rows = rows_by_time[time]
        matches = [window for window in windows if _matching_channels(window, time)]
        owners = {window.get('owner_turn_id') for window in matches}
        if len(owners) != 1:
            continue
        owner = next(iter(owners))
        values_by_channel = {}
        evidence_by_channel = {}
        metadata = []
        for window in matches:
            for request in _matching_channels(window, time):
                channel = request['channel']
                requested = set(request.get('requested_fields', CHANNEL_FIELDS[channel]))
                observations = []
                for row in source_rows:
                    expected_source = request.get('source_sha256', window.get('source_sha256'))
                    row_source = row.get('source_sha256', row.get('source_video_sha256'))
                    if (expected_source is not None and row_source is not None
                            and row_source != expected_source):
                        continue
                    raw_expected_identity = request.get('candidate_boundary_identity')
                    if raw_expected_identity is None:
                        raw_expected_identity = window.get('boundary_identity')
                    expected_identity = _identity_tuple(raw_expected_identity)
                    # A malformed declared owner is safer than an omitted
                    # owner: do not accept a probe whose provenance cannot be
                    # compared to one explicit turn boundary.
                    if raw_expected_identity is not None and expected_identity is None:
                        continue
                    expected_identity_json = _identity_json(expected_identity)
                    row_identity = boundary_identity(row)
                    if (expected_identity_json is not None
                            and not _identity_matches(row_identity, expected_identity)):
                        # A probe can straddle a UI transition.  Do not let a
                        # readable panel from the other side of that
                        # transition satisfy this request.
                        continue
                    evidence = _proof(row.get('evidence'))
                    observed = {field: value for field, value in _observed_values(row, channel).items()
                                if field in requested}
                    if observed and evidence:
                        observations.append((observed, evidence))
                if not observations:
                    continue
                signatures = {json.dumps(values, sort_keys=True) for values, _ in observations}
                if len(signatures) != 1:
                    # Same source timestamp, different OCR interpretations:
                    # reject the channel rather than silently choosing a view
                    # or building a composite from conflicting readings.
                    continue
                values_by_channel[channel] = deepcopy(observations[0][0])
                evidence_by_channel[channel] = observations[0][1]
                metadata.append(dict(
                    owner_turn_id=owner,
                    owner_start_ms=window['owner_start_ms'],
                    owner_action_time_ms=window['owner_action_time_ms'],
                    window_id=window['id'],
                    window_start_ms=window['start_ms'],
                    window_end_ms=window['end_ms'],
                    channel=channel,
                    requested_fields=list(request.get('requested_fields', [])),
                    observed_fields=sorted(observations[0][0]),
                    candidate_source_timestamp_ms=request.get('candidate_source_timestamp_ms'),
                    candidate_evidence=deepcopy(request.get('candidate_evidence', [])),
                    candidate_boundary_identity=deepcopy(expected_identity_json),
                    ownership_basis=request.get(
                        'ownership_basis', 'bounded_recovery_window_with_source_boundary'),
                    source_sha256=expected_source,
                    source_timestamp_ms=time,
                    source_evidence=deepcopy(observations[0][1]),
                ))
        if not values_by_channel:
            continue
        evidence = next(iter(evidence_by_channel.values()))
        additions[time] = _new_state_row(
            time,
            _evidence_value(evidence),
            values_by_channel,
            metadata,
        )
    result = list(readings) + list(additions.values())
    result.sort(key=lambda row: row['source_timestamp_ms'])
    return result


def _relocate(value, directory, root):
    """Relocate cached proof paths from the recovery root to report root."""
    if isinstance(value, dict):
        return {key: _relocate(item, directory, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_relocate(item, directory, root) for item in value]
    if isinstance(value, str) and value.endswith(('.png', '.jpg', '.json')):
        path = directory / value
        if path.is_file():
            return path.relative_to(root).as_posix()
    return value


def _cached_model_provenance(inspection, directory):
    models = {}
    for observed in inspection.get('readings', []):
        evidence = observed.get('evidence')
        if not isinstance(evidence, str):
            raise ValueError('Boundary recovery reading lacks evidence')
        raw_path = (directory / evidence).with_suffix('.v2.json')
        if not raw_path.exists():
            raise ValueError('Boundary recovery OCR cache is missing')
        raw = json.loads(raw_path.read_text(encoding='utf-8'))
        fingerprint = raw.get('engine_fingerprint')
        model_sha256 = raw.get('model_sha256')
        if not fingerprint or not model_sha256:
            raise ValueError('Boundary recovery cache lacks OCR provenance')
        if fingerprint in models and models[fingerprint] != model_sha256:
            raise ValueError('One boundary recovery OCR fingerprint has different models')
        models[fingerprint] = model_sha256
    return models


def recover(source, root, report, readings, *, allow_ocr=True,
            model_dir='.local/models/rapidocr', fps=DEFAULT_FPS,
            probe_radius_ms=DEFAULT_RADIUS_MS, max_windows=DEFAULT_MAX_WINDOWS,
            max_duration_ms=DEFAULT_MAX_DURATION_MS, dense_workers=1):
    """Run a bounded boundary pass and return ``(readings, metadata)``.

    New source windows use :func:`tracen_replay.inspect_receipts.inspect` and
    the full ``NeuralReader``.  Completed windows can be validated and
    reparsed with ``allow_ocr=False``; uncached windows remain pending in the
    metadata without constructing an OCR reader.
    """
    root = Path(root)
    source_path = Path(source)
    source_info = report.get('source') if isinstance(report, dict) else None
    if not isinstance(source_info, dict):
        raise ValueError('Boundary recovery requires report.source metadata')
    if fps not in (30, 60):
        raise ValueError('Boundary recovery fps must be 30 or 60')
    _validate_radius(probe_radius_ms)
    all_windows = _candidate_windows(report, readings, probe_radius_ms=probe_radius_ms)
    endpoint_evidence = endpoint_candidates(report, readings, probe_radius_ms=probe_radius_ms)
    endpoint_projections = promote_existing_endpoints(
        report, readings, probe_radius_ms=probe_radius_ms, candidates=endpoint_evidence)
    selected, budget_pending, used_ms = _budget_windows(all_windows, max_windows, max_duration_ms)
    directory = root / 'boundary-state-recovery'
    manifest = directory / 'receipt-inspection.json'
    capture_path = directory / 'capture.json'
    if not all_windows and not manifest.exists():
        return readings, dict(
            schema_version=SCHEMA,
            method='bounded_turn_boundary_state_recovery',
            source_sha256=source_info.get('sha256'),
            requested_windows=[], processed_windows=[], pending_windows=[],
            candidate_windows=[], candidate_endpoint_evidence=endpoint_evidence,
            candidate_endpoint_projections=endpoint_projections,
            new_frames=0, promoted_source_timestamps=0,
            source_probe_duration_ms=0, configured_model_dir=str(model_dir),
            complete_event_history=False,
        )

    from .full_recording import save_json
    from .inspect_receipts import inspect, window_readings
    from .inspect_training import reparse_inspection
    from .vision import NeuralReader

    directory.mkdir(parents=True, exist_ok=True)
    capture = dict(source=deepcopy(source_info))
    if capture_path.exists():
        existing_capture = json.loads(capture_path.read_text(encoding='utf-8'))
        if existing_capture != capture:
            raise ValueError('Boundary recovery source metadata changed')
    else:
        save_json(capture_path, capture)
    existing = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else None
    if existing and existing.get('source_sha256') != source_info.get('sha256'):
        raise ValueError('Boundary recovery observations belong to another recording')
    initial_count = len(existing.get('readings', [])) if existing else 0
    completed = {(window.get('start_ms'), window.get('end_ms'), window.get('fps'))
                 for window in existing.get('windows', [])} if existing else set()
    processed, pending = [], [dict(window, pending_reason='source_probe_budget') for window in budget_pending]
    reader = None
    new_window_count = 0
    new_probe_duration = 0
    steps = []
    for window in selected:
        identity = (window['start_ms'], window['end_ms'], fps)
        cached = identity in completed
        if not cached:
            if not allow_ocr:
                pending.append(dict(window, pending_reason='ocr_disabled'))
                continue
            new_window_count += 1
            new_probe_duration += window['end_ms'] - window['start_ms']
        steps.append((window, cached))
    if allow_ocr:
        from .dense_inspection_pool import prepare_windows
        prepare_windows(source_path, directory, [w for w, cached in steps if not cached], fps,
                        kind='base', model_dir=model_dir, workers=dense_workers)
    for window, cached in steps:
        if allow_ocr and reader is None:
            # Passing the configured reader validates cached model identity.
            reader = NeuralReader(model_dir)
        inspect(source_path, directory, window['start_ms'], window['end_ms'], fps,
                reader=reader if allow_ocr else None)
        processed.append(window)

    if not manifest.exists():
        metadata = dict(
            schema_version=SCHEMA,
            method='bounded_turn_boundary_state_recovery',
            source_sha256=source_info.get('sha256'),
            requested_windows=selected,
            processed_windows=processed,
            pending_windows=pending,
            candidate_windows=all_windows,
            candidate_endpoint_evidence=endpoint_evidence,
            candidate_endpoint_projections=endpoint_projections,
            new_frames=0,
            new_windows=0,
            source_probe_duration_ms=used_ms,
            new_probe_duration_ms=new_probe_duration,
            configured_model_dir=str(model_dir),
            complete_event_history=False,
        )
        save_json(directory / 'last-plan.json', metadata)
        return readings, metadata

    inspection = json.loads(manifest.read_text(encoding='utf-8'))
    if inspection.get('source_sha256') != source_info.get('sha256'):
        raise ValueError('Boundary recovery inspection belongs to another recording')
    models = _cached_model_provenance(inspection, directory)
    # Only frames of this run's windows, as a run straight through has.
    fresh = reparse_inspection(window_readings(inspection, processed, fps), directory)
    relocated = _relocate(fresh, directory, root)
    promoted = promote(readings, relocated, processed)
    original_times = {_valid_time(row) for row in readings if _valid_time(row) is not None}
    new_rows = [row for row in promoted if _valid_time(row) not in original_times]
    metadata = dict(
        schema_version=SCHEMA,
        method='bounded_turn_boundary_state_recovery',
        source_sha256=source_info.get('sha256'),
        requested_windows=selected,
        processed_windows=processed,
        pending_windows=pending,
        candidate_windows=all_windows,
        candidate_endpoint_evidence=endpoint_evidence,
        candidate_endpoint_projections=endpoint_projections,
        requested_fps=fps,
        configured_model_dir=str(model_dir),
        new_frames=len(inspection.get('readings', [])) - initial_count,
        new_windows=new_window_count,
        source_samples=len(relocated),
        promoted_source_timestamps=len(new_rows),
        promoted_channels=sorted({
            channel.get('channel')
            for row in new_rows
            for channel in row.get('facts', {}).get('boundary_state_recovery', [])
            if isinstance(channel, dict) and channel.get('channel')
        }),
        source_probe_duration_ms=used_ms,
        new_probe_duration_ms=new_probe_duration,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),
        ocr_engine_fingerprint=reader.fingerprint if reader else None,
        observed_ocr_models=models,
        complete_event_history=False,
    )
    save_json(directory / 'last-plan.json', metadata)
    return promoted, metadata
