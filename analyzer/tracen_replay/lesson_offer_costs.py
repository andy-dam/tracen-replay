"""Join source-visible lesson prices with a repeated request and receipt.

This module explains a lesson cost when the confirmation projection is partly
unreadable.  A displayed offer price is evidence for a cost, but it is never a
purchase by itself.  The caller remains responsible for attaching the result
to a lesson receipt or transaction.

The join is deliberately strict:

* the outcome must contain exactly one named lesson receipt;
* the request title must repeat exactly at distinct timestamps;
* the selected offer title must repeat in the final continuous menu visit;
* every price used to fill a missing projection field must repeat at distinct
  timestamps, and known prices must agree with known projections;
* the request-to-receipt path may contain one nearby transition menu frame but
  no other screen, acquisition, or sustained menu return.

Unknown projection and price fields remain unknown.  No zero is manufactured
from a missing value, and no post-action balance is used by this helper.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from typing import Any

from .gameplay import CURRENCIES


MAX_COUNTER = 1_000_000
_ACQUISITION_KINDS = {"named_acquisition", "song_learned"}
_INTERVENING_MUTATION_KINDS = _ACQUISITION_KINDS | {
    "performance_change",
    "stat_change",
    "currency_change",
    "skill_change",
    "skill_purchase",
    "lesson_purchase",
    "race_result",
}
_ALLOWED_PATH_SCREENS = {"lesson_confirmation", "lesson_selection", "unknown"}
_REJECT_OFFER_REASONS = {
    "uncertain_title",
    "ambiguous_or_missing_title",
    "ambiguous_cost_label",
    "uncertain_cost_label",
    "clipped_or_invalid_cost_row",
}


def _integer(value: Any, *, nonnegative: bool = True) -> bool:
    if type(value) is not int:
        return False
    if abs(value) > MAX_COUNTER:
        return False
    return not nonnegative or value >= 0


def _timestamp(row: Any) -> int | None:
    if not isinstance(row, dict) or type(row.get("source_timestamp_ms")) is not int:
        return None
    return row["source_timestamp_ms"]


def _time(value: Any) -> bool:
    """Validate a source timestamp without applying the counter bound."""

    return type(value) is int and value >= 0


def _evidence(row: dict[str, Any]) -> str | None:
    value = row.get("evidence")
    return value if isinstance(value, str) and value else None


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or not 0 <= value <= 100:
        return None
    return value


def _box(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4 or any(type(item) is not int for item in value):
        return None
    left, top, right, bottom = value
    if min(value) < 0 or right <= left or bottom <= top:
        return None
    return value


def _row_facts(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    facts = row.get("facts", {})
    return facts if isinstance(facts, dict) else None


def _receipt_name(event: Any) -> str | None:
    if not isinstance(event, dict) or not isinstance(event.get("effects"), list):
        return None
    receipts = [
        effect
        for effect in event["effects"]
        if isinstance(effect, dict) and effect.get("kind") in _ACQUISITION_KINDS
    ]
    if len(receipts) != 1:
        return None
    name = receipts[0].get("name")
    return name if isinstance(name, str) and name else None


def _request_name_rows(
    group: Any, name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    if not isinstance(group, list):
        return None
    if not group:
        return None
    valid_group: list[dict[str, Any]] = []
    exact: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for row in group:
        timestamp = _timestamp(row)
        facts = _row_facts(row)
        if timestamp is None or facts is None or row.get("screen") not in {
            "lesson_confirmation",
            "unknown",
        }:
            return None
        valid_group.append(row)
        names = facts.get("name_candidates", [])
        if not isinstance(names, list):
            return None
        if len(names) > 1 or any(not isinstance(candidate, str) for candidate in names):
            return None
        if len(names) == 1:
            seen_names.add(names[0])
            if names[0] == name:
                exact.append(row)
    if seen_names - {name}:
        return None
    exact_evidence = [_evidence(row) for row in exact]
    if (
        len({row["source_timestamp_ms"] for row in exact}) < 2
        or any(value is None for value in exact_evidence)
        or len(set(exact_evidence)) < 2
    ):
        return None
    return valid_group, exact


def _projection_readings(group: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]] | None:
    readings: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in group:
        facts = _row_facts(row)
        assert facts is not None
        projection = facts.get("projected_performance_points")
        if projection is None:
            continue
        if not isinstance(projection, dict):
            return None
        for field in CURRENCIES:
            value = projection.get(field)
            if value is None:
                continue
            if not _integer(value):
                return None
            readings[field].append(
                dict(
                    timestamp_ms=row["source_timestamp_ms"],
                    value=value,
                    evidence=_evidence(row),
                )
            )
    return readings


def _source_title_name(offer: Any) -> str | None:
    """Return a source-proved display title when OCR kept a note alias.

    The rich lesson adapter retains the neural ``title.text`` for immutable
    sidecar identity and puts its source-pixel display name in ``name``.  A
    cost join may therefore see ``Present March >`` in the raw title and
    ``Present March ♪`` in the same offer.  Require the exact shared detector
    method and a mechanically valid suffix before accepting that alias.
    """
    if not isinstance(offer, dict):
        return None
    title = offer.get("title")
    if not isinstance(title, dict) or not isinstance(title.get("text"), str):
        return None
    name = offer.get("name")
    if not isinstance(name, str) or not name:
        return None
    title_text = title["text"]
    if name == title_text:
        return name
    symbol = offer.get("source_symbol")
    if not isinstance(symbol, dict) \
            or symbol.get("method") != "source_title_note_stem_flag_head" \
            or symbol.get("symbol") != "♪" \
            or symbol.get("independent_observations") is not False:
        return None
    base = re.sub(r"\s*[>▶→]\s*$", "", title_text).rstrip()
    if not base or name != base + " ♪":
        return None
    return name


def _offer_title(offer: Any) -> str | None:
    if not isinstance(offer, dict):
        return None
    source_name = _source_title_name(offer)
    if source_name is not None:
        return source_name
    title = offer.get("title")
    if isinstance(title, dict):
        title = title.get("text")
    return title if isinstance(title, str) and title else None


def _offer_prices(offer: dict[str, Any]) -> dict[str, int] | None:
    """Return known source prices, or ``None`` for malformed price structure."""

    prices = offer.get("prices")
    values: dict[str, int] = {}
    if not isinstance(prices, list):
        # A title-only observation is useful for repeated identity but supplies
        # no price evidence.
        return {} if prices is None else None
    if len(prices) != len(CURRENCIES):
        return None
    seen_fields: set[str] = set()
    for price in prices:
        if not isinstance(price, dict) or price.get("field") not in CURRENCIES:
            return None
        field = price["field"]
        if field in seen_fields:
            return None
        seen_fields.add(field)
        value = price.get("value")
        status = price.get("status")
        if value is None:
            if status != "unknown":
                return None
            unknown_reason = price.get("unknown_reason")
            if unknown_reason in {"clipped_or_invalid_cost_row", "clipped_geometry"}:
                return None
            continue
        confidence = _confidence(price.get("confidence"))
        if status != "accepted" or confidence is None:
            return None
        if not _integer(value):
            return None
        if confidence < 97:
            # A zero price is drawn dim and reads with low confidence; it is
            # still a zero. Any other low-confidence price is unknown for its
            # field rather than a reason to discard the whole card.
            if value == 0 and confidence >= 50:
                values[field] = 0
            continue
        values[field] = value
    if seen_fields != set(CURRENCIES):
        return None
    return values


def _offer_is_usable(offer: Any) -> bool:
    """Validate title, cost-label and geometry metadata before using a card."""

    if not isinstance(offer, dict):
        return False
    if type(offer.get("card_index")) is not int or offer["card_index"] < 0:
        return False
    if "status" in offer and offer.get("status") not in {"complete", "unknown"}:
        return False
    reasons = offer.get("unknown_reasons", [])
    if not isinstance(reasons, list) or any(not isinstance(reason, str) for reason in reasons):
        return False
    if any(reason in _REJECT_OFFER_REASONS for reason in reasons):
        return False
    title = offer.get("title")
    if not isinstance(title, dict) or not isinstance(title.get("text"), str) or not title["text"]:
        return False
    title_confidence = _confidence(title.get("confidence"))
    if title_confidence is None or title_confidence < 97:
        return False
    cost_label = offer.get("cost_label")
    if cost_label is not None:
        if not isinstance(cost_label, dict) or not isinstance(cost_label.get("text"), str) \
                or not cost_label["text"]:
            return False
        cost_confidence = _confidence(cost_label.get("confidence"))
        if cost_confidence is None or cost_confidence < 97:
            return False
        if "box" in cost_label and _box(cost_label.get("box")) is None:
            return False
    source_boxes = offer.get("source_price_boxes")
    crop_boxes = offer.get("crop_price_boxes")
    if source_boxes is not None or crop_boxes is not None:
        if not isinstance(source_boxes, list) or not isinstance(crop_boxes, list) \
                or len(source_boxes) != len(CURRENCIES) or len(crop_boxes) != len(CURRENCIES):
            return False
        if any(_box(source) is None or _box(crop) is None for source, crop in zip(source_boxes, crop_boxes)):
            return False
    return True


def _final_menu_rows(before_rows: Any, request_start: int) -> list[dict[str, Any]] | None:
    if not isinstance(before_rows, list):
        return None
    ordered: list[dict[str, Any]] = []
    for row in before_rows:
        timestamp = _timestamp(row)
        if timestamp is None:
            return None
        if timestamp < request_start:
            ordered.append(row)
    ordered.sort(key=lambda row: row["source_timestamp_ms"])
    if not ordered:
        return None

    # Use only the final contiguous lesson-selection suffix.  Earlier menu
    # visits cannot provide identity or prices for this request.
    suffix: list[dict[str, Any]] = []
    for row in reversed(ordered):
        if row.get("screen") != "lesson_selection":
            break
        if suffix and suffix[-1]["source_timestamp_ms"] - row["source_timestamp_ms"] > 500:
            break
        suffix.append(row)
    suffix.reverse()
    return suffix or None


def _offer_readings(
    menu_rows: list[dict[str, Any]], name: str
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]] | None:
    """Collect exact-title offer rows and known prices from the menu suffix."""

    title_rows: list[dict[str, Any]] = []
    values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in menu_rows:
        facts = _row_facts(row)
        assert facts is not None
        # The rich adapter carries the source-pixel title alias while the
        # legacy cost sidecar keeps the raw neural title.  Prefer the rich
        # offer when present; retain the legacy path for rows produced before
        # the adapter was wired into the fresh pipeline.
        preview = facts.get("lesson_offer_preview")
        if isinstance(preview, dict) and isinstance(preview.get("offers"), list):
            offers = preview["offers"]
        else:
            offers = facts.get("lesson_offer_observations", row.get("lesson_offer_observations"))
        if offers is None:
            continue
        if not isinstance(offers, list):
            return None
        matching = [offer for offer in offers if _offer_title(offer) == name]
        if len(matching) > 1:
            # Two same-named cards in one frame are not safely attributable.
            return None
        if not matching:
            continue
        offer = matching[0]
        # A frame whose card read with an uncertain title or cost label is
        # skipped; the card's identity and prices come from its clean frames,
        # of which at least two distinct ones are still required below.
        if not _offer_is_usable(offer):
            continue
        title = offer.get("title") if isinstance(offer, dict) else None
        if not isinstance(title, dict) or (_confidence(title.get("confidence")) or 0) < 97:
            continue
        if title.get("text") != name and _source_title_name(offer) != name:
            return None
        price_values = _offer_prices(offer)
        if price_values is None:
            continue
        evidence = _evidence(row)
        title_rows.append(
            dict(
                timestamp_ms=row["source_timestamp_ms"],
                evidence=evidence,
                card_index=offer.get("card_index"),
            )
        )
        for field, value in price_values.items():
            values[field].append(
                dict(
                    timestamp_ms=row["source_timestamp_ms"],
                    value=value,
                    evidence=evidence,
                    card_index=offer.get("card_index"),
                )
            )
    if len({row["timestamp_ms"] for row in title_rows}) < 2:
        return None
    if len({row["card_index"] for row in title_rows}) != 1:
        return None
    title_evidence = [row["evidence"] for row in title_rows]
    if not title_evidence or any(not isinstance(value, str) or not value for value in title_evidence):
        return None
    if len(set(title_evidence)) < 2:
        return None
    return title_rows, values


def _unique_value(readings: list[dict[str, Any]]) -> tuple[int | None, list[dict[str, Any]]]:
    """The one value the readings agree on; a value read once loses to one read twice."""
    counts = Counter(reading["value"] for reading in readings)
    if len(counts) == 1:
        return next(iter(counts)), readings
    repeated = [value for value, n in counts.items() if n >= 2]
    if len(repeated) == 1 and all(n == 1 for value, n in counts.items() if value != repeated[0]):
        return repeated[0], [reading for reading in readings if reading["value"] == repeated[0]]
    return None, []


def _repeated_proof(readings: list[dict[str, Any]]) -> bool:
    """Require distinct source timestamps and distinct nonempty evidence."""

    if len(readings) < 2:
        return False
    timestamps = {reading.get("timestamp_ms") for reading in readings}
    evidence = [reading.get("evidence") for reading in readings]
    return (
        len(timestamps) >= 2
        and all(isinstance(value, str) and value for value in evidence)
        and len(set(evidence)) >= 2
    )


def _initial_proof(menu_rows: list[dict[str, Any]], initial: dict[str, int]) -> list[dict[str, Any]]:
    proof: list[dict[str, Any]] = []
    for row in menu_rows:
        facts = _row_facts(row)
        if facts is None:
            continue
        values = facts.get("performance_points")
        if not isinstance(values, dict) or any(values.get(field) != initial[field] for field in initial):
            continue
        if any(not _integer(values.get(field)) for field in initial):
            continue
        evidence = _evidence(row)
        if evidence:
            proof.append(dict(timestamp_ms=row["source_timestamp_ms"], evidence=evidence))
    return proof


def _request_to_receipt_is_clean(
    readings: Any, group: list[dict[str, Any]], event: dict[str, Any], name: str
) -> bool:
    if not isinstance(readings, list):
        return False
    event_start = event.get("first_seen_ms")
    event_end = event.get("last_seen_ms")
    request_end = max(row["source_timestamp_ms"] for row in group)
    if not _time(event_start) or not _time(event_end):
        return False
    if event_end < event_start or event_start <= request_end:
        return False

    between: list[dict[str, Any]] = []
    for row in readings:
        timestamp = _timestamp(row)
        if timestamp is None:
            return False
        if request_end < timestamp < event_start:
            between.append(row)
    between.sort(key=lambda row: row["source_timestamp_ms"])
    path_times = [request_end] + [row["source_timestamp_ms"] for row in between] + [event_start]
    if any(right - left > 500 for left, right in zip(path_times, path_times[1:])):
        return False

    returned_menu: list[dict[str, Any]] = []
    for row in between:
        screen = row.get("screen")
        if screen not in _ALLOWED_PATH_SCREENS:
            return False
        if screen == "lesson_selection":
            returned_menu.append(row)
        if screen == "lesson_confirmation":
            facts = _row_facts(row)
            if facts is None:
                return False
            names = facts.get("name_candidates", [])
            if not isinstance(names, list):
                return False
            # A currency label ('Da') bleeding into the title box is not a name.
            names = [n for n in names if not (isinstance(n, str) and len(n.strip()) <= 2)]
            if len(names) > 1:
                return False
            if names and names != [name]:
                # The exact receipt name was already checked above; this
                # comparison only protects the intervening path.
                return False
        effects = row.get("effects", [])
        if not isinstance(effects, list):
            return False
        if any(
            isinstance(effect, dict) and effect.get("kind") in _INTERVENING_MUTATION_KINDS
            for effect in effects
        ):
            return False
        facts = _row_facts(row)
        if facts is None:
            return False
        if facts.get("awarded_performance_gains"):
            return False

    if len(returned_menu) > 1:
        return False
    if returned_menu and event_start - returned_menu[0]["source_timestamp_ms"] > 500:
        return False
    return True


def join_lesson_cost(
    readings: Any,
    event: Any,
    group: Any,
    before_rows: Any,
    initial: Any,
) -> dict[str, Any] | None:
    """Return a fully evidenced lesson cost, or ``None`` when unresolved.

    ``group`` is the repeated lesson-confirmation request. ``before_rows`` is
    the preceding menu visit and may carry ``facts.lesson_offer_observations``
    produced by :mod:`lesson_offer_refinement`. ``initial`` is the already
    selected repeated pre-request balance. Inputs are never mutated.
    """

    name = _receipt_name(event)
    if name is None or not isinstance(event, dict):
        return None
    request_result = _request_name_rows(group, name)
    if request_result is None:
        return None
    request_group, request_rows = request_result
    request_start = min(row["source_timestamp_ms"] for row in request_group)
    menu_rows = _final_menu_rows(before_rows, request_start)
    if menu_rows is None:
        return None
    offer_result = _offer_readings(menu_rows, name)
    if offer_result is None:
        return None
    offer_title_rows, offer_values = offer_result
    if not _request_to_receipt_is_clean(readings, request_group, event, name):
        return None
    if not isinstance(initial, dict) or not any(_integer(initial.get(field)) for field in CURRENCIES):
        return None
    initial_proof = _initial_proof(menu_rows, {f: v for f, v in initial.items() if _integer(v)})
    if not _repeated_proof(initial_proof):
        return None

    projections = _projection_readings(request_group)
    if projections is None:
        return None
    projection_values: dict[str, int | None] = {}
    projection_proofs: dict[str, list[dict[str, Any]]] = {}
    for field in CURRENCIES:
        value, proof = _unique_value(projections.get(field, []))
        if projections.get(field) and value is None:
            return None
        projection_values[field] = value
        projection_proofs[field] = proof

    offer_unique_values: dict[str, int | None] = {}
    offer_proofs: dict[str, list[dict[str, Any]]] = {}
    for field in CURRENCIES:
        value, proof = _unique_value(offer_values.get(field, []))
        if offer_values.get(field) and value is None:
            return None
        offer_unique_values[field] = value
        offer_proofs[field] = proof

    costs: dict[str, int] = {}
    fields: dict[str, dict[str, Any]] = {}
    for field in CURRENCIES:
        start_value = initial.get(field)
        projected = projection_values[field]
        offer_price = offer_unique_values[field]
        repeated_projection = _repeated_proof(projection_proofs[field]) and _integer(start_value)
        repeated_offer = _repeated_proof(offer_proofs[field])

        projected_cost = None
        if projected is not None and _integer(start_value):
            projected_cost = start_value - projected
            if not 0 <= projected_cost <= start_value:
                return None
        if offer_price is not None and _integer(start_value) and not 0 <= offer_price <= start_value:
            return None

        # Every known price/projection must agree, even if one side has not
        # repeated enough to establish the cost on its own.
        if projected_cost is not None and offer_price is not None and projected_cost != offer_price:
            return None

        if repeated_projection:
            cost = projected_cost
            basis = "repeated_request_projection_difference"
            if repeated_offer:
                basis = "repeated_request_projection_difference_confirmed_by_repeated_offer_price"
            elif offer_price is not None:
                basis = "repeated_request_projection_difference_checked_against_offer_price"
        elif repeated_offer:
            cost = offer_price
            basis = "repeated_named_offer_price"
            if projected is not None:
                basis = "repeated_named_offer_price_checked_against_request_projection"
        else:
            return None
        if cost is None:
            return None
        costs[field] = cost
        fields[field] = dict(
            cost=cost,
            basis=basis,
            initial_value=start_value,
            projected_value=projected,
            offer_price=offer_price,
            projection_evidence=projection_proofs[field],
            offer_evidence=offer_proofs[field],
        )

    if sum(costs.values()) <= 0:
        return None
    return dict(
        cost=costs,
        total_cost=sum(costs.values()),
        fields=fields,
        receipt_name=name,
        request=dict(
            title=name,
            timestamps_ms=sorted({row["source_timestamp_ms"] for row in request_group}),
            title_timestamps_ms=sorted({row["source_timestamp_ms"] for row in request_rows}),
            evidence=[proof for proof in (_evidence(row) for row in request_group) if proof],
            title_evidence=[proof for proof in (_evidence(row) for row in request_rows) if proof],
        ),
        initial=dict(
            timestamps_ms=sorted({proof["timestamp_ms"] for proof in initial_proof}),
            evidence=[proof["evidence"] for proof in initial_proof],
        ),
        offer=dict(
            title=name,
            timestamps_ms=sorted({row["timestamp_ms"] for row in offer_title_rows}),
            evidence=[proof for proof in (row["evidence"] for row in offer_title_rows) if proof],
        ),
        receipt=dict(
            event_id=event.get("id"),
            first_seen_ms=event.get("first_seen_ms"),
            last_seen_ms=event.get("last_seen_ms"),
            evidence=event.get("evidence"),
        ),
        basis="repeated_request_projection_and_named_offer_prices",
        independent_observations=False,
    )


__all__ = ["join_lesson_cost"]
