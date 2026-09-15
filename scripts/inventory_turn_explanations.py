"""Inventory missing turn endpoints and uncertain attribution without guessing.

The inventory is deliberately a report-only operation. It does not replay a
recording or turn a missing value into zero. In addition to the historical
summary, it exposes one row per missing opening field so that a partial source
snapshot is distinguishable from a completely unavailable opening state.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path


CHANNEL_FIELDS = {
    "stats": ("speed", "stamina", "power", "guts", "wit", "skill_points"),
    "performance": ("dance", "passion", "vocal", "visual", "composure"),
}


def _is_int(value):
    """Return whether *value* is an observed integer, while rejecting bool."""

    return type(value) is int


def _values_for_reading(row, channel):
    if channel == "stats":
        return (row.get("stats") or {}).get("values") or {}
    return (row.get("facts") or {}).get("performance_points") or {}


def _pre_action_observations(data, turn, channel, end, fields):
    observations = []
    for index, row in enumerate(data.get("readings", [])):
        time = row.get("source_timestamp_ms")
        if type(time) is not int or not turn["start_ms"] <= time < end:
            continue
        values = _values_for_reading(row, channel)
        seen = {
            field: values[field]
            for field in fields
            if isinstance(values, dict) and _is_int(values.get(field))
        }
        if not seen:
            continue
        observations.append(
            dict(
                source_ref=f"/gameplay_tracking/readings/{index}",
                time_ms=time,
                evidence=row.get("evidence"),
                values=seen,
                complete=len(seen) == len(fields),
            )
        )
    return observations


def _observation_metrics(observations):
    times = [row["time_ms"] for row in observations if type(row.get("time_ms")) is int]
    distinct = set(times)
    return dict(
        observation_count=len(observations),
        distinct_source_timestamp_count=len(distinct),
        duplicate_view_count=max(0, len(times) - len(distinct)),
    )


def _opening_values(opening):
    values = opening.get("values") if isinstance(opening, dict) else None
    return values if isinstance(values, dict) else {}


def _state_missing_fields(opening, fields):
    values = _opening_values(opening)
    return [field for field in fields if not _is_int(values.get(field))]


def _value_state(opening, field):
    if opening is None:
        return "unobserved_opening"
    values = _opening_values(opening)
    if field not in values:
        return "absent"
    if values[field] is None:
        return "null"
    if not _is_int(values[field]):
        return "non_integer"
    return "observed_integer_zero" if values[field] == 0 else "observed_integer"


def _opening_record(data, ledger, turn, channel, fields, state, action_end):
    """Return an opening-gap record, or ``None`` for a complete opening."""

    del ledger  # Kept in the signature to make the report/ledger provenance explicit.
    opening = state.get("opening") if isinstance(state, dict) else None
    opening_status = state.get("opening_status") if isinstance(state, dict) else None
    missing_fields = _state_missing_fields(opening, fields)

    # Reports from before opening_status was added use a null opening as the
    # only indication of a missing state.
    full_missing = opening is None or opening_status == "not_observed_before_action"
    if not full_missing and not missing_fields:
        return None

    observations = _pre_action_observations(data, turn, channel, action_end, fields)
    complete = [row for row in observations if row["complete"]]
    if full_missing:
        classification = (
            "complete_parsed_snapshot_rejected"
            if complete
            else "partial_parsed_snapshots_only"
            if observations
            else "no_parsed_pre_action_values"
        )
        missing_kind = "full_missing"
        cause = classification
        missing_fields = list(fields)
        observed_fields = []
    else:
        # The source provided an opening state, but the individual absent or
        # null fields remain unobserved. Keep this separate from no opening.
        classification = "partial_opening_state"
        missing_kind = "partial_missing"
        cause = "partial_opening_field_missing"
        observed_fields = [field for field in fields if field not in missing_fields]

    record = dict(
        turn_id=turn["id"],
        channel=channel,
        start_ms=turn["start_ms"],
        pre_action_end_ms=action_end,
        window_kind=turn.get("window_kind"),
        classification=classification,
        missing_kind=missing_kind,
        opening_status=opening_status or "not_observed_before_action",
        missing_fields=list(missing_fields),
        observed_fields=observed_fields,
        missing_field_value_states={field: _value_state(opening, field) for field in missing_fields},
        source_visibility=(
            "partially_observed_source_opening"
            if not full_missing
            else "not_determined_by_parsed_observations"
        ),
        observations=observations,
    )
    if isinstance(opening, dict):
        for key in (
            "source_ref",
            "values_ref",
            "supporting_source_refs",
            "observed_at_ms",
            "values",
            "evidence",
            "basis",
            "exact_turn_boundary",
            "field_corroboration",
        ):
            if key in opening:
                record[key] = opening[key]
    record["cause"] = cause
    record.update(_observation_metrics(observations))
    if isinstance(opening, dict):
        opening_times = []
        for proofs in (opening.get("field_corroboration") or {}).values():
            for proof in proofs if isinstance(proofs, list) else []:
                if type(proof.get("observed_at_ms")) is int:
                    opening_times.append(proof["observed_at_ms"])
        record["opening_corroboration_timestamp_count"] = len(set(opening_times))
    return record


def _field_level_rows(opening_states, missing_comparisons, unresolved):
    rows = []
    for state in opening_states:
        for field in state["missing_fields"]:
            rows.append(
                dict(
                    scope="opening",
                    turn_id=state["turn_id"],
                    channel=state["channel"],
                    field=field,
                    missing_kind=state["missing_kind"],
                    cause=state["cause"],
                    classification=state["classification"],
                    opening_status=state["opening_status"],
                    value_state=state["missing_field_value_states"].get(field),
                    source_refs=(
                        [state["values_ref"]]
                        if state.get("values_ref")
                        else [row["source_ref"] for row in state["observations"]]
                    ),
                    evidence=(
                        state.get("evidence")
                        or [row.get("evidence") for row in state["observations"] if row.get("evidence")]
                    ),
                )
            )
    for row in missing_comparisons:
        rows.append(
            dict(
                scope="endpoint",
                turn_id=row["turn_id"],
                channel=row["channel"],
                field=row["field"],
                missing_kind="endpoint_missing",
                cause="missing_endpoint",
                before_missing=row["before_missing"],
                after_missing=row["after_missing"],
            )
        )
    for row in unresolved:
        rows.append(
            dict(
                scope="attribution",
                turn_id=row["turn_id"],
                channel=row["channel"],
                field=row["field"],
                missing_kind="attribution_unresolved",
                cause=";".join(
                    sorted(
                        {
                            reason
                            for cause in row.get("causes", [])
                            for reason in cause.get("reasons", [])
                        }
                    )
                ),
                unresolved_change=row["unresolved_change"],
            )
        )
    return rows


def inventory(report):
    data = report["gameplay_tracking"]
    ledger = report["turn_ledger"]
    accounting = report["causal_accounting"]
    turns = {turn["id"]: turn for turn in ledger["turns"]}
    contributions = {item["id"]: item for item in accounting["contributions"]}

    missing = []
    unresolved = []
    reasons = Counter()
    for comparison in accounting["turn_transitions"]:
        for field in comparison["fields"]:
            if field["status"] == "missing_endpoint":
                missing.append(
                    dict(
                        turn_id=comparison["turn_id"],
                        channel=comparison["channel"],
                        field=field["field"],
                        before_missing=field["before"] is None,
                        after_missing=field["after"] is None,
                    )
                )
            elif field["status"] == "unresolved_attribution":
                causes = []
                for claim in field["ambiguous_contributions"]:
                    contribution = contributions[claim["contribution_ref"]]
                    causes.append(dict(contribution=contribution, reasons=claim["reasons"]))
                reason = ",".join(sorted({reason for item in causes for reason in item["reasons"]}))
                reasons[reason] += 1
                unresolved.append(
                    dict(
                        turn_id=comparison["turn_id"],
                        channel=comparison["channel"],
                        field=field["field"],
                        unresolved_change=field["unresolved_change"],
                        causes=causes,
                    )
                )

    missing_states = []
    for turn in ledger["turns"]:
        actions = [
            event["first_seen_ms"]
            for event in ledger.get("timeline", [])
            if event.get("turn_id") == turn["id"] and event.get("kind") == "committed_action"
        ]
        action_end = min(actions, default=turn["end_ms"])
        for channel, fields in CHANNEL_FIELDS.items():
            state = (turn.get("states") or {}).get(channel) or {}
            record = _opening_record(data, ledger, turn, channel, fields, state, action_end)
            if record is not None:
                missing_states.append(record)

    field_rows = _field_level_rows(missing_states, missing, unresolved)
    eligible = [
        turn
        for turn in turns.values()
        if turn.get("window_kind") == "calendar_turn" and turn.get("action_status") == "one_action"
    ]
    endpoint_complete = []
    direct_numeric = []
    derived_numeric = []
    for turn in eligible:
        comparisons = [
            comparison
            for comparison in accounting["turn_transitions"]
            if comparison["turn_id"] == turn["id"]
        ]
        statuses = [field["status"] for comparison in comparisons for field in comparison["fields"]]
        if len(statuses) != 11 or any(
            status in ("missing_endpoint", "unordered_endpoints") for status in statuses
        ):
            continue
        endpoint_complete.append(turn["id"])
        if all(status == "balanced_observations" for status in statuses):
            direct_numeric.append(turn["id"])
        elif all(status in ("balanced_observations", "balanced_with_derived_changes") for status in statuses):
            derived_numeric.append(turn["id"])

    opening_causes = Counter(state["classification"] for state in missing_states)
    opening_kinds = Counter(state["missing_kind"] for state in missing_states)
    field_causes = Counter(row["cause"] for row in field_rows)
    value_states = Counter(row.get("value_state") for row in field_rows if row["scope"] == "opening")
    opening_observations = [
        observation
        for state in missing_states
        for observation in state.get("observations", [])
    ]
    opening_observation_metrics = _observation_metrics(opening_observations)
    observed_zero_fields = 0
    observed_integer_fields = 0
    null_fields = 0
    absent_fields = 0
    non_integer_fields = 0
    for turn in ledger["turns"]:
        for channel, fields in CHANNEL_FIELDS.items():
            opening = ((turn.get("states") or {}).get(channel) or {}).get("opening")
            values = _opening_values(opening)
            for field in fields:
                if _is_int(values.get(field)):
                    observed_integer_fields += 1
                    observed_zero_fields += values[field] == 0
                elif opening is not None and field in values and values[field] is None:
                    null_fields += 1
                elif opening is not None and field in values:
                    non_integer_fields += 1
                elif opening is not None:
                    absent_fields += 1
    field_inventory = dict(
        rows=field_rows,
        missing_opening_fields=[row for row in field_rows if row["scope"] == "opening"],
        summary=dict(
            total_fields=len(field_rows),
            missing_opening_fields=sum(row["scope"] == "opening" for row in field_rows),
            full_missing_opening_fields=sum(
                row["scope"] == "opening" and row["missing_kind"] == "full_missing" for row in field_rows
            ),
            partial_missing_opening_fields=sum(
                row["scope"] == "opening" and row["missing_kind"] == "partial_missing" for row in field_rows
            ),
            endpoint_missing_fields=sum(row["scope"] == "endpoint" for row in field_rows),
            unresolved_attribution_fields=sum(row["scope"] == "attribution" for row in field_rows),
            causes=dict(field_causes),
            opening_value_states=dict(value_states),
            observed_opening_integer_fields=observed_integer_fields,
            observed_opening_zero_fields=observed_zero_fields,
            missing_opening_null_fields=null_fields,
            missing_opening_absent_fields=absent_fields,
            missing_opening_non_integer_fields=non_integer_fields,
            pre_action_observations=opening_observation_metrics,
        ),
    )

    summary = dict(
        missing_field_comparisons=len(missing),
        missing_opening_states=len(missing_states),
        missing_opening_causes=dict(opening_causes),
        missing_opening_kinds=dict(opening_kinds),
        missing_opening_fields=field_inventory["summary"]["missing_opening_fields"],
        full_missing_opening_fields=field_inventory["summary"]["full_missing_opening_fields"],
        partial_missing_opening_fields=field_inventory["summary"]["partial_missing_opening_fields"],
        field_level_cause_counts=dict(field_causes),
        opening_value_states=dict(value_states),
        observed_opening_integer_fields=observed_integer_fields,
        observed_opening_zero_fields=observed_zero_fields,
        missing_opening_null_fields=null_fields,
        missing_opening_absent_fields=absent_fields,
        missing_opening_non_integer_fields=non_integer_fields,
        opening_observation_count=opening_observation_metrics["observation_count"],
        opening_distinct_source_timestamp_count=opening_observation_metrics["distinct_source_timestamp_count"],
        opening_duplicate_view_count=opening_observation_metrics["duplicate_view_count"],
        unresolved_field_comparisons=len(unresolved),
        attribution_reasons=dict(reasons),
        eligible_dated_one_action_turns=len(eligible),
        fully_observed_endpoint_turns=len(endpoint_complete),
        fully_direct_numeric_accounting_turns=len(direct_numeric),
        numeric_accounting_with_derived_changes_turns=len(derived_numeric),
        independently_verified_complete_history_turns=None,
    )
    return dict(
        source_sha256=report["source"]["sha256"],
        missing_field_comparisons=missing,
        missing_opening_states=missing_states,
        missing_opening_fields=field_inventory["missing_opening_fields"],
        field_level_cause_inventory=field_inventory,
        unresolved_field_comparisons=unresolved,
        summary=summary,
        turn_refs=dict(
            fully_observed_endpoints=endpoint_complete,
            direct_numeric_accounting=direct_numeric,
            numeric_accounting_with_derived_changes=derived_numeric,
        ),
        limitations=[
            "Eligibility is a dated turn with one attributed committed action; all eleven resource fields must qualify.",
            "Direct numeric accounting is not independent verification of complete event history, hints or conditions.",
            "Complete-history count is unmeasured; do not interpret it as zero accurate turns.",
            "No parsed values does not establish that the source video is unreadable or unobservable.",
            "A partial opening contributes one row per absent or null field; repeated views are reported separately and do not create new states.",
            "Integer zero is an observed value; only absent, null or non-integer values remain missing.",
        ],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory(json.loads(args.report.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result["summary"]))
