from pathlib import Path
import unittest


from tests import localdata


REPO_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = localdata.root("third_recording_hint_recovery_report", "report.json")
PREPARED_ROOTS = (
    localdata.root("prepared_snapshot_final", "independent-02"),
    localdata.root("prepared_snapshot_late", "independent-02"),
)


def _prepared_root():
    return next(
        (
            path
            for path in PREPARED_ROOTS
            if (path / "occluded-receipt-recovery/receipt-inspection.json").is_file()
        ),
        None,
    )


if __name__ == "__main__":
    unittest.main()
