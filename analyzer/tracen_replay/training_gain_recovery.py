"""Bounded native-frame probes of unresolved training gain animations."""
import copy
import hashlib
import json
from pathlib import Path

from .training_gain_resolution import (
    candidate_recovery_policy,
    resolve_candidate_only_gain,
)
from .reconcile import FIELDS
from .training_gain_phases import is_committed_training_result_row


# A result reread may also expose the signed performance sidebar while the
# result cards are visible.  Keep these fields separate from the stat-gain
# resolver: the latter uses ``FIELDS``-shaped amounts, while performance
# awards are emitted by ``vision.parse`` under ``awarded_performance_gains``.
_PERFORMANCE_FIELDS = ('dance', 'passion', 'vocal', 'visual', 'composure')
_RESULT_GAIN_FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')
_SOURCE_ENVELOPE_KEYS = (
    'source_sha256', 'source_frame_sha256', 'source_frame_id',
    'engine_fingerprint', 'model_sha256', 'gameplay_sha256',
)
_RESULT_RECOVERY_RADIUS_MS = 500
_RESULT_RECOVERY_MAX_SPAN_MS = 1500


def _candidate_only_fields(row):
    """Return fields with typed source candidates but no accepted gain.

    This is intentionally based on crop provenance rather than the balance
    ledger.  A candidate-only field is a reason to request a bounded source
    reread; it is not itself an accepted transaction effect.
    """

    if not is_committed_training_result_row(row):
        return set()
    facts = row.get('facts')
    if not isinstance(facts, dict):
        return set()
    accepted = facts.get('training_gains')
    accepted = accepted if isinstance(accepted, dict) else {}
    provenance = facts.get('training_gain_crop_provenance')
    if not isinstance(provenance, dict):
        return set()
    result = set()
    for field, resolution in provenance.items():
        if not isinstance(field, str) or field in accepted or not isinstance(resolution, dict):
            continue
        amounts = resolution.get('candidate_amounts')
        if isinstance(amounts, list) and any(type(amount) is int for amount in amounts):
            result.add(field)
    return result


def _result_rows(readings, *, start_ms=None, end_ms=None):
    """Keep only source rows in one committed training-result interval."""

    result = []
    for row in readings:
        if not isinstance(row, dict) or row.get('screen') != 'training_result':
            continue
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is not int:
            continue
        if start_ms is not None and timestamp < start_ms:
            continue
        if end_ms is not None and timestamp > end_ms:
            continue
        result.append(row)
    return result


_INVALID_TRAINING_OPTION = object()


def _row_training_option(row):
    """Normalize a source row's option while preserving malformed values."""

    value = row.get('training_option') if isinstance(row, dict) else None
    if value is None:
        return None
    if not isinstance(value, str):
        return _INVALID_TRAINING_OPTION
    value = value.strip()
    return value or None


def _window_training_option_context(readings, fresh, window):
    """Return the option proven by committed rows in one source window.

    A reread frame may miss the action heading while a neighboring committed
    row in the same bounded interval still identifies the selected option.
    That row is sufficient to bind an optionless frame, but only when it is
    the sole explicit option in the interval.  The window metadata alone is
    never treated as proof, and a competing explicit option keeps the interval
    ambiguous.
    """

    expected = _row_training_option(window)
    if expected is _INVALID_TRAINING_OPTION:
        expected = None
    explicit = set()
    start = window.get('start_ms')
    end = window.get('end_ms')
    if type(start) is not int or type(end) is not int:
        return expected, explicit
    seen = set()
    for row in [*(readings or ()), *(fresh or ())]:
        if (not isinstance(row, dict)
                or row.get('screen') != 'training_result'
                or not is_committed_training_result_row(row)):
            continue
        timestamp = row.get('source_timestamp_ms')
        if type(timestamp) is not int or not start <= timestamp <= end:
            continue
        option = _row_training_option(row)
        if option is _INVALID_TRAINING_OPTION or option is None:
            continue
        identity = (timestamp, row.get('evidence'), option)
        if identity in seen:
            continue
        seen.add(identity)
        explicit.add(option)
    return expected, explicit


def _committed_result_rows(readings, event):
    """Return source rows that can own a bounded result reread.

    A missing signed badge is recoverable only when the base timeline already
    identifies the same committed result phase.  The result screen, selected
    training option, visible success banner, and at least one typed result
    total are all source observations used to schedule the reread.  The
    totals never become gain amounts.
    """

    option = event.get('training_option')
    if not isinstance(option, str) or not option.strip():
        return []
    rows = _result_rows(
        readings,
        start_ms=event.get('first_seen_ms'),
        end_ms=event.get('last_seen_ms'),
    )
    result = []
    for row in rows:
        if row.get('training_option') != option:
            continue
        if not is_committed_training_result_row(row):
            continue
        facts = row.get('facts')
        # A skipped result animation can leave the outcome banner unread on
        # every base frame.  An unresolved outcome still owns a reread; an
        # explicit failure never does.
        if not isinstance(facts, dict) or facts.get('training_outcome') not in ('success', 'unknown', None):
            continue
        if any(facts.get(key) is True for key in (
                'preview', 'preview_overlay_proven', 'preview_modifier_proven')):
            continue
        totals = facts.get('result_values')
        has_totals = isinstance(totals, dict) and any(type(value) is int and value >= 0
                                                       for value in totals.values())
        # A legible SUCCESS banner on a committed result row is itself proof
        # of the applied-result phase.  A row whose banner was never read
        # must instead show at least one typed result total.
        if not has_totals and facts.get('training_outcome') != 'success':
            continue
        result.append(row)
    if any(isinstance(r.get('facts'), dict) and r['facts'].get('training_outcome') == 'failure' for r in rows):
        return []
    return result


