import shutil
import subprocess
import unittest
import uuid

from pathlib import Path
from unittest.mock import patch

from PIL import Image

import numpy

from tests import localdata
from tracen_replay.layout import REFERENCE_PANE
from tracen_replay.pipeline import (PipelineError, decode_frames, display_size, frame_layout, frame_rate,
                                    frame_scale, game_area, locate_game, probe)


class FrameSizeTests(unittest.TestCase):
    def test_a_pc_recording_of_any_16_9_size_becomes_the_1920x1080_frame(self):
        for size in ((1920, 1080), (1280, 720), (3840, 2160)):
            layout = frame_layout(*size)
            self.assertEqual((layout.frame, layout.pane), ((1920, 1080), REFERENCE_PANE))
        self.assertEqual(frame_scale(frame_layout(1920, 1080), 1920, 1080), "")
        self.assertEqual(frame_scale(frame_layout(1280, 720), 1280, 720), "scale=1920:1080:flags=lanczos")

    def test_a_portrait_game_keeps_its_shape_at_the_pc_pane_text_size(self):
        # A design unit is 0.5625 pixels: a phone 1080 wide is scaled to 608,
        # a tablet 1920 units tall is scaled to 1080.
        phone = frame_layout(1080, 2340)
        self.assertEqual((phone.frame, phone.pane), ((608, 1316), (0, 0, 608, 1316)))
        tablet = frame_layout(1940, 2778)
        self.assertEqual((tablet.frame, tablet.pane), ((754, 1080), (0, 0, 754, 1080)))
        self.assertEqual(frame_scale(phone, 1080, 2340), "scale=608:1316:flags=lanczos")

    def test_a_game_placed_inside_a_wider_video_is_cut_out_and_read_in_its_own_shape(self):
        layout = frame_layout(1920, 1080, (605, 3, 710, 1074))
        self.assertEqual((layout.frame, layout.pane, layout.crop), ((714, 1080), (0, 0, 714, 1080), (605, 3, 710, 1074)))
        self.assertEqual(frame_scale(layout, 1920, 1080), "crop=710:1074:605:3,scale=714:1080:flags=lanczos")
        with self.assertRaises(PipelineError):
            frame_layout(1920, 1080, (0, 0, 300, 400))

    def test_other_shapes_and_games_too_small_to_read_are_refused(self):
        for size in ((1920, 1200), (2560, 1080), (1080, 1080), (1080, 3000), (360, 780)):
            with self.subTest(size=size), self.assertRaises(PipelineError):
                frame_layout(*size)

    def change(self, width, height, left, top, right, bottom, around=1.0):
        """How much each pixel changed across a recording: the game's area busy, the rest ``around``."""
        change = numpy.full((height, width), around, dtype=numpy.float32)
        change[top:bottom, left:right] = 60.0
        return change

    def test_the_pc_clients_pane_keeps_the_pc_layout(self):
        # The client's panels beside the pane change too, but less than half as much.
        self.assertIsNone(locate_game(self.change(1920, 1080, 148, 0, 958, 1080, around=20.0), 1920, 1080))
        self.assertIsNone(locate_game(self.change(1280, 720, 99, 0, 639, 720), 1280, 720))

    def test_a_portrait_game_placed_in_a_wider_video_is_its_game_area(self):
        self.assertEqual(locate_game(self.change(1920, 1080, 605, 3, 1315, 1077), 1920, 1080), (605, 3, 710, 1074))
        # A 16:10 video holds no PC client: a pane-shaped busy area in it is a game placed there.
        self.assertEqual(locate_game(self.change(1920, 1200, 148, 0, 958, 1200), 1920, 1200), (148, 0, 810, 1200))

    def test_a_landscape_video_without_a_portrait_game_is_refused(self):
        for change in (numpy.full((1080, 1920), 30.0, dtype=numpy.float32), self.change(1920, 1080, 100, 0, 1700, 1080)):
            with self.subTest(), self.assertRaisesRegex(PipelineError, "not found"):
                locate_game(change, 1920, 1080)

    def test_a_recording_that_hardly_changes_is_read_by_its_shape(self):
        still = numpy.full((1080, 1920), 0.5, dtype=numpy.float32)
        still[:, 300:900] = 2.0
        self.assertIsNone(locate_game(still, 1920, 1080))
        with self.assertRaisesRegex(PipelineError, "not found"):
            locate_game(numpy.full((1200, 1920), 0.5, dtype=numpy.float32), 1920, 1200)

    def test_a_rotation_flag_turns_the_stored_size(self):
        video = {"width": 2778, "height": 1940, "side_data_list": [{"rotation": -90}]}
        self.assertEqual(display_size(video), (1940, 2778))
        self.assertEqual(display_size({"width": 1920, "height": 1080}), (1920, 1080))
        self.assertEqual(display_size({"width": 2340, "height": 1080, "tags": {"rotate": "90"}}), (1080, 2340))

    def test_the_frame_rate_comes_from_the_average_then_the_nominal_rate(self):
        self.assertAlmostEqual(frame_rate({"avg_frame_rate": "30000/1001"}), 29.97, places=2)
        self.assertEqual(frame_rate({"avg_frame_rate": "0/0", "r_frame_rate": "60/1"}), 60)
        self.assertIsNone(frame_rate({"avg_frame_rate": "0/0"}))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class PipelineIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = localdata.scratch(uuid.uuid4().hex)
        cls.source = cls.root / "synthetic.mkv"
        # Uneven original PTS plus a nonzero media origin. Frame-index/fps
        # timestamp reconstruction would fail this fixture. It is 720p, so
        # its frames are scaled to the analyzer's frame size.
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=10:duration=2", "-vf",
                        "select='eq(n,0)+eq(n,1)+eq(n,4)+eq(n,9)+eq(n,13)+eq(n,19)',setpts=PTS+5/TB",
                        "-fps_mode", "vfr", "-c:v", "ffv1", str(cls.source)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root)

    def test_variable_pts_and_nonzero_origin_with_offset(self):
        _, video, _, origin = probe(self.source)
        self.assertEqual(origin, 5)
        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        layout = frame_layout(*display_size(video))
        frames = decode_frames(self.source, directory, 0.2, 1.4, 8, origin,
                               scale=frame_scale(layout, *display_size(video)))
        self.assertEqual([f["source_timestamp_ms"] for f in frames], [400, 900, 1300])
        self.assertEqual([f["clip_timestamp_ms"] for f in frames], [200, 700, 1100])
        for frame in frames:
            with Image.open(directory / Path(frame["evidence"]).name) as image:
                self.assertEqual(image.size, layout.frame)

    def test_an_interval_without_a_source_frame_has_no_frames(self):
        _, _, _, origin = probe(self.source)
        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        # The source has no frame between 400 and 900 ms.
        self.assertEqual(decode_frames(self.source, directory, 0.45, 0.35, 60, origin, scale=""), [])

    def test_an_interval_starts_at_its_first_frame_s_whole_millisecond(self):
        # A 1/600 s time base, as a tablet's recorder writes, puts the second
        # frame at 1.67 ms, recorded as 2 ms: an interval from 2 ms holds it.
        source = self.root / "coarse.mov"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1280x720:rate=600:duration=1", "-vf", "select='eq(n,0)+eq(n,1)+eq(n,400)'",
                        "-fps_mode", "vfr", "-video_track_timescale", "600", "-c:v", "mjpeg", str(source)],
                       check=True, capture_output=True)
        _, _, _, origin = probe(source)
        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        frames = decode_frames(source, directory, 0.002, 0.016, 60, origin, scale="")
        self.assertEqual([f["source_timestamp_ms"] for f in frames], [2])

    def test_a_portrait_recording_is_decoded_in_its_own_shape(self):
        source = self.root / "portrait.mkv"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=1080x2340:rate=10:duration=0.5", "-c:v", "ffv1", str(source)],
                       check=True, capture_output=True)
        _, video, _, origin = probe(source)
        layout = frame_layout(*display_size(video))
        directory = self.root / uuid.uuid4().hex
        directory.mkdir()
        frames = decode_frames(source, directory, 0, 0.5, 4, origin, scale=frame_scale(layout, *display_size(video)))
        with Image.open(directory / Path(frames[0]["evidence"]).name) as image:
            self.assertEqual(image.size, (608, 1316))

    def test_a_recording_of_another_shape_is_refused(self):
        for size in ("640x360", "1080x1080"):
            with self.subTest(size=size):
                source = self.root / f"{size}.mkv"
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                f"testsrc2=size={size}:rate=10:duration=0.2", "-c:v", "ffv1", str(source)],
                               check=True, capture_output=True)
                with self.assertRaisesRegex(PipelineError, size.replace('x', '×')):
                    probe(source)

    def test_the_game_placed_inside_a_wider_video_is_found_from_its_change(self):
        # A portrait strip that changes on every frame, over a still picture;
        # the recording is 16:10, so its game is located rather than refused.
        source = self.root / "framed.mkv"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=1920x1200:r=10:d=3",
                        "-f", "lavfi", "-i", "color=c=gray:s=720x1080:r=10:d=3", "-filter_complex",
                        "[1]noise=alls=100:allf=t[game];[0][game]overlay=600:60", "-c:v", "ffv1", str(source)],
                       check=True, capture_output=True)
        _, video, duration, _ = probe(source)
        self.assertEqual(game_area(source, *display_size(video), duration), (600, 60, 720, 1080))

    def test_bad_media_is_refused(self):
        bad = self.root / "invalid.mp4"
        bad.write_text("not a video")
        with self.assertRaisesRegex(PipelineError, "ffprobe failed"):
            probe(bad)

    def test_an_inconsistent_showinfo_sequence_is_decoded_once_more(self):
        # The decoder's log came out with a duplicated showinfo line on the
        # first attempt; the same part decodes cleanly on the second.
        from tracen_replay import pipeline
        directory = self.root / "retry-frames"
        directory.mkdir()
        calls = []
        def fake_run(command):
            calls.append(command)
            for path in directory.glob("*.jpg"):
                path.unlink()
            for i in range(3):
                (directory / f"{i + 1:06d}.jpg").write_bytes(b"jpg")
            lines = ["[Parsed_showinfo_1 @ 0] config in time_base: 1/15360, frame_rate: 60/1"]
            order = [0, 0, 1, 2] if len(calls) == 1 else [0, 1, 2]
            for n in order:
                lines.append(f"[Parsed_showinfo_1 @ 0] n: {n} pts: {3840 * n} pts_time:{0.25 * n:.6f} pos: 1")
            return subprocess.CompletedProcess(command, 0, "", "\n".join(lines))
        with patch("tracen_replay.pipeline.run", side_effect=fake_run):
            frames = pipeline.decode_frames(self.source, directory, 0, 1, 4, 0, scale="")
        self.assertEqual(len(calls), 2)
        self.assertEqual([f["source_pts"] for f in frames], [0, 3840, 7680])
        with patch("tracen_replay.pipeline.run", side_effect=fake_run):
            calls.clear()
            with self.assertRaisesRegex(PipelineError, "inconsistent .*showinfo n=0 for image 1"):
                pipeline.decode_frames(self.source, directory, 0, 1, 4, 0, scale="", attempts=1)


if __name__ == "__main__":
    unittest.main()
