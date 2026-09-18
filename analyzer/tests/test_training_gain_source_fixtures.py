from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import uuid
import unittest

from tests import localdata

ROOT = Path(__file__).resolve().parents[2]
BUILDER = ROOT / "analyzer" / "tools" / "build_training_gain_source_fixtures.py"
FIXTURE = localdata.root("final_reliability_artifacts", "training-gain-source-fixtures.json")


if __name__ == "__main__":
    unittest.main()
