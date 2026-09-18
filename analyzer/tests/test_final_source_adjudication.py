import copy
import hashlib
import json
import unittest
from pathlib import Path

from tests import localdata
from tools.build_final_source_adjudication import apply_adjudications, build


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = localdata.root("final_reliability_artifacts", "source-references", "independent-01.json")
RECORDING = localdata.root("development_second_recording")
BASELINE = localdata.root("final_reliability_artifacts", "before-frozen-semantic-grade-v2.json")
MODIFIER_ARTIFACT = localdata.root("final_reliability_artifacts", "source-adjudication-modifiers-v1.json")
PREVIEW_ARTIFACT = localdata.root("final_reliability_artifacts", "preview-phase-adjudication-proposals-v1.json")
RACE_ARTIFACT = localdata.root("final_reliability_artifacts", "race-item-adjudication", "independent-01-t033-gold-omission.json")
ACTION_INTERVAL_ARTIFACT = localdata.root("final_reliability_artifacts", "semantic-action-interval-verdict-v1.json")


if __name__ == "__main__":
    unittest.main()
