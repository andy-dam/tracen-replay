"""Regression tests for source-observed race result projection."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from tests import localdata
from tracen_replay.evaluation_adapters import report_document
from tracen_replay.observation_evaluate import evaluate
from tests.test_causal_accounting import fixture


ROOT = Path(__file__).resolve().parents[2]
FINAL_ROOT = localdata.root("final_reliability_artifacts")
REFERENCE_PATH = FINAL_ROOT / "source-references" / "independent-01.json"
REPORT_PATH = localdata.root("second_recording_integration_replay", "candidate-report.json")


if __name__ == "__main__":
    unittest.main()
