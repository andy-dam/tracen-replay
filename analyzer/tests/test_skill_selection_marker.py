import math
from pathlib import Path
import unittest

from PIL import Image, ImageDraw

from tests.test_gameplay import workspace_temp
from tracen_replay.skill_menu_observations import detect_skill_selection_marker


def _action_image(path: Path, shape: str) -> None:
    """Draw a card control and one candidate feedback shape at its anchor."""

    control_box = (710, 500, 770, 532)
    left, top, right, bottom = control_box
    anchor_x = left - 65
    anchor_y = round((top + bottom) / 2 - 2)
    image = Image.new("RGB", (810, 1080), (190, 190, 196))
    draw = ImageDraw.Draw(image)
    control = [
        (anchor_x, anchor_y - 18),
        (anchor_x + 18, anchor_y - 5),
        (anchor_x + 18, anchor_y + 11),
        (anchor_x + 10, anchor_y + 18),
        (anchor_x - 10, anchor_y + 18),
        (anchor_x - 18, anchor_y + 11),
        (anchor_x - 18, anchor_y - 5),
    ]
    draw.polygon(control, fill=(132, 208, 37), outline=(45, 100, 20))
    mark_x, mark_y = anchor_x - 10, anchor_y + 6
    if shape == "star":
        points = []
        for index in range(10):
            angle = -math.pi / 2 + index * math.pi / 5
            radius = 9 if index % 2 == 0 else 4
            points.append((
                mark_x + radius * math.cos(angle),
                mark_y + radius * math.sin(angle),
            ))
        draw.polygon(points, fill=(250, 252, 242))
    elif shape == "plus":
        draw.line((mark_x - 7, mark_y, mark_x + 7, mark_y),
                  fill=(245, 250, 235), width=3)
        draw.line((mark_x, mark_y - 7, mark_x, mark_y + 7),
                  fill=(245, 250, 235), width=3)
    elif shape == "square":
        draw.rectangle((mark_x - 8, mark_y - 8, mark_x + 7, mark_y + 7),
                       fill=(250, 252, 242))
    elif shape == "circle":
        draw.ellipse((mark_x - 8, mark_y - 8, mark_x + 7, mark_y + 7),
                     fill=(250, 252, 242))
    else:
        raise ValueError(shape)
    image.save(path)


class SkillSelectionMarkerTests(unittest.TestCase):
    def test_shape_gate_accepts_star_and_rejects_simple_glyphs(self):
        with workspace_temp() as directory:
            control = [710, 500, 770, 532]
            paths = {shape: Path(directory) / f"{shape}.png"
                     for shape in ("star", "plus", "square", "circle")}
            for shape, path in paths.items():
                _action_image(path, shape)

            marker = detect_skill_selection_marker(paths["star"], control)
            self.assertIsNotNone(marker)
            self.assertEqual(marker["method"], "source_pixel_action_star_v1")
            self.assertEqual(marker["votes"], 4)
            self.assertTrue(all(
                observation["normalized_perimeter"] >= 3.90
                and observation["normalized_radial_range"] >= 0.28
                for observation in marker["observations"]
            ))
            for shape in ("plus", "square", "circle"):
                with self.subTest(shape=shape):
                    self.assertIsNone(
                        detect_skill_selection_marker(paths[shape], control)
                    )
