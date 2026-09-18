"""Focused validation tests for the source-bound replay input manifest."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from pathlib import Path

from tests.test_gameplay import workspace_temp
from tracen_replay.replay_inputs import ReplayInputError, SCHEMA, load_manifest, normalize_manifest


SOURCE_SHA256 = "a" * 64


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_hash(value: object) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True).encode("utf-8"))


class ReplayInputManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._workspace = workspace_temp()
        self.root = self._workspace.__enter__()

    def tearDown(self) -> None:
        self._workspace.__exit__(None, None, None)

    def _write_bytes(self, relative: str, value: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def _write_json(self, relative: str, value: object) -> Path:
        return self._write_bytes(relative, json.dumps(value, sort_keys=True).encode("utf-8"))

    def _make_base(self) -> dict[str, object]:
        folder = "initial-baseline"
        source_frame = self._write_bytes(
            f"{folder}/part-000/frames/000001.jpg", b"source frame pixels"
        )
        gameplay = self._write_bytes(
            f"{folder}/gameplay/part-000-frame-000001.png", b"cropped gameplay pixels"
        )
        raw = {
            "source_timestamp_ms": 1000,
            "source_frame_sha256": _sha256_file(source_frame),
            "evidence": "gameplay/part-000-frame-000001.png",
            "engine_fingerprint": "engine-v1",
            "model_sha256": "b" * 64,
        }
        raw_path = self._write_json(f"{folder}/neural/part-000-frame-000001.json", raw)
        self.assertTrue(gameplay.is_file())
        capture = {
            "source": {"sha256": SOURCE_SHA256, "duration_ms": 5000},
            "frames": [
                {
                    "id": "part-000-frame-000001",
                    "source_timestamp_ms": 1000,
                    "evidence": "part-000/frames/000001.jpg",
                }
            ],
        }
        capture_path = self._write_json(f"{folder}/capture.json", capture)
        return {
            "folder": folder,
            "capture": "capture.json",
            "neural": "neural",
            "capture_sha256": _sha256_file(capture_path),
            "frame_count": 1,
            "capture_path": capture_path,
            "raw_path": raw_path,
        }

    def _manifest(self, **overrides: object) -> dict[str, object]:
        base = self._make_base()
        manifest: dict[str, object] = {
            "schema_version": SCHEMA,
            "source_sha256": SOURCE_SHA256,
            "base": {
                key: value
                for key, value in base.items()
                if key in {"folder", "capture", "neural", "capture_sha256", "frame_count"}
            },
            "inspections": [],
            "recovery": [],
            "supplements": [],
        }
        manifest.update(overrides)
        return manifest

    def _make_inspection(
        self,
        *,
        count: int = 1,
        folder: str = "receipt-recovery-v1",
        own_root: str | None = None,
        row_evidence_root: str = "folder",
        manifest_name: str = "receipt-inspection.json",
        merge: str = "receipt",
    ) -> dict[str, object]:
        if own_root is None:
            own_root = folder
        rows: list[dict[str, object]] = []
        frames: list[dict[str, object]] = []
        for index in range(1, count + 1):
            frame_id = f"frame-{index:06d}"
            timestamp = 1000 + index
            image_path = self._write_bytes(
                f"{own_root}/receipt-inspection/window/frames/{index:06d}.jpg",
                f"source-{index}".encode("ascii"),
            )
            evidence_path = self._write_bytes(
                f"{own_root}/receipt-inspection/window/{frame_id}.png",
                f"crop-{index}".encode("ascii"),
            )
            raw = {
                "source_sha256": SOURCE_SHA256,
                "source_timestamp_ms": timestamp,
                "source_frame_sha256": _sha256_file(image_path),
                "evidence": f"receipt-inspection/window/{frame_id}.png",
                "engine_fingerprint": "engine-v1",
                "model_sha256": "c" * 64,
            }
            self._write_json(
                f"{own_root}/receipt-inspection/window/{frame_id}.v2.json", raw
            )
            frames.append(
                {
                    "id": frame_id,
                    "source_timestamp_ms": timestamp,
                    "evidence": f"frames/{index:06d}.jpg",
                }
            )
            row_evidence = f"receipt-inspection/window/{frame_id}.png"
            if row_evidence_root == "root":
                row_evidence = f"{own_root}/{row_evidence}"
            rows.append({"source_timestamp_ms": timestamp, "evidence": row_evidence})
            self.assertTrue(evidence_path.is_file())
        self._write_json(f"{own_root}/receipt-inspection/window/frames.json", frames)
        manifest_folder = folder
        manifest_path = self._write_json(
            f"{manifest_folder}/{manifest_name}" if manifest_folder else manifest_name,
            {"source_sha256": SOURCE_SHA256, "readings": rows},
        )
        return {
            "id": "receipt-probe",
            "folder": folder,
            "own_root": own_root,
            "manifest": manifest_name,
            "manifest_sha256": _sha256_file(manifest_path),
            "row_evidence_root": row_evidence_root,
            "row_count": count,
            "merge": merge,
        }

    def _make_currency_supplement(self) -> dict[str, object]:
        self._make_base()
        raw_path = self.root / "initial-baseline/neural/part-000-frame-000001.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        evidence_path = self.root / "initial-baseline" / raw["evidence"]
        sidecar = {
            "raw_sha256": _canonical_hash(raw),
            "evidence_sha256": _sha256_file(evidence_path),
            "source_timestamp_ms": raw["source_timestamp_ms"],
            "regions": {"speed": [1, 2, 3, 4]},
        }
        sidecar_path = self._write_json("currency-refinement/part-000-frame-000001.json", sidecar)
        return {
            "kind": "currency_regions",
            "folder": "currency-refinement",
            "entries": [
                {
                    "path": sidecar_path.name,
                    "sidecar_sha256": _sha256_file(sidecar_path),
                    "raw_path": raw_path.relative_to(self.root).as_posix(),
                    "raw_sha256": sidecar["raw_sha256"],
                    "evidence_path": evidence_path.relative_to(self.root).as_posix(),
                    "evidence_sha256": sidecar["evidence_sha256"],
                    "source_timestamp_ms": sidecar["source_timestamp_ms"],
                }
            ],
        }

    def test_nested_own_root_mixed_manifest_normalizes_23_rows(self):
        inspection = self._make_inspection(
            count=23,
            folder="",
            own_root="receipt-recovery-v1",
            row_evidence_root="root",
        )
        inspection["row_indices"] = list(range(23))
        manifest = self._manifest(inspections=[inspection])

        normalized = normalize_manifest(manifest, self.root)

        group = normalized["inspections"][0]
        self.assertEqual(group["own_root"], "receipt-recovery-v1")
        self.assertEqual(group["row_count"], 23)
        self.assertEqual(len(group["rows"]), 23)
        self.assertTrue(all(row["evidence"].startswith("receipt-recovery-v1/") for row in group["rows"]))
        self.assertTrue(all(row["raw_evidence"].startswith("receipt-recovery-v1/") for row in group["rows"]))
        self.assertTrue(all(row["source_frame"] for row in group["rows"]))
        self.assertEqual(group["row_indices"], list(range(23)))

    def test_duplicate_manifest_row_index_is_rejected(self):
        inspection = self._make_inspection()
        inspection["row_indices"] = [0, 0]

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(self._manifest(inspections=[inspection]), self.root)
        self.assertEqual(raised.exception.code, "duplicate_reading")

    def test_manifest_file_loader_uses_its_cache_root(self):
        path = self._write_json("replay-input-manifest.json", self._manifest())

        normalized = load_manifest(path)

        self.assertEqual(normalized["source_sha256"], SOURCE_SHA256)
        self.assertEqual(normalized["base"]["frame_count"], 1)

    def test_nested_base_paths_keep_the_manifest_root_prefix(self):
        normalized = normalize_manifest(self._manifest(), self.root)

        self.assertEqual(normalized["base"]["folder"], "initial-baseline")
        self.assertEqual(normalized["base"]["capture"], "initial-baseline/capture.json")
        self.assertEqual(normalized["base"]["neural"], "initial-baseline/neural")

    def test_generic_occluded_receipt_recovery_is_a_source_bound_group(self):
        recovery = self._make_inspection(
            folder="occluded-receipt-recovery",
            own_root="occluded-receipt-recovery",
            merge="receipt",
        )
        recovery["kind"] = "occluded_receipt"
        plan = self._write_json("occluded-receipt-recovery/last-plan.json", {
            "source_sha256": SOURCE_SHA256,
            "manifest_sha256": recovery["manifest_sha256"],
            "requested_windows": [], "processed_windows": [],
        })
        recovery.update(plan="last-plan.json", plan_sha256=_sha256_file(plan))
        manifest = self._manifest(recovery=[recovery])

        normalized = normalize_manifest(manifest, self.root)

        self.assertEqual(normalized["recovery"][0]["kind"], "occluded_receipt")
        self.assertEqual(normalized["recovery"][0]["row_count"], 1)
        self.assertEqual(normalized["recovery"][0]["plan"], "occluded-receipt-recovery/last-plan.json")

        original = plan.read_bytes()
        for mutation in (
            {"requested_windows": [{"start_ms": 0, "end_ms": 50000}]},
            {"processed_windows": [{"triggers": [{"line_box": [300, 800, 500, 900]}]}]},
        ):
            with self.subTest(mutation=mutation):
                payload = json.loads(original)
                payload.update(mutation)
                plan.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ReplayInputError, "plan_sha256"):
                    normalize_manifest(manifest, self.root)
        plan.write_bytes(original)

    def test_overlapping_receipt_windows_keep_distinct_bound_views(self):
        recovery = self._make_inspection(folder="occluded-receipt-recovery")
        recovery["kind"] = "occluded_receipt"
        folder = self.root / "occluded-receipt-recovery"
        original = folder / "receipt-inspection/window"
        alternate = "occluded-receipt-recovery/receipt-inspection/overlap"
        for name in ("frame-000001.png", "frames/000001.jpg", "frames.json"):
            self._write_bytes(f"{alternate}/{name}", (original / name).read_bytes())
        raw = json.loads((original / "frame-000001.v2.json").read_text())
        raw["evidence"] = "receipt-inspection/overlap/frame-000001.png"
        self._write_json(f"{alternate}/frame-000001.v2.json", raw)
        payload = json.loads((folder / "receipt-inspection.json").read_text())
        payload["readings"].append({"source_timestamp_ms": 1001, "evidence": raw["evidence"]})

        def bind():
            inspection = self._write_json("occluded-receipt-recovery/receipt-inspection.json", payload)
            recovery["manifest_sha256"] = _sha256_file(inspection)
            recovery["row_count"] = len(payload["readings"])
            plan = self._write_json("occluded-receipt-recovery/last-plan.json", {
                "source_sha256": SOURCE_SHA256,
                "manifest_sha256": recovery["manifest_sha256"],
                "requested_windows": [], "processed_windows": [],
            })
            recovery.update(plan="last-plan.json", plan_sha256=_sha256_file(plan))
            return self._manifest(recovery=[recovery])

        manifest = bind()
        rows = normalize_manifest(manifest, self.root)["recovery"][0]["rows"]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["source_timestamp_ms"] for row in rows}, {1001})
        self.assertEqual(len({row["evidence"] for row in rows}), 2)
        # The normalized descriptor intentionally carries only file pointers.
        # Reconciliation receives parsed rows, so attach the validated raw
        # source/model envelope that the normal reparse route propagates.
        for row in rows:
            raw = json.loads((self.root / row["raw"]).read_text(encoding="utf-8"))
            row.update(
                source_sha256=SOURCE_SHA256,
                source_frame_sha256=raw["source_frame_sha256"],
                source_frame_id=row["source_frame"],
                engine_fingerprint=raw["engine_fingerprint"],
                model_sha256=raw["model_sha256"],
            )
        # Receipt assembly collapses repeated effects while retaining each
        # source view as evidence, rather than counting two transactions.
        from tracen_replay.inspect_receipts import merge
        effects = [{"kind": "energy_change", "amount": -13}]
        merged = merge([], [dict(row, screen="event_outcome", effects=effects) for row in rows])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["effects"], effects)
        self.assertEqual(len(merged[0]["supplemental_receipt_observations"]), 1)
        for kind in ("numeric_receipt", "training_gain"):
            recovery["kind"] = kind
            with self.subTest(kind=kind), self.assertRaisesRegex(ReplayInputError, "duplicates a reading"):
                normalize_manifest(bind(), self.root)
        recovery["kind"] = "occluded_receipt"
        payload["readings"].append(dict(payload["readings"][0]))
        with self.assertRaisesRegex(ReplayInputError, "duplicates a reading"):
            normalize_manifest(bind(), self.root)

        for key in ("plan", "plan_sha256"):
            with self.subTest(missing=key):
                value = recovery.pop(key)
                with self.assertRaises(ReplayInputError):
                    normalize_manifest(manifest, self.root)
                recovery[key] = value

    def test_bound_recovery_plan_cannot_escape_root_or_change_after_normalization(self):
        from tracen_replay.replay_inputs import load_bound_recovery_plan
        plan = self._write_json("recovery/last-plan.json", {
            "source_sha256": SOURCE_SHA256, "manifest_sha256": "c" * 64,
        })
        expected = _sha256_file(plan)
        for path in ("../last-plan.json", str(plan.resolve())):
            with self.subTest(path=path), self.assertRaises(ReplayInputError):
                load_bound_recovery_plan(self.root, path, expected,
                                         source_sha256=SOURCE_SHA256, manifest_sha256="c" * 64)
        plan.write_text('{"changed":true}', encoding="utf-8")
        with self.assertRaisesRegex(ReplayInputError, "plan_sha256"):
            load_bound_recovery_plan(self.root, "recovery/last-plan.json", expected,
                                     source_sha256=SOURCE_SHA256, manifest_sha256="c" * 64)

    def test_source_hash_mismatch_is_rejected(self):
        manifest = self._manifest(source_sha256="b" * 64)

        with self.assertRaisesRegex(ReplayInputError, "manifest source") as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "source_mismatch")

    def test_expected_source_hash_mismatch_is_rejected(self):
        manifest = self._manifest()

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root, expected_source_sha256="b" * 64)
        self.assertEqual(raised.exception.code, "source_mismatch")

    def test_stale_manifest_hash_is_rejected(self):
        manifest = self._manifest()
        capture_path = self.root / "initial-baseline/capture.json"
        capture_path.write_bytes(capture_path.read_bytes() + b"\n")

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "hash_mismatch")

    def test_path_escape_is_rejected(self):
        manifest = self._manifest()
        manifest["base"]["folder"] = "../outside"

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "path_outside_root")

    def test_invalid_merge_kind_is_rejected(self):
        manifest = self._manifest(inspections=[self._make_inspection(merge="unknown")])

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "invalid_merge_kind")

    def test_inspection_source_frame_hash_mismatch_is_rejected(self):
        inspection = self._make_inspection()
        raw_path = self.root / "receipt-recovery-v1/receipt-inspection/window/frame-000001.v2.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        raw["source_frame_sha256"] = "f" * 64
        raw_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(self._manifest(inspections=[inspection]), self.root)
        self.assertEqual(raised.exception.code, "hash_mismatch")

    def test_inspection_window_timestamp_out_of_range_is_rejected(self):
        inspection = self._make_inspection()
        manifest_path = self.root / "receipt-recovery-v1/receipt-inspection.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["windows"] = [{"start_ms": 4000, "end_ms": 6000}]
        manifest_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        inspection["manifest_sha256"] = _sha256_file(manifest_path)

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(self._manifest(inspections=[inspection]), self.root)
        self.assertEqual(raised.exception.code, "timestamp_out_of_range")

    def test_missing_inspection_proof_is_rejected(self):
        inspection = self._make_inspection()
        evidence = self.root / "receipt-recovery-v1/receipt-inspection/window/frame-000001.png"
        evidence.unlink()

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(self._manifest(inspections=[inspection]), self.root)
        self.assertEqual(raised.exception.code, "file_missing")

    def test_raw_sidecar_refinement_paths_and_hashes_normalize(self):
        supplement = self._make_currency_supplement()
        manifest = self._manifest(supplements=[supplement])

        normalized = normalize_manifest(manifest, self.root)

        entry = normalized["supplements"][0]["entries"][0]
        self.assertEqual(entry["raw_path"], "initial-baseline/neural/part-000-frame-000001.json")
        self.assertEqual(entry["evidence_path"], "initial-baseline/gameplay/part-000-frame-000001.png")
        self.assertEqual(entry["raw_sha256"], supplement["entries"][0]["raw_sha256"])

    def test_generic_raw_sidecar_group_keeps_its_dispatch_name(self):
        supplement = self._make_currency_supplement()
        supplement["kind"] = "raw_sidecars"
        supplement["name"] = "currency-refinement"
        normalized = normalize_manifest(self._manifest(supplements=[supplement]), self.root)

        self.assertEqual(normalized["supplements"][0]["kind"], "raw_sidecars")
        self.assertEqual(normalized["supplements"][0]["name"], "currency-refinement")

    def test_stale_sidecar_hash_is_rejected(self):
        supplement = self._make_currency_supplement()
        supplement["entries"][0]["raw_sha256"] = "d" * 64
        manifest = self._manifest(supplements=[supplement])

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "hash_mismatch")

    def test_sidecar_timestamp_mismatch_is_rejected(self):
        supplement = self._make_currency_supplement()
        supplement["entries"][0]["source_timestamp_ms"] = 1001
        manifest = self._manifest(supplements=[supplement])

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "timestamp_mismatch")

    def test_sidecar_raw_evidence_mismatch_is_rejected(self):
        supplement = self._make_currency_supplement()
        manifest = self._manifest(supplements=[supplement])
        entry = supplement["entries"][0]
        raw_path = self.root / entry["raw_path"]
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        source_evidence = self.root / entry["evidence_path"]
        alternate_evidence = source_evidence.with_name("alternate-proof.png")
        shutil.copyfile(source_evidence, alternate_evidence)
        raw["evidence"] = "gameplay/alternate-proof.png"
        raw_path.write_text(json.dumps(raw, sort_keys=True), encoding="utf-8")
        raw_hash = _canonical_hash(raw)
        entry["raw_sha256"] = raw_hash
        sidecar_path = self.root / supplement["folder"] / entry["path"]
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        sidecar["raw_sha256"] = raw_hash
        sidecar_path.write_text(json.dumps(sidecar, sort_keys=True), encoding="utf-8")
        entry["sidecar_sha256"] = _sha256_file(sidecar_path)

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "path_mismatch")

    def test_accepted_report_values_are_rejected_but_hash_reference_is_allowed(self):
        manifest = self._manifest(reference={"accepted_report": {"gameplay_tracking": {}}})

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "accepted_report_values_forbidden")

        manifest = self._manifest(reference={"accepted_report_sha256": "e" * 64})
        normalized = normalize_manifest(manifest, self.root)
        self.assertEqual(normalized["reference"]["accepted_report_sha256"], "e" * 64)

    def test_accepted_report_value_cannot_hide_in_an_input_spec(self):
        manifest = self._manifest()
        manifest["base"]["accepted_report"] = {"gameplay_tracking": {}}

        with self.assertRaises(ReplayInputError) as raised:
            normalize_manifest(manifest, self.root)
        self.assertEqual(raised.exception.code, "invalid_manifest")


if __name__ == "__main__":
    unittest.main()
