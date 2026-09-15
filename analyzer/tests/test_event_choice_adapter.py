import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from tracen_replay.event_choice_adapter import (
    SCHEMA,
    build_choice_observations,
    merge_committed_choices,
    merge_choice_observations,
    same_frame_choice_observation,
)
from tracen_replay.event_choice_commitment import reconstruct_committed_choices
from tracen_replay.evaluation_adapters import report_document
from tests.test_gameplay import workspace_temp


def _pane(*, marks=False):
    pane = Image.new("RGB", (810, 1080), (30, 50, 70))
    draw = ImageDraw.Draw(pane)
    for top in (603, 714):
        draw.rectangle((120, top, 685, top + 80), fill="white")
    if marks:
        for left in (115, 660):
            draw.rectangle((left, 700, left + 25, 725), fill=(255, 240, 0))
    return pane


def _lines(second="Second option"):
    return [
        {"text": "First option?", "confidence": 99, "box": [317, 628, 600, 656]},
        {"text": second, "confidence": 99, "box": [317, 739, 600, 767]},
        {"text": "A speaker", "confidence": 99, "box": [350, 800, 520, 825]},
    ]


def _raw(pane, timestamp, evidence, *, marks=False):
    lines = _lines()
    if marks:
        lines = [lines[1], lines[2], {"text": "Selected?", "confidence": 99,
                                     "box": [350, 840, 520, 865]}]
    return {
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "lines": lines,
        "gameplay_sha256": hashlib.sha256(pane.tobytes()).hexdigest(),
    }


def _write_frame(root, raw, pane):
    evidence = root / raw["evidence"]
    evidence.parent.mkdir(parents=True, exist_ok=True)
    pane.save(evidence)
    sidecar = root / "neural" / (Path(raw["evidence"]).stem + ".json")
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(raw), encoding="utf-8")


def _write_namespaced_frame(root, raw, pane):
    """Write a proof and its sibling neural sidecar under the same namespace."""
    evidence_relative = Path(raw["evidence"])
    evidence = root / evidence_relative
    evidence.parent.mkdir(parents=True, exist_ok=True)
    pane.save(evidence)
    parts = list(evidence_relative.parts)
    gameplay_index = max(index for index, part in enumerate(parts)
                         if part.casefold() == "gameplay")
    sidecar_relative = Path(*parts[:gameplay_index], "neural",
                            *parts[gameplay_index + 1:]).with_suffix(".json")
    sidecar = root / sidecar_relative
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(raw), encoding="utf-8")


