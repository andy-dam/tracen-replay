from copy import deepcopy
import unittest

from scripts.compare_final_report_structure import canonical_value_sha256, compare


SOURCE = "same-source"


def report():
    return {
        "source": {"sha256": SOURCE},
        "states": [{"channel": "stats", "values": {"speed": 10}}],
        "purchases": [{"name": "Lesson", "price": 20}],
        "inventory": [{"name": "Carrot", "count": 1}],
        "observation_metadata": {"uncertain": False},
    }


def at(result, pointer, *, kind=None):
    rows = [item for item in result["changes"] if item["pointer"] == pointer]
    if kind is not None:
        rows = [item for item in rows if item["kind"] == kind]
    if len(rows) != 1:
        raise AssertionError(f"expected one change at {pointer!r}, got {rows!r}")
    return rows[0]


class FinalReportStructureComparisonTests(unittest.TestCase):
    def test_states_purchase_inventory_and_metadata_are_all_in_scope(self):
        before = report()
        after = deepcopy(before)
        after["states"][0]["values"]["speed"] = 11
        after["purchases"][0]["price"] = 25
        after["inventory"][0]["count"] = 2
        after["observation_metadata"]["uncertain"] = True

        result = compare(before, after)
        pointers = {item["pointer"] for item in result["changes"]}
        self.assertEqual(
            pointers,
            {
                "/states/0",
                "/purchases/0",
                "/inventory/0",
                "/observation_metadata/uncertain",
            },
        )
        self.assertEqual(result["changed_value_count"], 4)
        self.assertTrue(result["review_required"])

    def test_changed_values_are_hash_bound_without_copying_payloads(self):
        before = report()
        after = deepcopy(before)
        after["states"][0]["values"]["speed"] = 11
        before_sha = "a" * 64
        after_sha = "b" * 64
        result = compare(
            before,
            after,
            before_sha256=before_sha,
            after_sha256=after_sha,
        )
        change = at(result, "/states/0")
        self.assertEqual(change["before"]["report_sha256"], before_sha)
        self.assertEqual(change["after"]["report_sha256"], after_sha)
        self.assertEqual(change["kind"], "replaced_at_index")
        self.assertEqual(change["before"]["value_sha256"], canonical_value_sha256(before["states"][0]))
        self.assertEqual(change["after"]["value_sha256"], canonical_value_sha256(after["states"][0]))
        self.assertNotIn("value", change["before"])
        self.assertNotIn("value", change["after"])

    def test_changed_nested_list_row_uses_one_whole_row_pointer(self):
        before = report()
        after = deepcopy(before)
        after["inventory"][0]["count"] = 3
        result = compare(before, after)
        self.assertEqual(
            [item["pointer"] for item in result["changes"]],
            ["/inventory/0"],
        )
        self.assertEqual(result["changes"][0]["kind"], "replaced_at_index")
        self.assertEqual(
            result["changes"][0]["after"]["value_sha256"],
            canonical_value_sha256(after["inventory"][0]),
        )

    def test_deeply_nested_changed_reading_stays_one_row_replacement(self):
        before = report()
        before["readings"] = [{
            "source": {"frame": {"metadata": {"confidence": 0.91}}},
            "facts": {"stats": {"values": {"speed": 381, "wit": 200}}},
        }]
        after = deepcopy(before)
        after["readings"][0]["facts"]["stats"]["values"]["speed"] = 382
        result = compare(before, after)
        self.assertEqual([item["pointer"] for item in result["changes"]], ["/readings/0"])
        self.assertEqual(result["changes"][0]["kind"], "replaced_at_index")

    def test_duplicate_list_rows_are_not_collapsed(self):
        before = report()
        after = deepcopy(before)
        after["inventory"].append(deepcopy(after["inventory"][0]))
        result = compare(before, after)
        self.assertEqual(result["changed_value_count"], 1)
        self.assertEqual(at(result, "/inventory/1", kind="added")["after"]["type"], "object")

        after = deepcopy(before)
        after["inventory"] = [deepcopy(before["inventory"][0]), deepcopy(before["inventory"][0])]
        result = compare(after, before)
        self.assertEqual(at(result, "/inventory/1", kind="removed")["before"]["value_sha256"],
                         canonical_value_sha256(before["inventory"][0]))

    def test_reordered_rows_are_reported_as_relocations(self):
        before = report()
        before["inventory"] = [
            {"name": "Carrot", "count": 1},
            {"name": "Apple", "count": 2},
        ]
        after = deepcopy(before)
        after["inventory"].reverse()
        result = compare(before, after)
        self.assertFalse(result["changes"])
        self.assertEqual(result["ordering_change_count"], 2)
        self.assertEqual(
            {(item["before_index"], item["after_index"]) for item in result["ordering_changes"]},
            {(0, 1), (1, 0)},
        )
        self.assertTrue(result["review_required"])

    def test_exact_row_matching_avoids_shift_cascade_but_reports_order(self):
        before = report()
        before["inventory"] = [
            {"name": "Carrot", "count": 1},
            {"name": "Apple", "count": 2},
        ]
        after = {**deepcopy(before), "inventory": [{"name": "Pear", "count": 3}] + deepcopy(before["inventory"])}
        result = compare(before, after)
        self.assertEqual(at(result, "/inventory/0", kind="added")["after"]["value_sha256"],
                         canonical_value_sha256(after["inventory"][0]))
        self.assertEqual(result["ordering_change_count"], 2)

    def test_shifted_list_removal_has_no_false_absent_counterpart_pointer(self):
        before = report()
        before["inventory"] = [
            {"name": "Carrot", "count": 1},
            {"name": "Apple", "count": 2},
            {"name": "Pear", "count": 3},
        ]
        after = deepcopy(before)
        after["inventory"] = [
            {"name": "Kiwi", "count": 9},
            {"name": "Pear", "count": 3},
        ]
        result = compare(before, after)
        removed = at(result, "/inventory/1", kind="removed")
        self.assertIsNone(removed["after"]["pointer"])
        self.assertFalse(removed["after"]["present"])
        self.assertEqual(removed["before"]["value_sha256"],
                         canonical_value_sha256(before["inventory"][1]))

        # Every present side reference must resolve to the bound report value;
        # the absent side is intentionally the only pointerless reference.
        def resolve(document, pointer):
            value = document
            for token in pointer.lstrip("/").split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) else value[token]
            return value

        for change in result["changes"]:
            for side, document in (("before", before), ("after", after)):
                reference = change[side]
                if not reference["present"]:
                    self.assertIsNone(reference["pointer"])
                    continue
                actual = resolve(document, reference["pointer"])
                self.assertEqual(reference["value_sha256"], canonical_value_sha256(actual))

    def test_missing_and_null_are_distinct(self):
        before = report()
        before["observation_metadata"]["nullable"] = None
        after = deepcopy(before)
        del after["observation_metadata"]["nullable"]
        result = compare(before, after)
        removed = at(result, "/observation_metadata/nullable", kind="removed")
        self.assertTrue(removed["before"]["present"])
        self.assertEqual(removed["before"]["type"], "null")
        self.assertFalse(removed["after"]["present"])
        self.assertEqual(removed["after"]["type"], "missing")

        before = report()
        after = deepcopy(before)
        after["observation_metadata"]["nullable"] = None
        added = at(compare(before, after), "/observation_metadata/nullable", kind="added")
        self.assertEqual(added["after"]["value_sha256"], canonical_value_sha256(None))

    def test_json_pointer_escapes_slash_and_tilde(self):
        before = report()
        before["observation_metadata"] = {"a/b~c": 1}
        after = deepcopy(before)
        after["observation_metadata"]["a/b~c"] = 2
        change = at(compare(before, after), "/observation_metadata/a~1b~0c")
        self.assertEqual(change["kind"], "changed")

    def test_different_source_recordings_are_refused(self):
        before = report()
        after = deepcopy(before)
        after["source"]["sha256"] = "other-source"
        with self.assertRaises(ValueError):
            compare(before, after)


if __name__ == "__main__":
    unittest.main()
