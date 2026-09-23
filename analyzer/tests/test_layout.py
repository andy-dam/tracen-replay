import shutil
import unittest
import uuid
from types import SimpleNamespace

import numpy as np
from PIL import Image

from tests import localdata
from tracen_replay import layout, layout_fit
from tracen_replay.gameplay import receipt_band, receipt_rows
from tracen_replay.layout import PC, Layout, working_size
from tracen_replay.pipeline import PipelineError
from tracen_replay.preview_confirmed_gains import preview_amounts, preview_performance_amounts
from tracen_replay.race_hub_stats import COLUMNS, observation
from tracen_replay.stats import BOXES
from tracen_replay.status_badges import read_badges

# The two portrait recordings the layout rule was measured on: a phone with a
# camera cutout at the top, and a tablet with a home bar at the bottom.
PHONE = Layout((608, 1316), (0, 0, 608, 1316), top=53, bottom=0)
TABLET = Layout((754, 1080), (0, 0, 754, 1080), top=0, bottom=20)


class LayoutTests(unittest.TestCase):
    def test_the_pc_layout_moves_nothing(self):
        box = (309, 721, 366, 747)
        for pin in ('tl', 'tc', 'mc', 'sc', 'bc', 'br'):
            self.assertEqual(PC.offset(pin), (0, 0))
            self.assertIs(PC.place(box, pin), box)
        self.assertIs(PC.place(box, 'mc', 'sc'), box)
        self.assertEqual(PC.pane_box, (148, 0, 958, 1080))
        self.assertEqual(PC.frame_box, (0, 0, 1920, 1080))
        self.assertTrue(PC.is_reference)

    def test_each_pin_follows_its_edge_or_centre_of_the_game_area(self):
        # Measured on the recordings: the phone's clear area starts 53 pixels
        # down; the tablet keeps 20 pixels clear at the bottom.
        self.assertEqual({pin: PHONE.offset(pin + 'c')[1] for pin in 'tsmb'},
                         {'t': 53, 's': 118, 'm': 144, 'b': 236})
        self.assertEqual({pin: PHONE.offset('t' + pin)[0] for pin in 'lcr'}, {'l': 0, 'c': -101, 'r': -202})
        self.assertEqual({pin: TABLET.offset(pin + 'c')[1] for pin in 'tsmb'},
                         {'t': 0, 's': 0, 'm': -10, 'b': -20})
        self.assertEqual({pin: TABLET.offset('t' + pin)[0] for pin in 'lcr'}, {'l': 0, 'c': -28, 'r': -56})
        with self.assertRaises(ValueError):
            PHONE.offset('xc')

    def test_a_box_with_several_pins_covers_every_place_they_put_it(self):
        self.assertEqual(PHONE.place((250, 770, 850, 1000), 'mc', 'sc'), (149, 888, 749, 1144))
        self.assertEqual(PHONE.place_rows(770, 1000, 'm', 's'), (888, 1144))
        self.assertEqual(TABLET.place_rows(770, 1000, 'm', 's'), (760, 1000))

    def test_a_placed_box_stays_inside_the_game_area_and_keeps_its_type(self):
        self.assertEqual(PHONE.place((120, 850, 850, 1010), 'bc'), (148, 1086, 749, 1246))
        self.assertEqual(PHONE.place([309, 721, 366, 747], 'bc'), [208, 957, 265, 983])
        self.assertEqual(PHONE.pane_box, (148, 0, 756, 1316))
        self.assertEqual(PHONE.frame_box, (148, 0, 756, 1316))

    def test_the_current_layout_is_used_by_the_module_helpers_and_restored(self):
        with layout.using(PHONE.to_dict()) as current:
            self.assertEqual(current, PHONE)
            self.assertEqual(layout.pane_size(), (608, 1316))
            self.assertEqual(layout.place_x(250), 149)
            self.assertEqual(layout.place_y(721, 'b'), 957)
            self.assertEqual(receipt_band(), (149, 888, 749, 1144))
            self.assertEqual(receipt_rows(780, 960), (898, 1104))
            self.assertTrue(layout.inside_pane((148, 0, 756, 1316)))
            self.assertFalse(layout.inside_pane((148, 0, 758, 1316)))
            self.assertEqual(layout.clamp((100, -5, 800, 20)), (148, 0, 756, 20))
        self.assertIs(layout.current(), PC)
        self.assertEqual(layout.place(BOXES[0], 'bc'), BOXES[0])

    def test_a_layout_is_stored_and_read_back(self):
        self.assertEqual(Layout.from_dict(PHONE.to_dict()), PHONE)
        self.assertEqual(Layout.from_dict(None), PC)
        self.assertEqual(PHONE.to_dict(), dict(frame=[608, 1316], pane=[0, 0, 608, 1316], top=53, bottom=0))
        # A game cut from a wider video keeps where it was cut from.
        framed = Layout((714, 1080), (0, 0, 714, 1080), crop=(605, 3, 710, 1074))
        self.assertEqual(Layout.from_dict(framed.to_dict()), framed)
        self.assertEqual(framed.to_dict()['crop'], [605, 3, 710, 1074])

    def test_the_working_frame_gives_a_design_unit_the_pc_pane_size(self):
        self.assertEqual(working_size(1080, 2340), (0.5625, (608, 1316)))
        scale, size = working_size(1940, 2778)
        self.assertAlmostEqual(scale, 0.5625 / (2778 / 1920))
        self.assertEqual(size, (754, 1080))


