"""A fresh run reads every lesson balance slot again from wide and padded crops."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracen_replay import full_recording
from tracen_replay.refine_currencies import refine


class CurrencyRefinementStageTests(unittest.TestCase):
    def test_a_run_without_lesson_frames_needs_no_reader(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "neural").mkdir()
            with patch("tracen_replay.refine_currencies.NeuralReader") as reader:
                result = refine(root, model_dir="models")
            reader.assert_not_called()
            self.assertEqual((result["new_frames"], result["padded_frames"], result["model_sha256"]), (0, 0, None))
            self.assertTrue((root / "currency-refinement").is_dir())
            self.assertTrue((root / "currency-padding-refinement").is_dir())

    def test_the_pipeline_generates_the_sidecars_with_its_model_dir(self):
        with patch("tracen_replay.refine_currencies.refine", return_value=dict(new_frames=3)) as run:
            out = full_recording._generate_currency_refinement(Path("run"), model_dir=Path("models"))
        run.assert_called_once_with(Path("run"), model_dir=Path("models"))
        self.assertEqual(out, dict(new_frames=3))

    def test_a_reader_failure_is_a_pipeline_error_not_a_crash(self):
        with patch("tracen_replay.refine_currencies.refine", side_effect=OSError("no models")):
            with self.assertRaises(full_recording.PipelineError):
                full_recording._generate_currency_refinement(Path("run"), model_dir=Path("models"))

    def test_the_stage_runs_only_on_fresh_runs_before_the_reload(self):
        source = Path(full_recording.__file__).read_text(encoding="utf-8")
        fresh = source.index("if not args.reparse_only:\n        race_quantity=")
        currency = source.index("_guarded(report,'currency_refinement'")
        reload = source.index("readings=cached_readings(report,args.output,workers=args.workers)")
        self.assertTrue(fresh < currency < reload)
        self.assertEqual(source.count("_guarded(report,'currency_refinement'"), 1)


if __name__ == "__main__":
    unittest.main()
