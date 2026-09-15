"""Read source-bound lesson offers from one gameplay frame.

The Lessons screen is a menu of offers.  Its cards describe a possible
technique, the effects shown by that card, and the performance-point prices
shown in its cost row.  None of those pixels establish that a lesson was
selected or purchased; a later confirmation/receipt is required for that.

This adapter deliberately sits beside the ordinary vision and transaction
paths.  It consumes the immutable neural line geometry that is already stored
for a frame and, when paths are supplied, verifies the gameplay and source
frame hashes before returning the result.  It does not consult a lesson
catalogue, a predicted value, a balance, a run identifier, or a timestamp.
Unknown OCR slots stay unknown.  The card is the identity boundary, so two
effects on one card remain attached to one offer rather than becoming two
independent purchases.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping


SCHEMA = "tracen-replay/lesson-offer-source-adapter-v1"
COST_REFINEMENT_SCHEMA = "tracen-replay/lesson-offer-cost-refinement-v1"
GAMEPLAY_SIZE = (810, 1080)
PANE_LEFT = 148
COST_FIELDS = ("dance", "passion", "vocal", "visual", "composure")
MIN_TITLE_CONFIDENCE = 90.0
MIN_EFFECT_CONFIDENCE = 85.0
MIN_COST_CONFIDENCE = 60.0

# These are full-gameplay coordinates from the neural sidecar.  The PNG under
# ``gameplay/`` is the 810-pixel-wide pane and therefore uses x - PANE_LEFT
# when an image crop is needed by another consumer.
_COST_COLUMNS = ((455, 515), (530, 600), (610, 680), (690, 760), (765, 835))
_PROMPT = "select a technique or song to learn."
_COST_LABEL = "performance point cost"
_TITLE_EXCLUSIONS = frozenset({
    "learnable!", "learnable", "technique", "none", "performance point cost",
})

_EFFECT_FIELDS = {
    "speed": "speed",
    "stamina": "stamina",
    "power": "power",
    "guts": "guts",
    "wit": "wit",
    "skill points": "skill_points",
    # The game uses the abbreviated label in menu cards and confirmation
    # panels.  Keep it in the parser-owned vocabulary so the card identity
    # boundary does not discard a readable Skill Pts row as an unknown
    # adjacent effect.
    "skill pts": "skill_points",
    "energy": "energy",
    "dance": "dance",
    "passion": "passion",
    "vocal": "vocal",
    "visual": "visual",
    "composure": "composure",
}
_EFFECT_LABELS = "|".join(re.escape(label) for label in _EFFECT_FIELDS)
_STAT_EFFECT_RE = re.compile(
    rf"^(?P<label>{_EFFECT_LABELS})\s*\+\s*(?P<amount>\d{{1,3}})$",
    re.IGNORECASE,
)
_HINT_EFFECT_RE = re.compile(
    r"^skill\s+hint\s+lvl\s*\+\s*(?P<amount>\d{1,2})\s*\(\s*(?P<level>[^()]+?)\s*\)$",
    re.IGNORECASE,
)
_INTEGER_RE = re.compile(r"^\d{1,3}$")


class LessonOfferSourceError(ValueError):
    """Raised when the requested source proof is stale or malformed."""


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not 0 <= number <= 100:
        return None
    return number


def _box(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    numbers: list[int] = []
    for part in value:
        if isinstance(part, bool) or not isinstance(part, (int, float)):
            return None
        try:
            numeric = float(part)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(numeric) or numeric != int(numeric):
            return None
        numbers.append(int(numeric))
    left, top, right, bottom = numbers
    if not (0 <= left < right <= 1100 and 0 <= top < bottom <= GAMEPLAY_SIZE[1]):
        return None
    return numbers


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LessonOfferSourceError("Lesson offer source proof is not JSON serializable.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _legacy_digest(value: Any) -> str:
    """Hash the refinement sidecar's historical JSON representation.

    The lesson crop refiner predates this adapter and uses ``json.dumps`` with
    its default separators.  The adapter uses compact separators for its own
    source proof.  Keep both representations explicit so a merge can bind the
    sidecar to the same raw row without weakening either hash check.
    """

    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LessonOfferSourceError("Lesson offer source proof is not JSON serializable.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise LessonOfferSourceError(f"Lesson offer source evidence is unavailable: {path}") from exc
    return digest.hexdigest()


def _gameplay_sha256(path: str | Path) -> tuple[str, str]:
    """Return decoded RGB and encoded-file hashes for a gameplay crop."""

    try:
        from .frame_cache import open_rgb, rgb_sha256

        if open_rgb(path).size != GAMEPLAY_SIZE:
            raise LessonOfferSourceError("Lesson offer gameplay evidence is not an 810x1080 pane.")
        pixels = rgb_sha256(path)
    except LessonOfferSourceError:
        raise
    except (OSError, ValueError) as exc:
        raise LessonOfferSourceError(f"Lesson offer gameplay evidence is unreadable: {path}") from exc
    return pixels, _file_sha256(path)


def _line_record(line: Any, index: int) -> dict[str, Any] | None:
    if not isinstance(line, Mapping):
        return None
    text = _text(line.get("text"))
    box = _box(line.get("box"))
    confidence = _confidence(line.get("confidence"))
    if not text or box is None or confidence is None:
        return None
    # Keep only immutable source geometry used by this parser.  Arbitrary
    # fields from a mutable worker object must not become semantic evidence.
    return dict(index=index, text=text, confidence=confidence, box=box)


def _lines(raw: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = raw.get("lines")
    if not isinstance(values, list):
        nested = raw.get("ocr")
        values = nested.get("neural") if isinstance(nested, Mapping) else None
    if not isinstance(values, list):
        return []
    return [record for index, line in enumerate(values)
            if (record := _line_record(line, index)) is not None]


def _center(box: list[int]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _line_copy(line: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return deepcopy(dict(line)) if isinstance(line, Mapping) else None


def _screen_is_lesson_selection(raw: Mapping[str, Any], lines: list[dict[str, Any]]) -> bool:
    header = _text(raw.get("header")).casefold()
    explicit_screen = _text(raw.get("screen"))
    if explicit_screen and explicit_screen != "lesson_selection":
        return False
    if not explicit_screen and header != "lessons":
        return False
    if raw.get("current_grid") is True or raw.get("result_grid") is True:
        return False
    # A source row that already says it is applied/committed cannot be
    # downgraded to a preview just because its line text resembles a menu.
    for key in ("committed", "applied", "awarded", "purchase_committed"):
        if raw.get(key) is True:
            return False
    phase = _text(raw.get("phase"))
    if phase and phase.casefold() != "preview":
        return False
    normalized = [line["text"].casefold() for line in lines]
    if _PROMPT not in normalized:
        return False
    if any("technique learned" in text or "lesson learned" in text for text in normalized):
        return False
    return True


def _title_groups(lines: list[dict[str, Any]], label_box: list[int]) -> list[list[dict[str, Any]]]:
    """Find title line groups above one cost label using source geometry."""

    _, label_center_y = _center(label_box)
    candidates: list[dict[str, Any]] = []
    for line in lines:
        box = line["box"]
        center_x, center_y = _center(box)
        text = line["text"]
        folded = text.casefold()
        if not (260 <= box[0] <= 700 and center_x <= 760):
            continue
        if not label_center_y - 225 <= center_y <= label_center_y - 85:
            continue
        if line["confidence"] < MIN_TITLE_CONFIDENCE:
            continue
        if folded in _TITLE_EXCLUSIONS or folded == _PROMPT or "+" in text:
            continue
        if not any(character.isalpha() for character in text):
            continue
        candidates.append(line)
    candidates.sort(key=lambda line: (line["box"][1], line["box"][0]))
    groups: list[list[dict[str, Any]]] = []
    for line in candidates:
        if not groups:
            groups.append([line])
            continue
        previous = groups[-1][-1]
        current_box, previous_box = line["box"], previous["box"]
        current_center_y, previous_center_y = _center(current_box)[1], _center(previous_box)[1]
        horizontal_gap = current_box[0] - previous_box[2]
        same_baseline = abs(current_center_y - previous_center_y) <= 14
        same_column = abs(current_box[0] - groups[-1][0]["box"][0]) <= 28
        if (same_baseline and -24 <= horizontal_gap <= 42) or (
            0 <= current_box[1] - previous_box[3] <= 18 and same_column
        ):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _title_record(group: list[dict[str, Any]]) -> dict[str, Any]:
    box = [
        min(line["box"][0] for line in group),
        min(line["box"][1] for line in group),
        max(line["box"][2] for line in group),
        max(line["box"][3] for line in group),
    ]
    return dict(
        text=" ".join(line["text"] for line in group),
        confidence=min(line["confidence"] for line in group),
        box=box,
        line_indices=[line["index"] for line in group],
    )


def _effect(line: dict[str, Any]) -> dict[str, Any] | None:
    text = line["text"]
    hint = _HINT_EFFECT_RE.fullmatch(text)
    if hint:
        level = _text(hint.group("level"))
        if not level:
            return None
        return dict(
            kind="skill_hint_change",
            category="skill_hint",
            amount=int(hint.group("amount")),
            level=level.casefold(),
            name_visible=False,
            raw_label=text,
            source_line=_line_copy(line),
        )
    match = _STAT_EFFECT_RE.fullmatch(text)
    if not match:
        return None
    field = _EFFECT_FIELDS[match.group("label").casefold()]
    return dict(
        kind="energy_change" if field == "energy" else
        "performance_change" if field in {"dance", "passion", "vocal", "visual", "composure"}
        else "stat_change",
        field=field,
        amount=int(match.group("amount")),
        source_line=_line_copy(line),
    )


def _effects(lines: list[dict[str, Any]], title: dict[str, Any], label_box: list[int]) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    title_bottom = title["box"][3]
    label_center_y = _center(label_box)[1]
    candidates = []
    for line in lines:
        box = line["box"]
        center_x, center_y = _center(box)
        if not (430 <= box[0] <= 760 and center_x <= 780):
            continue
        if not title_bottom + 12 <= center_y <= label_center_y - 65:
            continue
        candidates.append(line)
    candidates.sort(key=lambda line: (line["box"][1], line["box"][0]))
    effects: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    reasons: list[str] = []
    for line in candidates:
        # ``None`` is the card's second, empty effect row and is not a value.
        if line["text"].casefold() == "none":
            continue
        parsed = _effect(line) if line["confidence"] >= MIN_EFFECT_CONFIDENCE else None
        if parsed is None:
            if "+" in line["text"] or "hint" in line["text"].casefold():
                unknown.append(_line_copy(line) or {})
                reasons.append("unreadable_effect")
            continue
        effects.append(parsed)
    identities = [(effect["kind"], effect.get("field"), effect.get("category")) for effect in effects]
    if len(set(identities)) != len(identities):
        reasons.append("duplicate_effect_field")
        duplicated = {identity for identity in identities if identities.count(identity) > 1}
        retained = []
        for effect, identity in zip(effects, identities):
            if identity in duplicated:
                unknown.append(dict(effect["source_line"]))
            else:
                retained.append(effect)
        effects = retained
    return effects, list(dict.fromkeys(reasons)), unknown


def _source_title_identity(title: Mapping[str, Any], pane: Any) -> tuple[str, dict[str, Any] | None]:
    """Return a title enriched only by a source-visible music-note glyph.

    The neural title stays intact in ``title`` for sidecar identity checks.
    ``name`` is the display/matching form and may gain ``♪`` only when the
    shared source-pixel detector proves the glyph at this title's right edge.
    A one-letter suffix remains unresolved because it may be real song text;
    the independent title witness in ``song_symbol_refinement`` owns that
    decision for committed receipts.
    """
    text = _text(title.get("text"))
    confidence = _confidence(title.get("confidence"))
    if not text or confidence is None or confidence < MIN_TITLE_CONFIDENCE or pane is None:
        return text, None
    from .song_symbols import music_note_title_suffix
    symbol = music_note_title_suffix(pane, title.get("box"))
    if symbol is None or not isinstance(symbol.get("symbol"), str):
        return text, None
    # ``>``/``▶``/``→`` at the title edge are UI-arrow OCR artifacts.  Strip
    # one only after the source pixel has independently proved the note.
    base = re.sub(r"\s*[>▶→]\s*$", "", text).rstrip()
    marker = symbol["symbol"]
    if marker in base:
        return base, symbol
    if not base or re.search(r"\s+[A-Za-z]$", base):
        return text, None
    return f"{base} {marker}".strip(), symbol


def _cost_candidates(lines: list[dict[str, Any]], label_box: list[int]) -> list[list[dict[str, Any]]]:
    label_top, label_bottom = label_box[1], label_box[3]
    result: list[list[dict[str, Any]]] = [[] for _ in COST_FIELDS]
    for line in lines:
        text = line["text"]
        if not _INTEGER_RE.fullmatch(text):
            continue
        center_x, center_y = _center(line["box"])
        if not label_top - 8 <= center_y <= label_bottom + 10:
            continue
        for index, (left, right) in enumerate(_COST_COLUMNS):
            if left <= center_x <= right:
                result[index].append(line)
                break
    return result


def _costs(lines: list[dict[str, Any]], label_box: list[int]) -> tuple[list[dict[str, Any]], dict[str, int], list[str]]:
    prices: list[dict[str, Any]] = []
    known: dict[str, int] = {}
    reasons: list[str] = []
    for field, candidates in zip(COST_FIELDS, _cost_candidates(lines, label_box)):
        values = {int(line["text"]) for line in candidates}
        if len(values) > 1:
            prices.append(dict(field=field, value=None, amount=None, status="unknown",
                               unknown_reason="ambiguous_numeric_cost", confidence=None,
                               source_line=None))
            reasons.append(f"{field}_ambiguous_numeric_cost")
            continue
        if not candidates:
            prices.append(dict(field=field, value=None, amount=None, status="unknown",
                               unknown_reason="missing_numeric_cost", confidence=None,
                               source_line=None))
            reasons.append(f"{field}_missing_numeric_cost")
            continue
        line = candidates[0]
        confidence = line["confidence"]
        if confidence < MIN_COST_CONFIDENCE:
            prices.append(dict(field=field, value=None, amount=None, status="unknown",
                               unknown_reason="low_confidence_numeric_cost", confidence=confidence,
                               source_line=_line_copy(line)))
            reasons.append(f"{field}_low_confidence_numeric_cost")
            continue
        amount = int(line["text"])
        prices.append(dict(field=field, value=amount, amount=amount, status="accepted",
                           unknown_reason=None, confidence=confidence,
                           source_line=_line_copy(line)))
        known[field] = amount
    return prices, known, list(dict.fromkeys(reasons))


def _card(
    card_index: int,
    title: dict[str, Any],
    cost_label: dict[str, Any],
    effects: list[dict[str, Any]],
    unknown_effects: list[dict[str, Any]],
    prices: list[dict[str, Any]],
    known_cost: dict[str, int],
    reasons: list[str],
    *,
    display_name: str | None = None,
    source_symbol: dict[str, Any] | None = None,
) -> dict[str, Any]:
    name = title["text"] if display_name is None else display_name
    geometry = dict(
        card_index=card_index,
        title=title,
        cost_label=cost_label,
        effects=[effect.get("source_line") for effect in effects],
        unknown_effects=unknown_effects,
        prices=[dict(field=price["field"], source_line=price.get("source_line")) for price in prices],
    )
    geometry_sha256 = _digest(geometry)
    offer_id = "lesson-offer:" + _digest({
        "title": name,
        "title_box": title["box"],
        "cost_label_box": cost_label["box"],
    })[:24]
    normalized_effects = []
    for effect in effects:
        value = {key: deepcopy(item) for key, item in effect.items() if key != "source_line"}
        normalized_effects.append(value)
    payload = dict(kind="lesson", name=name)
    if normalized_effects:
        payload["effects"] = deepcopy(normalized_effects)
    # Omitting an unreadable slot is intentional.  A missing value is not a
    # zero and is never recovered from the performance-point state panel.
    if known_cost:
        payload["cost"] = dict(known_cost)
    complete = not reasons and len(prices) == len(COST_FIELDS) and all(
        price["value"] is not None for price in prices
    )
    result = dict(
        offer_id=offer_id,
        card_index=card_index,
        name=name,
        title=deepcopy(title),
        effects=normalized_effects,
        prices=deepcopy(prices),
        cost=deepcopy(known_cost),
        # ``status`` keeps the shape consumed by the existing lesson-cost
        # joiner.  ``observation_status`` carries the source-layer meaning;
        # neither value means that a purchase happened.
        status="complete" if complete else "unknown",
        observation_status="observed",
        completeness="complete" if complete else "partial",
        unknown_reasons=list(dict.fromkeys(reasons)),
        payload=payload,
        phase="preview",
        preview_only=True,
        committed=False,
        receipt_required=True,
        geometry_sha256=geometry_sha256,
        source_geometry=geometry,
    )
    if source_symbol is not None:
        result["source_symbol"] = deepcopy(source_symbol)
    return result


def _source_proof(
    raw: Mapping[str, Any],
    lines: list[dict[str, Any]],
    cards: list[dict[str, Any]],
    *,
    gameplay_path: str | Path | None,
    source_frame_path: str | Path | None,
    expected_evidence_sha256: str | None,
    source_sha256: str | None,
) -> dict[str, Any]:
    raw_source = _text(raw.get("source_sha256")).casefold()
    supplied_source = _text(source_sha256).casefold()
    for value in (raw_source, supplied_source):
        if value and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise LessonOfferSourceError("Lesson offer source hash is not SHA-256.")
    if raw_source and supplied_source and raw_source != supplied_source:
        raise LessonOfferSourceError("Lesson offer source hash changed.")
    source_hash = supplied_source or raw_source or None
    raw_gameplay = _text(raw.get("gameplay_sha256")).casefold()
    raw_source_frame = _text(raw.get("source_frame_sha256")).casefold()
    if raw_gameplay and not re.fullmatch(r"[0-9a-f]{64}", raw_gameplay):
        raise LessonOfferSourceError("Lesson offer gameplay hash is not SHA-256.")
    if raw_source_frame and not re.fullmatch(r"[0-9a-f]{64}", raw_source_frame):
        raise LessonOfferSourceError("Lesson offer source-frame hash is not SHA-256.")

    evidence_sha = None
    gameplay_sha = raw_gameplay or None
    if gameplay_path is not None:
        gameplay_sha, evidence_sha = _gameplay_sha256(gameplay_path)
        if raw_gameplay and gameplay_sha != raw_gameplay:
            raise LessonOfferSourceError("Lesson offer gameplay pixels do not match the source row.")
    if expected_evidence_sha256 is not None:
        expected = _text(expected_evidence_sha256).casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise LessonOfferSourceError("Lesson offer evidence hash is not SHA-256.")
        if gameplay_path is None:
            raise LessonOfferSourceError("Lesson offer evidence path is required for evidence verification.")
        if evidence_sha != expected:
            raise LessonOfferSourceError("Lesson offer gameplay evidence bytes changed.")
        evidence_sha = expected

    source_frame_verified = False
    if source_frame_path is not None:
        source_frame_sha = _file_sha256(source_frame_path)
        if raw_source_frame and source_frame_sha != raw_source_frame:
            raise LessonOfferSourceError("Lesson offer source frame changed.")
        if not raw_source_frame:
            raw_source_frame = source_frame_sha
        source_frame_verified = True

    selected_geometry = [card["source_geometry"] for card in cards]
    return dict(
        source_sha256=source_hash,
        evidence=_text(raw.get("evidence")) or None,
        evidence_sha256=evidence_sha,
        gameplay_sha256=gameplay_sha,
        source_frame_sha256=raw_source_frame or None,
        source_frame_verified=source_frame_verified,
        raw_sha256=_digest(dict(raw)),
        # ``lesson_offer_refinement`` uses the non-compact JSON encoding.  It
        # is retained as an explicitly named alternate identity for the
        # source-bound cost sidecar; it is not an independent source claim.
        raw_fingerprint_sha256=_legacy_digest(dict(raw)),
        geometry_sha256=_digest(selected_geometry),
        coordinate_space="full_gameplay",
        gameplay_size=list(GAMEPLAY_SIZE),
        parser_input="immutable_neural_lines",
        line_count=len(lines),
    )


def adapt_lesson_offer_frame(
    raw: Mapping[str, Any],
    *,
    gameplay_path: str | Path | None = None,
    source_frame_path: str | Path | None = None,
    expected_evidence_sha256: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Return source-bound preview offers from one neural source row.

    ``raw`` is the immutable neural JSON object for one frame.  ``gameplay_path``
    is optional for callers that have already verified the crop; when supplied
    its decoded RGB hash must agree with ``raw['gameplay_sha256']``.  Supplying
    ``source_frame_path`` additionally verifies the original captured frame
    bytes against ``raw['source_frame_sha256']``.
    ``source_sha256`` can bind the row to the immutable source video hash from
    ``capture.json``; when the raw row already carries that hash, both values
    must agree.

    The result is safe to attach as a preview fact.  In particular, it has no
    action receipt, no applied effect, and no balance-derived value.  Callers
    should pass a later receipt to their transaction layer before recording a
    purchase.
    """

    if not isinstance(raw, Mapping):
        raise LessonOfferSourceError("Lesson offer source row must be an object.")
    lines = _lines(raw)
    if not _screen_is_lesson_selection(raw, lines):
        return dict(schema_version=SCHEMA, screen=None, phase="preview", offers=[], observations=[],
                    preview_only=True, committed=False, receipt_required=True,
                    rejected=["not_lesson_selection"])

    # Card identity must be selected once for both the rich preview adapter
    # and the numeric crop refiner.  Their former independent title scans
    # disagreed on benign text below a card title (for example a song panel
    # label or an effect row), so the refiner emitted every cost label while
    # this adapter discarded one or more cards.  That shifted/expanded card
    # identity at merge time and surfaced as an ``unknown card`` failure.
    # Keep the refiner's source geometry as the canonical selection and use
    # its original label index as the stable card index.  Invalid/ambiguous
    # candidates remain rejected in both paths; no name or amount is used to
    # join cards.
    from .lesson_offer_refinement import (
        find_offer_cards,
        is_mergeable_offer_candidate,
    )

    selected = find_offer_cards(dict(raw))
    lines_by_index = {line["index"]: line for line in lines}
    source_pane = None
    if gameplay_path is not None and selected:
        # Validate the decoded pane hash before allowing it to influence the
        # source title.  ``_source_proof`` repeats the file-level checks below
        # and retains the same evidence envelope for every card.
        try:
            from .frame_cache import open_rgb, rgb_sha256
            source_pane = open_rgb(gameplay_path)
            if source_pane.size != GAMEPLAY_SIZE:
                raise LessonOfferSourceError("Lesson offer gameplay evidence is not an 810x1080 pane.")
            actual_gameplay = rgb_sha256(gameplay_path)
        except LessonOfferSourceError:
            raise
        except (OSError, ValueError) as exc:
            raise LessonOfferSourceError(
                f"Lesson offer gameplay evidence is unreadable: {gameplay_path}"
            ) from exc
        expected_gameplay = _text(raw.get("gameplay_sha256")).casefold()
        if expected_gameplay and actual_gameplay != expected_gameplay:
            raise LessonOfferSourceError("Lesson offer gameplay pixels do not match the source row.")
    cards: list[dict[str, Any]] = []
    rejected: list[str] = []
    for candidate in selected:
        card_index = candidate["card_index"]
        candidate_reasons = list(candidate.get("unknown_reasons", []))
        if not is_mergeable_offer_candidate(candidate):
            if "ambiguous_cost_label" in candidate_reasons:
                rejected.append(f"card_{card_index}_ambiguous_cost_label")
            else:
                rejected.append(f"card_{card_index}_ambiguous_or_missing_title")
            continue
        label = lines_by_index.get(candidate.get("cost_label_line_index"))
        title = candidate.get("title")
        if label is None or not isinstance(title, Mapping):
            rejected.append(f"card_{card_index}_ambiguous_or_missing_title")
            continue
        cost_label = deepcopy(dict(candidate["cost_label"]))
        assert cost_label is not None
        effects, effect_reasons, unknown_effects = _effects(lines, title, label["box"])
        prices, known_cost, cost_reasons = _costs(lines, label["box"])
        display_name, source_symbol = _source_title_identity(title, source_pane)
        card = _card(card_index, title, cost_label, effects, unknown_effects,
                     prices, known_cost,
                     candidate_reasons + effect_reasons + cost_reasons,
                     display_name=display_name, source_symbol=source_symbol)
        cards.append(card)

    proof = _source_proof(
        raw,
        lines,
        cards,
        gameplay_path=gameplay_path,
        source_frame_path=source_frame_path,
        expected_evidence_sha256=expected_evidence_sha256,
        source_sha256=source_sha256,
    )
    offers: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for card in cards:
        card_proof = deepcopy(proof)
        card_proof["geometry_sha256"] = card["geometry_sha256"]
        card["source_proof"] = card_proof
        # Preserve the card identity in the payload and observation.  Effects
        # are still one grouped offer; consumers must not split them into
        # separate purchases.
        offer = deepcopy(card)
        offers.append(offer)
        observations.append(dict(
            id=card["offer_id"],
            category="purchase",
            phase="preview",
            status="observed",
            offer_id=card["offer_id"],
            payload=deepcopy(card["payload"]),
            evidence=[proof["evidence"]] if proof.get("evidence") else [],
            source_proof=card_proof,
            preview_only=True,
            committed=False,
            receipt_required=True,
        ))

    return dict(
        schema_version=SCHEMA,
        screen="lesson_selection",
        phase="preview",
        offers=offers,
        observations=observations,
        preview_only=True,
        committed=False,
        receipt_required=True,
        rejected=list(dict.fromkeys(rejected)),
        source_proof=proof,
    )


