"""Training gains confirmed from the committed card's preview and the totals.

A player who skips the result animation can leave no readable gain badge at
all: the base pass sees the preview of the selected card, then the settled
result panel (or the next home panel) with the new totals.  The preview is a
prediction, not an observation of the applied result, so it is accepted as
the gain only under three checks:

1. the preview amounts of the committed option were read identically on at
   least two frames immediately before the result;
2. the result carried no failure marker;
3. every total that is readable both before and after the training changed
   by exactly the preview amount (plus any receipt recorded in between), and
   at least one previewed field is confirmed that way (tier 1); or every
   previewed field rose by at least its preview while every other readable
   field balanced exactly (tier 2, a scenario bonus the preview does not
   show), in which case the applied amounts come from the two panels.

Fields hidden by the cursor may stay unreadable, but no readable field may
contradict the preview.  The same rule covers the performance-point panel,
whose menu rows show ``current+projected`` for the awards of the selected
card.  The accounting records such gains as state-derived, distinct from
badge-observed gains.  Totals never become gain amounts on their own.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right

from .layout import place_x, place_y
from .source_clock import elapsed
from .training_gain_phases import direct_gain_proof

FIELDS = ('speed', 'stamina', 'power', 'guts', 'wit', 'skill_points')
PERFORMANCE_FIELDS = ('dance', 'passion', 'vocal', 'visual', 'composure')
VERSION = 2
BASIS = 'preview_confirmed_by_result_totals'
BOUNDED_BASIS = 'preview_bounded_state_derived'
# Preview badge columns of the training menu, in PC pane coordinates; the
# badges are pinned to the bottom centre, the panel below to the top left.
_PREVIEW_COLUMNS = {'speed': (250, 400), 'stamina': (360, 490), 'power': (455, 590),
                    'guts': (550, 690), 'wit': (645, 765), 'skill_points': (735, 860)}
_PREVIEW_ROW = (650, 720)
# Performance panel rows (``current+projected`` text) on the training menu.
_PANEL_BANDS = {'dance': (290, 335), 'passion': (346, 391), 'vocal': (402, 447),
                'visual': (458, 503), 'composure': (514, 559)}
_PANEL_COLUMNS = (190, 340)
_SIGNED = re.compile(r'\+(\d{1,3})')
_PANEL_PROJECTED = re.compile(r'(\d{1,3})\+(\d{1,3})')
PREVIEW_LOOKBACK_MS = 10000
PREVIEW_GAP_MS = 1500
SNAPSHOT_LOOKBACK_MS = 25000
SNAPSHOT_LOOKAHEAD_MS = 25000
# A committed training's result card follows its preview run after the
# training animation, which took 5.25 s on a blind recording; the next
# turn's result can only follow a home panel.  A result inside this window
# with no full panel between it and the run is this run's own commit (or
# the commit of another card browsed after it).
RESULT_LOOKAHEAD_MS = 15000
# Preview rows this soon after an existing training window still belong to
# it (the menu shown again while the result settles).
EVENT_ADJACENCY_MS = 5000

CHANNELS = {
    'stats': dict(fields=FIELDS, event_key='deltas', evidence_key='field_evidence'),
    'performance': dict(fields=PERFORMANCE_FIELDS, event_key='performance_deltas', evidence_key='performance_evidence'),
}


def _facts(row):
    facts = row.get('facts') if isinstance(row, dict) else None
    return facts if isinstance(facts, dict) else {}


def _time(row):
    time = row.get('source_timestamp_ms') if isinstance(row, dict) else None
    return time if type(time) is int else None


def _lines(row):
    ocr = row.get('ocr') if isinstance(row.get('ocr'), dict) else {}
    for key in ('lines', 'neural'):
        if isinstance(ocr.get(key), list):
            return ocr[key]
    return row.get('lines') if isinstance(row.get('lines'), list) else []


def _boxed(line, minimum_confidence=90):
    if not isinstance(line, dict) or line.get('confidence', 0) < minimum_confidence:
        return None
    box = line.get('box')
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    return box


def preview_amounts(row):
    """Signed stat preview amounts read on one training-menu row, by field."""
    facts = _facts(row)
    amounts = {}
    recovery = facts.get('preview_recovery')
    if isinstance(recovery, dict):
        for effect in recovery.get('effects') or ():
            if (isinstance(effect, dict) and effect.get('kind') == 'stat_change'
                    and effect.get('field') in FIELDS and type(effect.get('amount')) is int
                    and effect.get('phase') == 'preview'):
                amounts[effect['field']] = effect['amount']
    for line in _lines(row):
        box = _boxed(line)
        if box is None:
            continue
        text = re.sub(r'\s+', '', str(line.get('text', '')))
        match = _SIGNED.fullmatch(text)
        cy = (box[1] + box[3]) / 2
        if not match or not place_y(_PREVIEW_ROW[0], 'b') <= cy <= place_y(_PREVIEW_ROW[1], 'b'):
            continue
        cx = (box[0] + box[2]) / 2
        owners = [f for f, (left, right) in _PREVIEW_COLUMNS.items() if place_x(left) <= cx <= place_x(right)]
        if len(owners) == 1 and owners[0] not in amounts:
            amounts[owners[0]] = int(match[1])
    return amounts


def preview_performance_amounts(row):
    """Projected performance awards (``current+projected``) on one menu row."""
    facts = _facts(row)
    amounts = {}
    projected = facts.get('projected_performance_gains')
    if isinstance(projected, dict):
        for field, amount in projected.items():
            if field in PERFORMANCE_FIELDS and type(amount) is int:
                amounts[field] = amount
    for line in _lines(row):
        box = _boxed(line)
        if box is None:
            continue
        text = re.sub(r'\s+', '', str(line.get('text', '')))
        match = _PANEL_PROJECTED.fullmatch(text)
        if not match:
            continue
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        if not _PANEL_COLUMNS[0] <= cx <= _PANEL_COLUMNS[1]:
            continue
        owners = [f for f, (top, bottom) in _PANEL_BANDS.items() if place_y(top, 't') <= cy <= place_y(bottom, 't')]
        if len(owners) == 1 and owners[0] not in amounts:
            amounts[owners[0]] = int(match[2])
    return amounts


_AMOUNTS = {'stats': preview_amounts, 'performance': preview_performance_amounts}


def _committed_preview_rows(readings, group):
    """Contiguous rows previewing the committed option right before the result."""
    if group.get('preview_rows'):
        return list(group['preview_rows'])
    option = group.get('option')
    first = group.get('first_seen_ms')
    if not isinstance(option, str) or type(first) is not int:
        return []
    candidates = [r for r in readings
                  if (t := _time(r)) is not None and first - PREVIEW_LOOKBACK_MS <= t < first
                  and _facts(r).get('preview_option') == option
                  and r.get('screen') != 'training_result']
    candidates.sort(key=_time)
    rows = []
    for row in reversed(candidates):
        if rows and elapsed(_time(row), _time(rows[-1])) > PREVIEW_GAP_MS:
            break
        rows.append(row)
    return list(reversed(rows))


def _previewed_fields(rows, channel):
    fields = set()
    for row in rows:
        fields.update(_AMOUNTS[channel](row))
    return fields


def _consensus_preview(rows, channel='stats', minimum_frames=2):
    """Amounts seen identically on enough distinct frames; conflicts drop the field."""
    seen = {}
    for row in rows:
        for field, amount in _AMOUNTS[channel](row).items():
            seen.setdefault(field, {}).setdefault(amount, set()).add(_time(row))
    result = {}
    for field, values in seen.items():
        if len(values) != 1:
            continue
        amount, times = next(iter(values.items()))
        if len(times) >= minimum_frames:
            result[field] = amount
    return result


def _full_snapshot(row, channel='stats'):
    if channel == 'performance':
        values = _facts(row).get('performance_points')
        fields = PERFORMANCE_FIELDS
    else:
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        values = stats.get('values')
        fields = FIELDS
    if isinstance(values, dict) and all(type(values.get(f)) is int for f in fields):
        return {f: values[f] for f in fields}
    return None


def _stable_result_totals(rows, before, channel='stats'):
    """Per-field result totals seen identically on at least two result rows.

    Result frames before the applied panel still show the prior total, so
    that value is allowed beside the new one.  Any third distinct value (a
    rolling counter, a cursor-clipped digit) leaves the field unresolved.
    """
    from collections import Counter
    seen = {}
    for row in rows:
        facts = _facts(row)
        source = facts.get('performance_points') if channel == 'performance' else facts.get('result_values')
        if not isinstance(source, dict):
            continue
        for field, value in source.items():
            if field in CHANNELS[channel]['fields'] and type(value) is int and value >= 0:
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


def _intervening_effects(rows, channel='stats'):
    """Receipts recorded between two snapshots, assembled as events.

    Receipt frames repeat the same lines many times; the ordinary outcome
    event assembly already collapses them into one event per receipt, so the
    same assembly is reused here rather than a private de-duplication.
    """
    from .transactions import outcome_events
    fields = CHANNELS[channel]['fields']
    totals = {f: 0 for f in fields}
    for event in outcome_events(list(rows)):
        if not isinstance(event, dict):
            continue
        if channel == 'stats':
            for field, amount in (event.get('deltas') or {}).items():
                if field in fields and type(amount) is int:
                    totals[field] += amount
        else:
            for effect in event.get('effects') or ():
                if (isinstance(effect, dict) and effect.get('kind') == 'performance_change'
                        and effect.get('field') in fields and type(effect.get('amount')) is int):
                    totals[effect['field']] += effect['amount']
    return totals


def _after_last_result(snapshots, candidates, rows):
    """The snapshots taken after the last result card among ``candidates``.

    A difference measured across another training's result card is that
    training's, not this one's: the menu shown again after a card, with the
    same option previewed, must not read the card's gains a second time.
    """
    results = [_time(r) for r in candidates if r.get('screen') == 'training_result' and r not in rows]
    if not results:
        return list(snapshots)
    last = max(results)
    return [r for r in snapshots if _time(r) > last]


def result_total_gains(readings, group, deltas, channel='stats'):
    """Return ``{field: proof}`` for gains read as the result panel's totals minus the prior snapshot.

    The result panel shows the applied totals and the home bar (or a preview
    frame) showed them just before the training. With no receipt between the
    two, the difference is the training's effect. Only fields without a badge
    reading are supplied; a badge that disagrees with the totals means the
    panels do not belong together, and nothing is supplied. A failure result
    is left alone.
    """
    fields = CHANNELS[channel]['fields']
    rows = group.get('rows') or []
    result_rows = [r for r in rows if r.get('screen') == 'training_result']
    first = group.get('first_seen_ms')
    if not result_rows or type(first) is not int or any(_facts(r).get('training_outcome') == 'failure' for r in rows):
        return {}
    ordered = sorted((r for r in readings if _time(r) is not None), key=_time)
    times = [_time(r) for r in ordered]
    window = ordered[bisect_left(times, first - SNAPSHOT_LOOKBACK_MS):bisect_left(times, first)]
    before_rows = [r for r in window if r.get('screen') != 'training_result' and r not in rows and _full_snapshot(r, channel)]
    before_rows = _after_last_result(before_rows, window, rows)
    if not before_rows:
        return {}
    before_row = before_rows[-1]
    before = _full_snapshot(before_row, channel)
    # A field the frames after the last full panel read repeatedly as another
    # value was misread on that panel (or changed since): the repeated value,
    # from its last frame, is the field's prior total.
    anchors = {field: before_row for field in fields}
    later = _after_last_result([r for r in window if _time(r) > _time(before_row) and r not in rows
                                and r.get('screen') != 'training_result'], window, rows)
    for field in fields:
        reads = [(r, _field_value(r, field, channel)) for r in later]
        reads = [(r, value) for r, value in reads if type(value) is int]
        run = []
        for r, value in reversed(reads):
            if run and value != run[0][1]:
                break
            run.append((r, value))
        if len(run) >= 2 and run[0][1] != before[field]:
            before[field] = run[0][1]
            anchors[field] = run[0][0]
    after = _stable_result_totals(result_rows, before, channel)
    if not after:
        return {}
    proofs = {}
    for field in fields:
        if field not in after:
            continue
        anchor = anchors[field]
        between = [r for r in ordered[bisect_left(times, _time(anchor)):bisect_left(times, first)]
                   if _time(anchor) < _time(r) < first and r not in rows]
        receipt = _intervening_effects(between, channel).get(field, 0)
        gain = after[field] - before[field] - receipt
        if gain < 0 or (field in deltas and deltas[field] != gain):
            return {}
        if field in deltas or gain == 0:
            continue
        proofs[field] = dict(
            value=gain, basis='result_panel_totals_minus_prior_snapshot',
            before=dict(source_timestamp_ms=_time(anchor), evidence=anchor.get('evidence'), value=before[field]),
            after=dict(source_timestamp_ms=_time(result_rows[0]), value=after[field],
                       evidence=[r.get('evidence') for r in result_rows]),
            intervening_receipts=receipt,
            evidence=[anchor.get('evidence')] + [r.get('evidence') for r in result_rows])
    return proofs


def _field_value(row, field, channel):
    """One field of a row's panel, read whether or not the whole panel was."""
    if channel == 'performance':
        values = _facts(row).get('performance_points')
    else:
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        values = stats.get('values')
    return values.get(field) if isinstance(values, dict) else None