def _moved(box, pin, layout_):
    dx, dy = layout_.offset(pin)
    return [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy]


class PlacedReaderTests(unittest.TestCase):
    def test_the_mood_badge_is_read_where_the_phone_draws_it(self):
        lines = [dict(text='Energy', confidence=99, box=_moved((380, 120, 450, 160), 'tc', PHONE)),
                 dict(text='GREAT', confidence=99, box=_moved((720, 120, 800, 160), 'tc', PHONE))]
        with layout.using(PHONE):
            self.assertEqual([badge['value'] for badge in read_badges(lines)], ['great'])
        self.assertEqual(read_badges(lines), [])

    def test_the_race_day_totals_are_read_where_the_tablet_draws_them(self):
        lines = [dict(text=str(100 + index), confidence=99, box=_moved((left + 5, 782, right - 5, 798), 'bc', TABLET))
                 for index, (left, right) in enumerate(COLUMNS)]
        with layout.using(TABLET):
            result = observation(lines, grid_verified=True)
        self.assertEqual(list(result['values'].values()), [100, 101, 102, 103, 104, 105])
        self.assertIsNone(observation(lines, grid_verified=True))

    def test_a_training_preview_is_read_where_the_phone_draws_it(self):
        # The badges are pinned to the bottom centre, the panel to the top left.
        row = dict(ocr=dict(neural=[
            dict(text='+17', confidence=99, box=_moved((500, 675, 540, 705), 'bc', PHONE)),
            dict(text='58+26', confidence=99, box=_moved((220, 355, 320, 380), 'tl', PHONE))]))
        with layout.using(PHONE):
            self.assertEqual(preview_amounts(row), dict(power=17))
            self.assertEqual(preview_performance_amounts(row), dict(passion=26))
        # Read as the PC pane, the badge misses its row and the panel line
        # lands on the row below.
        self.assertEqual(preview_amounts(row), {})
        self.assertEqual(preview_performance_amounts(row), dict(vocal=26))


class _Engine:
    """Returns the same labels for every frame, at the given boxes."""

    def __init__(self, labels):
        self.labels = labels

    def __call__(self, _image):
        boxes = [np.array([[left, top], [right, top], [right, bottom], [left, bottom]], dtype=float)
                 for _, (left, top, right, bottom) in self.labels]
        return SimpleNamespace(boxes=np.array(boxes), txts=[text for text, _ in self.labels],
                               scores=[0.99] * len(self.labels))


def _label(text, top, centre, width=40, height=20):
    return text, (centre - width / 2, top, centre + width / 2, top + height)


class LayoutFitTests(unittest.TestCase):
    def setUp(self):
        self.root = localdata.scratch(uuid.uuid4().hex)
        Image.new('RGB', PHONE.frame).save(self.root / 'frame.png')
        self.report = dict(layout=Layout(PHONE.frame, PHONE.pane).to_dict(),
                           frames=[dict(evidence='frame.png')] * 10)

    def tearDown(self):
        shutil.rmtree(self.root)

    def reader(self, labels):
        return SimpleNamespace(Image=Image, np=np, engine=_Engine(labels))

    def test_the_margins_come_from_labels_pinned_to_the_top_and_bottom(self):
        # Where the phone draws them: the turn counter 53 pixels below its PC
        # place, Back and Quick 236 pixels below theirs, each across where its
        # pin puts it.
        labels = [_label('turn(s)', 61 + 53, 201 - 101), _label('Back', 1024 + 236, 75),
                  _label('Quick', 1038 + 236, 554 - 202)]
        fitted = layout_fit.fit(self.report, self.root, self.reader(labels))
        self.assertEqual(fitted, PHONE)

    def test_a_game_cut_from_a_wider_video_keeps_its_crop_when_fitted(self):
        labels = [_label('turn(s)', 61 + 53, 201 - 101), _label('Back', 1024 + 236, 75),
                  _label('Quick', 1038 + 236, 554 - 202)]
        report = dict(self.report, layout=dict(self.report['layout'], crop=[420, 0, 1080, 2340]))
        fitted = layout_fit.fit(report, self.root, self.reader(labels))
        self.assertEqual((fitted.crop, fitted.top, fitted.bottom), ((420, 0, 1080, 2340), PHONE.top, PHONE.bottom))

    def test_a_pc_layout_is_not_fitted(self):
        report = dict(layout=PC.to_dict(), frames=[])
        self.assertEqual(layout_fit.fit(report, self.root, self.reader([])), PC)
        self.assertEqual(layout_fit.fit(dict(frames=[]), self.root, self.reader([])), PC)

    def test_a_recording_without_the_interface_or_laid_out_otherwise_is_refused(self):
        with self.assertRaises(PipelineError):
            layout_fit.fit(self.report, self.root, self.reader([_label('turn(s)', 114, 100)]))
        shifted = [_label('turn(s)', 114, 201 - 101 + 40), _label('Back', 1260, 75 + 40)]
        with self.assertRaises(PipelineError):
            layout_fit.fit(self.report, self.root, self.reader(shifted))


if __name__ == '__main__':
    unittest.main()
