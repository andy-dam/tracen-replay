"""Evidence-linked transactions and state transitions; no balancing by invented deltas."""
from collections import Counter
from copy import deepcopy
import re
from .reconcile import FIELDS,stable_checkpoints,account
from .gameplay import lesson_transitions, CURRENCIES, screen_summary
from .skill_chains import cart_bundles,reconcile_skill_chains
from .receipt_continuity import collapse_cross_event_hint_duplicates


def skill_point_states(readings,states):
    """A visible completion-hub SP counter need not expose all five attributes."""
    result=list(states);group=[]
    def finish():
        if len({r['source_timestamp_ms'] for r in group})<3:return
        result.append(dict(id=f'skill-points-{group[0]["source_timestamp_ms"]}',
                           first_seen_ms=group[0]['source_timestamp_ms'],
                           last_seen_ms=group[-1]['source_timestamp_ms'],
                           values={'skill_points':group[0]['facts']['current_skill_points']},
                           evidence=group[-1]['evidence'],
                           supporting_frames=[r['evidence'] for r in group],
                           basis='repeated_completion_hub_skill_points'))
    for row in readings:
        value=row['facts'].get('current_skill_points')
        eligible=row['screen']=='career_completion_hub' and type(value) is int and value>=0
        if group and (not eligible or value!=group[-1]['facts']['current_skill_points']
                      or not 0<row['source_timestamp_ms']-group[-1]['source_timestamp_ms']<=500):
            finish();group=[]
        if eligible:group.append(row)
    finish()
    return sorted(result,key=lambda s:(s['last_seen_ms'],s['first_seen_ms']))


def skill_transactions(readings,states):
    """Only receipt-backed batches; menu counters are never independently charged."""
    states=skill_point_states(readings,states)
    spans=screen_summary(readings);transactions=[]
    for span in spans:
        if span['screen']!='skill_receipt' or span.get('completed_action')!='skill_purchase_batch':continue
        if transactions and span['first_seen_ms']-transactions[-1]['last_seen_ms']<=500:
            transactions[-1]['last_seen_ms']=span['last_seen_ms'];continue
        preceding=[s for s in spans if s['screen']=='skill_confirmation' and 0<span['first_seen_ms']-s['last_seen_ms']<=15000]
        if not preceding:continue
        confirmation=preceding[-1]
        before=[s for s in states if s['last_seen_ms']<confirmation['first_seen_ms']]
        after=[s for s in states if s['first_seen_ms']>span['last_seen_ms'] and s['first_seen_ms']-span['last_seen_ms']<=15000]
        before=before[-1] if before else None;after=after[0] if after else None
        between=[r for r in readings if before and after and before['last_seen_ms']<r['source_timestamp_ms']<after['first_seen_ms']]
        other_receipts=[s for s in spans if before and after and s['screen']=='skill_receipt' and before['last_seen_ms']<s['first_seen_ms']<after['first_seen_ms'] and s['id']!=span['id']]
        other_sp_effects=any(any(e['kind']=='stat_change' and e['field']=='skill_points' for e in r['effects']) or r['screen']=='training_result' for r in between)
        spent=before['values']['skill_points']-after['values']['skill_points'] if before and after and not other_receipts and not other_sp_effects else None
        if spent is not None and spent<=0:spent=None
        counter_proofs=[];basis='observed_states_without_other_sp_transactions' if spent is not None else 'unresolved'
        if spent is None and before:
            selections=[r for r in readings if r['screen']=='skill_selection' and before['last_seen_ms']<r['source_timestamp_ms']<confirmation['first_seen_ms']]
            cart=[r for r in selections if confirmation['first_seen_ms']-r['source_timestamp_ms']<=2000 and type(r['facts'].get('displayed_skill_points')) is int]
            if cart:
                tail=[]
                for row in reversed(cart):
                    if tail and (row['facts']['displayed_skill_points']!=tail[-1]['facts']['displayed_skill_points'] or tail[-1]['source_timestamp_ms']-row['source_timestamp_ms']>500):break
                    tail.append(row)
                cart=list(reversed(tail))
            post=[r for r in readings if r['screen']=='skill_selection' and 0<r['source_timestamp_ms']-span['last_seen_ms']<=1500 and type(r['facts'].get('displayed_skill_points')) is int]
            numbers={r['facts']['displayed_skill_points'] for r in cart+post}
            intervening=[r for r in readings if before['last_seen_ms']<r['source_timestamp_ms']<span['first_seen_ms']]
            unsafe=any(r['screen'] in ('training_result','skill_receipt') or any(e['kind']=='stat_change' and e['field']=='skill_points' for e in r['effects']) for r in intervening)
            if selections and selections[0]['source_timestamp_ms']-before['last_seen_ms']<=5000 and len(cart)>=2 and len(post)>=2 and len(numbers)==1 and not unsafe:
                value=numbers.pop();difference=before['values']['skill_points']-value
                if difference>0:
                    spent=difference;counter_proofs=[cart[-1]['evidence'],post[0]['evidence'],post[-1]['evidence']]
                    basis='receipt_with_matching_cart_and_post_receipt_balance'
            if spent is None:
                completed=[r for r in readings if r['screen']=='career_completion_hub' and 0<r['source_timestamp_ms']-span['last_seen_ms']<=10000 and type(r['facts'].get('current_skill_points')) is int]
                values={r['facts']['current_skill_points'] for r in completed}
                if len(completed)>=3 and len(values)==1:
                    difference=before['values']['skill_points']-values.pop()
                    intervening=[r for r in readings if before['last_seen_ms']<r['source_timestamp_ms']<span['first_seen_ms']]
                    unsafe=any(r['screen'] in ('training_result','skill_receipt') or any(e['kind']=='stat_change' and e['field']=='skill_points' for e in r['effects']) for r in intervening)
                    if difference>0 and not unsafe:
                        spent=difference;counter_proofs=[r['evidence'] for r in completed[:3]];basis='receipt_and_career_completion_balances'
        names=[]
        for row in readings:
            if confirmation['first_seen_ms']<=row['source_timestamp_ms']<=confirmation['last_seen_ms']:
                for name in row['facts'].get('visible_skill_names',[]):
                    if name not in names:names.append(name)
        histories={}
        for row in readings:
            if not before or not before['last_seen_ms']<row['source_timestamp_ms']<confirmation['first_seen_ms']:continue
            for card in row['facts'].get('skill_cards',[]):histories.setdefault(card['name'],[]).append((row,card))
        selected=[]
        for name,history in histories.items():
            costs={card['displayed_cost'] for _,card in history if card['menu_status']=='available'}
            if len(costs)!=1:continue
            initial=next((i for i,(_,card) in enumerate(history) if card['menu_status']=='available'),None)
            obtained=[r for r,card in history[initial+1:] if card['menu_status']=='obtained_or_selected']
            if name not in names and (len(obtained)<2 or history[-1][1]['menu_status']!='obtained_or_selected'):continue
            selected.append(dict(name=name,cost=costs.pop(),evidence=[history[initial][0]['evidence'],confirmation['evidence'] if name in names else obtained[-1]['evidence']],variant_verified=False,
                                 basis='visible_confirmation_and_price' if name in names else 'menu_state_change_candidate'))
        # Track removals as well as additions; an intermediate selection is not the final cart.
        counter_groups=[];active=[]
        for row in readings:
            if not before or not before['last_seen_ms']<row['source_timestamp_ms']<confirmation['first_seen_ms']:continue
            value=row['facts'].get('displayed_skill_points')
            if type(value) is not int:continue
            if active and (value!=active[-1]['facts']['displayed_skill_points'] or row['source_timestamp_ms']-active[-1]['source_timestamp_ms']>500):
                counter_groups.append(active)
                active=[]
            active.append(row)
        if active:counter_groups.append(active)
        counters=[];counter_basis={}
        for index,group in enumerate(counter_groups):
            if len(group)>=2:counters.append(group);continue
            row=group[0];time=row['source_timestamp_ms'];value=row['facts']['displayed_skill_points']
            previous=[g for g in counter_groups[:index] if len(g)>=2 and time-g[-1]['source_timestamp_ms']<=1500]
            following=[g for g in counter_groups[index+1:] if len(g)>=2 and g[0]['source_timestamp_ms']-time<=1500]
            if not previous or not following:continue
            upper=previous[-1][-1]['facts']['displayed_skill_points'];lower=following[0][0]['facts']['displayed_skill_points']
            prices={card['displayed_cost'] for name,history in histories.items() if name in names for r,card in history
                    if 0<=time-r['source_timestamp_ms']<=2000 and card['menu_status']=='available'}
            if lower<value<upper and upper-value in prices and value-lower in prices:
                counters.append(group);counter_basis[time]='single_frame_between_stable_balances_with_confirmation_and_price_support'
        toggles={}
        cart_changes=[]
        for previous,current in zip(counters,counters[1:]):
            delta=previous[-1]['facts']['displayed_skill_points']-current[0]['facts']['displayed_skill_points']
            if not delta:continue
            possible={name for name,history in histories.items() if any(0<=current[0]['source_timestamp_ms']-r['source_timestamp_ms']<=2000 and card['displayed_cost']==abs(delta) for r,card in history)}
            changed=[]
            start=current[0]['source_timestamp_ms'];prior_time=previous[-1]['source_timestamp_ms']
            for name,history in histories.items():
                prior=[(r,c) for r,c in history if start-2000<=r['source_timestamp_ms']<=prior_time]
                later=[(r,c) for r,c in history if start<=r['source_timestamp_ms']<=current[-1]['source_timestamp_ms']]
                from_status,to_status=('available','obtained_or_selected') if delta>0 else ('obtained_or_selected','available')
                if prior and prior[-1][1]['menu_status']==from_status and any(c['menu_status']==to_status for _,c in later):changed.append(name)
            directly_supported=possible.intersection(changed)
            if directly_supported:possible=directly_supported
            if len(possible)>1 and len(possible.intersection(names))==1:possible=possible.intersection(names)
            variant=None;variant_proofs=[]
            if len(possible)==1 and delta>0:
                history=histories[next(iter(possible))]
                visible=[(r,c) for r,c in history if 0<=prior_time-r['source_timestamp_ms']<=2000 and c['menu_status']=='available' and c['displayed_cost']==delta and c.get('variant')]
                suffix=[]
                for observation in reversed(visible):
                    if suffix and observation[1]['variant']!=suffix[-1][1]['variant']:break
                    suffix.append(observation)
                if len(suffix)>=2:variant=suffix[0][1]['variant'];variant_proofs=[r['evidence'] for r,_ in suffix]
            cart_changes.append(dict(first_seen_ms=current[0]['source_timestamp_ms'],projected_charge_delta=delta,
                candidate_names=sorted(possible),co_changed_names=sorted(changed),
                selected_variant=variant,variant_evidence=variant_proofs,
                counter_basis=counter_basis.get(start,'repeated_counter'),
                evidence=[previous[-1]['evidence'],current[0]['evidence']],committed=False))
            if len(possible)!=1:continue
            name=possible.pop();toggles.setdefault(name,[]).append(dict(amount=delta,evidence=[previous[-1]['evidence'],current[0]['evidence']]))
        for name,steps in toggles.items():
            if name in {s['name'] for s in selected}:continue
            net=sum(s['amount'] for s in steps)
            if net>0:selected.append(dict(name=name,cost=net,evidence=[p for s in steps for p in s['evidence']],variant_verified=False,basis='cart_counter_and_unique_visible_price_candidate'))
        item_sum=sum(item['cost'] for item in selected)
        bundles,unassigned=cart_bundles(cart_changes)
        cart_total=sum(c['projected_charge_delta'] for c in cart_changes)
        bundle_total=sum(b['net_cost'] for b in bundles)
        transactions.append(dict(id=f'skills-{len(transactions)+1:03d}',kind='skill_purchase_batch',first_seen_ms=span['first_seen_ms'],last_seen_ms=span['last_seen_ms'],
            confirmation_first_seen_ms=confirmation['first_seen_ms'],confirmation_last_seen_ms=confirmation['last_seen_ms'],
            spent_skill_points=spent,deltas={'skill_points':-spent} if spent is not None else {},
            visible_confirmation_names=names,purchased_list_complete=False,complete_transaction_verified=False,
            selected_item_candidates=selected,item_cost_sum=item_sum,item_cost_sum_matches_charge=spent is not None and item_sum==spent,
            committed_cart_bundles=bundles,cart_net_cost=cart_total,bundle_net_cost=bundle_total,
            cart_charge_matches_receipt=spent is not None and cart_total==spent,
            bundle_charge_assignment_complete=spent is not None and bundle_total==spent and not unassigned,
            unassigned_cart_changes=unassigned,
            cart_changes=cart_changes,pricing_limitation='Displayed prerequisite and upgrade prices are not additive; card candidates are not individually verified charges.',
            balance_evidence=[dict(role=role,skill_points=state['values']['skill_points'],
                                   first_seen_ms=state['first_seen_ms'],last_seen_ms=state['last_seen_ms'],
                                   basis=state.get('basis','repeated_six_field_state'),
                                   evidence=state.get('supporting_frames',[state['evidence']]))
                              for role,state in (('before',before),('after',after)) if state],
            evidence=[s['evidence'] for s in (before,confirmation,span,after) if s]+counter_proofs,
            cost_basis=basis))
    return reconcile_skill_chains(transactions,readings,spans)


