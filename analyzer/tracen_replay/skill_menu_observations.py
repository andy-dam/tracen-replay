"""Read source-bound skill menu observations without creating a purchase.

The Learn screen is a browse and draft surface.  Its ``Obtained`` badges and
card prices describe what is visible while a cart is being assembled; they do
not prove that a skill was acquired or that points were charged.  This module
keeps that distinction at the parser boundary.  It consumes immutable neural
line geometry (or already typed card facts), groups consecutive menu frames
into menu episodes, and returns preview observations that a later receipt
adapter may consume.

There are two intentionally separate entry points:

``adapt_skill_menu_frame`` reads one source frame.  It is suitable for the
normal producer beside :func:`tracen_replay.vision.parse` and returns typed
frame facts, card line proofs, and a preview observation.

``build_skill_menu_observations`` coalesces frame rows into menu episodes.  An
explicit ``selected``/``draft`` fact starts the draft portion of an episode;
the function never guesses a selected card from a balance, a price delta, or
the order of cards.  If that proof is absent, the draft name remains unknown.
"""

from __future__ import annotations

from copy import deepcopy
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCHEMA = "tracen-replay/skill-menu-observations-v1"
GAMEPLAY_SIZE = (810, 1080)

MENU_SCREENS = frozenset({
    "skill_selection", "skill_menu", "skill_preview", "training_skill_selection",
})
RECEIPT_SCREENS = frozenset({"skill_confirmation", "skill_receipt"})
MIN_TITLE_CONFIDENCE = 90.0
MIN_CONTROL_CONFIDENCE = 90.0
MIN_PRICE_CONFIDENCE = 85.0
MIN_CONFIRM_CONFIDENCE = 90.0

# The neural sidecar uses full-gameplay coordinates.  These bounds describe
# the stable gameplay layout, rather than one recording's card positions.
_TITLE_X = (320, 700)
_CONTROL_X = (650, 850)
_CARD_Y = (370, 900)
_CONFIRM_BOX = (430, 670, 850, 980)
_POINTS_LABEL_BOX = (480, 850, 300, 390)
_RECEIPT_RE = re.compile(r"(?:your|our)\s+trainee\s+learned\s+new\s+skills?\s*!?", re.I)
_INTEGER_RE = re.compile(r"\d{1,4}")
_NAME_SUFFIX_RE = re.compile(r"\s*[○◯◎⦿]\s*$")

_DESCRIPTION_PREFIXES = (
    "slightly ", "moderately ", "increase ", "decrease ", "recover ",
    "gain ", "control ", "improve ", "boost ", "decreases ", "raises ",
    "reduces ", "into a ", "when ", "during ", "on ", "at ", "for ",
)
_UI_TEXT = frozenset({
    "learn", "skill points", "confirm", "reset", "back", "skip", "quick",
    "obtained", "obtalned", "hint", "full", "stats", "connecting",
})
_KNOWN_STATUS = frozenset({
    "available", "obtained_or_selected", "obtained", "selected",
    "draft_selected", "unavailable", "unknown",
})
_KNOWN_PHASES = frozenset({"preview", "observed", "committed", "applied"})
_PRICE_STATUSES = frozenset({"complete", "partially_visible", "unreadable", "unknown"})

# The green action control sits to the left of the OCR price token in the
# fixed 810px gameplay layout.  The offset is derived from the control's
# source box for every card; it is never tied to a skill name, price, frame,
# or recording.  The selected control briefly shows a white five-point star
# over the ordinary plus icon.  These bounds are deliberately strict so a
# cursor animation, clipped control, or card background remains unknown.
_ACTION_ICON_X_OFFSET = 65
_ACTION_ICON_Y_OFFSET = 2
_ACTION_ICON_RADIUS = 18
_ACTION_GREEN_HUE = (35, 100)
_ACTION_GREEN_SATURATION = 70
_ACTION_GREEN_VALUE = 40
_SELECTION_WHITE_THRESHOLDS = (220, 230, 240, 250)
_SELECTION_WHITE_SPREAD = 75


class SkillMenuSourceError(ValueError):
    """Raised when optional source binding is malformed or stale."""


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()


