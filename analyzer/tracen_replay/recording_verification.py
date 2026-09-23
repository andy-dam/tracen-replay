"""Recording coverage and unresolved-work accounting, separate from OCR confidence."""
from collections import Counter
from fractions import Fraction
from .reconcile import FIELDS
from .source_clock import Clock


def final_crosscheck(data):
    rows=data['readings']
    summaries=[r for r in rows if r['screen']=='career_summary' and all(type(r['facts'].get('final_attributes',{}).get(f)) is int for f in FIELDS[:5])]
    hubs=[r for r in rows if r['screen']=='career_completion_hub' and all(type(r['facts'].get('final_attributes',{}).get(f)) is int for f in FIELDS[:5])]
    points=[r for r in rows if r['screen']=='career_completion_hub' and type(r['facts'].get('current_skill_points')) is int]
    if not summaries or not hubs or not points:
        # Missing corroboration must not erase separately observed values.
        # Ranks on the completion hub cannot supply numeric attributes.
        values={};evidence=[];timestamps={}
        if summaries:
            summary=max(summaries,key=lambda r:r['source_timestamp_ms'])
            values.update({f:summary['facts']['final_attributes'][f] for f in FIELDS[:5]})
            evidence.append(summary['evidence']);timestamps['attributes']=summary['source_timestamp_ms']
        if points:
            point=max(points,key=lambda r:r['source_timestamp_ms'])
            values['skill_points']=point['facts']['current_skill_points']
            evidence.append(point['evidence']);timestamps['skill_points']=point['source_timestamp_ms']
        missing=[name for name,available in (
            ('numeric_summary_attributes',summaries),
            ('numeric_completion_hub_attributes',hubs),
            ('completion_hub_skill_points',points)) if not available]
        return dict(complete_final_observations=False,observed_values=values,
                    observation_timestamps_ms=timestamps,evidence=list(dict.fromkeys(evidence)),
                    missing_observations=missing,attributes_agree_between_hub_and_summary=None,
                    inventory_complete=False,fully_verified=False,
                    scope='Separately observed final-screen values; the required numeric cross-check is unavailable. Missing numeric observations may be unreadable or not displayed; ranks are not converted to numbers.')
    final=summaries[-1];hub=hubs[-1];point=points[-1]
    attributes=final['facts']['final_attributes'];values={f:attributes[f] for f in FIELDS[:5]};values['skill_points']=point['facts']['current_skill_points']
    agree=all(attributes[f]==hub['facts']['final_attributes'][f] for f in FIELDS[:5])
    result=dict(complete_final_observations=True,attributes_agree_between_hub_and_summary=agree,values=values,
                attribute_timestamp_ms=final['source_timestamp_ms'],skill_points_timestamp_ms=point['source_timestamp_ms'],
                evidence=[hub['evidence'],point['evidence'],final['evidence']],inventory_complete=False,fully_verified=False)
    states=data.get('checkpoints',[])
    if states:
        initial=states[0];events=[e for e in data.get('events',[]) if initial['last_seen_ms']<e['first_seen_ms']<=final['source_timestamp_ms']]
        observed={f:values[f]-initial['values'][f] for f in FIELDS}
        supported={f:sum(e['deltas'].get(f,0) for e in events) for f in FIELDS}
        result['whole_career_stat_accounting']=dict(observed_change=observed,supported_change=supported,unexplained_change={f:observed[f]-supported[f] for f in FIELDS})
    return result


