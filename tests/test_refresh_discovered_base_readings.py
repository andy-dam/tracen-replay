"""Tests for the source-bound discovered base refresh helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO

from PIL import Image


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts/refresh_discovered_base_readings.py"
)
SPEC = importlib.util.spec_from_file_location("refresh_discovered_base_readings", SCRIPT)
refresh_module = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = refresh_module
SPEC.loader.exec_module(refresh_module)


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pane_hash(path: Path) -> str:
    with Image.open(path) as image:
        return hashlib.sha256(image.convert("RGB").tobytes()).hexdigest()


class StubReader:
    fingerprint = "frozen-test-reader-v1"
    models = {"det.onnx": "d" * 64, "rec.onnx": "e" * 64}

    def __init__(self) -> None:
        self.calls = 0

    def read(self, pane: Image.Image) -> dict:
        self.calls += 1
        return {
            "lines": [{"text": "Training", "confidence": 99.0, "box": [155, 1, 220, 30]}],
            "regions": {},
            "header": "Training",
            "result_grid": False,
            "current_grid": False,
            "engine_fingerprint": self.fingerprint,
            "model_sha256": self.models,
            "gameplay_sha256": hashlib.sha256(pane.tobytes()).hexdigest(),
        }


class NaNReader(StubReader):
    def read(self, pane: Image.Image) -> dict:
        value = super().read(pane)
        value["invalid_number"] = float("nan")
        return value


class RefreshFixture:
    def __init__(self) -> None:
        # The managed Windows test runner can deny access to its global temp
        # directory and to the evidence tree under .local. Keep the fixture
        # directly below the writable repository workspace so setup and
        # cleanup exercise the helper without an environment-specific ACL
        # dependency.
        test_temp_root = Path(__file__).resolve().parents[1]
        self.root = test_temp_root / f".refresh-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.old = self.root / "old"
        self.target = self.root / "target"
        self.snapshot = self.root / "snapshot"
        self.discovery_layout = self.root / "layout.json"
        self.discovery_state = self.root / "state.json"
        for root in (self.old, self.target):
            (root / "part-000/frames").mkdir(parents=True)
            (root / "gameplay").mkdir()
            (root / "neural").mkdir()
        source_sha = "a" * 64
        self.source_sha = source_sha
        frame_id = "part-000-frame-000001"
        self.frame_id = frame_id
        source_path = self.old / "part-000/frames/000001.png"
        with Image.new("RGB", (1920, 1080), (20, 40, 60)) as image:
            image.save(source_path)
        shutil.copy2(source_path, self.target / "part-000/frames/000001.png")
        with Image.open(source_path) as image:
            pane = image.convert("RGB").crop((148, 0, 958, 1080))
            gameplay_path = self.old / f"gameplay/{frame_id}.png"
            pane.save(gameplay_path)
        shutil.copy2(gameplay_path, self.target / f"gameplay/{frame_id}.png")
        self.source_frame_sha = _file_hash(source_path)
        self.gameplay_sha = _pane_hash(gameplay_path)
        self.old_raw = {
            "lines": [],
            "regions": {},
            "header": "",
            "result_grid": False,
            "current_grid": False,
            "engine_fingerprint": "old-reader",
            "model_sha256": {"old.onnx": "b" * 64},
            "gameplay_sha256": self.gameplay_sha,
            "source_frame_sha256": self.source_frame_sha,
            "source_timestamp_ms": 100,
            "evidence": f"gameplay/{frame_id}.png",
        }
        old_raw_path = self.old / f"neural/{frame_id}.json"
        old_raw_path.write_text(json.dumps(self.old_raw), encoding="utf-8")
        shutil.copy2(old_raw_path, self.target / f"neural/{frame_id}.json")
        self.old_raw_path = old_raw_path
        capture = {
            "schema_version": "tracen-replay/full-recording-v1",
            "source": {"sha256": source_sha},
            "frames": [
                {
                    "id": frame_id,
                    "source_timestamp_ms": 100,
                    "evidence": "part-000/frames/000001.png",
                }
            ],
        }
        self._write_manifest_tree(self.old, capture, source_sha)
        self._write_manifest_tree(self.target, capture, source_sha)
        self._make_snapshot()
        candidate = {
            "raw": str(old_raw_path),
            "raw_sha256": _file_hash(old_raw_path),
            "source_timestamp_ms": 100,
            "evidence": f"gameplay/{frame_id}.png",
            "source_sha256": source_sha,
            "source_frame_sha256": self.source_frame_sha,
            "gameplay_sha256": self.gameplay_sha,
            "layout_proof": {"status": "recognized"},
        }
        record = {
            "recording": "v1",
            "root": str(self.old),
            "base": str(self.old),
            "neural": str(self.old / "neural"),
            "scanned_rows": 1,
            "new_result_candidates": 1,
            "candidates": [candidate],
        }
        self.discovery_layout.write_text(
            json.dumps(
                {
                    "scope": "Raw OCR layout candidate discovery only",
                    "helper_sha256": self.layout_sha,
                    "helper_unchanged": True,
                    "recordings": [record],
                }
            ),
            encoding="utf-8",
        )
        current_record = dict(record)
        current_record.pop("new_result_candidates")
        current_record["new_current_state_candidates"] = 1
        self.discovery_state.write_text(
            json.dumps(
                {
                    "scope": "Candidate discovery only",
                    "helper_hashes": {
                        str(self.snapshot / "repo/tracen_replay/current_state_layout.py"): self.state_sha,
                        str(self.snapshot / "repo/tracen_replay/training_result_layout.py"): self.layout_sha,
                    },
                    "helpers_unchanged": True,
                    "recordings": [current_record],
                }
            ),
            encoding="utf-8",
        )

    def _write_manifest_tree(self, root: Path, capture: dict, source_sha: str) -> None:
        capture_path = root / "capture.json"
        capture_path.write_text(json.dumps(capture), encoding="utf-8")
        manifest = {
            "schema_version": "tracen-replay/replay-input-manifest-v1",
            "source_sha256": source_sha,
            "base": {
                "folder": "",
                "capture": "capture.json",
                "capture_sha256": _file_hash(capture_path),
                "neural": "neural",
                "frame_count": 1,
            },
            "inspections": [],
            "recovery": [],
            "supplements": [],
        }
        (root / "replay-input-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def _make_snapshot(self) -> None:
        files = self.snapshot / "files/tracen_replay"
        files.mkdir(parents=True)
        contents = {
            "vision.py": "class NeuralReader: pass\n",
            "training_result_layout.py": "VERSION = 1\n",
            "current_state_layout.py": "VERSION = 1\n",
            "choice_evidence.py": (
                "def observe(pane, lines=(), include_slots=False):\n"
                "    return {\"offered_card_candidates\": [], "
                "\"selection_mark_pairs\": [], \"menu_text_complete\": False, "
                "\"selected_card_candidates\": [], \"selected_option\": None, "
                "\"selection_verified\": False}\n"
            ),
        }
        hashes = {}
        for name, content in contents.items():
            path = files / name
            path.write_text(content, encoding="utf-8")
            hashes[f"tracen_replay/{name}"] = _file_hash(path)
        manifest = {
            "schema_version": "tracen-replay/implementation-snapshot-v1",
            "files": hashes,
        }
        manifest_path = self.snapshot / "code-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.snapshot_hash = _file_hash(manifest_path)
        self.layout_sha = hashes["tracen_replay/training_result_layout.py"]
        self.state_sha = hashes["tracen_replay/current_state_layout.py"]

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


class RefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RefreshFixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def _refresh(self, **kwargs):
        f = self.fixture
        return refresh_module.refresh(
            f.snapshot,
            f.snapshot_hash,
            f.target,
            "v1",
            [f.discovery_layout, f.discovery_state],
            **kwargs,
        )

    def test_preflight_is_source_bound_and_does_not_call_reader(self) -> None:
        plan = self._refresh()
        self.assertEqual(plan["candidate_count"], 1)
        self.assertEqual(plan["selected_count"], 1)
        self.assertFalse(plan["ocr_executed"])
        self.assertFalse(plan["accepted_report_values_loaded"])
        self.assertEqual(StubReader().calls, 0)
        self.assertEqual(_file_hash(self.fixture.target / f"neural/{self.fixture.frame_id}.json"), _file_hash(self.fixture.old_raw_path))

    def test_execute_replaces_only_clone_raw_and_records_provenance(self) -> None:
        old_raw_hash = _file_hash(self.fixture.old_raw_path)
        reader = StubReader()
        plan = self._refresh(reader=reader, execute=True)
        self.assertEqual(reader.calls, 1)
        self.assertTrue(plan["ocr_executed"])
        self.assertEqual(plan["refreshed_count"], 1)
        target_raw = self.fixture.target / f"neural/{self.fixture.frame_id}.json"
        refreshed = json.loads(target_raw.read_text(encoding="utf-8"))
        self.assertEqual(refreshed["engine_fingerprint"], reader.fingerprint)
        self.assertEqual(refreshed["source_timestamp_ms"], 100)
        self.assertEqual(refreshed["source_frame_sha256"], self.fixture.source_frame_sha)
        self.assertEqual(_file_hash(self.fixture.old_raw_path), old_raw_hash)
        self.assertTrue((self.fixture.target / "discovered-base-reading-refresh-v1.json").is_file())
        journal_path = Path(plan["progress_journal_path"])
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["schema_version"], refresh_module.PROGRESS_SCHEMA)
        self.assertEqual(journal["status"], "completed")
        self.assertEqual(journal["failure"], None)
        self.assertEqual(journal["selected_frames"][0]["status"], "written")
        self.assertEqual(journal["writes_completed"][0]["kind"], "neural_raw")
        self.assertEqual(plan["progress_journal_sha256"], _file_hash(journal_path))

    def test_original_prepared_root_is_refused(self) -> None:
        f = self.fixture
        with self.assertRaises(refresh_module.RefreshError) as raised:
            refresh_module.refresh(
                f.snapshot,
                f.snapshot_hash,
                f.old,
                "v1",
                [f.discovery_layout],
            )
        self.assertEqual(raised.exception.code, "original_root")

    def test_discovery_helper_must_match_snapshot(self) -> None:
        f = self.fixture
        report = json.loads(f.discovery_layout.read_text(encoding="utf-8"))
        report["helper_sha256"] = "f" * 64
        f.discovery_layout.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "discovery_helper_mismatch")

    def test_changed_old_raw_is_refused_before_reader(self) -> None:
        f = self.fixture
        f.old_raw_path.write_text(json.dumps({"changed": True}), encoding="utf-8")
        reader = StubReader()
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(reader=reader, execute=True)
        self.assertEqual(raised.exception.code, "old_raw_hash_mismatch")
        self.assertEqual(reader.calls, 0)

    def test_sidecar_binding_is_reported_and_refused(self) -> None:
        f = self.fixture
        sidecar = f.target / "weak-state-recovery/conflict.json"
        sidecar.parent.mkdir()
        sidecar.write_text(
            json.dumps({"raw_sha256": _file_hash(f.old_raw_path)}), encoding="utf-8"
        )
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "sidecar_conflict")
        self.assertEqual(raised.exception.details[0]["frame_id"], f.frame_id)

    def test_semantic_raw_fingerprint_binding_is_reported(self) -> None:
        f = self.fixture
        fingerprint = refresh_module._raw_identity_fingerprints(f.old_raw)[
            "raw_fingerprint_default_ascii"
        ]
        sidecar = f.target / "receipt-inspection/conflict.json"
        sidecar.parent.mkdir()
        sidecar.write_text(json.dumps({"source_row_fingerprint": fingerprint}), encoding="utf-8")
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "sidecar_conflict")
        self.assertEqual(raised.exception.details[0]["reason"], "raw_fingerprint_default_ascii")

    def test_same_image_sidecar_binding_is_preserved(self) -> None:
        f = self.fixture
        sidecar = f.target / "training-inspection/100/frame-000001.v2.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps(
                {
                    "source_timestamp_ms": 100,
                    "evidence": f"gameplay/{f.frame_id}.png",
                    "source_frame_evidence": "part-000/frames/000001.png",
                    "gameplay_crop_path": f"gameplay/{f.frame_id}.png",
                    "source_frame_sha256": f.source_frame_sha,
                    "gameplay_sha256": f.gameplay_sha,
                }
            ),
            encoding="utf-8",
        )
        plan = self._refresh()
        self.assertEqual(plan["sidecar_conflicts"], [])
        self.assertEqual(plan["image_bound_sidecar_count"], 1)
        self.assertTrue(plan["image_bound_sidecar_examples"])

    def test_source_frame_evidence_is_binding_context_for_image_sidecar(self) -> None:
        f = self.fixture
        sidecar = f.target / "training-inspection/source-frame-only.v2.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps(
                {
                    "source_frame_sha256": f.source_frame_sha,
                    "source_frame_evidence": "part-000/frames/000001.png",
                }
            ),
            encoding="utf-8",
        )
        plan = self._refresh()
        self.assertEqual(plan["sidecar_conflicts"], [])
        self.assertEqual(plan["image_bound_sidecar_count"], 1)
        self.assertEqual(plan["image_bound_sidecar_examples"][0]["field"], "source_frame_sha256")

    def test_unstructured_image_hash_is_refused(self) -> None:
        f = self.fixture
        sidecar = f.target / "training-inspection/unbound.json"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text(
            json.dumps({"source_frame_sha256": f.source_frame_sha}),
            encoding="utf-8",
        )
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "sidecar_conflict")
        self.assertEqual(raised.exception.details[0]["reason"], "unverified_image_binding")

    def test_strict_reader_payload_fails_before_replacement(self) -> None:
        f = self.fixture
        target_raw = f.target / f"neural/{f.frame_id}.json"
        before = _file_hash(target_raw)
        reader = NaNReader()
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(reader=reader, execute=True)
        self.assertEqual(raised.exception.code, "reader_invalid")
        self.assertEqual(_file_hash(target_raw), before)
        self.assertFalse((f.target / "discovered-base-reading-refresh-v1.json").exists())
        self.assertFalse(
            (f.target / "reports/refresh-journals/discovered-base-reading-refresh-v1.progress.json").exists()
        )

    def _install_choice_dependency(self) -> tuple[Path, dict]:
        f = self.fixture
        sidecar = f.target / "choice-refinement" / f"{f.frame_id}.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        old_choice = {
            "version": 2,
            "raw_sha256": refresh_module._choice_raw_fingerprint(f.old_raw),
            "evidence_sha256": _file_hash(f.target / f"gameplay/{f.frame_id}.png"),
            "observation": {
                "offered_card_candidates": [],
                "selection_mark_pairs": [],
                "menu_text_complete": False,
                "selected_card_candidates": [],
                "selected_option": None,
                "selection_verified": False,
            },
            "independent_observations": False,
        }
        sidecar.write_text(json.dumps(old_choice, indent=2) + "\n", encoding="utf-8")
        manifest_path = f.target / "replay-input-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["supplements"] = [
            {
                "kind": "raw_sidecars",
                "name": "choice",
                "folder": "choice-refinement",
                "entries": [
                    {
                        "path": sidecar.name,
                        "sidecar_sha256": _file_hash(sidecar),
                        "raw_path": f"neural/{f.frame_id}.json",
                        "raw_sha256": old_choice["raw_sha256"],
                        "evidence_path": f"gameplay/{f.frame_id}.png",
                        "evidence_sha256": old_choice["evidence_sha256"],
                        "source_timestamp_ms": f.old_raw["source_timestamp_ms"],
                    }
                ],
            }
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return sidecar, old_choice

    def test_choice_dependency_mode_allows_only_registered_choice_rows(self) -> None:
        sidecar, old_choice = self._install_choice_dependency()
        before_sidecar = sidecar.read_bytes()
        plan = self._refresh(refresh_choice_dependencies=True)
        self.assertEqual(plan["choice_dependency_refresh"]["frames"], [self.fixture.frame_id])
        self.assertEqual(plan["choice_dependency_refresh"]["conflict_count"], 5)
        self.assertEqual(sidecar.read_bytes(), before_sidecar)
        self.assertEqual(plan["ocr_executed"], False)
        self.assertEqual(old_choice["raw_sha256"], refresh_module._choice_raw_fingerprint(self.fixture.old_raw))

    def test_choice_dependency_mode_recomputes_sidecar_and_manifest_in_clone(self) -> None:
        sidecar, _old_choice = self._install_choice_dependency()
        old_sidecar_bytes = sidecar.read_bytes()
        old_manifest = (self.fixture.target / "replay-input-manifest.json").read_bytes()
        reader = StubReader()
        plan = self._refresh(
            reader=reader,
            execute=True,
            refresh_choice_dependencies=True,
            audit_name="choice-refresh-audit.json",
        )
        self.assertEqual(reader.calls, 1)
        self.assertEqual(plan["choice_dependency_refresh"]["refreshed_count"], 1)
        refreshed_raw = json.loads(
            (self.fixture.target / f"neural/{self.fixture.frame_id}.json").read_text(encoding="utf-8")
        )
        refreshed_sidecar = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual(
            refreshed_sidecar["raw_sha256"],
            refresh_module._choice_raw_fingerprint(refreshed_raw),
        )
        self.assertNotEqual(old_sidecar_bytes, sidecar.read_bytes())
        new_manifest = json.loads(
            (self.fixture.target / "replay-input-manifest.json").read_text(encoding="utf-8")
        )
        entry = new_manifest["supplements"][0]["entries"][0]
        self.assertEqual(entry["raw_sha256"], refreshed_sidecar["raw_sha256"])
        self.assertEqual(entry["sidecar_sha256"], _file_hash(sidecar))
        self.assertEqual(
            plan["choice_dependency_refresh"]["observer_sha256"],
            _file_hash(self.fixture.snapshot / "files/tracen_replay/choice_evidence.py"),
        )
        archive_root = Path(plan["choice_dependency_refresh"]["archive_root"])
        self.assertEqual(
            (archive_root / "choice-refinement" / sidecar.name).read_bytes(),
            old_sidecar_bytes,
        )
        self.assertEqual(
            (archive_root / "replay-input-manifest.json").read_bytes(),
            old_manifest,
        )
        self.assertTrue((self.fixture.target / "choice-refresh-audit.json").is_file())

    def test_choice_manifest_sidecar_hash_is_validated_before_reader(self) -> None:
        f = self.fixture
        self._install_choice_dependency()
        manifest_path = f.target / "replay-input-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["supplements"][0]["entries"][0]["sidecar_sha256"] = "f" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        reader = StubReader()
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(reader=reader, execute=True, refresh_choice_dependencies=True)
        self.assertEqual(raised.exception.code, "choice_dependency_conflict")
        self.assertEqual(reader.calls, 0)
        self.assertFalse(
            (f.target / "reports/refresh-journals/discovered-base-reading-refresh-v1.progress.json").exists()
        )

    def test_choice_manifest_evidence_hash_is_validated_before_reader(self) -> None:
        f = self.fixture
        self._install_choice_dependency()
        manifest_path = f.target / "replay-input-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["supplements"][0]["entries"][0]["evidence_sha256"] = "f" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        reader = StubReader()
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(reader=reader, execute=True, refresh_choice_dependencies=True)
        self.assertEqual(raised.exception.code, "choice_dependency_conflict")
        self.assertEqual(reader.calls, 0)

    def test_source_frame_change_is_rejected_before_progress_journal(self) -> None:
        f = self.fixture
        target_source = f.target / "part-000/frames/000001.png"
        with Image.new("RGB", (1920, 1080), (21, 41, 61)) as image:
            image.save(target_source)
        reader = StubReader()
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(reader=reader, execute=True)
        self.assertEqual(raised.exception.code, "target_source_mismatch")
        self.assertEqual(reader.calls, 0)
        self.assertFalse(
            (f.target / "reports/refresh-journals/discovered-base-reading-refresh-v1.progress.json").exists()
        )

    def test_choice_sidecar_write_failure_leaves_durable_failed_journal(self) -> None:
        f = self.fixture
        sidecar, _old_choice = self._install_choice_dependency()
        old_raw_hash = _file_hash(f.old_raw_path)
        original_writer = refresh_module._write_json_replacement

        def fail_sidecar(path, payload, root, field):
            if field.startswith("choice-refinement sidecar"):
                raise refresh_module.RefreshError("injected_write_failure", "injected choice sidecar failure")
            return original_writer(path, payload, root, field)

        refresh_module._write_json_replacement = fail_sidecar
        try:
            reader = StubReader()
            with self.assertRaises(refresh_module.RefreshError) as raised:
                self._refresh(
                    reader=reader,
                    execute=True,
                    refresh_choice_dependencies=True,
                    audit_name="choice-write-failure.json",
                )
        finally:
            refresh_module._write_json_replacement = original_writer
        self.assertEqual(raised.exception.code, "injected_write_failure")
        self.assertEqual(reader.calls, 1)
        journal_path = f.target / "reports/refresh-journals/choice-write-failure.progress.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["status"], "failed")
        self.assertEqual(journal["failure"]["code"], "injected_write_failure")
        self.assertEqual(journal["archive"]["status"], "completed")
        self.assertEqual(journal["selected_frames"][0]["status"], "written")
        self.assertEqual(journal["choice_sidecars"][0]["status"], "failed")
        self.assertEqual(journal["writes_completed"][0]["kind"], "neural_raw")
        self.assertNotEqual(_file_hash(f.target / f"neural/{f.frame_id}.json"), old_raw_hash)
        self.assertEqual(_file_hash(f.old_raw_path), old_raw_hash)
        self.assertFalse((f.target / "choice-write-failure.json").exists())

    def test_choice_dependency_mode_keeps_non_choice_conflicts_fail_closed(self) -> None:
        f = self.fixture
        sidecar = f.target / "weak-state-recovery/conflict.json"
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(
            json.dumps({"raw_sha256": _file_hash(f.old_raw_path)}), encoding="utf-8"
        )
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh(refresh_choice_dependencies=True)
        self.assertEqual(raised.exception.code, "sidecar_conflict")

    def test_cli_defers_reader_creation_to_refresh(self) -> None:
        captured = {}
        original_refresh = refresh_module.refresh

        def fake_refresh(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"schema_version": refresh_module.SCHEMA, "ok": True}

        refresh_module.refresh = fake_refresh
        try:
            output = StringIO()
            with redirect_stdout(output):
                status = refresh_module.main(
                    [
                        "--snapshot",
                        "snapshot",
                        "--snapshot-manifest-sha256",
                        "a" * 64,
                        "--root",
                        "target",
                        "--recording",
                        "v1",
                        "--discovery",
                        "discovery.json",
                        "--model-dir",
                        "models",
                        "--execute",
                        "--refresh-choice-dependencies",
                    ]
                )
        finally:
            refresh_module.refresh = original_refresh
        self.assertEqual(status, 0)
        self.assertNotIn("reader", captured["kwargs"])
        self.assertEqual(captured["kwargs"]["model_dir"], Path("models"))
        self.assertTrue(captured["kwargs"]["execute"])
        self.assertTrue(captured["kwargs"]["refresh_choice_dependencies"])

    def test_discovery_declared_gameplay_hash_must_match_raw(self) -> None:
        f = self.fixture
        report = json.loads(f.discovery_layout.read_text(encoding="utf-8"))
        report["recordings"][0]["candidates"][0]["gameplay_sha256"] = "f" * 64
        f.discovery_layout.write_text(json.dumps(report), encoding="utf-8")
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "candidate_binding_mismatch")

    def test_accepted_report_is_not_read_as_a_sidecar(self) -> None:
        f = self.fixture
        (f.target / "report.json").write_text("{not valid JSON", encoding="utf-8")
        plan = self._refresh()
        self.assertEqual(plan["candidate_count"], 1)

    def test_target_source_pixels_must_match_old_binding(self) -> None:
        f = self.fixture
        with Image.new("RGB", (1920, 1080), (1, 2, 3)) as image:
            image.save(f.target / "part-000/frames/000001.png")
        with self.assertRaises(refresh_module.RefreshError) as raised:
            self._refresh()
        self.assertEqual(raised.exception.code, "target_source_mismatch")


if __name__ == "__main__":
    unittest.main()