def partial_song_name(requested,received):
    """Match only a shared full name plus one unresolved terminal character."""
    if not requested or not received or requested==received:return False
    short,long=sorted((requested,received),key=len)
    if len(short.split())<2 or len(short)<8 or not long.startswith(short):return False
    remainder=long[len(short):];suffix=remainder.strip()
    return len(suffix)==1 and not suffix.isdigit() and (remainder.startswith(' ') or not suffix.isalnum())


def source_song_alias(effect):
    """Keep the raw receipt identity available after validated symbol recovery.

    This is an association candidate, not a second canonical name. The caller
    still requires repeated request and debit evidence before linking it.
    """
    if not isinstance(effect,dict):return None
    proof=effect.get('visual_symbol_observation',{})
    if not isinstance(proof,dict):return None
    method=proof.get('method')
    title=proof.get('title_evidence',{})
    if not isinstance(title,dict):return None
    original=effect.get('original_text')
    if method=='strict_note_and_independently_read_title':
        if not isinstance(original,str) or title.get('line',{}).get('text')!=original:return None
        name=title.get('title')
        if not isinstance(name,str) or effect.get('name')!=name+' ♪':return None
        match=re.fullmatch(r'Learned the song "(.+)"[.!]',original)
        if not match or not re.fullmatch(re.escape(name)+r'\s+[A-Za-z]',match[1]):return None
        return match[1]
    if method!='outlined_star_one_hole_ten_alternating_turns' or proof.get('symbol')!='☆':return None
    if not isinstance(original,str):return None
    match=re.fullmatch(r'Learned the song (["“])(.+?)(["”])([.!])',original)
    if not match:return None
    opening,raw_title,closing,stop=match.groups()
    if effect.get('name')!=raw_title+'☆':return None
    prefix=title.get('prefix_text');continuation=title.get('continuation_text')
    raw_lines=title.get('raw_lines')
    if not isinstance(prefix,str) or not isinstance(continuation,str):return None
    if not prefix or not continuation or raw_title!=prefix+' '+continuation:return None
    if not isinstance(raw_lines,list) or len(raw_lines)!=2 or not all(isinstance(line,dict) for line in raw_lines):return None
    first=f'Learned the song {opening}{prefix}'
    first_text=raw_lines[0].get('text')
    second_text=raw_lines[1].get('text')
    if first_text!=first or not isinstance(second_text,str):return None
    if second_text!=f'{continuation}{closing}{stop}':return None
    if ' '.join((first_text,second_text))!=original:return None
    return raw_title


