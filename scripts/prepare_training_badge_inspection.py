"""CLI wrapper for bounded dense training inspection badge preparation."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracen_replay.training_inspection_localization import main


if __name__ == "__main__":
    raise SystemExit(main())
