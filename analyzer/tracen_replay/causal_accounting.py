"""Evidence-linked accounting, separate from arithmetic and coverage claims.

Canonical events own awards. Action, lesson and song summaries are references
to those awards, not new credits. Purchases contribute only their debit here.
An observed state difference never manufactures a missing receipt, with one
explicit and flagged exception: a turn's remaining difference for a field goes
to its only possible owner, the sole training whose gain for the field was
not read, the one receipt that named the field but lost its number, the one
lesson without an observed cost, or the one skill batch whose charge was never
read (basis ``turn_difference``). When that owner is a training and the
learned reader read exactly the difference as the stat's gain on its card,
or the value the stat lands on with it, the amount is observed instead
(basis ``observed_learned_training_gain``), as is a training gain worked out
from the result totals that the reader read on two of the card's frames;
the reader's reads never make a difference, they only match one. Boundary source probes may make a turn
opening available after reassembly, but this module never promotes a terminal
observation or a later state into an endpoint.
"""
import re
from collections import Counter, defaultdict
from copy import deepcopy

from .reconcile import FIELDS
from .gameplay import CURRENCIES
from .source_clock import elapsed
# A result card is the same card on a frame whose banner left the screen a
# candidate, so both count as the training's own frames.
from .learned_reader import RESULT_SCREENS as _RESULT_SCREENS


CHANNELS = {'stats': tuple(FIELDS), 'performance': tuple(CURRENCIES)}
# Bases that are read off the screen rather than worked out; the learned reader's
# gain counts only where it equals a difference the stat bars worked out.
_OBSERVED_BASES = ('observed_receipt', 'observed_training_gain', 'observed_learned_training_gain', 'committed_skill_debit')

_STATE_CONSTRAINED_BASES = frozenset({
    'visible_complete_gain_and_surrounding_state_constraints',
    'visible_gain_candidate_and_stable_result_counter',
    'visible_gain_candidate_and_stable_result_suffix',
    'single_visible_gain_candidate_and_surrounding_state_constraints',
})


def _proof(value):
    if isinstance(value, str):
        value = [value]
    return list(dict.fromkeys(value or []))


def _source_proof_paths(value):
    """Return only source path strings from a possibly nested proof value."""

    paths = []

    def visit(item):
        if isinstance(item, str):
            if item and item not in paths:
                paths.append(item)
            return
        if isinstance(item, (list, tuple)):
            for child in item:
                visit(child)

    visit(value)
    return paths


def _state_constrained_gain_proof(event, field, amount):
    """Propagate a selected candidate's source paths without selecting it.

    ``state_supported_candidate_resolutions`` is an upstream, field-scoped
    selection.  Causal accounting may cite its already selected gain frames,
    but it must not use those frames to choose an amount or to turn the
    candidate into an observed receipt.  Ambiguous or amount-inconsistent
    resolutions therefore contribute no paths.
    """

    resolutions = [
        resolution
        for resolution in event.get('state_supported_candidate_resolutions', [])
        if isinstance(resolution, dict) and resolution.get('field') == field
    ]
    if len(resolutions) != 1:
        return []
    resolution = resolutions[0]
    if (type(amount) is not int or type(resolution.get('amount')) is not int
            or resolution.get('amount') != amount
            or resolution.get('basis') not in _STATE_CONSTRAINED_BASES):
        return []
    return _source_proof_paths(resolution.get('gain_evidence'))


def _debit_timed(purchase):
    """The purchase dated at its debit (request to matched balance), not its receipt.

    A receipt shown after the next turn's opening panel would otherwise put
    the cost in the wrong window.
    """
    window = purchase.get('debit_window_ms')
    if (isinstance(window, (list, tuple)) and len(window) == 2 and all(type(v) is int for v in window)
            and window[0] <= window[1]):
        return dict(purchase, first_seen_ms=window[0], last_seen_ms=window[1])
    return purchase


def _lesson_cost_basis(purchase, receipt, readings):
    """A committed, source-priced purchase differs from a browsed projection.

    Offer/request joins remain derived costs: this does not claim observation
    of the resulting balance or an independently measured debit.
    """
    # Both bases rest on the actual balance read before and after the purchase:
    # the projection matched by the next balance, or two repeated balances.
    if purchase.get('cost_basis') in ('observed_debit','receipt_and_repeated_observed_balances'):return 'observed_balance_debit'
    if purchase.get('cost_basis')=='state_confirmed_balance_drop':return 'state_derived_debit'
    if purchase.get('cost_basis')!='receipt_request_and_observed_offer_prices':return 'projected_debit'
    proof=purchase.get('offer_cost_evidence',{}); name=purchase.get('name')
    if (not receipt or not name or purchase.get('name_conflicted') or proof.get('cost')!=purchase.get('performance_cost')
        or proof.get('receipt',{}).get('event_id')!=receipt.get('id')
        or proof.get('receipt_name')!=name or proof.get('request',{}).get('title')!=name
        or proof.get('offer',{}).get('title')!=name):return 'projected_debit'
    awards=[e for e in receipt.get('effects',[]) if e.get('kind') in ('song_learned','named_acquisition')]
    if len(awards)!=1 or awards[0].get('name')!=name or receipt.get('conflicting_readings'):return 'projected_debit'
    times={r['evidence']:r['source_timestamp_ms'] for r in readings}
    for section in ('offer','request'):
        group=proof[section]; stamps=group.get('timestamps_ms',[]); paths=_proof(group.get('evidence'))
        if (len(set(stamps))<2 or not paths or any(p not in times or times[p] not in stamps for p in paths)
            or {times[p] for p in paths if p in times}!=set(stamps)
            or max(stamps)>=receipt['first_seen_ms']):return 'projected_debit'
    if max(proof['offer']['timestamps_ms'])>=min(proof['request']['timestamps_ms']):return 'projected_debit'
    return 'committed_offer_cost_derived'


def _field_conflicts(event, channel, field):
    """Keep a disagreement attached to its declared field, not every award.

    Training readers use plain stat-field keys; receipt readers use typed
    kind|field|name keys. Unscoped or malformed conflicts stay conservative.
    This does not resolve the disputed field, even when its total balances.
    """
    performance_conflicts = event.get('performance_reading_conflicts', {})
    if performance_conflicts and channel == 'performance':
        if not isinstance(performance_conflicts, dict) or any(k not in CHANNELS[channel] for k in performance_conflicts):
            return True
        if field in performance_conflicts:
            return True
    conflicts = event.get('conflicting_readings', [])
    if not conflicts:
        return False
    if isinstance(conflicts, dict):
        if event.get('kind') != 'training' or any(k not in FIELDS for k in conflicts):
            return True
        return channel == 'stats' and field in conflicts
    if not isinstance(conflicts, list):
        return True
    expected_kind = 'stat_change' if channel == 'stats' else 'performance_change'
    for conflict in conflicts:
        key = conflict.get('field') if isinstance(conflict, dict) else None
        parts = key.split('|') if isinstance(key, str) else []
        if len(parts) != 3 or not parts[0]:
            return True
        kind, affected, name = parts
        if kind in ('stat_change', 'performance_change'):
            target_channel = 'stats' if kind == 'stat_change' else 'performance'
            if affected not in CHANNELS[target_channel] or name:
                return True
            if kind == expected_kind and affected == field:
                return True
        elif kind in ('stat_cap_change', 'performance_cap_change', 'training_modifier_change'):
            cap_channel = 'performance' if kind == 'performance_cap_change' else 'stats'
            if affected not in CHANNELS[cap_channel] or name:
                return True
        elif kind in ('friendship_change', 'friendship_status', 'skill_hint_change',
                      'inheritance_inspiration', 'inheritance_spark', 'condition_acquired', 'condition_removed'):
            if affected or not name:
                return True
        elif kind in ('energy_change', 'max_energy_change', 'energy_status',
                      'mood_change', 'mood_status', 'fan_change'):
            if affected or name:
                return True
        else:
            return True
    return False


