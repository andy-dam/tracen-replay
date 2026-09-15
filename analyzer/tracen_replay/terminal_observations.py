"""Expose late source observations without manufacturing a final snapshot.

Terminal observations are an evidence view. They do not fill a turn closing,
carry an earlier balance forward, or combine fields from separate gameplay
frames into an accounting endpoint.
"""

from .reconcile import FIELDS
from .gameplay import CURRENCIES


TERMINAL_SCREENS = frozenset({
    'career_completion_hub', 'career_finish_confirmation', 'career_summary',
})


def build(report, contributions):
    """Keep every observation tied to one frame and its own field pointers.

    These are observations, not credits, debits, or next-turn endpoints. A
    later purchase can invalidate an earlier SP balance even when the final
    attribute screen never displays SP again.
    """
    turns = report.get('turn_ledger', {}).get('turns', [])
    if not turns or type(turns[-1].get('start_ms')) is not int:
        return []
    start = turns[-1]['start_ms']
    observations = []
    for index, row in enumerate(report['gameplay_tracking']['readings']):
        time = row.get('source_timestamp_ms')
        if (type(time) is not int or time < start
                or row.get('screen') not in TERMINAL_SCREENS):
            continue
        facts = row.get('facts', {})
        base = f'/gameplay_tracking/readings/{index}'
        candidates = {'stats': {}, 'performance': {}}

        def add(channel, field, value, ref):
            if type(value) is int and value >= 0:
                candidates[channel].setdefault(field, []).append((value, ref))

        attributes = facts.get('final_attributes')
        if isinstance(attributes, dict):
            for field in FIELDS:
                add('stats', field, attributes.get(field),
                    f'{base}/facts/final_attributes/{field}')
        add('stats', 'skill_points', facts.get('current_skill_points'),
            f'{base}/facts/current_skill_points')
        performance = facts.get('remaining_performance_points')
        if isinstance(performance, dict):
            for field in CURRENCIES:
                add('performance', field, performance.get(field),
                    f'{base}/facts/remaining_performance_points/{field}')

        for channel, fields in candidates.items():
            if not fields:
                continue
            values, refs, conflicts = {}, {}, {}
            for field, alternatives in fields.items():
                amounts = {value for value, _ in alternatives}
                if len(amounts) == 1:
                    values[field] = alternatives[0][0]
                    refs[field] = [ref for _, ref in alternatives]
                else:
                    conflicts[field] = [dict(value=value, source_ref=ref)
                                        for value, ref in alternatives]
            later = {field: [c['id'] for c in contributions
                             if c['channel'] == channel and c['field'] == field
                             and c['observation_end_ms'] > time]
                     for field in fields}
            # A confirmed purchase can have an unreadable price/balance and
            # therefore no numeric contribution. Its absence from arithmetic
            # is not evidence that an earlier SP observation is still current.
            unquantified = {field: [] for field in fields}
            if channel == 'stats' and 'skill_points' in fields:
                for event_index, event in enumerate(report['gameplay_tracking'].get('events', [])):
                    event_end = event.get('last_seen_ms', event.get('first_seen_ms'))
                    if (event.get('kind') == 'skill_purchase_batch'
                            and type(event_end) is int and event_end > time
                            and type(event.get('deltas', {}).get('skill_points')) is not int):
                        unquantified['skill_points'].append(f'/gameplay_tracking/events/{event_index}')
            observations.append(dict(
                source_ref=base, channel=channel, observed_at_ms=time,
                screen=row['screen'], evidence=[row['evidence']],
                values=values, value_refs=refs, conflicts=conflicts,
                later_numeric_contribution_refs=later,
                later_unquantified_change_refs=unquantified,
                basis='source_frame_observation',
                accounting_role='observation_only_not_additional_change',
                cross_frame_values_merged=False,
                run_completion_verified=False,
            ))
    return sorted(observations, key=lambda item: (item['observed_at_ms'], item['source_ref'], item['channel']))
