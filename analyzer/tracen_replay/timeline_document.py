"""Compact per-run timeline document derived from a full-recording report.

The full report keeps every OCR reading (about 95% of its size) and cites
frame images that live in a disposable cache.  The timeline document keeps
only what a consumer of the run needs: the turns with their opening states
and accounting status, and every timeline entry with its timestamps, changes
and the basis on which each amount was accepted.  It contains no image paths;
every fact is located by its source timestamp, from which any frame can be
re-extracted from the source video.

The document is a strict subset of the report: nothing here is computed
beyond selecting and reshaping fields that the report already states.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = 'tracen-replay/timeline-v1'

# Keys whose values are frame images, OCR text dumps or proof envelopes.
_DROP_KEYS = frozenset({
    'evidence', 'field_evidence', 'proof', 'proofs', 'source_evidence', 'applied_result_evidence',
    'supporting_frames', 'supporting_source_refs', 'observations', 'views', 'regions', 'lines', 'ocr',
    'source_observations', 'effect_observations', 'result_group', 'candidate_recovery_provenance',
    'gain_phase_candidates', 'gain_prefix_resolutions', 'source_result_projection',
    'result_continuation_observations', 'performance_evidence', 'performance_reading_candidates',
    'training_identity_evidence', 'preview_overlay_evidence', 'concert_info_evidence',
    'evidence_readings', 'evidence_timestamps_ms', 'field_corroboration', 'timeline_refs',
    'ambiguous_timeline_refs', 'comparisons', 'entries', '_group_rows',
    # Per-field provenance envelopes: the accepted basis per field is already
    # carried on the entry's ``changes``; the envelopes repeat frame lists.
    'direct_gain_provenance', 'source_clipping_resolution', 'preview_confirmed_gains',
    'training_identity', 'identity_basis', 'performance_reading_conflicts_superseded_by_preview_confirmation',
    'conflicting_readings_superseded_by_preview_confirmation', 'gain_phase_candidates',
    'precommit_price_visibility', 'source_resolution', 'phase', 'requests', 'selection',
})
_IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp')
_HEX_DIGEST = frozenset('0123456789abcdef')


def _is_image_path(value):
    return isinstance(value, str) and value.lower().endswith(_IMAGE_SUFFIXES)


def _is_digest(value):
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX_DIGEST


def _compact(value, depth=0):
    """Return ``value`` without image paths and proof envelopes."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _DROP_KEYS or (isinstance(key, str) and (key.endswith('_evidence') or key.endswith('_sha256')
                                                                 or key.endswith('_observations') or key == 'source_identity')):
                continue
            if _is_image_path(item) or _is_digest(item):
                continue
            if isinstance(item, list) and item and all(_is_image_path(x) for x in item):
                continue
            compacted = _compact(item, depth + 1)
            if compacted is None and item is not None:
                continue
            out[key] = compacted
        return out
    if isinstance(value, list):
        items = [_compact(item, depth + 1) for item in value if not _is_image_path(item)]
        return [item for item in items if item is not None or True]
    if _is_image_path(value):
        return None
    return value


def _resolve(report, ref):
    """Follow a JSON pointer such as ``/gameplay_tracking/events/12``."""
    if not isinstance(ref, str) or not ref.startswith('/'):
        return None
    node = report
    for token in ref.lstrip('/').split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        try:
            node = node[int(token)] if isinstance(node, list) else node[token]
        except (KeyError, IndexError, ValueError, TypeError):
            return None
    return node


def _basis_map(report):
    """Map (event ref, channel, field) to the accounting basis of its contribution."""
    out = {}
    accounting = report.get('causal_accounting') or {}
    for contribution in accounting.get('contributions') or []:
        if not isinstance(contribution, dict):
            continue
        key = (contribution.get('event_ref'), contribution.get('channel'), contribution.get('field'))
        out[key] = contribution.get('basis')
    return out