def badge_contradictions(readings, group, deltas, channel='stats'):
    """Return ``{field: proof}`` for badge-read gains larger than the surrounding panels allow.

    The last full snapshot before the training and the first one after it,
    less the receipts between them, bound what the training can have given.
    A badge above that bound is a misread (a "+5" read as "+51"), not a gain:
    the caller records it as a conflict so the dense re-read can look again
    and the review queue shows it.
    """
    rows = group.get('rows') or []
    first, last = group.get('first_seen_ms'), group.get('last_seen_ms')
    if not deltas or type(first) is not int or type(last) is not int:
        return {}
    ordered = sorted((r for r in readings if _time(r) is not None), key=_time)
    times = [_time(r) for r in ordered]
    before_rows = [r for r in ordered[bisect_left(times, first - SNAPSHOT_LOOKBACK_MS):bisect_left(times, first)]
                   if r.get('screen') != 'training_result' and r not in rows and _full_snapshot(r, channel)]
    after_rows = [r for r in ordered[bisect_right(times, last):bisect_right(times, last + SNAPSHOT_LOOKAHEAD_MS)]
                  if r.get('screen') != 'training_result' and r not in rows and _full_snapshot(r, channel)]
    if not before_rows or not after_rows:
        return {}
    before_row, after_row = before_rows[-1], after_rows[0]
    before, after = _full_snapshot(before_row, channel), _full_snapshot(after_row, channel)
    between = [r for r in ordered[bisect_left(times, _time(before_row)):bisect_right(times, _time(after_row))]
               if _time(before_row) < _time(r) < _time(after_row) and r not in rows]
    receipts = _intervening_effects(between, channel)
    out = {}
    for field, amount in deltas.items():
        if field not in after or type(before.get(field)) is not int or type(amount) is not int:
            continue
        allowed = after[field] - before[field] - receipts.get(field, 0)
        # A stat never falls across a training: a bound below zero means the
        # panel after it was read wrong (a lost leading digit), and a wrong
        # panel contradicts no badge.
        if allowed < 0:
            continue
        if amount > allowed:
            out[field] = dict(badge=amount, allowed=allowed, before=dict(source_timestamp_ms=_time(before_row), value=before[field], evidence=before_row.get('evidence')),
                              after=dict(source_timestamp_ms=_time(after_row), value=after[field], evidence=after_row.get('evidence')),
                              intervening_receipts=receipts.get(field, 0), basis='badge_exceeds_surrounding_panels')
    return out


