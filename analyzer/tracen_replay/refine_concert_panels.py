"""Generate provenance-bound refinements for unreadable Concert Info slots.

The generator works only from cached base OCR and decoded frame manifests. It
does not inspect the side log, infer a game value, or turn an ambiguous OCR
letter into a digit. A candidate is eligible only when its full cached panel
shows one unreadable support level with no visible current-to-planned change.
Nearby base frames are re-read from a fixed source crop and passed through the
existing refinement guards before an artifact is written.
"""

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps

from .concert_panel_refinement import (
    BONUS_HEADER_BOX,
    DEFAULT_MAX_BASE_DISTANCE_MS,
    PANEL_CONTEXT_BOX,
    PANEL_HEADER_BOX,
    SLOT_BAND,
    SLOT_BOX,
    apply,
    build,
    is_concert_info_panel,
    normalize_level,
)
from .refine_contrast import fingerprint
from .vision import NeuralReader


TIGHT_CROP_BOX = (695, 378, 790, 425)
RESIZE_SCALE = 4
RESAMPLE_NAME = "LANCZOS"
CONTRAST_FACTOR = 2.0
MIN_CONFIDENCE = 90.0
OCR_VARIANTS = ("gray", "contrast")
OUTPUT_DIRNAME = "concert-panel-refinement"
OBSERVATION_DIRNAME = "observations"
REJECTED_DIRNAME = "rejected"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _center(box):
    return ((float(box[0]) + float(box[2])) / 2, (float(box[1]) + float(box[3])) / 2)


def _within(line, box):
    try:
        x, y = _center(line["box"])
    except (KeyError, TypeError, ValueError):
        return False
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _lines(raw):
    return [line for line in raw.get("lines", []) if isinstance(line, dict)]


def _extract_anchor_metadata(raw):
    anchors = (
        ("panel_header", "Concert Info", PANEL_HEADER_BOX),
        ("bonus_header", "Concert Bonus Changes", BONUS_HEADER_BOX),
    )
    result = []
    for name, text, box in anchors:
        candidates = [
            line
            for line in _lines(raw)
            if line.get("text", "").strip().lower() == text.lower() and _within(line, box)
        ]
        if not candidates:
            return None
        line = max(candidates, key=lambda item: float(item.get("confidence", 0)))
        result.append(
            {
                "name": name,
                "text": line.get("text", ""),
                "confidence": line.get("confidence", 0),
                "box": list(line.get("box", [])),
            }
        )
    return result


def _extract_panel_context(raw):
    candidates = [
        line
        for line in _lines(raw)
        if line.get("confidence", 0) >= MIN_CONFIDENCE
        and _within(line, PANEL_CONTEXT_BOX)
        and line.get("text", "").strip().lower() != "concert info"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("confidence", 0))).get("text", "").strip()


def _looks_like_level(text):
    return re.match(r"^Lv[lI1]\s*", str(text or "").strip(), re.IGNORECASE) is not None


def _slot_state(raw):
    """Classify the full panel before spending OCR work on a candidate."""

    candidates = []
    for line in _lines(raw):
        if not _within(line, SLOT_BAND) or not _looks_like_level(line.get("text", "")):
            continue
        text = str(line.get("text", "")).strip()
        compact = re.sub(r"\s+", "", text)
        if re.search(r"[>▶→]", text) or len(re.findall(r"Lvl", compact, re.IGNORECASE)) != 1:
            return "transition"
        if normalize_level(text) is None and not re.fullmatch(r"Lvl\s*[Oo]", text, re.IGNORECASE):
            return "unknown"
        candidates.append(line)
    if len(candidates) != 1:
        return "unknown"
    line = candidates[0]
    return "readable" if normalize_level(line.get("text")) is not None and line.get("confidence", 0) >= MIN_CONFIDENCE else "unreadable"


