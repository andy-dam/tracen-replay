import hashlib
import json
from pathlib import Path
import unittest

from tools.bundle_final_source_reviews import (
    BundleError,
    build_bundle,
    digest,
    verify_bundle,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BundleFinalSourceReviewsTests(unittest.TestCase):
    def setUp(self):
        # The managed Windows test runner applies restrictive ACLs to
        # directories created by Python.  Keep a checked-in fixture directory
        # and only replace files inside it, which also keeps the tests fast.
        self.root = Path(__file__).parent / "fixtures" / "bundle-final-source-reviews"
        self.cache = self.root / "cache"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.cache / "frames").mkdir(parents=True, exist_ok=True)
        for path in (
            self.root / "review.json",
            self.root / "bundle.json",
            self.root / "recording.mp4",
            self.root / "replay-config.json",
            self.root / "source-review-generator.py",
            self.cache / "frame-000001.png",
            self.cache / "frame-000001.v2.json",
            self.cache / "frame-000002.png",
            self.cache / "frame-000002.v2.json",
            self.cache / "unmapped.png",
            self.cache / "frames.json",
            self.cache / "frames" / "000001.jpg",
            self.cache / "source-observations-v3.json",
        ):
            path.unlink(missing_ok=True)
        self.crop = self.cache / "frame-000001.png"
        self.crop.write_bytes(b"gameplay-crop")
        self.raw = self.cache / "frames" / "000001.jpg"
        self.raw.write_bytes(b"native-source-frame")
        self.frames_manifest = self.cache / "frames.json"
        self.frames_manifest.write_text(json.dumps([
            {
                "id": "frame-000001",
                "source_timestamp_ms": 123,
                "evidence": "frames/000001.jpg",
            }
        ]), encoding="utf-8")
        self.sidecar = self.cache / "frame-000001.v2.json"
        self.sidecar.write_text(json.dumps({
            "source_timestamp_ms": 123,
            "evidence": "cache/frame-000001.png",
        }), encoding="utf-8")
        self.video = self.root / "recording.mp4"
        self.video.write_bytes(b"small test video")
        self.config = self.root / "replay-config.json"
        self.config.write_text(json.dumps({"cache_root": "cache"}), encoding="utf-8")

    def tearDown(self):
        for path in (
            self.root / "review.json",
            self.root / "bundle.json",
            self.root / "recording.mp4",
            self.root / "replay-config.json",
            self.root / "source-review-generator.py",
            self.cache / "frame-000001.png",
            self.cache / "frame-000001.v2.json",
            self.cache / "frame-000002.png",
            self.cache / "frame-000002.v2.json",
            self.cache / "unmapped.png",
            self.cache / "frames.json",
            self.cache / "frames" / "000001.jpg",
            self.cache / "source-observations-v3.json",
        ):
            path.unlink(missing_ok=True)

    def _review(self, *, timestamp=123, crop_hash=None, path=None):
        return {
            "schema_version": "tracen-replay/test-source-review-v1",
            "run": "run-a",
            "evidence_root": "cache",
            "source_video": {
                "path": str(self.video),
                "sha256": _sha(self.video),
            },
            "replay_config": "replay-config.json",
            "replay_config_sha256": _sha(self.config),
            "source_evidence": [{
                "timestamp_ms": timestamp,
                "path": path or "cache/frame-000001.png",
                "sha256": crop_hash or _sha(self.crop),
                "raw_sidecar": "cache/frame-000001.v2.json",
                "source_frame_sha256": _sha(self.raw),
            }],
        }

    def _write_review(self, payload, name="review.json"):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_binds_images_sidecar_manifest_raw_frame_config_and_video_once(self):
        review = self._write_review(self._review())
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root,
                                hash_source_video=True)

        immutable = manifest["immutable_files_sha256"]
        for path in (review, self.crop, self.sidecar, self.frames_manifest,
                     self.raw, self.config, self.video):
            self.assertEqual(immutable[str(path.resolve())], _sha(path))
        self.assertEqual(len(manifest["source_videos"]), 1)
        self.assertEqual(manifest["source_videos"][0]["verification"],
                         "streamed_and_verified")
        crop_binding = next(row for row in manifest["evidence_bindings"]
                            if row["path"] == str(self.crop.resolve()))
        self.assertEqual(crop_binding["timestamp_verification"]["status"], "bound")
        self.assertTrue(any(row["path"] == str(self.raw.resolve())
                            for row in manifest["evidence_bindings"]))
        self.assertEqual(verify_bundle(output)["schema_version"], manifest["schema_version"])

    def test_hash_mismatch_fails_before_writing_output(self):
        review = self._write_review(self._review(crop_hash="0" * 64))
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "Hash mismatch"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_verify_checks_streamed_source_video_binding(self):
        review = self._write_review(self._review())
        output = self.root / "bundle.json"
        manifest = build_bundle(review, output, repo_root=self.root,
                                hash_source_video=True)
        manifest["source_videos"][0]["sha256"] = "0" * 64
        output.write_text(json.dumps(manifest), encoding="utf-8")

        with self.assertRaisesRegex(BundleError, "Verified source video hash is stale"):
            verify_bundle(output)

    def test_missing_and_parent_traversal_references_fail(self):
        review = self._write_review(self._review(path="../missing.png"))
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "Parent traversal"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

        review.unlink()
        review = self._write_review(self._review(path="cache/missing.png"))
        with self.assertRaisesRegex(BundleError, "File reference is missing"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_timestamp_mismatch_is_not_accepted_from_review_assertion(self):
        review = self._write_review(self._review(timestamp=124))
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "Timestamp mismatch"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_scalar_timestamp_is_not_reused_for_aggregate_evidence_list(self):
        (self.cache / "unmapped.png").write_bytes(b"aggregate evidence")
        payload = self._review()
        payload["aggregate_observation"] = {
            "source_timestamp_ms": 123,
            "evidence": ["cache/frame-000001.png", "cache/unmapped.png"],
        }
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root)
        aggregate_path = str((self.cache / "unmapped.png").resolve())
        aggregate_binding = next(row for row in manifest["evidence_bindings"]
                                 if row["path"] == aggregate_path)
        self.assertIsNone(aggregate_binding["timestamp_ms"])

    def test_parent_timestamp_binds_single_nested_evidence_object(self):
        payload = self._review()
        payload["nested_observation"] = {
            "timestamp_ms": 123,
            "source_evidence": {
                "path": "cache/frame-000001.png",
                "sha256": _sha(self.crop),
                "raw_sidecar": "cache/frame-000001.v2.json",
                "source_frame_sha256": _sha(self.raw),
            },
        }
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root)
        binding = next(row for row in manifest["evidence_bindings"]
                       if row["path"] == str(self.crop.resolve()) and
                       any("nested_observation.source_evidence.path" in pointer
                           for pointer in row["json_pointers"]))
        self.assertEqual(binding["timestamp_ms"], 123)
        self.assertEqual(
            binding["timestamp_verification"]["inherited_from_record"],
            "$.nested_observation",
        )

    def test_aligned_timestamp_list_can_be_verified_by_capture_time_set(self):
        second = self.cache / "frame-000002.png"
        second.write_bytes(b"second gameplay crop")
        (self.cache / "frame-000002.v2.json").write_text(json.dumps({
            "source_timestamp_ms": 124,
            "evidence": "cache/frame-000002.png",
        }), encoding="utf-8")
        payload = self._review()
        payload["aligned_observation"] = {
            # Deliberately reversed relative to the image paths.  The review
            # still supplies the complete timestamp set for this evidence.
            "evidence": ["cache/frame-000002.png", "cache/frame-000001.png"],
            "evidence_source_timestamp_ms": [123, 124],
        }
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root)
        binding = next(row for row in manifest["evidence_bindings"]
                       if row["path"] == str(second.resolve()))
        self.assertEqual(binding["timestamp_ms"], 124)
        self.assertEqual(binding["timestamp_verification"]["status"],
                         "bound_by_timestamp_set")
        self.assertEqual(binding["timestamp_verification"]["declared_timestamp_ms"], 123)

    def test_unmapped_timestamp_fails_closed(self):
        image = self.cache / "unmapped.png"
        image.write_bytes(b"unmapped")
        payload = self._review(path="cache/unmapped.png")
        payload["source_evidence"][0]["sha256"] = _sha(image)
        payload["source_evidence"][0].pop("source_frame_sha256")
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "not bound"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_hash_without_a_path_reference_fails_closed(self):
        payload = self._review()
        payload["unbound_source_proof"] = {
            "independent_02_source_observations_sha256": "a" * 64,
        }
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "Hash-only reference is not uniquely bound"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_video_hashing_deduplicates_repeated_path(self):
        payload = self._review()
        payload["source_video"] = [
            {"path": str(self.video), "sha256": _sha(self.video)},
            {"path": str(self.video), "sha256": _sha(self.video)},
        ]
        # Repeated source-video objects exercise path-level deduplication.
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root,
                                hash_source_video=True)
        self.assertEqual(len(manifest["source_videos"]), 1)
        self.assertEqual(str(self.video.resolve()),
                         next(iter(path for path in manifest["immutable_files_sha256"]
                                   if path == str(self.video.resolve()))))

    def test_conflicting_source_video_declarations_fail_closed(self):
        payload = self._review()
        payload["source_video"] = [
            {"path": str(self.video), "sha256": _sha(self.video)},
            {"path": str(self.video), "sha256": "0" * 64},
        ]
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        with self.assertRaisesRegex(BundleError, "Conflicting source-video hashes"):
            build_bundle(review, output, repo_root=self.root)
        self.assertFalse(output.exists())

    def test_prefixed_source_artifact_and_generator_hashes_are_bound(self):
        source_observations = self.cache / "source-observations-v3.json"
        source_observations.write_bytes(b"sampled source observations")
        payload = self._review()
        payload["terminal_source"] = {
            "sampled_source_artifact": "cache/source-observations-v3.json",
            "independent_02_source_observations_sha256": _sha(source_observations),
        }
        payload["hash_proofs"] = {
            "independent_02_source_observations_sha256": _sha(source_observations),
        }
        payload["derived_artifact"] = {
            "artifact_path": "replay-config.json",
            "artifact_source_sha256": _sha(self.video),
        }
        script = self.root / "source-review-generator.py"
        script.write_bytes(b"generator source")
        payload["generator"] = {"script": str(script), "script_sha256": _sha(script)}
        review = self._write_review(payload)
        output = self.root / "bundle.json"

        manifest = build_bundle(review, output, repo_root=self.root,
                                hash_source_video=True)

        immutable = manifest["immutable_files_sha256"]
        self.assertEqual(immutable[str(source_observations.resolve())], _sha(source_observations))
        self.assertEqual(immutable[str(script.resolve())], _sha(script))
        self.assertTrue(any(
            "hash_proofs.independent_02_source_observations_sha256" in pointer
            for row in manifest["evidence_bindings"]
            for pointer in row["json_pointers"]
        ))
        video = next(row for row in manifest["source_videos"]
                     if row["path"] == str(self.video.resolve()))
        self.assertIn(_sha(self.video), video["declared_sha256"])


if __name__ == "__main__":
    unittest.main()
