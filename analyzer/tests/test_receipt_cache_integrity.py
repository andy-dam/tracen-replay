"""Regression coverage for bound OCR sidecar content."""

from __future__ import annotations

import json
import hashlib
import shutil
import unittest
import uuid

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from tests import localdata
from tracen_replay.inspect_training import reparse_inspection
from tracen_replay.occluded_receipt_recovery import (
    OccludedReceiptRecoveryError,
    _validate_cache_provenance,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = localdata.root("fourth_recording_matikane_package")
INSPECTION_PATH = SOURCE_ROOT / "receipt-inspection.json"
SOURCE_SHA256 = "deee9d88028611c7a7eb52dd3d589478561ca92193408f046c33b237e93edb1e"


@contextmanager
def workspace_temp():
    """Use the repository's writable scratch root on managed Windows hosts."""

    root = localdata.scratch(uuid.uuid4().hex)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
