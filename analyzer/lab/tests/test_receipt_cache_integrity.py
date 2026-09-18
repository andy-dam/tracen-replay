"""Tests of ``tests.test_receipt_cache_integrity`` that need locally preserved evidence; they run only where it is."""
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
from tests.test_receipt_cache_integrity import INSPECTION_PATH, SOURCE_ROOT, SOURCE_SHA256, workspace_temp


@unittest.skipUnless(
    INSPECTION_PATH.is_file()
    and (SOURCE_ROOT / "receipt-inspection").is_dir(),
    "The source-bound Matikane inspection cache is not available",
)
class ReceiptCacheIntegrityTests(unittest.TestCase):
    """Use a disposable copy of the real source cache for mutation tests."""

    @staticmethod
    def _copy_cache(destination: Path) -> dict[str, Any]:
        shutil.copy2(INSPECTION_PATH, destination / "receipt-inspection.json")
        shutil.copytree(
            SOURCE_ROOT / "receipt-inspection",
            destination / "receipt-inspection",
        )
        return json.loads(
            (destination / "receipt-inspection.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def _sidecar_for_line(
        cache: Path,
        inspection: dict[str, Any],
        predicate: Callable[[dict[str, Any]], bool],
    ) -> Path:
        for reading in inspection["readings"]:
            neural = (reading.get("ocr") or {}).get("neural")
            if not isinstance(neural, list):
                continue
            if not any(
                isinstance(line, dict) and predicate(line) for line in neural
            ):
                continue
            evidence = Path(reading["evidence"])
            sidecar = cache / evidence.with_suffix(".v2.json")
            if not sidecar.is_file():
                sidecar = cache / evidence.with_suffix(".json")
            return sidecar
        raise AssertionError("No registered source line matched the test predicate")

    @staticmethod
    def _validated_reparse(
        cache: Path, inspection: dict[str, Any]
    ) -> list[dict[str, Any]]:
        _validate_cache_provenance(inspection, cache, SOURCE_SHA256)
        return reparse_inspection(inspection, cache)

    def test_original_bound_cache_reparses_matikane_before_mutation(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            rows = self._validated_reparse(cache, inspection)
            effects = [
                effect
                for row in rows
                for effect in row.get("effects", [])
                if effect.get("kind") == "friendship_status"
                and effect.get("name") == "Matikanefukukitaru"
            ]
            self.assertTrue(effects)
            self.assertTrue(all(effect.get("value") == "maximum" for effect in effects))

    def test_mutated_registered_text_amount_direction_is_rejected(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            sidecar = self._sidecar_for_line(
                cache,
                inspection,
                lambda line: line.get("text") == "Energy went down by 14.",
            )
            original = sidecar.read_bytes()
            payload = json.loads(original.decode("utf-8"))
            target = next(
                line
                for line in payload["lines"]
                if line.get("text") == "Energy went down by 14."
            )
            target["text"] = "Energy went up by 999."
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            try:
                with self.assertRaisesRegex(
                    OccludedReceiptRecoveryError,
                    "lines differ from the bound inspection witness",
                ):
                    self._validated_reparse(cache, inspection)
            finally:
                sidecar.write_bytes(original)

    def test_mutated_registered_box_and_confidence_are_rejected(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            sidecar = self._sidecar_for_line(
                cache,
                inspection,
                lambda line: "Matikanefukukitaru" in str(line.get("text", "")),
            )
            original = sidecar.read_bytes()
            try:
                for mutation in (
                    lambda line: line.update(box=[317, 855, 744, 881]),
                    lambda line: line.update(confidence=1),
                ):
                    payload = json.loads(original.decode("utf-8"))
                    target = next(
                        line
                        for line in payload["lines"]
                        if "Matikanefukukitaru" in str(line.get("text", ""))
                    )
                    mutation(target)
                    sidecar.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(
                        OccludedReceiptRecoveryError,
                        "lines differ from the bound inspection witness",
                    ):
                        self._validated_reparse(cache, inspection)
                    sidecar.write_bytes(original)
            finally:
                sidecar.write_bytes(original)

    def test_optional_new_sidecar_digest_catches_non_line_mutation(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            sidecar = self._sidecar_for_line(
                cache,
                inspection,
                lambda line: "Matikanefukukitaru" in str(line.get("text", "")),
            )
            relative = sidecar.relative_to(cache).as_posix()
            reading = next(
                item
                for item in inspection["readings"]
                if Path(item.get("evidence", "")).with_suffix(".v2.json").as_posix()
                == relative
            )
            reading["sidecar_sha256"] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            self._validated_reparse(cache, inspection)
            original = sidecar.read_bytes()
            payload = json.loads(original.decode("utf-8"))
            payload["header"] = "mutated-but-lines-unchanged"
            sidecar.write_text(json.dumps(payload), encoding="utf-8")
            try:
                with self.assertRaisesRegex(
                    OccludedReceiptRecoveryError,
                    "changed after its inspection manifest was bound",
                ):
                    self._validated_reparse(cache, inspection)
            finally:
                sidecar.write_bytes(original)

    def test_registered_reading_without_neural_or_sidecar_witness_is_rejected(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            reading = inspection["readings"][0]
            reading.pop("ocr", None)
            reading.pop("sidecar_sha256", None)
            with self.assertRaisesRegex(
                OccludedReceiptRecoveryError,
                "no immutable OCR witness",
            ):
                self._validated_reparse(cache, inspection)

    def test_legacy_reading_with_bound_sidecar_digest_remains_compatible(self):
        with workspace_temp() as cache:
            inspection = self._copy_cache(cache)
            reading = inspection["readings"][0]
            evidence = cache / Path(reading["evidence"])
            sidecar = evidence.with_suffix(".v2.json")
            if not sidecar.is_file():
                sidecar = evidence.with_suffix(".json")
            reading.pop("ocr", None)
            reading["sidecar_sha256"] = hashlib.sha256(sidecar.read_bytes()).hexdigest()
            rows = self._validated_reparse(cache, inspection)
            self.assertTrue(rows)
