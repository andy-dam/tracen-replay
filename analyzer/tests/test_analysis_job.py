import hashlib
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

from tests.test_gameplay import workspace_temp
from tests.test_report_contract import valid_report
from tracen_replay.analysis_job import JOB_SCHEMA, WORKER_VERSION_SCHEMA, main
from tracen_replay.pipeline import PipelineError


def _write_report(output, source, *, frame_evidence=None, observations=None):
    report = valid_report()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report["source"].update(
        name=source.name,
        sha256=source_hash,
        size_bytes=source.stat().st_size,
    )
    report["verification"] = {
        "source_sha256": source_hash,
        "full_source_processed": True,
        "fully_verified": False,
        "go_ready": False,
    }
    from tracen_replay.turn_ledger import build
    report["gameplay_tracking"]["readings"] = []
    report["gameplay_tracking"]["performance_accounting"] = {"checkpoints": [], "intervals": []}
    report["turn_ledger"] = build(report)
    if frame_evidence is not None:
        report["frames"][0]["evidence"] = frame_evidence
    if observations is not None:
        report["observations"] = observations
    output.mkdir(parents=True, exist_ok=True)
    from tracen_replay.analysis_job import _evidence_paths
    evidence_root = output.resolve()
    for _, evidence in _evidence_paths(report, "report"):
        path = (evidence_root / evidence).resolve(strict=False)
        try:
            path.relative_to(evidence_root)
        except ValueError:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"evidence")
    report_path = output / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


