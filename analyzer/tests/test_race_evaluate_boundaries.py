import unittest

from tracen_replay.race_evaluate import SCHEMA_VERSION, _overlap, evaluate


class RaceEvaluationBoundaryTests(unittest.TestCase):
    source_hash = "a" * 64

    def pair(
        self,
        *,
        scope=(200, 500),
        race_window=(300, 450),
        proof_timestamp=350,
        candidate_windows=(),
        expected=None,
    ):
        expected = expected or {"race_name": "Boundary Race"}
        evidence = "boundary.png"
        proof = {
            "source_timestamp_ms": proof_timestamp,
            "evidence": evidence,
            "sha256": "b" * 64,
        }
        reference = {
            "schema_version": SCHEMA_VERSION,
            "source_sha256": self.source_hash,
            "scope": "adjacent race boundary",
            "start_ms": scope[0],
            "end_ms": scope[1],
            "races": [
                {
                    "start_ms": race_window[0],
                    "end_ms": race_window[1],
                    "expected": expected,
                    "proofs": [proof],
                }
            ],
        }
        report = {
            "source": {"sha256": self.source_hash, "duration_ms": 1000},
            "gameplay_tracking": {
                "auxiliary_log_used": False,
                "races": [
                    {
                        "id": f"race-{index}",
                        "first_seen_ms": first,
                        "last_seen_ms": last,
                        **expected,
                    }
                    for index, (first, last) in enumerate(candidate_windows)
                ],
                "readings": [
                    {
                        "source_timestamp_ms": proof_timestamp,
                        "evidence": evidence,
                        "facts": {},
                    }
                ],
            },
        }
        return reference, report

    def test_prior_candidate_ending_at_scope_start_is_carry_in(self):
        reference, report = self.pair(
            candidate_windows=((100, 200), (300, 400)),
        )

        result = evaluate(reference, report)

        self.assertTrue(result["passed"])
        self.assertEqual(result["predicted_races"], 1)
        self.assertEqual(result["matched_races"], 1)
        self.assertEqual(result["unexpected_races"], [])

    def test_candidate_straddling_scope_start_remains_matchable(self):
        reference, report = self.pair(
            candidate_windows=((150, 250),),
            race_window=(200, 300),
            proof_timestamp=225,
        )

        result = evaluate(reference, report)

        self.assertTrue(result["passed"])
        self.assertEqual(result["predicted_races"], 1)
        self.assertEqual(result["matched_races"], 1)

    def test_adjacent_race_windows_do_not_reuse_boundary_only_carry_in(self):
        reference, report = self.pair(
            race_window=(200, 300),
            proof_timestamp=275,
            candidate_windows=((250, 300), (300, 400)),
        )
        reference["races"].append({
            "start_ms": 300,
            "end_ms": 450,
            "expected": {"race_name": "Boundary Race"},
            "proofs": [{"source_timestamp_ms": 350, "evidence": "next.png", "sha256": "c" * 64}],
        })
        report["gameplay_tracking"]["readings"].append({
            "source_timestamp_ms": 350, "evidence": "next.png", "facts": {},
        })

        result = evaluate(reference, report)

        self.assertTrue(result["passed"])
        self.assertEqual(result["matched_races"], 2)
        self.assertEqual([r["actual_id"] for r in result["race_results"]], ["race-0", "race-1"])

    def test_candidate_first_observed_at_scope_start_is_included(self):
        reference, report = self.pair(
            scope=(300, 500),
            race_window=(300, 450),
            proof_timestamp=300,
            candidate_windows=((300, 300),),
        )

        result = evaluate(reference, report)

        self.assertTrue(result["passed"])
        self.assertEqual(result["predicted_races"], 1)
        self.assertEqual(result["matched_races"], 1)

    def test_candidate_starting_at_scope_end_is_outside_half_open_scope(self):
        reference, report = self.pair(
            candidate_windows=((500, 500),),
        )

        result = evaluate(reference, report)

        self.assertFalse(result["passed"])
        self.assertEqual(result["predicted_races"], 0)
        self.assertEqual(result["matched_races"], 0)
        self.assertEqual(result["missing_races"], [0])
        self.assertEqual(result["unexpected_races"], [])

    def test_unknown_expected_fields_remain_unscored_at_boundary(self):
        expected = {
            "race_name": "Boundary Race",
            "course": {"variant": {"unknown": True}},
        }
        reference, report = self.pair(
            scope=(300, 500),
            race_window=(300, 450),
            proof_timestamp=300,
            candidate_windows=((300, 300),),
            expected=expected,
        )
        report["gameplay_tracking"]["races"][0]["course"] = {"variant": "outer"}

        result = evaluate(reference, report)

        self.assertTrue(result["passed"])
        self.assertEqual(result["unknown_fields"], 0)
        self.assertEqual(result["unscored_fields"], 1)
        self.assertEqual(
            next(
                check
                for check in result["race_results"][0]["checks"]
                if check["field"] == "course.variant"
            )["status"],
            "unscored",
        )

    def test_overlap_relation_keeps_boundary_cases_explicit(self):
        cases = (
            ((100, 200, 200, 500), False),
            ((200, 200, 200, 500), True),
            ((100, 250, 200, 500), True),
            ((500, 500, 200, 500), False),
            ((450, 500, 200, 500), True),
        )
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(_overlap(*values), expected)


if __name__ == "__main__":
    unittest.main()