_COST_UNKNOWN_REASONS = frozenset({
    "missing_numeric_cost",
    "low_confidence_numeric_cost",
    "ambiguous_numeric_cost",
    "missing_digit",
    "low_confidence",
    "ambiguous_digit",
    "clipped_or_invalid_cost_row",
})


def missing_cost_fields(result: Mapping[str, Any]) -> dict[int, tuple[str, ...]]:
    """Return source-card slots that still need a bounded crop reread.

    The result is derived only from the adapter's own per-slot observations.
    A missing slot is never converted to zero and a complete card is never
    scheduled for another reader pass.
    """

    if not isinstance(result, Mapping):
        return {}
    missing: dict[int, tuple[str, ...]] = {}
    for offer in result.get("offers", []):
        if not isinstance(offer, Mapping) or type(offer.get("card_index")) is not int:
            continue
        fields: list[str] = []
        prices = offer.get("prices")
        if not isinstance(prices, list):
            continue
        for field, price in zip(COST_FIELDS, prices):
            if not isinstance(price, Mapping):
                fields.append(field)
                continue
            if price.get("value") is None or price.get("status") != "accepted":
                fields.append(field)
        if fields:
            missing[offer["card_index"]] = tuple(fields)
    return missing


def _valid_refined_price(price: Any, field: str) -> bool:
    """Require crop/provenance proof before accepting a recovered price."""

    if not isinstance(price, Mapping) or price.get("field") != field:
        return False
    value = price.get("value")
    if type(value) is not int or not 0 <= value <= 999 or price.get("status") != "accepted":
        return False
    if ("amount" in price and price.get("amount") != value) or price.get("unknown_reason") is not None:
        return False
    text = price.get("text")
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]{1,3}", text) or int(text) != value:
        return False
    confidence = _confidence(price.get("confidence"))
    if confidence is None or confidence < 97:
        return False
    if price.get("coordinate_space") != "gameplay_pane":
        return False
    preprocess = price.get("preprocess")
    if preprocess is not None:
        if (preprocess not in {"gray_autocontrast", "gray_autocontrast_3x"}
                or not isinstance(price.get("recognizer_rgb_sha256"), str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", price["recognizer_rgb_sha256"])):
            return False
    if "source_readings" in price and not _valid_source_readings(price.get("source_readings")):
        return False
    for key in ("source_box", "crop_box"):
        if _box(price.get(key)) is None:
            return False
    for key in ("crop_rgb_sha256", "box_sha256", "geometry_sha256"):
        value = price.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return False
    return True


def _valid_source_readings(readings: Any) -> bool:
    """Check the structural part of retained raw/variant OCR diagnostics.

    Pixel-level validation is performed by ``lesson_offer_refinement.observe``
    when a pane is available.  The merge boundary still rejects a swapped or
    fabricated diagnostic list when called directly, so it cannot promote an
    arbitrary amount merely because the outer price looks accepted.
    """

    if not isinstance(readings, list) or not readings:
        return False
    variants: set[str] = set()
    for reading in readings:
        if not isinstance(reading, Mapping):
            return False
        variant = reading.get("variant")
        if variant not in {"raw", "gray_autocontrast", "gray_autocontrast_3x"}:
            return False
        if variant in variants:
            return False
        variants.add(variant)
        text = reading.get("text")
        if text is not None and (not isinstance(text, str) or text != text.strip()):
            return False
        confidence = reading.get("confidence")
        if confidence is not None:
            try:
                confidence_value = float(confidence)
            except (TypeError, ValueError, OverflowError):
                return False
            if not math.isfinite(confidence_value) or not 0 <= confidence_value <= 100:
                return False
        value = reading.get("value")
        if value is not None and (type(value) is not int or not 0 <= value <= 999):
            return False
        status = reading.get("status")
        if status != ("accepted" if value is not None else "unknown"):
            return False
        reason = reading.get("unknown_reason")
        if value is None and (not isinstance(reason, str) or not reason):
            return False
        if value is not None and reason is not None:
            return False
        recognizer_hash = reading.get("recognizer_rgb_sha256")
        if not isinstance(recognizer_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", recognizer_hash):
            return False
    return "raw" in variants


def _expected_refinement_boxes(label_box: Any) -> tuple[list[list[int] | None], list[list[int] | None]]:
    """Recreate the refiner's fixed five-slot boxes from the current label."""

    try:
        from .lesson_offer_refinement import _price_boxes

        geometry = _price_boxes(label_box)
    except (ImportError, TypeError, ValueError):
        geometry = None
    if geometry is None:
        empty = [None] * len(COST_FIELDS)
        return empty, list(empty)
    source_boxes, crop_boxes = geometry
    return list(source_boxes), list(crop_boxes)


def _refinement_card_geometry_is_valid(card: Mapping[str, Any]) -> bool:
    try:
        from .lesson_offer_refinement import _candidate_signature, fingerprint

        return card.get("geometry_sha256") == fingerprint(_candidate_signature(dict(card)))
    except (ImportError, TypeError, ValueError):
        return False


def _refinement_proof(result: Mapping[str, Any], provenance: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a refinement's immutable source identity against the offer."""

    if provenance.get("independent_observations") is not False:
        raise LessonOfferSourceError("Lesson offer cost refinement is not source-bound.")
    source_proof = result.get("source_proof")
    if not isinstance(source_proof, Mapping):
        raise LessonOfferSourceError("Lesson offer source proof is missing.")
    if provenance.get("version") not in (None, 1):
        raise LessonOfferSourceError("Lesson offer cost refinement version changed.")
    if provenance.get("policy") not in (None, "source_bound_lesson_offer_prices_v1"):
        raise LessonOfferSourceError("Lesson offer cost refinement policy changed.")
    refinement_cards = provenance.get("cards_sha256")
    if not isinstance(refinement_cards, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", refinement_cards):
        raise LessonOfferSourceError("Lesson offer cost refinement card proof is missing.")
    for key in ("raw_sha256", "evidence_sha256", "gameplay_sha256", "source_frame_sha256"):
        expected = source_proof.get(key)
        actual = provenance.get(key)
        if key == "raw_sha256" and actual is not None:
            allowed = {expected, source_proof.get("raw_fingerprint_sha256")}
            if actual not in allowed:
                raise LessonOfferSourceError(f"Lesson offer cost refinement {key} changed.")
        elif (expected is not None or actual is not None) and expected != actual:
            raise LessonOfferSourceError(f"Lesson offer cost refinement {key} changed.")
    proof = {
        "schema_version": COST_REFINEMENT_SCHEMA,
        "policy": provenance.get("policy", "source_bound_lesson_offer_prices_v1"),
        "version": provenance.get("version", 1),
        "raw_sha256": provenance.get("raw_sha256"),
        "evidence_sha256": provenance.get("evidence_sha256"),
        "gameplay_sha256": provenance.get("gameplay_sha256"),
        "source_frame_sha256": provenance.get("source_frame_sha256"),
        "cards_sha256": provenance.get("cards_sha256"),
        "independent_observations": False,
    }
    return {key: value for key, value in proof.items() if value is not None}


def _cost_reason_field(reason: Any) -> str | None:
    if not isinstance(reason, str):
        return None
    for field in COST_FIELDS:
        if reason.startswith(field + "_") and reason[len(field) + 1:] in _COST_UNKNOWN_REASONS:
            return field
    return None


def _price_alternative(base: Mapping[str, Any], refined: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "base": deepcopy(dict(base)),
        "refined": deepcopy(dict(refined)),
    }


def merge_lesson_offer_cost_refinement(
    result: Mapping[str, Any],
    refined_cards: Any,
    *,
    provenance: Mapping[str, Any] | None = None,
    raw: Mapping[str, Any] | None = None,
    gameplay_path: str | Path | None = None,
    source_frame_path: str | Path | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Merge source-crop prices into one offer envelope without inference.

    ``refined_cards`` is the card list emitted by
    :mod:`lesson_offer_refinement`.  Card identity and crop provenance must
    agree with the base adapter result.  An accepted crop value can fill a
    missing base slot; conflicting accepted values become an explicit unknown
    with both readings retained.  No balance, expected value, or neighboring
    card is consulted.
    """

    if not isinstance(result, Mapping):
        raise LessonOfferSourceError("Lesson offer result must be an object.")
    if not isinstance(refined_cards, list):
        raise LessonOfferSourceError("Lesson offer cost refinement cards are not a list.")
    if not isinstance(raw, Mapping) or gameplay_path is None:
        raise LessonOfferSourceError(
            "Lesson offer merge requires validated source row and gameplay evidence."
        )
    if not isinstance(provenance, Mapping):
        raise LessonOfferSourceError("Lesson offer merge requires refinement provenance.")
    if source_sha256 is not None and raw.get("source_sha256") not in (None, source_sha256):
        raise LessonOfferSourceError("Lesson offer source hash changed.")
    try:
        from .frame_cache import open_rgb
        from .lesson_offer_refinement import observe as observe_refinement

        observe_refinement(
            open_rgb(gameplay_path),
            dict(raw),
            dict(provenance),
            source_frame_path=source_frame_path,
        )
    except (OSError, TypeError, ValueError, KeyError) as exc:
        raise LessonOfferSourceError(
            f"Lesson offer merge source validation failed: {exc}"
        ) from exc
    proof = _refinement_proof(result, provenance)
    # ``find_offer_cards`` intentionally retains source labels whose title is
    # ambiguous so the numeric sidecar can preserve that uncertainty.  Such a
    # card is not present in the rich adapter result, however, and must not be
    # mistaken for an unknown card injected by a caller.  The source candidate
    # and the rich result are both already bound by ``observe``; use the same
    # mergeability predicate as the adapter to discard only those explicitly
    # rejected source candidates.  Any other extra index remains an error.
    from .lesson_offer_refinement import (
        find_offer_cards,
        is_mergeable_offer_candidate,
    )
    source_candidates = {
        candidate["card_index"]: candidate
        for candidate in find_offer_cards(dict(raw))
    }
    result_card_indices = {
        offer.get("card_index")
        for offer in result.get("offers", [])
        if isinstance(offer, Mapping) and type(offer.get("card_index")) is int
    }
    by_card: dict[int, Mapping[str, Any]] = {}
    for card in refined_cards:
        if not isinstance(card, Mapping) or type(card.get("card_index")) is not int:
            raise LessonOfferSourceError("Lesson offer cost refinement card identity is invalid.")
        if card["card_index"] in by_card:
            raise LessonOfferSourceError("Lesson offer cost refinement has duplicate cards.")
        if card["card_index"] not in result_card_indices:
            candidate = source_candidates.get(card["card_index"])
            if candidate is not None and not is_mergeable_offer_candidate(candidate):
                # This is a source-visible but intentionally rejected card
                # (for example a song-panel line made the title ambiguous).
                # It was validated above and remains in the sidecar for audit,
                # but has no rich offer to receive a cost projection.
                continue
            raise LessonOfferSourceError("Lesson offer refinement contains an unknown card.")
        by_card[card["card_index"]] = card
    if provenance.get("cards_sha256") != _legacy_digest(refined_cards):
        raise LessonOfferSourceError("Lesson offer cost refinement cards changed.")

    merged = deepcopy(dict(result))
    merged_offers: list[dict[str, Any]] = []
    for original_offer in result.get("offers", []):
        if not isinstance(original_offer, Mapping):
            raise LessonOfferSourceError("Lesson offer result contains an invalid offer.")
        offer = deepcopy(dict(original_offer))
        card_index = offer.get("card_index")
        refined = by_card.get(card_index)
        if refined is None:
            merged_offers.append(offer)
            continue
        title = refined.get("title")
        base_title = offer.get("title")
        if not isinstance(title, Mapping) or not isinstance(base_title, Mapping):
            raise LessonOfferSourceError("Lesson offer refinement title proof is missing.")
        if dict(title) != dict(base_title):
            raise LessonOfferSourceError("Lesson offer refinement card identity changed.")
        refined_label = refined.get("cost_label")
        base_label = offer.get("source_geometry", {}).get("cost_label")
        if not isinstance(refined_label, Mapping) or not isinstance(base_label, Mapping):
            raise LessonOfferSourceError("Lesson offer refinement cost-label proof is missing.")
        if dict(refined_label) != dict(base_label):
            raise LessonOfferSourceError("Lesson offer refinement cost-label geometry changed.")
        if not _refinement_card_geometry_is_valid(refined):
            raise LessonOfferSourceError("Lesson offer refinement card geometry proof changed.")
        expected_source_boxes, expected_crop_boxes = _expected_refinement_boxes(base_label.get("box"))
        if (
            refined.get("source_price_boxes") != expected_source_boxes
            or refined.get("crop_price_boxes") != expected_crop_boxes
        ):
            raise LessonOfferSourceError("Lesson offer refinement price crop geometry changed.")
        prices = offer.get("prices")
        recovered_prices = refined.get("prices")
        if not isinstance(prices, list) or len(prices) != len(COST_FIELDS):
            raise LessonOfferSourceError("Lesson offer result does not contain five price slots.")
        if not isinstance(recovered_prices, list) or len(recovered_prices) != len(COST_FIELDS):
            raise LessonOfferSourceError("Lesson offer refinement does not contain five price slots.")

        merged_prices: list[dict[str, Any]] = []
        base_reasons = offer.get("unknown_reasons", [])
        if not isinstance(base_reasons, list):
            raise LessonOfferSourceError("Lesson offer result uncertainty reasons are invalid.")
        reasons = [reason for reason in base_reasons if isinstance(reason, str)]
        for field_index, (field, base_price, recovered_price) in enumerate(
            zip(COST_FIELDS, prices, recovered_prices)
        ):
            if not isinstance(base_price, Mapping):
                raise LessonOfferSourceError("Lesson offer result contains an invalid price slot.")
            if not isinstance(recovered_price, Mapping):
                raise LessonOfferSourceError("Lesson offer refinement contains an invalid price slot.")
            base_price = deepcopy(dict(base_price))
            if (
                recovered_price.get("source_box") != expected_source_boxes[field_index]
                or recovered_price.get("crop_box") != expected_crop_boxes[field_index]
                or recovered_price.get("geometry_sha256") != refined.get("geometry_sha256")
            ):
                raise LessonOfferSourceError("Lesson offer refined price crop binding changed.")
            if recovered_price.get("value") is not None:
                if not _valid_refined_price(recovered_price, field):
                    raise LessonOfferSourceError("Lesson offer refined price lacks direct crop proof.")
                if base_price.get("status") == "accepted" and base_price.get("value") != recovered_price.get("value"):
                    conflict = dict(
                        field=field, value=None, amount=None, status="unknown",
                        unknown_reason="conflicting_source_cost", confidence=None,
                        source_line=deepcopy(base_price.get("source_line")),
                        source_alternatives=_price_alternative(base_price, recovered_price),
                        source_refinement=deepcopy(dict(recovered_price)),
                    )
                    merged_prices.append(conflict)
                    reasons = [reason for reason in reasons if _cost_reason_field(reason) != field]
                    reasons.append(f"{field}_conflicting_source_cost")
                    continue
                merged_price = deepcopy(dict(recovered_price))
                merged_price.setdefault("amount", merged_price.get("value"))
                merged_price["source_line"] = deepcopy(base_price.get("source_line"))
                merged_price["source_refinement"] = deepcopy(dict(recovered_price))
                merged_prices.append(merged_price)
                reasons = [reason for reason in reasons if _cost_reason_field(reason) != field]
                continue
            # A crop reread that remains unknown cannot erase a direct base
            # value.  It also cannot manufacture a value for an unresolved
            # base slot.
            if base_price.get("status") == "accepted" and base_price.get("value") is not None:
                base_price["source_refinement"] = deepcopy(dict(recovered_price))
                merged_prices.append(base_price)
            else:
                base_price["source_refinement"] = deepcopy(dict(recovered_price))
                merged_prices.append(base_price)

        known_cost = {}
        for field, price in zip(COST_FIELDS, merged_prices):
            if price.get("status") == "accepted" and type(price.get("value")) is int:
                known_cost[field] = price["value"]
        reasons = list(dict.fromkeys(reasons))
        offer["prices"] = merged_prices
        offer["cost"] = deepcopy(known_cost)
        payload = deepcopy(offer.get("payload", {}))
        if known_cost:
            payload["cost"] = deepcopy(known_cost)
        else:
            payload.pop("cost", None)
        offer["payload"] = payload
        offer["unknown_reasons"] = reasons
        complete = not reasons and len(merged_prices) == len(COST_FIELDS) and all(
            price.get("status") == "accepted" and type(price.get("value")) is int
            for price in merged_prices
        )
        offer["status"] = "complete" if complete else "unknown"
        offer["completeness"] = "complete" if complete else "partial"
        card_proof = deepcopy(offer.get("source_proof", {}))
        card_proof["cost_refinement"] = deepcopy(proof)
        offer["source_proof"] = card_proof
        merged_offers.append(offer)

    if any(card_index not in {offer.get("card_index") for offer in result.get("offers", [])}
           for card_index in by_card):
        raise LessonOfferSourceError("Lesson offer refinement contains an unknown card.")
    merged["offers"] = merged_offers
    merged["cost_refinement_provenance"] = deepcopy(proof)
    # Keep the adapter's grouped observation list in lockstep with offers.
    # This is a separate rich envelope; callers must not append these rows to
    # the generic preview_effects list.
    observations = []
    existing_observations = {
        item.get("offer_id"): item for item in result.get("observations", [])
        if isinstance(item, Mapping)
    }
    for offer in merged_offers:
        observation = deepcopy(existing_observations.get(offer.get("offer_id"), {}))
        observation.update(
            offer_id=offer.get("offer_id"),
            payload=deepcopy(offer.get("payload", {})),
            source_proof=deepcopy(offer.get("source_proof", {})),
            preview_only=True,
            committed=False,
            receipt_required=True,
        )
        observations.append(observation)
    merged["observations"] = observations
    return merged


def refine_lesson_offer_costs(
    raw: Mapping[str, Any],
    *,
    gameplay_path: str | Path,
    source_frame_path: str | Path | None = None,
    source_sha256: str | None = None,
    reader: Any,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Reread only missing card slots through the existing crop refiner.

    The crop reader is candidate-driven: it runs only for a verified lesson
    menu whose source line adapter left at least one price unresolved.  The
    source image, raw row, and frame identity are validated before merging.
    The returned sidecar is suitable for the normal cache loader; no file is
    written here.
    """

    base = adapt_lesson_offer_frame(
        raw,
        gameplay_path=gameplay_path,
        source_frame_path=source_frame_path,
        source_sha256=source_sha256,
    )
    if not base.get("offers") or not missing_cost_fields(base):
        return base, None
    if reader is None:
        return base, None
    try:
        from PIL import Image
        from .lesson_offer_refinement import (
            build as build_refinement,
            file_fingerprint,
            observe as observe_refinement,
        )

        from .frame_cache import open_rgb
        if True:
            pane = open_rgb(gameplay_path)
            extra = build_refinement(
                pane,
                deepcopy(dict(raw)),
                file_fingerprint(gameplay_path),
                reader,
                source_frame_path=source_frame_path,
            )
            # Validate the just-built sidecar through the same immutable crop,
            # diagnostic, and source-frame checks used by the cache loader
            # before allowing its cards into the rich adapter envelope.
            observe_refinement(
                pane,
                deepcopy(dict(raw)),
                extra,
                source_frame_path=source_frame_path,
            )
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        raise LessonOfferSourceError(f"Lesson offer cost crop reread failed: {exc}") from exc
    merged = merge_lesson_offer_cost_refinement(
        base,
        extra.get("cards"),
        provenance=extra,
        raw=raw,
        gameplay_path=gameplay_path,
        source_frame_path=source_frame_path,
        source_sha256=source_sha256,
    )
    return merged, extra


def parse_lesson_offers(raw: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Alias emphasizing that the adapter consumes source geometry only."""

    return adapt_lesson_offer_frame(raw, **kwargs)


def lesson_offer_observations(raw: Mapping[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    """Return only grouped preview offers for a source row."""

    return adapt_lesson_offer_frame(raw, **kwargs)["offers"]


__all__ = [
    "SCHEMA",
    "COST_REFINEMENT_SCHEMA",
    "GAMEPLAY_SIZE",
    "COST_FIELDS",
    "LessonOfferSourceError",
    "adapt_lesson_offer_frame",
    "missing_cost_fields",
    "merge_lesson_offer_cost_refinement",
    "refine_lesson_offer_costs",
    "parse_lesson_offers",
    "lesson_offer_observations",
]