def lesson_receipts(readings, outcomes):
    """Join named acquisition evidence to a matching request, retaining cost gaps."""
    purchases=[];used=set()
    for event in outcomes:
        acquired=[e for e in event['effects'] if e['kind'] in ('named_acquisition','song_learned')]
        for effect in acquired:
            partial=False;symbol_alias=False;requested=effect['name'];request_names={requested}
            confirmations=[r for r in readings if r['screen']=='lesson_confirmation'
                and 0<event['first_seen_ms']-r['source_timestamp_ms']<=5000
                and r['facts'].get('name_candidates')==[effect['name']]]
            if effect['kind']=='song_learned' and len(acquired)==1:
                nearby=[r for r in readings if r['screen']=='lesson_confirmation'
                        and 0<event['first_seen_ms']-r['source_timestamp_ms']<=2000]
                names={r['facts']['name_candidates'][0] for r in nearby if len(r['facts'].get('name_candidates',[]))==1}
                alias=source_song_alias(effect)
                compatible=bool(alias and names and all(
                    n==alias or n==effect['name'] or partial_song_name(n,effect['name'])
                    or partial_song_name(n,alias) or partial_song_name(alias,n) for n in names))
                # Source proof can reconcile a mixed request even when one
                # frame already contains the corrected symbol name. Without
                # that proof, retain the exact-text path unchanged.
                source_compatible=bool(alias and compatible)
                if (not confirmations and (len(names)==1 or compatible)) or source_compatible:
                    requested=next(r['facts']['name_candidates'][0] for r in reversed(nearby)
                                   if len(r['facts'].get('name_candidates',[]))==1)
                    partial_match=partial_song_name(requested,effect['name'])
                    # A lone raw title keeps the existing partial-name basis.
                    # Use the source alias when the corrected symbol is the
                    # only way to relate the names, or when mixed spellings
                    # need to be retained for auditability.
                    symbol_alias=compatible and alias in names and (not partial_match or len(names)>1)
                    if partial_match or symbol_alias:
                        request_names=names if compatible else {requested}
                        confirmations=[r for r in nearby if len(r['facts'].get('name_candidates',[]))==1
                                       and r['facts']['name_candidates'][0] in request_names];partial=True
            if not confirmations:continue
            def request_matches(row):
                names=row['facts'].get('name_candidates')
                return names==[] or isinstance(names,list) and len(names)==1 and names[0] in request_names
            last=confirmations[-1]
            # Only the final continuous request belongs to this receipt.
            group=[last]
            for row in reversed([r for r in readings if r['source_timestamp_ms']<last['source_timestamp_ms']]):
                if row['screen']!='lesson_confirmation' or group[-1]['source_timestamp_ms']-row['source_timestamp_ms']>500:break
                if not request_matches(row):break
                group.append(row)
            first=group[-1];key=first['source_timestamp_ms']
            if key in used:continue
            prior_receipts=[e['last_seen_ms'] for e in outcomes if e['last_seen_ms']<first['source_timestamp_ms']
                            and any(effect['kind'] in ('named_acquisition','song_learned') for effect in e['effects'])]
            baseline_start=max(prior_receipts,default=-1)
            before_rows=[r for r in readings if r['screen']=='lesson_selection'
                and r['source_timestamp_ms']>baseline_start
                and 0<first['source_timestamp_ms']-r['source_timestamp_ms']<=10000
                ]
            # Keep only the last continuous menu visit. Even a missed named
            # receipt must not let a balance cross an intervening dialog.
            if before_rows:
                tail=before_rows[-1]['source_timestamp_ms'];recent=[]
                for row in reversed(before_rows):
                    if tail-row['source_timestamp_ms']>500:break
                    recent.append(row);tail=row['source_timestamp_ms']
                before_rows=list(reversed(recent))
            before=before_rows[-1] if before_rows else None
            def balance(rows,key):
                result={}
                for field in CURRENCIES:
                    values=[r['facts'].get(key,{}).get(field) for r in rows if type(r['facts'].get(key,{}).get(field)) is int]
                    suffix=[]
                    for value in reversed(values):
                        if suffix and value!=suffix[-1]:break
                        suffix.append(value)
                    result[field]=suffix[0] if suffix and (len(suffix)>=2 or len(set(values))==1) else None
                return result
            initial=balance(before_rows,'performance_points')
            projected={}
            for field in CURRENCIES:
                seen={r['facts'].get('projected_performance_points',{}).get(field) for r in group}
                seen.discard(None)
                projected[field]=seen.pop() if len(seen)==1 else None
            complete=all(type(projected.get(k)) is int for k in CURRENCIES)
            cost={k:initial[k]-projected[k] for k in CURRENCIES} if before and complete and all(type(v) is int for v in initial.values()) else None
            if cost and any(v<0 for v in cost.values()):cost=None
            after=[]
            for row in readings:
                if row['source_timestamp_ms']<=event['last_seen_ms']:continue
                if row['source_timestamp_ms']-event['last_seen_ms']>5000 or row['screen']=='lesson_confirmation':break
                if row['screen']=='lesson_selection' and all(type(row['facts'].get('performance_points',{}).get(k)) is int for k in CURRENCIES):after.append(row)
            matched=next((r for r in after if complete and r['facts']['performance_points']==projected),None)
            if partial:
                agreeing=[r for r in after if complete and r['facts']['performance_points']==projected]
                between=[r for r in readings if first['source_timestamp_ms']<r['source_timestamp_ms']<event['first_seen_ms']]
                returned=[r for r in between if r['screen']=='lesson_selection']
                if (cost is None or not any(v>0 for v in cost.values())
                    or len({r['source_timestamp_ms'] for r in group})<2
                    or len({r['source_timestamp_ms'] for r in before_rows if r['facts'].get('performance_points')==initial})<2
                    or len({r['source_timestamp_ms'] for r in agreeing})<2
                    or len({r['source_timestamp_ms'] for r in returned})>=2
                    or any(r['screen'] not in ('lesson_confirmation','lesson_selection','unknown') for r in between)
                    or any(r['screen']=='lesson_confirmation' and not request_matches(r) for r in between)):continue
            used.add(key)
            projected_effects=[];effect_keys=set()
            for row in reversed(group):
                for projected_effect in row['facts'].get('projected_effects',[]):
                    effect_key=(projected_effect['kind'],projected_effect.get('field'),projected_effect.get('amount')) if projected_effect.get('field') else (projected_effect['kind'],projected_effect['raw_text'])
                    if effect_key not in effect_keys:projected_effects.append(projected_effect);effect_keys.add(effect_key)
            purchases.append(dict(id=f'lesson-{len(purchases)+1:04d}',kind='lesson_purchase',name=effect['name'],
                source_timestamp_ms=event['first_seen_ms'],receipt_event_id=event['id'],
                performance_cost=cost,cost_basis='observed_debit' if cost is not None and matched else 'displayed_request' if cost is not None else 'unresolved',
                requested_name=requested,receipt_name=effect['name'],name_identity_verified=False,
                name_match_basis='source_symbol_alias_and_repeated_observed_debit' if symbol_alias else 'partial_name_and_repeated_observed_debit' if partial else 'exact_observed_text',
                after_balance_observed=matched is not None,awarded_stats=event['deltas'],
                projected_effects=projected_effects,
                evidence=[r['evidence'] for r in (before,first,matched) if r]+[event['evidence']],
                complete_transaction_verified=False))
            if symbol_alias:purchases[-1]['observed_request_names']=sorted(request_names)
    return purchases