def _part_manifests(root):
    """Load part manifests and map every decoded frame to its manifest."""

    by_id = {}
    for path in sorted(root.glob("part-*/frames.json")):
        try:
            rows = _load_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("id") and row["id"] not in by_id:
                by_id[row["id"]] = (row, path)
    return by_id


_CAPTURE_FIELDS = ("id", "source_timestamp_ms", "source_pts", "time_base", "evidence")


def _capture_rows(root):
    """Load a usable capture and retain its exact frame index."""

    capture_path = root / "capture.json"
    if not capture_path.is_file():
        return None, None, "capture_missing"
    try:
        capture = _load_json(capture_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None, "capture_malformed"
    if (
        not isinstance(capture, dict)
        or not isinstance(capture.get("source"), dict)
        or not capture["source"].get("sha256")
        or not isinstance(capture.get("frames"), list)
    ):
        return None, None, "capture_malformed"
    rows = capture.get("frames", []) if isinstance(capture, dict) else []
    if any(not isinstance(row, dict) or not row.get("id") for row in rows):
        return None, None, "capture_malformed"
    indexed = {row.get("id"): row for row in rows if isinstance(row, dict) and row.get("id")}
    if len(indexed) != len(rows):
        return None, None, "capture_malformed"
    return capture, indexed, None


def _capture_match(frame_id, row, capture_rows):
    if capture_rows is None:
        return "capture_unusable"
    capture_row = capture_rows.get(frame_id)
    if capture_row is None:
        return "capture_row_missing"
    if any(
        capture_row.get(key) != (frame_id if key == "id" else row.get(key))
        for key in _CAPTURE_FIELDS
    ):
        return "capture_row_mismatch"
    return None


def _raw_index(root):
    result = {}
    for path in sorted((root / "neural").glob("*.json")):
        try:
            raw = _load_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        frame_id = path.stem
        if frame_id not in result:
            result[frame_id] = (raw, path)
    return result


def _source_identity(root, raw, row):
    try:
        root = Path(root).resolve()
        source_path = (root / row["evidence"]).resolve()
        if not source_path.is_relative_to(root):
            return None
        timestamp = int(raw["source_timestamp_ms"])
        if timestamp != row["source_timestamp_ms"] or raw.get("source_frame_sha256") != _sha256(source_path):
            return None
        return source_path
    except (KeyError, OSError, TypeError, ValueError):
        return None


def _same_context_panel(raw, base_context):
    return (
        is_concert_info_panel(raw)
        and _extract_panel_context(raw) == base_context
        and _slot_state(raw) in ("unreadable", "readable")
    )


def _nearby_frames(base_raw, by_id, raw_by_id, capture_rows):
    """Select same-context base frames within the frozen guard distance."""

    base_time = base_raw.get("source_timestamp_ms")
    base_context = _extract_panel_context(base_raw)
    if not isinstance(base_time, int) or not base_context:
        return [], "base_context_unknown"
    rows = sorted(by_id.items(), key=lambda item: item[1][0].get("source_timestamp_ms", 0))
    selected = []
    for frame_id, (row, manifest_path) in rows:
        timestamp = row.get("source_timestamp_ms")
        if not isinstance(timestamp, int) or abs(timestamp - base_time) > DEFAULT_MAX_BASE_DISTANCE_MS:
            continue
        pair = raw_by_id.get(frame_id)
        if pair is None:
            continue
        if _capture_match(frame_id, row, capture_rows) is not None:
            continue
        raw, raw_path = pair
        if not _same_context_panel(raw, base_context):
            continue
        selected.append((frame_id, row, manifest_path, raw, raw_path))
    selected.sort(key=lambda item: item[1]["source_timestamp_ms"])
    return selected, None


def _make_crops(source_path):
    """Create the two fixed views used for every source frame.

    ``gray`` is the autocontrasted grayscale image represented as RGB for the
    recognition engine. ``contrast`` applies the fixed factor of 2.0 to that
    same image. Both views come from one source crop and retain independent
    proof files so consensus can reject a disagreement instead of selecting a
    view using an expected label.
    """

    with Image.open(source_path) as image:
        image = image.convert("RGB").crop(TIGHT_CROP_BOX)
    resized = image.resize((image.width * RESIZE_SCALE, image.height * RESIZE_SCALE), Image.Resampling.LANCZOS)
    gray = ImageOps.autocontrast(ImageOps.grayscale(resized)).convert("RGB")
    return {
        "gray": gray,
        "contrast": ImageEnhance.Contrast(gray).enhance(CONTRAST_FACTOR),
    }


def _make_crop(source_path, variant="contrast"):
    """Return one named crop view, retaining the old helper's convenience."""

    if variant not in OCR_VARIANTS:
        raise ValueError(f"Unsupported Concert Info OCR variant: {variant}")
    return _make_crops(source_path)[variant]


def _recognized_result(result):
    return {
        "text": str(result.get("text", "")),
        "confidence": round(float(result.get("confidence", 0)), 4),
    }


def _recognize_crop(reader, crop):
    """Run the reader's raw recognition interface without label hints."""

    custom = getattr(reader, "recognize_crop", None)
    if callable(custom):
        return _recognized_result(custom(crop))
    array = reader.np.array(crop.convert("RGB"))
    result = reader.engine.text_rec(reader.TextRecInput(img=[array[:, :, ::-1]]))
    return _recognition_results(result, 1)[0]


def _recognition_results(result, count):
    texts = list(getattr(result, "txts", []) or [])
    scores = list(getattr(result, "scores", []) or [])
    return [
        {
            "text": str(texts[index]) if index < len(texts) else "",
            "confidence": round(float(scores[index]) * 100, 4) if index < len(scores) else 0.0,
        }
        for index in range(count)
    ]


def _recognize_crops(reader, crops):
    """Recognize all fixed views in one deterministic request when possible."""

    custom_many = getattr(reader, "recognize_crops", None)
    if callable(custom_many):
        results = custom_many(crops)
        if len(results) != len(crops):
            raise ValueError("Custom OCR returned a different number of results than crops.")
        return [_recognized_result(result) for result in results]

    custom_one = getattr(reader, "recognize_crop", None)
    if callable(custom_one):
        return [_recognized_result(custom_one(crop)) for crop in crops]

    arrays = [reader.np.array(crop.convert("RGB"))[:, :, ::-1] for crop in crops]
    result = reader.engine.text_rec(reader.TextRecInput(img=arrays))
    return _recognition_results(result, len(crops))


def _measurement(
    root,
    output_root,
    reader,
    frame_id,
    row,
    manifest_path,
    raw,
    raw_path,
    base_context,
    *,
    variant="contrast",
    crop=None,
    recognized=None,
):
    if variant not in OCR_VARIANTS:
        raise ValueError(f"Unsupported Concert Info OCR variant: {variant}")
    source_path = root / row["evidence"]
    crop = crop if crop is not None else _make_crop(source_path, variant)
    crop_path = output_root / OBSERVATION_DIRNAME / raw_path.stem / f"{frame_id}-{variant}.png"
    crop_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(crop_path, format="PNG")
    recognized = recognized if recognized is not None else _recognize_crop(reader, crop)
    anchors = _extract_anchor_metadata(raw)
    source_manifest_hash = _sha256(manifest_path)
    source_hash = _sha256(source_path)
    return {
        "timestamp_ms": row["source_timestamp_ms"],
        "evidence": crop_path.relative_to(root).as_posix(),
        "evidence_sha256": _sha256(crop_path),
        "source_frame_evidence": source_path.relative_to(root).as_posix(),
        "source_frame_sha256": source_hash,
        "source_pts": row["source_pts"],
        "time_base": row["time_base"],
        "source_manifest_evidence": manifest_path.relative_to(root).as_posix(),
        "source_manifest_sha256": source_manifest_hash,
        "source_manifest_row_id": frame_id,
        "panel_raw_evidence": raw_path.relative_to(root).as_posix(),
        "panel_raw_sha256": _sha256(raw_path),
        "panel_anchors": anchors,
        "panel_context": base_context,
        "box": list(SLOT_BOX),
        "crop_box": list(TIGHT_CROP_BOX),
        "variant": variant,
        "preprocessing": _preprocessing(variant),
        "text": recognized["text"],
        "confidence": recognized["confidence"],
    }


def _metadata(reader):
    return {
        "reader_fingerprint": getattr(reader, "fingerprint", None),
        "model_sha256": copy.deepcopy(getattr(reader, "models", {})),
        "ocr": "raw_neuralreader_text_recognition",
    }


def _preprocessing(variant=None):
    common = {
        "crop_box": list(TIGHT_CROP_BOX),
        "resize_scale": RESIZE_SCALE,
        "resize_resample": RESAMPLE_NAME,
        "grayscale": True,
        "autocontrast": True,
        "output_mode": "RGB",
    }
    if variant is None:
        # Keep the common top-level fields for existing artifact consumers,
        # while making the two actual per-observation transforms explicit.
        return {
            **common,
            "contrast_factor": CONTRAST_FACTOR,
            "variants": {name: _preprocessing(name) for name in OCR_VARIANTS},
        }
    if variant not in OCR_VARIANTS:
        raise ValueError(f"Unsupported Concert Info OCR variant: {variant}")
    return {
        **common,
        "variant": variant,
        "contrast_factor": 1.0 if variant == "gray" else CONTRAST_FACTOR,
    }


def _next_available(path):
    if not path.exists():
        return path
    index = 1
    while True:
        candidate = path.with_name(f"{path.stem}-attempt-{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _rejection(root, output_root, base_id, reason, raw=None, observations=(), reader=None, extra=None):
    if raw is None:
        base = {"frame_id": base_id}
    else:
        base = {
            "frame_id": base_id,
            "source_timestamp_ms": raw.get("source_timestamp_ms"),
            "evidence": raw.get("evidence"),
            "raw_sha256": fingerprint(raw),
        }
    value = {
        "version": 1,
        "reason": reason,
        "base": base,
        "preprocessing": _preprocessing(),
        "observations": list(observations),
    }
    if reader is not None:
        value["reader"] = _metadata(reader)
    if extra:
        value["details"] = extra
    rejected = output_root / REJECTED_DIRNAME / f"{base_id}.json"
    _save_json(_next_available(rejected), value)
    return rejected


def generate(root, *, reader_factory=NeuralReader, model_dir=Path(".local/models/rapidocr")):
    """Generate all safe Concert Info refinements under ``root``."""

    root = Path(root).resolve()
    output_root = root / OUTPUT_DIRNAME
    by_id = _part_manifests(root)
    if not by_id:
        raise ValueError(f"No decoded part manifests found under {root}.")
    capture, capture_rows, capture_error = _capture_rows(root)
    raw_by_id = _raw_index(root)
    artifacts = []
    rejected = []
    reader = None
    handled_until = {}

    for frame_id, (raw, raw_path) in sorted(raw_by_id.items(), key=lambda item: item[1][0].get("source_timestamp_ms", 0)):
        pair = by_id.get(frame_id)
        if pair is None:
            continue
        row, manifest_path = pair
        if not is_concert_info_panel(raw):
            continue
        context = _extract_panel_context(raw)
        timestamp = raw.get("source_timestamp_ms")
        if context and isinstance(timestamp, int) and timestamp <= handled_until.get(context, -1):
            continue
        capture_reason = capture_error or _capture_match(frame_id, row, capture_rows)
        if capture_reason:
            if context and isinstance(timestamp, int):
                handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, capture_reason, raw=raw)))
            continue
        if _source_identity(root, raw, row) is None:
            if context and isinstance(timestamp, int):
                handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, "source_identity_mismatch", raw=raw)))
            continue
        state = _slot_state(raw)
        if state == "readable":
            continue
        if state != "unreadable":
            if context and isinstance(timestamp, int):
                handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, f"support_slot_{state}", raw=raw)))
            continue

        artifact_path = output_root / raw_path.name
        if artifact_path.exists():
            handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, "existing_artifact", raw=raw)))
            continue
        if reader is None:
            reader = reader_factory(model_dir)
        selected, selection_reason = _nearby_frames(raw, by_id, raw_by_id, capture_rows)
        # _nearby_frames deliberately uses only cached base rows. Recheck
        # their source paths against the run root before making any crop.
        selected = [
            item
            for item in selected
            if _source_identity(root, item[3], item[1]) is not None
        ]
        if selection_reason or len(selected) < 3:
            handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, selection_reason or "insufficient_same_context_frames", raw=raw)))
            continue

        base_context = _extract_panel_context(raw)
        observations = []
        measurement_inputs = []
        try:
            for item in selected:
                source_path = root / item[1]["evidence"]
                views = _make_crops(source_path)
                for variant in OCR_VARIANTS:
                    measurement_inputs.append((item, variant, views[variant]))
            recognitions = _recognize_crops(
                reader,
                [crop for _, _, crop in measurement_inputs],
            )
            if len(recognitions) != len(measurement_inputs):
                raise ValueError("OCR returned a different number of results than measurements.")
            for (item, variant, crop), recognized in zip(measurement_inputs, recognitions):
                observations.append(
                    _measurement(
                        root,
                        output_root,
                        reader,
                        *item,
                        base_context,
                        variant=variant,
                        crop=crop,
                        recognized=recognized,
                    )
                )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rejected.append(str(_rejection(root, output_root, frame_id, "measurement_failed", raw=raw, observations=observations, reader=reader, extra={"error": str(exc)})))
            observations = []
        if not observations:
            handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            continue

        try:
            artifact = build(
                raw,
                observations,
                root / raw["evidence"],
                model_sha256=getattr(reader, "models", {}),
                observation_root=root,
            )
            artifact.update(
                source_sha256=capture["source"]["sha256"],
                generator="tracen_replay.refine_concert_panels",
                generator_version=2,
                reader_fingerprint=getattr(reader, "fingerprint", None),
                crop_box=list(TIGHT_CROP_BOX),
                preprocessing=_preprocessing(),
                ocr_variants=list(OCR_VARIANTS),
                measurement_count=len(observations),
                all_observations=observations,
                rejected_observations=[item for item in observations if item not in artifact["observations"]],
            )
            # Re-run the existing apply guards against the exact artifact
            # before publishing it. This is also a proof that the generated
            # observation bundle is portable within this run root.
            apply(raw, artifact, root / raw["evidence"], observation_root=root, original=raw)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            handled_until[context] = timestamp + DEFAULT_MAX_BASE_DISTANCE_MS
            rejected.append(str(_rejection(root, output_root, frame_id, "refinement_abstained", raw=raw, observations=observations, reader=reader, extra={"error": str(exc)})))
            continue

        _save_json(artifact_path, artifact)
        artifacts.append(str(artifact_path))
        handled_until[context] = max(
            timestamp + DEFAULT_MAX_BASE_DISTANCE_MS,
            selected[-1][1]["source_timestamp_ms"],
        )

    summary = {
        "root": str(root),
        "artifacts": artifacts,
        "rejected": rejected,
        "artifact_count": len(artifacts),
        "rejected_count": len(rejected),
    }
    _save_json(output_root / "generation-summary.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="full-recording run root containing capture/manifests/neural cache")
    parser.add_argument("--model-dir", type=Path, default=Path(".local/models/rapidocr"))
    args = parser.parse_args(argv)
    print(json.dumps(generate(args.root, model_dir=args.model_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