_FIELD_WORDS = {'speed': 'speed', 'stamina': 'stamina', 'power': 'power', 'guts': 'guts', 'wit': 'wit',
                'skill_points': 'skill', 'dance': 'dance', 'passion': 'passion', 'vocal': 'vocal', 'visual': 'visual',
                'composure': 'composure'}


def _resolve_pointer(report, ref):
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


def _caption_direction(field, raw_text):
    """'up' or 'down' when the line is this field's own receipt caption, else None.

    Only ``<field> went up/down`` counts: a friendship line containing the
    letters of a stat, or a ``Power Bonus`` modifier line, is not the field's
    receipt.
    """
    word = _FIELD_WORDS.get(field, field)
    text = ' '.join(str(raw_text or '').split())
    match = re.match(rf'{word}(?:s|\s*p(?:ts|oints))?\s+went\s*(up|down)\b', text, re.I)
    return match[1].lower() if match else None


def _unparsed_mentions(candidates, field, start, end):
    """The unparsed receipt captions of the field inside the window."""
    found = []
    for candidate in candidates or ():
        if not isinstance(candidate, dict):
            continue
        time = candidate.get('first_seen_ms')
        if type(time) is not int or not start <= time <= end:
            continue
        if _caption_direction(field, candidate.get('raw_text')):
            found.append(candidate)
    return found


_CONFUSABLE_DIGITS = str.maketrans({'T': '1', 'I': '1', 'l': '1', 'i': '1', 'O': '0', 'o': '0', 'S': '5', 'B': '8'})


def _caption_digits(raw_text):
    """The digits a number-cut caption still shows after ``by``; '' when none, None when unreadable."""
    text = ' '.join(str(raw_text or '').split())
    match = re.search(r'by\b(.*)$', text, re.I)
    if not match:
        return ''
    tail = match[1].strip().rstrip('.!').strip()
    if not tail:
        return ''
    digits = tail.translate(_CONFUSABLE_DIGITS)
    return digits if digits.isdigit() else None


def _training_can_own(event, read_key, field):
    """Whether a training's gain for the field is unknown rather than absent."""
    read = event.get(read_key) or {}
    if field in read:
        return False
    if not read:
        return True
    # A performance row the card never read (a badge over it, a merged read
    # under the floor) is unknown, unlike a row read as its current value.
    if read_key == 'performance_deltas' and field in (event.get('performance_rows_unread') or ()):
        return True
    # A performance row read two ways is unknown too, the way a disputed stat badge is.
    if read_key == 'performance_deltas' and field in (event.get('performance_reading_conflicts') or {}):
        return True
    conflicts = event.get('conflicting_readings') or {}
    names = set(conflicts) if isinstance(conflicts, dict) else {c.get('field') for c in conflicts if isinstance(c, dict)}
    return field in names or field in (event.get('gain_conflicts') or {})


def _learned_gain_frames(event, readings, field, amount, value_after=None):
    """The training's own result frames on which the learned reader read exactly this gain.

    The learned reader's reads are observations stored on the readings (see
    ``learned_reader``). A read only matters where it equals a difference the
    stat bars left unexplained: the stat bars, not the model, decide. Given
    ``value_after``, the stat's value once this gain lands, frames that read
    that value count too, but only when it is the last value read on the card:
    the badge counts up to it and stays, so an earlier frame can show a value
    on the way. A value confirms the gain only through the amounts counted
    before the card, so it never overrules a gain read on the same card that
    disagrees; a zoomed gain cut to its leading digits does not disagree.
    """
    start, end = event.get('first_seen_ms'), event.get('last_seen_ms')
    if type(start) is not int or type(end) is not int or type(amount) is not int or amount <= 0:
        return []
    frames, values, gains = [], [], set()
    for row in readings:
        time = row.get('source_timestamp_ms')
        # Only frames inside the training's own window: a confirmed amount is
        # timed by its frames, and one outside the window would leave it untimed.
        if row.get('screen') not in _RESULT_SCREENS or type(time) is not int or not start <= time <= end:
            continue
        if not isinstance(row.get('evidence'), str):
            continue
        reads = (row.get('facts') or {}).get('learned_result_reads')
        read = ((reads.get('fields') or {}).get(field) or {}) if isinstance(reads, dict) else {}
        if read.get('gain') == amount:
            frames.append(row['evidence'])
        if type(read.get('gain')) is int:
            gains.add(read['gain'])
        if type(read.get('value')) is int:
            values.append((time, read['value'], row['evidence']))
    disagrees = any(not str(amount).startswith(str(gain)) for gain in gains)
    if (type(value_after) is int and values and not disagrees
            and max(values, key=lambda v: v[0])[1] == value_after):
        frames.extend(proof for _, value, proof in values if value == value_after)
    return list(dict.fromkeys(frames))


def _complete_list_from_cart(batch, charge, data):
    """A batch whose named cart prices add up to the turn's charge has a complete list.

    The receipt's own charge was never read, so the turn's point drop stands
    in for it. When the items the cart named cost exactly that between them,
    every purchased skill is one of them; the charge itself stays worked out.
    The batch's transaction row is completed the same way, since the ledger
    shows both.
    """
    twins = [t for t in data.get('skill_purchases') or [] if isinstance(t, dict) and t.get('id') == batch.get('id')]
    if batch.get('spent_skill_points') is None and batch.get('cart_net_cost') == charge and charge > 0:
        # The cart counter's net change and the turn's drop agree: that is
        # the charge, worked out from two independent readings, whether or
        # not every purchased skill was named.
        for target in (batch, *twins):
            target.update(spent_skill_points=charge, spent_skill_points_basis='turn_difference_matches_cart_net_cost')
    items = batch.get('selected_item_candidates') or []
    if not items or batch.get('purchased_list_complete'):
        return
    visible = batch.get('visible_confirmation_names') or []
    priced = {item['name']: item['cost'] for item in items
              if isinstance(item, dict) and item.get('basis') == 'visible_confirmation_and_price'
              and isinstance(item.get('name'), str) and type(item.get('cost')) is int}
    if visible and all(name in priced for name in visible) and sum(priced[name] for name in visible) == charge:
        # The names the confirmation showed each carry a price, and those
        # prices add up to the turn's drop: nothing scrolled off the list
        # (the same inference the cart's own net cost supports), whatever
        # else the cart counter saw come and go before the purchase.
        basis, names = 'visible_names_cost_the_turn_difference', sorted(set(visible))
    elif (any(not isinstance(item, dict) or type(item.get('cost')) is not int or not item.get('name')
              for item in items)
          or sum(item['cost'] for item in items) != charge):
        return
    else:
        basis, names = 'cart_costs_match_turn_difference', sorted({item['name'] for item in items} | set(visible))
    completed = dict(spent_skill_points=charge, spent_skill_points_basis='turn_difference',
                     purchased_list_complete=True, purchased_list_basis=basis,
                     purchased_skill_names=names)
    for target in (batch, *twins):
        target.update(completed)
        if target.get('identity_status_basis') == 'incomplete_skill_confirmation_list':
            for key in ('identity_status', 'identity_status_evidence', 'identity_status_basis'):
                target.pop(key, None)


