"""Tests of ``tests.test_hint_card_cache`` that need locally preserved evidence; they run only where it is."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from tests import localdata
from tracen_replay.hint_card_cache import load


class ActualHintCardCacheTests(unittest.TestCase):
    @staticmethod
    def _prepared_cache_rows(name):
        root = localdata.root("prepared_snapshot_final", name)
        capture_path = root / "capture.json"
        cache_path = root / "hint-card-recovery.json"
        if not capture_path.is_file() or not cache_path.is_file():
            return None
        capture = json.loads(capture_path.read_text(encoding="utf-8"))
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        times = sorted(
            {
                observation["timestamp_ms"]
                for candidate in cache["candidates"]
                for observation in candidate["cache_provenance"]["observations"]
            }
        )
        frames = [
            frame
            for frame in capture["frames"]
            if frame.get("source_timestamp_ms") in times
        ]
        from tracen_replay.full_recording import cached_readings

        return (
            root,
            capture,
            cache_path,
            cached_readings(dict(capture, frames=frames), root),
        )


    def test_actual_row_metadata_is_bound_before_hash_compatibility(self):
        prepared = self._prepared_cache_rows("independent-01")
        if prepared is None:
            self.skipTest("preserved independent-01 hint-card cache fixture is unavailable")
        root, capture, cache_path, rows = prepared
        mutations = {
            "source_sha256": "f" * 64,
            "source_frame_id": "part-000-frame-000000",
            "source_frame_path": "part-000/frames/000000.jpg",
            "source_frame_sha256": "f" * 64,
            "gameplay_sha256": "f" * 64,
            "engine_fingerprint": "f" * 64,
        }
        model = deepcopy(rows[0]["model_sha256"])
        model[next(iter(model))] = "f" * 64
        mutations["model_sha256"] = model
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = deepcopy(rows)
                changed[0][field] = value
                with self.assertRaises(ValueError):
                    load(
                        changed,
                        root,
                        capture["source"]["sha256"],
                        cache_path=cache_path,
                    )

    def test_actual_prepared_caches_load_after_normal_reparse(self):
        for name in ("v1", "independent-01"):
            with self.subTest(root=name):
                prepared = self._prepared_cache_rows(name)
                if prepared is None:
                    self.skipTest("preserved hint-card cache fixture is unavailable")
                root, capture, cache_path, rows = prepared
                with patch(
                    "tracen_replay.vision.NeuralReader",
                    side_effect=AssertionError("cache load must not construct OCR"),
                ):
                    loaded = load(
                        rows,
                        root,
                        capture["source"]["sha256"],
                        cache_path=cache_path,
                    )
                self.assertEqual(
                    sorted((candidate["name"], candidate["amount"]) for candidate in loaded),
                    sorted(
                        (candidate["name"], candidate["amount"])
                        for candidate in json.loads(cache_path.read_text(encoding="utf-8"))["candidates"]
                    ),
                )

    def test_actual_full_readings_allow_registered_inspection_namespace(self):
        """Unrelated bound inspection rows must not poison hint replay.

        The normal replay bundle merges base capture rows with registered
        inspection namespaces.  This uses the real preserved inspection envelope
        from the prepared source: its ``frame-000015`` identity is valid for
        that inspection artifact, but intentionally has no entry in the base
        capture manifest.  The hint candidate still has to validate its own
        exact base rows and must replay without OCR.
        """
        inspection_paths = {
            "v1": Path("training-gain-source-refinement")
            / "7dd06ed3b5d4cda64eee4e91.json",
            "independent-01": Path("training-inspection")
            / "1026750"
            / "frame-000001.v2.json",
        }
        for name, relative_path in inspection_paths.items():
            with self.subTest(root=name):
                prepared = self._prepared_cache_rows(name)
                if prepared is None:
                    self.skipTest("preserved hint-card cache fixture is unavailable")
                root, capture, cache_path, rows = prepared
                envelope_path = root / relative_path
                if not envelope_path.is_file():
                    self.skipTest("preserved inspection envelope is unavailable")
                inspection_row = json.loads(
                    envelope_path.read_text(encoding="utf-8")
                )
                mixed_rows = [*rows, inspection_row]
                with patch(
                    "tracen_replay.vision.NeuralReader",
                    side_effect=AssertionError("cache load must not construct OCR"),
                ):
                    loaded = load(
                        mixed_rows,
                        root,
                        capture["source"]["sha256"],
                        cache_path=cache_path,
                    )
                expected = sorted(
                    (candidate["name"], candidate["amount"])
                    for candidate in json.loads(
                        cache_path.read_text(encoding="utf-8")
                    )["candidates"]
                )
                self.assertEqual(
                    sorted((candidate["name"], candidate["amount"]) for candidate in loaded),
                    expected,
                )

