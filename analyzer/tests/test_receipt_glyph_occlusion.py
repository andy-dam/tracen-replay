import hashlib
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from tracen_replay.receipt_occlusion import annotate


def source(lines, pane):
    return dict(
        lines=[dict(item) for item in lines],
        regions={},
        header="",
        current_grid=False,
        result_grid=False,
        gameplay_sha256=hashlib.sha256(pane.tobytes()).hexdigest(),
    )


def alignment(box, words, columns, line_length):
    return dict(
        line_box=list(box),
        recognized_text=" ".join(words),
        confidence=99,
        words=list(words),
        columns=[list(group) for group in columns],
        line_length=line_length,
    )


class ReceiptGlyphOcclusionTests(unittest.TestCase):
    def pane_with_glyph_rows(self, top=836, bottom=852):
        pane = Image.new("RGB", (810, 1080), "white")
        # The source dialogue uses warm brown ink. Multiple horizontal runs
        # model the visible glyph rows without depending on an OCR engine.
        draw = ImageDraw.Draw(pane)
        for y in range(top, bottom):
            for left in range(166, 612, 7):
                draw.rectangle((left, y, min(left + 4, 611), y), fill=(105, 70, 50))
        return pane

    def friendship(self, box=(314, 828, 759, 862), text=None):
        return dict(
            text=text or "Friendship with Etsuko Stonashi went up by 7.",
            box=list(box),
            confidence=97.974,
        )

    def etusko_alignment(self, *, clear=False):
        if clear:
            box = [314, 828, 759, 862]
            columns = [
                [2, 3, 5, 6, 8, 10, 12, 14, 15, 17],
                [20, 22, 23, 25],
                [28, 29, 31, 33, 35, 37],
                [40, 43, 44, 46, 48, 50, 52, 53],
                [56, 58, 60, 62],
                [65, 67],
                [70, 72],
                [75, 76],
            ]
            words = ["Friendship", "with", "Etsuko", "Otonashi", "went", "up", "by", "7."]
        else:
            box = [314, 828, 759, 862]
            columns = [
                [2, 3, 5, 6, 8, 10, 12, 14, 15, 17],
                [20, 22, 23, 25],
                [28, 29, 31, 33, 35, 37],
                [41, 43, 44, 46, 48, 50, 52, 53],
                [56, 58, 60, 62],
                [65, 67],
                [70, 72],
                [75, 76],
            ]
            words = ["Friendship", "with", "Etsuko", "Stonashi", "went", "up", "by", "7."]
        return alignment(box, words, columns, 78)

    def kitasan_alignment(self):
        return alignment(
            [316, 855, 730, 883],
            ["Friendship", "with", "Kitasan", "Black", "went", "up", "by", "5."],
            [
                [2, 4, 5, 7, 9, 12, 14, 16, 18, 20],
                [24, 26, 28, 30],
                [34, 36, 37, 39, 41, 44, 46],
                [50, 52, 54, 56, 58],
                [62, 65, 67, 69],
                [72, 75],
                [79, 81],
                [85, 87],
            ],
            89,
        )

    def nishino_alignment(self):
        return alignment(
            [316, 878, 743, 907],
            ["Friendship", "with", "Nishino", "Flwer", "went", "up", "by", "7."],
            [
                [2, 4, 5, 7, 9, 12, 14, 16, 18, 20],
                [23, 25, 27, 29],
                [33, 35, 36, 38, 40, 42, 44],
                [48, 50, 54, 57, 59],
                [62, 65, 67, 69],
                [72, 75],
                [78, 81],
                [84, 86],
            ],
            88,
        )

    def status_alignment(self):
        box = [300, 820, 740, 850]
        words = ["Friendship", "with", "Example", "Name", "is", "maxed", "out."]
        columns = [
            [1, 2],
            [4, 5],
            [7, 8, 9],
            [11, 12],
            [14, 15],
            [17, 18, 19, 20, 21],
            [23, 24, 25, 26],
        ]
        return alignment(box, words, columns, 27)

    def test_upper_glyph_overlap_blocks_corrupted_recipient(self):
        pane = self.pane_with_glyph_rows()
        raw = source([self.friendship()], pane)
        raw["overlay_alignment"] = [self.etusko_alignment()]
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[539, 824, 553, 842]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["lines"][0]["overlay_occluded"])
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_cursor_in_inferred_word_gap_cannot_establish_clear_name(self):
        pane = self.pane_with_glyph_rows()
        raw = source(
            [self.friendship(text="Friendship with Etsuko Otonashi went up by 7.")],
            pane,
        )
        raw["overlay_alignment"] = [self.etusko_alignment(clear=True)]
        # OCR whitespace could instead be a missing glyph. The obstructed
        # frame cannot establish a clear name merely from character columns.
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[530, 825, 544, 841]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_erased_digit_uses_surviving_receipt_text_for_vertical_position(self):
        pane = self.pane_with_glyph_rows(861, 876)
        box = [318, 856, 530, 882]
        text = "Vocals went up by 2."
        raw_line = dict(text=text, box=box, confidence=99)
        matched = alignment(
            box, ["Vocals", "went", "up", "by", "2."],
            [[2, 4, 6, 8, 10, 12], [15, 17, 19, 21], [24, 26],
             [30, 32], [40, 42]], 43,
        )
        # Erase nearly all ink around the amount, leaving only one row.
        # Estimating glyph height from that tiny remainder misses the cursor.
        draw = ImageDraw.Draw(pane)
        draw.rectangle((331, 856, 382, 881), fill="white")
        draw.rectangle((345, 871, 363, 871), fill=(105, 70, 50))
        raw = source([raw_line], pane)
        raw["overlay_alignment"] = [matched]
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[509, 851, 524, 871]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertFalse(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])
        self.assertEqual(marked["lines"][0]["text"], text)

    def test_small_vertical_edge_contact_does_not_block_receipt(self):
        pane = self.pane_with_glyph_rows()
        raw = source([self.friendship()], pane)
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[539, 834, 553, 838]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 97.974)
        self.assertEqual(marked["occluded_receipt_lines"], [])

    def test_internal_missing_name_glyph_still_blocks_receipt(self):
        pane = self.pane_with_glyph_rows(884, 900)
        raw = source(
            [self.friendship(
                box=(316, 878, 743, 907),
                text="Friendship with Nishino Flwer went up by 7.",
            )],
            pane,
        )
        raw["overlay_alignment"] = [self.nishino_alignment()]
        # The source cursor covers the missing 'o' in Flwer. There is no
        # character column for that glyph, so the whole-word span must carry
        # the name obstruction evidence.
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[562, 879, 577, 897]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_three_pixel_lower_edge_contact_preserves_clear_anchor(self):
        pane = self.pane_with_glyph_rows(860, 876)
        raw = source(
            [self.friendship(
                box=(316, 855, 730, 883),
                text="Friendship with Kitasan Black went up by 5.",
            )],
            pane,
        )
        raw["overlay_alignment"] = [self.kitasan_alignment()]
        # This is the 238000-238500 geometry: the padded cursor box touches
        # the glyph band by exactly three rows at its lower edge.
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[561, 873, 576, 891]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 97.974)
        self.assertEqual(marked["occluded_receipt_lines"], [])

    def test_cursor_on_next_line_does_not_block_previous_receipt(self):
        pane = self.pane_with_glyph_rows(836, 852)
        draw = ImageDraw.Draw(pane)
        for y in range(873, 889):
            for left in range(166, 612, 7):
                draw.rectangle((left, y, min(left + 4, 611), y), fill=(105, 70, 50))
        first = self.friendship(box=(314, 828, 759, 862))
        second = dict(
            text="Skill Pts went up by 7.",
            box=[314, 865, 759, 893],
            confidence=98,
        )
        raw = source([first, second], pane)
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[539, 864, 553, 890]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 97.974)
        self.assertEqual(marked["lines"][1]["confidence"], 0)
        self.assertEqual(len(marked["occluded_receipt_lines"]), 1)
        self.assertEqual(marked["occluded_receipt_lines"][0]["text"], second["text"])

    def test_unobstructed_line_is_unchanged(self):
        pane = self.pane_with_glyph_rows()
        raw = source([self.friendship()], pane)
        with patch("tracen_replay.receipt_occlusion.overlay_boxes", return_value=[]):
            marked = annotate(raw, pane)
        self.assertEqual(marked, raw)

    def test_name_classification_survives_general_line_overlap(self):
        pane = self.pane_with_glyph_rows(836, 850)
        status = dict(
            text="Friendship with Example Name is maxed out.",
            box=[300, 820, 740, 850],
            confidence=99,
        )
        raw = source([status], pane)
        raw["overlay_alignment"] = [self.status_alignment()]
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[445, 824, 458, 846]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])

    def test_name_occlusion_cannot_trigger_leading_digit_recovery(self):
        pane = self.pane_with_glyph_rows()
        text = "Friendship with Etsuko Stonashi went up by 18."
        raw = source([self.friendship(text=text)], pane)
        complete = self.etusko_alignment()
        complete["words"][-1] = "18."
        complete["columns"][-1] = [75, 76, 77]
        complete["recognized_text"] = text
        raw["overlay_alignment"] = [
            complete,
            dict(
                line_box=list(complete["line_box"]),
                recognized_text="Friendship with Etsuko Stonashi went up by 8.",
                confidence=99,
                numeric_box=[729, 828, 759, 862],
            ),
        ]
        raw["lines"][0]["receipt_crop_views"] = [
            {"text": text, "confidence": 99},
            {"text": text, "confidence": 99},
            {"text": text, "confidence": 99},
        ]
        with patch(
            "tracen_replay.receipt_occlusion.overlay_boxes",
            return_value=[[539, 824, 553, 842]],
        ):
            marked = annotate(raw, pane)
        self.assertEqual(marked["lines"][0]["confidence"], 0)
        self.assertTrue(marked["occluded_receipt_lines"][0]["recipient_name_occluded"])
        self.assertEqual(marked["resolved_receipt_occlusions"], [])


if __name__ == "__main__":
    unittest.main()