def _confirm(before, after, preview, receipts, previewed=(), observed_deltas=None, fields=FIELDS):
    """Compare each readable field with the preview plus intervening receipts.

    ``confirmed`` lists previewed fields that match exactly.  ``excess`` lists
    previewed fields whose total rose by more than the preview (a scenario
    bonus the preview does not show).  ``contradictions`` are fields that fell
    short, or fields the preview never touched that changed at all.
    """
    confirmed, excess, contradictions = [], {}, []
    previewed = set(previewed) | set(preview)
    observed_deltas = observed_deltas or {}
    for field in fields:
        if field not in after or type(before.get(field)) is not int:
            continue
        expected = preview.get(field, 0) + receipts[field]
        observed = after[field] - before[field]
        if field in observed_deltas:
            # Already carried by a badge: the panels must agree with it.
            if observed != observed_deltas[field] + receipts[field]:
                contradictions.append(dict(field=field, before=before[field], after=after[field], expected=observed_deltas[field] + receipts[field]))
            continue
        if observed == expected:
            if field in preview:
                confirmed.append(field)
        elif field in previewed and observed > expected:
            excess[field] = observed - receipts[field]
        else:
            contradictions.append(dict(field=field, before=before[field], after=after[field], expected=expected))
    return confirmed, excess, contradictions