def audit(report):
    frames=report['frames'];duration=report['source']['duration_ms'];fps=report['sampling']['requested_fps']
    data=report['gameplay_tracking'];readings=data['readings'];times=[f['source_timestamp_ms'] for f in frames]
    seen={r['source_timestamp_ms'] for r in readings};expected=set(times)
    # The sampler keeps the first decoded frame at least one step after the
    # last it kept. Where the source's frames do not land on that step (30 fps
    # against 4 fps: every 267 ms), a gap runs up to one source frame longer.
    source_rate=report['source'].get('frame_rate')
    errors=[];maximum_interval=1000/fps+(1000/source_rate if source_rate else 0)+1
    # A stretch the source recorded no frame in is not a gap in the sampling:
    # the time is measured without the stretches the capture recorded.
    elapsed=Clock(report['sampling'].get('source_frame_gaps_ms') or ()).elapsed
    if not times or elapsed(0,times[0])>maximum_interval or elapsed(times[-1],duration)>maximum_interval:errors.append('source_endpoint_gap')
    if any(b<=a for a,b in zip(times,times[1:])):errors.append('duplicate_or_out_of_order_source_timestamp')
    if any(elapsed(a,b)>maximum_interval for a,b in zip(times,times[1:])):errors.append('base_sampling_gap')
    origin=report['source'].get('timeline_origin_seconds',0)
    for frame in frames:
        if 'source_pts' not in frame or 'time_base' not in frame:
            errors.append('missing_source_pts');break
        decoded=round((float(frame['source_pts']*Fraction(frame['time_base']))-origin)*1000)
        if abs(decoded-frame['source_timestamp_ms'])>1:
            errors.append('source_pts_timestamp_mismatch');break
    missing=sorted(expected-seen)
    if missing:errors.append('unprocessed_base_frames')
    bins=[]
    for start in range(0,duration,60000):
        end=min(start+60000,duration);rows=[r for r in readings if start<=r['source_timestamp_ms']<end]
        bases=[t for t in expected if start<=t<end]
        bins.append(dict(start_ms=start,end_ms=end,base_frames_expected=len(bases),base_frames_processed=sum(t in seen for t in bases),
            classified_observations=sum(r['screen']!='unknown' for r in rows),unknown_observations=sum(r['screen']=='unknown' for r in rows),
            reviewed=False))
    intervals=data['intervals'];unresolved=[dict(start_ms=i['start_ms'],end_ms=i['end_ms'],residual=i['unexplained_change']) for i in intervals if i['status']=='unresolved']
    resources=data.get('performance_accounting',{}).get('intervals',[])
    lessons=data.get('lesson_purchases',[]);skills=data.get('skill_purchases',[])
    from .calendar_coverage import audit as calendar_audit
    return dict(source_sha256=report['source']['sha256'],source_duration_ms=duration,full_source_processed=not errors,
        source_coverage_errors=errors,base_frames_expected=len(frames),base_frames_processed=len(expected&seen),missing_base_timestamps_ms=missing,
        sampled_coverage_by_minute=bins,stat_intervals=dict(Counter(i['status'] for i in intervals)),unresolved_stat_intervals=unresolved,
        state_supported_training_fields=sum(len(i.get('state_supported_candidate_resolutions',[])) for i in intervals),
        performance_intervals=dict(Counter(i['status'] for i in resources)),
        fan_intervals=data.get('fan_accounting',{}).get('statuses',{}),
        song_acquisitions=dict(Counter(s['acquisition'] for s in data.get('song_acquisitions',[]))),
        lesson_receipts=len(lessons),lesson_costs_unresolved=sum(p['performance_cost'] is None for p in lessons),
        skill_batches=len(skills),skill_costs_unresolved=sum(p['spent_skill_points'] is None for p in skills),
        complete_skill_lists=sum(p['purchased_list_complete'] for p in skills),
        skill_bundle_charges_assigned=sum(p.get('bundle_charge_assignment_complete',False) for p in skills),
        concerts_with_reward_receipts=sum(bool(c['reward_event_ids']) for c in data.get('concerts',[])),
        concerts_with_activation_receipts=sum(c['bonus_activation_verified'] for c in data.get('concerts',[])),
        calendar_action_coverage=calendar_audit(readings,data.get('turn_action_receipts',[])),
        final_state=final_crosscheck(data),
        fully_verified=False,go_ready=False,
        remaining_gates=['Review complete action and effect coverage against source evidence.',
                         'Keep surrounding-state candidate resolutions distinct from independently recognized gains; their separate visual reference does not establish generalization.',
                         'Verify skill inventory completeness, uncertain song names, race item identities and all active concert bonus values; mark unobservable fields explicitly.',
                         'Extend contiguous reference scoring beyond the opening actions and first concert transactions to full effect recall.',
                         'Preserve a separate recording initial evaluation and distinguish subsequent source-driven fixes from independent results.'])
