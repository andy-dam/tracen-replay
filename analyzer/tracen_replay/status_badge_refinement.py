"""Source-bound rereads for low-confidence gameplay status badges.

The ordinary detector can recognize a mood or hype badge while assigning a
confidence below the status reader's fixed 97 percent threshold.  This module
re-reads only the already detected badge crop from the same gameplay pane.  A
sidecar can promote the original line only when the crop reads the unchanged
literal text in at least two of three fixed contrast views.  It never chooses
the value from a neighboring frame, an expected report value, or an energy
balance.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path

from PIL import Image, ImageOps

from .layout import ORIGIN_X, inside_pane, pane_size, place
from .refine_contrast import fingerprint


SCHEMA = "tracen-replay/status-badge-refinement-v1"
STAGE = "status_badge_refinement"
VERSION = 1
REFINEMENT_DIR = "status-badge-refinement"
MIN_CONFIDENCE = 97.0
ORIGINAL_MIN_CONFIDENCE = 80.0
MIN_STRONG_VIEWS = 2
CROP_PADDING = 4
VIEW_MODES = ("rgb", "grayscale", "blue")

# These are full-screen coordinates used by the gameplay OCR cache, on the PC
# pane.  The gameplay pane itself starts at x=148; crop boxes below are
# converted to pane coordinates before reading pixels.  The energy row and
# mood badge are pinned to the top centre, the hype badge to the top left.
ENERGY_BOX = (375, 115, 455, 165)
MOOD_BOX = (705, 112, 825, 165)
HYPE_HEADER_BOX = (150, 150, 300, 188)
HYPE_VALUE_BOX = (150, 185, 295, 224)
MOOD_VALUES = frozenset(("awful", "bad", "normal", "good", "great"))

_SHA = re.compile(r"^[0-9a-f]{64}$")
_HYPE_COMBINED = re.compile(r"([A-Za-z]+(?: [A-Za-z]+)?)\s+Hype$", re.IGNORECASE)


def file_fingerprint(path: str | Path) -> str:
    """Hash evidence file bytes without relying on its image encoding."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: str | Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ValueError(f"Refusing to overwrite status badge sidecar: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def _confidence(value) -> float | None:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        return None
    value = float(value)
    return value if 0 <= value <= 100 else None


def _hash(value) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _region(line, box) -> bool:
    if not isinstance(line, dict):
        return False
    points = line.get("box")
    if not isinstance(points, (list, tuple)) or len(points) != 4:
        return False
    try:
        left, top, right, bottom = (float(item) for item in points)
    except (TypeError, ValueError):
        return False
    if not left < right or not top < bottom:
        return False
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    return box[0] <= center_x <= box[2] and box[1] <= center_y <= box[3]


def _eligible_original(line) -> bool:
    confidence = _confidence(line.get("confidence")) if isinstance(line, dict) else None
    return confidence is not None and ORIGINAL_MIN_CONFIDENCE <= confidence < MIN_CONFIDENCE


def _text(line) -> str:
    return line.get("text", "").strip() if isinstance(line, dict) else ""


