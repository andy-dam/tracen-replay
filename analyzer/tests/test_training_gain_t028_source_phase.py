"""Source-bound regression for the T028 skill-points animation.

The preserved source window contains a short ``+1`` component, one isolated
expanded-crop ``+18`` alternative, and repeated source rereads of the visible
``+13`` badge.  These tests exercise the phase owner and crop provenance
without using a balance, an expected amount, or a report label as a selector.
"""

import json
import unittest

from tests import localdata


REPORT = localdata.root("first_recording_t028_logs", "report.json")


def _t028_rows():
    if not REPORT.is_file():
        raise unittest.SkipTest(f"preserved T028 report is unavailable: {REPORT}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    rows = [
        row
        for row in report["gameplay_tracking"]["readings"]
        if row.get("screen") == "training_result"
        and 531300 <= row.get("source_timestamp_ms", -1) <= 531600
    ]
    return report, rows


if __name__ == "__main__":
    unittest.main()
