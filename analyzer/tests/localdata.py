"""The repository's ``.local`` directory, as the tests see it.

It holds the OCR models and the tests' scratch directories, is never
published, and has no models on a clean checkout; a test that reads frames
with the real models lives in ``analyzer/lab/tests`` and skips without them.

``TRACEN_LOCAL_EVIDENCE`` overrides the base directory (default ``.local``,
resolved against the current working directory, which the suite runs from
the repository root). ``scratch()`` directories are absolute, because tests
create symbolic links there and a link whose target is a relative path
resolves against the link's own directory, not the working directory.
"""
import os
from pathlib import Path

BASE = Path(os.environ.get("TRACEN_LOCAL_EVIDENCE", ".local"))

# The OCR models the analyzer runs with; tests that read frames need them.
MODEL_DIR = BASE / "models" / "rapidocr"

def scratch(name: str) -> Path:
    """A writable directory for a test's own outputs under the local base.

    The path is absolute so that symbolic links a test makes inside it point
    where the test means them to.
    """
    path = (BASE / "test-runs" / name).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