def _candidate_records(raw: dict) -> list[dict]:
    """Find low-confidence badge lines with the anchors the normal reader uses."""

    lines = raw.get("lines") if isinstance(raw, dict) else None
    if not isinstance(lines, list):
        return []

    energy = [
        (index, line)
        for index, line in enumerate(lines)
        if _region(line, place(ENERGY_BOX, "tc"))
        and _confidence(line.get("confidence")) is not None
        and float(line["confidence"]) >= 95
        and _text(line).casefold() == "energy"
    ]
    moods = [
        (index, line)
        for index, line in enumerate(lines)
        if _region(line, place(MOOD_BOX, "tc"))
        and _eligible_original(line)
        and _text(line).casefold() in MOOD_VALUES
    ]

    candidates = []
    if len(energy) == 1 and len(moods) == 1:
        energy_index, energy_line = energy[0]
        mood_index, mood_line = moods[0]
        candidates.append(
            dict(
                kind="mood_status",
                line_index=mood_index,
                line=copy.deepcopy(mood_line),
                anchor_line_index=energy_index,
                anchor=copy.deepcopy(energy_line),
                anchor_kind="energy",
            )
        )

    headers = [
        (index, line)
        for index, line in enumerate(lines)
        if _region(line, place(HYPE_HEADER_BOX, "tl"))
        and _confidence(line.get("confidence")) is not None
        and float(line["confidence"]) >= 97
        and _text(line).casefold() == "hype level"
    ]
    hype_values = []
    value_box = place(HYPE_VALUE_BOX, "tl")
    for index, line in enumerate(lines):
        if not _region(line, value_box) or not _eligible_original(line):
            continue
        text = _text(line)
        combined = _HYPE_COMBINED.fullmatch(text)
        if (combined and combined[1].strip().casefold() != "hype") or (
            re.fullmatch(r"[A-Za-z]+", text) and text.casefold() != "hype"
        ):
            hype_values.append((index, line))
    if len(headers) == 1 and len(hype_values) == 1:
        header_index, header_line = headers[0]
        hype_index, hype_line = hype_values[0]
        candidates.append(
            dict(
                kind="hype_status",
                line_index=hype_index,
                line=copy.deepcopy(hype_line),
                anchor_line_index=header_index,
                anchor=copy.deepcopy(header_line),
                anchor_kind="hype level",
            )
        )
    return candidates


