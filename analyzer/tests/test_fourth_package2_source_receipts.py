import copy
import hashlib
import json
from pathlib import Path
import unittest

from PIL import Image

from tests import localdata
from tracen_replay.vision import parse


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = localdata.root("fourth_recording_retest_newer", "report.json")
SOURCE_FRAME_PATH = localdata.root(
    "fourth_recording_source_controls_middle",
    "extensions/window-03-1526000-1564000/frames/000141.jpg",
)
REPORT_GAMEPLAY_PATH = localdata.root(
    "fourth_recording_retest_newer", "gameplay/part-013-frame-000005.png"
)


if __name__ == "__main__":
    unittest.main()