def training_events(readings,states=()):
    groups=[];current=None
    for row in readings:
        if row['screen']!='training_result':
            if current and row['source_timestamp_ms']-current['last_seen_ms']>500:current=None
            continue
        option=row.get('training_option');time=row['source_timestamp_ms']
        if current is None or time-current['last_seen_ms']>500 or (option and current['option'] and option!=current['option']):
            current=dict(kind='training',option=option,first_seen_ms=time,last_seen_ms=time,rows=[]);groups.append(current)
        current['last_seen_ms']=time
        if option:current['option']=option
        current['rows'].append(row)
    events=[]
    for group in groups:
        deltas={};proofs={};conflicts={}
        for field in FIELDS:
            observations=[(r,r['facts']['training_gains'][field]) for r in group['rows'] if field in r['facts'].get('training_gains',{})]
            values={value for _,value in observations}
            if len(values)==1 and len(observations)>=2:
                deltas[field]=next(iter(values));proofs[field]=[r['evidence'] for r,_ in observations]
            elif len(values)>1:
                counts=Counter(value for _,value in observations)
                complete=[value for value,count in counts.items() if count>=3 and all(str(value).startswith(str(other)) for other in values)]
                if len(complete)==1:
                    deltas[field]=complete[0];proofs[field]=[r['evidence'] for r,value in observations if value==complete[0]]
                else:conflicts[field]=sorted(values)
        evidence=next(iter(proofs.values()),[group['rows'][0]['evidence']])[0]
        events.append(dict(id=f'training-{len(events)+1:04d}',kind='training',training_option=group['option'],
            first_seen_ms=group['first_seen_ms'],last_seen_ms=group['last_seen_ms'],deltas=deltas,
            evidence=evidence,field_evidence=proofs,conflicting_readings=conflicts,
            repeated_fields=[field for field,paths in proofs.items() if len(paths)>=2],
            effect_coverage_verified=False,action_time_ms=None))
        performance={};performance_proofs={}
        for field in CURRENCIES:
            observations=[(r,r['facts']['awarded_performance_gains'][field]) for r in group['rows'] if field in r['facts'].get('awarded_performance_gains',{})]
            values={value for _,value in observations}
            if len(values)==1:
                performance[field]=values.pop();performance_proofs[field]=[r['evidence'] for r,_ in observations]
            elif len(values)>1:
                by_value={v:{r['source_timestamp_ms'] for r,n in observations if n==v} for v in values}
                complete=[v for v,times in by_value.items() if len(times)>=3 and max(times)-min(times)>=50
                          and sum(len(other_times) for n,other_times in by_value.items() if n!=v)==1
                          and all(str(v).startswith(str(n)) for n in values)]
                if len(complete)==1:
                    value=complete[0];performance[field]=value
                    performance_proofs[field]=[r['evidence'] for r,n in observations if n==value]
                    events[-1].setdefault('performance_reading_resolutions',{})[field]=dict(
                        observed_amounts=sorted(values),accepted_amount=value,
                        basis='repeated_complete_digits_with_single_truncated_outlier',
                        outlier_evidence=[r['evidence'] for r,n in observations if n!=value])
                else:events[-1].setdefault('performance_reading_conflicts',{})[field]=sorted(values)
        events[-1].update(performance_deltas=performance,performance_evidence=performance_proofs,
                         action_identity_evidence=[r['evidence'] for r in group['rows']
                             if r.get('training_option')==group['option']],
                         action_identity_observations=len({r['source_timestamp_ms'] for r in group['rows']
                             if r.get('training_option')==group['option']}))
        failures=[r for r in group['rows'] if r['facts'].get('training_outcome')=='failure']
        if failures:
            events[-1].update(training_outcome='failure',failure_evidence=[r['evidence'] for r in failures],
                             unawarded_performance_projection=dict(performance),performance_deltas={},performance_evidence={})
            for row in failures:events[-1]['unawarded_performance_projection'].update(row['facts'].get('unawarded_performance_projection',{}))
        before=[s for s in states if 0<group['first_seen_ms']-s['last_seen_ms']<=5000]
        before=before[-1] if before else None
        earlier_awards=before and any(before['last_seen_ms']<r['source_timestamp_ms']<group['first_seen_ms'] and any(e['kind']=='stat_change' for e in r.get('effects',[])) for r in readings)
        if before and not earlier_awards:
            for field in FIELDS:
                gain_observations=[(row,value) for row in group['rows'] for value in row['facts'].get('training_gain_candidates',{}).get(field,[])]
                if gain_observations:
                    last_gain=max(row['source_timestamp_ms'] for row,_ in gain_observations)
                    qualified=[]
                    for gain in {value for _,value in gain_observations}:
                        gain_rows=[row for row,value in gain_observations if value==gain]
                        expected_total=before['values'][field]+gain
                        complete_totals=[row for row in group['rows'] if row['source_timestamp_ms']>last_gain
                                         and row['facts'].get('result_values',{}).get(field)==expected_total]
                        after_rows=[row for row in group['rows'] if row['source_timestamp_ms']>last_gain and (
                            row['facts'].get('result_value_candidates',{}).get(field)==expected_total
                            or (row['facts'].get('result_numerator_candidates',{}).get(field)==[expected_total]
                                and any(full['source_timestamp_ms']>=row['source_timestamp_ms'] for full in complete_totals)))]
                        times={row['source_timestamp_ms'] for row in gain_rows+after_rows}
                        strict_gain=any(row['facts'].get('training_gains',{}).get(field)==gain for row in gain_rows)
                        strict_total=any(row['facts'].get('result_values',{}).get(field)==before['values'][field]+gain for row in after_rows)
                        # A short result can expose the gain in just one frame.
                        # A stable before state plus an explicit high-confidence
                        # gain and a later high-confidence total are three
                        # separately displayed observations of that transition.
                        corroborated=len(times)>=3 or (len(times)>=2 and strict_gain and strict_total)
                        if after_rows and corroborated and max(times)-min(times)>=50:qualified.append((gain,gain_rows,after_rows))
                    if len(qualified)==1:
                        gain,gain_rows,after_rows=qualified[0]
                        if field in conflicts or (field in deltas and deltas[field]!=gain):
                            events[-1].setdefault('gain_reading_disagreements',{})[field]=dict(animated_candidates=sorted({v for _,v in gain_observations}),cross_checked_change=gain)
                        deltas[field]=gain;proofs[field]=[before['evidence']]+[r['evidence'] for r in gain_rows+after_rows]
                        events[-1].setdefault('cross_checked_gain_fields',[]).append(field)
                        partial_proofs=[dict(source_timestamp_ms=r['source_timestamp_ms'],
                            evidence=r.get('supplemental_evidence',r['evidence']),
                            readings=r['facts']['partial_result_counter_readings'][field]) for r in after_rows
                            if r['facts'].get('result_numerator_candidates',{}).get(field)==[before['values'][field]+gain]
                            and field in r['facts'].get('partial_result_counter_readings',{})]
                        if partial_proofs:events[-1].setdefault('partial_result_counter_evidence',{})[field]=partial_proofs
                        if field not in events[-1]['repeated_fields']:events[-1]['repeated_fields'].append(field)
                suffix=[]
                for row in reversed(group['rows']):
                    value=row['facts'].get('result_values',{}).get(field)
                    if type(value) is not int:
                        if suffix:break
                        continue
                    if suffix and value!=suffix[-1][1]:break
                    suffix.append((row,value))
                if len(suffix)>=3 and suffix[0][0]['source_timestamp_ms']-suffix[-1][0]['source_timestamp_ms']>=60:
                    delta=suffix[0][1]-before['values'][field]
                    if delta>0:
                        if field in deltas and deltas[field]!=delta:
                            events[-1].setdefault('gain_reading_disagreements',{})[field]=dict(animated_gain=deltas[field],stable_result_change=delta)
                            # A truncated gain prefix can lose a trailing digit. Other
                            # disagreements cannot be resolved by preferring arithmetic.
                            if not str(delta).startswith(str(deltas[field])):continue
                        deltas[field]=delta;proofs[field]=[before['evidence']]+[r['evidence'] for r,_ in suffix]
                        events[-1].setdefault('result_state_derived_fields',[]).append(field)
                        if field not in events[-1]['repeated_fields']:events[-1]['repeated_fields'].append(field)
    return events


