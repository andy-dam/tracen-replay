"""Worker preparation must fail before writes when frozen evidence changed."""
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import prepare_final_worker_inputs as preparation


class WorkerPreparationFreezeTests(unittest.TestCase):
    def test_reports_verified_checkpoint_and_bound_file_count(self):
        manifest = {"schema_version": "freeze-schema", "immutable_files_sha256": {"a": "hash"}}
        with patch("scripts.freeze_final_reliability.verify_manifest", return_value=manifest) as verify:
            result = preparation.verified_source_freeze()
        verify.assert_called_once_with(
            preparation.G2_FREEZE_PATH,
            expected_manifest_sha256=preparation.G2_FREEZE_SHA256,
        )
        self.assertEqual(result["g2_freeze_path"], str(preparation.G2_FREEZE_PATH))
        self.assertEqual(result["g2_freeze_verified_files"], 1)

    def test_invalid_freeze_prevents_new_root_creation(self):
        with patch.object(Path, "exists", return_value=False), patch.object(Path, "mkdir") as mkdir:
            with patch("scripts.freeze_final_reliability.verify_manifest", side_effect=ValueError("changed evidence")):
                with self.assertRaises(preparation.PreparationError):
                    preparation.prepare(Path(".local/not-created-freeze-test"), [])
        mkdir.assert_not_called()

    def test_invalid_freeze_prevents_existing_root_augmentation(self):
        with patch.object(Path, "is_dir", return_value=True), patch.object(preparation, "_copy_writable") as copy:
            with patch("scripts.freeze_final_reliability.verify_manifest", side_effect=ValueError("changed evidence")):
                with self.assertRaises(preparation.PreparationError):
                    preparation.augment(Path(".local/not-mutated-freeze-test"), ["v1"])
        copy.assert_not_called()