def _settled_conflicts(entry, record, changes):
    """The disputed badges of a training that the stat bars settled, by field.

    The ledger flags a training whose card was read as different gains for a
    field. The accounting settles such a field when the difference the stat
    bars leave for it is one of those reads (``settled_conflicting_readings``
    on the event); the entry then carries that amount, and the other reads
    belong beside it for a reviewer, not as a conflict. Any other source of
    the flag (an ambiguous effect, a performance-row disagreement, an
    acquisition conflict, a worked-out amount the card contradicts) or a
    field the bars did not settle keeps the flag, and this returns None.
    """
    from .reconcile import FIELDS
    if record is None or record.get('kind') != 'training':
        return None
    # A stat's disputed reads sit under conflicting_readings, a performance
    # row's under performance_reading_conflicts; both settle the same way.
    reads = {}
    for key in ('conflicting_readings', 'performance_reading_conflicts'):
        found = record.get(key)
        if isinstance(found, dict):
            reads.update(found)
        elif found:
            return None
    if not reads:
        return None
    if (record.get('ambiguous_effect_candidates') or record.get('contradicted_turn_difference')
            or entry.get('acquisition_conflicts')):
        return None
    settled = record.get('settled_conflicting_readings') or {}
    out = {}
    for field, values in reads.items():
        settlement = settled.get(field)
        change = (changes.get('stats' if field in FIELDS else 'performance') or {}).get(field)
        # A field the bars settled at nothing carries no amount on the entry.
        nothing = change is None and isinstance(settlement, dict) and settlement.get('amount') == 0
        if not nothing and (not isinstance(settlement, dict) or not isinstance(change, dict)
                            or type(settlement.get('amount')) is not int or change.get('amount') != settlement['amount']):
            return None
        out[field] = sorted(v for v in set(values) if v != settlement['amount'])
    return out


def _state_values(state):
    if not isinstance(state, dict):
        return None
    values = state.get('values')
    return dict(values) if isinstance(values, dict) else None