def _prefix_resolved_fields(event):
    """Fields whose accepted amount came from a dense-frame prefix resolution."""
    resolutions = event.get('gain_prefix_resolutions') if isinstance(event, dict) else None
    if not isinstance(resolutions, dict):
        return set()
    return {field for field in resolutions if field in _RESULT_GAIN_FIELDS}


def _missing_result_fields(result_rows, event=None):
    """Return fields for an all-badges-missing committed result group.

    A result card can legitimately expose only the fields affected by the
    selected training.  Treating every absent canonical field as an omitted
    badge therefore schedules unnecessary rereads (and would make a
    two/three-field result look incomplete).  Once any signed stat gain is
    already accepted, the candidate-only path above is the only supported
    route for a partial recovery; it carries a source crop candidate for the
    specific field.  An empty accepted set still needs the bounded source
    reread that recovers a result whose badge crops were all missed.
    """

    accepted = set()
    if isinstance(event, dict) and isinstance(event.get('deltas'), dict):
        # Acceptance is decided at the event level: a single-frame badge
        # reading that the transaction builder declined to promote is not an
        # accepted gain, so the group still needs its bounded reread.  Events
        # without a deltas view keep the historical row-level rule.
        # A preview- or state-derived fill is bookkeeping, not a read badge;
        # the bounded reread is still owed so the direct signed badges (and the
        # performance awards beside them) can be observed on source frames.
        derived = event.get('result_state_derived_fields')
        derived = set(derived) if isinstance(derived, (list, tuple, set)) else set()
        accepted.update(field for field, amount in event['deltas'].items()
                        if field in _RESULT_GAIN_FIELDS and type(amount) is int
                        and field not in derived)
    else:
        for row in result_rows:
            facts = row.get('facts')
            gains = facts.get('training_gains') if isinstance(facts, dict) else None
            if isinstance(gains, dict):
                accepted.update(field for field, amount in gains.items()
                                if field in _RESULT_GAIN_FIELDS and type(amount) is int)
    # Keep this list local rather than importing parser constants.  Result
    # totals are never consulted here: they only prove that this is a
    # committed result group for a bounded reread.  Missing individual fields
    # are not evidence of an omitted effect; only an entirely empty signed
    # gain set warrants the generic fallback.  Partial source-backed gaps are
    # scheduled by ``_candidate_only_fields`` before this fallback.
    return set(_RESULT_GAIN_FIELDS) if not accepted else set()


