"""Corroborate completed numeric receipts with later punctuation-only omissions."""


def resolve_with_later_repeat(event, key, observations, pending):
    """An unfinished line cannot supply a new amount or recipient identity.

    A completed, confident receipt must precede its exact text repeat in the
    same outcome. Only a single shorter digit reading may be rejected. This
    supplements the complete-receipt resolver without treating typewriter
    prefixes or alternate OCR views of one source frame as repeated proof.
    """
    conflicts = [c for c in event['conflicting_readings'] if c['field'] == key]
    if not conflicts or any(c['reason'] != 'changing_effect_value' for c in conflicts):
        return False
    amounts = {e.get('amount') for _, _, e in observations}
    if len(amounts) != 2 or any(type(n) is not int or n < 0 for n in amounts):
        return False
    winner = max(amounts, key=lambda n: len(str(n)))
    other = next(n for n in amounts if n != winner)
    if len(str(winner)) <= len(str(other)) or not (
            str(winner).startswith(str(other)) or str(winner).endswith(str(other))):
        return False
    outliers = [o for o in observations if o[2]['amount'] == other]
    if len(outliers) != 1:
        return False
    # A pending disagreement is still evidence against the proposed resolution.
    if any(e.get('amount') != winner for _, _, e in pending):
        return False
    complete = [o for o in observations if o[2]['amount'] == winner
                and o[2].get('confidence', 0) >= 97
                and not o[2].get('name') and o[2].get('field')
                and o[2].get('raw_text', '').endswith(('.', '!'))]
    pairs = [(a, b) for a in complete for b in pending
             if 50 <= b[0] - a[0] <= 500 and b[1] != a[1]
             and b[2].get('confidence', 0) >= 97
             and b[2].get('raw_text') == a[2]['raw_text'][:-1]
             and all(b[2].get(k) == a[2].get(k)
                     for k in ('kind', 'field', 'name', 'direction', 'value', 'amount'))
             and not a[0] <= outliers[0][0] <= b[0]]
    if not pairs:
        return False
    accepted, repeated = pairs[-1]
    event['effects'][key] = accepted[2]
    event['field_evidence'][key] = list(dict.fromkeys(
        [o[1] for o in complete] + [b[1] for _, b in pairs]))
    def proof(observation):
        time, evidence, effect = observation
        return dict(source_timestamp_ms=time, evidence=evidence,
                    raw_text=effect['raw_text'], amount=effect['amount'],
                    confidence=effect.get('confidence'))
    event.setdefault('resolved_reading_conflicts', []).append(dict(
        field=key, observed_amounts=sorted(amounts), accepted_amount=winner,
        basis='complete_receipt_with_later_punctuation_only_repeat',
        complete_receipt=proof(accepted), later_repeat=proof(repeated),
        rejected_observations=[proof(o) for o in outliers]))
    event['conflicting_readings'] = [c for c in event['conflicting_readings']
                                   if c['field'] != key]
    return True
