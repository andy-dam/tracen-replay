from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid
import unittest


ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "analyzer" / "tools" / "build_training_gain_source_fixtures.py"
FIXTURE = ROOT / ".local" / "final-reliability-v1" / (
    "training-gain-source-fixtures.json"
)


class TrainingGainSourceFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest("preserved training-gain source fixtures are not present")
        cls.output_dir = ROOT / '.local' / ('fixture-build-' + uuid.uuid4().hex)
        cls.output_dir.mkdir()
        def cleanup():
            for path in cls.output_dir.iterdir():
                path.unlink()
            cls.output_dir.rmdir()
        cls.addClassCleanup(cleanup)
        output = cls.output_dir / 'fixtures.json'
        subprocess.run(
            [sys.executable, str(BUILDER), '--output', str(output)],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.fixture = json.loads(output.read_text(encoding="utf-8"))

    def test_builder_refuses_to_overwrite_existing_evidence(self):
        output = self.output_dir / 'existing.json'
        output.write_text('preserved evidence', encoding='utf-8')
        result = subprocess.run([sys.executable, str(BUILDER), '--output', str(output)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing to overwrite', result.stderr)
        self.assertEqual(output.read_text(encoding='utf-8'), 'preserved evidence')

    def test_turn072_aggregate_keeps_two_source_reviewed_children(self):
        fixture = self.fixture
        parent = next(
            item for item in fixture["numeric_cases"]
            if item["fixture_id"] == "v1/turn-072"
        )

        self.assertEqual(fixture["summary"]["numeric_case_count"], 15)
        self.assertEqual(fixture["summary"]["numeric_contribution_count"], 16)
        self.assertEqual(
            sum(item["contribution_count"] for item in fixture["numeric_cases"]),
            16,
        )
        self.assertEqual(fixture["summary"]["numeric_cases_with_source_gap"], 0)
        self.assertTrue(parent["source_backed"])
        self.assertEqual(parent["contribution_count"], 2)
        self.assertNotIn("source_expected_amount", parent)
        self.assertNotIn("source_gap", parent)

        children = parent["contributions"]
        self.assertEqual(
            [(child["event_id"], child["source_expected_amount"])
             for child in children],
            [("training-0074", 14), ("training-0076", 42)],
        )
        self.assertTrue(all(child["source_backed"] for child in children))
        self.assertTrue(all(len(child["observations"]) == 2 for child in children))
        self.assertNotIn(56, {
            child["source_expected_amount"] for child in children
        })

    def test_turn072_child_timestamps_match_sidecars(self):
        parent = next(
            item for item in self.fixture["numeric_cases"]
            if item["fixture_id"] == "v1/turn-072"
        )
        for child in parent["contributions"]:
            for observation in child["observations"]:
                self.assertEqual(
                    observation["timestamp_ms"],
                    observation["raw_sidecar"]["source_timestamp_ms"],
                )
        self.assertTrue(self.fixture["validation"]["all_references_validated"])
        self.assertEqual(self.fixture["validation"]["timestamp_mismatches"], [])


if __name__ == "__main__":
    unittest.main()
