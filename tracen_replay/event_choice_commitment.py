"""Conservative commitment witnesses for dialogue choice menus.

The gameplay reader can see a choice menu long before the player commits to an
option.  This module keeps those two facts separate.  A menu is only promoted
to a committed dialogue choice when a later observation contains an explicit
visual selection witness, currently a bilateral pair of selection marks, a
parser supplied witness with the same semantics, or a green selected-card
transition where the remaining menu cards visibly collapse.

The input is intentionally the small observation shape emitted by
``choice_evidence.observe``.  It contains card text, card geometry, and visual
selection marks; it does not require a list of known event names.  Text is used
only to associate an observed mark with one of the already visible cards.  No
effect, reward, timestamp, or expected answer is used to infer commitment.
"""

from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import math
import unicodedata
from typing import Any, Iterable, Mapping


SCHEMA = "tracen-replay/event-choice-commitment-v1"
"""Schema identifier for the emitted committed-choice observations."""

DEFAULT_MAX_MENU_GAP_MS = 1_500
"""Maximum time between adjacent observations in one visible menu."""

_TEXT_NEAR_THRESHOLD = 0.97
_MAX_COORDINATE = 2_000
_WITNESS_BASES = frozenset(
    {
        "bilateral_selection_marks",
        "selected_card_highlight_and_transition",
        "explicit_selected_card_transition",
    }
)
_GREEN_PROOF_KIND = "green_card_fill"
_GREEN_PROOF_DETECTOR = "choice_evidence.green_card_fill_v1"
_GREEN_CARD_LEFT = 175
_GREEN_CARD_RIGHT = 610
_GREEN_CARD_FILL_FRACTION = 0.85


def _green_card_proof(item: Mapping[str, Any], card: Mapping[str, Any]) -> dict[str, Any] | None:
    """Validate the parser-owned fill proof attached to a selected card.

    A lower-confidence selected OCR line is useful only when the source pixel
    observer also saw a sufficiently filled green card.  The count is bounded
    by the selected card geometry, so an arbitrary marker cannot turn a menu
    or a downstream effect into a commitment witness.
    """

    proof = item.get("selection_visual_proof")
    if not isinstance(proof, Mapping):
        return None
    if (proof.get("kind") != _GREEN_PROOF_KIND
            or proof.get("detector") != _GREEN_PROOF_DETECTOR):
        return None
    pixels = proof.get("green_pixels")
    if type(pixels) is not int or pixels < 0:
        return None
    top, bottom = card["card_y"]
    height = bottom - top
    width = _GREEN_CARD_RIGHT - _GREEN_CARD_LEFT
    minimum = math.ceil(height * width * _GREEN_CARD_FILL_FRACTION)
    maximum = math.floor(height * width)
    if pixels < minimum or pixels > maximum:
        return None
    return {
        "kind": _GREEN_PROOF_KIND,
        "detector": _GREEN_PROOF_DETECTOR,
        "green_pixels": pixels,
    }


