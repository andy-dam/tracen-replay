"""Validate bounded extra race samples without using them as other gameplay events."""
import copy




def merge_reward_rows(base, extra):
    """Merge same-PTS reward observations once; conflicting quantities abstain.

    A bounded inspection has its own OCR row for the same source frame. The
    inspection may add reward facts and the two result headers that give those
    facts section identity, but it must not replace the base row or leak
    unrelated OCR into the gameplay reading.
    """
    def valid_header(line):
        name = str(line.get('text', '')).strip().casefold() if isinstance(line, dict) else ''
        if name not in ('items', 'bonus'):
            return None
        try:
            box = line.get('box')
            confidence = float(line.get('confidence', 0))
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                return None
            left, top, right, bottom = (float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(value == value and abs(value) != float('inf')
                   for value in (left, top, right, bottom, confidence)):
            return None
        if not (250 <= left < right <= 830 and 500 <= top < bottom <= 900 and confidence >= 97):
            return None
        return name

    def merge_header_proof(target, supplement):
        """Add missing validated reward headers while retaining base OCR."""
        if not isinstance(supplement, dict):
            return
        supplement_ocr = supplement.get('ocr')
        supplement_lines = supplement_ocr.get('neural') if isinstance(supplement_ocr, dict) else None
        if not isinstance(supplement_lines, list):
            return
        target_ocr = target.get('ocr')
        if not isinstance(target_ocr, dict):
            target_ocr = {}
            target['ocr'] = target_ocr
        target_lines = target_ocr.get('neural')
        if not isinstance(target_lines, list):
            target_lines = []
            target_ocr['neural'] = target_lines
        existing = {name for line in target_lines if (name := valid_header(line)) is not None}
        for line in supplement_lines:
            name = valid_header(line)
            if name is not None and name not in existing:
                target_lines.append(copy.deepcopy(line))
                existing.add(name)

    def facts_for(row):
        facts = row.get('facts') if isinstance(row, dict) else None
        if isinstance(facts, dict):
            return facts
        facts = {}
        if isinstance(row, dict):
            row['facts'] = facts
        return facts

    def same_race_anchor(first, second):
        first_facts = facts_for(first)
        second_facts = facts_for(second)
        for field in ('fans', 'fans_gained'):
            first_value = first_facts.get(field)
            second_value = second_facts.get(field)
            if first_value is not None and second_value is not None and first_value != second_value:
                return False
        return True

    def quantity_box(item):
        if not isinstance(item, dict):
            return None
        box = item.get('box')
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            return None
        try:
            values = tuple(float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            return None
        if not all(value == value and abs(value) != float('inf') for value in values):
            return None
        if not (0 <= values[0] < values[2] <= 1920 and 0 <= values[1] < values[3] <= 1080):
            return None
        return values

    def quantity_value(item):
        value = item.get('quantity') if isinstance(item, dict) else None
        return value if type(value) is int and value >= 0 else None

    def valid_quantities(items):
        return isinstance(items, list) and all(
            quantity_value(item) is not None and quantity_box(item) is not None
            for item in items
        )

    def merge_one(target, supplement):
        target_facts = facts_for(target)
        supplement_facts = facts_for(supplement)
        prior = target_facts.get('visible_item_quantities') or []
        current = supplement_facts.get('visible_item_quantities') or []
        if not valid_quantities(prior) or not valid_quantities(current):
            target_facts['visible_item_quantities'] = []
            target['quantity_inspection_conflict'] = True
            target.setdefault('quantity_inspection_evidence', []).append(supplement.get('evidence'))
            merge_header_proof(target, supplement)
            return
        merged = copy.deepcopy(prior)
        conflict = False
        for item in current:
            item_box = quantity_box(item)
            matches = [
                prior_item for prior_item in merged
                if (quantity_box(prior_item) is not None
                    and abs((quantity_box(prior_item)[0] + quantity_box(prior_item)[2]
                            - item_box[0] - item_box[2]) / 2) <= 16
                    and abs((quantity_box(prior_item)[1] + quantity_box(prior_item)[3]
                            - item_box[1] - item_box[3]) / 2) <= 16)
            ]
            if len(matches) > 1 or (matches and quantity_value(matches[0]) != quantity_value(item)):
                conflict = True
                break
            if not matches:
                merged.append(copy.deepcopy(item))
        target_facts['visible_item_quantities'] = [] if conflict else sorted(
            merged,
            key=lambda x: (quantity_box(x)[1] // 50, quantity_box(x)[0]),
        )
        if conflict:
            target['quantity_inspection_conflict'] = True
        evidence = supplement.get('evidence')
        if evidence is not None:
            target.setdefault('quantity_inspection_evidence', []).append(evidence)
        merge_header_proof(target, supplement)

    by_time = {
        row['source_timestamp_ms']: copy.deepcopy(row)
        for row in base
        if isinstance(row, dict) and type(row.get('source_timestamp_ms')) is int
    }
    candidates = [
        row for row in extra
        if isinstance(row, dict) and type(row.get('source_timestamp_ms')) is int
    ]
    for row in sorted(candidates, key=lambda r: (r['source_timestamp_ms'], str(r.get('evidence', '')))):
        time = row['source_timestamp_ms']
        old = by_time.get(time)
        if old is None:
            if row.get('screen') == 'race_result':
                by_time[time] = copy.deepcopy(row)
            else:
                interruption = copy.deepcopy(row)
                interruption['facts'] = dict(facts_for(interruption), visible_item_quantities=[])
                by_time[time] = interruption
            continue
        if old.get('quantity_inspection_conflict'):
            continue
        # Supplemental observations can contain playback/other-screen rows
        # so the continuity caller can retain an interruption. They never
        # promote a non-result row or supply quantities from another race.
        if row.get('screen') != 'race_result' or old.get('screen') != 'race_result':
            continue
        if not same_race_anchor(old, row):
            continue
        merge_one(old, row)
    return [by_time[t] for t in sorted(by_time)]


def refine_base_rows(base, extra):
    """Apply corroborating same-frame reward facts while preserving base observations."""
    indexed = {}
    for row in extra:
        indexed.setdefault(row['source_timestamp_ms'], []).append(row)
    result = []
    for original in base:
        candidates = indexed.get(original['source_timestamp_ms'], [])
        facts = original.get('facts', {})
        matching = [row for row in candidates if row['screen'] == original['screen'] == 'race_result'
                    and (row['facts'].get('fans'), row['facts'].get('fans_gained')) ==
                    (facts.get('fans'), facts.get('fans_gained'))]
        if not matching:
            result.append(original)
            continue
        row = merge_reward_rows([original], matching)[0]
        if row['facts'].get('visible_item_quantities') != facts.get('visible_item_quantities'):
            row['facts']['base_visible_item_quantities'] = copy.deepcopy(facts.get('visible_item_quantities'))
        row['quantity_inspection_evidence'] = list(dict.fromkeys(row.get('quantity_inspection_evidence', [])))
        result.append(row)
    return result