class EventChoiceAdapterTests(unittest.TestCase):
    def test_physical_selection_conflicts_stay_unresolved_through_report_assembly(self):
        from unittest.mock import patch
        from tracen_replay.full_recording import assemble
        from tracen_replay.choice_evidence import observe
        from tracen_replay.vision import parse
        from tests.test_turn_ledger import report as ledger_fixture

        for conflicting in (False, True):
            with self.subTest(conflicting=conflicting), workspace_temp() as root:
                menu = _pane()
                selected_b = _pane(marks=True)
                frames = [(_raw(menu, 100, 'gameplay/menu-100.png'), menu),
                          (_raw(menu, 250, 'gameplay/menu-250.png'), menu),
                          (_raw(selected_b, 500, 'gameplay/selected-b.png', marks=True), selected_b)]
                if conflicting:
                    selected_a = _pane()
                    draw = ImageDraw.Draw(selected_a)
                    for left in (115, 660):
                        draw.rectangle((left, 590, left + 25, 615), fill=(255, 240, 0))
                    frames.append((_raw(selected_a, 500, 'gameplay/selected-a.png'), selected_a))
                readings = []
                inspection_rows = []
                for raw, pane in frames:
                    _write_frame(root, raw, pane)
                    parsed = parse(dict(raw, regions={}, header='', current_grid=False, result_grid=False))
                    readings.append(dict(parsed, source_timestamp_ms=raw['source_timestamp_ms'],
                                         evidence=raw['evidence']))
                    inspection_rows.append(dict(observe(pane, raw['lines']),
                                                source_timestamp_ms=raw['source_timestamp_ms'],
                                                evidence=raw['evidence']))
                envelope = build_choice_observations(readings, root)
                expected = 0 if conflicting else 1
                self.assertEqual(len(envelope['committed_choices']), expected)
                with patch('tracen_replay.full_recording.audit', return_value={}):
                    report = assemble(ledger_fixture(), readings, inspection_rows,
                                      committed_choices=envelope['committed_choices'],
                                      event_choice_observations=envelope)
                self.assertEqual(len(report['gameplay_tracking']['dialogue_choices']), expected)
                if conflicting:
                    self.assertTrue(any(row.get('observation_conflict') for row in envelope['observations']))
                else:
                    self.assertEqual(report['gameplay_tracking']['dialogue_choices'][0]['selected_index'], 1)

    def test_same_frame_helper_is_reusable_and_candidate_driven(self):
        pane = _pane(marks=True)
        result = same_frame_choice_observation(
            pane, _lines(), source_timestamp_ms=100, evidence="gameplay/frame.png"
        )
        self.assertEqual(result["observation_basis"], "verified_same_frame_choice_pixels")
        self.assertEqual(len(result["offered_card_candidates"]), 2)
        self.assertEqual(len(result["selection_mark_pairs"]), 1)
        self.assertIsNone(same_frame_choice_observation(
            pane, [{"text": "Career", "confidence": 99, "box": [10, 10, 100, 30]}],
            source_timestamp_ms=100, evidence="gameplay/hub.png"
        ))

    def test_cached_sidecars_and_proofs_produce_one_committed_choice(self):
        with workspace_temp() as root:
            first = _pane()
            second = _pane()
            selected = _pane(marks=True)
            raws = [
                _raw(first, 100, "gameplay/frame-100.png"),
                _raw(second, 250, "gameplay/frame-250.png"),
                _raw(selected, 500, "gameplay/frame-500.png", marks=True),
            ]
            for raw, pane in zip(raws, (first, second, selected)):
                _write_frame(root, raw, pane)
            readings = [dict(source_timestamp_ms=raw["source_timestamp_ms"],
                             evidence=raw["evidence"], screen="unknown") for raw in raws]
            result = build_choice_observations(readings, root)
            self.assertEqual(result["schema_version"], SCHEMA)
            self.assertEqual(result["committed_choice_count"], 1)
            choice = result["committed_choices"][0]
            self.assertEqual(choice["selected_text"], "Second option")
            self.assertEqual(choice["selection_observed_ms"], 500)
            self.assertEqual(result["merged_observation_count"], 3)
            self.assertTrue(result["audit"]["preview_observations_are_not_committed"])

    def test_fresh_rows_can_supply_raw_sidecars_without_cache_lookup(self):
        with workspace_temp() as root:
            panes = [_pane(), _pane(), _pane(marks=True)]
            raws = [_raw(pane, time, f"gameplay/fresh-{time}.png", marks=time == 500)
                    for pane, time in zip(panes, (100, 250, 500))]
            for raw, pane in zip(raws, panes):
                evidence = root / raw["evidence"]
                evidence.parent.mkdir(parents=True, exist_ok=True)
                pane.save(evidence)
            readings = [dict(source_timestamp_ms=raw["source_timestamp_ms"],
                             evidence=raw["evidence"], screen="unknown") for raw in raws]
            result = build_choice_observations(readings, root, raw_rows=raws)
            self.assertEqual(result["committed_choice_count"], 1)
            self.assertEqual(result["committed_choices"][0]["selected_index"], 1)

    def test_tampered_gameplay_pixels_are_not_promoted(self):
        with workspace_temp() as root:
            pane = _pane(marks=True)
            raw = _raw(pane, 100, "gameplay/frame.png", marks=True)
            _write_frame(root, raw, pane)
            Image.open(root / raw["evidence"]).convert("RGB").save(root / raw["evidence"])
            # Change one pixel after the sidecar was sealed.
            with Image.open(root / raw["evidence"]) as image:
                tampered = image.convert("RGB")
            tampered.putpixel((0, 0), (1, 2, 3))
            tampered.save(root / raw["evidence"])
            result = build_choice_observations([
                dict(source_timestamp_ms=100, evidence=raw["evidence"], screen="unknown")
            ], root)
            self.assertEqual(result["observations"], [])
            self.assertEqual(result["audit"]["counts"]["gameplay_pixels_changed"], 1)

    def test_candidate_gate_skips_non_dialogue_without_opening_proof(self):
        with workspace_temp() as root:
            raw = {
                "source_timestamp_ms": 100,
                "evidence": "gameplay/no-choice.png",
                "lines": [{"text": "Career", "confidence": 99,
                           "box": [154, 3, 218, 30]}],
            }
            (root / "neural").mkdir()
            (root / "neural" / "no-choice.json").write_text(json.dumps(raw), encoding="utf-8")
            result = build_choice_observations([
                dict(source_timestamp_ms=100, evidence=raw["evidence"], screen="unknown")
            ], root)
            self.assertEqual(result["observations"], [])
            self.assertEqual(result["audit"]["counts"]["candidate_gate_skipped"], 1)

    def test_existing_choice_rows_are_deduplicated_and_boundary_wins(self):
        observation = {
            "source_timestamp_ms": 100,
            "evidence": "frame.png",
            "offered_card_candidates": [],
            "selection_mark_pairs": [],
        }
        merged = merge_choice_observations(
            [observation],
            [dict(observation, evidence="duplicate.png", offered_card_slots=[{"text": "x",
                "text_box": [317, 628, 600, 656], "card_y": [603, 683]}])],
            [{"source_timestamp_ms": 100, "evidence": "known.png", "screen_boundary": True}],
        )
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["screen_boundary"])

    def test_equivalent_committed_outputs_are_emitted_once(self):
        base = {
            "kind": "dialogue_choice",
            "options": ["First", "Second"],
            "selected_index": 1,
            "selected_text": "Second",
            "first_seen_ms": 100,
            "selection_observed_ms": 500,
            "selection_marks": {"left": {"box": [258, 700, 313, 733]},
                                 "right": {"box": [798, 700, 853, 733]}},
            "evidence": "shared.png",
        }
        fresh = dict(base, selection_state="committed")
        result = merge_committed_choices([base], [fresh])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["selection_state"], "committed")
        self.assertEqual(result[0]["phase"], "committed")
        self.assertEqual(result[0]["evidence"], ["shared.png"])

    def test_committed_choice_projects_as_selected_action(self):
        choice = {
            "kind": "dialogue_choice",
            "options": ["First", "Second"],
            "selected_index": 1,
            "selected_text": "Second",
            "first_seen_ms": 100,
            "selection_observed_ms": 500,
            "selection_marks": {"left": {"box": [258, 700, 313, 733]},
                                 "right": {"box": [798, 700, 853, 733]}},
            "selection_state": "committed",
            "evidence": ["gameplay/menu.png", "gameplay/selected.png"],
        }
        choice = merge_committed_choices([choice])[0]
        report = {
            "source": {"sha256": "source-sha"},
            "gameplay_tracking": {
                "readings": [
                    {"source_timestamp_ms": 100, "evidence": "gameplay/menu.png"},
                    {"source_timestamp_ms": 500, "evidence": "gameplay/selected.png"},
                ],
                "events": [],
                "dialogue_choices": [choice],
                "turn_action_receipts": [],
            },
        }
        document = report_document(report)
        self.assertEqual(len(document["observations"]), 1)
        observation = document["observations"][0]
        self.assertEqual(observation["phase"], "committed")
        self.assertEqual(observation["payload"]["selected_choice"], "Second")

    def test_same_time_distinct_proofs_are_preserved_and_abstained(self):
        left = {
            "source_timestamp_ms": 100,
            "evidence": "run-a/gameplay/frame.png",
            "offered_card_candidates": [
                {"text": "Alpha", "text_box": [317, 628, 600, 656], "card_y": [603, 683]},
            ],
            "selection_mark_pairs": [],
        }
        right = {
            "source_timestamp_ms": 100,
            "evidence": "run-b/gameplay/frame.png",
            "offered_card_candidates": [
                {"text": "Beta", "text_box": [317, 628, 600, 656], "card_y": [603, 683]},
            ],
            "selection_mark_pairs": [],
        }
        merged = merge_choice_observations([left], [right])
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(row["observation_conflict"] for row in merged))
        self.assertEqual(reconstruct_committed_choices(merged), [])

    def test_nested_basename_collision_uses_exact_namespace_sidecar(self):
        with workspace_temp() as root:
            pane = _pane()
            alpha = _raw(pane, 100, "alpha/gameplay/frame.png")
            alpha["lines"] = _lines("Alpha option")
            beta = _raw(pane, 100, "beta/gameplay/frame.png")
            beta["lines"] = _lines("Beta option")
            _write_namespaced_frame(root, alpha, pane)
            _write_namespaced_frame(root, beta, pane)
            # This is the legacy root-level basename trap.  A correct lookup
            # must never consult it for either namespaced proof.
            (root / "neural").mkdir(parents=True, exist_ok=True)
            (root / "neural" / "frame.json").write_text(json.dumps(beta), encoding="utf-8")
            result = build_choice_observations([
                {"source_timestamp_ms": 100, "evidence": alpha["evidence"], "screen": "unknown"}
            ], root)
            self.assertEqual(result["source_observation_count"], 1)
            self.assertEqual(result["observations"][0]["offered_card_candidates"][1]["text"],
                             "Alpha option")

    def test_prepared_recording_choices_survive_namespace_rebasing(self):
        base = Path('.local/final-reliability-v1')
        report_path = base / 'full-worker-candidate-v6/independent-02/report.json'
        root = base / 'worker-runs/post-recognition-g8-v3-prepared/independent-02'
        if not report_path.is_file() or not (root / 'neural').is_dir():
            self.skipTest('Prepared recording report or its v3 evidence root (removed in the 2026-09-13 cleanup) unavailable')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        readings = [row for row in report['gameplay_tracking']['readings']
                    if 1267000 <= row['source_timestamp_ms'] <= 1274000]
        result = build_choice_observations(readings, root)
        self.assertEqual(len(result['committed_choices']), 1)
        choice = result['committed_choices'][0]
        self.assertEqual(choice['selected_index'], 1)
        self.assertEqual(choice['selection_observed_ms'], 1271000)
        self.assertEqual(choice['phase'], 'committed')
        self.assertIsNone(choice['click_timestamp_ms'])
        self.assertTrue(all(path.startswith('initial-baseline/')
                            for path in choice['evidence']))
        self.assertNotIn('evidence_path_mismatch', result['audit']['counts'])
        # Exercise the same consumer projection and frozen source row that
        # exposed the missing action in the complete worker output.
        from tracen_replay.observation_evaluate import evaluate
        report['gameplay_tracking']['dialogue_choices'] = result['committed_choices']
        reference = json.loads((base / 'source-references/independent-02.json')
                               .read_text(encoding='utf-8'))
        expected = next(row for case in reference['cases'] for row in case['observations']
                        if row['id'] == 'ind02-t053-expression-conviction-choice')
        grade = evaluate(dict(source_sha256=reference['source_sha256'],
                              scope_ms=[1267000, 1274001], reference_complete=False,
                              observations=[expected]), report_document(report))
        self.assertEqual(grade['results'][0]['status'], 'correct')

    def test_nested_sidecar_local_paths_keep_exact_namespace(self):
        with workspace_temp() as root:
            pane = _pane()
            raw = _raw(pane, 100, "alpha/gameplay/frame.png")
            _write_namespaced_frame(root, raw, pane)
            local = dict(raw, evidence="gameplay/frame.png")
            sidecar = root / "alpha/neural/frame.json"
            sidecar.write_text(json.dumps(local), encoding="utf-8")
            reading = {"source_timestamp_ms": 100, "evidence": raw["evidence"],
                       "screen": "unknown"}
            result = build_choice_observations([reading], root)
            self.assertEqual(result["source_observation_count"], 1)
            self.assertEqual(result["observations"][0]["evidence"], [raw["evidence"]])
            for wrong in ("../beta/gameplay/frame.png", "gameplay/other.png"):
                sidecar.write_text(json.dumps(dict(local, evidence=wrong)), encoding="utf-8")
                rejected = build_choice_observations([reading], root)
                self.assertEqual(rejected["observations"], [])

    def test_traversal_and_outside_sidecar_are_rejected(self):
        with workspace_temp() as root:
            pane = _pane()
            inside = _raw(pane, 100, "gameplay/frame.png")
            _write_frame(root, inside, pane)
            traversal = build_choice_observations([
                {"source_timestamp_ms": 100, "evidence": "../outside/gameplay/frame.png",
                 "screen": "unknown"}
            ], root)
            self.assertEqual(traversal["observations"], [])
            self.assertEqual(traversal["audit"]["counts"]["missing_raw_sidecar"], 1)

            malicious = dict(inside, evidence="../outside/gameplay/frame.png")
            (root / "neural" / "frame.json").write_text(json.dumps(malicious), encoding="utf-8")
            rejected = build_choice_observations([
                {"source_timestamp_ms": 100, "evidence": inside["evidence"], "screen": "unknown"}
            ], root)
            self.assertEqual(rejected["observations"], [])
            self.assertEqual(rejected["audit"]["counts"]["evidence_path_outside_root"], 1)

    def test_committed_same_signature_from_distinct_proofs_is_omitted(self):
        base = {
            "kind": "dialogue_choice",
            "options": ["First", "Second"],
            "selected_index": 1,
            "selected_text": "Second",
            "first_seen_ms": 100,
            "selection_observed_ms": 500,
            "selection_marks": {"left": {"box": [258, 700, 313, 733]},
                                 "right": {"box": [798, 700, 853, 733]}},
            "selection_state": "committed",
        }
        self.assertEqual(
            merge_committed_choices([dict(base, evidence="run-a/frame.png")],
                                    [dict(base, evidence="run-b/frame.png")]),
            [],
        )

    def test_same_proof_with_conflicting_source_hashes_is_abstained(self):
        left = {
            "source_timestamp_ms": 100,
            "evidence": "run/gameplay/frame.png",
            "source_gameplay_sha256": "aaa",
            "offered_card_candidates": [{"text": "A", "card_y": [603, 683],
                                          "text_box": [317, 628, 600, 656]}],
            "selection_mark_pairs": [],
        }
        right = dict(left, source_gameplay_sha256="bbb")
        merged = merge_choice_observations([left], [right])
        self.assertEqual(len(merged), 2)
        self.assertTrue(all(row["observation_conflict"] for row in merged))

    def test_duplicate_raw_source_key_with_conflicting_hash_is_not_selected(self):
        with workspace_temp() as root:
            pane = _pane()
            first = _raw(pane, 100, "gameplay/frame.png")
            second = dict(first, gameplay_sha256="different-hash")
            _write_frame(root, first, pane)
            result = build_choice_observations([
                {"source_timestamp_ms": 100, "evidence": first["evidence"], "screen": "unknown"}
            ], root, raw_rows=[first, second])
            self.assertEqual(result["observations"], [])
            self.assertEqual(result["audit"]["counts"]["missing_raw_sidecar"], 1)

    @unittest.skipUnless(
        Path(".local/full-recording/independent-02/initial-baseline/neural/part-010-frame-000285.json").is_file(),
        "local frozen source images are not part of a clean checkout",
    )
    def test_independent_source_cache_fixture_reaches_commitment_adapter(self):
        base = Path(".local/full-recording/independent-02/initial-baseline")
        readings = []
        for number in range(272, 286):
            raw = json.loads((base / "neural" / f"part-010-frame-{number:06d}.json").read_text(encoding="utf-8"))
            readings.append({"source_timestamp_ms": raw["source_timestamp_ms"],
                             "evidence": raw["evidence"], "screen": "unknown"})
        result = build_choice_observations(readings, base)
        self.assertEqual(result["committed_choice_count"], 1)
        self.assertEqual(result["committed_choices"][0]["selected_text"],
                         "Your dedication to the potential of Umamusume?")


if __name__ == "__main__":
    unittest.main()