def build(report):
    """Build the timeline document from a full-recording report."""
    source = report.get('source') or {}
    ledger = report.get('turn_ledger') or {}
    accounting = report.get('causal_accounting') or {}
    basis = _basis_map(report)

    contributions = {c.get('id'): c for c in accounting.get('contributions') or [] if isinstance(c, dict)}
    # Amounts the accounting assigned to an owner's entry: worked out from the
    # turn difference, or read on the card by the learned reader where it
    # equals that difference.
    extrapolations = [c for c in contributions.values()
                      if c.get('basis') in ('turn_difference', 'observed_learned_training_gain')]

    def owner_kind(contribution):
        ref = str(contribution.get('event_ref') or '')
        if '/lesson_purchases/' in ref:
            return 'lesson'
        if '/races/' in ref:
            return 'race'
        record = _resolve(report, ref)
        kind = record.get('kind') if isinstance(record, dict) else None
        return 'training' if kind == 'training' else 'event' if kind else None

    transitions = {}
    for transition in accounting.get('turn_transitions') or []:
        if not isinstance(transition, dict):
            continue
        fields = {}
        for field in transition.get('fields') or []:
            if isinstance(field, dict) and field.get('field'):
                # The part of the derived amount that is the turn difference assigned
                # to its only possible owner (the sole training, the one receipt that
                # lost its number, or the one lesson without an observed cost): shown
                # as worked out from the difference, replaceable by a viewer.
                worked = [contributions[r] for r in field.get('contribution_refs') or []
                          if r in contributions and contributions[r].get('basis') == 'turn_difference'
                          and type(contributions[r].get('amount')) is int]
                extrapolated = sum(c['amount'] for c in worked)
                owners = sorted({k for k in (owner_kind(c) for c in worked) if k})
                fields[field['field']] = dict(
                    before=field.get('before'), after=field.get('after'), status=field.get('status'),
                    direct=field.get('direct_change'), derived=field.get('derived_or_summary_change'),
                    unresolved=field.get('unresolved_change'), turn_difference=extrapolated or None,
                    turn_difference_owner=owners[0] if len(owners) == 1 else None)
        for row in fields.values():
            row['window_start_ms'] = transition.get('start_ms')
            row['window_end_ms'] = transition.get('end_ms')
        transitions.setdefault(transition.get('turn_id'), {})[transition.get('channel')] = fields

    turns = []
    for turn in ledger.get('turns') or []:
        if not isinstance(turn, dict):
            continue
        states = turn.get('states') or {}
        turns.append(dict(
            id=turn.get('id'), label=turn.get('label'), phase=turn.get('phase'), calendar_value=turn.get('calendar_value'),
            start_ms=turn.get('start_ms'), end_ms=turn.get('end_ms'), window_kind=turn.get('window_kind'),
            action_status=turn.get('action_status'), action_count=turn.get('action_count'),
            expects_one_action=bool(turn.get('expects_one_action')),
            scheduled_race=turn.get('scheduled_race'),
            opening=dict(stats=_state_values((states.get('stats') or {}).get('opening')),
                         performance=_state_values((states.get('performance') or {}).get('opening'))),
            accounting=transitions.get(turn.get('id'), {}),
        ))

    entries = []
    for entry in ledger.get('timeline') or []:
        if not isinstance(entry, dict):
            continue
        ref = entry.get('source_ref')
        record = _resolve(report, ref)
        changes = {}
        for channel, key in (('stats', 'stat_changes'), ('performance', 'performance_changes')):
            amounts = entry.get(key)
            if isinstance(amounts, dict) and amounts:
                changes[channel] = {
                    field: dict(amount=amount.get('amount') if isinstance(amount, dict) else amount,
                                basis=basis.get((ref, channel, field)))
                    for field, amount in amounts.items()}
        for c in extrapolations:
            # A training committed from its result card alone has a committed
            # action pointing at the same event; the amounts live on the event's
            # own entry, never on both.
            if (c.get('event_ref') == ref and entry.get('kind') != 'committed_action'
                    and c.get('channel') and c.get('field')):
                existing = (changes.get(c['channel']) or {}).get(c['field'])
                if c.get('completes') and existing and type(existing.get('amount')) is int and type(c.get('amount')) is int:
                    # A clipped badge: the read digits plus the difference.
                    changes[c['channel']][c['field']] = dict(amount=existing['amount'] + c['amount'], basis=c['basis'],
                                                              read_amount=existing['amount'])
                else:
                    changes.setdefault(c['channel'], {})[c['field']] = dict(amount=c.get('amount'), basis=c['basis'])
                # What the card showed instead, so a review can see both numbers.
                reads = [r.get('gain') for r in c.get('contradicted_reads') or [] if type(r.get('gain')) is int]
                if reads:
                    changes[c['channel']][c['field']]['contradicted_by'] = sorted(set(reads))
        item = dict(
            id=entry.get('id'), kind=entry.get('kind'), turn_id=entry.get('turn_id'),
            first_seen_ms=entry.get('first_seen_ms'), last_seen_ms=entry.get('last_seen_ms'),
            assignment_basis=entry.get('assignment_basis'),
        )
        for key in ('context_title', 'training_option', 'action_kind', 'identity_basis', 'effect', 'raw_text',
                    'accepted_award', 'accounting_role', 'transaction_id', 'reward_link_status', 'conflicts_present'):
            if entry.get(key) not in (None, '', [], {}):
                item[key] = _compact(entry[key]) if isinstance(entry[key], (dict, list)) else entry[key]
        settled = _settled_conflicts(entry, record, changes) if item.get('conflicts_present') else None
        if settled is not None:
            # The card's reads disagreed and the stat bars settled the field at
            # one of them: shown beside the amount, not as a conflict.
            item['conflicts_present'] = False
            from .reconcile import FIELDS
            for field, others in settled.items():
                change = (changes.get('stats' if field in FIELDS else 'performance') or {}).get(field)
                if others and change is not None:
                    change['disagreeing_reads'] = others
        if changes:
            item['changes'] = changes
        if isinstance(record, dict):
            detail = _compact(record)
            for key in ('id', 'kind', 'first_seen_ms', 'last_seen_ms', 'deltas', 'performance_deltas',
                        'stat_changes', 'performance_changes', 'source_ref'):
                detail.pop(key, None)
            if detail:
                item['detail'] = detail
        entries.append(item)

    summary = dict(
        field_status_counts=(accounting.get('summary') or {}).get('field_status_counts'),
        action_statuses=(ledger.get('summary') or {}).get('action_statuses'),
        observed_turn_windows=(ledger.get('summary') or {}).get('observed_turn_windows'),
        entry_counts={},
        stage_failures=report.get('stage_failures') or [],
    )
    for item in entries:
        summary['entry_counts'][item['kind']] = summary['entry_counts'].get(item['kind'], 0) + 1

    recognition = report.get('recognition') or {}
    return dict(
        schema_version=SCHEMA,
        report_schema_version=report.get('schema_version'),
        source=dict(name=source.get('name'), sha256=source.get('sha256'), duration_ms=source.get('duration_ms'),
                    width=source.get('width'), height=source.get('height')),
        recognition=dict(model=recognition.get('model'), device=recognition.get('device')),
        turns=turns,
        entries=entries,
        summary=summary,
        note='Facts are located by source timestamps in milliseconds; frame images are not referenced and may be deleted.',
    )


def write(report, path):
    """Write the timeline document next to a report and return its size in bytes."""
    payload = json.dumps(build(report), ensure_ascii=False, separators=(',', ':'))
    Path(path).write_text(payload, encoding='utf-8')
    return len(payload.encode('utf-8'))