def plan(readings, events):
    # Known crop/candidate evidence is more actionable than a generic result
    # reread.  Preserve it at the front of the bounded work list so adding a
    # result-without-badges fallback cannot starve existing source recoveries.
    requests=[]
    fallback_requests=[]
    for event in events:
        if event.get('kind')!='training':continue
        try:
            first_seen = int(event['first_seen_ms'])
            last_seen = int(event['last_seen_ms'])
        except (KeyError, TypeError, ValueError):
            continue
        raw_conflicting = event.get('conflicting_readings')
        conflicting_fields = set(raw_conflicting) if isinstance(raw_conflicting, dict) else set()
        # A field accepted from dense-frame prefix evidence depends on the
        # frames of the same reread window.  Keep it in the reread scope so
        # the projection retains its supporting observations; otherwise the
        # later checkpoint-aware rebuild would see only the sparse frames.
        prefix_fields = set(_prefix_resolved_fields(event))
        result_rows = _result_rows(readings, start_ms=first_seen, end_ms=last_seen)
        candidate_fields = set()
        for row in result_rows:
            candidate_fields.update(_candidate_only_fields(row))
        # A gain read on exactly one result frame that the event did not accept
        # (a badge caught mid-animation, or clipped) is worth a bounded reread
        # of the same interval; a fresh run then observes it instead of the
        # accounting working it out from the turn difference.
        single_frame_fields = set()
        if isinstance(event.get('deltas'), dict):
            # Only an assembled event (with its accepted deltas) can say which
            # single-frame readings it declined; a bare candidate event cannot.
            frames_per_field = {}
            for row in result_rows:
                gains = (row.get('facts') or {}).get('training_gains') if isinstance(row.get('facts'), dict) else None
                for field, value in (gains or {}).items():
                    if field in FIELDS and type(value) is int and value > 0:
                        frames_per_field[field] = frames_per_field.get(field, 0) + 1
            single_frame_fields = {field for field, count in frames_per_field.items()
                                   if count == 1 and field not in event['deltas']}
        fields = conflicting_fields | candidate_fields | prefix_fields | single_frame_fields
        candidates=[r for r in result_rows
                    if fields.intersection((r.get('facts',{}).get('training_gains',{})
                                            if isinstance(r.get('facts',{}),dict) else {}))
                    or fields.intersection(_candidate_only_fields(r))]
        # A result that accepted no signed gain at all is owed the bounded
        # reread of its whole result interval below; a lone single-frame
        # reading narrows that interval only when other gains were accepted.
        committed = _committed_result_rows(readings, event)
        missing = _missing_result_fields(committed, event)
        if candidates and not (committed and missing and not (conflicting_fields or candidate_fields or prefix_fields)):
            start=max(first_seen,min(r['source_timestamp_ms'] for r in candidates)-100)
            end=min(last_seen,max(r['source_timestamp_ms'] for r in candidates)+100)
            if not start<end or end-start>_RESULT_RECOVERY_MAX_SPAN_MS:continue
            reason = ('conflicting_observed_training_badge_digits' if conflicting_fields else
                      'candidate_only_source_gain_evidence' if candidate_fields or prefix_fields else
                      'single_frame_training_gain')
            request = dict(
                start_ms=start,
                end_ms=end,
                owner_id=event['id'],
                fields=sorted(fields),
                reason=reason,
            )
            # The selected option is the source owner for candidate recovery.
            # Legacy synthetic events may omit it, while real training events
            # always carry the field and therefore bind the reread explicitly.
            option = event.get('training_option')
            if isinstance(option, str) and option.strip():
                request['training_option'] = option.strip()
            requests.append(request)
            continue

        # A low-rate base pass can classify a committed result while missing
        # every signed badge.  Do not reconstruct an amount from result totals
        # or from a neighboring event.  Instead, request a bounded source
        # reread around this event's own result interval.  The high-rate
        # reader returns direct signed fields when they are visible, and the
        # normal promotion path remains the only place that can accept them.
        bare = [r for r in readings
                if isinstance(r, dict) and r.get('screen') in ('training_result', 'training_result_candidate')
                and type(r.get('source_timestamp_ms')) is int and first_seen <= r['source_timestamp_ms'] <= last_seen
                and isinstance(r.get('facts'), dict)]
        if (not committed and not (event.get('deltas') or {}) and bare
                and not any(r['facts'].get(key) is True for r in bare
                            for key in ('preview', 'preview_overlay_proven', 'preview_modifier_proven'))
                and not any((r.get('stats') or {}).get('training_preview') is True for r in bare)
                and not any(r['facts'].get('training_outcome') == 'failure' for r in bare)):
            # A card the base pass caught on a frame or two, too few to commit
            # the result and with no signed gain read at all (the player skipped
            # through it), is owed the same bounded reread: the high-rate reader
            # sees the badges the sparse pass fell between.
            start=max(0, first_seen - _RESULT_RECOVERY_RADIUS_MS)
            end=last_seen + _RESULT_RECOVERY_RADIUS_MS
            if start<end and end-start<=_RESULT_RECOVERY_MAX_SPAN_MS:
                fallback_requests.append(dict(
                    start_ms=start, end_ms=end, owner_id=event['id'], fields=sorted(FIELDS),
                    performance_fields=list(_PERFORMANCE_FIELDS), training_option=event.get('training_option'),
                    source_result_projection=True, reason='result_seen_without_any_signed_gain'))
            continue
        # A performance row the committed card left unread (a badge over it,
        # a merged read under the floor, the award's box cut before its
        # digits) is owed the same bounded reread as a missing stat badge:
        # the high-rate reader sees the award on the frames the sparse pass
        # fell between. The card's accepted badges bind each reread frame to
        # this result.
        unread = event.get('performance_rows_unread')
        unread = sorted(f for f in unread if f in _PERFORMANCE_FIELDS) if isinstance(unread, (list, tuple)) else []
        if committed and unread and not missing:
            accepted = sorted(f for f, n in (event.get('deltas') or {}).items()
                              if f in _RESULT_GAIN_FIELDS and type(n) is int)
            start=max(0, first_seen - _RESULT_RECOVERY_RADIUS_MS)
            end=last_seen + _RESULT_RECOVERY_RADIUS_MS
            if start<end and end-start<=_RESULT_RECOVERY_MAX_SPAN_MS:
                fallback_requests.append(dict(
                    start_ms=start, end_ms=end, owner_id=event['id'],
                    fields=accepted or sorted(_RESULT_GAIN_FIELDS), performance_fields=unread,
                    training_option=event.get('training_option'), source_result_projection=True,
                    reason='performance_row_unread_on_committed_result'))
            continue
        if not committed or not missing:
            continue
        start=max(0, first_seen - _RESULT_RECOVERY_RADIUS_MS)
        end=last_seen + _RESULT_RECOVERY_RADIUS_MS
        if not start<end or end-start>_RESULT_RECOVERY_MAX_SPAN_MS:
            continue
        fallback_requests.append(dict(
            start_ms=start,
            end_ms=end,
            owner_id=event['id'],
            fields=sorted(missing),
            performance_fields=list(_PERFORMANCE_FIELDS),
            training_option=event.get('training_option'),
            source_result_projection=True,
            reason='committed_result_missing_signed_gain_observation',
        ))
    return requests + fallback_requests


def _new_addition(timestamp, evidence, *, training_option=None):
    return dict(source_timestamp_ms=timestamp,evidence=evidence,screen='training_result',
                training_option=training_option,stats={},effects=[],facts={'training_gains':{},
                'observed_training_gain_fields':[]},ocr={'neural':[]})


def _merge_addition_gain(additions, row, field, amount):
    """Merge one source-backed gain into a physical fresh row."""

    timestamp = row['source_timestamp_ms']
    addition = additions.get(timestamp)
    if addition is None:
        addition = _new_addition(
            timestamp, row['evidence'], training_option=row.get('training_option'),
        )
        additions[timestamp] = addition
    facts = addition.setdefault('facts', {})
    gains = facts.setdefault('training_gains', {})
    previous = gains.get(field)
    if previous is not None and previous != amount:
        return None
    gains[field] = amount
    observed = set(facts.get('observed_training_gain_fields', []))
    observed.add(field)
    facts['observed_training_gain_fields'] = sorted(observed)
    return addition