def continued_title(current,row):
    """A weaker full caption can link a truncated title, never invent an award."""
    if not current or row['source_timestamp_ms']-current['last_seen_ms']>500:return None
    title=row.get('context_title');previous=current.get('context_title')
    if not title or not previous or title==previous:return None
    if row.get('context_title_candidate')==previous:full=previous
    elif current.get('context_title_candidate')==title:full=title
    else:return None
    def signature(e):return tuple(e.get(k) for k in ('kind','field','name','amount','direction','value'))
    overlap={signature(e) for e in current['effects'].values()} & {signature(e) for e in row.get('effects',[])}
    return full if overlap else None


def outcome_events(readings):
    events=[];current=None
    rows_by_evidence={r['evidence']:r for r in readings}
    for row in readings:
        # Event reconciliation annotates accepted alternatives. Keep those
        # annotations separate from the original parsed observations.
        time=row['source_timestamp_ms'];effects=deepcopy(row.get('effects',[]));pending=deepcopy(row.get('facts',{}).get('effect_candidates',[]))
        if not effects and not pending:
            # Recognized dialogue or a different screen is evidence of a boundary.
            lines=row.get('ocr',{}).get('neural',[])
            narrative=any(l['confidence']>=95 and 790<(l['box'][1]+l['box'][3])/2<950 and len(l['text'])>25 for l in lines)
            if current and (narrative or row['screen'] not in ('unknown','event_outcome') or time-current['last_seen_ms']>500):current=None
            continue
        title=row.get('context_title')
        continuation=continued_title(current,row)
        if continuation:title=continuation
        if current is None or time-current['last_seen_ms']>500 or (title and current['context_title'] and title!=current['context_title'] and not continuation):
            current=dict(id=f'outcome-{len(events)+1:04d}',kind='outcome',first_seen_ms=time,last_seen_ms=time,evidence=row['evidence'],
                         context_title=title,effects={},field_evidence={},conflicting_readings=[],action_time_ms=None,pending_effects={},effect_observations={})
            events.append(current)
        current['last_seen_ms']=time
        if title:current['context_title']=title
        current['context_title_candidate']=row.get('context_title_candidate')
        if continuation:
            current.setdefault('title_continuation_evidence',[]).append(dict(evidence=row['evidence'],
                observed_title=row.get('context_title'),full_candidate=row.get('context_title_candidate'),
                retained_title=title,basis='matching_full_caption_and_overlapping_receipt'))
        for effect in pending:
            key='|'.join(str(v or '') for v in (effect['kind'],effect.get('field'),effect.get('name')))
            current['pending_effects'].setdefault(key,[]).append((row['source_timestamp_ms'],row['evidence'],effect))
        keys=[(e['kind'],e.get('field'),e.get('name')) for e in effects]
        duplicate=any(count>1 for count in Counter(keys).values())
        if duplicate:
            for key,count in Counter(keys).items():
                if count>1:current['conflicting_readings'].append(dict(reason='multiple_same_field_lines',field='|'.join(str(part or '') for part in key),evidence=row['evidence']))
        for effect,key in zip(effects,keys):
            key='|'.join(str(part or '') for part in key)
            current['effect_observations'].setdefault(key,[]).append((time,row['evidence'],effect))
            prior=current['effects'].get(key)
            if prior and (prior.get('amount'),prior.get('direction'),prior.get('value'))!=(effect.get('amount'),effect.get('direction'),effect.get('value')):
                current['conflicting_readings'].append(dict(reason='changing_effect_value',field=key,evidence=row['evidence']))
            else:
                current['effects'][key]=effect
                current['field_evidence'].setdefault(key,[]).append(row['evidence'])
    for event in events:
        receipt_observations=event.pop('effect_observations')
        for key,observations in receipt_observations.items():
            conflicts=[c for c in event['conflicting_readings'] if c['field']==key]
            if not conflicts or any(c['reason']!='changing_effect_value' for c in conflicts):continue
            amounts=Counter(e.get('amount') for _,_,e in observations)
            winners=[v for v,n in amounts.items() if type(v) is int and n>=2 and all(other==v or (type(other) is int and count==1 and (str(v).startswith(str(other)) or str(v).endswith(str(other)))) for other,count in amounts.items())]
            if len(winners)!=1:continue
            matches=[o for o in observations if o[2].get('amount')==winners[0]]
            event['effects'][key]=matches[-1][2];event['field_evidence'][key]=[o[1] for o in matches]
            event.setdefault('resolved_reading_conflicts',[]).append(dict(field=key,observed_amounts=list(amounts),accepted_amount=winners[0],basis='repeated_complete_digits_with_single_truncated_outlier'))
            event['conflicting_readings']=[c for c in event['conflicting_readings'] if c['field']!=key]
        from .receipt_confirmation import resolve_with_later_repeat
        for key,observations in receipt_observations.items():
            resolve_with_later_repeat(event,key,observations,event['pending_effects'].get(key,[]))
        for key,observations in event.pop('pending_effects').items():
            if key in event['effects']:continue
            partial=observations[0][2]
            if partial.get('name') and any(e['kind']==partial['kind'] and e.get('name','').startswith(partial['name']+' ') for e in event['effects'].values()):continue
            values={e.get('amount') for _,_,e in observations}
            eligible=[]
            for value in values:
                matches=[r for r in observations if r[2].get('amount')==value]
                if len(matches)>=3 and matches[-1][0]-matches[0][0]>=500:eligible.append(matches)
            if len(eligible)==1:
                matches=eligible[0];effect=dict(matches[0][2],confirmation='repeated_unterminated_text')
                event['effects'][key]=effect;event['field_evidence'][key]=[r[1] for r in matches]
        from .animated_performance import reconcile as reconcile_animated_performance
        reconcile_animated_performance(event,readings)
        reconcile_animated_performance(event,readings,stat=True,receipt_observations=receipt_observations)
        event['effects']=list(event['effects'].values())
        from .friendship_suffix import resolve as resolve_friendship_suffix
        resolve_friendship_suffix(event,rows_by_evidence)
        from .receipt_names import flag_friendship_identity_conflicts
        flag_friendship_identity_conflicts(event,rows_by_evidence)
        from .receipt_names import flag_inheritance_identity_conflicts
        flag_inheritance_identity_conflicts(event,rows_by_evidence)
        from .inheritance_spark_identity import resolve as resolve_spark_identity
        resolve_spark_identity(event,rows_by_evidence)
        from .receipt_names import collapse_visual_hint_variants
        collapse_visual_hint_variants(event,{r['evidence']:r['source_timestamp_ms'] for r in readings},rows_by_evidence)
        from .receipt_names import collapse_punctuated_hint_variants
        collapse_punctuated_hint_variants(event,rows_by_evidence)
        # Missing circle glyphs must not turn one visible hint into two awards.
        # Only collapse a suffix alternative when the exact base was also read
        # in this receipt with the same amount; retain the identity uncertainty.
        hints={e['name']:e for e in event['effects'] if e['kind']=='skill_hint_change'}
        removed=[]
        for name,effect in hints.items():
            base=re.sub(r'\s*[O○◯◎]$','',name).strip()
            if base==name or base not in hints or hints[base]['amount']!=effect['amount']:continue
            target=hints[base]
            target.setdefault('observed_name_candidates',[base]).append(name)
            target['circle_variant_verified']=False
            base_key='skill_hint_change||'+base;variant_key='skill_hint_change||'+name
            event['field_evidence'].setdefault(base_key,[]).extend(event['field_evidence'].get(variant_key,[]))
            removed.append(effect)
        event['effects']=[e for e in event['effects'] if e not in removed]
        from .receipt_names import collapse_separator_hint_variants
        collapse_separator_hint_variants(event,rows_by_evidence)
        from .receipt_names import collapse_song_variants
        collapse_song_variants(event,{r['evidence']:r['source_timestamp_ms'] for r in readings})
        ambiguous={c['field'] for c in event['conflicting_readings']}
        event['deltas']={e['field']:e['amount'] for e in event['effects'] if e['kind']=='stat_change' and f'stat_change|{e["field"]}|' not in ambiguous}
    events=collapse_cross_event_hint_duplicates(events,readings)
    for event in events:
        ambiguous={c['field'] for c in event['conflicting_readings']}
        event['deltas']={e['field']:e['amount'] for e in event['effects'] if e['kind']=='stat_change' and f'stat_change|{e["field"]}|' not in ambiguous}
    return events