def preview_confirmed_gains(readings, group, deltas, channel='stats'):
    """Return ``{field: proof}`` for previewed gains confirmed by the totals."""
    fields = CHANNELS[channel]['fields']
    rows = group.get('rows') or []
    if any(_facts(r).get('training_outcome') == 'failure' for r in rows):
        return {}
    preview_rows = _committed_preview_rows(readings, group)
    # A group with its own result rows already proves which card was
    # committed, so one preview frame suffices there; a preview-only group
    # must repeat the preview on two frames.
    minimum_frames = 1 if rows else 2
    if len(preview_rows) < minimum_frames:
        return {}
    preview = _consensus_preview(preview_rows, channel, minimum_frames)
    # An amount already observed on a badge must not fall below its preview;
    # a scenario bonus may lift it above.
    if not preview or any(field in deltas and deltas[field] < amount for field, amount in preview.items()):
        return {}
    first, last = group['first_seen_ms'], group['last_seen_ms']
    ordered = sorted((r for r in readings if _time(r) is not None), key=_time)
    times = [_time(r) for r in ordered]

    def rows_within(lo, hi):
        """The ordered rows a scan limited to ``lo <= time <= hi`` visits; each scan keeps its own bounds."""
        if type(lo) is not int or type(hi) is not int:
            return ordered
        return ordered[bisect_left(times, lo):bisect_right(times, hi)]
    before_rows = [r for r in rows_within(first - SNAPSHOT_LOOKBACK_MS, _time(preview_rows[0])) if first - SNAPSHOT_LOOKBACK_MS <= _time(r) < _time(preview_rows[0]) and _full_snapshot(r, channel)]
    before_rows = _after_last_result(before_rows, rows_within(first - SNAPSHOT_LOOKBACK_MS, _time(preview_rows[0])), rows)
    if not before_rows:
        return {}
    before_row = before_rows[-1]
    before = _full_snapshot(before_row, channel)
    # Result-panel totals first; when they confirm no previewed field (the
    # cards that changed were hidden), the next home panel after the result.
    attempts = []
    panel = _stable_result_totals([r for r in rows if r.get('screen') == 'training_result'], before, channel)
    if panel:
        attempts.append((panel, None, last))
    after_rows = [r for r in rows_within(last, last + SNAPSHOT_LOOKAHEAD_MS) if last < _time(r) <= last + SNAPSHOT_LOOKAHEAD_MS and _full_snapshot(r, channel)]
    if after_rows:
        attempts.append((_full_snapshot(after_rows[0], channel), after_rows[0], _time(after_rows[0])))
    previewed = _previewed_fields(preview_rows, channel)
    # Evaluate every available after-panel; any contradiction on any panel
    # rejects the preview.  Among confirming panels prefer the one that reads
    # the most previewed fields (the result panel can hide the clicked card
    # under the cursor while the next home panel shows it).
    chosen = None
    for after, after_row, between_end in attempts:
        between = [r for r in rows_within(_time(before_row), between_end) if _time(before_row) < _time(r) <= between_end and r not in rows]
        receipts = _intervening_effects(between, channel)
        confirmed, excess, contradictions = _confirm(before, after, preview, receipts, previewed, deltas, fields)
        if contradictions:
            return {}
        coverage = len([f for f in previewed if f in after])
        if (confirmed or excess) and (chosen is None or coverage > chosen[0]):
            chosen = (coverage, after, after_row, receipts, confirmed, excess)
    if chosen is None:
        return {}
    chosen = chosen[1:]
    after, after_row, receipts, confirmed, excess = chosen
    # Tier 1: every readable previewed field matched exactly, so the preview
    # amounts are the gains.  Tier 2: some previewed field rose beyond its
    # preview (a scenario bonus the preview does not show) while every other
    # readable field balanced; the applied amounts then come from the two
    # panels, bounded below by the preview, recorded as state-derived with
    # the preview and the excess kept in the proof.
    unreadable = [f for f in fields if f not in after]
    common = dict(version=VERSION, channel=channel, confirmed_fields=confirmed, unreadable_fields=unreadable,
                  before=dict(source_timestamp_ms=_time(before_row), evidence=before_row.get('evidence'), values=before),
                  after=dict(source_timestamp_ms=_time(after_row) if after_row else None, evidence=after_row.get('evidence') if after_row else None,
                             values=after, source='home_panel' if after_row else 'result_panel'),
                  intervening_receipts={f: n for f, n in receipts.items() if n})
    amounts_of = _AMOUNTS[channel]
    proofs = {}
    if not excess:
        for field, amount in preview.items():
            if field in deltas:
                continue
            proof = direct_gain_proof(amount, [(r, amount) for r in preview_rows if amounts_of(r).get(field) == amount], basis=BASIS)
            proof.update(common)
            proofs[field] = proof
        return proofs
    for field in sorted(previewed):
        if field in deltas or field in unreadable:
            # A previewed field hidden on both panels has no readable amount.
            continue
        amount = after[field] - before[field] - receipts[field]
        if amount < 0 or (field in preview and amount < preview[field]):
            return {}
        witnesses = [(r, amounts_of(r).get(field)) for r in preview_rows if field in amounts_of(r)]
        proof = direct_gain_proof(amount, witnesses, basis=BOUNDED_BASIS)
        proof.update(common, preview_amount=preview.get(field),
                     bonus_excess=(amount - preview[field]) if field in preview else None)
        proofs[field] = proof
    return proofs


