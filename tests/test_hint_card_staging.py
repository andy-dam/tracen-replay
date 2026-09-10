"""Regression coverage for rejected recovery effects and real scene boundaries."""
from copy import deepcopy
import re
import unittest

from tests.test_hint_card_rows import source_rows
from tracen_replay.transactions import reconstruct
from tracen_replay.hint_card_rows import prepare

SOURCE = 'a' * 64


def _clone_rows_for_span(rows, source_times, target_times):
    """Clone wrapped source rows into a second, disjoint source span."""
    for source_time, target_time in zip(source_times, target_times):
        source = next(row for row in rows if row["source_timestamp_ms"] == source_time)
        cloned = deepcopy(source)
        cloned["source_timestamp_ms"] = target_time
        cloned["evidence"] = f"gameplay/{target_time}.png"
        rows.append(cloned)
    rows.sort(key=lambda row: row["source_timestamp_ms"])


def _candidate_for_span(candidate, target_times):
    result = deepcopy(candidate)
    result["source_timestamps_ms"] = list(target_times)
    for observation, timestamp in zip(result["observations"], target_times):
        observation["timestamp_ms"] = timestamp
        observation["evidence"] = f"gameplay/{timestamp}.png"
    return result


def _rank_candidate(candidate, suffix, target_times):
    result = _candidate_for_span(candidate, target_times)
    result["identity_proof"]["rank_state"] = "present"
    result["identity_proof"]["suffix"] = suffix
    for observation in result["observations"]:
        observation["card"]["suffix"] = suffix
        observation["card"]["suffix_state"] = "present"
    return result


def _amount_candidate(candidate, amount, target_times):
    result = _candidate_for_span(candidate, target_times)
    result["amount"] = amount
    for observation in result["observations"]:
        observation["receipt"]["amount"] = amount
        observation["receipt"]["text"] = observation["receipt"]["text"].replace(
            "Gained 2 ", f"Gained {amount} ", 1
        )
        observation["receipt_parts"]["prefix"]["text"] = observation[
            "receipt_parts"
        ]["prefix"]["text"].replace("Gained 2 ", f"Gained {amount} ", 1)
        observation["prefix_amount_proof"]["amount"] = amount
        observation["prefix_amount_proof"]["recognized_text"] = str(amount)
    return result


def _logical_hint_name(name):
    return re.sub(r"\s*[○◯◎]$", "", name).strip()


def _hint_effects(result):
    return [
        effect
        for event in result["events"]
        for effect in event.get("effects", [])
        if effect.get("kind") == "skill_hint_change"
    ]


def _assert_rejected_without_staged_effects(result, label):
    recovery = result.get("hint_card_recovery", {})
    accepted = recovery.get("accepted", [])
    accepted_summary = [
        (item.get("index"), item.get("name"), item.get("amount"))
        for item in accepted
        if isinstance(item, dict)
    ]
    assert not accepted, (label, "candidate survived rejection", accepted_summary)
    assert recovery.get('rejected'), (label, 'rejection is missing from the public audit')
    leaked = [
        effect
        for effect in _hint_effects(result)
        if _logical_hint_name(effect.get("name", "")) == "Wrapped Skill"
    ]
    leaked_summary = [(effect.get("name"), effect.get("amount")) for effect in leaked]
    assert not leaked, (label, "staged effects survived rejection", leaked_summary)


def _run_rank_conflict():
    rows, candidates = source_rows()
    _clone_rows_for_span(rows, (1000, 1250), (1500, 1750))
    first = _rank_candidate(candidates[0], "single_circle", (1000, 1250))
    second = _rank_candidate(candidates[0], "double_circle", (1500, 1750))
    result = reconstruct(
        rows,
        hint_card_observations=[first, second],
        source_sha256=SOURCE,
    )
    _assert_rejected_without_staged_effects(result, "disjoint rank conflict")


def _run_amount_conflict():
    rows, candidates = source_rows()
    _clone_rows_for_span(rows, (1000, 1250), (1500, 1750))
    first = _amount_candidate(candidates[0], 2, (1000, 1250))
    second = _amount_candidate(candidates[0], 3, (1500, 1750))
    # The copied source rows must carry the amount-3 prefix that the second
    # candidate claims; all other source geometry remains unchanged.
    for row in rows:
        if row["source_timestamp_ms"] not in (1500, 1750):
            continue
        for line in row["ocr"]["neural"]:
            if isinstance(line.get("text"), str) and line["text"].startswith("Gained 2 "):
                line["text"] = line["text"].replace("Gained 2 ", "Gained 3 ", 1)
    result = reconstruct(
        rows,
        hint_card_observations=[first, second],
        source_sha256=SOURCE,
    )
    _assert_rejected_without_staged_effects(result, "disjoint amount conflict")


def _run_narrative_boundary():
    rows, candidates = source_rows()
    rows[1]["ocr"]["neural"].append(
        {
            "text": "A long narrative line that closes this receipt sequence.",
            "confidence": 99,
            "box": [200, 830, 800, 870],
        }
    )
    result = reconstruct(
        rows,
        hint_card_observations=candidates,
        source_sha256=SOURCE,
    )
    _assert_rejected_without_staged_effects(result, "event-outcome narrative boundary")


class HintCardStagingTests(unittest.TestCase):
    def test_disjoint_rank_conflicts_leave_no_staged_hint(self):
        _run_rank_conflict()

    def test_disjoint_amount_conflicts_leave_no_staged_hint(self):
        _run_amount_conflict()

    def test_public_audit_preserves_original_indices_after_filtering(self):
        rows, candidates = source_rows()
        invalid = deepcopy(candidates[0])
        invalid['source_sha256'] = 'b' * 64
        result = reconstruct(rows, hint_card_observations=[invalid, candidates[0]], source_sha256=SOURCE)
        audit = result['hint_card_recovery']
        self.assertEqual([entry['index'] for entry in audit['accepted']], [1])
        self.assertEqual([entry['index'] for entry in audit['rejected']], [0])
        self.assertEqual(audit['row_recovery']['eligible_input_indices'], [1])

    def test_malformed_boundary_evidence_rejects_without_crashing(self):
        for mutation in ('observations', 'parts', 'line'):
            with self.subTest(mutation=mutation):
                rows, candidates = source_rows()
                if mutation == 'observations':
                    candidates[0]['observations'] = None
                elif mutation == 'parts':
                    candidates[0]['observations'][0]['receipt_parts'] = None
                else:
                    rows[1]['ocr']['neural'].append(dict(text='Unusable narrative evidence.', confidence=99))
                staged, eligible, audit = prepare(rows, candidates, source_sha256=SOURCE)
                self.assertEqual(staged, rows)
                self.assertEqual(eligible, [])
                self.assertTrue(audit['rejected'])

    def test_unrelated_narrative_preserves_the_receipt_boundary(self):
        _run_narrative_boundary()


if __name__ == '__main__':
    unittest.main()