def checkpoints(readings):
    rows=[]
    for row in readings:
        stats=dict(row['stats'])
        if row['screen']=='lesson_confirmation':
            stats['values']=row['facts'].get('current_stats')
        rows.append(dict(stats,source_timestamp_ms=row['source_timestamp_ms'],evidence=row['evidence']))
    return stable_checkpoints(rows)


def reconcile_visible_training_candidates(before,after,events,readings):
    """Disambiguate visible digit prefixes; never manufacture a residual event.

    This is constraint-supported recognition, not independent confirmation of
    effect recall. Its provenance remains separate in the report.
    """
    scoped=[e for e in events if before['last_seen_ms']<e['first_seen_ms']<=after['first_seen_ms']]
    trainings=[e for e in scoped if e['kind']=='training' and e.get('training_option')]
    if len(trainings)!=1:return []
    event=trainings[0];rows=[r for r in readings if event['first_seen_ms']<=r['source_timestamp_ms']<=event['last_seen_ms'] and r['screen']=='training_result']
    ledger=account(before,after,[e for e in scoped if e['deltas']]);resolutions=[]
    for field,residual in ledger['unexplained_change'].items():
        if not residual:continue
        # Ambiguous dialogue receipts can hide an additional award; do not use
        # their unexplained amount to choose between training observations.
        if any(e.get('conflicting_readings') for e in scoped if e['kind']!='training'):continue
        observations=[(r,r['facts']['training_gains'][field]) for r in rows if field in r['facts'].get('training_gains',{})]
        values={value for _,value in observations}
        target=event['deltas'].get(field,0)+residual
        if target not in values or target<=0 or not all(str(target).startswith(str(v)) for v in values):continue
        proofs=[r['evidence'] for r,value in observations if value==target]
        event['deltas'][field]=target;event['field_evidence'][field]=proofs
        resolution=dict(field=field,amount=target,visual_candidates=sorted(values),
                        gain_evidence=proofs,before_evidence=before['evidence'],after_evidence=after['evidence'],
                        basis='visible_complete_gain_and_surrounding_state_constraints',independent_effect_verification=False)
        event.setdefault('state_supported_candidate_resolutions',[]).append(resolution);resolutions.append(resolution)
    return resolutions


def reconstruct(readings,choice_observations=(),race_reward_observations=(),*,hint_card_observations=(),source_sha256=None):
    outcome_readings=readings;eligible_hints=hint_card_observations;hint_row_audit=None
    if any(isinstance(candidate,dict) and candidate.get('observation_kind')=='wrapped_hint_receipt'
           for candidate in hint_card_observations):
        from .hint_card_rows import prepare as prepare_hint_rows
        outcome_readings,eligible_hints,hint_row_audit=prepare_hint_rows(
            readings,hint_card_observations,source_sha256=source_sha256)
    states=checkpoints(readings);events=training_events(readings,states)+outcome_events(outcome_readings)
    if hint_card_observations:
        from .hint_card_events import apply as apply_hint_cards
        hint_card_audit=apply_hint_cards(events,readings,eligible_hints,source_sha256=source_sha256)
        if hint_row_audit is not None:
            original_indices=hint_row_audit['eligible_input_indices']
            for channel in ('accepted','rejected'):
                hint_card_audit[channel]=[dict(entry,index=original_indices[entry['index']])
                                          for entry in hint_card_audit[channel]]
            hint_card_audit['rejected']+=deepcopy(hint_row_audit['rejected'])
            hint_card_audit['rejected'].sort(key=lambda entry:entry['index'])
            hint_card_audit['row_recovery']=hint_row_audit
    skills=skill_transactions(readings,states);events+=skills
    events.sort(key=lambda e:e['first_seen_ms'])
    intervals=[]
    for before,after in zip(states,states[1:]):
        resolutions=reconcile_visible_training_candidates(before,after,events,readings)
        candidates=[e for e in events if before['last_seen_ms']<e['first_seen_ms']<=after['first_seen_ms'] and e['deltas']]
        item=account(before,after,candidates)
        item['ambiguous_events']=[e['id'] for e in events if before['last_seen_ms']<e['first_seen_ms']<=after['first_seen_ms'] and e.get('conflicting_readings')]
        item['verified']=False
        item['state_supported_candidate_resolutions']=resolutions
        intervals.append(item)
    lessons=lesson_receipts(readings,[e for e in events if e['kind']=='outcome'])
    actions=training_actions(events)
    for event in events:
        if event['kind']!='outcome' or event.get('context_title')!='All Refreshed':continue
        confirmations=[r for r in readings if r['screen']=='rest_confirmation' and 0<event['first_seen_ms']-r['source_timestamp_ms']<=10000]
        if confirmations and any(e['kind']=='energy_change' and e['amount']>0 for e in event['effects']):
            actions.append(dict(kind='rest',source_timestamp_ms=event['first_seen_ms'],evidence=[confirmations[-1]['evidence'],event['evidence']],event_id=event['id'],click_timestamp_ms=None))
    actions+=outing_actions(readings,events)
    race_results=races(readings,race_reward_observations)
    actions += [dict(kind='race',source_timestamp_ms=r['first_seen_ms'],evidence=r['evidence'],race_id=r['id'],click_timestamp_ms=None) for r in race_results]
    actions.sort(key=lambda a:a['source_timestamp_ms'])
    from .mechanics_audit import fan_accounting,song_acquisitions,unparsed_receipt_candidates
    from .choice_evidence import reconstruct as reconstruct_choices,collect as collect_choices
    choice_rows=collect_choices(readings,choice_observations)
    return dict(checkpoints=states,events=events,intervals=intervals,
                **({'hint_card_recovery':hint_card_audit} if hint_card_observations else {}),
                dialogue_choices=reconstruct_choices(choice_rows),
                fan_accounting=fan_accounting(race_results,events),song_acquisitions=song_acquisitions(events,lessons),
                unparsed_receipt_candidates=unparsed_receipt_candidates(readings),
                lesson_purchases=lessons,skill_purchases=skills,concerts=concerts(readings,events,lessons),races=race_results,turn_action_receipts=actions,
                performance_accounting=performance_accounting(readings,events,lessons),
                lesson_debit_observations=lesson_transitions(readings))


