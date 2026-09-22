import unittest

from tests.test_neural_transactions import line, row
from tracen_replay.transactions import outcome_events


def hint(name="Example Skill", amount=3):
    return dict(kind="skill_hint_change", name=name, amount=amount,
                raw_text=f"Gained {amount} hint level(s) for {name}.", confidence=99)


def occluded(effect, box=(316, 852, 756, 884), confidence=99):
    return dict(text=effect["raw_text"], box=list(box), confidence=confidence,
                overlay_boxes=[[522, 862, 535, 877]], recipient_name_occluded=False)


class CrossEventReceiptContinuityTests(unittest.TestCase):
    def rows(self):
        effect=hint()
        return [
            row(0, "event_outcome", effects=[effect],ocr={'neural':[line(effect['raw_text'],(316,852,756,884))]}),
            row(250, "event_outcome", facts={"occluded_receipt_lines": [occluded(effect)]}),
            row(500, "event_outcome", facts={"occluded_receipt_lines": [occluded(effect)]}),
            row(750, "event_outcome", facts={"occluded_receipt_lines": [occluded(effect)]}),
            row(1000, "event_outcome", effects=[effect, hint("Another Skill", 2)],
                ocr={'neural':[line(effect['raw_text'],(316,852,756,884))]}),
        ]

    def test_explicit_duplicate_is_suppressed_but_later_receipt_is_retained(self):
        events=outcome_events(self.rows())
        self.assertEqual(len(events), 2)
        self.assertEqual([(e["name"], e["amount"]) for e in events[0]["effects"]
                          if e["kind"] == "skill_hint_change"], [("Example Skill", 3)])
        self.assertEqual([(e["name"], e["amount"]) for e in events[1]["effects"]], [("Another Skill", 2)])
        self.assertEqual(events[0]["effects"][0]["occluded_continuity_evidence"], ["250.png", "500.png", "750.png"])
        self.assertEqual(events[1]["deduplicated_receipt_effects"][0]["evidence"], ["1000.png"])

    def test_occluded_only_text_never_creates_an_award(self):
        effect=hint()
        rows=[row(0, "event_outcome", facts={"occluded_receipt_lines": [occluded(effect)]}),
              row(250, "event_outcome", facts={"occluded_receipt_lines": [occluded(effect)]})]
        self.assertEqual(outcome_events(rows), [])

    def test_missing_or_wrong_continuity_keeps_two_explicit_receipts(self):
        for mutation in ("missing", "wrong_amount", "wrong_name", "shift", "long_gap"):
            with self.subTest(mutation=mutation):
                rows=self.rows()
                if mutation == "missing":
                    rows[2]["facts"]={}
                elif mutation == "wrong_amount":
                    rows[2]["facts"]["occluded_receipt_lines"][0]["text"]="Gained 4 hint level(s) for Example Skill."
                elif mutation == "wrong_name":
                    rows[2]["facts"]["occluded_receipt_lines"][0]["text"]="Gained 3 hint level(s) for Different Skill."
                elif mutation == "shift":
                    rows[2]["facts"]["occluded_receipt_lines"][0]["box"]=[316, 950, 756, 982]
                else:
                    rows[-1]["source_timestamp_ms"]=1251
                    rows[-1]["evidence"]="1251.png"
                events=outcome_events(rows)
                self.assertEqual([e["name"] for e in events[0]["effects"]], ["Example Skill"])
                self.assertEqual([e["name"] for e in events[1]["effects"]], ["Example Skill", "Another Skill"])

    def test_narrative_screen_and_title_boundaries_are_not_bridged(self):
        for mutation in ("narrative", "screen", "title"):
            with self.subTest(mutation=mutation):
                rows=self.rows()
                if mutation == "narrative":
                    rows[2]["facts"]={}
                    rows[2]["ocr"]={"neural": [line("A different conversation starts here.", (300, 810, 800, 840))]}
                elif mutation == "screen":
                    rows[2]["screen"]="training_preview"
                else:
                    rows[2]["context_title"]="A different receipt"
                events=outcome_events(rows)
                self.assertEqual([e["name"] for e in events[0]["effects"]], ["Example Skill"])
                self.assertEqual([e["name"] for e in events[1]["effects"]], ["Example Skill", "Another Skill"])

    def test_recipient_identity_occlusion_is_not_used_as_continuity(self):
        rows=self.rows()
        for source in rows[1:3]:
            source["facts"]["occluded_receipt_lines"][0]["recipient_name_occluded"]=True
        events=outcome_events(rows)
        self.assertEqual([e["name"] for e in events[1]["effects"]], ["Example Skill", "Another Skill"])

    def test_occlusion_metadata_cannot_be_bypassed_by_duplicate_neural_line(self):
        rows = self.rows()
        for source in rows[1:3]:
            blocked = source["facts"]["occluded_receipt_lines"][0]
            blocked["recipient_name_occluded"] = True
            source["ocr"] = {"neural": [
                line(blocked["text"], tuple(blocked["box"]), confidence=99)
            ]}
        events = outcome_events(rows)
        self.assertEqual(
            [e["name"] for e in events[1]["effects"]],
            ["Example Skill", "Another Skill"],
        )

    def test_low_confidence_corrupt_middle_is_geometry_only(self):
        rows = self.rows()
        rows[1]["facts"]["occluded_receipt_lines"][0].update(
            text="Gained 3 hint leor Example Skill.", confidence=40
        )
        rows[2]["facts"]["occluded_receipt_lines"][0].update(
            text="Gained 3 hint level(s for Example Skill.", confidence=94.9
        )
        events = outcome_events(rows)
        self.assertEqual([e["name"] for e in events[-1]["effects"]], ["Another Skill"])
        unknown = events[0]["effects"][0]["unknown_slot_evidence"]
        self.assertEqual([item["evidence"] for item in unknown], ["250.png", "500.png"])
        self.assertEqual(unknown[0]["text"], "Gained 3 hint leor Example Skill.")

    def test_incomplete_geometry_and_distinct_rank_cannot_establish_continuity(self):
        for case in ('missing_endpoint','different_endpoint','missing_flag','circle','double_circle',
                     'missing_frame','narrative','nan_box','downward_reset','competing_same_slot'):
            with self.subTest(case=case):
                rows=self.rows()
                target=rows[2]['facts']['occluded_receipt_lines'][0]
                if case=='missing_endpoint': rows[0].pop('ocr')
                if case=='different_endpoint': rows[-1]['ocr']['neural'][0]['box']=[500,852,900,884]
                if case=='missing_flag': target.pop('recipient_name_occluded')
                if case in ('circle','double_circle'):
                    target['text']='Gained 3 hint level(s) for Example Skill'+('○.' if case=='circle' else '◎.')
                if case=='missing_frame': rows.pop(2)
                if case=='narrative':
                    rows[2]['ocr']={'neural':[line('I went to see my fans yesterday.',(316,800,756,830))]}
                if case=='nan_box': target['box'][0]=float('nan')
                if case=='downward_reset': target['box']=[316,900,756,932]
                if case=='competing_same_slot':
                    rows[2]['facts']['occluded_receipt_lines'].append(
                        occluded(hint('Different Skill', 3), box=(316,852,756,884))
                    )
                events=outcome_events(rows)
                self.assertEqual([e['name'] for e in events[-1]['effects']],['Example Skill','Another Skill'])

    def test_adjacent_different_amount_line_does_not_veto_tracked_receipt(self):
        rows = self.rows()
        rows[2]["facts"]["occluded_receipt_lines"].append(
            occluded(hint("Adjacent Skill", 2), box=(316,860,688,892))
        )
        events = outcome_events(rows)
        self.assertEqual([e["name"] for e in events[-1]["effects"]], ["Another Skill"])


if __name__ == "__main__":
    unittest.main()