def _settle_conflicting_reads(event, field, gain, settled_by, reads=None):
    """Record that the stat bars settled a badge whose reads disagreed.

    A training whose card was read as two or three gains for one field (a
    digit cut by the sparkle, a glyph misread under the glow, beside the
    number the badge settles on) carries the reads as ``conflicting_readings``
    and no amount. When the difference the stat bars leave for the field,
    confirmed on the card by the learned reader or standing on its own, is
    one of those very reads, the disagreement is settled: the card showed
    this number too. The reads stay beside the amount for a reviewer. A
    difference that matches none of them settles nothing, and neither does
    one the learned reader read differently on the card: a text read that
    happens to equal a difference some other error corrupted is how a wrong
    number would slip through, and the model's disagreement is the alarm.
    """
    # A stat's disputed reads sit under conflicting_readings, a performance
    # row's under performance_reading_conflicts; the bars settle both alike.
    # A panel read the card outranks is passed in as the disputed reads: the
    # accounting never writes it into the event's own conflict list, so the
    # report rebuilt from its records projects the same accounting again.
    if reads is None:
        source = event.get('conflicting_readings') if field in FIELDS else event.get('performance_reading_conflicts')
        if not isinstance(source, dict) or not isinstance(source.get(field), list):
            return
        reads = source[field]
    settled = dict(amount=gain, reads=sorted(set(reads)), settled_by=settled_by)
    if gain not in reads:
        # The cursor or a sparkle over the badge's last digit leaves a read cut
        # to its leading digits ("3" of 36) on frame after frame. When the
        # learned reader read the whole number on the card, that cut read is
        # the same number, not a disagreement; a bare turn difference gets
        # no such allowance.
        cut = [read for read in reads if type(read) is int and 0 < read < gain
               and str(gain).startswith(str(read))]
        if not cut or not str(settled_by).startswith('learned_reader'):
            return
        settled['completes_read'] = max(cut)
    event.setdefault('settled_conflicting_readings', {})[field] = settled


def _learned_gain_contradictions(event, readings, field, amount):
    """The reads on a training's own card that contradict a worked-out amount.

    The stat bars decide an amount, so a gain the model read never overrules
    them and this changes no amount. But a difference worked out for a
    training whose card the model read as a different gain is a number the
    card disagrees with, and only a reviewer can say which is right. A read
    cut to its leading digits, as a zoomed badge leaves it, is not a
    disagreement.
    """
    start, end = event.get('first_seen_ms'), event.get('last_seen_ms')
    if type(start) is not int or type(end) is not int or type(amount) is not int:
        return []
    found = {}
    for row in readings:
        time = row.get('source_timestamp_ms')
        if row.get('screen') not in _RESULT_SCREENS or type(time) is not int or not start <= time <= end:
            continue
        if not isinstance(row.get('evidence'), str):
            continue
        reads = (row.get('facts') or {}).get('learned_result_reads')
        read = ((reads.get('fields') or {}).get(field) or {}) if isinstance(reads, dict) else {}
        gain = read.get('gain')
        if type(gain) is not int or str(amount).startswith(str(gain)):
            continue
        found.setdefault(gain, []).append(row['evidence'])
    return [dict(gain=gain, evidence=frames) for gain, frames in sorted(found.items())]


def _clipped_start(read, total):
    """True when a badge read is the leading digit(s) of the whole gain the card showed."""
    return type(read) is int and read > 0 and str(total).startswith(str(read)) and len(str(total)) > len(str(read))


def _value_after_training(row, contributions, event, gain):
    """A stat's value once a training's gain lands, from the turn's opening value.

    The opening plus every amount the turn counted for the field that was
    observed before the training's card, plus the gain. None when any of those
    amounts is unknown.
    """
    before, start = row.get('before'), event.get('first_seen_ms')
    if type(before) is not int or type(start) is not int or type(gain) is not int:
        return None
    refs = set(row.get('contribution_refs') or ())
    earlier = [c for c in contributions if c['id'] in refs
               and type(c.get('observation_end_ms')) is int and c['observation_end_ms'] < start]
    if any(type(c.get('amount')) is not int for c in earlier):
        return None
    return before + sum(c['amount'] for c in earlier) + gain


def _clipped_badge_mode(event, readings, read_key, field, residual):
    """How a read training panel can still own a positive difference for a field.

    ``clipped_badge_prefix``: the badge was read as the leading digit(s) of
    the true gain (a 1 that was 11, a 3 that was 35); the read value plus the
    difference must start with the read value and be longer. ``visible_candidate``:
    no value was accepted for the field, but a result frame showed a candidate
    that equals the difference or is its leading digits (a 33 dropped by a
    conflicting frame, a 6 that was 64). Anything else is None.
    """
    read = (event.get(read_key) or {}).get(field)
    if type(read) is int and read > 0:
        total = read + residual
        return 'clipped_badge_prefix' if str(total).startswith(str(read)) and len(str(total)) > len(str(read)) else None
    if read is not None:
        return None
    start, end = event.get('first_seen_ms'), event.get('last_seen_ms')
    if type(start) is not int or type(end) is not int:
        return None
    seen = set()
    for row in readings:
        time = row.get('source_timestamp_ms')
        if row.get('screen') != 'training_result' or type(time) is not int or elapsed(time, start) > 250 or elapsed(end, time) > 250:
            continue
        facts = row.get('facts') or {}
        if read_key == 'deltas':
            values = (facts.get('training_gain_candidates') or {}).get(field) or []
            gains = (facts.get('training_gains') or {}).get(field)
            values = list(values) + ([gains] if type(gains) is int else [])
        else:
            award = (facts.get('awarded_performance_gains') or {}).get(field)
            values = [award] if type(award) is int else []
        seen.update(v for v in values if type(v) is int and v > 0)
    if any(v == residual or (str(residual).startswith(str(v)) and len(str(residual)) > len(str(v))) for v in seen):
        return 'visible_candidate'
    return None


def _caption_owner(data, candidate, field, channel, event_refs):
    """The one outcome event a number-cut caption belongs to, or None.

    A caption whose event already read a value for the field is a duplicate
    line, not a lost number; it neither owns nor blocks the difference.
    """
    time = candidate.get('first_seen_ms')
    read_key = 'effects'
    owners = []
    for index, event in enumerate(data['events']):
        if event.get('kind') != 'outcome':
            continue
        if elapsed(time, event['first_seen_ms']) > 500 or elapsed(event['last_seen_ms'], time) > 500:
            continue
        kind = 'stat_change' if channel == 'stats' else 'performance_change'
        if any(e.get('kind') == kind and e.get('field') == field and type(e.get('amount')) is int
               for e in event.get(read_key) or []):
            return 'represented'
        owners.append(index)
    return owners[0] if len(owners) == 1 else None