def training_actions(events):
    """Completed training identity does not depend on readable reward digits."""
    return [dict(kind='training',training_option=e['training_option'],source_timestamp_ms=e['first_seen_ms'],
                 evidence=e['evidence'],event_id=e['id'],click_timestamp_ms=None,
                 action_identity_evidence=e.get('action_identity_evidence',[]),
                 training_outcome=e.get('training_outcome','unknown'),failure_evidence=e.get('failure_evidence',[]),
                 effect_coverage_verified=e['effect_coverage_verified'])
            for e in events if e['kind']=='training' and e['training_option']
            and (e['deltas'] or e.get('action_identity_observations',0)>=2)]


def outing_actions(readings,events):
    actions=[];used=set()
    for event in events:
        if event['kind']!='outcome':continue
        recovery=any(e['kind']=='energy_change' and e['amount']>0 for e in event['effects'])
        companions={e['name'] for e in event['effects'] if e['kind'] in ('friendship_change','friendship_status')}
        mood=any(e['kind']=='mood_change' and e.get('direction')=='up' for e in event['effects'])
        if not recovery and not (mood and len(companions)==1):continue
        requests=[r for r in readings if r['screen']=='outing_confirmation' and 0<event['first_seen_ms']-r['source_timestamp_ms']<=30000]
        if not requests:continue
        request=requests[-1];time=request['source_timestamp_ms']
        intervening=[r for r in readings if time<r['source_timestamp_ms']<event['first_seen_ms']]
        if any(r['screen'] in ('training_result','training_preview','race_result','rest_confirmation') for r in intervening):continue
        hubs=[r for r in intervening if r['screen']=='unknown' and r.get('stats',{}).get('values')]
        if any(0<b['source_timestamp_ms']-a['source_timestamp_ms']<=500 and a['stats']['values']==b['stats']['values']
               for a,b in zip(hubs,hubs[1:])):continue
        if time in used:continue
        extra=[]
        if not recovery:
            extra=outing_turn_evidence(readings,event,request)
            if not extra:continue
        used.add(time)
        actions.append(dict(kind='outing',source_timestamp_ms=event['first_seen_ms'],event_id=event['id'],
                            companion=companions.pop() if len(companions)==1 else None,
                            evidence=list(dict.fromkeys([request['evidence'],event['evidence']]+extra)),click_timestamp_ms=None,
                            basis='outing_request_followed_by_recovery_receipt_without_another_turn_action' if recovery else
                                  'outing_request_support_event_and_observed_next_date'))
    return actions


def outing_turn_evidence(readings,event,request):
    """Support outings need not award energy; require an observed turn transition."""
    from .calendar_coverage import date_key
    time=request['source_timestamp_ms'];title=event.get('context_title')
    if not title:return []
    confirmations=[r for r in readings if r['screen']=='outing_confirmation' and 0<=time-r['source_timestamp_ms']<=1000]
    if len({r['source_timestamp_ms'] for r in confirmations})<2:return []
    narrative=[r for r in readings if time<r['source_timestamp_ms']<event['first_seen_ms'] and r.get('context_title')==title]
    if len({r['source_timestamp_ms'] for r in narrative})<2 or narrative[0]['source_timestamp_ms']-time>3000:return []
    current={date_key(r.get('stats',{}).get('calendar_text')) for r in narrative}
    if len(current)!=1 or None in current:return []
    current=current.pop()
    after=[r for r in readings if event['last_seen_ms']<r['source_timestamp_ms']<=event['last_seen_ms']+5000]
    next_rows=[]
    for row in after:
        date=date_key(row.get('stats',{}).get('calendar_text'))
        if row['screen'] in ('training_result','race_result','rest_confirmation','outing_confirmation','training_preview'):return []
        if date is not None and date not in (current,current+1):return []
        if date==current+1:next_rows.append(row)
        elif row.get('stats',{}).get('values'):return []
        if len({r['source_timestamp_ms'] for r in next_rows})>=2:
            return [r['evidence'] for r in confirmations+narrative[:2]+next_rows]
    return []


def performance_accounting(readings,events,lessons):
    states=[];group=[]
    def finish():
        if len(group)>=3 and group[-1]['source_timestamp_ms']-group[0]['source_timestamp_ms']>=500:
            states.append(dict(first_seen_ms=group[0]['source_timestamp_ms'],last_seen_ms=group[-1]['source_timestamp_ms'],
                values=group[0]['facts']['performance_points'],evidence=group[0]['evidence'],verified=False))
    for row in readings:
        values=row['facts'].get('performance_points',{})
        valid=row['screen'] in ('lesson_selection','training_preview') and all(type(values.get(f)) is int for f in CURRENCIES)
        if not valid:
            finish();group=[];continue
        if group and (values!=group[-1]['facts']['performance_points'] or row['source_timestamp_ms']-group[-1]['source_timestamp_ms']>500):
            finish();group=[]
        group.append(row)
    finish();transactions=[]
    for event in events:
        if event['kind'] not in ('training','outcome'):continue
        deltas=event.get('performance_deltas',{}) if event['kind']=='training' else {e['field']:e['amount'] for e in event['effects'] if e['kind']=='performance_change'}
        if deltas:transactions.append(dict(source_timestamp_ms=event['first_seen_ms'],deltas=deltas,evidence=event['evidence']))
    for lesson in lessons:
        if lesson['performance_cost'] is not None:
            transactions.append(dict(source_timestamp_ms=lesson['source_timestamp_ms'],deltas={k:-v for k,v in lesson['performance_cost'].items()},evidence=lesson['evidence']))
    intervals=[]
    for before,after in zip(states,states[1:]):
        candidates=[t for t in transactions if before['last_seen_ms']<t['source_timestamp_ms']<=after['first_seen_ms']]
        observed={f:after['values'][f]-before['values'][f] for f in CURRENCIES}
        supported={f:sum(t['deltas'].get(f,0) for t in candidates) for f in CURRENCIES}
        residual={f:observed[f]-supported[f] for f in CURRENCIES}
        intervals.append(dict(start_ms=before['last_seen_ms'],end_ms=after['first_seen_ms'],observed_change=observed,
            supported_change=supported,unexplained_change=residual,status='unresolved' if any(residual.values()) else 'balanced',verified=False,transactions=candidates))
    return dict(checkpoints=states,intervals=intervals)