def _source_result_projection(row, window):
    """Return direct result fields eligible for a source-result window.

    The generic fallback is deliberately stricter than the historical direct
    promotion path.  Its window is created only from a committed result row,
    so a reread must carry the same selected option and a successful result
    banner.  Only typed signed gains/awards from that reread are projected;
    result totals and current counters remain diagnostic inputs.
    """

    if window.get('source_result_projection') is not True:
        return None
    option = window.get('training_option')
    if not isinstance(option, str) or not option.strip() or row.get('training_option') != option:
        return None
    if not is_committed_training_result_row(row):
        return None
    facts = row.get('facts')
    if not isinstance(facts, dict) or facts.get('training_outcome') not in ('success', 'unknown', None):
        return None
    requested_fields = window.get('fields')
    if not isinstance(requested_fields, (list, tuple, set)):
        return None
    requested_fields = set(requested_fields)
    allowed_fields = set(_RESULT_GAIN_FIELDS)
    if not requested_fields or not requested_fields.issubset(allowed_fields):
        return None
    raw_gains = facts.get('training_gains')
    gains = {}
    if isinstance(raw_gains, dict):
        for field, amount in raw_gains.items():
            if (field in requested_fields and type(amount) is int
                    and 0 <= amount <= 999):
                gains[field] = amount

    requested_performance = window.get('performance_fields', ())
    if not isinstance(requested_performance, (list, tuple, set)):
        return None
    requested_performance = set(requested_performance)
    if not requested_performance.issubset(set(_PERFORMANCE_FIELDS)):
        return None
    raw_awards = facts.get('awarded_performance_gains')
    awards = {}
    if isinstance(raw_awards, dict):
        for field, amount in raw_awards.items():
            if (field in requested_performance and type(amount) is int
                    and 0 <= amount <= 999):
                awards[field] = amount
    badge_gains = _animation_badge_gains(facts, window, requested_fields - set(gains))
    if badge_gains and facts.get('training_outcome') in ('unknown', None):
        # Positive gain badges are only drawn for an applied result; the
        # window's own rows carry no failure banner (checked by the planner).
        pass
    gains.update(badge_gains)
    if not gains and not awards:
        return None
    return gains, awards


def _animation_badge_gains(facts, window, open_fields):
    """Resolve large animation badges to stat fields via the window's totals.

    The badge supplies the amount.  The card it belongs to is the unique
    field in the badge's row whose result total rose by exactly that amount
    between the last stable pre-result reading and the stable result panel
    of the same bounded window.  Ambiguous or unconfirmed badges are left
    unresolved; totals never become gain amounts on their own.
    """
    badges = facts.get('animation_gain_badges') if isinstance(facts, dict) else None
    context = window.get('_animation_totals_context')
    if not isinstance(badges, list) or not isinstance(context, dict) or not open_fields:
        return {}
    from .vision import ANIMATION_BADGE_ROWS
    resolved = {}
    for badge in badges:
        if not isinstance(badge, dict) or type(badge.get('amount')) is not int:
            continue
        amount = badge['amount']
        row_fields = ANIMATION_BADGE_ROWS.get(badge.get('row'), ())
        matches = [field for field in row_fields
                   if field in open_fields and field not in resolved
                   and context.get(field) == amount]
        if not matches:
            # Ownership by elimination: a badge is only drawn for a field that
            # changed, so when every other card in the row has a confirmed
            # total that did not rise by this amount, the one card whose
            # total could not be read (cursor, glare) must own the badge.
            unknown = [field for field in row_fields if field not in context]
            others_exclude = all(context.get(field) != amount for field in row_fields if field in context)
            if len(unknown) == 1 and others_exclude and unknown[0] in open_fields and unknown[0] not in resolved:
                matches = unknown
        if len(matches) == 1:
            resolved[matches[0]] = amount
    return resolved


def _stable_result_totals(rows, before):
    """Per-field result totals seen identically on at least two rows.

    Result frames before the applied panel still show the prior total, so
    that value is allowed beside the new one.  Any third distinct value (a
    rolling counter, a cursor-clipped digit) leaves the field unresolved.
    """
    from collections import Counter
    seen = {}
    for row in rows:
        facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
        source = facts.get('result_values')
        if not isinstance(source, dict):
            continue
        for field, value in source.items():
            if type(value) is int and value >= 0:
                seen.setdefault(field, Counter())[value] += 1
    stable = {}
    for field, counts in seen.items():
        prior = before.get(field)
        new_values = {value: count for value, count in counts.items() if value != prior}
        if len(new_values) == 1:
            value, count = next(iter(new_values.items()))
            if count >= 2:
                stable[field] = value
        elif not new_values and type(prior) is int and counts.get(prior, 0) >= 2:
            stable[field] = prior
    return stable