def build(report):
    data = report['gameplay_tracking']
    if data.get('auxiliary_log_used') is not False:
        raise ValueError('Causal accounting requires explicit gameplay-only input')
    times = {}
    for row in data['readings']:
        proof, time = row['evidence'], row['source_timestamp_ms']
        if proof in times and times[proof] != time:
            raise ValueError('One evidence identity has contradictory timestamps')
        times[proof] = time
    contributions, issues, context = [], [], []
    event_refs = {}
    owners = {e['source_ref']: e for e in report.get('turn_ledger', {}).get('timeline', [])}

    def add(ref, parent, event, channel, field, amount, evidence, basis, *, conflicts=False):
        if field not in CHANNELS[channel]:
            raise ValueError(f'Unsupported {channel} field: {field}')
        if amount is not None and type(amount) is not int:
            raise ValueError(f'Noninteger contribution at {ref}')
        start = event.get('first_seen_ms', event.get('source_timestamp_ms'))
        end = event.get('last_seen_ms', start)
        proof = _proof(evidence)
        positive = [times[p] for p in proof if p in times and start <= times[p] <= end]
        owner = owners.get(parent, {})
        contributions.append(dict(id=ref, source_ref=ref, event_ref=parent,
            event_id=event.get('id'), channel=channel, field=field, amount=amount,
            observation_start_ms=min(positive) if positive else start,
            observation_end_ms=max(positive) if positive else end,
            evidence=proof, timing_basis='field_observations' if positive else 'parent_window_only',
            basis=basis, conflicts_present=conflicts,
            turn_id=owner.get('turn_id'), candidate_turn_ids=owner.get('candidate_turn_ids', []),
            turn_assignment_basis=owner.get('assignment_basis', 'unresolved'),
            independent_effect_verification=False))

    for index, event in enumerate(data['events']):
        parent = f'/gameplay_tracking/events/{index}'
        identity = event.get('id')
        if not identity or identity in event_refs:
            raise ValueError('Canonical event IDs must be unique')
        event_refs[identity] = parent
        represented = set()
        effect_totals = defaultdict(int)
        for ei, effect in enumerate(event.get('effects', [])):
            ref = f'{parent}/effects/{ei}'
            kind, field = effect.get('kind'), effect.get('field')
            channel = {'stat_change': 'stats', 'performance_change': 'performance'}.get(kind)
            key = '|'.join(str(effect.get(k) or '') for k in ('kind', 'field', 'name'))
            evidence = event.get('field_evidence', {}).get(key, [])
            if channel:
                represented.add((channel, field))
                amount = effect.get('amount')
                if type(amount) is int:
                    effect_totals[(channel, field)] += amount
                # A lesson's stat the receipt never showed, awarded from the
                # dialog's projection, is worked out, not observed.
                add(ref, parent, event, channel, field, amount, evidence,
                    'lesson_confirmation_projection' if effect.get('amount_basis') == 'lesson_confirmation_projection' else 'observed_receipt',
                    conflicts=_field_conflicts(event, channel, field))
            else:
                context.append(dict(source_ref=ref, event_ref=parent, effect=deepcopy(effect),
                                    evidence=_proof(evidence), accounting_role='outside_reconciled_numeric_channels'))
        if (event.get('kind') == 'skill_purchase_batch'
                and type((event.get('deltas') or {}).get('skill_points')) is not int):
            # A committed batch whose charge was never read carries no debit.
            # Name it here, the way an unpriced lesson is named, so its points
            # are not left as an unattributed negative difference.
            issues.append(dict(kind='unobserved_purchase_debit', source_ref=parent))
        for channel, key in (('stats', 'deltas'), ('performance', 'performance_deltas')):
            for field, amount in event.get(key, {}).items():
                if (channel, field) in represented:
                    if amount != effect_totals[(channel, field)]:
                        issues.append(dict(kind='summary_effect_disagreement', source_ref=f'{parent}/{key}/{field}',
                            summary_amount=amount, receipt_sum=effect_totals[(channel, field)]))
                    continue
                basis = ('state_derived' if field in event.get('result_state_derived_fields', []) else
                         'state_constrained' if field in {r.get('field') for r in event.get('state_supported_candidate_resolutions', [])} else
                         'observed_training_gain' if event['kind'] == 'training' else
                         'committed_skill_debit' if event['kind'] == 'skill_purchase_batch' else 'summary_only')
                proof_key = 'field_evidence' if channel == 'stats' else 'performance_evidence'
                evidence = event.get(proof_key, {}).get(field, [])
                if basis == 'state_constrained':
                    evidence = [
                        *_proof(evidence),
                        *_state_constrained_gain_proof(event, field, amount),
                    ]
                if basis == 'committed_skill_debit':
                    evidence = event.get('evidence', [])
                if basis in ('state_derived', 'state_constrained') and channel == 'stats' and event['kind'] == 'training':
                    # The totals can settle a gain before the card's reads are
                    # weighed (a digit hidden on one frame puts the badge in
                    # dispute). When the learned reader read this very gain on
                    # two of the card's frames and another on none, the card
                    # showed it: the amount was read, not worked out.
                    learned = _learned_gain_frames(event, data['readings'], field, amount)
                    if len(learned) >= 2 and not _learned_gain_contradictions(event, data['readings'], field, amount):
                        basis, evidence = 'observed_learned_training_gain', learned
                add(f'{parent}/{key}/{field}', parent, event, channel, field, amount, evidence,
                    basis, conflicts=_field_conflicts(event, channel, field))
    for index, purchase in enumerate(data.get('lesson_purchases', [])):
        parent = f'/gameplay_tracking/lesson_purchases/{index}'
        receipt = event_refs.get(purchase.get('receipt_event_id'))
        if receipt is None:
            issues.append(dict(kind='missing_purchase_receipt', source_ref=parent))
        cost = purchase.get('performance_cost')
        if cost is None:
            issues.append(dict(kind='unobserved_purchase_debit', source_ref=parent))
            continue
        for field, amount in cost.items():
            if type(amount) is not int or amount < 0:
                raise ValueError('Purchase cost must be a nonnegative integer')
            if amount:
                basis = _lesson_cost_basis(purchase, next((e for e in data['events']
                    if e['id']==purchase.get('receipt_event_id')),None), data['readings'])
                add(f'{parent}/performance_cost/{field}', parent, _debit_timed(purchase), 'performance', field,
                    -amount, purchase.get('evidence'), basis, conflicts=receipt is None)

    by_id = {c['id']: c for c in contributions}
    claims = defaultdict(list)
    for c in contributions:
        if c.get('completes'):
            continue  # a clipped-badge completion sits beside the digits it completes
        claims[(c['event_ref'], c['channel'], c['field'])].append(c)
    for rows in claims.values():
        if len(rows) > 1:
            issues.append(dict(kind='multiple_effect_claims_for_one_event_field',
                               contribution_refs=[c['id'] for c in rows]))
            for c in rows:
                c['conflicts_present'] = True
    physical_claims = defaultdict(list)
    for c in contributions:
        for proof in c['evidence']:
            if proof in times and c['observation_start_ms'] <= times[proof] <= c['observation_end_ms']:
                physical_claims[(c['channel'],c['field'],c['basis'],proof)].append(c)
    duplicate_groups = set()
    for rows in physical_claims.values():
        if len({c['event_ref'] for c in rows}) < 2:
            continue
        identities = tuple(sorted(c['id'] for c in rows))
        if identities not in duplicate_groups:
            issues.append(dict(kind='shared_source_effect_claim',contribution_refs=list(identities)))
            duplicate_groups.add(identities)
        for c in rows:
            c['conflicts_present'] = True
    assigned = set()
    def compare_fields(channel, before, after, start, end, *, assign=False):
        # A closing read off several ending screens ends each field where
        # that field was last read; a change after a field's own last
        # reading is not in its comparison.
        field_ends = after.get('field_times') or {}
        accepted, uncertain = [], []
        for c in contributions:
            if c['channel'] != channel:
                continue
            first, last = c['observation_start_ms'], c['observation_end_ms']
            field_end = field_ends.get(c['field'], end)
            if not (last > start and first <= field_end):
                continue
            reasons = []
            if not start < first <= last <= field_end:
                reasons.append('crosses_observed_endpoint')
            if c['timing_basis'] != 'field_observations':
                reasons.append('missing_field_timing')
            if c['amount'] is None:
                reasons.append('unobserved_amount')
            if c['conflicts_present']:
                reasons.append('conflicting_evidence')
            if c['basis'] in ('projected_debit', 'summary_only'):
                reasons.append('no_observed_resource_change')
            if reasons:
                uncertain.append(dict(contribution_ref=c['id'], reasons=reasons))
            else:
                accepted.append(c)
                if assign: assigned.add(c['id'])
        resource_rows = []
        for field in CHANNELS[channel]:
            left, right = before['values'].get(field), after['values'].get(field)
            observed = right - left if type(left) is int and type(right) is int else None
            parts = [c for c in accepted if c['field'] == field]
            direct = sum(c['amount'] for c in parts if c['basis'] in _OBSERVED_BASES)
            derived = sum(c['amount'] for c in parts if c['basis'] not in _OBSERVED_BASES)
            residual = observed - direct - derived if observed is not None else None
            ambiguous = [x for x in uncertain if by_id[x['contribution_ref']]['field'] == field]
            # A lesson's displayed cost that the turn's own difference confirms
            # to the point is that cost: the bars decide, as they do for a
            # badge. Any other doubt on the field keeps it unresolved.
            projected = [x for x in ambiguous if x['reasons'] == ['no_observed_resource_change']
                         and by_id[x['contribution_ref']]['basis'] == 'projected_debit'
                         and type(by_id[x['contribution_ref']]['amount']) is int]
            if (residual is not None and projected and len(projected) == len(ambiguous)
                    and sum(by_id[x['contribution_ref']]['amount'] for x in projected) == residual):
                for x in projected:
                    c = by_id[x['contribution_ref']]
                    c['basis'] = 'projected_debit_confirmed_by_turn_difference'
                    parts.append(c)
                    derived += c['amount']
                residual, ambiguous = 0, []
            confirmed = [c['id'] for c in parts if c['basis'] == 'projected_debit_confirmed_by_turn_difference']
            resource_rows.append(dict(field=field, before=left, after=right, observed_change=observed,
                direct_change=direct, derived_or_summary_change=derived,
                unresolved_change=residual, contribution_refs=[c['id'] for c in parts],
                ambiguous_contributions=ambiguous,
                **({'projected_debits_confirmed_by_turn_difference': confirmed} if confirmed else {}),
                status='missing_endpoint' if observed is None else 'unresolved_attribution' if ambiguous else
                       'unexplained_change' if residual else 'balanced_with_derived_changes' if any(c['basis'] not in _OBSERVED_BASES for c in parts) else 'balanced_observations'))
        return resource_rows

    def checkpoint_comparisons():
        out = []
        assigned.clear()
        for channel, fields in CHANNELS.items():
            key = 'checkpoints' if channel == 'stats' else 'performance_accounting/checkpoints'
            checkpoints = data['checkpoints'] if channel == 'stats' else data['performance_accounting']['checkpoints']
            if len(checkpoints) < 2 and not any(i.get('kind') == 'insufficient_observed_endpoints' and i.get('channel') == channel for i in issues):
                issues.append(dict(kind='insufficient_observed_endpoints', channel=channel, checkpoint_count=len(checkpoints)))
            for i, (before, after) in enumerate(zip(checkpoints, checkpoints[1:])):
                start, end = before['last_seen_ms'], after['first_seen_ms']
                if not start < end:
                    raise ValueError('Observed checkpoint intervals must be ordered and nonoverlapping')
                resource_rows = compare_fields(channel,before,after,start,end,assign=True)
                out.append(dict(id=f'{channel}-{i:04d}', channel=channel, start_ms=start, end_ms=end,
                    before_state_ref=f'/gameplay_tracking/{key}/{i}', after_state_ref=f'/gameplay_tracking/{key}/{i+1}',
                    fields=resource_rows, complete_event_history=False, independent_effect_verification=False))
        return out
    comparisons = checkpoint_comparisons()
    def resolve(ref):
        value = report
        for token in ref.lstrip('/').split('/'):
            token = token.replace('~1','/').replace('~0','~')
            value = value[int(token)] if isinstance(value,list) else value[token]
        return value

    def observed_state(state):
        if state is None:
            return None
        summary = state['values']
        sources = state.get('field_sources')
        if isinstance(sources, dict) and sources:
            # A closing read off the career's ending screens carries each
            # field from the frame that showed it; every field is bound to
            # its own frame, and the state carries only the fields it read.
            if (not isinstance(summary, dict) or set(summary) != set(sources)
                    or any(resolve(sources[field]['values_ref']) != amount for field, amount in summary.items())):
                raise ValueError(
                    'Turn state summary disagrees with its field sources: '
                    f"{state['values_ref']} at {state.get('observed_at_ms')} ms; ledger {summary!r}")
            return dict(values=dict(summary),observed_at_ms=state['observed_at_ms'],values_ref=state['values_ref'],
                        field_times={field: source['observed_at_ms'] for field, source in sources.items()
                                     if type(source.get('observed_at_ms')) is int})
        value = resolve(state['values_ref'])
        # A promoted opening endpoint keeps only the fields that were read as
        # integers on its source row, while the row itself may still carry
        # an unreadable field as ``None``.  The summary is source-bound when
        # every value it states equals the referenced row's value; it must not
        # claim a field the row does not carry.
        if (not isinstance(value, dict) or not isinstance(summary, dict)
                or any(field not in value or value[field] != amount for field, amount in summary.items())):
            raise ValueError(
                'Turn state summary disagrees with its source reference: '
                f"{state['values_ref']} at {state.get('observed_at_ms')} ms; "
                f"ledger {summary!r} vs report {value!r}")
        return dict(values=value,observed_at_ms=state['observed_at_ms'],values_ref=state['values_ref'])

    turns = report.get('turn_ledger',{}).get('turns',[])
    # The turn on which each channel was first observed. The performance
    # panel is not on the hub before the debut, and the career's last turn
    # has no next turn: neither is a gap in the reading, and each gets a
    # status of its own rather than a missing endpoint.
    first_shown = {channel: next((index for index, turn in enumerate(turns)
                                  if observed_state(turn['states'][channel].get('opening')) is not None), None)
                   for channel in CHANNELS}
    def turn_comparisons():
      turn_transitions = []
      for i, turn in enumerate(turns):
        following = turns[i+1] if i+1<len(turns) else None
        for channel, fields in CHANNELS.items():
            before = observed_state(turn['states'][channel].get('opening'))
            after = observed_state(following['states'][channel].get('opening') if following else
                                   turn['states'][channel].get('closing'))
            start = before['observed_at_ms'] if before else None
            end = after['observed_at_ms'] if after else None
            usable = start is not None and end is not None and start < end
            if usable:
                resource_rows = compare_fields(channel,before,after,start,end)
                if following is None:
                    # The closing screens showed some fields and not others:
                    # a field they never showed ends with the career, it is
                    # not an endpoint a later screen could still supply.
                    for row in resource_rows:
                        if row['status'] == 'missing_endpoint' and row['after'] is None:
                            row['status'] = 'career_end'
            else:
                if before is None and (first_shown[channel] is None or i < first_shown[channel]):
                    gap = 'not_yet_shown'
                elif after is None and following is None:
                    gap = 'career_end'
                else:
                    gap = 'missing_endpoint' if before is None or after is None else 'unordered_endpoints'
                resource_rows = [dict(field=field,before=before['values'].get(field) if before else None,
                    after=after['values'].get(field) if after else None,observed_change=None,direct_change=None,
                    derived_or_summary_change=None,unresolved_change=None,contribution_refs=[],ambiguous_contributions=[],
                    status=gap) for field in fields]
            cause_refs = [f'/gameplay_tracking/events/{index}' for index,event in enumerate(data['events'])
                          if usable and event['last_seen_ms']>start and event['first_seen_ms']<=end]
            # Applicability is separate from the historical arithmetic status.
            # A final observation is not a next-turn endpoint or proof that
            # purchases and other changes later in the recording were seen.
            endpoint_availability = []
            for field in fields:
                endpoint_availability.append(dict(field=field,
                    opening='observed' if before and type(before['values'].get(field)) is int else 'not_observed',
                    next_turn=('no_next_turn' if following is None else
                               'observed' if after and type(after['values'].get(field)) is int else 'not_observed'),
                    terminal_observation=('not_applicable' if following else
                                          'observed' if after and type(after['values'].get(field)) is int else 'not_observed')))
            terminal_observation = None
            if following is None and after is not None:
                terminal_observation = dict(values_ref=after['values_ref'], observed_at_ms=end,
                    later_numeric_contribution_refs=[c['id'] for c in contributions
                        if c['channel']==channel and c['observation_end_ms']>end],
                    run_completion_verified=False)
            turn_transitions.append(dict(turn_id=turn['id'],next_turn_id=following['id'] if following else None,
                channel=channel,start_ms=start,end_ms=end,before_state_ref=before['values_ref'] if before else None,
                after_state_ref=after['values_ref'] if after else None,fields=resource_rows,cause_refs=cause_refs,
                transition_kind='between_turn_openings' if following else 'terminal_observation_window',
                endpoint_availability=endpoint_availability, terminal_observation=terminal_observation,
                endpoint_basis='observed_turn_openings' if following else 'last_observed_closing_state',
                accounting_role='comparison_view_only_not_additional_changes',complete_event_history=False,
                exact_award_times_verified=False,independent_effect_verification=False))
      # A field no screen showed for a stretch of turns still has a value
      # read before the stretch and one read after it. Those two, and every
      # change counted between them, add up or do not; each turn inside the
      # stretch is marked by that sum rather than left as a missing endpoint.
      per_channel = {}
      for index, transition in enumerate(turn_transitions):
          per_channel.setdefault(transition['channel'], []).append(index)
      for channel, fields in CHANNELS.items():
          indexes = per_channel.get(channel, [])
          for field in fields:
              def field_row(k):
                  return next(f for f in turn_transitions[indexes[k]]['fields'] if f['field'] == field)
              k = 0
              while k < len(indexes):
                  if field_row(k)['status'] != 'missing_endpoint' or type(field_row(k).get('before')) is not int:
                      k += 1
                      continue
                  last = k
                  while (type(field_row(last).get('after')) is not int and last + 1 < len(indexes)
                         and field_row(last + 1)['status'] == 'missing_endpoint'):
                      last += 1
                  if type(field_row(last).get('after')) is not int:
                      k = last + 1
                      continue
                  before_state = observed_state(turns[k]['states'][channel].get('opening'))
                  after_state = observed_state(turns[last + 1]['states'][channel].get('opening') if last + 1 < len(turns)
                                               else turns[last]['states'][channel].get('closing'))
                  start = before_state['observed_at_ms'] if before_state else None
                  end = after_state['observed_at_ms'] if after_state else None
                  if start is None or end is None or not start < end:
                      k = last + 1
                      continue
                  span = next(f for f in compare_fields(channel, before_state, after_state, start, end) if f['field'] == field)
                  status = ('balanced_across_unread_stretch' if span['status'] in ('balanced_observations', 'balanced_with_derived_changes')
                            else 'unexplained_across_unread_stretch' if span['status'] == 'unexplained_change' else None)
                  if status is not None:
                      stretch = dict(start_ms=start, end_ms=end, turn_ids=[turn_transitions[indexes[j]]['turn_id'] for j in range(k, last + 1)],
                                     **{key: span.get(key) for key in ('before', 'after', 'observed_change', 'direct_change',
                                                                        'derived_or_summary_change', 'unresolved_change',
                                                                        'contribution_refs', 'ambiguous_contributions')})
                      for j in range(k, last + 1):
                          field_row(j).update(status=status, stretch=deepcopy(stretch))
                  k = last + 1
      return turn_transitions
    turn_transitions = turn_comparisons()

    # The one explicit extrapolation: a turn difference goes to its only
    # possible owner. Inside one turn window a field's remaining difference
    # can belong to the turn's sole training whose gain for that field was not
    # read, or to the one outcome whose receipt named the field but lost its
    # number (cut, covered, or misread), and a negative performance
    # difference to the one lesson bought in the window whose cost was not
    # observed. Two possible owners, an ambiguous contribution, or a caption
    # whose remaining digits contradict the difference leave it open with its
    # window. Each assignment is recorded on the owner and as a derived
    # contribution with its own basis, never as a receipt, so every consumer
    # shows it as worked out from the difference and a viewer can replace it.
    actions_by_turn = defaultdict(list)
    for entry in report.get('turn_ledger', {}).get('timeline', []):
        if isinstance(entry, dict) and entry.get('kind') == 'committed_action' and entry.get('turn_id'):
            actions_by_turn[entry['turn_id']].append(entry)
    lessons = data.get('lesson_purchases') or []
    extrapolated = 0
    for transition in turn_transitions:
        start, end = transition.get('start_ms'), transition.get('end_ms')
        if start is None or end is None:
            continue
        actions = actions_by_turn.get(transition['turn_id'], [])
        decisions = [a for a in actions if a.get('action_kind') != 'race']
        trainings = [a for a in decisions if a.get('action_kind') == 'training']
        training_event = training_parent = None
        if len(decisions) == 1 and trainings:
            # The ledger links a committed training to its event, including one
            # committed from its result card alone, which has no action receipt.
            training_parent = trainings[0].get('event_ref')
            if training_parent in event_refs.values():
                training_event = data['events'][int(training_parent.rsplit('/', 1)[1])]
        elif not decisions:
            # A training whose identity was never read is not a committed
            # decision, but it is still the turn's only training.
            seen = [(i, e) for i, e in enumerate(data['events']) if e.get('kind') == 'training'
                    and e['last_seen_ms'] > start and e['first_seen_ms'] <= end]
            if len(seen) == 1:
                trainings = [seen[0][1]]
                training_parent, training_event = f'/gameplay_tracking/events/{seen[0][0]}', seen[0][1]
        channel = transition['channel']
        read_key = 'deltas' if channel == 'stats' else 'performance_deltas'
        store = 'turn_difference_gains' if channel == 'stats' else 'turn_difference_performance_gains'
        race_in_turn = any(a.get('action_kind') == 'race' for a in actions)
        open_lessons = [(i, l) for i, l in enumerate(lessons) if isinstance(l, dict) and l.get('performance_cost') is None
                        and type(l.get('source_timestamp_ms')) is int and start < l['source_timestamp_ms'] <= end]
        open_skill_batches = [(i, e) for i, e in enumerate(data['events'])
                              if e.get('kind') == 'skill_purchase_batch'
                              and type((e.get('deltas') or {}).get('skill_points')) is not int
                              and e['last_seen_ms'] > start and e['first_seen_ms'] <= end]
        for row in transition['fields']:
            field, residual = row['field'], row.get('unresolved_change')
            if row.get('status') != 'unexplained_change' or type(residual) is not int or residual == 0:
                continue
            if row.get('ambiguous_contributions'):
                continue
            possible = []
            if race_in_turn and field == 'skill_points' and residual > 0:
                # Race rewards are not itemised on screen; the turn's one race is
                # the only possible owner of a positive skill-point difference
                # when no training or cut receipt could also have paid it.  A
                # race never charges skill points, so a negative difference on
                # the same turn is left to the owners considered below.
                races_here = [a for a in actions if a.get('action_kind') == 'race']
                receipt = _resolve_pointer(report, races_here[0].get('source_ref')) if len(races_here) == 1 else None
                race_index = next((i for i, r in enumerate(data.get('races') or [])
                                   if isinstance(receipt, dict) and r.get('id') == receipt.get('race_id')), None)
                # A training whose panel read its skill points (and cannot be a
                # clipped badge of the total) leaves the race as the only owner.
                # A training card on which the learned reader saw exactly this many
                # skill points is an owner the race cannot outrank.
                learned_training = (training_event is not None and len(trainings) == 1 and bool(
                    _learned_gain_frames(training_event, data['readings'], field, residual,
                                         _value_after_training(row, contributions, training_event, residual))))
                training_could = (bool(trainings) and (training_event is None or learned_training
                                  or _training_can_own(training_event, read_key, field)
                                  or _clipped_badge_mode(training_event, data['readings'], read_key, field, residual)))
                if residual > 0 and race_index is not None and not training_could and not _unparsed_mentions(
                        data.get('unparsed_receipt_candidates'), field, start, end):
                    race = data['races'][race_index]
                    parent = f'/gameplay_tracking/races/{race_index}'
                    race.setdefault('turn_difference_gains', {})[field] = residual
                    race['turn_difference_basis'] = 'sole_race_takes_skill_point_residual'
                    add(f'{parent}/turn_difference_gains/{field}', parent, race, channel, field, residual,
                        race.get('evidence'), 'turn_difference')
                    extrapolated += 1
                if not learned_training:
                    continue
            # The sole training takes a positive difference when its result was
            # not read at all or its reading of the field conflicted. A result
            # panel read without a row for the field did not raise it.
            if residual > 0 and trainings:
                if training_event is None:
                    possible.append(('unresolved', None, None))
                elif _training_can_own(training_event, read_key, field):
                    possible.append(('training', training_parent, training_event))
                else:
                    mode = _clipped_badge_mode(training_event, data['readings'], read_key, field, residual)
                    panel_read = (training_event.get(read_key) or {}).get(field)
                    if (mode is None and channel == 'stats' and (panel_read is None or _clipped_start(panel_read, residual))
                            and _learned_gain_frames(training_event, data['readings'], field, residual,
                                                     _value_after_training(row, contributions, training_event, residual))):
                        # The recognizer read the panel without this stat, or
                        # read only the start of its badge, but the learned
                        # reader saw exactly the difference as its gain on the
                        # card, or the value it lands on: the training owns it.
                        mode = 'learned_gain'
                    if (mode is None and channel == 'stats' and type(panel_read) is int and panel_read > 0 and residual > 0
                            and len(_learned_gain_frames(training_event, data['readings'], field, residual + panel_read,
                                                         _value_after_training(row, contributions, training_event, residual + panel_read))) >= 2):
                        # The recognizer read one number off the badge; the
                        # learned reader read the card at another on two
                        # frames or more (the gain, or the value the stat
                        # lands on), and the bars agree with the card: the
                        # card decides, and the panel's read stays beside it.
                        mode = 'card_read_outranks_panel'
                    if mode:
                        possible.append(('training', training_parent, training_event, mode))
            # The one outcome whose receipt named the field but lost its number.
            captions = _unparsed_mentions(data.get('unparsed_receipt_candidates'), field, start, end)
            caption_events = {}
            for candidate in captions:
                owner = _caption_owner(data, candidate, field, channel, event_refs)
                if owner == 'represented':
                    continue
                if owner is None:
                    possible.append(('unresolved', None, None))
                    continue
                caption_events.setdefault(owner, []).append(candidate)
            for index, found in caption_events.items():
                possible.append(('outcome', f'/gameplay_tracking/events/{index}', data['events'][index], found))
            # The one lesson bought in the window whose cost was not observed
            # takes a negative performance difference.
            if residual < 0 and channel == 'performance' and open_lessons:
                for index, lesson in open_lessons:
                    possible.append(('lesson', f'/gameplay_tracking/lesson_purchases/{index}', lesson))
            # The one skill batch committed in the window whose charge was not
            # observed takes a negative skill-point difference.
            if residual < 0 and channel == 'stats' and field == 'skill_points' and open_skill_batches:
                for index, batch in open_skill_batches:
                    possible.append(('skill_batch', f'/gameplay_tracking/events/{index}', batch))
            # Two or more unpriced batches in one window can still be priced
            # when their carts' net costs add up to exactly the turn's drop:
            # each cart counter is an independent reading of its own charge,
            # and together they account for the difference.
            if (residual < 0 and channel == 'stats' and field == 'skill_points' and len(open_skill_batches) >= 2
                    and all(kind == 'skill_batch' for kind, *_ in possible)
                    and all(type(batch.get('cart_net_cost')) is int and batch['cart_net_cost'] > 0
                            for _, batch in open_skill_batches)
                    and sum(batch['cart_net_cost'] for _, batch in open_skill_batches) == -residual):
                for index, batch in open_skill_batches:
                    parent = f'/gameplay_tracking/events/{index}'
                    charge = batch['cart_net_cost']
                    batch.setdefault('turn_difference_cost', {})[field] = charge
                    batch['turn_difference_basis'] = 'unpriced_skill_batches_cart_nets_sum_to_turn_residual'
                    add(f'{parent}/turn_difference_cost/{field}', parent, batch, channel, field, -charge,
                        batch.get('evidence'), 'turn_difference')
                    _complete_list_from_cart(batch, charge, data)
                extrapolated += 1
                continue
            if len(possible) != 1 or possible[0][0] == 'unresolved':
                continue
            kind, parent, owner = possible[0][:3]
            if kind == 'training':
                mode = possible[0][3] if len(possible[0]) > 3 else None
                read = (owner.get(read_key) or {}).get(field)
                # When the learned reader read the whole gain on the card (the
                # difference, plus any leading digits the recognizer read), or
                # the value the stat lands on with it, the amount is observed,
                # not worked out.
                gain = residual + (read if mode in ('clipped_badge_prefix', 'card_read_outranks_panel') else 0)
                value_after = _value_after_training(row, contributions, owner, gain)
                learned = (_learned_gain_frames(owner, data['readings'], field, gain, value_after)
                           if channel == 'stats' else [])
                if learned:
                    amount = residual
                    disputed = None
                    if mode == 'card_read_outranks_panel':
                        # The panel's number and the card's are the disputed
                        # reads the card settled; the reader adds the rest.
                        # They are recorded on the settlement, not written
                        # into the event's conflict list, so a rebuild from
                        # the report sees the same event this pass saw.
                        disputed = [read, gain]
                        owner.setdefault('learned_reader_completions', {})[field] = dict(read=read, total=gain, card_outranks_panel_read=True)
                    elif mode != 'clipped_badge_prefix' and _clipped_start(read, gain):
                        # The card showed the whole gain; the digits the
                        # recognizer read are its clipped start and stay
                        # counted, so the reader adds only the rest. What the
                        # turn's difference still leaves stays open.
                        amount = gain - read
                        owner.setdefault('learned_reader_completions', {})[field] = dict(read=read, total=gain)
                    owner.setdefault('learned_reader_gains', {})[field] = amount
                    owner.setdefault('learned_reader_frames', {})[field] = learned
                    if not _learned_gain_frames(owner, data['readings'], field, gain):
                        owner.setdefault('learned_reader_values', {})[field] = value_after
                    add(f'{parent}/learned_reader_gains/{field}', parent, owner, channel, field, amount, learned,
                        'observed_learned_training_gain')
                    if channel == 'stats':
                        _settle_conflicting_reads(owner, field, gain, 'learned_reader_on_card', reads=disputed)
                else:
                    owner.setdefault(store, {})[field] = residual
                    owner['turn_difference_basis'] = ('sole_training_takes_turn_residual' if mode is None else
                                                      f'sole_training_{mode}_completed_by_turn_residual')
                    if mode:
                        owner.setdefault('turn_difference_completions', {})[field] = dict(
                            mode=mode, read=read, completed=(read or 0) + residual)
                    # The amount stands, because the stat bars decide it, but a
                    # card that shows another gain is worth a reviewer's eye.
                    contradicted = (_learned_gain_contradictions(owner, data['readings'], field, gain)
                                    if channel == 'stats' else [])
                    if contradicted:
                        owner.setdefault('contradicted_turn_difference', {})[field] = dict(
                            worked_out=gain, reads=contradicted)
                        issues.append(dict(kind='worked_out_amount_contradicted_by_card',
                                           source_ref=f'{parent}/{store}/{field}', channel=channel, field=field,
                                           worked_out=gain, reads=contradicted))
                    add(f'{parent}/{store}/{field}', parent, owner, channel, field, residual, owner.get('evidence'),
                        'turn_difference')
                    if contradicted:
                        # On the contribution too, so a reviewer sees the card's
                        # own number beside the one worked out for it.
                        contributions[-1]['contradicted_reads'] = contradicted
                    else:
                        _settle_conflicting_reads(owner, field, gain, 'turn_difference')
                if mode == 'clipped_badge_prefix' or field in (owner.get('learned_reader_completions') or {}):
                    # The read digits stay as observed; the completion is a
                    # second, flagged contribution on the same field, not a claim
                    # competing with it.
                    observed = next((c for c in contributions if c['event_ref'] == parent and c['channel'] == channel
                                     and c['field'] == field and c['basis'] not in ('turn_difference', 'observed_learned_training_gain')), None)
                    if observed is not None:
                        contributions[-1]['completes'] = observed['id']
                        observed['completed_by'] = contributions[-1]['id']
            elif kind == 'outcome':
                found = possible[0][3]
                shown = {_caption_digits(c.get('raw_text')) for c in found}
                if any(d and not str(abs(residual)).startswith(d) for d in shown):
                    continue
                directions = {_caption_direction(field, c.get('raw_text')) for c in found}
                if directions != {'up' if residual > 0 else 'down'}:
                    continue
                owner.setdefault(store, {})[field] = residual
                owner['turn_difference_basis'] = 'sole_number_cut_receipt_takes_turn_residual'
                owner.setdefault('turn_difference_captions', {})[field] = [
                    dict(raw_text=c.get('raw_text'), first_seen_ms=c.get('first_seen_ms'),
                         evidence=list(c.get('evidence') or [])[:1]) for c in found]
                for c in found:
                    # The cut line's number is now known from the turn's
                    # difference; the line is a note, not a review item.
                    c['worked_out'] = dict(field=field, amount=residual, event_ref=parent)
                proof = [owner.get('evidence'), *(e for c in found for e in (c.get('evidence') or []))]
                add(f'{parent}/{store}/{field}', parent, owner, channel, field, residual, [e for e in proof if e],
                    'turn_difference')
            elif kind == 'skill_batch':
                owner.setdefault('turn_difference_cost', {})[field] = -residual
                owner['turn_difference_basis'] = 'sole_unpriced_skill_batch_takes_turn_residual'
                add(f'{parent}/turn_difference_cost/{field}', parent, owner, channel, field, residual,
                    owner.get('evidence'), 'turn_difference')
                _complete_list_from_cart(owner, -residual, data)
            else:
                owner.setdefault('turn_difference_cost', {})[field] = -residual
                owner['turn_difference_basis'] = 'sole_unpriced_lesson_takes_turn_residual'
                add(f'{parent}/turn_difference_cost/{field}', parent, owner, 'performance', field, residual,
                    owner.get('evidence'), 'turn_difference')
            extrapolated += 1
    # A turn with no next opening cannot settle a disputed badge by the stat
    # bars. The card itself can: when the learned reader read one of the
    # disputed gains on two of its frames, and read the value the stat lands
    # on with that gain as the card's last value, that gain is the badge's
    # number.
    for transition in turn_transitions:
        if transition['channel'] != 'stats':
            continue
        trainings = [a for a in actions_by_turn.get(transition['turn_id'], []) if a.get('action_kind') == 'training']
        if len(trainings) != 1 or trainings[0].get('event_ref') not in event_refs.values():
            continue
        parent = trainings[0]['event_ref']
        event = data['events'][int(parent.rsplit('/', 1)[1])]
        reads = event.get('conflicting_readings')
        if not isinstance(reads, dict):
            continue
        for row in transition['fields']:
            field = row['field']
            if row.get('status') not in ('missing_endpoint', 'career_end') or not isinstance(reads.get(field), list):
                continue
            if any(c['event_ref'] == parent and c['channel'] == 'stats' and c['field'] == field for c in contributions):
                continue
            landing = []
            for gain in sorted({g for g in reads[field] if type(g) is int and g > 0}):
                value_after = _value_after_training(row, contributions, event, gain)
                if value_after is None:
                    continue
                gain_frames = _learned_gain_frames(event, data['readings'], field, gain)
                value_frames = [f for f in _learned_gain_frames(event, data['readings'], field, gain, value_after)
                                if f not in gain_frames]
                if len(gain_frames) >= 2 and value_frames:
                    landing.append((gain, gain_frames + value_frames, value_after))
            if len(landing) != 1:
                continue
            gain, frames, value_after = landing[0]
            event.setdefault('learned_reader_gains', {})[field] = gain
            event.setdefault('learned_reader_frames', {})[field] = frames
            event.setdefault('learned_reader_values', {})[field] = value_after
            add(f'{parent}/learned_reader_gains/{field}', parent, event, 'stats', field, gain, frames,
                'observed_learned_training_gain')
            _settle_conflicting_reads(event, field, gain, 'learned_reader_landing_value_on_card')
            extrapolated += 1
    if extrapolated:
        by_id.update({c['id']: c for c in contributions})
        comparisons = checkpoint_comparisons()
        turn_transitions = turn_comparisons()
    counts = Counter(f['status'] for interval in comparisons for f in interval['fields'])
    turn_counts = Counter(f['status'] for interval in turn_transitions for f in interval['fields'])
    from .terminal_observations import build as build_terminal_observations
    return dict(schema_version='tracen-replay/causal-accounting-v1', source_sha256=report['source']['sha256'],
        contributions=contributions, comparisons=comparisons,turn_transitions=turn_transitions,
        terminal_observations=build_terminal_observations(report, contributions),
        other_effects=context, issues=issues,
        unassigned_contribution_refs=[c['id'] for c in contributions if c['id'] not in assigned],
        summary=dict(contributions=len(contributions), comparisons=len(comparisons), field_status_counts=dict(counts),
                     turn_comparisons=len(turn_transitions),turn_field_status_counts=dict(turn_counts)),
        limitations=['Arithmetic closure does not verify every individual effect or exclude offsetting recognition errors.',
                     'State-derived and projected changes are not independent receipt evidence.',
                     'A turn_difference is the turn difference assigned to its only possible owner (the sole training, the one receipt that lost its number, the one lesson without an observed cost, or the one skill batch whose charge was never read); it is flagged, not observed, and a viewer may replace it.',
                     'Observation windows constrain attribution; they do not establish the exact award time.',
                     'Missing opening or final checkpoints leave recording coverage incomplete.',
                     'Turn and checkpoint comparisons are overlapping views; sum canonical contributions only once.',
                     'Other effects, including energy, hints and conditions, retain source references and are not summed as stats.'])