def races(readings,reward_observations=()):
    from .race_reward_sections import annotate as annotate_reward_sections
    groups=[]
    for row in readings:
        if row['screen']!='race_result':continue
        facts=row['facts'];key=(facts.get('fans'),facts.get('fans_gained'))
        if not groups or row['source_timestamp_ms']-groups[-1]['last_seen_ms']>1000 or key!=groups[-1]['_key']:
            groups.append(dict(id=f'race-{len(groups)+1:03d}',first_seen_ms=row['source_timestamp_ms'],last_seen_ms=row['source_timestamp_ms'],_key=key,rows=[]))
        groups[-1]['last_seen_ms']=row['source_timestamp_ms'];groups[-1]['rows'].append(row)
    for group in groups:
        rows=group.pop('rows');group.pop('_key');fields={};conflicts={}
        for field in ('race_name','placing','fans','fans_gained','course'):
            observations=[r['facts'].get(field) for r in rows if r['facts'].get(field) is not None]
            unique=[]
            for value in observations:
                if value not in unique:unique.append(value)
            fields[field]=unique[0] if len(unique)==1 else None
            if len(unique)>1:conflicts[field]=unique
        conditions=list(dict.fromkeys(r['facts']['course_condition'] for r in rows if r['facts'].get('course_condition')))
        if fields['course'] is not None:
            fields['course']=dict(fields['course'],condition=conditions[0] if len(conditions)==1 else None)
        if len(conditions)>1:conflicts['course.condition']=conditions
        group.update(fields,evidence=[r['evidence'] for r in rows],conflicting_readings=conflicts,completed_action='race',item_rewards_complete=False,verified=False)
        # Preserve stable visible snapshots; scrolling cannot establish item identity
        # or authorize summing repeated quantities into an inventory transaction.
        snapshots=[];pending=[]
        reward_rows=rows
        if reward_observations:
            from .race_reward_inspection import merge_reward_rows
            extras=[]
            for observation in reward_observations:
                if not group['first_seen_ms']<=observation['source_timestamp_ms']<=group['last_seen_ms']:continue
                facts=observation.get('facts',{})
                if observation['screen']=='race_result' and (facts.get('fans'),facts.get('fans_gained'))==(group['fans'],group['fans_gained']):
                    extras.append(observation)
                else:
                    # A non-result or different race interrupts quantity continuity.
                    extras.append(dict(observation,facts=dict(facts,visible_item_quantities=[])))
            reward_rows=merge_reward_rows(rows,extras)
        def finish_items():
            if len({r['source_timestamp_ms'] for r in pending})<2:return
            snapshots.append(dict(first_seen_ms=pending[0]['source_timestamp_ms'],
                last_seen_ms=pending[-1]['source_timestamp_ms'],
                items=[dict(quantity=x['quantity'],name=None,section=x['section']) for x in annotate_reward_sections(pending[0])],
                item_observations=[dict(source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence'],
                    items=annotate_reward_sections(r),
                    **({'inspection_evidence':list(dict.fromkeys(r['quantity_inspection_evidence']))}
                       if r.get('quantity_inspection_evidence') else {})) for r in pending],
                evidence=list(dict.fromkeys(r['evidence'] for r in pending)),
                identity_verified=False,list_complete=False))
        for row in sorted(reward_rows,key=lambda r:r['source_timestamp_ms']):
            items=annotate_reward_sections(row)
            previous=annotate_reward_sections(pending[-1]) if pending else []
            same=(len(items)==len(previous) and all(a['quantity']==b['quantity'] and
                a['section']==b['section'] and
                all(abs(x-y)<=8 for x,y in zip(a['box'],b['box'])) for a,b in zip(items,previous)))
            if pending and (not same or row['source_timestamp_ms']-pending[-1]['source_timestamp_ms']>500):
                finish_items();pending=[]
            if items:pending.append(row)
        finish_items()
        group['visible_item_reward_snapshots']=snapshots
    return groups


def active_bonus_snapshot(snapshots):
    """Retain observed current values separately from repeated confirmation."""
    values={};proofs={};observations={};unresolved={}
    for field in ('friendship_training_effectiveness','specialty_priority','support_chain_event_frequency'):
        rows=[r for r in snapshots if field in r['facts'].get('current_concert_bonuses',{})]
        observations[field]=[dict(value=r['facts']['current_concert_bonuses'][field],
                                  source_timestamp_ms=r['source_timestamp_ms'],evidence=r['evidence']) for r in rows]
        # The validated slot refinement has distinct source-frame support even
        # though its corrected OCR line is emitted on a single base row.
        if field=='support_chain_event_frequency':
            for row in rows:
                detail=row['facts'].get('concert_bonus_evidence',{}).get(field,{})
                times=detail.get('refinement_source_timestamps_ms',[])
                evidence=detail.get('refinement_evidence',[])
                if detail.get('refinement')!='concert_support_level' or len(times)!=len(evidence):continue
                if len(set(times))<3 or any(type(t) is not int for t in times):continue
                for time,proof in zip(times,evidence):
                    observations[field].append(dict(value=row['facts']['current_concert_bonuses'][field],
                        source_timestamp_ms=time,evidence=proof,basis='validated_concert_slot_refinement'))
        numbers={o['value'] for o in observations[field]}
        times={o['source_timestamp_ms'] for o in observations[field]}
        if len(numbers)==1 and len(times)>=2:
            values[field]=next(iter(numbers));proofs[field]=list(dict.fromkeys(o['evidence'] for o in observations[field]))
        else:
            unresolved[field]='not_observed' if not rows else ('conflicting_observations' if len(numbers)>1 else 'insufficient_distinct_timestamps')
    return dict(values=values,evidence=proofs,complete=len(values)==3,
                observations=observations,unresolved_fields=unresolved)


def concerts(readings,events,lessons):
    result=[]
    for span in screen_summary(readings):
        if span['screen']!='concert_result':continue
        if result and span['first_seen_ms']-result[-1]['last_seen_ms']<=1000:
            result[-1]['last_seen_ms']=span['last_seen_ms'];continue
        rewards=[e for e in events if e['kind']=='outcome' and 0<e['first_seen_ms']-span['last_seen_ms']<=15000
            and re.fullmatch(r'The (?:First|Second|Third|Fourth|Grand) Concert Ends!',e.get('context_title') or '',re.I)]
        association='named_concert_aftermath'
        if not rewards:
            adjacent=[e for e in events if e['kind']=='outcome' and 0<e['first_seen_ms']-span['last_seen_ms']<=5000
                      and not e.get('context_title') and set(e['deltas'])==set(FIELDS)
                      and any(f['kind']=='fan_change' for f in e['effects'])]
            if len(adjacent)==1:rewards=adjacent;association='adjacent_untitled_stat_and_fan_receipt'
        title=rewards[0]['context_title'] if len(rewards)==1 else None
        previous=result[-1]['last_seen_ms'] if result else 0
        queued=[dict(lesson_id=l['id'],song=l['name'],effect=e['raw_text'],field=e.get('field'),amount=e.get('amount'),activation_evidence_verified=False)
                for l in lessons if previous<l['source_timestamp_ms']<span['first_seen_ms']
                for e in l['projected_effects'] if e['kind']=='queued_concert_bonus']
        result.append(dict(id=f'concert-{len(result)+1:02d}',first_seen_ms=span['first_seen_ms'],last_seen_ms=span['last_seen_ms'],
            result='great_success',title=title,reward_event_ids=[e['id'] for e in rewards],
            reward_association=association if rewards else None,
            observed_rewards=[e['effects'] for e in rewards],queued_bonus_candidates=queued,
            evidence=[span['confirmation_evidence'],span['evidence']]+[e['evidence'] for e in rewards],
            complete_rewards_verified=False,bonus_activation_verified=False))
    for index,concert in enumerate(result):
        end=result[index+1]['first_seen_ms'] if index+1<len(result) else float('inf')
        updates=[r for r in readings if r['screen']=='concert_bonus_update' and concert['last_seen_ms']<r['source_timestamp_ms']<min(end,concert['last_seen_ms']+90000)]
        if updates:
            concert['bonus_activation_verified']=True
            concert['bonus_update_receipt']=dict(first_seen_ms=updates[0]['source_timestamp_ms'],last_seen_ms=updates[-1]['source_timestamp_ms'],evidence=[r['evidence'] for r in updates])
            concert['activation_scope']='The update receipt confirms the activation step; it does not enumerate every bonus value.'
        snapshots=[r for r in readings if r['screen']=='concert_info' and updates and updates[-1]['source_timestamp_ms']<r['source_timestamp_ms']<end]
        concert['later_active_bonus_snapshot']=active_bonus_snapshot(snapshots)
        concert['all_bonus_totals_verified']=False
    return result