def _pane_box(line: dict, padding: int = CROP_PADDING) -> list[int]:
    """Convert a full-screen OCR box into a bounded gameplay-pane crop."""

    if not isinstance(padding, int) or padding < 0:
        raise ValueError("Status badge crop padding is invalid.")
    try:
        left, top, right, bottom = (int(value) for value in line["box"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Status badge source line box is invalid.") from None
    if not inside_pane((left, top, right, bottom)):
        raise ValueError("Status badge source line is outside the gameplay pane.")
    width, height = pane_size()
    crop = [
        max(0, left - ORIGIN_X - padding),
        max(0, top - padding),
        min(width, right - ORIGIN_X + padding),
        min(height, bottom + padding),
    ]
    if not (crop[0] < crop[2] and crop[1] < crop[3]):
        raise ValueError("Status badge crop is empty.")
    return crop


def _view(crop, mode: str):
    if mode == "rgb":
        return crop.convert("RGB")
    if mode == "grayscale":
        return ImageOps.grayscale(crop).convert("RGB")
    if mode == "blue":
        channel = crop.getchannel("B")
        return Image.merge("RGB", (channel, channel, channel))
    raise ValueError("Unknown status badge contrast view.")


def _view_record(crop, mode: str, text: str, confidence: float) -> dict:
    transformed = _view(crop, mode)
    return dict(
        mode=mode,
        crop_box=None,
        crop_pixel_sha256=hashlib.sha256(crop.tobytes()).hexdigest(),
        view_pixel_sha256=hashlib.sha256(transformed.tobytes()).hexdigest(),
        text=str(text),
        confidence=round(float(confidence), 4),
    )


def _ocr_candidates(raw: dict, pane, candidates: list[dict], reader) -> list[dict]:
    """Read fixed crop views and retain only unchanged high-confidence text."""

    requests = []
    crops = {}
    for candidate in candidates:
        crop_box = _pane_box(candidate["line"])
        with_crop = pane.crop(tuple(crop_box)).convert("RGB")
        crops[candidate["line_index"]] = (crop_box, with_crop)
        for mode in VIEW_MODES:
            requests.append((candidate["line_index"], mode, _view(with_crop, mode)))
    arrays = [reader.np.asarray(view)[:, :, ::-1] for _, _, view in requests]
    result = reader.engine.text_rec(reader.TextRecInput(img=arrays))
    observations = []
    by_index = {candidate["line_index"]: candidate for candidate in candidates}
    grouped = {candidate["line_index"]: [] for candidate in candidates}
    for (line_index, mode, _view_image), text, score in zip(
        requests, getattr(result, "txts", []) or [], getattr(result, "scores", []) or []
    ):
        confidence = _confidence(float(score) * 100)
        if confidence is None:
            confidence = 0.0
        crop_box, crop = crops[line_index]
        view_record = _view_record(crop, mode, text, confidence)
        view_record["crop_box"] = list(crop_box)
        grouped[line_index].append(view_record)

    for line_index, views in grouped.items():
        candidate = by_index[line_index]
        text = _text(candidate["line"])
        strong = [
            view
            for view in views
            if view["text"].strip() == text and view["confidence"] >= MIN_CONFIDENCE
        ]
        accepted = len(views) == len(VIEW_MODES) and len(strong) >= MIN_STRONG_VIEWS and all(
            view["text"].strip() == text for view in views
        )
        if not accepted:
            continue
        observations.append(
            dict(
                kind=candidate["kind"],
                line_index=line_index,
                line=copy.deepcopy(candidate["line"]),
                anchor_line_index=candidate["anchor_line_index"],
                anchor=copy.deepcopy(candidate["anchor"]),
                anchor_kind=candidate["anchor_kind"],
                views=views,
                accepted=True,
                accepted_confidence=round(min(view["confidence"] for view in strong), 4),
            )
        )
    return observations


def _required_provenance(raw: dict, refinement: dict, original: dict, evidence_path):
    if not isinstance(refinement, dict) or refinement.get("schema_version") != SCHEMA:
        raise ValueError("Status badge refinement schema mismatch.")
    if refinement.get("version") != VERSION or refinement.get("stage") != STAGE:
        raise ValueError("Status badge refinement version mismatch.")
    if not isinstance(original, dict) or not isinstance(original.get("lines"), list):
        raise ValueError("Status badge source observation has no immutable lines.")
    if not isinstance(raw, dict) or not isinstance(raw.get("lines"), list):
        raise ValueError("Status badge target observation has no lines.")
    if refinement.get("raw_sha256") != fingerprint(original):
        raise ValueError("Status badge refinement source JSON changed.")
    required = (
        "source_frame_id",
        "source_timestamp_ms",
        "evidence",
        "evidence_sha256",
        "source_frame_evidence",
        "gameplay_sha256",
        "source_frame_sha256",
        "source_model_sha256",
        "source_engine_fingerprint",
        "refinement_model_sha256",
        "refinement_engine_fingerprint",
        "independent_observations",
    )
    for key in required:
        if key not in refinement:
            raise ValueError(f"Status badge refinement missing {key} provenance.")
    if (not isinstance(refinement["source_frame_id"], str)
            or re.fullmatch(r"[A-Za-z0-9_-]+", refinement["source_frame_id"]) is None):
        raise ValueError("Status badge source frame identity is missing.")
    if refinement["source_frame_evidence"] is not None and (
        not isinstance(refinement["source_frame_evidence"], str)
        or not refinement["source_frame_evidence"]
    ):
        raise ValueError("Status badge source-frame evidence path is invalid.")
    if refinement["source_timestamp_ms"] != original.get("source_timestamp_ms"):
        raise ValueError("Status badge refinement timestamp mismatch.")
    if refinement["evidence"] != original.get("evidence"):
        raise ValueError("Status badge refinement gameplay path mismatch.")
    if refinement["gameplay_sha256"] != original.get("gameplay_sha256"):
        raise ValueError("Status badge refinement gameplay hash mismatch.")
    if refinement["source_frame_sha256"] != original.get("source_frame_sha256"):
        raise ValueError("Status badge refinement source-frame hash mismatch.")
    if refinement["source_model_sha256"] != original.get("model_sha256"):
        raise ValueError("Status badge refinement source model mismatch.")
    if refinement["source_engine_fingerprint"] != original.get("engine_fingerprint"):
        raise ValueError("Status badge refinement source engine mismatch.")
    if refinement["independent_observations"] is not False:
        raise ValueError("Status badge crop views cannot claim independent observations.")
    if not isinstance(refinement["source_model_sha256"], dict) or not refinement["source_model_sha256"]:
        raise ValueError("Status badge source model identity is missing.")
    if not isinstance(refinement["refinement_model_sha256"], dict) or not refinement["refinement_model_sha256"]:
        raise ValueError("Status badge refinement model identity is missing.")
    if any(not _hash(value) for value in refinement["source_model_sha256"].values()):
        raise ValueError("Status badge source model hash is invalid.")
    if any(not _hash(value) for value in refinement["refinement_model_sha256"].values()):
        raise ValueError("Status badge refinement model hash is invalid.")
    if not _hash(refinement["source_engine_fingerprint"]):
        raise ValueError("Status badge source engine fingerprint is invalid.")
    if not _hash(refinement["refinement_engine_fingerprint"]):
        raise ValueError("Status badge refinement engine fingerprint is invalid.")
    for name in ("evidence_sha256", "gameplay_sha256", "source_frame_sha256", "raw_sha256"):
        if not _hash(refinement[name]):
            raise ValueError(f"Status badge {name} is invalid.")
    expected_policy = dict(
        padding=CROP_PADDING,
        views=list(VIEW_MODES),
        minimum_strong_views=MIN_STRONG_VIEWS,
        minimum_confidence=MIN_CONFIDENCE,
    )
    if refinement.get("crop_policy") != expected_policy:
        raise ValueError("Status badge crop policy changed.")
    if evidence_path is None:
        raise ValueError("Status badge gameplay evidence is required.")
    evidence_path = Path(evidence_path)
    if file_fingerprint(evidence_path) != refinement["evidence_sha256"]:
        raise ValueError("Status badge gameplay evidence changed.")
    with Image.open(evidence_path) as image:
        pane = image.convert("RGB")
    if pane.size != pane_size() or hashlib.sha256(pane.tobytes()).hexdigest() != refinement["gameplay_sha256"]:
        raise ValueError("Status badge gameplay pixels changed.")
    return pane


def _validate_observation(original: dict, observation: dict, pane) -> tuple[int, float]:
    if not isinstance(observation, dict):
        raise ValueError("Status badge observation is invalid.")
    kind = observation.get("kind")
    candidates = {
        (candidate["kind"], candidate["line_index"]): candidate
        for candidate in _candidate_records(original)
    }
    key = (kind, observation.get("line_index"))
    candidate = candidates.get(key)
    if candidate is None:
        raise ValueError("Status badge source line is no longer an eligible candidate.")
    if observation.get("line") != candidate["line"]:
        raise ValueError("Status badge source line changed.")
    if observation.get("anchor_line_index") != candidate["anchor_line_index"]:
        raise ValueError("Status badge anchor line changed.")
    if observation.get("anchor") != candidate["anchor"] or observation.get("anchor_kind") != candidate["anchor_kind"]:
        raise ValueError("Status badge anchor evidence changed.")
    views = observation.get("views")
    if not isinstance(views, list) or len(views) != len(VIEW_MODES):
        raise ValueError("Status badge refinement requires all fixed crop views.")
    if {view.get("mode") for view in views if isinstance(view, dict)} != set(VIEW_MODES):
        raise ValueError("Status badge refinement view set changed.")
    crop_box = _pane_box(candidate["line"])
    crop = pane.crop(tuple(crop_box)).convert("RGB")
    strong = []
    text = _text(candidate["line"])
    for view in views:
        if not isinstance(view, dict) or view.get("crop_box") != crop_box:
            raise ValueError("Status badge crop geometry changed.")
        transformed = _view(crop, view["mode"])
        if view.get("crop_pixel_sha256") != hashlib.sha256(crop.tobytes()).hexdigest():
            raise ValueError("Status badge crop pixels changed.")
        if view.get("view_pixel_sha256") != hashlib.sha256(transformed.tobytes()).hexdigest():
            raise ValueError("Status badge contrast view pixels changed.")
        confidence = _confidence(view.get("confidence"))
        if confidence is None or not isinstance(view.get("text"), str):
            raise ValueError("Status badge crop reading is invalid.")
        if view["text"].strip() != text:
            raise ValueError("Status badge crop text disagrees with the source line.")
        if confidence >= MIN_CONFIDENCE:
            strong.append(confidence)
    if len(strong) < MIN_STRONG_VIEWS or observation.get("accepted") is not True:
        raise ValueError("Status badge refinement lacks strong unchanged-text views.")
    accepted = round(min(strong), 4)
    if observation.get("accepted_confidence") != accepted:
        raise ValueError("Status badge accepted confidence changed.")
    return observation["line_index"], accepted


def apply(raw: dict, refinement: dict, *, evidence_path: str | Path | None = None,
          original: dict | None = None, source_frame_id: str | None = None) -> dict:
    """Apply a validated sidecar while retaining the original literal text."""

    original = raw if original is None else original
    if source_frame_id is not None and refinement.get("source_frame_id") != source_frame_id:
        raise ValueError("Status badge source frame identity changed.")
    pane = _required_provenance(raw, refinement, original, evidence_path)
    observations = refinement.get("observations")
    if not isinstance(observations, list) or not observations:
        raise ValueError("Status badge refinement has no accepted observations.")
    result = copy.deepcopy(raw)
    applied = []
    seen = set()
    for observation in observations:
        index, confidence = _validate_observation(original, observation, pane)
        if index in seen or result["lines"][index] != original["lines"][index]:
            raise ValueError("Status badge source line was already refined or duplicated.")
        seen.add(index)
        result["lines"][index].update(
            confidence=confidence,
            original_confidence=original["lines"][index].get("confidence"),
            status_badge_refined=True,
        )
        applied.append(dict(kind=observation["kind"], line_index=index, confidence=confidence))
    metadata = copy.deepcopy(refinement)
    metadata["applied_observations"] = applied
    result["status_badge_refinement"] = metadata
    return result


def load(raw: dict, path: str | Path, *, evidence_path: str | Path | None = None,
         original: dict | None = None, source_frame_id: str | None = None) -> dict:
    """Load and apply a sidecar, returning ``raw`` when it is absent."""

    path = Path(path)
    if not path.exists():
        return raw
    try:
        refinement = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid status badge refinement artifact.") from exc
    return apply(raw, refinement, evidence_path=evidence_path, original=original,
                 source_frame_id=source_frame_id)


def prepare(raw: dict, pane, reader) -> dict:
    """Run only fixed status badge crops and return cache-ready observations."""

    if pane.size != pane_size():
        raise ValueError("Status badge preparation expects the gameplay pane.")
    candidates = _candidate_records(raw)
    observations = _ocr_candidates(raw, pane.convert("RGB"), candidates, reader) if candidates else []
    return dict(candidate_count=len(candidates), accepted_count=len(observations), observations=observations)


def _selected_frames(root: Path, start_ms: int, end_ms: int, frame_ids) -> list[dict]:
    if type(start_ms) is not int or type(end_ms) is not int or end_ms <= start_ms:
        raise ValueError("Status badge generation requires a non-empty time range.")
    wanted = set(frame_ids or ())
    report = root / "report.json"
    selected = []
    if report.is_file():
        data = json.loads(report.read_text(encoding="utf-8"))
        frames = data.get("frames", [])
        for frame in frames:
            frame_id, timestamp = frame.get("id"), frame.get("source_timestamp_ms")
            if (not isinstance(frame_id, str) or type(timestamp) is not int
                    or not start_ms <= timestamp < end_ms
                    or (wanted and frame_id not in wanted)):
                continue
            selected.append(dict(frame_id=frame_id, timestamp=timestamp,
                                 raw_path=root / "neural" / f"{frame_id}.json",
                                 source_frame_evidence=frame.get("evidence")))
    else:
        frame_manifest = root / "frames.json"
        if not frame_manifest.is_file():
            # ``capture`` writes a full envelope before the final report is
            # published.  Its frame evidence is needed to bind a new sidecar
            # to the original source image.
            frame_manifest = root / "capture.json"
        manifest_frames = {}
        if frame_manifest.is_file():
            data = json.loads(frame_manifest.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data = data.get("frames")
            if isinstance(data, list):
                manifest_frames = {
                    item.get("id"): item.get("evidence")
                    for item in data
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                }
        for path in sorted((root / "neural").glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            timestamp = raw.get("source_timestamp_ms")
            if (type(timestamp) is int and start_ms <= timestamp < end_ms
                    and (not wanted or path.stem in wanted)):
                selected.append(dict(frame_id=path.stem, timestamp=timestamp,
                                     raw_path=path,
                                     source_frame_evidence=raw.get("source_frame_evidence")
                                     or manifest_frames.get(path.stem)))
    selected.sort(key=lambda item: (item["timestamp"], item["frame_id"]))
    if wanted:
        found = {item["frame_id"] for item in selected}
        missing = sorted(wanted - found)
        if missing:
            raise ValueError("Requested frame is outside the bounded selection: " + ", ".join(missing))
    return selected


def generate(root: str | Path, *, start_ms: int, end_ms: int, frame_ids=None,
             reader=None, model_dir: str | Path = ".local/models/rapidocr",
             output_dir: str | Path | None = None) -> dict:
    """Write source-bound sidecars for an explicitly bounded frame selection."""

    root = Path(root).resolve()
    selected = _selected_frames(root, start_ms, end_ms, frame_ids)
    destination = root / REFINEMENT_DIR if output_dir is None else Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if reader is None:
        from .vision import NeuralReader
        reader = NeuralReader(model_dir)
    summary = dict(stage=STAGE, start_ms=start_ms, end_ms=end_ms,
                   selected_frames=len(selected), candidates=0, accepted=0,
                   written=0, skipped_existing=0, unresolved=0,
                   unresolved_frames=[])
    for selected_frame in selected:
        raw_path = selected_frame["raw_path"]
        if not raw_path.is_file():
            raise ValueError(f"Missing neural observation: {selected_frame['frame_id']}")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        if raw.get("source_timestamp_ms") != selected_frame["timestamp"]:
            raise ValueError("Status badge source timestamp mismatch.")
        evidence_path = root / raw.get("evidence", "")
        if not evidence_path.is_file():
            raise ValueError("Status badge gameplay evidence is missing.")
        with Image.open(evidence_path) as image:
            pane = image.convert("RGB")
        if pane.size != pane_size() or hashlib.sha256(pane.tobytes()).hexdigest() != raw.get("gameplay_sha256"):
            raise ValueError("Status badge gameplay pixels changed.")
        prepared = prepare(raw, pane, reader)
        summary["candidates"] += prepared["candidate_count"]
        summary["accepted"] += prepared["accepted_count"]
        unresolved = prepared["candidate_count"] - prepared["accepted_count"]
        summary["unresolved"] += unresolved
        if unresolved:
            summary["unresolved_frames"].append(
                dict(frame_id=selected_frame["frame_id"],
                     source_timestamp_ms=selected_frame["timestamp"],
                     candidate_count=prepared["candidate_count"],
                     accepted_count=prepared["accepted_count"],
                     reason="status_badge_consensus_not_accepted")
            )
        if not prepared["observations"]:
            continue
        target = destination / raw_path.name
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if existing.get("raw_sha256") != fingerprint(raw):
                raise ValueError(f"Existing status badge sidecar source changed: {target}")
            summary["skipped_existing"] += 1
            continue
        source_frame_evidence = selected_frame.get("source_frame_evidence")
        source_frame_path = root / source_frame_evidence if isinstance(source_frame_evidence, str) else None
        source_frame_hash = raw.get("source_frame_sha256")
        if source_frame_evidence is not None:
            if source_frame_path is None or not source_frame_path.is_file():
                raise ValueError("Status badge source-frame evidence is missing.")
            if file_fingerprint(source_frame_path) != source_frame_hash:
                raise ValueError("Status badge source frame changed.")
        refinement = dict(
            schema_version=SCHEMA,
            version=VERSION,
            stage=STAGE,
            source_frame_id=selected_frame["frame_id"],
            source_timestamp_ms=raw["source_timestamp_ms"],
            evidence=raw["evidence"],
            evidence_sha256=file_fingerprint(evidence_path),
            gameplay_sha256=raw["gameplay_sha256"],
            source_frame_evidence=source_frame_evidence,
            source_frame_sha256=source_frame_hash,
            raw_sha256=fingerprint(raw),
            source_model_sha256=raw.get("model_sha256"),
            source_engine_fingerprint=raw.get("engine_fingerprint"),
            refinement_model_sha256=getattr(reader, "models", {}),
            refinement_engine_fingerprint=getattr(reader, "fingerprint", None),
            independent_observations=False,
            crop_policy=dict(padding=CROP_PADDING, views=list(VIEW_MODES),
                             minimum_strong_views=MIN_STRONG_VIEWS,
                             minimum_confidence=MIN_CONFIDENCE),
            observations=prepared["observations"],
        )
        apply(raw, refinement, evidence_path=evidence_path, original=raw)
        _write_json(target, refinement)
        summary["written"] += 1
    return summary