def _confidence(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or not 0 <= result <= 100:
        return None
    return result


def _box(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        try:
            number = float(item)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(number) or number != int(number):
            return None
        result.append(int(number))
    left, top, right, bottom = result
    if not (0 <= left < right <= 1100 and 0 <= top < bottom <= GAMEPLAY_SIZE[1]):
        return None
    return result


def _center(box: list[int]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _line_copy(line: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return deepcopy(dict(line)) if isinstance(line, Mapping) else None


def _line_records(source: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = source.get("lines")
    if not isinstance(values, list):
        ocr = source.get("ocr")
        values = ocr.get("neural", ocr.get("lines")) if isinstance(ocr, Mapping) else None
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            continue
        text = _text(item.get("text"))
        box = _box(item.get("box"))
        confidence = _confidence(item.get("confidence"))
        if not text or box is None or confidence is None:
            continue
        result.append(dict(index=index, text=text, confidence=confidence, box=box))
    return result


def _evidence(value: Any) -> list[str]:
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = []
    result = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            continue
        normalized = item.strip().replace("\\", "/")
        if ".." in Path(normalized).parts:
            raise SkillMenuSourceError("Skill menu evidence escapes its declared root.")
        if normalized not in result:
            result.append(normalized)
    return result


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillMenuSourceError("Skill menu source proof is not JSON serializable.") from exc
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SkillMenuSourceError(f"Skill menu source evidence is unavailable: {path}") from exc
    return digest.hexdigest()


def _gameplay_sha256(path: str | Path) -> tuple[str, str]:
    try:
        from .frame_cache import rgb_digest

        pixels, size = rgb_digest(path)
        if size != GAMEPLAY_SIZE:
            raise SkillMenuSourceError("Skill menu gameplay evidence is not an 810x1080 pane.")
    except SkillMenuSourceError:
        raise
    except (OSError, ValueError) as exc:
        raise SkillMenuSourceError(f"Skill menu gameplay evidence is unreadable: {path}") from exc
    return pixels, _file_sha256(path)


def _selection_image(path: str | Path) -> tuple[Any, Any] | None:
    """Load one gameplay pane for the optional source-pixel selection proof."""

    try:
        from PIL import Image
        import cv2
        import numpy as np

        with Image.open(path) as opened:
            image = opened.convert("RGB")
            if image.size != GAMEPLAY_SIZE:
                return None
            array = np.asarray(image)
        if array.shape != (GAMEPLAY_SIZE[1], GAMEPLAY_SIZE[0], 3):
            return None
        return array, cv2.cvtColor(array, cv2.COLOR_RGB2HSV)
    except (ImportError, OSError, TypeError, ValueError):
        return None


def _selection_anchor(control_box: list[int] | None) -> tuple[int, int] | None:
    """Map a source price/control line to its adjacent action icon."""

    if control_box is None:
        return None
    left, top, right, bottom = control_box
    center_x = left - _ACTION_ICON_X_OFFSET
    center_y = round((top + bottom) / 2.0 - _ACTION_ICON_Y_OFFSET)
    if not (0 <= center_x < GAMEPLAY_SIZE[0] and 0 <= center_y < GAMEPLAY_SIZE[1]):
        return None
    return center_x, center_y


def _green_action_component(hsv: Any, anchor: tuple[int, int]) -> dict[str, Any] | None:
    """Require an intact, pane-local green action control."""

    import cv2
    import numpy as np

    center_x, center_y = anchor
    x0, x1 = center_x - 26, center_x + 27
    y0, y1 = center_y - 24, center_y + 25
    if x0 < 0 or y0 < 0 or x1 > GAMEPLAY_SIZE[0] or y1 > GAMEPLAY_SIZE[1]:
        return None
    crop = hsv[y0:y1, x0:x1]
    mask = (
        (crop[:, :, 0] >= _ACTION_GREEN_HUE[0])
        & (crop[:, :, 0] <= _ACTION_GREEN_HUE[1])
        & (crop[:, :, 1] >= _ACTION_GREEN_SATURATION)
        & (crop[:, :, 2] >= _ACTION_GREEN_VALUE)
    ).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    candidates: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if not (30 <= width <= 40 and 32 <= height <= 43 and 650 <= area <= 1250):
            continue
        if x <= 0 or y <= 0 or x + width >= mask.shape[1] or y + height >= mask.shape[0]:
            # A clipped icon cannot establish the action state.
            continue
        absolute_center = centroids[index] + (x0, y0)
        if abs(float(absolute_center[0]) - center_x) > 5 or abs(float(absolute_center[1]) - center_y) > 7:
            continue
        candidates.append({
            "label": index,
            "box": [x + x0, y + y0, x + x0 + width, y + y0 + height],
            "center": [round(float(absolute_center[0]), 2), round(float(absolute_center[1]), 2)],
            "area": area,
        })
    if len(candidates) != 1:
        return None
    return candidates[0]


def _white_star_candidates(array: Any, anchor: tuple[int, int], threshold: int) -> list[dict[str, Any]]:
    """Find compact star-like feedback inside an intact action-control box."""

    import cv2
    import numpy as np

    center_x, center_y = anchor
    x0, x1 = center_x - 26, center_x + 27
    y0, y1 = center_y - 24, center_y + 25
    crop = array[y0:y1, x0:x1]
    maximum = crop.max(axis=2).astype(np.int16)
    minimum = crop.min(axis=2).astype(np.int16)
    yy, xx = np.mgrid[0:crop.shape[0], 0:crop.shape[1]]
    radius = _ACTION_ICON_RADIUS
    region = ((xx - (center_x - x0)) ** 2 + (yy - (center_y - y0)) ** 2 <= radius ** 2)
    mask = (
        (maximum >= threshold)
        & ((maximum - minimum) <= _SELECTION_WHITE_SPREAD)
        & region
    ).astype(np.uint8)
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    candidates: list[dict[str, Any]] = []
    for index in range(1, count):
        x, y, width, height, area = (int(value) for value in stats[index])
        if not (12 <= width <= 18 and 12 <= height <= 21 and 100 <= area <= 230):
            continue
        occupancy = area / float(width * height)
        if occupancy < 0.40:
            continue
        component = (labels == index).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        perimeter = float(cv2.arcLength(contour, True))
        moments = cv2.moments(component)
        if moments["m00"] <= 0 or area <= 0:
            continue
        component_center = np.array(
            [moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]],
            dtype=float,
        )
        contour_points = contour[:, 0, :].astype(float)
        radii = np.linalg.norm(contour_points - component_center, axis=1)
        normalized_perimeter = perimeter / math.sqrt(float(area))
        normalized_radial_range = (
            float(radii.max() - radii.min()) / math.sqrt(float(area))
            if radii.size else 0.0
        )
        # A filled square or smooth circle can occupy the same source box and
        # brightness range as the feedback mark.  The mark has a pointed,
        # irregular outline: its edge length and radial range are both larger
        # than a compact smooth/rectangular glyph of the same area.  These are
        # scale-normalized shape tests, so they remain tied to the observed
        # pixels rather than a particular card, frame, or skill.
        if normalized_perimeter < 3.90 or normalized_radial_range < 0.28:
            continue
        absolute_center = centroids[index] + (x0, y0)
        # The feedback star is left of the ordinary plus center.  This rejects
        # isolated bright UI specks while retaining a partially antialiased
        # star.  It is a geometry relationship, not a card/value lookup.
        if not (-17 <= float(absolute_center[0]) - center_x <= -4):
            continue
        if not (-2 <= float(absolute_center[1]) - center_y <= 12):
            continue
        candidates.append({
            "box": [x + x0, y + y0, x + x0 + width, y + y0 + height],
            "center": [round(float(absolute_center[0]), 2), round(float(absolute_center[1]), 2)],
            "area": area,
            "occupancy": round(float(occupancy), 4),
            "normalized_perimeter": round(normalized_perimeter, 4),
            "normalized_radial_range": round(normalized_radial_range, 4),
            "threshold": threshold,
        })
    return candidates


def detect_skill_selection_marker(
    image_or_path: Any, control_box: list[int] | tuple[int, int, int, int] | None,
) -> dict[str, Any] | None:
    """Return a strict same-frame pixel proof for a selected draft card.

    ``control_box`` is the immutable OCR box of the card's numeric price.  The
    function reads only the adjacent action control in the supplied 810x1080
    gameplay pane.  A complete green control plus a stable five-point-star
    feedback shape is required at every brightness threshold; an ordinary
    plus, cursor animation, clipped crop, or ambiguous shape returns ``None``.
    """

    normalized = _box(control_box)
    if normalized is None:
        return None
    loaded = _selection_image(image_or_path) if isinstance(image_or_path, (str, Path)) else None
    if loaded is None:
        try:
            from PIL import Image

            image = image_or_path.convert("RGB")
            if image.size != GAMEPLAY_SIZE:
                return None
            import cv2
            import numpy as np

            array = np.asarray(image)
            loaded = (array, cv2.cvtColor(array, cv2.COLOR_RGB2HSV))
        except (AttributeError, ImportError, TypeError, ValueError):
            return None
    array, hsv = loaded
    anchor = _selection_anchor(normalized)
    if anchor is None:
        return None
    base = _green_action_component(hsv, anchor)
    if base is None:
        return None
    observations: list[dict[str, Any]] = []
    for threshold in _SELECTION_WHITE_THRESHOLDS:
        candidates = _white_star_candidates(array, anchor, threshold)
        if len(candidates) != 1:
            continue
        observations.append(candidates[0])
    if len(observations) != len(_SELECTION_WHITE_THRESHOLDS):
        return None
    reference = observations[len(observations) // 2]
    if any(
        max(abs(item["center"][axis] - reference["center"][axis]) for axis in (0, 1)) > 2
        or abs(item["box"][2] - item["box"][0] - (reference["box"][2] - reference["box"][0])) > 4
        or abs(item["box"][3] - item["box"][1] - (reference["box"][3] - reference["box"][1])) > 4
        for item in observations
    ):
        return None
    selected = observations[len(observations) // 2]
    return {
        "method": "source_pixel_action_star_v1",
        "basis": "same_frame_card_control_geometry",
        "semantics": "transient_draft_selection_feedback",
        "coordinate_space": "gameplay_crop",
        "anchor": [anchor[0], anchor[1]],
        "box": deepcopy(selected["box"]),
        "center": deepcopy(selected["center"]),
        "votes": len(observations),
        "thresholds": list(_SELECTION_WHITE_THRESHOLDS),
        "observations": deepcopy(observations),
        "action_control": deepcopy(base),
    }


def _hash_value(value: Any, label: str) -> str | None:
    if value is None:
        return None
    text = _text(value).casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise SkillMenuSourceError(f"Skill menu {label} is not SHA-256.")
    return text


def _facts(source: Mapping[str, Any]) -> Mapping[str, Any]:
    value = source.get("facts")
    return value if isinstance(value, Mapping) else source


def _first(source: Mapping[str, Any], facts: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in facts:
            return facts[key]
        if key in source:
            return source[key]
    return None


def _normal_name(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    text = _NAME_SUFFIX_RE.sub("", text).strip()
    if not text or not any(character.isalpha() for character in text):
        return None
    return text


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _is_name_candidate(line: Mapping[str, Any]) -> bool:
    text = _normal_name(line.get("text"))
    if not text:
        return False
    folded = text.casefold()
    box = line["box"]
    if line.get("confidence", 0) < MIN_TITLE_CONFIDENCE:
        return False
    if not (_TITLE_X[0] <= box[0] <= _TITLE_X[1] and _CARD_Y[0] <= _center(box)[1] <= _CARD_Y[1]):
        return False
    if folded in _UI_TEXT or folded.startswith("performance point cost"):
        return False
    if folded.startswith(_DESCRIPTION_PREFIXES):
        return False
    if any(character.isdigit() for character in folded) and not any(character.isalpha() for character in folded):
        return False
    if re.fullmatch(r"(?:hint|lvl|level)\b.*", folded):
        return False
    return True


def _control_lines(lines: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for line in lines:
        box = line["box"]
        x, y = _center(box)
        if not (_CONTROL_X[0] <= x <= _CONTROL_X[1] and _CARD_Y[0] <= y <= _CARD_Y[1]):
            continue
        label = line["text"].casefold()
        # The fixed badge may lose its final glyph under cursor/animation
        # overlap. Keep the original text and require all remaining glyphs,
        # high confidence, and the existing card-control geometry. This is a
        # visible menu status, never an acquisition receipt.
        obtained_label = label == "obtained" or (
            len(label) == len("obtained") - 1 and "obtained".startswith(label))
        if line["confidence"] >= MIN_CONTROL_CONFIDENCE and obtained_label:
            result.append(("obtained", line))
            continue
        if line["confidence"] >= MIN_PRICE_CONFIDENCE and re.fullmatch(r"\d{1,4}", line["text"]):
            result.append(("price", line))
    return result


def _title_for_control(lines: list[dict[str, Any]], control: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    control_box = control["box"]
    candidates: list[dict[str, Any]] = []
    for line in lines:
        if not _is_name_candidate(line):
            continue
        title_box = line["box"]
        gap = control_box[1] - title_box[3]
        if not (10 <= gap <= 72):
            continue
        candidates.append(line)
    if not candidates:
        return None, "missing_card_title"
    candidates.sort(key=lambda line: (control_box[1] - line["box"][3], -line["confidence"], line["index"]))
    nearest_gap = control_box[1] - candidates[0]["box"][3]
    nearest = [line for line in candidates if control_box[1] - line["box"][3] == nearest_gap]
    if len(nearest) != 1:
        return None, "ambiguous_card_title"
    # A second title at the same distance is a source conflict.  Do not
    # select one merely because it appears earlier in OCR order.
    if len(candidates) > 1 and control_box[1] - candidates[1]["box"][3] == nearest_gap:
        return None, "ambiguous_card_title"
    return nearest[0], None


def _typed_cards(source: Mapping[str, Any], facts: Mapping[str, Any]) -> list[dict[str, Any]]:
    values: Any = None
    for key in ("skill_menu_cards", "skill_cards", "visible_skill_menu_cards"):
        candidate = facts.get(key, source.get(key))
        if isinstance(candidate, list):
            values = candidate
            break
    if not isinstance(values, list):
        return []
    result: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        name = _normal_name(item.get("name", item.get("name_text")))
        if name is None:
            continue
        cost = item.get("displayed_cost", item.get("cost"))
        if isinstance(cost, bool) or not isinstance(cost, int) or cost < 0:
            cost = None
        status = _text(item.get("menu_status", item.get("status"))).casefold()
        if status not in _KNOWN_STATUS:
            status = "unknown"
        selected = item.get("selected") is True or status in {"selected", "draft_selected"}
        record = {
            "name": name,
            "name_box": _box(item.get("name_box")),
            "displayed_cost": cost,
            "menu_status": "obtained_or_selected" if status == "obtained" else status,
            "selected": selected,
            "selection_status": "selected" if selected else None,
            "source_line_indices": [],
        }
        proof = item.get("source_proof")
        if isinstance(proof, Mapping):
            record["source_proof"] = deepcopy(dict(proof))
        result.append(record)
    return result


def _line_cards(lines: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    controls = _control_lines(lines)
    cards: list[dict[str, Any]] = []
    rejected: list[str] = []
    seen_controls: set[int] = set()
    for kind, control in controls:
        title, reason = _title_for_control(lines, control)
        if title is None:
            if reason:
                rejected.append(reason)
            continue
        if control["index"] in seen_controls:
            continue
        seen_controls.add(control["index"])
        name = _normal_name(title["text"])
        if name is None:
            rejected.append("invalid_card_title")
            continue
        cards.append({
            "name": name,
            "name_box": deepcopy(title["box"]),
            "displayed_cost": int(control["text"]) if kind == "price" else None,
            "menu_status": "obtained_or_selected" if kind == "obtained" else "available",
            "selected": False,
            "selection_status": None,
            "source_line_indices": [title["index"], control["index"]],
            "source_lines": [_line_copy(title), _line_copy(control)],
        })
    return cards, list(dict.fromkeys(rejected))


def _selected_names(source: Mapping[str, Any], facts: Mapping[str, Any], cards: list[dict[str, Any]]) -> tuple[list[str] | None, list[list[str]]]:
    values: list[list[str]] = []
    for key in (
        "selected_draft_names", "selected_skill_names", "draft_skill_names", "selected_names",
    ):
        value = facts.get(key, source.get(key))
        if value is None:
            continue
        if not isinstance(value, list):
            continue
        names = [_normal_name(item) for item in value]
        names = [name for name in names if name is not None]
        values.append(list(dict.fromkeys(names)))
    for key in ("skill_menu_selection", "selection_observation", "draft_selection"):
        envelope = facts.get(key, source.get(key))
        if not isinstance(envelope, Mapping):
            continue
        value = envelope.get("selected_draft_names", envelope.get("selected_names", envelope.get("names")))
        if isinstance(value, list):
            names = [_normal_name(item) for item in value]
            values.append(list(dict.fromkeys(name for name in names if name is not None)))
    card_names = [card["name"] for card in cards if card.get("selected") is True]
    if card_names:
        values.append(list(dict.fromkeys(card_names)))
    if not values:
        return None, []
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
    if len(unique) > 1:
        return None, unique
    return unique[0], []


def _pixel_selected_cards(
    cards: list[dict[str, Any]], gameplay_path: str | Path | None,
) -> list[dict[str, Any]]:
    """Attach only complete same-frame pixel selection proofs to card rows."""

    if gameplay_path is None:
        return []
    proofs: list[dict[str, Any]] = []
    for card in cards:
        if card.get("menu_status") != "available":
            continue
        controls = card.get("source_lines")
        if not isinstance(controls, list):
            continue
        control = next(
            (line for line in controls
             if isinstance(line, Mapping)
             and re.fullmatch(r"\d{1,4}", _text(line.get("text")))),
            None,
        )
        control_box = _box(control.get("box")) if isinstance(control, Mapping) else None
        marker = detect_skill_selection_marker(gameplay_path, control_box)
        if marker is None:
            continue
        card["selected"] = True
        card["selection_status"] = "pixel_selected_draft_card"
        card["selection_marker"] = deepcopy(marker)
        proofs.append({"name": card.get("name"), "marker": deepcopy(marker)})
    return proofs


def _confirm_line(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = []
    for line in lines:
        if line["confidence"] < MIN_CONFIRM_CONFIDENCE or line["text"].casefold() != "confirm":
            continue
        box = line["box"]
        x, y = _center(box)
        if _CONFIRM_BOX[0] <= x <= _CONFIRM_BOX[2] and _CONFIRM_BOX[1] <= y <= _CONFIRM_BOX[3]:
            candidates.append(line)
    if len(candidates) != 1:
        return None
    return _line_copy(candidates[0])


def _displayed_points(lines: list[dict[str, Any]], source: Mapping[str, Any], facts: Mapping[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    values = []
    for key in ("displayed_skill_points", "skill_points_after"):
        value = facts.get(key, source.get(key))
        if type(value) is int and value >= 0:
            values.append((value, None))
    labels = [line for line in lines if line["confidence"] >= MIN_TITLE_CONFIDENCE and line["text"].casefold() == "skill points"]
    for label in labels:
        label_box = label["box"]
        candidates = []
        for line in lines:
            if not re.fullmatch(r"\d{1,5}", line["text"]):
                continue
            if line["confidence"] < MIN_PRICE_CONFIDENCE:
                continue
            box = line["box"]
            if box[0] <= label_box[2] or abs(_center(box)[1] - _center(label_box)[1]) > 25:
                continue
            if _center(box)[0] > 850:
                continue
            candidates.append(line)
        if len(candidates) == 1:
            values.append((int(candidates[0]["text"]), candidates[0]))
    distinct = {value for value, _line in values}
    if len(distinct) != 1:
        return None, None
    value = next(iter(distinct))
    line = next((line for candidate, line in values if candidate == value and line is not None), None)
    return value, _line_copy(line)


def _explicit_bool(source: Mapping[str, Any], facts: Mapping[str, Any], *keys: str) -> tuple[bool | None, bool]:
    values = []
    for key in keys:
        for value in (facts.get(key), source.get(key)):
            if isinstance(value, bool):
                values.append(value)
    if not values:
        return None, False
    if len(set(values)) != 1:
        return None, True
    return values[0], False


def _screen(source: Mapping[str, Any], lines: list[dict[str, Any]], override: str | None) -> str | None:
    phase = _text(source.get("phase")).casefold()
    if phase and phase not in {"preview", "observed"}:
        return None
    if source.get("result_grid") is True:
        return None
    if any(_RECEIPT_RE.search(line["text"]) for line in lines):
        return None
    if any(source.get(key) is True for key in ("committed", "applied", "charged", "purchase_committed")):
        return None
    value = _text(override or source.get("screen")).casefold()
    if value:
        if value in MENU_SCREENS:
            return "skill_selection"
        return None
    header = _text(source.get("header")).casefold()
    if header not in {"learn", "skills", "skill"}:
        if not any(line["text"].casefold() == "learn" and line["box"][1] < 80 for line in lines):
            return None
    return "skill_selection"


def _source_proof(
    raw: Mapping[str, Any], lines: list[dict[str, Any]], cards: list[dict[str, Any]],
    *, timestamp: int | None, evidence: list[str], gameplay_path: str | Path | None,
    source_frame_path: str | Path | None, expected_evidence_sha256: str | None,
    source_sha256: str | None,
) -> dict[str, Any]:
    raw_source = _hash_value(raw.get("source_sha256"), "source hash")
    supplied_source = _hash_value(source_sha256, "source hash")
    if raw_source and supplied_source and raw_source != supplied_source:
        raise SkillMenuSourceError("Skill menu source hash changed.")
    source_hash = supplied_source or raw_source
    raw_gameplay = _hash_value(raw.get("gameplay_sha256"), "gameplay hash")
    raw_source_frame = _hash_value(raw.get("source_frame_sha256"), "source-frame hash")
    evidence_hash = None
    gameplay_hash = raw_gameplay
    if gameplay_path is not None:
        gameplay_hash, evidence_hash = _gameplay_sha256(gameplay_path)
        if raw_gameplay and gameplay_hash != raw_gameplay:
            raise SkillMenuSourceError("Skill menu gameplay pixels do not match the source row.")
    expected = _hash_value(expected_evidence_sha256, "evidence hash")
    if expected is not None:
        if evidence_hash is None:
            raise SkillMenuSourceError("Skill menu gameplay evidence is required for hash verification.")
        if evidence_hash != expected:
            raise SkillMenuSourceError("Skill menu gameplay evidence bytes changed.")
    source_frame_verified = False
    if source_frame_path is not None:
        actual = _file_sha256(source_frame_path)
        if raw_source_frame and actual != raw_source_frame:
            raise SkillMenuSourceError("Skill menu source frame changed.")
        raw_source_frame = raw_source_frame or actual
        source_frame_verified = True
    used_lines = sorted({index for card in cards for index in card.get("source_line_indices", [])})
    if timestamp is not None:
        proof_timestamp = timestamp
    else:
        proof_timestamp = raw.get("source_timestamp_ms")
        if type(proof_timestamp) is not int or proof_timestamp < 0:
            proof_timestamp = None
    return {
        "basis": "immutable_neural_lines" if lines else "typed_skill_menu_facts",
        "evidence": list(evidence),
        "source_timestamp_ms": proof_timestamp,
        "source_sha256": source_hash,
        "evidence_sha256": evidence_hash or expected,
        "gameplay_sha256": gameplay_hash,
        "source_frame_sha256": raw_source_frame,
        "source_frame_verified": source_frame_verified,
        "raw_sha256": _digest(dict(raw)),
        "line_count": len(lines),
        "used_line_indices": used_lines,
        "coordinate_space": "full_gameplay",
        "gameplay_size": list(GAMEPLAY_SIZE),
        "parser_input": "immutable_neural_lines" if lines else "typed_skill_menu_facts",
    }


def _frame_payload(
    cards: list[dict[str, Any]], selected_names: list[str] | None,
    selection_conflicts: list[list[str]], confirm: bool | None,
    confirm_conflict: bool, points: int | None, price_status: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    obtained = []
    prices: dict[str, int] = {}
    price_values: dict[str, set[int]] = {}
    for card in cards:
        name = card.get("name")
        if not isinstance(name, str):
            continue
        if card.get("menu_status") == "obtained_or_selected" and name not in obtained:
            obtained.append(name)
        cost = card.get("displayed_cost")
        if card.get("menu_status") == "available" and type(cost) is int and cost >= 0:
            price_values.setdefault(name, set()).add(cost)
    price_conflicts = {
        name: sorted(values) for name, values in price_values.items() if len(values) > 1
    }
    for name, values in price_values.items():
        if len(values) == 1:
            prices[name] = next(iter(values))
    for name in price_conflicts:
        prices.pop(name, None)
    selection_status = None
    if selected_names is not None and confirm is True:
        selection_status = "not_yet_confirmed"
    payload: dict[str, Any] = {"kind": "skill"}
    if obtained:
        payload["visible_precommit_obtained_names"] = obtained
    if prices:
        payload["visible_card_prices"] = prices
    if price_conflicts:
        payload["visible_card_price_conflicts"] = price_conflicts
    if price_status is not None:
        payload["price_status"] = price_status
    if selected_names is not None:
        payload["selected_draft_names"] = deepcopy(selected_names)
    if selection_conflicts:
        payload["selected_draft_name_conflicts"] = deepcopy(selection_conflicts)
    if selection_status is not None:
        payload["selection_status"] = selection_status
    if confirm is not None and not confirm_conflict:
        payload["confirm_available"] = confirm
    if confirm_conflict:
        payload["confirm_conflict"] = True
    # This is a displayed counter, included only when a source-backed draft
    # identity is present.  It is never a charge and cannot be reconstructed
    # from prices, gains, or before/after state.
    if selected_names is not None and points is not None:
        payload["skill_points_after"] = points
    return payload, {
        "obtained": obtained,
        "prices": prices,
        "price_conflicts": price_conflicts,
        "selection_status": selection_status,
    }


def adapt_skill_menu_frame(
    raw: Mapping[str, Any], *, screen: str | None = None,
    source_timestamp_ms: int | None = None, evidence: str | Iterable[str] | None = None,
    gameplay_path: str | Path | None = None, source_frame_path: str | Path | None = None,
    expected_evidence_sha256: str | None = None, source_sha256: str | None = None,
) -> dict[str, Any]:
    """Return source-bound preview facts from one Learn-screen frame.

    Draft identity requires explicit selected-card facts or a source-verified
    visual selection marker beside that card. A visible Confirm button alone
    does not identify a draft, and a displayed point counter remains a counter
    observation rather than a debit.
    """

    if not isinstance(raw, Mapping):
        raise SkillMenuSourceError("Skill menu source row must be an object.")
    lines = _line_records(raw)
    facts = _facts(raw)
    actual_screen = _screen(raw, lines, screen)
    evidence_paths = _evidence(evidence if evidence is not None else raw.get("evidence"))
    timestamp = source_timestamp_ms if type(source_timestamp_ms) is int else raw.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        timestamp = None
    if actual_screen is None:
        return {
            "schema_version": SCHEMA,
            "screen": None,
            "phase": "preview",
            "cards": [],
            "observations": [],
            "facts": {},
            "preview_only": True,
            "committed": False,
            "receipt_required": True,
            "rejected": ["not_skill_selection"],
        }
    cards, rejected = _line_cards(lines)
    if not cards:
        cards = _typed_cards(raw, facts)
    # A typed card list may be present alongside raw lines.  Raw geometry is
    # authoritative when it yields a card; typed facts fill only missing rows.
    if lines and cards and any(card.get("source_line_indices") for card in cards):
        typed = _typed_cards(raw, facts)
        by_name = {card["name"]: card for card in cards}
        for card in typed:
            by_name.setdefault(card["name"], card)
        cards = list(by_name.values())
    pixel_selection_proofs = _pixel_selected_cards(cards, gameplay_path)
    selected, selection_conflicts = _selected_names(raw, facts, cards)
    explicit_confirm, confirm_conflict = _explicit_bool(raw, facts, "confirm_available")
    confirm_line = _confirm_line(lines)
    line_confirm = confirm_line is not None
    if explicit_confirm is not None and explicit_confirm != line_confirm and confirm_line is not None:
        confirm_conflict = True
        explicit_confirm = None
    confirm = None if confirm_conflict else (
        explicit_confirm if explicit_confirm is not None else (line_confirm if lines else None)
    )
    points, points_line = _displayed_points(lines, raw, facts)
    explicit_status = _first(raw, facts, "selection_status")
    status = _text(explicit_status).casefold() if isinstance(explicit_status, str) else None
    if status not in {"not_yet_confirmed", "confirmed", "unknown"}:
        status = None
    if status == "confirmed":
        # A selection screen with a Confirm control cannot establish a commit.
        status = "unknown"
    price_status = _first(raw, facts, "price_status")
    if not isinstance(price_status, str) or price_status.casefold() not in _PRICE_STATUSES:
        price_status = "partially_visible" if cards else "unknown"
    else:
        price_status = price_status.casefold()
    payload, summary = _frame_payload(
        cards, selected, selection_conflicts, confirm, confirm_conflict, points, price_status
    )
    if pixel_selection_proofs:
        payload["selected_draft_card_proof"] = deepcopy(pixel_selection_proofs)
    proof = _source_proof(
        raw, lines, cards, timestamp=timestamp, evidence=evidence_paths,
        gameplay_path=gameplay_path, source_frame_path=source_frame_path,
        expected_evidence_sha256=expected_evidence_sha256, source_sha256=source_sha256,
    )
    if selected is not None and status is not None:
        payload["selection_status"] = status
    elif selection_conflicts:
        payload["selection_status"] = "unknown"
    frame_facts: dict[str, Any] = {
        "skill_menu_cards": deepcopy(cards),
        "skill_cards": deepcopy(cards),
        "visible_precommit_obtained_names": deepcopy(summary["obtained"]),
        "visible_card_prices": deepcopy(summary["prices"]),
        "price_status": price_status,
        "item_list_complete": False,
        "skill_menu_preview": True,
        "skill_menu_source_proof": deepcopy(proof),
        "displayed_skill_points": points,
        "confirm_available": confirm,
        "selected_draft_names": deepcopy(selected) if selected is not None else [],
        "selection_status": payload.get("selection_status", "unknown"),
    }
    if points_line is not None:
        frame_facts["skill_points_line_proof"] = points_line
    if confirm_line is not None:
        frame_facts["confirm_line_proof"] = confirm_line
    if summary["price_conflicts"]:
        frame_facts["visible_card_price_conflicts"] = deepcopy(summary["price_conflicts"])
    if selection_conflicts:
        frame_facts["selected_draft_name_conflicts"] = deepcopy(selection_conflicts)
    if pixel_selection_proofs:
        frame_facts["selected_draft_card_proof"] = deepcopy(pixel_selection_proofs)
    if selected is not None and points is not None:
        frame_facts["skill_points_after"] = points
        frame_facts["skill_points_semantics"] = "displayed_menu_counter_after_explicit_draft"
    observation = {
        "id": "skill-menu-frame:" + (str(timestamp) if timestamp is not None else proof["raw_sha256"][:24]),
        "category": "purchase",
        "phase": "preview",
        "status": "observed",
        "start_ms": timestamp if timestamp is not None else 0,
        "end_ms": timestamp if timestamp is not None else 0,
        "payload": deepcopy(payload),
        "evidence": evidence_paths,
        "source_proof": deepcopy(proof),
        "preview_only": True,
        "committed": False,
        "receipt_required": True,
        "uncertain": bool(confirm_conflict or selection_conflicts or summary["price_conflicts"]),
    }
    return {
        "schema_version": SCHEMA,
        "screen": "skill_selection",
        "phase": "preview",
        "cards": deepcopy(cards),
        "observations": [observation] if evidence_paths else [],
        "payload": deepcopy(payload),
        "facts": frame_facts,
        "source_proof": proof,
        "preview_only": True,
        "committed": False,
        "receipt_required": True,
        "rejected": list(dict.fromkeys(rejected)),
    }


def _canonical_names(records: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve only close OCR spellings, retaining unrelated names."""

    names = []
    for record in records:
        name = record.get("name")
        if isinstance(name, str) and name not in names:
            names.append(name)
    parent = {name: name for name in names}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if _name_key(left) == _name_key(right):
                union(left, right)
                continue
            left_tokens, right_tokens = left.casefold().split(), right.casefold().split()
            if len(left_tokens) != len(right_tokens):
                continue
            ratio = difflib.SequenceMatcher(None, _name_key(left), _name_key(right)).ratio()
            if ratio < 0.84:
                continue
            left_rows = [record for record in records if record.get("name") == left]
            right_rows = [record for record in records if record.get("name") == right]
            costs_left = {row.get("displayed_cost") for row in left_rows if type(row.get("displayed_cost")) is int}
            costs_right = {row.get("displayed_cost") for row in right_rows if type(row.get("displayed_cost")) is int}
            if costs_left and costs_right and costs_left.isdisjoint(costs_right):
                continue
            times_left = [row.get("source_timestamp_ms") for row in left_rows if type(row.get("source_timestamp_ms")) is int]
            times_right = [row.get("source_timestamp_ms") for row in right_rows if type(row.get("source_timestamp_ms")) is int]
            if times_left and times_right and min(
                abs(left_time - right_time) for left_time in times_left for right_time in times_right
            ) > 1000:
                continue
            union(left, right)
    groups: dict[str, list[str]] = {}
    for name in names:
        groups.setdefault(find(name), []).append(name)
    selected: dict[str, str] = {}
    for root, variants in groups.items():
        def score(name: str) -> tuple[int, float, int, int, str]:
            rows = [record for record in records if record.get("name") == name]
            return (
                len(rows),
                max((float(row.get("confidence", 0)) for row in rows), default=0),
                sum(1 for row in rows if row.get("source_line_indices")),
                len(name),
                name,
            )
        canonical = max(variants, key=score)
        for variant in variants:
            selected[variant] = canonical
    return selected


def read_pixel_selection(raw, *, gameplay_path, source_frame_path, source_sha256=None):
    """Persist source-bound draft evidence for replay without reopening images."""
    adapted = adapt_skill_menu_frame(raw, gameplay_path=gameplay_path,
                                    source_frame_path=source_frame_path,
                                    source_sha256=source_sha256)
    payload = adapted.get("payload", {})
    if not payload.get("selected_draft_card_proof"):
        return None
    return {"selected_draft_names": deepcopy(payload["selected_draft_names"]),
            "selected_draft_card_proof": deepcopy(payload["selected_draft_card_proof"]),
            "line_geometry_sha256": _digest(_line_records(raw)),
            "source_proof": deepcopy(adapted["source_proof"])}


def _row_gameplay_path(row: Mapping[str, Any], source_root: str | Path | None) -> Path | None:
    """Resolve an optional normal-producer gameplay pane beside one row."""

    values: list[Any] = []
    for key in ("gameplay_path", "gameplay_evidence_path", "source_image_path", "image_path"):
        if key in row:
            values.append(row.get(key))
    if source_root is not None:
        for item in _evidence(row.get("evidence")):
            values.append(item)
    root = Path(source_root) if source_root is not None else None
    for value in values:
        if not isinstance(value, (str, Path)):
            continue
        candidate = Path(value)
        if not candidate.is_absolute() and root is not None:
            candidate = root / candidate
        if candidate.suffix.casefold() != ".png" or not candidate.is_file():
            continue
        return candidate
    return None


def _row_frame(
    row: Mapping[str, Any], index: int, source_root: str | Path | None = None,
) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    gameplay_path = _row_gameplay_path(row, source_root)
    try:
        adapted = adapt_skill_menu_frame(row, gameplay_path=gameplay_path)
    except SkillMenuSourceError:
        return None
    if adapted.get("screen") != "skill_selection":
        return None
    timestamp = row.get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        timestamp = adapted.get("source_proof", {}).get("source_timestamp_ms")
    if type(timestamp) is not int or timestamp < 0:
        return None
    facts = adapted.get("facts") if isinstance(adapted.get("facts"), Mapping) else {}
    payload = adapted.get("payload") if isinstance(adapted.get("payload"), Mapping) else {}
    cards = adapted.get("cards") if isinstance(adapted.get("cards"), list) else []
    pixel = _facts(row).get("skill_menu_pixel_selection")
    if isinstance(pixel, Mapping):
        proof = pixel.get("source_proof", {})
        names = pixel.get("selected_draft_names")
        markers = pixel.get("selected_draft_card_proof")
        known = {card.get("name") for card in cards}
        if (isinstance(proof, Mapping)
                and proof.get("source_timestamp_ms") == timestamp
                and proof.get("evidence") == _evidence(row.get("evidence"))
                and pixel.get("line_geometry_sha256") == _digest(_line_records(row))
                and isinstance(names, list) and names
                and all(isinstance(name, str) and name in known for name in names)
                and isinstance(markers, list) and len(markers) == len(names)
                and [marker.get("name") for marker in markers if isinstance(marker, Mapping)] == names
                and all(isinstance(marker.get("marker"), Mapping)
                        and marker["marker"].get("method") == "source_pixel_action_star_v1"
                        for marker in markers)):
            facts = dict(facts, selected_draft_names=deepcopy(names))
            payload = dict(payload, selected_draft_names=deepcopy(names),
                           selected_draft_card_proof=deepcopy(markers))
            if facts.get("confirm_available") is True:
                facts["selection_status"] = "not_yet_confirmed"
                payload["selection_status"] = "not_yet_confirmed"
            if type(facts.get("displayed_skill_points")) is int:
                payload["skill_points_after"] = facts["displayed_skill_points"]
            adapted["source_proof"] = deepcopy(proof)
    selected = facts.get("selected_draft_names")
    selected = selected if isinstance(selected, list) and selected else None
    evidence = _evidence(row.get("evidence"))
    if not evidence:
        evidence = _evidence(adapted.get("source_proof", {}).get("evidence"))
    return {
        "index": index,
        "source_timestamp_ms": timestamp,
        "evidence": evidence,
        "cards": deepcopy(cards),
        "facts": deepcopy(dict(facts)),
        "payload": deepcopy(dict(payload)),
        "selected_names": deepcopy(selected),
        "selection_status": facts.get("selection_status"),
        "confirm_available": facts.get("confirm_available") if isinstance(facts.get("confirm_available"), bool) else None,
        "displayed_skill_points": facts.get("displayed_skill_points") if type(facts.get("displayed_skill_points")) is int else None,
        "source_proof": deepcopy(adapted.get("source_proof", {})),
        "episode_key": row.get("skill_menu_episode_id", row.get("menu_episode_id")),
    }


def _episode_payload(frames: list[dict[str, Any]], post_commit: bool, episode_index: int) -> dict[str, Any]:
    draft_frames = [frame for frame in frames if frame.get("selected_names")]
    draft_start = min((frame["source_timestamp_ms"] for frame in draft_frames), default=None)
    offer_frames = [
        frame for frame in frames
        if not post_commit and (draft_start is None or frame["source_timestamp_ms"] < draft_start)
    ]
    source_records = []
    for frame in offer_frames:
        for card in frame.get("cards", []):
            if isinstance(card, Mapping):
                item = dict(card)
                item["source_timestamp_ms"] = frame["source_timestamp_ms"]
                item["evidence"] = frame.get("evidence", [])
                source_records.append(item)
    name_map = _canonical_names(source_records)
    name_variants: dict[str, list[str]] = {}
    for raw_name, canonical_name in name_map.items():
        if raw_name != canonical_name:
            name_variants.setdefault(canonical_name, []).append(raw_name)
    name_variants = {
        canonical_name: sorted(set([canonical_name, *variants]))
        for canonical_name, variants in name_variants.items()
    }
    obtained: list[str] = []
    price_values: dict[str, set[int]] = {}
    status_observations: dict[str, list[dict[str, Any]]] = {}
    for record in source_records:
        raw_name = record.get("name")
        if not isinstance(raw_name, str):
            continue
        name = name_map.get(raw_name, raw_name)
        status_observations.setdefault(name, []).append({
            "status": record.get("menu_status"),
            "source_timestamp_ms": record.get("source_timestamp_ms"),
            "evidence": deepcopy(record.get("evidence", [])),
        })
        if record.get("menu_status") == "obtained_or_selected" and name not in obtained:
            obtained.append(name)
        cost = record.get("displayed_cost")
        if record.get("menu_status") == "available" and type(cost) is int and cost >= 0:
            price_values.setdefault(name, set()).add(cost)
    price_conflicts = {name: sorted(values) for name, values in price_values.items() if len(values) > 1}
    prices = {name: next(iter(values)) for name, values in price_values.items() if len(values) == 1}
    for name in price_conflicts:
        prices.pop(name, None)
    selected_values: list[list[str]] = []
    for frame in draft_frames:
        names = [name_map.get(name, name) for name in frame.get("selected_names", []) if isinstance(name, str)]
        if names and names not in selected_values:
            selected_values.append(names)
    selected = selected_values[0] if len(selected_values) == 1 else None
    selection_conflicts = selected_values if len(selected_values) > 1 else []
    confirm_values = {frame["confirm_available"] for frame in frames if isinstance(frame.get("confirm_available"), bool)}
    confirm = next(iter(confirm_values)) if len(confirm_values) == 1 else None
    confirm_conflict = len(confirm_values) > 1
    points = {frame["displayed_skill_points"] for frame in draft_frames if type(frame.get("displayed_skill_points")) is int}
    point_value = next(iter(points)) if len(points) == 1 else None
    point_conflicts = sorted(points) if len(points) > 1 else []
    status_values = {
        _text(frame.get("selection_status")).casefold()
        for frame in draft_frames if isinstance(frame.get("selection_status"), str)
        and _text(frame.get("selection_status"))
    }
    status = next(iter(status_values)) if len(status_values) == 1 else None
    if selected and confirm is True and not confirm_conflict:
        status = status or "not_yet_confirmed"
    payload: dict[str, Any] = {"kind": "skill"}
    if obtained:
        payload["visible_precommit_obtained_names"] = obtained
    if prices:
        payload["visible_card_prices"] = prices
    if name_variants:
        # OCR spelling alternatives are retained beside the selected repeated
        # spelling.  They never become an additional card or price claim.
        payload["visible_card_name_variants"] = name_variants
    if price_conflicts:
        payload["visible_card_price_conflicts"] = price_conflicts
    if offer_frames:
        explicit_statuses = {
            _text(frame.get("facts", {}).get("price_status")).casefold()
            for frame in offer_frames
            if isinstance(frame.get("facts"), Mapping)
            and _text(frame.get("facts", {}).get("price_status")) in _PRICE_STATUSES
        }
        if len(explicit_statuses) == 1:
            payload["price_status"] = next(iter(explicit_statuses))
        elif len(explicit_statuses) > 1:
            payload["price_status"] = "unknown"
            payload["price_status_conflict"] = sorted(explicit_statuses)
        else:
            payload["price_status"] = "partially_visible" if prices else "unknown"
    if selected is not None:
        payload["selected_draft_names"] = selected
        markers = [dict(proof, source_timestamp_ms=frame["source_timestamp_ms"],
                        evidence=deepcopy(frame["evidence"]))
                   for frame in draft_frames
                   for proof in frame.get("payload", {}).get("selected_draft_card_proof", [])
                   if isinstance(proof, Mapping) and proof.get("name") in selected]
        if markers:
            payload["selected_draft_card_proof"] = markers
    if selection_conflicts:
        payload["selected_draft_name_conflicts"] = selection_conflicts
        payload["selection_status"] = "unknown"
    elif status is not None:
        payload["selection_status"] = status
    if not confirm_conflict and confirm is not None:
        payload["confirm_available"] = confirm
    if confirm_conflict:
        payload["confirm_conflict"] = True
    if selected is not None and point_value is not None:
        payload["skill_points_after"] = point_value
    if point_conflicts:
        payload["skill_points_conflict"] = point_conflicts
    evidence = []
    for frame in frames:
        for path in frame.get("evidence", []):
            if path not in evidence:
                evidence.append(path)
    start = frames[0]["source_timestamp_ms"]
    end = frames[-1]["source_timestamp_ms"]
    source_observations = []
    for frame in frames:
        source_observations.append({
            "source_timestamp_ms": frame["source_timestamp_ms"],
            "evidence": deepcopy(frame.get("evidence", [])),
            "source_ref": f"/gameplay_tracking/readings/{frame['index']}",
            "payload": deepcopy(frame.get("payload", {})),
            "source_proof": deepcopy(frame.get("source_proof", {})),
        })
    observation_id = f"/gameplay_tracking/skill_menu_observations/{episode_index}"
    result = {
        "id": observation_id,
        "source_ref": observation_id,
        "category": "purchase",
        "phase": "preview",
        "status": "observed",
        "start_ms": start,
        "end_ms": end,
        "payload": payload,
        "evidence": evidence,
        "source_observations": source_observations,
        "uncertain": bool(
            post_commit or price_conflicts or selection_conflicts or confirm_conflict or point_conflicts
        ),
        "preview_only": True,
        "committed": False,
        "receipt_required": True,
        "menu_episode": {
            "first_seen_ms": start,
            "last_seen_ms": end,
            "frame_count": len(frames),
            "draft_start_ms": draft_start,
            "post_commit": post_commit,
            "status_observations": status_observations,
            "name_variants": name_variants,
        },
    }
    # Conflicts concern the named payload fields. Keep whole-observation
    # uncertainty for consumers, while allowing an evaluator to distinguish
    # a known draft from an unrelated unreadable or disputed card price.
    fields = ['/visible_card_prices/' + name.replace('~', '~0').replace('/', '~1')
              for name in price_conflicts]
    if selection_conflicts:
        fields.extend(['/selected_draft_names', '/selection_status'])
    if confirm_conflict:
        fields.append('/confirm_available')
    if point_conflicts:
        fields.append('/skill_points_after')
    if fields and not post_commit:
        result['uncertainty_fields'] = sorted(set(fields))
    return result


def build_skill_menu_observations(
    readings: Iterable[Mapping[str, Any]], maximum_gap_ms: int = 1000,
    *, source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Group source rows into preview menu episodes.

    A non-menu row, a receipt/confirmation row, a changed explicit episode
    identity, or a gap larger than ``maximum_gap_ms`` closes the current menu
    episode.  No timestamp, recording, expected amount, or balance is used to
    select a card.
    """

    if type(maximum_gap_ms) is not int or maximum_gap_ms < 0:
        raise ValueError("maximum_gap_ms must be a non-negative integer")
    rows = list(readings) if isinstance(readings, Iterable) else []
    ordered = sorted(enumerate(rows), key=lambda pair: pair[1].get("source_timestamp_ms", -1) if isinstance(pair[1], Mapping) else -1)
    observations: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    active_key: Any = None
    post_commit = False
    rejected: list[dict[str, Any]] = []

    def finish() -> None:
        nonlocal active, active_key, post_commit
        if active:
            draft_index = next((i for i, frame in enumerate(active)
                                if frame.get('selected_names')), None)
            groups = ([active[:draft_index], active[draft_index:]]
                      if draft_index is not None and draft_index > 0 else [active])
            for group in groups:
                observations.append(_episode_payload(group, post_commit, len(observations)))
        active = []
        active_key = None
        post_commit = False

    for index, row in ordered:
        if not isinstance(row, Mapping):
            finish()
            continue
        raw_screen = _text(row.get("screen")).casefold()
        if raw_screen in RECEIPT_SCREENS:
            finish()
            post_commit = True
            continue
        frame = _row_frame(row, index, source_root)
        if frame is None:
            if raw_screen or row.get("header"):
                finish()
            continue
        key = frame.get("episode_key")
        if active:
            gap = frame["source_timestamp_ms"] - active[-1]["source_timestamp_ms"]
            if gap < 0 or gap > maximum_gap_ms or (key is not None and active_key is not None and key != active_key):
                finish()
        if not active:
            active_key = key
        active.append(frame)
        if row.get("screen") in RECEIPT_SCREENS:
            post_commit = True
    finish()
    return {
        "schema_version": SCHEMA,
        "observations": observations,
        "episodes": deepcopy(observations),
        "preview_only": True,
        "committed": False,
        "receipt_required": True,
        "rejected": rejected,
    }


def build_observations(
    readings: Iterable[Mapping[str, Any]], maximum_gap_ms: int = 1000,
    *, source_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Compatibility wrapper returning only the grouped observation list."""

    return build_skill_menu_observations(
        readings, maximum_gap_ms, source_root=source_root,
    )["observations"]


def parse_skill_menu(raw: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Alias emphasizing that parsing consumes source geometry only."""

    return adapt_skill_menu_frame(raw, **kwargs)


def read_skill_menu(raw: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Normal-producer alias for :func:`adapt_skill_menu_frame`."""

    return adapt_skill_menu_frame(raw, **kwargs)


def skill_menu_frame_observations(raw: Mapping[str, Any], **kwargs: Any) -> list[dict[str, Any]]:
    """Return the one-frame preview observation list."""

    return adapt_skill_menu_frame(raw, **kwargs)["observations"]


__all__ = [
    "SCHEMA", "GAMEPLAY_SIZE", "MENU_SCREENS", "SkillMenuSourceError",
    "detect_skill_selection_marker",
    "adapt_skill_menu_frame", "parse_skill_menu", "read_skill_menu",
    "skill_menu_frame_observations", "build_skill_menu_observations",
    "build_observations",
]
