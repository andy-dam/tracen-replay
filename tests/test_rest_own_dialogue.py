"""The Rest result dialogue's own frame before its receipt text is part of the Rest path."""
import unittest

from tracen_replay.rest_actions import reconstruct
from tests.test_rest_actions import dated_sequence, row


class RestOwnDialogueTests(unittest.TestCase):
    def test_the_results_own_titled_frame_before_the_receipt_is_allowed(self):
        readings, events = dated_sequence()
        events[0]["context_title"] = "All Refreshed"
        readings.append(row(1750, "result-0.png", screen="event_outcome", calendar="Senior Year Late Apr", context="All Refreshed"))
        readings.sort(key=lambda r: r["source_timestamp_ms"])
        actions = reconstruct(readings, events)
        self.assertEqual([a["kind"] for a in actions], ["rest"])

    def test_another_dialogue_before_the_receipt_still_blocks(self):
        readings, events = dated_sequence()
        events[0]["context_title"] = "All Refreshed"
        readings.append(row(1750, "other.png", screen="event_outcome", calendar="Senior Year Late Apr", context="A Fan Letter"))
        readings.sort(key=lambda r: r["source_timestamp_ms"])
        self.assertEqual(reconstruct(readings, events), [])


if __name__ == "__main__":
    unittest.main()