class AnalysisJobTests(unittest.TestCase):
    def test_evidence_validation_checks_each_exact_path_once_per_call(self):
        from tracen_replay.analysis_job import _validate_evidence_paths, _check_evidence_path
        with workspace_temp() as root:
            (root / 'frames').mkdir()
            (root / 'frames/x.png').write_bytes(b'evidence')
            payload = {'supporting_frames': ['frames/x.png'] * 100 + ['frames/./x.png']}
            with patch('tracen_replay.analysis_job._check_evidence_path', wraps=_check_evidence_path) as check:
                _validate_evidence_paths(payload, root)
                self.assertEqual(check.call_count, 2)
                self.assertEqual([call.args[0] for call in check.call_args_list],
                                 ['frames/x.png', 'frames/./x.png'])
                _validate_evidence_paths(payload, root)
                self.assertEqual(check.call_count, 4)

    def test_repeated_valid_evidence_does_not_hide_a_missing_distinct_path(self):
        from tracen_replay.analysis_job import _validate_evidence_paths, JobInputError
        with workspace_temp() as root:
            (root / 'x.png').write_bytes(b'evidence')
            payload = {'supporting_frames': ['x.png'] * 100 + ['missing.png']}
            with self.assertRaises(JobInputError) as failure:
                _validate_evidence_paths(payload, root)
            self.assertIn('supporting_frames[100]', str(failure.exception))

    def test_evidence_records_keep_name_alternatives_and_reasons_as_metadata(self):
        from pathlib import Path
        from tracen_replay.analysis_job import _validate_evidence_paths, _evidence_paths
        payload = {'hint_card_evidence': [{'raw_receipt_name_candidates': ['../uncertain name'],
            'observations': [{'evidence': 'frames/hint.png'}]}],
            'inheritance_occurrence_evidence': {'by_key': {'example': {
                'uncertainty_reasons': ['C:/not a file reference'], 'evidence': ['frames/receipt.png']}}}}
        with workspace_temp() as root:
            for relative in ('frames/hint.png', 'frames/receipt.png'):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'evidence')
            _validate_evidence_paths(payload, root)
        self.assertEqual({path for _, path in _evidence_paths(payload, 'report')},
                         {'frames/hint.png', 'frames/receipt.png'})

    def test_legacy_report_without_ledger_cannot_be_published_by_worker(self):
        with workspace_temp() as root:
            source = root / 'run.mp4'
            source.write_bytes(b'source')
            output = root / 'output'
            stdout = io.StringIO()

            def producer():
                path = _write_report(output, source)
                report = json.loads(path.read_text(encoding='utf-8'))
                del report['turn_ledger']
                path.write_text(json.dumps(report), encoding='utf-8')

            with patch('tracen_replay.analysis_job.full_recording.main', side_effect=producer), patch('sys.stdout', stdout):
                result = main([str(source), '--output', str(output)])
            self.assertEqual(result, 1)
            self.assertEqual(json.loads(stdout.getvalue())['error']['code'], 'missing_turn_ledger')

    def test_nested_field_evidence_and_checkpoint_proofs_cannot_escape_root(self):
        from tracen_replay.analysis_job import _validate_evidence_paths, JobInputError
        from pathlib import Path
        for payload in (
            {'field_evidence': {'stat_change|speed|': ['../../outside.png']}},
            {'field_evidence': {'speed': {'stable': ['../../outside.png']}}},
            {'supporting_frames': ['../../outside.png']},
        ):
            with self.subTest(payload=payload), self.assertRaisesRegex(JobInputError, 'inside the evidence root'):
                _validate_evidence_paths(payload, Path.cwd())

    def test_path_resolution_failure_is_structured_and_unicode_is_portable(self):
        stdout = io.StringIO()
        with patch('tracen_replay.analysis_job._paths', side_effect=OSError('Cannot open 馬')), patch('sys.stdout', stdout):
            result = main(['source.mp4', '--output', 'output'])
        self.assertEqual(result, 2)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload['error']['code'], 'invalid_paths')
        self.assertIn('馬', payload['error']['message'])
        stdout.getvalue().encode('ascii')

    def test_worker_version_answers_without_a_source_output_or_model(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            self.assertEqual(main(["--worker-version"]), 0)
        record = json.loads(stdout.getvalue())
        self.assertEqual(record["schema_version"], WORKER_VERSION_SCHEMA)
        self.assertEqual(sorted(record["worker_version"]), ["code_digest", "package"])
        self.assertRegex(record["worker_version"]["code_digest"], r"^[0-9a-f]{64}$")
        # It is the identity question, not a job, so it never claims a status.
        self.assertNotIn("status", record)
        # And it answers even where a job's own arguments would be rejected.
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            self.assertEqual(main(["--worker-version", "--output", "nowhere", "missing.mp4"]), 0)
        self.assertEqual(json.loads(stdout.getvalue())["schema_version"], WORKER_VERSION_SCHEMA)

    def test_success_emits_one_record_and_redirects_producer_progress(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            model_dir = root / "models"
            stdout = io.StringIO()
            stderr = io.StringIO()

            def producer():
                self.assertEqual(sys.argv[0], "tracen-replay-full-recording")
                self.assertIn("--reparse-only", sys.argv)
                self.assertIn("--fps", sys.argv)
                self.assertIn("2.5", sys.argv)
                self.assertIn("--workers", sys.argv)
                self.assertNotIn("--dense-workers", sys.argv)
                self.assertIn("3", sys.argv)
                self.assertIn(str(model_dir.resolve()), sys.argv)
                print("producer-progress")
                _write_report(output, source)

            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout), patch("sys.stderr", stderr):
                result = main([
                    str(source),
                    "--output", str(output),
                    "--fps", "2.5",
                    "--workers", "3",
                    "--model-dir", str(model_dir),
                    "--reparse-only",
                ])

            self.assertEqual(result, 0)
            self.assertEqual(stdout.getvalue().count("\n"), 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["schema_version"], JOB_SCHEMA)
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(payload["report_schema_version"], "tracen-replay/full-recording-v1")
            report_path = (output / "report.json").resolve()
            self.assertEqual(payload["report_path"], str(report_path))
            self.assertEqual(
                payload["report_sha256"],
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                payload["source_sha256"],
                hashlib.sha256(source.read_bytes()).hexdigest(),
            )
            self.assertEqual(payload["evidence_root"], str(output.resolve()))
            self.assertTrue(payload["full_source_processed"])
            self.assertFalse(payload["fully_verified"])
            self.assertFalse(payload["go_ready"])
            self.assertIn("producer-progress", stderr.getvalue())

    def test_manifest_arguments_forward_to_common_cached_producer(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            manifest = root / "replay-input-manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            stdout = io.StringIO()

            def producer():
                self.assertIn("--replay-input-manifest", sys.argv)
                self.assertIn(str(manifest.resolve()), sys.argv)
                self.assertIn("--replay-input-root", sys.argv)
                self.assertIn(str(output.resolve()), sys.argv)
                _write_report(output, source)

            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([
                    str(source),
                    "--output", str(output),
                    "--reparse-only",
                    "--replay-input-manifest", str(manifest),
                    "--replay-input-root", str(output),
                ])

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "succeeded")

    def test_learned_reader_forwards_to_the_producer_and_must_exist(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            model = root / "reader.onnx"
            model.write_bytes(b"onnx")
            stdout = io.StringIO()

            def producer():
                self.assertIn("--learned-reader", sys.argv)
                self.assertIn(str(model.resolve()), sys.argv)
                _write_report(output, source)

            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output), "--learned-reader", str(model)])
            self.assertEqual(result, 0)

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main") as never, patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(root / "other"), "--learned-reader", str(root / "missing.onnx")])
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "learned_reader_not_found")
            never.assert_not_called()

    def test_manifest_root_mismatch_is_rejected_before_producer(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            manifest = root / "replay-input-manifest.json"
            manifest.write_text("{}", encoding="utf-8")
            stdout = io.StringIO()

            with patch("tracen_replay.analysis_job.full_recording.main") as producer, \
                    patch("sys.stdout", stdout):
                result = main([
                    str(source),
                    "--output", str(output),
                    "--reparse-only",
                    "--replay-input-manifest", str(manifest),
                    "--replay-input-root", str(root / "other"),
                ])

            self.assertEqual(result, 2)
            self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], "replay_root_mismatch")
            producer.assert_not_called()

    def test_producer_failure_is_one_error_record_without_success_fields(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            stdout = io.StringIO()
            with patch(
                "tracen_replay.analysis_job.full_recording.main",
                side_effect=PipelineError("cached observations are invalid"),
            ), patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(root / "output")])

            self.assertEqual(result, 1)
            self.assertEqual(stdout.getvalue().count("\n"), 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["schema_version"], JOB_SCHEMA)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "producer_failed")
            self.assertNotIn("report_path", payload)

    def test_missing_report_after_producer_is_failure(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main"), patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "report_missing")

    def test_existing_report_noop_is_report_stale(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            _write_report(output, source)
            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main"), patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "report_stale")

    def test_same_content_report_rewrite_is_allowed(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            report_path = _write_report(output, source)
            original = report_path.read_bytes()
            old_mtime_ns = report_path.stat().st_mtime_ns

            def producer():
                report_path.write_bytes(original)
                rewritten_mtime_ns = old_mtime_ns + 1_000_000_000
                os.utime(report_path, ns=(rewritten_mtime_ns, rewritten_mtime_ns))

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 0)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["status"], "succeeded")
            self.assertEqual(report_path.read_bytes(), original)

    def test_source_changed_during_producer_is_source_mismatch(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                _write_report(output, source)
                source.write_bytes(b"changed")

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "source_mismatch")

    def test_absolute_and_traversal_evidence_paths_are_rejected(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            cases = {
                "traversal": "../outside.png",
                "absolute": str((root / "outside.png").resolve()),
            }
            for name, evidence in cases.items():
                with self.subTest(path=name):
                    output = root / f"output-{name}"
                    stdout = io.StringIO()

                    def producer(evidence=evidence, output=output):
                        _write_report(output, source, frame_evidence=evidence)

                    with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                            patch("sys.stdout", stdout):
                        result = main([str(source), "--output", str(output)])

                    self.assertEqual(result, 1)
                    payload = json.loads(stdout.getvalue())
                    self.assertEqual(payload["error"]["code"], "evidence_outside_root")

    def test_observation_evidence_path_is_checked(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                _write_report(output, source, observations=[{"evidence": "../outside.png"}])

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "evidence_outside_root")

    def test_evidence_symlink_outside_root_is_rejected(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"
            output.mkdir()
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            link = output / "link.png"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlink unavailable: {exc}")

            def producer():
                _write_report(output, source, frame_evidence="link.png")

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "evidence_outside_root")

    def test_report_contract_failure_cannot_be_published(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report_path = _write_report(output, source)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["gameplay_tracking"].pop("concerts")
                report_path.write_text(json.dumps(report), encoding="utf-8")

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "invalid_report")
            self.assertNotIn("report_sha256", payload)

    def test_missing_verification_boolean_cannot_be_published(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report_path = _write_report(output, source)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["verification"].pop("go_ready")
                report_path.write_text(json.dumps(report), encoding="utf-8")

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "invalid_go_ready")

    def test_report_source_status_mismatch_cannot_be_published(self):
        with workspace_temp() as root:
            source = root / "run.mp4"
            source.write_bytes(b"source")
            output = root / "output"

            def producer():
                report_path = _write_report(output, source)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                report["verification"]["source_sha256"] = "0" * 64
                report_path.write_text(json.dumps(report), encoding="utf-8")

            stdout = io.StringIO()
            with patch("tracen_replay.analysis_job.full_recording.main", side_effect=producer), \
                    patch("sys.stdout", stdout):
                result = main([str(source), "--output", str(output)])

            self.assertEqual(result, 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["error"]["code"], "invalid_report_status")

    def test_missing_source_is_a_cli_input_error_and_does_not_start_producer(self):
        with workspace_temp() as root:
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                result = main([str(root / "missing.mp4"), "--output", str(root / "output")])

            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue().count("\n"), 1)
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["schema_version"], JOB_SCHEMA)
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["error"]["code"], "source_not_found")


if __name__ == "__main__":
    unittest.main()