def preview_only_training_groups(readings, events):
    """Preview runs that were committed without any result frame being sampled.

    A contiguous run of menu rows previewing one option, with no
    ``training_result`` row or training window claiming it before the next
    full panel, is a candidate committed training.  The group has
    no result rows; its gains must be confirmed against the next panel, and
    the group's own preview rows are its evidence window.
    """
    ordered = sorted((r for r in readings if _time(r) is not None), key=_time)
    windows = [(e.get('first_seen_ms'), e.get('last_seen_ms')) for e in events
               if isinstance(e, dict) and e.get('kind') == 'training'
               and type(e.get('first_seen_ms')) is int and type(e.get('last_seen_ms')) is int]
    result_times = [_time(r) for r in ordered if r.get('screen') == 'training_result']
    panel_times = [_time(r) for r in ordered if _full_snapshot(r) is not None]
    groups = []
    run = []

    def panel_between(lo, hi):
        index = bisect_right(panel_times, lo)
        return index < len(panel_times) and panel_times[index] < hi

    def claimed(start, end):
        """Whether a sampled result or training window is this run's own commit.

        A result before the next full panel belongs to this turn's action;
        once a home panel has been seen, a later result is the next turn's,
        and this run's own result was simply never sampled.
        """
        if any(end < t <= end + RESULT_LOOKAHEAD_MS and not panel_between(end, t) for t in result_times):
            return True
        for first, last in windows:
            if end < first:
                if first - RESULT_LOOKAHEAD_MS <= end and not panel_between(end, first):
                    return True
            elif start <= last + EVENT_ADJACENCY_MS:
                return True
        return False

    def flush():
        if len(run) < 2:
            return
        option = _facts(run[0]).get('preview_option')
        start, end = _time(run[0]), _time(run[-1])
        if claimed(start, end):
            return
        groups.append(dict(option=option, first_seen_ms=start, last_seen_ms=end, rows=[],
                           preview_rows=list(run), preview_only=True))

    for row in ordered:
        option = _facts(row).get('preview_option')
        if isinstance(option, str) and row.get('screen') != 'training_result':
            if run and (option != _facts(run[-1]).get('preview_option') or elapsed(_time(run[-1]), _time(row)) > PREVIEW_GAP_MS):
                flush()
                run = []
            run.append(row)
        elif run and elapsed(_time(run[-1]), _time(row)) > PREVIEW_GAP_MS:
            flush()
            run = []
    flush()
    return groups