def animation_totals_context(readings, fresh, window, *, lookback_ms=15000):
    """Return ``{field: delta}`` for one reread window, or ``None``.

    ``before`` is the last base home-panel snapshot before the window with no
    stat-changing effect recorded between it and the window; ``after`` is the
    stable result-panel total inside the window.  Only fields present in both
    with a non-negative delta are returned.
    """
    start, end = window.get('start_ms'), window.get('end_ms')
    if type(start) is not int or type(end) is not int:
        return None
    before_rows = []
    for row in readings:
        time = row.get('source_timestamp_ms')
        if type(time) is not int or not start - lookback_ms <= time < start:
            continue
        values = (row.get('stats') or {}).get('values') if isinstance(row.get('stats'), dict) else None
        if isinstance(values, dict) and all(type(values.get(f)) is int for f in ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')):
            before_rows.append(row)
        elif any(isinstance(e, dict) and e.get('kind') == 'stat_change' for e in row.get('effects', []) or []):
            # An intervening stat effect invalidates every earlier snapshot.
            before_rows = []
    if not before_rows:
        return None
    before = before_rows[-1]['stats']['values']
    window_rows = [r for r in [*readings, *fresh]
                   if type(r.get('source_timestamp_ms')) is int and start <= r['source_timestamp_ms'] <= end
                   and r.get('screen') == 'training_result']
    after = _stable_result_totals(window_rows, before)
    context = {}
    for field, value in after.items():
        if type(before.get(field)) is int and value >= before[field]:
            context[field] = value - before[field]
    return context or None


def _explicit_projection_option_conflict(row, window):
    """Return whether a typed result view names another training option."""

    expected = window.get('training_option')
    actual = row.get('training_option')
    if (not isinstance(expected, str) or not expected.strip()
            or not isinstance(actual, str) or not actual.strip()
            or actual == expected):
        return False
    facts = row.get('facts')
    if not isinstance(facts, dict):
        return False
    for key in ('training_gains', 'awarded_performance_gains'):
        values = facts.get(key)
        if isinstance(values, dict) and any(type(value) is int for value in values.values()):
            return True
    return False


def _match_existing_source_row(merged, indexes, source_row):
    """Match one reread source frame to an existing timeline row.

    Recovery rereads can use a different evidence namespace for the same
    physical frame, so a unique timestamp is sufficient when it identifies
    exactly one existing row.  Once a timestamp has multiple rows, however,
    only one exact evidence identity is safe.  Never choose the first row from
    an ambiguous timestamp: doing so can attach a gain to an unrelated frame.
    """

    source_evidence = source_row.get('evidence')
    exact = [
        index for index in indexes
        if 0 <= index < len(merged)
        and merged[index].get('evidence') == source_evidence
    ]
    if len(exact) == 1:
        return exact[0], 'exact_evidence'
    if len(exact) > 1:
        return None, 'ambiguous_exact_evidence'
    if len(indexes) == 1 and 0 <= indexes[0] < len(merged):
        return indexes[0], 'unique_timestamp'
    if not indexes:
        return None, 'source_timestamp_not_found'
    return None, 'ambiguous_timestamp'


def _candidate_proof_source_rows(window_rows, observations):
    """Resolve every accepted proof identity to one fresh source row."""

    result = {}
    for observation in observations:
        timestamp = observation.get('source_timestamp_ms')
        evidence = observation.get('evidence')
        if type(timestamp) is not int or not isinstance(evidence, str) or not evidence.strip():
            return None, 'invalid_accepted_source_identity'
        key = (timestamp, evidence.strip())
        candidates = [row for row in window_rows
                      if row.get('source_timestamp_ms') == timestamp
                      and isinstance(row.get('evidence'), str)
                      and row.get('evidence').strip() == evidence.strip()]
        if len(candidates) != 1:
            return None, ('source_row_not_found' if not candidates
                          else 'ambiguous_source_row')
        result.setdefault(key, candidates[0])
    return result, None


def _bind_optionless_candidate_rows(rows, option):
    """Bind optionless candidate views to an already-proven owner option.

    The source row is kept unchanged for identity/provenance checks.  The
    resolver view receives a copy with the owner option so a readable amount
    from a frame whose heading is animated does not form a false phase
    conflict with the stable heading frame.
    """

    result = []
    for row in rows:
        actual = _row_training_option(row)
        if actual is None and isinstance(option, str) and option.strip():
            bound = copy.deepcopy(row)
            bound['training_option'] = option.strip()
            result.append(bound)
        else:
            result.append(row)
    return result


def _promote_accepted(readings, fresh, windows):
    """Only new physical timestamps and requested source fields can be added.

    Existing source rows retain their original fields and proof identities.
    A source-result window may additionally project direct performance awards
    from the same successful result row.  No result totals, action labels, or
    unrelated OCR effects are imported.
    """
    original_times={r['source_timestamp_ms'] for r in readings}
    additions={}
    ambiguous_projection_times=set()
    blocked_option_windows = set()
    option_context = {}
    for candidate_window in windows:
        expected_option = _row_training_option(candidate_window)
        if expected_option is _INVALID_TRAINING_OPTION:
            expected_option = None
        if expected_option is not None:
            _expected, explicit = _window_training_option_context(
                readings, fresh, candidate_window,
            )
            option_context[id(candidate_window)] = (expected_option, explicit)
    # Preflight option conflicts before touching any row.  A conflicting view
    # with a requested typed effect makes the entire bounded window ambiguous;
    # otherwise promoting the matching row first would hide the contradiction.
    for candidate_window in windows:
        expected_option = _row_training_option(candidate_window)
        if expected_option is _INVALID_TRAINING_OPTION or expected_option is None:
            continue
        requested_fields = set(candidate_window.get('fields', ()))
        requested_performance = set(candidate_window.get('performance_fields', ()))
        for candidate_row in [*readings, *fresh]:
            timestamp = candidate_row.get('source_timestamp_ms')
            if (not is_committed_training_result_row(candidate_row)
                    or type(timestamp) is not int
                    or not candidate_window['start_ms'] <= timestamp <= candidate_window['end_ms']):
                continue
            actual_option = _row_training_option(candidate_row)
            if (actual_option is _INVALID_TRAINING_OPTION
                    or actual_option is None
                    or actual_option == expected_option):
                continue
            candidate_facts = candidate_row.get('facts', {})
            gains = (candidate_facts.get('training_gains', {})
                     if isinstance(candidate_facts, dict) else {})
            awards = (candidate_facts.get('awarded_performance_gains', {})
                      if isinstance(candidate_facts, dict) else {})
            has_requested_effect = (
                any(field in requested_fields and type(amount) is int
                    for field, amount in gains.items())
                or any(field in requested_performance and type(amount) is int
                       for field, amount in awards.items())
                or bool(requested_fields.intersection(_candidate_only_fields(candidate_row)))
            )
            if has_requested_effect:
                blocked_option_windows.add(id(candidate_window))
                break
    for candidate_window in windows:
        if candidate_window.get('source_result_projection') is True:
            candidate_window['_animation_totals_context'] = animation_totals_context(readings, fresh, candidate_window)
    for row in fresh:
        time=row['source_timestamp_ms']
        if time in original_times or not is_committed_training_result_row(row):
            continue
        if time in ambiguous_projection_times:
            continue
        owners=[w for w in windows if w['start_ms']<=time<=w['end_ms']]
        if len(owners)!=1:continue
        window=owners[0]
        if id(window) in blocked_option_windows:
            continue
        window_option = _row_training_option(window)
        if window_option is _INVALID_TRAINING_OPTION:
            continue
        if window_option is not None:
            actual_option = _row_training_option(row)
            if actual_option is _INVALID_TRAINING_OPTION:
                continue
            if actual_option is None:
                _expected, explicit = option_context.get(
                    id(window), (window_option, set()))
                # An optionless source row is admissible only when a committed
                # row in this exact window proves one unique owner option.
                if explicit != {window_option}:
                    continue
            elif actual_option != window_option:
                continue
        if (window.get('source_result_projection') is True
                and _explicit_projection_option_conflict(row, window)):
            additions.pop(time, None)
            ambiguous_projection_times.add(time)
            continue
        projected = _source_result_projection(row, window)
        if projected is not None:
            gains, awards = projected
        else:
            if window.get('source_result_projection') is True:
                continue
            facts = row.get('facts') if isinstance(row.get('facts'), dict) else {}
            raw_gains = facts.get('training_gains', {})
            gains={f:n for f,n in raw_gains.items()
                   if f in window.get('fields', ())}
            awards={}
        if not gains and not awards:
            continue
        if projected is not None and not gains:
            # A frame that shows no stat badge is not a result card yet: the
            # side panel's "+N" beside a row is the preview's projection until
            # a badge shows the result. Its awards, and its outcome, wait for
            # a frame with a badge.
            continue

        # A source-result projection is never allowed to overwrite an
        # existing base timestamp.  Such rows are handled by the explicit
        # same-frame merge/reconciliation paths, where evidence identity is
        # available.  This recovery path only appends genuinely new source
        # frames.
        if time in original_times:
            continue
        addition=additions.get(time)
        if addition is None:
            addition=_new_addition(
                time, row.get('evidence'),
                training_option=row.get('training_option'),
            )
            additions[time]=addition
        facts=addition['facts']
        existing_gains=facts.get('training_gains', {})
        existing_awards=facts.get('awarded_performance_gains', {})
        source_identity_conflict = projected is not None and any(
            key in addition and key in row and addition[key] != row[key]
            for key in _SOURCE_ENVELOPE_KEYS
        )
        if (source_identity_conflict
                or any(field in existing_gains and existing_gains[field] != amount
                       for field, amount in gains.items())
                or any(field in existing_awards and existing_awards[field] != amount
                       for field, amount in awards.items())):
            # A second source view at one timestamp disagrees with the first;
            # do not retain a partial promoted effect or let a later third
            # view silently resurrect the first value.
            if projected is not None:
                additions.pop(time, None)
                ambiguous_projection_times.add(time)
            elif not existing_gains and not existing_awards:
                additions.pop(time, None)
            continue
        facts['training_gains'].update(gains)
        facts['observed_training_gain_fields']=sorted(set(facts['observed_training_gain_fields']) | set(gains))
        if awards:
            facts.setdefault('awarded_performance_gains', {}).update(awards)
        if projected is not None:
            # Preserve only the same-frame committed result phase needed by
            # the downstream source-proof validator.  Result totals and all
            # other parser state remain intentionally unprojected.
            facts['training_outcome'] = 'success'
            for key in _SOURCE_ENVELOPE_KEYS:
                if row.get(key) is not None:
                    addition[key] = copy.deepcopy(row[key])
        recovery=dict(
            owner_id=window['owner_id'],
            owner_interval_ms=[window['start_ms'], window['end_ms']],
            requested_fields=window.get('fields', []),
            evidence=row.get('evidence'),
            source_timestamp_ms=time,
        )
        if projected is not None:
            recovery.update(
                requested_performance_fields=window.get('performance_fields', []),
                projection_mode='committed_result_direct_fields',
                training_option=row.get('training_option'),
            )
            for key in _SOURCE_ENVELOPE_KEYS:
                if row.get(key) is not None:
                    recovery[key] = copy.deepcopy(row[key])
        facts['training_gain_recovery']=recovery
    return sorted([*readings,*additions.values()],key=lambda r:r['source_timestamp_ms'])


def promote_with_metadata(readings, fresh, windows):
    """Promote accepted gains and bounded candidate-only source recoveries.

    Candidate recovery is deliberately separate from the native canonical
    parser.  Each accepted result is tied to one window owner, one field, one
    result phase, and source crop evidence returned by
    :func:`resolve_candidate_only_gain`.  A reread may enrich one existing row
    only when its timestamp identifies one row or one exact evidence identity;
    an ambiguous timestamp remains unresolved for the caller to inspect.
    """

    original_times={r['source_timestamp_ms'] for r in readings}
    merged=_promote_accepted(readings,fresh,windows)
    merged_indexes={}
    for index,row in enumerate(merged):
        merged_indexes.setdefault(row.get('source_timestamp_ms'),[]).append(index)
    additions={r['source_timestamp_ms']:r for r in merged if r['source_timestamp_ms'] not in original_times}
    enriched_timestamps=set()
    recoveries=[]
    for window in windows:
        window_rows=[r for r in fresh if r.get('screen')=='training_result'
                     and type(r.get('source_timestamp_ms')) is int
                     and window['start_ms']<=r['source_timestamp_ms']<=window['end_ms']]
        if not window_rows:continue
        window_option = _row_training_option(window)
        if window_option is _INVALID_TRAINING_OPTION:
            continue
        if window_option is not None:
            _expected, explicit = _window_training_option_context(
                readings, fresh, window,
            )
            # Candidate windows generated from a selected training action must
            # not consume a neighboring result row from another option.  An
            # optionless frame is allowed only when this exact source window
            # has one unique committed owner option; the resolver remains a
            # second fail-closed guard for phase disagreement.
            if explicit != {window_option}:
                continue
        # Overlapping or duplicated windows cannot provide a unique owner for
        # a candidate-only source frame.  This keeps repeated scheduling from
        # creating duplicate recovered effects.
        if any(sum(1 for owner in windows
                   if owner.get('start_ms')<=row['source_timestamp_ms']<=owner.get('end_ms')) != 1
               for row in window_rows):
            continue
        accepted_fields={field for row in window_rows
                         for field in (row.get('facts',{}).get('training_gains',{})
                                       if isinstance(row.get('facts',{}),dict) else {})
                         if field in window['fields']}
        resolver_rows = _bind_optionless_candidate_rows(
            window_rows, window_option,
        ) if window_option is not None else window_rows
        for field in window.get('fields',[]):
            if field in accepted_fields:continue
            resolution=resolve_candidate_only_gain(
                resolver_rows, field, phase_key=window.get('owner_id'),
            )
            if resolution.get('status')!='accepted':continue
            observations=resolution.get('accepted_observations',[])
            representative=next(iter(observations),None)
            if representative is None:
                record=dict(resolution)
                record.update(status='unresolved_missing_accepted_proof',owner_id=window.get('owner_id'))
                recoveries.append(record)
                continue
            timestamp=representative['source_timestamp_ms']
            source_rows, source_error = _candidate_proof_source_rows(
                window_rows, observations,
            )
            if source_rows is None:
                record=dict(resolution)
                record.update(
                    status=('unresolved_source_row_not_found'
                            if source_error == 'source_row_not_found'
                            else 'unresolved_source_row_ambiguous'),
                    owner_id=window.get('owner_id'),
                    source_timestamp_ms=timestamp,
                    evidence=representative.get('evidence'),
                )
                recoveries.append(record)
                continue
            representative_key=(
                timestamp,
                representative.get('evidence').strip()
                if isinstance(representative.get('evidence'), str)
                else representative.get('evidence'),
            )
            source_row=source_rows[representative_key]
            # Validate every accepted proof identity before mutating or
            # appending anything.  A candidate proof cannot refer to a second
            # row at an already ambiguous timestamp.
            existing_proof_rows={}
            evidence_remap={}
            source_identity_match=None
            identity_error=None
            for proof_key, proof_row in source_rows.items():
                proof_timestamp, _proof_evidence = proof_key
                proof_addition=additions.get(proof_timestamp)
                if proof_timestamp in original_times or proof_addition is not None:
                    proof_indexes=merged_indexes.get(proof_timestamp,[])
                    proof_index,proof_identity=_match_existing_source_row(
                        merged,proof_indexes,proof_row,
                    )
                    if proof_index is None:
                        identity_error=(proof_timestamp, proof_key[1], proof_identity)
                        break
                    existing_proof_rows[proof_key]=(proof_index,proof_identity)
                    canonical_evidence=merged[proof_index].get('evidence')
                    if isinstance(canonical_evidence, str) and canonical_evidence.strip():
                        evidence_remap[proof_key]=canonical_evidence
            if identity_error is not None:
                error_timestamp,error_evidence,error_identity=identity_error
                record=dict(resolution)
                record.update(
                    status='unresolved_ambiguous_source_identity',
                    owner_id=window.get('owner_id'),
                    source_timestamp_ms=error_timestamp,
                    evidence=error_evidence,
                    source_identity=error_identity,
                )
                recoveries.append(record)
                continue

            representative_existing=existing_proof_rows.get(representative_key)
            if representative_existing is not None:
                representative_index,source_identity_match=representative_existing
                addition=copy.deepcopy(merged[representative_index])
            else:
                addition=_new_addition(
                    timestamp, source_row['evidence'],
                    training_option=source_row.get('training_option'),
                )
            existing_facts=addition.get('facts',{})
            existing_gains=(existing_facts.get('training_gains',{})
                            if isinstance(existing_facts,dict) else {})
            existing=existing_gains.get(field) if isinstance(existing_gains,dict) else None
            if existing is not None and existing != resolution['accepted_amount']:
                record=dict(resolution)
                record.update(status='unresolved_existing_gain_conflict',owner_id=window.get('owner_id'))
                recoveries.append(record)
                continue
            if (existing is not None and isinstance(existing_facts,dict)
                    and field in existing_facts.get('training_gain_candidate_recovery',{})):
                recoveries.append(dict(status='already_present',owner_id=window.get('owner_id'),
                    field=field,source_timestamp_ms=timestamp,evidence=representative.get('evidence'),
                    basis=resolution.get('basis'),accepted_amount=resolution.get('accepted_amount'),
                    policy=resolution.get('policy')))
                continue

            if representative_existing is not None:
                merged[representative_index]=addition
                additions[timestamp]=addition
                enriched_timestamps.add(timestamp)
            else:
                additions[timestamp]=addition
                merged.append(addition)
                merged_indexes.setdefault(timestamp,[]).append(len(merged)-1)

            # Keep each accepted source frame available to the later
            # transaction/evaluation layers.  These are evidence rows, not
            # independently promoted effects.
            for proof_key, proof_row in source_rows.items():
                proof_timestamp, _proof_evidence = proof_key
                if proof_key == representative_key or proof_key in existing_proof_rows:
                    continue
                proof_addition=copy.deepcopy(proof_row)
                additions[proof_timestamp]=proof_addition
                merged.append(proof_addition)
                merged_indexes.setdefault(proof_timestamp,[]).append(len(merged)-1)
            facts=addition.setdefault('facts',{})
            gains=facts.setdefault('training_gains',{})
            gains[field]=resolution['accepted_amount']
            observed=set(facts.get('observed_training_gain_fields',[]));observed.add(field)
            facts['observed_training_gain_fields']=sorted(observed)
            def remap_observations(items):
                remapped=[]
                for item in items:
                    item=copy.deepcopy(item)
                    key=(item.get('source_timestamp_ms'),
                         item.get('evidence').strip()
                         if isinstance(item.get('evidence'), str) else item.get('evidence'))
                    canonical=evidence_remap.get(key)
                    if canonical is not None:
                        item['evidence']=canonical
                    remapped.append(item)
                return remapped
            proof=dict(owner_id=window.get('owner_id'),requested_fields=window.get('fields',[]),
                       field=field,phase_key=resolution.get('phase_key'),
                        source_timestamp_ms=timestamp,evidence=representative.get('evidence'),
                        basis=resolution.get('basis'),accepted_amount=resolution.get('accepted_amount'),
                        source_identity_match=source_identity_match,
                        row_update=('enriched_existing_source_row'
                                    if timestamp in enriched_timestamps else 'new_recovery_row'),
                        accepted_observations=remap_observations(
                            resolution.get('accepted_observations', [])),
                        observations=remap_observations(
                            resolution.get('observations', [])),
                        policy=resolution.get('policy'))
            representative_proof_key=(timestamp, representative.get('evidence'))
            canonical_representative=evidence_remap.get(representative_proof_key)
            if canonical_representative is not None:
                proof['evidence']=canonical_representative
            candidate_proofs=facts.setdefault('training_gain_candidate_recovery',{})
            candidate_proofs[field]=proof
            recoveries.append(dict(status='accepted',**proof))
    return sorted(merged,key=lambda r:r['source_timestamp_ms']),recoveries


def promote(readings, fresh, windows):
    """Promote accepted and source-corroborated candidate-only gain rows."""

    return promote_with_metadata(readings,fresh,windows)[0]


class TrainingReader:
    """The base reader driven through its training-result read path, with its own fingerprint."""
    def __init__(self, model_dir='.local/models/rapidocr'):
        from .vision import NeuralReader
        self.base=NeuralReader(model_dir)
        self.models=self.base.models;self.Image=self.base.Image
        from .code_identity import function_digest
        self.fingerprint=self.base.fingerprint+'-training-'+hashlib.sha256(function_digest(type(self.base).read_training)).hexdigest()
    def read(self,image):return self.base.read_training(image)


def recover(source, root, source_info, readings, events, *, allow_ocr=True,
            model_dir='.local/models/rapidocr', max_windows=96, max_duration_ms=120000, dense_workers=1):
    from .full_recording import save_json
    from .inspect_receipts import inspect
    from .inspect_training import reparse_inspection
    root=Path(root); directory=root/'training-gain-recovery'; manifest=directory/'receipt-inspection.json'
    requested=plan(readings,events)
    if not requested and not manifest.exists():return readings,dict(requested_windows=[],processed_windows=[],pending_windows=[],new_frames=0)
    directory.mkdir(parents=True,exist_ok=True)
    capture=directory/'capture.json'
    if capture.exists():
        if json.loads(capture.read_text(encoding='utf-8'))['source']!=source_info:raise ValueError('Training recovery source changed')
    else:save_json(capture,dict(source=source_info))
    prior=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else dict(readings=[],windows=[])
    initial_count=len(prior['readings']);processed=[];pending=[];budget=0;new_windows=0;reader=None
    # Decide the plan first (same budget rule as before), OCR the new windows in
    # a pool, then record them sequentially in plan order.
    steps=[]
    for window in requested:
        cached=any(w['start_ms']==window['start_ms'] and w['end_ms']==window['end_ms'] and w['fps']==60 for w in prior['windows'])
        duration=window['end_ms']-window['start_ms']
        if not cached and (not allow_ocr or new_windows>=max_windows or budget+duration>max_duration_ms):
            pending.append(window);continue
        if not cached:new_windows+=1;budget+=duration
        steps.append((window,cached))
    if allow_ocr:
        from .dense_inspection_pool import prepare_windows
        prepare_windows(source,directory,[w for w,cached in steps if not cached],60,kind='training',model_dir=model_dir,workers=dense_workers)
    for window,cached in steps:
        if not cached and reader is None:reader=TrainingReader(model_dir)
        inspect(source,directory,window['start_ms'],window['end_ms'],fps=60,reader=reader)
        processed.append(window)
    if not manifest.exists():
        return readings,dict(requested_windows=requested,processed_windows=processed,pending_windows=pending,new_frames=0)
    inspection=json.loads(manifest.read_text(encoding='utf-8'))
    fresh=reparse_inspection(inspection,directory)
    for row in fresh:row['evidence']='training-gain-recovery/'+row['evidence']
    merged,candidate_recoveries=promote_with_metadata(readings,fresh,processed)
    metadata=dict(method='bounded_native_training_gain_recovery',source_sha256=(
        source_info.get('sha256') if isinstance(source_info, dict) else None
    ),requested_windows=requested,processed_windows=processed,
        pending_windows=pending,new_frames=len(inspection['readings'])-initial_count,source_samples=len(fresh),
        promoted_source_timestamps=len(merged)-len(readings),fps=60,configured_model_dir=str(model_dir),
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(),complete_event_history=False,
        candidate_only_recovery_policy=candidate_recovery_policy(),
        candidate_only_recoveries=candidate_recoveries,
        candidate_only_recovery_count=sum(1 for item in candidate_recoveries if item.get('status')=='accepted'))
    save_json(directory/'last-plan.json',metadata)
    return merged,metadata