def _selected_cards(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return parser-owned green-card transition witnesses.

    ``choice_evidence.observe`` emits these cards from a distinct green pixel
    mask.  They are a visual state transition, not a downstream effect.  Keep
    the set strict: a partial or ambiguous selected card cannot identify a
    committed option.
    """

    raw = row.get("selected_card_candidates")
    if not isinstance(raw, list):
        return []
    result = []
    for item in raw:
        card = _valid_card(item)
        confidence = item.get("confidence") if isinstance(item, Mapping) else None
        if card is None or type(confidence) not in (int, float):
            continue
        proof = _green_card_proof(item, card)
        confidence_valid = 0 <= confidence <= 100
        # Ordinary selected OCR remains subject to the established 97% gate.
        # Only a source pixel proof can authorize the scoped lower-confidence
        # path, and the stable-menu textual/geometry match is still required
        # by _match_selected_card below.
        if confidence_valid and (confidence >= 97 or proof is not None):
            if proof is not None:
                card["selection_visual_proof"] = proof
            result.append(card)
    return result


def _match_selected_card(options: list[dict[str, Any]], selected: Mapping[str, Any]) -> int | None:
    """Associate one selected card with exactly one stable option."""

    matches = []
    selected_y = selected["card_y"]
    for index, option in enumerate(options):
        option_y = option["card_y"]
        if any(abs(left - right) > 12 for left, right in zip(selected_y, option_y)):
            continue
        # The green transition is often captured while the card is moving and
        # its OCR can lose one short word.  Geometry establishes the slot;
        # retain a tight textual guard so a different option at that slot is
        # still rejected.
        compact_option = _compact(option["text"])
        compact_selected = _compact(selected["text"])
        if not compact_option or not compact_selected:
            continue
        if not (_text_agrees(option["text"], selected["text"])
                or SequenceMatcher(None, compact_option, compact_selected).ratio() >= 0.90):
            continue
        matches.append(index)
    return matches[0] if len(matches) == 1 else None


def _remaining_cards_match(
    options: list[dict[str, Any]],
    selected: Mapping[str, Any],
    remaining: list[dict[str, Any]],
) -> bool:
    """Require every visible post-selection card to retain its menu identity.

    A green card and a shorter card list establish a transition only when the
    shorter list is exactly the stable menu with the selected slot removed.
    Counting cards alone would let a changed card from another menu stand in
    for the untouched option and attach the commitment to the wrong menu.
    """

    selected_index = _match_selected_card(options, selected)
    if selected_index is None or len(remaining) != len(options) - 1:
        return False
    expected = [option for index, option in enumerate(options)
                if index != selected_index]
    return _near_menu(expected, remaining)


def _audit_menu(
    audit: dict[str, Any] | None,
    stable: Mapping[str, Any] | None,
    row: Mapping[str, Any] | None = None,
    *,
    status: str | None = None,
    reason: str | None = None,
) -> None:
    """Record why a stable menu did or did not become a committed choice."""

    if not isinstance(audit, dict) or not isinstance(stable, Mapping):
        return
    first_ms = stable.get("first_ms")
    if type(first_ms) is not int:
        return
    menus = audit.setdefault("menus", [])
    entry = next((item for item in menus
                  if isinstance(item, dict) and item.get("first_seen_ms") == first_ms), None)
    if entry is None:
        entry = {
            "first_seen_ms": first_ms,
            "last_seen_ms": stable.get("last_ms"),
            "options": [item["text"] for item in stable.get("options", [])],
            "status": "pending",
            "evidence": [],
            "reasons": [],
        }
        menus.append(entry)
    else:
        entry["last_seen_ms"] = stable.get("last_ms", entry.get("last_seen_ms"))
    evidence = []
    for item in stable.get("rows", []):
        evidence.extend(_row_evidence(item))
    if row is not None:
        evidence.extend(_row_evidence(row))
    entry["evidence"] = list(dict.fromkeys(entry.get("evidence", []) + evidence))
    if status is not None:
        # A terminal unsupported witness must not be downgraded to
        # ``selection_unobserved`` by the end-of-stream closeout below.
        priority = {"pending": 0, "selection_unobserved": 1,
                    "selection_unsupported": 2, "committed": 3}
        previous = entry.get("status", "pending")
        if priority.get(status, 0) >= priority.get(previous, 0):
            entry["status"] = status
    if reason and reason not in entry["reasons"]:
        entry["reasons"].append(reason)


def _audit_orphan_witness(
    audit: dict[str, Any] | None,
    row: Mapping[str, Any],
    *,
    reason: str,
) -> None:
    """Retain a visual witness that lacks a repeated menu to bind it to."""

    if not isinstance(audit, dict):
        return
    witnesses = audit.setdefault("orphan_witnesses", [])
    item = {
        "source_timestamp_ms": row.get("source_timestamp_ms"),
        "evidence": _row_evidence(row),
        "status": "selection_unsupported",
        "reason": reason,
    }
    if item not in witnesses:
        witnesses.append(item)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split()).strip()
    return value or None


def _compact(value: Any) -> str:
    """Normalize text for association without manufacturing OCR content."""

    value = _text(value) or ""
    value = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in value if character.isalnum())


def _text_agrees(left: Any, right: Any) -> bool:
    """Accept exact text or a small OCR spacing/character error.

    The near match is deliberately tight.  It lets a stable option survive a
    single dropped glyph (for example ``potential`` becoming ``pot ntial``),
    while a genuinely different option still invalidates the menu identity.
    """

    first, second = _compact(left), _compact(right)
    if not first or not second:
        return False
    if first == second:
        return True
    return SequenceMatcher(None, first, second).ratio() >= _TEXT_NEAR_THRESHOLD


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError):
        return None
    left, top, right, bottom = result
    if (not all(-_MAX_COORDINATE <= item <= _MAX_COORDINATE for item in result)
            or not left < right or not top < bottom):
        return None
    return result


def _card_y(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        top, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if (not all(-_MAX_COORDINATE <= item <= _MAX_COORDINATE for item in (top, bottom))
            or not top < bottom):
        return None
    return top, bottom


def _valid_card(card: Any) -> dict[str, Any] | None:
    if not isinstance(card, Mapping):
        return None
    text = _text(card.get("text"))
    box = _box(card.get("text_box"))
    card_y = _card_y(card.get("card_y"))
    if not text or box is None or card_y is None:
        return None
    return {
        "text": text,
        "text_box": [int(item) if item.is_integer() else item for item in box],
        "card_y": [int(item) if item.is_integer() else item for item in card_y],
        **({"confidence": card["confidence"]}
           if type(card.get("confidence")) in (int, float) else {}),
    }


def _cards(
    row: Mapping[str, Any],
    *,
    minimum_confidence: float | None = None,
) -> list[dict[str, Any]]:
    """Return a complete visible card set, preserving source order.

    A repeated full menu establishes its identity through slot completeness
    and repetition at two distinct timestamps (``_advance_pending``), exactly
    as the sealed v9-v12 implementations did; ordinary menu text carries no
    per-card confidence gate here.  The parser already separates high
    confidence ``offered_card_candidates`` from geometry-complete
    ``offered_card_slots``; requiring every slot to read above a fixed
    threshold discards readable menus whose one card dips to 95-96 while the
    highlight moves.  ``minimum_confidence`` is only supplied by the collapsed
    green-transition path, whose remaining cards must be ordinary readable OCR
    before they can stand in for the stable menu.
    """

    def menu_confidence(value: Any) -> bool:
        if minimum_confidence is None:
            return True
        return (type(value) in (int, float) and math.isfinite(value)
                and minimum_confidence <= value <= 100)

    slots = row.get("offered_card_slots")
    candidates = row.get("offered_card_candidates")
    # Slots retain unreadable card geometry.  An incomplete slot set cannot
    # establish the menu identity, even if one candidate happens to be clear.
    if isinstance(slots, list):
        if not slots or not all(_text(item.get("text")) for item in slots
                                if isinstance(item, Mapping)):
            return []
        raw_cards = slots
    else:
        if row.get("menu_text_complete") is False:
            return []
        raw_cards = candidates
    if not isinstance(raw_cards, list):
        return []
    if minimum_confidence is not None and any(
            not isinstance(item, Mapping)
            or not _text(item.get("text"))
            or not menu_confidence(item.get("confidence"))
            for item in raw_cards):
        return []
    result = []
    for raw in raw_cards:
        card = _valid_card(raw)
        if card is None:
            return []
        result.append(card)
    return result


def _same_geometry(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    if len(left) != len(right):
        return False
    for first, second in zip(left, right):
        first_y, second_y = first["card_y"], second["card_y"]
        if any(abs(a - b) > 4 for a, b in zip(first_y, second_y)):
            return False
        # Horizontal motion is a new layout, rather than an OCR variation.
        first_box, second_box = first["text_box"], second["text_box"]
        if any(abs(a - b) > 20 for a, b in zip(first_box, second_box)):
            return False
    return True


def _near_menu(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> bool:
    return (_same_geometry(left, right)
            and all(_text_agrees(first["text"], second["text"])
                    for first, second in zip(left, right)))


def _valid_mark_pair(pair: Any) -> dict[str, Any] | None:
    if not isinstance(pair, Mapping):
        return None
    left = _box(pair.get("left", {}).get("box") if isinstance(pair.get("left"), Mapping) else None)
    right = _box(pair.get("right", {}).get("box") if isinstance(pair.get("right"), Mapping) else None)
    if left is None or right is None:
        return None
    # The two marks must sit at the same vertical card edge.  Their expected
    # horizontal ordering prevents a decorative single marker from becoming a
    # selection witness.
    if left[0] >= right[0] or abs(left[1] - right[1]) > 10 or abs(left[3] - right[3]) > 10:
        return None
    if not (15 <= left[2] - left[0] <= 100 and 15 <= right[2] - right[0] <= 100):
        return None
    return {
        "left": {"box": [int(item) if item.is_integer() else item for item in left]},
        "right": {"box": [int(item) if item.is_integer() else item for item in right]},
    }


def _mark_pairs(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("selection_mark_pairs")
    if not isinstance(raw, list):
        return []
    result = []
    for pair in raw:
        valid = _valid_mark_pair(pair)
        if valid is not None:
            result.append(valid)
    return result


def _explicit_witness(row: Mapping[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Read a parser-owned selected-state witness, never a bare text field."""

    state = row.get("selected_state")
    if not isinstance(state, Mapping) or state.get("verified") is not True:
        return None, None
    basis = _text(state.get("basis"))
    selected = _text(state.get("selected_text", state.get("selected_option")))
    evidence = state.get("evidence")
    if (not selected or basis not in _WITNESS_BASES or not isinstance(evidence, list)
            or not evidence or any(not _text(item) for item in evidence)):
        return None, None
    return selected, {
        "basis": basis,
        "evidence": list(dict.fromkeys(_text(item) for item in evidence if _text(item))),
    }


def _match_mark(options: list[dict[str, Any]], pair: Mapping[str, Any]) -> int | None:
    left = pair["left"]["box"]
    right = pair["right"]["box"]
    matches = []
    for index, option in enumerate(options):
        text_top = option["text_box"][1]
        if (20 <= text_top - left[1] <= 70
                and 20 <= text_top - right[1] <= 70):
            matches.append(index)
    return matches[0] if len(matches) == 1 else None


def _match_explicit(options: list[dict[str, Any]], selected: str) -> int | None:
    matches = [index for index, option in enumerate(options)
               if _text_agrees(option["text"], selected)]
    return matches[0] if len(matches) == 1 else None


def _row_time(row: Mapping[str, Any]) -> int | None:
    value = row.get("source_timestamp_ms")
    return value if type(value) is int and value >= 0 else None


def _row_evidence(row: Mapping[str, Any]) -> list[str]:
    evidence = row.get("evidence")
    if isinstance(evidence, str) and evidence:
        return [evidence]
    if isinstance(evidence, (list, tuple)):
        return list(dict.fromkeys(item for item in evidence if _text(item)))
    return []


def _new_pending(row: Mapping[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    time = _row_time(row)
    return {
        "rows": [row],
        "options": deepcopy(cards),
        "first_ms": time,
        "last_ms": time,
        "conflicts": [],
    }


def _advance_pending(
    pending: dict[str, Any] | None,
    row: Mapping[str, Any],
    cards: list[dict[str, Any]],
    maximum_gap_ms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Advance a repeated menu and return ``(pending, stable)``."""

    time = _row_time(row)
    if time is None:
        return pending, None
    if pending is None or pending["last_ms"] is None or time - pending["last_ms"] > maximum_gap_ms:
        pending = _new_pending(row, cards)
    elif _near_menu(pending["options"], cards):
        pending["rows"].append(row)
        pending["last_ms"] = time
        # A menu is stable only after it has been seen at two timestamps.  The
        # first row is retained as the canonical wording and proof geometry.
    else:
        # A different menu must be repeated before it can replace a stable
        # identity.  This avoids losing a valid menu to one transient OCR row.
        pending = _new_pending(row, cards)
    distinct = {item.get("source_timestamp_ms") for item in pending["rows"]}
    if len(distinct) < 2:
        return pending, None
    return pending, {
        "rows": list(pending["rows"]),
        "options": deepcopy(pending["options"]),
        "first_ms": pending["first_ms"],
        "last_ms": pending["last_ms"],
    }


def reconstruct_committed_choices(
    observations: Iterable[Mapping[str, Any]],
    *,
    maximum_gap_ms: int = DEFAULT_MAX_MENU_GAP_MS,
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruct choices only where a visible selection witness is present.

    ``observations`` are normally rows returned by ``choice_evidence.observe``
    with ``source_timestamp_ms`` and ``evidence`` attached.  Known-screen
    boundary rows may set ``screen_boundary`` to clear a previous menu.  A
    menu must be readable at two distinct timestamps; a single selected or
    hovered card is therefore never enough.  When ``audit`` is supplied, it
    records stable menus with no visible selection as ``selection_unobserved``
    and parser witnesses that cannot be mapped safely as
    ``selection_unsupported``.
    """

    if type(maximum_gap_ms) is not int or maximum_gap_ms <= 0:
        raise ValueError("maximum_gap_ms must be a positive integer")
    if audit is not None:
        if not isinstance(audit, dict):
            raise TypeError("audit must be a dict when provided")
        audit.clear()
        audit.update({
            "schema_version": "tracen-replay/event-choice-commitment-audit-v1",
            "menus": [],
            "orphan_witnesses": [],
            "committed_count": 0,
        })
    rows = sorted(
        (row for row in observations if isinstance(row, Mapping) and _row_time(row) is not None),
        key=lambda row: row["source_timestamp_ms"],
    )
    pending: dict[str, Any] | None = None
    stable: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    used_stable_first_ms: set[int] = set()

    for row in rows:
        time = row["source_timestamp_ms"]
        # The source adapter retains same-time observations from different
        # physical proofs instead of blending their channels.  Such a row is
        # useful audit evidence, but it cannot safely identify which menu the
        # later selection belongs to.
        if row.get("observation_conflict"):
            pending = None
            stable = None
            continue
        if row.get("screen_boundary"):
            pending = None
            stable = None
            continue
        if stable is not None and time - stable["last_ms"] > maximum_gap_ms:
            stable = None
            pending = None

        selected_cards = _selected_cards(row)
        selected_witness_seen = bool(selected_cards)
        menu_cards = _cards(row)
        transition_cards = menu_cards
        # The remaining white cards of a collapsed transition frame must be
        # ordinary readable OCR (no visual fallback) before they can stand in
        # for the stable menu; the exact stable-menu identity check follows.
        # The repeated full menu itself is established by slot completeness
        # and repetition, not by this gate.
        if selected_cards and stable is not None:
            transition_cards = _cards(row, minimum_confidence=95)
        # A green selected card is a transition frame only when the white-card
        # set has collapsed relative to a stable full menu.  Some menu themes
        # use green as an ordinary option color, so a full menu plus a green
        # candidate is not by itself a commitment witness.
        selected_transition = bool(
            selected_cards and len(selected_cards) == 1
            and stable is not None
            and transition_cards
            and _remaining_cards_match(
                stable["options"], selected_cards[0], transition_cards
            )
        )
        if selected_transition:
            menu_cards = transition_cards
        transition_rejection_reason = None
        if (selected_cards and stable is not None and menu_cards
                and len(menu_cards) < len(stable["options"])
                and not selected_transition):
            if len(selected_cards) > 1:
                transition_rejection_reason = "multiple_selected_cards"
            elif _match_selected_card(stable["options"], selected_cards[0]) is None:
                transition_rejection_reason = "selected_card_not_unique_in_stable_menu"
            else:
                transition_rejection_reason = "remaining_cards_do_not_match_stable_menu"
        if selected_cards and not selected_transition:
            if transition_rejection_reason:
                _audit_menu(audit, stable, row, status="selection_unsupported",
                            reason=transition_rejection_reason)
            selected_cards = []
        # A shortened/colored selection animation must not replace the stable
        # full menu (or establish a new menu by itself).
        cards = [] if selected_transition else menu_cards
        if cards:
            pending, candidate = _advance_pending(pending, row, cards, maximum_gap_ms)
            if stable is None:
                stable = candidate
            elif candidate is not None and candidate["first_ms"] != stable["first_ms"]:
                # Only a menu repeated at two timestamps can replace the
                # current stable identity.  A shortened/colored selection
                # animation cannot replace the full menu either.
                if _near_menu(stable["options"], cards):
                    stable["last_ms"] = time
                elif len(candidate["options"]) == len(stable["options"]):
                    stable = candidate
            elif _near_menu(stable["options"], cards):
                stable["last_ms"] = time
            # A shortened/colored selection animation has no complete cards;
            # it intentionally leaves ``stable`` untouched for the witness.

        # If a complete, same-sized menu with materially different wording was
        # seen after the stable menu, a following mark cannot safely be
        # attributed to the old options.  Wait for the new menu to repeat (or
        # for a boundary) instead of guessing between the two identities.
        if (stable is not None and pending is not None
                and pending["first_ms"] > stable["first_ms"]
                and len(pending["options"]) == len(stable["options"])
                and not _near_menu(stable["options"], pending["options"])):
            stable = None

        if stable is None:
            # A selected mark/card without a repeated full menu is a real
            # witness candidate, but its option identity is unsupported.  It
            # is kept in the audit rather than silently labeled unobserved.
            # ``selected_witness_seen`` is captured before the non-transition
            # clearing above so an unbound green card is never dropped from
            # the audit without a trace.
            if (selected_witness_seen or _mark_pairs(row)
                    or _explicit_witness(row)[0] is not None):
                _audit_orphan_witness(
                    audit,
                    row,
                    reason="selection_witness_without_repeated_menu",
                )
            continue
        marks = _mark_pairs(row)
        explicit_text, explicit = _explicit_witness(row)
        if len(marks) != 1 and explicit_text is None and not selected_cards:
            continue
        if len(selected_cards) > 1:
            _audit_menu(audit, stable, row, status="selection_unsupported",
                        reason="multiple_selected_cards")
            continue
        if stable["first_ms"] in used_stable_first_ms:
            continue
        if explicit_text is not None:
            index = _match_explicit(stable["options"], explicit_text)
            if index is None:
                _audit_menu(audit, stable, row, status="selection_unsupported",
                            reason="explicit_selected_text_not_unique")
                continue
            selected_index = index
            selected_basis = explicit["basis"]
            witness_evidence = explicit["evidence"]
            selection_marks = marks[0] if len(marks) == 1 else None
            selected_card_proof = None
            if selected_cards:
                selected_card_index = _match_selected_card(stable["options"], selected_cards[0])
                if selected_card_index is None or selected_card_index != selected_index:
                    _audit_menu(audit, stable, row, status="selection_unsupported",
                                reason="explicit_and_selected_card_disagree")
                    continue
        else:
            selected_card_proof = None
            if selected_cards:
                selected_index = _match_selected_card(stable["options"], selected_cards[0])
                if selected_index is None:
                    _audit_menu(audit, stable, row, status="selection_unsupported",
                                reason="selected_card_not_unique_in_stable_menu")
                    continue
                # If both witnesses are visible, they must identify the same
                # slot.  A disagreement is an explicit conflict, never an
                # opportunity to choose whichever source is convenient.
                if len(marks) == 1:
                    marked_index = _match_mark(stable["options"], marks[0])
                    if marked_index is None or marked_index != selected_index:
                        _audit_menu(audit, stable, row, status="selection_unsupported",
                                    reason="selected_card_and_marks_disagree")
                        continue
                selected_basis = "selected_card_highlight_and_transition"
                selected_card_proof = {
                    "card": deepcopy(selected_cards[0]),
                    "evidence": _row_evidence(row),
                }
                witness_evidence = _row_evidence(row)
                selection_marks = None
            else:
                selected_index = _match_mark(stable["options"], marks[0])
                if selected_index is None:
                    _audit_menu(audit, stable, row, status="selection_unsupported",
                                reason="selection_marks_not_unique_in_stable_menu")
                    continue
                selected_basis = "bilateral_selection_marks"
                witness_evidence = [_row_evidence(row)[0]] if _row_evidence(row) else []
                # A visual witness without a source proof is not useful to the
                # report.  Keep the association conservative and abstain.
                if not witness_evidence:
                    _audit_menu(audit, stable, row, status="selection_unsupported",
                                reason="selection_witness_without_source_evidence")
                    continue
                selection_marks = marks[0]
        if not witness_evidence:
            _audit_menu(audit, stable, row, status="selection_unsupported",
                        reason="selection_witness_without_source_evidence")
            continue
        evidence = []
        for item in stable["rows"]:
            evidence.extend(_row_evidence(item))
        evidence.extend(witness_evidence)
        event = {
            "kind": "dialogue_choice" if len(stable["options"]) > 1 else "dialogue_response",
            "options": [item["text"] for item in stable["options"]],
            "selected_index": selected_index,
            "selected_text": stable["options"][selected_index]["text"],
            "first_seen_ms": stable["first_ms"],
            "selection_observed_ms": time,
            "click_timestamp_ms": None,
            "evidence": list(dict.fromkeys(evidence)),
            "selection_marks": selection_marks,
            "selection_basis": selected_basis,
            "selection_state": "committed",
            # Keep the commitment boundary explicit for downstream action
            # projection.  A reconstructed event has a visual selection
            # witness; callers must not have to infer its phase from the
            # selected text or from a later effect.
            "phase": "committed",
            "complete_effects_verified": False,
        }
        if selected_card_proof is not None:
            event["selected_card_proof"] = selected_card_proof
        events.append(event)
        _audit_menu(audit, stable, row, status="committed")
        if isinstance(audit, dict):
            audit["committed_count"] = int(audit.get("committed_count", 0)) + 1
        used_stable_first_ms.add(stable["first_ms"])
        pending = None
        stable = None

    if stable is not None:
        _audit_menu(audit, stable, status="selection_unobserved",
                    reason="stable_menu_without_selection_witness")
    if isinstance(audit, dict):
        statuses = [item.get("status") for item in audit.get("menus", [])
                    if isinstance(item, Mapping)]
        audit["unobserved_menu_count"] = statuses.count("selection_unobserved")
        audit["unsupported_selection_count"] = (
            statuses.count("selection_unsupported")
            + len(audit.get("orphan_witnesses", []))
        )
    return events


# The shorter name is useful at call sites that already import other choice
# reconstruction helpers.  Keep the explicit alias for integration code whose
# name should make the commitment boundary obvious.
reconstruct = reconstruct_committed_choices


__all__ = [
    "SCHEMA",
    "DEFAULT_MAX_MENU_GAP_MS",
    "reconstruct",
    "reconstruct_committed_choices",
]
