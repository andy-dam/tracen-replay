"""Evidence-linked transactions and state transitions; no balancing by invented deltas."""
from collections import Counter
from copy import deepcopy
import math
import re
import unicodedata
from .reconcile import FIELDS,stable_checkpoints,account
from .gameplay import lesson_transitions, CURRENCIES, receipt_rows, screen_summary
from .skill_chains import cart_bundles,reconcile_skill_chains
from .receipt_continuity import collapse_cross_event_hint_duplicates
from .receipt_stat_continuity import collapse_cross_event_stat_duplicates
from .training_gain_phases import (
    direct_gain_proof,
    distinct_gain_observations,
    resolve_full_component_phase,
    resolve_source_temporal_phase,
    source_gain_observations,
    source_result_projection_proof,
    source_result_projection_belongs_to_group,
    is_committed_training_result_row,
    stable_trailing_result_counter_suffix,
    stable_trailing_result_suffix,
)
from .training_identity import summarize as summarize_training_identity
from .training_gain_resolution import candidate_recovery_policy
from .ocr_confidence import confidence_percent
from .source_clock import elapsed


_RACE_GRADE_RE = re.compile(r'^(?:DEBUT|G[123]|OP|PRE[- ]?OP|EX)$', re.I)


def _normalized_race_grade(value):
    """Accept only the finite grade vocabulary emitted by result OCR."""

    if not isinstance(value, str):
        return None
    value = re.sub(r'\s+', ' ', value.strip()).upper()
    if value == 'PRE OP':
        value = 'PRE-OP'
    return value if _RACE_GRADE_RE.fullmatch(value) else None


def skill_point_states(readings,states):
    """A visible completion-hub SP counter need not expose all five attributes.

    The counter is static text (no count-up), so the value read the same on
    two consecutive frames is the balance, the standard every reading meets;
    a hub the player leaves within half a second still shows it.
    """
    result=list(states);group=[]
    def finish():
        if len({r['source_timestamp_ms'] for r in group})<2:return
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
                      or not 0<elapsed(group[-1]['source_timestamp_ms'],row['source_timestamp_ms'])<=500):
            finish();group=[]
        if eligible:group.append(row)
    finish()
    return sorted(result,key=lambda s:(s['last_seen_ms'],s['first_seen_ms']))


def _skill_evidence(value):
    """Return unique source paths without reducing them to basenames."""
    if isinstance(value,str): values=[value]
    elif isinstance(value,(list,tuple)): values=value
    else: values=[]
    return list(dict.fromkeys(v.replace('\\','/') for v in values if isinstance(v,str) and v.strip()))


def _skill_physical_ids(row):
    """Return stable source identities for repetition checks.

    A timestamp or copied evidence path alone is not a second observation.
    Prefer image digests when a reader provides one; otherwise use the
    normalized source path as the physical identity.
    """
    if not isinstance(row,dict):return set()
    for key in ('source_frame_sha256','source_image_sha256','evidence_sha256'):
        value=row.get(key)
        if isinstance(value,str) and value.strip():return {f'{key}:{value.strip()}'}
    return {f'path:{path}' for path in _skill_evidence(row.get('evidence'))}


def _skill_ocr_lines(row):
    ocr=row.get('ocr') if isinstance(row,dict) else None
    if isinstance(ocr,dict):
        lines=ocr.get('neural',ocr.get('lines',[]))
    else: lines=ocr
    return lines if isinstance(lines,list) else []


def _skill_card_rows(row):
    facts=row.get('facts') if isinstance(row,dict) else None
    cards=facts.get('skill_cards',[]) if isinstance(facts,dict) else []
    return cards if isinstance(cards,list) else []


def _receipt_text_candidates(readings,span):
    """Read exact receipt wording only inside the committed receipt span.

    The first frame in a modal can clip the leading ``Y`` (``our`` versus
    ``Your``).  These two spellings are retained as observations, while the
    earliest exact source line is the canonical value.  Other wording is not
    normalized into a receipt claim.
    """
    start,end=span.get('first_seen_ms'),span.get('last_seen_ms')
    if type(start) is not int or type(end) is not int or start>end:return []
    pattern=re.compile(r'^(?:our|your)\s+trainee\s+learned\s+new\s+skills!$',re.I)
    result=[]
    for row in readings:
        timestamp=row.get('source_timestamp_ms') if isinstance(row,dict) else None
        if (row.get('screen')!='skill_receipt' or type(timestamp) is not int
                or not start<=timestamp<=end):continue
        for line in _skill_ocr_lines(row):
            if not isinstance(line,dict) or not isinstance(line.get('text'),str):continue
            value=' '.join(line['text'].split()).strip()
            if not pattern.fullmatch(value):continue
            confidence=line.get('confidence')
            if type(confidence) not in (int,float) or confidence<90:continue
            result.append(dict(text=value,confidence=confidence,
                               source_timestamp_ms=timestamp,
                               evidence=_skill_evidence(row.get('evidence')),
                               box=line.get('box')))
    return result


def _post_learn_obtained_provenance(readings,confirmation,span,confirmation_names):
    """Find obtained inventory cards after the committed receipt.

    The parser's ``obtained_or_selected`` status deliberately does not claim
    whether a card was selected in a draft cart or already owned.  A card is
    therefore eligible here only in the bounded inventory context after the
    receipt span, where its status is retained as an unresolved parser fact.
    This field describes visible post-learn inventory; it is not an individual
    charge or a proof that the card was absent before the purchase.
    """
    receipt_end=span.get('last_seen_ms')
    if type(receipt_end) is not int:return []
    upper=receipt_end+1500
    grouped={}
    for row in readings:
        timestamp=row.get('source_timestamp_ms') if isinstance(row,dict) else None
        if (row.get('screen')!='skill_selection' or type(timestamp) is not int
                or not receipt_end<timestamp<=upper):continue
        evidence=_skill_evidence(row.get('evidence'))
        for card in _skill_card_rows(row):
            if not isinstance(card,dict) or card.get('menu_status')!='obtained_or_selected':continue
            name=card.get('name')
            if not isinstance(name,str) or not name.strip() or not evidence:continue
            grouped.setdefault(name.strip(),[]).append(dict(
                source_timestamp_ms=timestamp,evidence=evidence,
                physical_ids=_skill_physical_ids(row),
                name_box=card.get('name_box'),menu_status=card.get('menu_status')))
    result=[]
    for name,observations in grouped.items():
        times={item['source_timestamp_ms'] for item in observations}
        evidence={path for item in observations for path in item['evidence']}
        physical_ids={source_id for item in observations for source_id in item['physical_ids']}
        if not times or not evidence or not physical_ids:continue
        result.append(dict(name=name,first_seen_ms=min(times),last_seen_ms=max(times),
                           observation_count=len(observations),
                           evidence=sorted(evidence),
                           basis='post_receipt_inventory_obtained_or_selected_card',
                           observed_menu_status='obtained_or_selected',
                           ownership_status='post_receipt_inventory_observed',
                           acquisition_verified=False))
    return sorted(result,key=lambda item:(item['first_seen_ms'],item['name']))


def _partial_price_provenance(readings,confirmation,span,before,allowed_names):
    """Record committed and pre-commit price visibility separately.

    A status read from the confirmation or receipt belongs to the committed
    batch.  Card prices read while the selection menu is open are preview
    evidence, even when they later help identify a selected card.  The latter
    is kept as provenance without turning a menu offer into a batch price or
    charge.  An absent OCR price never becomes ``unreadable`` by inference.
    """
    confirm_start=confirmation.get('first_seen_ms')
    receipt_end=span.get('last_seen_ms') if isinstance(span,dict) else None
    if type(confirm_start) is not int or type(receipt_end) is not int:return None
    lower=before.get('last_seen_ms') if isinstance(before,dict) else None
    if type(lower) is not int:lower=confirm_start-15000
    explicit_committed=[]
    explicit_precommit=[]
    valid_statuses={'partially_visible','unreadable','unknown'}
    for row in readings:
        timestamp=row.get('source_timestamp_ms') if isinstance(row,dict) else None
        if type(timestamp) is not int or not lower<timestamp<=receipt_end:continue
        screen=row.get('screen')
        if screen not in ('skill_selection','skill_confirmation','skill_receipt'):continue
        # A committed status must be observed after this batch's confirmation
        # begins.  The broad transaction span can contain a canceled/previous
        # confirmation or receipt; accepting it would attach that status to
        # the current purchase.  Selection rows remain preview-only.
        if screen == 'skill_selection':
            if timestamp >= confirm_start:continue
        elif timestamp < confirm_start:
            continue
        facts=row.get('facts')
        status=facts.get('price_status') if isinstance(facts,dict) else None
        if status not in valid_statuses:continue
        item=dict(status=status,source_timestamp_ms=timestamp,
                  evidence=_skill_evidence(row.get('evidence')))
        (explicit_precommit if screen == 'skill_selection'
         else explicit_committed).append(item)

    result={}

    def explicit_record(items):
        statuses={item['status'] for item in items}
        if len(statuses)!=1:
            return None
        evidence=list(dict.fromkeys(path for item in items for path in item['evidence']))
        if not evidence:
            return None
        return dict(status=next(iter(statuses)),evidence=evidence,
                    basis='explicit_source_price_status',observed_card_count=0,
                    observed_timestamps=sorted({item['source_timestamp_ms'] for item in items}))

    committed=explicit_record(explicit_committed) if explicit_committed else None
    if committed:
        result['committed']=committed

    # A selection-screen status is scoped to the preview.  Conflicting
    # explicit statuses stay unresolved and suppress the derived fallback.
    precommit=explicit_record(explicit_precommit) if explicit_precommit else None
    if precommit:
        result['precommit']=precommit
        return result
    elif explicit_precommit:
        return result or None

    allowed={name.strip() for name in allowed_names if isinstance(name,str) and name.strip()}
    if not allowed:return result or None
    grouped={}
    for row in readings:
        timestamp=row.get('source_timestamp_ms') if isinstance(row,dict) else None
        if (row.get('screen')!='skill_selection' or type(timestamp) is not int
                or not lower<timestamp<confirm_start):continue
        facts=row.get('facts')
        if not isinstance(facts,dict) or facts.get('item_list_complete') is not False:continue
        evidence=_skill_evidence(row.get('evidence'))
        for card in _skill_card_rows(row):
            if not isinstance(card,dict) or type(card.get('displayed_cost')) is not int:continue
            cost=card['displayed_cost']
            if cost<=0:continue
            name=card.get('name') if isinstance(card.get('name'),str) else None
            if name is None or name.strip() not in allowed:continue
            key=(name,cost)
            grouped.setdefault(key,[]).append(dict(source_timestamp_ms=timestamp,evidence=evidence,
                                                    physical_ids=_skill_physical_ids(row)))
    if not grouped:return result or None
    timestamps={item['source_timestamp_ms'] for rows in grouped.values() for item in rows}
    evidence_paths={path for rows in grouped.values() for item in rows for path in item['evidence']}
    physical_ids={source_id for rows in grouped.values() for item in rows for source_id in item['physical_ids']}
    if len(timestamps)<2 or len(evidence_paths)<2 or len(physical_ids)<2:return result or None
    evidence=[]
    for key in sorted(grouped,key=lambda item:(item[0] or '',item[1])):
        rows=grouped[key]
        evidence.extend(rows[0]['evidence'])
        if len(rows)>1:evidence.extend(rows[-1]['evidence'])
    evidence=list(dict.fromkeys(evidence))
    result['precommit']=dict(status='partially_visible',evidence=evidence,
                             basis='visible_skill_card_prices_with_incomplete_item_list',
                             observed_card_count=len(grouped),
                             observed_timestamps=sorted(timestamps))
    return result


def _purchased_list(transaction):
    """Whether the cart names every purchased skill, from the batch's own fields.

    The confirmation list scrolls, so the names read off it never prove the
    whole purchase. The cart does: when every change of the cart counter was
    assigned to a bundle with one named target and those bundles sum to the
    charge the receipt read (or the chain between observed balances worked
    out), every purchased skill is named by a bundle, and the list is
    complete; ``purchased_skill_names`` then holds the bundle targets with
    the names the confirmation showed. An identity marked ambiguous only for
    the scrolling list is cleared with it.
    """
    spent=transaction.get('spent_skill_points')
    bundles=transaction.get('committed_cart_bundles') or []
    assigned=(type(spent) is int and sum(b['net_cost'] for b in bundles)==spent
              and not transaction.get('unassigned_cart_changes'))
    names=transaction.get('visible_confirmation_names') or []
    # The cart's net cost is what the purchase charged. When the names the
    # confirmation showed each have a price and those prices sum to exactly
    # that, nothing scrolled off the list: the visible names are the whole
    # purchase.
    priced={c['name']:c['cost'] for c in transaction.get('selected_item_candidates') or []
            if isinstance(c,dict) and c.get('basis')=='visible_confirmation_and_price'
            and isinstance(c.get('name'),str) and type(c.get('cost')) is int}
    net=transaction.get('cart_net_cost')
    by_cost=(not assigned and bool(names) and type(net) is int and net>0
             and all(n in priced for n in names) and sum(priced[n] for n in names)==net)
    transaction.update(purchased_list_complete=assigned or by_cost,
        purchased_list_basis=('cart_bundles_account_for_the_whole_charge' if assigned else
                              'visible_names_cost_the_cart_net' if by_cost else 'confirmation_list_may_scroll'),
        purchased_skill_names=sorted(set(names)|{b['target_name'] for b in bundles}) if assigned else sorted(set(names)) if by_cost else None)
    if assigned and transaction.get('identity_status_basis')=='incomplete_skill_confirmation_list':
        for key in ('identity_status','identity_status_evidence','identity_status_basis'):transaction.pop(key,None)


def _attach_skill_batch_metadata(transaction,readings,confirmation,span,before):
    """Attach source-bound metadata that the receipt transaction already owns."""
    names=transaction.get('visible_confirmation_names',[])
    metadata_evidence=[]
    if names and transaction.get('purchased_list_complete') is False:
        evidence=_skill_evidence(confirmation.get('supporting_frames',confirmation.get('evidence')))
        if evidence:
            transaction.update(identity_status='ambiguous',
                               identity_status_evidence=evidence,
                               identity_status_basis='incomplete_skill_confirmation_list')
            metadata_evidence.extend(evidence)

    obtained=_post_learn_obtained_provenance(readings,confirmation,span,names)
    if obtained:
        transaction['post_learn_obtained_names']=[item['name'] for item in obtained]
        transaction['post_learn_obtained_name_evidence']={item['name']:item['evidence'] for item in obtained}
        transaction['post_learn_obtained_name_provenance']=obtained
        metadata_evidence.extend(path for item in obtained for path in item['evidence'])

    allowed_names=set(names)
    allowed_names.update(item.get('name') for item in transaction.get('selected_item_candidates',[]))
    prices=_partial_price_provenance(readings,confirmation,span,before,allowed_names)
    if prices:
        committed=prices.get('committed')
        if committed:
            transaction['price_status']=committed['status']
            transaction['price_status_evidence']=committed['evidence']
            transaction['price_status_basis']=committed['basis']
            transaction['price_status_observed_card_count']=committed['observed_card_count']
            transaction['price_status_observed_timestamps']=committed['observed_timestamps']
            metadata_evidence.extend(committed['evidence'])
        precommit=prices.get('precommit')
        if precommit:
            transaction['precommit_price_visibility'] = dict(
                phase='preview', status=precommit['status'],
                evidence=precommit['evidence'], basis=precommit['basis'],
                observed_card_count=precommit['observed_card_count'],
                observed_timestamps=precommit['observed_timestamps'])
            metadata_evidence.extend(precommit['evidence'])

    receipt_texts=_receipt_text_candidates(readings,span)
    if receipt_texts:
        receipt_texts=sorted(receipt_texts,key=lambda item:(item['source_timestamp_ms'],-item['confidence'],item['text']))
        chosen=receipt_texts[0]
        if chosen['evidence']:
            transaction['confirmation_text']=chosen['text']
            transaction['confirmation_text_evidence']=chosen['evidence']
            transaction['confirmation_text_basis']='skill_receipt_exact_text'
            transaction['confirmation_text_observations']=receipt_texts
            transaction['confirmation_text_variants']=list(dict.fromkeys(item['text'] for item in receipt_texts))
            metadata_evidence.extend(chosen['evidence'])

    if metadata_evidence:
        transaction['evidence']=list(dict.fromkeys(_skill_evidence(transaction.get('evidence'))+metadata_evidence))


def skill_transactions(readings,states):
    """Only receipt-backed batches; menu counters are never independently charged."""
    states=skill_point_states(readings,states)
    spans=screen_summary(readings);transactions=[]
    for span in spans:
        if span['screen']!='skill_receipt' or span.get('completed_action')!='skill_purchase_batch':continue
        if transactions and elapsed(transactions[-1]['last_seen_ms'],span['first_seen_ms'])<=500:
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
                    if tail and (row['facts']['displayed_skill_points']!=tail[-1]['facts']['displayed_skill_points'] or elapsed(row['source_timestamp_ms'],tail[-1]['source_timestamp_ms'])>500):break
                    tail.append(row)
                cart=list(reversed(tail))
            post=[r for r in readings if r['screen']=='skill_selection' and 0<elapsed(span['last_seen_ms'],r['source_timestamp_ms'])<=1500 and type(r['facts'].get('displayed_skill_points')) is int]
            numbers={r['facts']['displayed_skill_points'] for r in cart+post}
            intervening=[r for r in readings if before['last_seen_ms']<r['source_timestamp_ms']<span['first_seen_ms']]
            unsafe=any(r['screen'] in ('training_result','skill_receipt') or any(e['kind']=='stat_change' and e['field']=='skill_points' for e in r['effects']) for r in intervening)
            if selections and selections[0]['source_timestamp_ms']-before['last_seen_ms']<=5000 and len(cart)>=2 and len(post)>=2 and len(numbers)==1 and not unsafe:
                value=numbers.pop();difference=before['values']['skill_points']-value
                if difference>0:
                    spent=difference;counter_proofs=[cart[-1]['evidence'],post[0]['evidence'],post[-1]['evidence']]
                    basis='receipt_with_matching_cart_and_post_receipt_balance'
                    # Preserve the same post-receipt observation that already
                    # supports this debit; consumers must not reconstruct it
                    # by subtracting the charge from the opening balance.
                    after=dict(first_seen_ms=post[0]['source_timestamp_ms'],
                               last_seen_ms=post[-1]['source_timestamp_ms'],
                               values={'skill_points':value},
                               evidence=post[0]['evidence'],
                               supporting_frames=[r['evidence'] for r in post],
                               basis='receipt_with_matching_cart_and_post_receipt_balance')
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
            if active and (value!=active[-1]['facts']['displayed_skill_points'] or elapsed(active[-1]['source_timestamp_ms'],row['source_timestamp_ms'])>500):
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
        _purchased_list(transactions[-1])
        _attach_skill_batch_metadata(transactions[-1],readings,confirmation,span,before)
    transactions=reconcile_skill_chains(transactions,readings,spans)
    # The chain can fill in a charge the receipt did not read; the list's
    # completeness follows the charge, so it is worked out again.
    for transaction in transactions:_purchased_list(transaction)
    return transactions


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
    original=effect.get('original_text')
    if method=='isolated_note_stem_flag_head_and_closing_quote':
        # The direct source-pixel path proves an omitted note at the receipt's
        # suffix.  Preserve the raw OCR title as an alias for a confirmation
        # that still omits the glyph; do not accept a generic UI arrow or a
        # marker without the exact source-symbol proof envelope.
        if proof.get('symbol')!='♪' or proof.get('independent_observations') is not False:
            return None
        if not isinstance(original,str):return None
        match=re.fullmatch(r'Learned the song (?:["“])(.+?)(?:["”])([.!])',original)
        if not match:return None
        raw_title=match[1]
        name=re.sub(r'\s*[>▶→]\s*$','',raw_title).rstrip()
        if not name or effect.get('name')!=name+' ♪':return None
        return name
    title=proof.get('title_evidence',{})
    if not isinstance(title,dict):return None
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


def _receipt_name_key(value):
    if not isinstance(value,str):return ''
    normalized=unicodedata.normalize('NFKD',value).casefold()
    return ''.join(char for char in normalized if char.isalnum())


def _recoverable_receipt_name_variant(canonical,variant,anchored=False):
    """Recognize a bounded OCR typo beside an exact source name.

    The exact name must already be present in the receipt/request pair.  This
    helper only decides whether an additional spelling can be retained as an
    OCR variant; it never turns two unrelated acquisitions into one purchase.
    """
    if not isinstance(canonical,str) or not isinstance(variant,str) or canonical==variant:return False
    canonical_key=_receipt_name_key(canonical);variant_key=_receipt_name_key(variant)
    if not canonical_key or not variant_key:return False
    # Beside an exact receipt spelling the first word must match; when the
    # request dialog itself names the item, the whole bounded distance rules.
    if not anchored and canonical.casefold().split()[0]!=variant.casefold().split()[0]:return False
    # An extra or missing word (a sequel number, an edition, a stray letter)
    # is another item, not a misread of this one.  A space the recognizer put
    # inside a word is not an extra word: it splits what is there instead of
    # adding to it, so a differing word count is allowed only while the
    # variant carries no more characters than the name it claims to be.
    if anchored and (len(canonical.split())!=len(variant.split())
                     and len(variant_key)>len(canonical_key)):return False
    if anchored and any(ch.isdigit() for ch in canonical)!=any(ch.isdigit() for ch in variant):return False
    previous=list(range(len(variant_key)+1))
    for index,char in enumerate(canonical_key,1):
        current=[index]
        for other_index,other in enumerate(variant_key,1):
            current.append(min(current[-1]+1,previous[other_index]+1,
                               previous[other_index-1]+(char!=other)))
        previous=current
    distance=previous[-1]
    return distance<=max(2,min(4,(max(len(canonical_key),len(variant_key))+8)//9))


def _collapse_recoverable_acquisition_variants(event,canonical):
    """Collapse exact-name plus bounded OCR variants on a committed receipt."""
    acquired=[effect for effect in event.get('effects',[])
              if effect.get('kind') in ('named_acquisition','song_learned')]
    exact=[effect for effect in acquired if effect.get('name')==canonical]
    variants=[effect for effect in acquired if effect.get('name')!=canonical]
    if len(exact)!=1 or not variants or not all(
            _recoverable_receipt_name_variant(canonical,effect.get('name'),anchored=True) for effect in variants):
        return False
    variant_ids={id(effect) for effect in variants}
    event['effects']=[effect for effect in event.get('effects',[])
                      if id(effect) not in variant_ids]
    variant_evidence=[]
    field_evidence=event.get('field_evidence',{})
    for effect in variants:
        key='|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))
        variant_evidence.append(dict(name=effect.get('name'),
                                     evidence=list(field_evidence.get(key,[]))))
    event.setdefault('resolved_acquisition_name_variants',[]).append(dict(
        canonical_name=canonical,discarded_names=[effect.get('name') for effect in variants],
        discarded_evidence=variant_evidence,
        evidence=event.get('evidence'),basis='exact_receipt_name_with_bounded_ocr_variants'))
    return True


def repeated_projection(group):
    """The leftover balance a request run shows per currency, or None.

    A value read on two or more frames wins over a value read once: the
    dialog's fading last frame can drop a digit or read a stray dim 0. Two
    values that each repeat, or two singletons, stay unresolved.
    """
    projected={}
    for field in CURRENCIES:
        counts=Counter(r['facts'].get('projected_performance_points',{}).get(field) for r in group
                       if isinstance(r['facts'].get('projected_performance_points',{}),dict))
        counts.pop(None,None)
        repeated=[v for v,n in counts.items() if n>=2]
        singles=[v for v,n in counts.items() if n==1]
        if len(counts)==1:projected[field]=next(iter(counts))
        elif len(repeated)==1 and len(singles)==len(counts)-1:projected[field]=repeated[0]
        else:projected[field]=None
    return projected


def first_debited_frame_ms(after_rows, matched):
    """The debit is observed where the new balance first shows; its repeats confirm it.

    ``matched`` may be the repeat of a pair or a later panel; any earlier
    row of ``after_rows`` showing the same balance is the observation.
    """
    if not isinstance(matched, dict) or type(matched.get('source_timestamp_ms')) is not int:
        return None
    balance = matched.get('facts', {}).get('performance_points')
    earlier = [r['source_timestamp_ms'] for r in after_rows
               if type(r.get('source_timestamp_ms')) is int and r['source_timestamp_ms'] <= matched['source_timestamp_ms']
               and r.get('facts', {}).get('performance_points') == balance]
    return min(earlier, default=matched['source_timestamp_ms'])


def _cut_read_of(read,whole):
    """Whether a number read is the whole number with its leading or trailing digits hidden."""
    if type(read) is not int or type(whole) is not int or read==whole or read<0:return False
    return str(whole).startswith(str(read)) or str(whole).endswith(str(read))


def observed_lesson_debit(readings, event, group, before_rows, after_rows, name):
    """Recover a debit from repeated actual balances, without filling projections."""
    if len(before_rows)<2 or len(after_rows)<2:return None
    # A balance at zero is drawn dim and the menu reads nothing there; when
    # the request dialog projected that currency as zero on every frame it
    # showed, the empty slot on both menus is that zero.
    projected_zero={k for k in CURRENCIES
                    if group and all(isinstance(r['facts'].get('projected_performance_points',{}),dict)
                                     and r['facts']['projected_performance_points'].get(k)==0 for r in group)}
    def complete(r):
        values=r['facts'].get('performance_points',{})
        return all((type(values.get(k)) is int and values[k]>=0) or (values.get(k) is None and k in projected_zero) for k in CURRENCIES)
    def pair(rows):
        # The cursor can hide one counter on a frame; the repeated balance is
        # the nearest pair of complete, equal frames within half a second.
        for a,b in zip(rows,rows[1:]):
            if complete(a) and complete(b) and 0<elapsed(a['source_timestamp_ms'],b['source_timestamp_ms'])<=500 \
                    and a['facts']['performance_points']==b['facts']['performance_points']:
                return [a,b]
        return None
    before=pair(before_rows[-4:]);after=pair(after_rows[:4])
    if before is None or after is None:return None
    def repeated_balance(rows):
        values=rows[0]['facts']['performance_points']
        return {k:(0 if values.get(k) is None and k in projected_zero else values[k]) for k in CURRENCIES}
    initial,final=repeated_balance(before),repeated_balance(after)
    if initial is None or final is None:return None
    # The run's repeated projection must agree with the observed balance; a
    # missing number is not replaced with an inferred OCR reading, a value
    # read once beside a repeated one is the fading frame's misread, and two
    # repeated values cannot vote.
    if any(not isinstance(r['facts'].get('projected_performance_points',{}),dict) for r in group):return None
    projected=repeated_projection(group)
    for k,v in projected.items():
        if v is not None and (type(v) is not int or v!=final[k]):return None
    for r in group:
        for k,v in r['facts'].get('projected_performance_points',{}).items():
            # A leftover read with a digit cut ("11" of 111 under the cursor
            # on frame after frame) is the observed balance cut short, not
            # a disagreement with it.
            if k in CURRENCIES and v is not None and projected.get(k) is None and (
                    type(v) is not int or (v!=final[k] and not _cut_read_of(v,final[k]))):return None
    acquired=[e for e in event['effects'] if e['kind'] in ('named_acquisition','song_learned')]
    exact=[e for e in acquired if e.get('name')==name]
    variants=[e for e in acquired if e.get('name')!=name]
    if len(exact)!=1 or any(not _recoverable_receipt_name_variant(name,e.get('name')) for e in variants):return None
    # Spellings already resolved as variants of this receipt's name still sit
    # on the individual frames; they are this acquisition, not another one.
    aliases={name}|{e.get('name') for e in variants}|{n for r in event.get('resolved_acquisition_name_variants',[])
                                                       for n in (r.get('discarded_names') or [])}
    # The request dialog shows a song title without its note glyph; that
    # plain title is this receipt's own request, not another item.
    alias=source_song_alias(exact[0]) if exact[0].get('kind')=='song_learned' else None
    if alias:aliases.add(alias)
    first=min(r['source_timestamp_ms'] for r in group)
    last=max(r['source_timestamp_ms'] for r in group)
    span=[r for r in readings if before[-1]['source_timestamp_ms']<=r['source_timestamp_ms']<=after[-1]['source_timestamp_ms']]
    if any(not 0<elapsed(a['source_timestamp_ms'],b['source_timestamp_ms'])<=500 for a,b in zip(span,span[1:])):return None
    returned=[]
    for r in span:
        t=r['source_timestamp_ms']
        if r['screen'] not in ('lesson_selection','lesson_confirmation','event_outcome','unknown'):return None
        if r['facts'].get('awarded_performance_gains'):return None
        if r['facts'].get('performance_points') and r['screen']!='lesson_selection':return None
        if r['screen']=='lesson_confirmation':
            candidates=[n for n in (r['facts'].get('name_candidates') or []) if not (isinstance(n,str) and len(n.strip())<=2)]
            if not first<=t<=last or not (candidates==[] or (len(candidates)==1 and (
                    candidates[0] in aliases or partial_song_name(candidates[0],name)))):return None
        if last<t<event['first_seen_ms'] and r['screen']=='lesson_selection':returned.append(r)
        if any(e['kind']=='performance_change' or
               (e['kind'] in ('named_acquisition','song_learned') and
                (e.get('name') not in aliases or not event['first_seen_ms']<=t<=event['last_seen_ms'])) for e in r['effects']):return None
    # A single transition frame can show the menu beneath the receipt. A
    # sustained return to the menu is cancellation/another visit, not proof.
    if len(returned)>1 or returned and elapsed(returned[0]['source_timestamp_ms'],event['first_seen_ms'])>500:return None
    cost={k:initial[k]-final[k] for k in CURRENCIES}
    if any(v<0 for v in cost.values()) or not any(v>0 for v in cost.values()):return None
    return dict(cost=cost,matched=after[-1],proofs=[dict(role=role,values=values,
        timestamps_ms=[r['source_timestamp_ms'] for r in rows],evidence=[r['evidence'] for r in rows])
        for role,values,rows in (('before',initial,before),('after',final,after))])


def lesson_receipts(readings, outcomes):
    """Join named acquisition evidence to a matching request, retaining cost gaps."""
    purchases=[];used=set()
    for event in outcomes:
        acquired=[e for e in event['effects'] if e['kind'] in ('named_acquisition','song_learned')]
        # A song title's note glyph is read two ways on one receipt: proven as
        # the note by the source pixels on some frames, and as a stray letter
        # ('Present March D') on others. The stray-letter spelling is the
        # unproven read of the same title; drop it beside the proven one.
        proven=[e for e in acquired if e['kind']=='song_learned' and source_song_alias(e)]
        if len(proven)==1 and len(acquired)>1:
            alias=source_song_alias(proven[0])
            strays=[e for e in acquired if e is not proven[0] and e['kind']=='song_learned'
                    and not e.get('visual_symbol_observation') and partial_song_name(alias,e.get('name'))]
            if strays and len(strays)==len(acquired)-1:
                drop={id(e) for e in strays}
                event['effects']=[e for e in event['effects'] if id(e) not in drop]
                event.setdefault('resolved_acquisition_name_variants',[]).append(dict(
                    canonical_name=proven[0].get('name'),discarded_names=[e.get('name') for e in strays],
                    evidence=event.get('evidence'),basis='proven_note_glyph_with_stray_letter_variant'))
                acquired=[proven[0]]
        # A committed request gives us the canonical owner.  Keep an exact
        # acquisition and collapse only bounded OCR variants beside it; a
        # second unrelated acquisition remains a hard ownership conflict.
        for effect in list(acquired):
            exact_confirmations=[r for r in readings if r['screen']=='lesson_confirmation'
                and 0<event['first_seen_ms']-r['source_timestamp_ms']<=5000
                and r['facts'].get('name_candidates')==[effect.get('name')]]
            if exact_confirmations and _collapse_recoverable_acquisition_variants(event,effect.get('name')):
                acquired=[e for e in event['effects'] if e['kind'] in ('named_acquisition','song_learned')]
                break
        else:
            # No frame of the receipt read the name exactly. The request
            # dialog just before it did; when every receipt spelling is a
            # bounded OCR variant of that one confirmed name, the receipt
            # is for it. Only the last request run before the receipt is that
            # dialog: a card the player opened and left earlier in the same
            # five seconds is a different request, not a second name for this
            # one. Two names inside that one run still leave the receipt alone.
            named=[r for r in readings if r['screen']=='lesson_confirmation'
                   and 0<event['first_seen_ms']-r['source_timestamp_ms']<=5000
                   and len(r['facts'].get('name_candidates') or [])==1]
            run=[]
            for row in reversed(named):
                if run and elapsed(row['source_timestamp_ms'],run[-1]['source_timestamp_ms'])>500:break
                run.append(row)
            confirmed={r['facts']['name_candidates'][0] for r in run}
            if len(confirmed)==1 and acquired and all(e.get('kind')=='named_acquisition' for e in acquired):
                canonical=next(iter(confirmed))
                if all(_recoverable_receipt_name_variant(canonical,e.get('name'),anchored=True) for e in acquired):
                    keep=acquired[0];discarded=[e.get('name') for e in acquired]
                    field_evidence=event.get('field_evidence',{})
                    variant_evidence=[dict(name=e.get('name'),evidence=list(field_evidence.get(
                        '|'.join(str(e.get(k) or '') for k in ('kind','field','name')),[]))) for e in acquired]
                    drop={id(e) for e in acquired[1:]}
                    event['effects']=[e for e in event['effects'] if id(e) not in drop]
                    keep['name']=canonical;keep['observed_name_variants']=discarded
                    event.setdefault('resolved_acquisition_name_variants',[]).append(dict(
                        canonical_name=canonical,discarded_names=discarded,discarded_evidence=variant_evidence,
                        evidence=event.get('evidence'),basis='confirmed_request_name_with_bounded_ocr_variants'))
                    acquired=[keep]
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
                    symbol_alias=compatible and alias in names and (
                        isinstance(effect.get('visual_symbol_observation'),dict)
                        and effect['visual_symbol_observation'].get('method')
                        == 'isolated_note_stem_flag_head_and_closing_quote'
                        or not partial_match or len(names)>1)
                    if partial_match or symbol_alias:
                        request_names=names if compatible else {requested}
                        confirmations=[r for r in nearby if len(r['facts'].get('name_candidates',[]))==1
                                       and r['facts']['name_candidates'][0] in request_names];partial=True
            if not confirmations:continue
            def request_matches(row):
                names=row['facts'].get('name_candidates')
                if isinstance(names,list):
                    names=[n for n in names if not (isinstance(n,str) and len(n.strip())<=2)]
                return names==[] or isinstance(names,list) and len(names)==1 and names[0] in request_names
            last=confirmations[-1]
            # Only the final continuous request belongs to this receipt.
            group=[last]
            for row in reversed([r for r in readings if r['source_timestamp_ms']<last['source_timestamp_ms']]):
                if elapsed(row['source_timestamp_ms'],group[-1]['source_timestamp_ms'])>500:break
                # The Learn press blanks the dialog for a frame; a blank frame
                # with no reading of its own does not end the request run.
                if (row['screen']=='unknown' and not row.get('effects') and not row['facts'].get('performance_points')
                        and not row['facts'].get('name_candidates')):continue
                if row['screen']!='lesson_confirmation':break
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
                    if elapsed(row['source_timestamp_ms'],tail)>500:break
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
            initial_fill=[]
            if before and any(initial.get(k) is None for k in CURRENCIES):
                # A menu balance field hidden by the cursor keeps its value
                # from the last complete performance panel shown before this
                # menu visit, provided nothing was bought in between and every
                # field readable on both agrees.
                # Candidates, latest first: another frame of the same menu
                # visit (including the single transition frame between the
                # request and the receipt), then a complete performance panel
                # shown before the visit.  Every field readable on both the
                # candidate and the menu must agree.
                same_visit=[r for r in readings if r['screen']=='lesson_selection'
                            and baseline_start<r['source_timestamp_ms']<event['first_seen_ms']
                            and all(type(r['facts'].get('performance_points',{}).get(k)) is int for k in CURRENCIES)]
                earlier=[r for r in readings if r['screen'] in ('unknown','training_preview')
                         and r['source_timestamp_ms']>baseline_start
                         and 0<before_rows[0]['source_timestamp_ms']-r['source_timestamp_ms']<=20000
                         and all(type(r['facts'].get('performance_points',{}).get(k)) is int for k in CURRENCIES)]
                for candidate in [*reversed(same_visit),*reversed(earlier)]:
                    panel=candidate['facts']['performance_points']
                    if all(initial.get(k) is None or initial[k]==panel[k] for k in CURRENCIES):
                        initial_fill=[k for k in CURRENCIES if initial.get(k) is None]
                        initial={k:(panel[k] if initial.get(k) is None else initial[k]) for k in CURRENCIES}
                        break
            invalid_projection=any(not isinstance(r['facts'].get('projected_performance_points',{}),dict) for r in group)
            projected=repeated_projection(group)
            # A balance at zero is drawn dim and the menu reads nothing there;
            # when the request dialog projected that currency as zero on every
            # frame it showed, the empty slot is that zero (the inference
            # observed_lesson_debit makes for repeated balances, here for the
            # menu the dialog's own leftover prices).
            dim_zero_fill=[k for k in CURRENCIES if initial.get(k) is None and group and not invalid_projection
                           and all(r['facts']['projected_performance_points'].get(k)==0 for r in group)]
            if dim_zero_fill:
                initial={k:(0 if k in dim_zero_fill else initial[k]) for k in CURRENCIES}
            complete=not invalid_projection and all(type(projected.get(k)) is int for k in CURRENCIES)
            cost={k:initial[k]-projected[k] for k in CURRENCIES} if before and complete and all(type(v) is int for v in initial.values()) else None
            if cost and any(v<0 for v in cost.values()):cost=None
            after=[]
            for row in readings:
                if row['source_timestamp_ms']<=event['last_seen_ms']:continue
                # A later request dialog does not move the balance; a later
                # receipt does, so the search ends at the next acquisition.
                if (row['source_timestamp_ms']-event['last_seen_ms']>8000
                        or any(e.get('kind') in ('named_acquisition','song_learned') for e in row.get('effects',[]))):break
                if row['screen']=='lesson_selection' and all(type(row['facts'].get('performance_points',{}).get(k)) is int for k in CURRENCIES):
                    if not after and all(row['facts']['performance_points'].get(k)==initial.get(k) for k in CURRENCIES):
                        continue  # the menu still shows the pre-purchase balance
                    after.append(row)
            matched=next((r for r in after if complete and r['facts']['performance_points']==projected),None)
            observed=None
            if cost is None and not complete and not invalid_projection:
                # Repeated balances before and after the receipt price it even
                # when the dialog's leftover row was not fully read; a partial
                # song name still needs that repeated evidence, checked below.
                observed=observed_lesson_debit(readings,event,group,before_rows,after,effect['name'])
                if observed:cost=observed['cost'];matched=observed['matched']
            offered=None
            if cost is None and not partial and not invalid_projection:
                from .lesson_offer_costs import join_lesson_cost
                offered=join_lesson_cost(readings,event,group,before_rows,initial)
                if offered:cost=offered['cost']
            state_confirmed=None
            if cost is None and not partial and not invalid_projection:
                # The player left the menu before a second menu balance was
                # shown; the next repeated performance panel confirms the drop.
                from .state_confirmed_lesson_debit import state_confirmed_lesson_debit
                state_confirmed=state_confirmed_lesson_debit(readings,event,group,before_rows,effect['name'],initial=initial)
                if state_confirmed:cost=state_confirmed['cost'];matched=state_confirmed['matched']
            if partial:
                implied={k:initial[k]-cost[k] for k in CURRENCIES} if cost and all(type(initial.get(k)) is int for k in CURRENCIES) else None
                agreeing=[r for r in after if (complete and r['facts']['performance_points']==projected)
                          or (implied is not None and r['facts']['performance_points']==implied)]
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
            # The dialog's stat row states what the lesson will add in the
            # game's own arithmetic (a gain halved above the cap shows as
            # "(+6)"). When the receipt named the lesson but showed no line
            # for a stat the dialog projected on two frames, the projection
            # is the award: worked out from the request, not read off the
            # receipt, and marked so on the effect.
            projected_gains={}
            for field in FIELDS:
                seen={}
                for r in group:
                    value=(r['facts'].get('projected_stat_gains') or {}).get(field)
                    if type(value) is int and value>0:seen.setdefault(value,set()).add(r['source_timestamp_ms'])
                if len(seen)==1 and len(next(iter(seen.values())))>=2:projected_gains[field]=next(iter(seen))
            awarded_by_projection=[]
            event.setdefault('deltas',{})
            # The award is realised at the receipt, so the stat's field
            # evidence is the receipt's own frames (its timing); the dialog
            # frames that projected it stay on the effect.
            receipt_key='|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))
            receipt_frames=list((event.get('field_evidence') or {}).get(receipt_key) or [])
            if not receipt_frames and event.get('evidence'):receipt_frames=[event['evidence']]
            # The receipt's stat line can appear on frames after the named
            # line and land in a following event; a line read for the field
            # anywhere in the next few seconds is the award, not a projection.
            later_lines={e.get('field') for r in readings if event['first_seen_ms']<r['source_timestamp_ms']<=event['last_seen_ms']+3000
                         for e in r.get('effects',[]) if e.get('kind')=='stat_change' and type(e.get('amount')) is int}
            for field,gain in projected_gains.items():
                if type(event['deltas'].get(field)) is int or field in later_lines or any(e.get('kind')=='stat_change' and e.get('field')==field for e in event['effects']):continue
                frames=[r['evidence'] for r in reversed(group) if (r['facts'].get('projected_stat_gains') or {}).get(field)==gain]
                event['effects'].append(dict(kind='stat_change',field=field,amount=gain,raw_text=None,confidence=None,
                    amount_basis='lesson_confirmation_projection',projection_evidence=frames))
                event['deltas'][field]=gain
                event.setdefault('field_evidence',{})[f'stat_change|{field}|']=list(receipt_frames)
                awarded_by_projection.append(field)
            purchases.append(dict(id=f'lesson-{len(purchases)+1:04d}',kind='lesson_purchase',name=effect['name'],
                projected_stat_gains=projected_gains,stat_gains_awarded_by_projection=awarded_by_projection,
                source_timestamp_ms=event['first_seen_ms'],receipt_event_id=event['id'],
                # The points leave the balance when Learn is pressed, between the
                # request and the receipt; the accounting dates the debit there.
                debit_window_ms=[first['source_timestamp_ms'],
                                 first_debited_frame_ms(after,matched) or event['first_seen_ms']],
                performance_cost=cost,cost_basis='receipt_request_and_observed_offer_prices' if offered else 'receipt_and_repeated_observed_balances' if observed else 'state_confirmed_balance_drop' if state_confirmed else 'observed_debit' if cost is not None and matched else 'displayed_request' if cost is not None else 'unresolved',
                requested_name=requested,receipt_name=effect['name'],name_identity_verified=False,
                name_match_basis='source_symbol_alias_and_repeated_observed_debit' if symbol_alias else 'partial_name_and_repeated_observed_debit' if partial else 'exact_observed_text',
                after_balance_observed=matched is not None,awarded_stats=event['deltas'],
                initial_balance_fill_fields=initial_fill,
                **({'initial_dim_zero_fields': dim_zero_fill} if dim_zero_fill else {}),
                projected_effects=projected_effects,
                evidence=[r['evidence'] for r in (before,first,matched) if r]+[event['evidence']],
                complete_transaction_verified=False))
            if event.get('resolved_acquisition_name_variants'):
                purchases[-1]['receipt_name_variants_resolved']=deepcopy(event['resolved_acquisition_name_variants'])
            if symbol_alias:purchases[-1]['observed_request_names']=sorted(request_names)
            if offered:
                purchases[-1]['offer_cost_evidence']=offered
                purchases[-1]['evidence']=list(dict.fromkeys(purchases[-1]['evidence']+
                    offered['offer']['evidence']+offered['request']['evidence']))
            if observed:
                purchases[-1]['balance_evidence']=observed['proofs']
                purchases[-1]['evidence']=list(dict.fromkeys(purchases[-1]['evidence']+
                    [p for proof in observed['proofs'] for p in proof['evidence']]))
    return purchases


def _training_total_component_candidate(field,observations):
    """Describe a possible full-result gain against component readings.

    Training-result animations can expose a complete multi-field result and
    then smaller component badges in the same screen interval.  The shape of
    the displayed gain set is source-derived evidence for that distinction;
    numeric size and checkpoint arithmetic are deliberately irrelevant here.
    Only an exact repeated shape that strictly contains every competing shape
    is reported. Equal-shape or mixed-shape alternatives remain ambiguous.
    The result carries the source-only phase decision.  The caller may promote
    its unique full value as a direct gain while retaining the component rows
    as diagnostic evidence.
    """
    resolution=resolve_full_component_phase(observations)
    if resolution is None:
        source_observations=source_gain_observations(
            [row for row,_value in observations], field)
        if source_observations:
            resolution=resolve_source_temporal_phase(source_observations, field)
    if resolution is None:return None
    # The crop resolver may retain a canonical field amount even when the
    # merged OCR field list on that same row omitted the field.  Project that
    # field into the serialized phase observation only when the row already
    # carries the source crop resolver's complete, field-specific metadata.
    # This preserves the source proof; it does not infer a value or add a
    # frame.  Malformed crop metadata remains omitted and is rejected by the
    # evaluator.
    for key in ('full_observations','component_observations',
                'ignored_unproven_observations'):
        for proof in resolution.get(key,[]):
            if not isinstance(proof,dict):continue
            shape=proof.get('shape')
            if not isinstance(shape,list) or field in shape:continue
            candidates=proof.get('crop_candidate_amounts')
            if (proof.get('crop_basis') not in {
                    'source_crop_family_geometry',
                    'source_crop_family_geometry_prefix_resolution',
                    'same_family_scaled_crop_prefix_consensus'}
                    or proof.get('crop_conflict_state') not in {
                        'resolved_same_amount_across_source_crops',
                        'resolved_broader_prefix',
                        'resolved_same_family_prefix'}
                    or not isinstance(proof.get('crop_region'),str)
                    or not proof['crop_region'].endswith(f'.{field}')
                    or not isinstance(candidates,list) or not candidates
                    or any(type(amount) is not int or amount < 0 for amount in candidates)
                    or proof.get('value') not in candidates):
                continue
            proof['shape']=sorted(set(shape)|{field})
    value=resolution['accepted_amount']
    component_shapes={}
    for item in resolution.get('component_observations',[]):
        observed=item['value']
        component_shapes.setdefault(str(observed),[]).append(item['shape'])
    for observed,shapes in component_shapes.items():
        component_shapes[observed]=sorted({tuple(shape) for shape in shapes},key=str)
        component_shapes[observed]=[list(shape) for shape in component_shapes[observed]]
    return dict(value=value,shape=resolution['full_shape'],
                evidence=[item['evidence'] for item in resolution['full_observations']],
                component_shapes=component_shapes,
                observed_values=resolution['observed_amounts'],
                phase_order=resolution['phase_order'],
                full_last_seen_ms=resolution['full_last_seen_ms'],
                component_first_seen_ms=resolution['component_first_seen_ms'],
                basis=('repeated_full_gain_shape_strictly_contains_all_component_shapes'
                       if resolution.get('basis') ==
                       'repeated_full_gain_before_repeated_component_phase'
                       else resolution.get('basis')),
                source_resolution=resolution,accepted=True)


def _training_result_group(group):
    """Retain the bounded source-reading group used by phase proofs.

    Training action identity is intentionally a smaller set: it identifies
    the selected option, while the result animation may contain component
    frames from a different crop family.  Preserve those source readings as
    a separate envelope so evaluators can bind nested phase proofs to the
    event's own timestamps and option without treating every component frame
    as an identity proof.
    """
    observations = []
    for row in group['rows']:
        observation = dict(
            source_timestamp_ms=row.get('source_timestamp_ms'),
            evidence=row.get('evidence'),
            training_option=row.get('training_option'),
            screen=row.get('screen'),
        )
        for key in ('turn_id', 'actual_turn_id', 'owner_turn_id', 'report_turn_id'):
            if key in row:
                observation[key] = row[key]
        observations.append(observation)
    result = dict(
        interval_ms=[group['first_seen_ms'], group['last_seen_ms']],
        training_option=group['option'],
        observations=observations,
    )
    for key in ('turn_id', 'actual_turn_id', 'owner_turn_id', 'report_turn_id'):
        values = {
            row[key] for row in observations
            if isinstance(row.get(key), str) and row[key]
        }
        if len(values) == 1:
            result[key] = next(iter(values))
    return result


def _performance_gain_candidate(row, field):
    """Return one strict weak performance crop without awarding it.

    The dedicated sidebar crop is useful when a later independently parsed
    panel repeats the same value, but its amount is not safe on its own.  Keep
    this predicate deliberately narrower than the normal performance parser:
    the source region, signed text, confidence band, timestamp and evidence
    path must all be present and internally consistent.
    """
    if row.get('screen') != 'training_result':
        return None
    facts = row.get('facts', {})
    if not is_committed_training_result_row(row):
        return None
    candidates = facts.get('performance_gain_candidates', {})
    if not isinstance(candidates, dict):
        return None
    candidate = candidates.get(field)
    if not isinstance(candidate, dict):
        return None
    if candidate.get('region') != 'performance_gain.' + field:
        return None
    text = candidate.get('text', candidate.get('raw_text'))
    confidence = candidate.get('confidence')
    amount = candidate.get('amount')
    box = candidate.get('box')
    timestamp = row.get('source_timestamp_ms')
    evidence = row.get('evidence')
    if (not isinstance(text, str) or not re.fullmatch(r'\+\d{1,3}', text.strip())
            or type(amount) is not int or amount < 0 or amount > 999
            or int(text.strip()[1:]) != amount
            or type(confidence) not in (int, float) or not 90 <= confidence < 97
            or not isinstance(box, (list, tuple)) or len(box) != 4
            or any(type(value) not in (int, float) for value in box)
            or type(timestamp) is not int or not isinstance(evidence, str) or not evidence):
        return None
    return dict(row=row, source_timestamp_ms=timestamp, evidence=evidence,
                value=amount, text=text.strip(), confidence=confidence,
                box=list(box), basis=candidate.get('basis',
                    'dedicated_performance_gain_region_below_canonical_threshold'))


def _verified_performance_observation(row, field):
    """Return a source-backed applied performance reading for corroboration."""
    if row.get('screen') != 'training_result':
        return None
    facts = row.get('facts', {})
    if not is_committed_training_result_row(row):
        return None
    awards = facts.get('awarded_performance_gains', {})
    provenances = facts.get('performance_panel_provenance', {})
    if not isinstance(awards, dict) or not isinstance(provenances, dict):
        return None
    amount = awards.get(field)
    provenance = provenances.get(field)
    if type(amount) is not int or amount < 0 or not isinstance(provenance, dict):
        return None
    if provenance.get('field') != field:
        return None
    if provenance.get('status') not in (
            'resolved_merged_panel_value',
            'resolved_separate_panel_values',
            'resolved_separate_component_panel_values'):
        return None
    projected = provenance.get('projected')
    observation = projected.get('observation') if isinstance(projected, dict) else None
    if not isinstance(projected, dict) or projected.get('value') != amount:
        return None
    if not isinstance(observation, dict):
        return None
    text = re.sub(r'\s+', '', str(observation.get('text', '')).strip())
    match = re.fullmatch(r'(?:\d{1,4})?\+(\d{1,3})', text)
    confidence = observation.get('confidence')
    box = observation.get('box')
    timestamp = row.get('source_timestamp_ms')
    evidence = row.get('evidence')
    if (not match or int(match[1]) != amount
            or type(confidence) not in (int, float) or confidence < 97
            or not isinstance(box, (list, tuple)) or len(box) != 4
            or any(type(value) not in (int, float) for value in box)
            or type(timestamp) is not int or not isinstance(evidence, str) or not evidence):
        return None
    return dict(row=row, source_timestamp_ms=timestamp, evidence=evidence,
                value=amount, text=text, confidence=confidence, box=list(box),
                status=provenance.get('status'),
                basis=provenance.get('basis'))


def _corroborated_performance_gain(group, field, observations):
    """Resolve a weak sidebar amount only from two later physical readings.

    The weak amount remains the selected value; later readings only corroborate
    it.  This prevents a repeated balance from inventing an award or choosing
    between conflicting amounts, while retaining the weak source frame in the
    eventual field evidence.
    """
    candidates = [_performance_gain_candidate(row, field)
                  for row in group['rows']]
    candidates = [candidate for candidate in candidates if candidate is not None]
    if not candidates:
        return None
    candidate_values = {candidate['value'] for candidate in candidates}
    if len(candidate_values) != 1:
        return None
    value = next(iter(candidate_values))
    if group.get('option') is not None and any(
            candidate['row'].get('training_option') != group['option']
            for candidate in candidates):
        return None
    verified = [_verified_performance_observation(row, field)
                for row, observed_value in observations
                if observed_value == value]
    verified = [observation for observation in verified if observation is not None]
    # Any direct amount that is not this candidate value is a conflict, even if
    # the candidate has two matching rows beside it.
    if any(observed_value != value for _, observed_value in observations):
        return None
    candidate_time = min(candidate['source_timestamp_ms'] for candidate in candidates)
    later = [observation for observation in verified
             if observation['source_timestamp_ms'] > candidate_time
             and (group.get('option') is None
                  or observation['row'].get('training_option') == group['option'])]
    physical = {}
    for observation in later:
        physical.setdefault(observation['evidence'], observation)
    if len(physical) < 2 or len({item['source_timestamp_ms'] for item in physical.values()}) < 2:
        return None
    # If explicit turn metadata is available, the weak source and all
    # corroborating frames must agree on every populated identity key.
    for key in ('turn_id', 'actual_turn_id', 'owner_turn_id', 'report_turn_id'):
        identities = {row.get(key) for row in [candidate['row'] for candidate in candidates]
                      + [item['row'] for item in physical.values()]
                      if isinstance(row.get(key), str) and row[key]}
        if len(identities) > 1:
            return None
    weak = sorted(candidates, key=lambda candidate: candidate['source_timestamp_ms'])
    strong = sorted(physical.values(), key=lambda observation: observation['source_timestamp_ms'])
    evidence = []
    for observation in weak + strong:
        if observation['evidence'] not in evidence:
            evidence.append(observation['evidence'])
    return dict(value=value, evidence=evidence,
                weak_observations=[dict(source_timestamp_ms=item['source_timestamp_ms'],
                                        evidence=item['evidence'], value=item['value'],
                                        text=item['text'], confidence=item['confidence'],
                                        box=item['box']) for item in weak],
                corroborating_observations=[dict(source_timestamp_ms=item['source_timestamp_ms'],
                                                 evidence=item['evidence'], value=item['value'],
                                                 text=item['text'], confidence=item['confidence'],
                                                 box=item['box'], status=item['status'],
                                                 basis=item['basis']) for item in strong],
                basis='same_result_group_two_later_physical_frames_corroborate_weak_performance_region')


_CANDIDATE_GAIN_RECOVERY_BASES = frozenset({
    'repeated_source_gain_badge_same_phase',
    'same_frame_nested_source_gain_crop_agreement',
})
_CANDIDATE_GAIN_FAMILIES = frozenset({'gain', 'wide_gain', 'expanded_gain'})
def _candidate_gain_box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        if any(
            isinstance(part, bool)
            or type(part) not in (int, float)
            or not math.isfinite(float(part))
            for part in value
        ):
            return None
        box = tuple(float(part) for part in value)
    except (TypeError, ValueError, OverflowError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _candidate_gain_policy(value):
    """Accept only the producer policy or bounds that are strictly tighter."""

    reference = candidate_recovery_policy()
    if not isinstance(value, dict) or set(value) != set(reference):
        return None
    result = dict(reference)
    confidence_keys = {
        'minimum_tight_confidence',
        'minimum_broad_confidence',
    }
    integer_keys = {
        'minimum_repeated_frames',
        'maximum_span_ms',
        'maximum_gap_ms',
    }
    for key, expected in reference.items():
        actual = value.get(key)
        if key in confidence_keys:
            normalized = confidence_percent(actual)
            if normalized is None:
                return None
            if normalized < float(expected):
                return None
            result[key] = normalized
        elif key in integer_keys:
            if type(actual) is not int or actual < 0:
                return None
            if key == 'minimum_repeated_frames' and actual < int(expected):
                return None
            if key in {'maximum_span_ms', 'maximum_gap_ms'} and actual > int(expected):
                return None
            result[key] = actual
        elif type(actual) is not type(expected) or actual != expected:
            return None
    return result


def _candidate_gain_observation_is_valid(observation, field, option, policy):
    if not isinstance(observation, dict):
        return False
    if observation.get('field') != field:
        return False
    amount = observation.get('amount')
    if type(amount) is not int or amount < 0 or amount > 999:
        return False
    timestamp = observation.get('source_timestamp_ms')
    if type(timestamp) is not int or timestamp < 0:
        return False
    evidence = observation.get('evidence')
    if not isinstance(evidence, str) or not evidence.strip():
        return False
    phase = observation.get('phase_signature')
    if (not isinstance(phase, list) or len(phase) != 3
            or phase[0] != 'training_result' or phase[1] != option
            or phase[2] is not False):
        return False
    family = observation.get('crop_family')
    region = observation.get('region')
    if family not in _CANDIDATE_GAIN_FAMILIES or region != f'{family}.{field}':
        return False
    role = observation.get('source_role')
    if family == 'gain' and role != 'amount_crop_candidate':
        return False
    if family in {'wide_gain', 'expanded_gain'} and role not in {
        'amount_crop_candidate', 'unparsed_crop_candidate',
    }:
        return False
    if _candidate_gain_box(observation.get('box')) is None:
        return False
    confidence = confidence_percent(observation.get('confidence'))
    if confidence is None:
        return False
    if family == 'gain':
        minimum = float(policy['minimum_tight_confidence'])
        text = re.fullmatch(r'\s*\+\s*(\d{1,3})\s*', str(observation.get('raw_text', '')))
    else:
        minimum = float(policy['minimum_broad_confidence'])
        text = re.fullmatch(r"\s*\+\s*(\d{1,3})([:.,'’)\]}])?\s*", str(observation.get('raw_text', '')))
    return bool(text and confidence >= minimum and int(text.group(1)) == amount)


def _candidate_gain_provenance(group_rows, field, option, canonical_observations):
    """Validate one accepted candidate-recovery proof for a training event.

    Candidate recovery is a separate source channel.  This adapter consumes
    only its accepted typed crop proof, verifies every physical source identity
    against the event's result rows, and emits the normal event-level direct
    provenance shape.  It never derives an amount from checkpoints, balances,
    result totals, or a state residual.
    """

    records = []
    for row in group_rows:
        if not is_committed_training_result_row(row):
            continue
        facts = row.get('facts', {})
        if not isinstance(facts, dict):
            continue
        recoveries = facts.get('training_gain_candidate_recovery', {})
        candidate = recoveries.get(field) if isinstance(recoveries, dict) else None
        if isinstance(candidate, dict):
            records.append(candidate)
    if len(records) != 1 or not isinstance(option, str) or not option.strip():
        return None
    candidate = records[0]
    if candidate.get('field') != field:
        return None
    if candidate.get('basis') not in _CANDIDATE_GAIN_RECOVERY_BASES:
        return None
    if candidate.get('status') not in (None, 'accepted'):
        return None
    for key in ('conflict_state', 'resolution_status'):
        if candidate.get(key) not in (None, 'resolved', 'accepted'):
            return None
    amount = candidate.get('accepted_amount')
    if type(amount) is not int or amount < 0 or amount > 999:
        return None
    phase_key = candidate.get('phase_key')
    if not isinstance(phase_key, (str, int)) or (isinstance(phase_key, str) and not phase_key.strip()):
        return None
    observations = candidate.get('accepted_observations')
    if not isinstance(observations, list) or not observations:
        return None
    all_observations = candidate.get('observations')
    if not isinstance(all_observations, list) or not all_observations:
        return None
    policy = _candidate_gain_policy(candidate.get('policy'))
    if policy is None:
        return None

    identities = []
    observations_by_identity = {}
    families_by_identity = {}
    for observation in observations:
        if not _candidate_gain_observation_is_valid(observation, field, option, policy):
            return None
        if observation.get('amount') != amount:
            return None
        identity = (observation['source_timestamp_ms'], observation['evidence'].strip())
        prior = observations_by_identity.get(identity)
        if prior is not None:
            # Nested tight/broad crops are deliberately two views of one
            # physical frame.  A second view from the same crop family remains
            # ambiguous and cannot be treated as corroboration.
            families = families_by_identity.setdefault(identity, set())
            if (candidate.get('basis') != 'same_frame_nested_source_gain_crop_agreement'
                    or observation.get('crop_family') in families):
                return None
        else:
            identities.append(identity)
            families_by_identity[identity] = set()
        families_by_identity[identity].add(observation.get('crop_family'))
        observations_by_identity[identity] = observation
    phases = {
        tuple(observation['phase_signature'])
        for observation in observations
    }
    if len(phases) != 1:
        return None
    accepted_phase = next(iter(phases))
    relaxed_policy = dict(policy)
    relaxed_policy['minimum_tight_confidence'] = 0.0
    relaxed_policy['minimum_broad_confidence'] = 0.0
    for observation in all_observations:
        if not _candidate_gain_observation_is_valid(
                observation, field, option, relaxed_policy):
            return None
        family = observation['crop_family']
        threshold = (float(policy['minimum_tight_confidence'])
                     if family == 'gain'
                     else float(policy['minimum_broad_confidence']))
        confidence = confidence_percent(observation.get('confidence'))
        if confidence is None:
            return None
        if (confidence >= threshold
                and (observation['amount'] != amount
                     or tuple(observation['phase_signature']) != accepted_phase)):
            return None

    if candidate.get('basis') == 'repeated_source_gain_badge_same_phase':
        tight = [observation for observation in observations if observation['crop_family'] == 'gain']
        times = sorted({identity[0] for identity in identities})
        minimum_frames = int(policy['minimum_repeated_frames'])
        if (len(tight) < minimum_frames or len(identities) < minimum_frames
                or len(times) < minimum_frames
                or times[-1] - times[0] > int(policy['maximum_span_ms'])
                or any(later - earlier > int(policy['maximum_gap_ms'])
                       for earlier, later in zip(times, times[1:]))):
            return None
    else:
        tight = [observation for observation in observations if observation['crop_family'] == 'gain']
        broad = [observation for observation in observations
                 if observation['crop_family'] in {'wide_gain', 'expanded_gain'}]
        if not tight or not broad:
            return None
        if not any(
            same['source_timestamp_ms'] == wide['source_timestamp_ms']
            and same['evidence'] == wide['evidence']
            and _candidate_gain_box(wide['box'])[0] <= _candidate_gain_box(same['box'])[0]
            and _candidate_gain_box(wide['box'])[1] <= _candidate_gain_box(same['box'])[1]
            and _candidate_gain_box(wide['box'])[2] >= _candidate_gain_box(same['box'])[2]
            and _candidate_gain_box(wide['box'])[3] >= _candidate_gain_box(same['box'])[3]
            for same in tight for wide in broad
        ):
            return None

    # Every accepted proof must be bound to one unique report-owned result
    # row.  Duplicate path/timestamp rows are ambiguous source identity.
    source_rows = {}
    preview_identities = set()
    for row in group_rows:
        if row.get('screen') != 'training_result':
            continue
        timestamp = row.get('source_timestamp_ms')
        evidence = row.get('evidence')
        if type(timestamp) is not int or not isinstance(evidence, str) or not evidence.strip():
            continue
        if row.get('training_option') not in (None, option):
            continue
        identity = (timestamp, evidence.strip())
        if identity in source_rows:
            return None
        stats = row.get('stats', {})
        if isinstance(stats, dict) and stats.get('training_preview') is True:
            preview_identities.add(identity)
        source_rows[identity] = row
    if any(identity not in source_rows or identity in preview_identities for identity in identities):
        return None

    # Canonical facts may coexist with the recovery proof on its representative
    # row.  They must agree, or the candidate channel remains unresolved.
    if any(value != amount for _row, value in canonical_observations):
        return None
    proof_rows = [
        dict(source_timestamp_ms=observation['source_timestamp_ms'],
             evidence=observation['evidence'], value=amount)
        for observation in observations
    ]
    evidence = list(dict.fromkeys(item['evidence'] for item in proof_rows))
    return dict(
        value=amount,
        basis='candidate_only_training_gain',
        candidate_recovery_basis=candidate['basis'],
        candidate_phase_key=phase_key,
        candidate_policy=deepcopy(policy),
        observations=proof_rows,
        source_timestamps_ms=[item['source_timestamp_ms'] for item in proof_rows],
        evidence=evidence,
        observation_count=len(proof_rows),
        independent_effect_verification=True,
        candidate_all_observations=deepcopy(all_observations),
        candidate_observations=deepcopy(observations),
    )


_RESULT_CONTINUATION_GAP_MS=3000


def _result_continuation_group(group,option):
    """True for a result-screen group carrying nothing a training could use.

    After the result cards, the training's own receipt (energy, friendship)
    overlays the screen; when it fades the result grid is detected again but
    the badges are gone.  Such frames have no outcome, no gains, no awards,
    no badges and no totals.  The heading of the same training (its option
    and name) may still be visible on the first of them; a different option
    is another training's result.
    """
    if group.get('option') and group.get('option')!=option:
        return False
    for row in group['rows']:
        if row.get('training_option') and row.get('training_option')!=option:
            return False
        facts=row.get('facts') if isinstance(row.get('facts'),dict) else {}
        if facts.get('training_outcome') or facts.get('animation_gain_badges'):
            return False
        # A parsed-but-unread field is stored as None (for example
        # ``result_values: {'skill_points': None}``); only an integer reading
        # is a payload.
        for key in ('training_gains','awarded_performance_gains','result_values'):
            values=facts.get(key)
            if isinstance(values,dict) and any(type(v) is int for v in values.values()):
                return False
            if values and not isinstance(values,dict):
                return False
    return True


def _fold_result_continuations(groups):
    """Attach empty trailing result groups to the training they belong to.

    Two trainings cannot be committed within a few seconds of each other, so
    an empty option-less result group that starts within
    ``_RESULT_CONTINUATION_GAP_MS`` of a preceding option-bearing group, and
    names no other option, is the tail of that result, not a new training.  Its rows are kept on the
    preceding group as ``continuation_rows`` (recorded on the event, never
    used as gain evidence) and the event window is left unchanged.
    """
    kept=[]
    for group in groups:
        previous=kept[-1] if kept else None
        if (previous is not None and previous.get('option') and _result_continuation_group(group,previous['option'])
                and 0<=group['first_seen_ms']-previous['last_seen_ms']<=_RESULT_CONTINUATION_GAP_MS):
            previous.setdefault('continuation_rows',[]).extend(group['rows'])
            continue
        kept.append(group)
    return kept


def training_events(readings,states=()):
    from .preview_confirmed_gains import preview_confirmed_gains, preview_only_training_groups
    groups=[];current=None
    for row in readings:
        # Source timestamps are untrusted parser metadata.  Filter malformed
        # values before the grouping arithmetic so a negative row cannot be
        # promoted as a repeated gain and a string/float cannot raise before
        # the numeric resolver gets a chance to abstain.
        if not isinstance(row,dict):
            continue
        time=row.get('source_timestamp_ms')
        if type(time) is not int or time < 0:
            continue
        # The half second between result frames is counted in sampling steps
        # (``source_clock``).
        if row.get('screen')!='training_result':
            if current and elapsed(current['last_seen_ms'],time)>500:current=None
            continue
        option=row.get('training_option');time=row['source_timestamp_ms']
        if current is None or elapsed(current['last_seen_ms'],time)>500 or (option and current['option'] and option!=current['option']):
            current=dict(kind='training',option=option,first_seen_ms=time,last_seen_ms=time,rows=[]);groups.append(current)
        current['last_seen_ms']=time
        if option:current['option']=option
        current['rows'].append(row)
    groups=_fold_result_continuations(groups)
    events=[]
    for group in groups:
        deltas={};proofs={};conflicts={};phase_candidates={};prefix_resolutions={};direct_provenance={}
        candidate_recovery_provenance={}
        source_clipped_resolutions={}
        for field in FIELDS:
            observations=source_gain_observations(group['rows'], field)
            from .training_gain_resolution import resolve_source_clipped_gain
            clipping=resolve_source_clipped_gain(
                group['rows'], field,
                phase_key=f"{group['option']}:{group['first_seen_ms']}:{group['last_seen_ms']}",
            )
            if clipping.get('status') == 'accepted':
                value=clipping['accepted_amount']
                # Several crops can expose one badge in the same physical
                # frame. Preserve all crop proof in the resolution, but emit
                # one observation per frame for the single applied effect.
                full={}
                for item in clipping['accepted_observations']:
                    identity=(item['source_timestamp_ms'], item['evidence'])
                    full[identity]=(dict(source_timestamp_ms=identity[0],
                                         evidence=identity[1]), value)
                proof=direct_gain_proof(
                    value, list(full.values()), basis='source_clipped_training_gain')
                proof['source_clipping_resolution']=deepcopy(clipping)
                deltas[field]=value
                proofs[field]=proof['evidence']
                direct_provenance[field]=proof
                source_clipped_resolutions[field]=clipping
                continue
            # A malformed signed-crop candidate is a source-integrity failure,
            # not an ordinary missing crop.  Do not let the legacy prefix or
            # direct-value fallback promote the same field from canonical
            # values after the clipped resolver has rejected that metadata.
            if clipping.get('reason') in {
                    'invalid_source_confidence', 'invalid_source_identity',
                    'duplicate_source_frame_identity'}:
                values={value for _,value in observations if type(value) is int and value >= 0}
                if values:
                    conflicts[field]=sorted(values)
                source_clipped_resolutions[field]=clipping
                continue
            values={value for _,value in observations}
            if len(values) == 1:
                short=next(iter(values))
                from .crop_provenance import source_geometry_overlaps
                crop_rows=clipping.get('observations', [])
                policy=clipping.get('policy', {})
                tight=[item for item in crop_rows
                       if item.get('crop_family') == 'gain'
                       and item.get('amount') == short
                       and item.get('confidence', 0) >= policy.get('minimum_tight_confidence', 90)]
                longer=set()
                for item in crop_rows:
                    amount=item.get('amount')
                    family=item.get('crop_family')
                    threshold=(policy.get('minimum_overlay_confidence', 90)
                               if family == 'result'
                               else policy.get('minimum_broad_confidence', 75))
                    if (family not in {'result', 'wide_gain', 'expanded_gain'}
                            or type(amount) is not int
                            or len(str(amount)) <= len(str(short))
                            or not str(amount).startswith(str(short))
                            or item.get('confidence', 0) < threshold):
                        continue
                    if any(item.get('source_timestamp_ms') == narrow.get('source_timestamp_ms')
                           and item.get('evidence') == narrow.get('evidence')
                           and source_geometry_overlaps(item.get('box'), narrow.get('box'))
                           for narrow in tight):
                        longer.add(amount)
                if longer:
                    # The full value is not established, but a repeatedly
                    # clipped prefix is no longer an unopposed observation.
                    # Preserve uncertainty rather than silently returning it.
                    conflicts[field]=sorted({short, *longer})
                    source_clipped_resolutions[field]=clipping
                    continue
            candidate_provenance=_candidate_gain_provenance(
                group['rows'], field, group['option'], observations,
            )
            if candidate_provenance is not None:
                value=candidate_provenance['value']
                deltas[field]=value
                proofs[field]=candidate_provenance['evidence']
                direct_provenance[field]=candidate_provenance
                candidate_recovery_provenance[field]=candidate_provenance
                continue
            values={value for _,value in observations}
            distinct_observations=distinct_gain_observations(observations)
            if len(values)==1 and len(distinct_observations)>=2:
                value=next(iter(values));deltas[field]=value;proofs[field]=[r['evidence'] for r,_ in distinct_observations]
                direct_provenance[field]=direct_gain_proof(value,distinct_observations)
            elif len(values)==1 and len(distinct_observations)==1:
                # A committed-result recovery window is source-bound by its
                # owner, option, success banner, and exact reread identity.
                # That proof shape permits one clear direct badge while the
                # ordinary unproven one-frame fallback still abstains.
                row, value = distinct_observations[0]
                result_proof = source_result_projection_proof(row, value, field)
                if (result_proof is not None
                        and source_result_projection_belongs_to_group(
                            result_proof, row, group)):
                    deltas[field] = value
                    proofs[field] = [row['evidence']]
                    direct_provenance[field] = direct_gain_proof(
                        value, [(row, value)], basis='training_gain_source_phase',
                    )
                    direct_provenance[field]['source_result_projection'] = result_proof
            elif len(values)>1:
                counts=Counter(value for _,value in observations)
                candidate=_training_total_component_candidate(field,observations)
                if candidate:
                    # Keep the public source-inspection helper diagnostic when
                    # no checkpoint context was supplied.  Full reconstruction
                    # passes checkpoints and may promote the source-only phase
                    # decision; this preserves the historical inspection API
                    # without using a missing balance to choose an amount.
                    if not states:
                        conflicts[field]=sorted(values)
                        phase_candidates[field]=candidate
                        continue
                    value=candidate['value']
                    full=[
                        (dict(source_timestamp_ms=item['source_timestamp_ms'],
                              evidence=item['evidence']), item['value'])
                        for item in candidate['source_resolution'].get('full_observations', [])
                    ]
                    deltas[field]=value;proofs[field]=[r['evidence'] for r,n in full]
                    direct_provenance[field]=direct_gain_proof(
                        value,full,basis='training_gain_full_phase')
                    phase_candidates[field]=candidate
                else:
                    from .training_gain_resolution import resolve_prefix
                    resolution=resolve_prefix(observations)
                    # A compact synthetic/source window can have sub-frame
                    # timestamps while still carrying three complete badges
                    # and two brief prefixes.  Keep the normal temporal span
                    # guard first; this bounded fallback still requires the
                    # stronger count pattern and source-only shape checks.
                    if (resolution is None and len(distinct_observations)>=5
                            and max(Counter(value for _,value in distinct_observations).values())>=3):
                        resolution=resolve_prefix(observations,minimum_span_ms=0)
                    if resolution:
                        deltas[field]=resolution['accepted_amount']
                        proofs[field]=[p['evidence'] for p in resolution['complete_observations']]
                        complete=[(r,n) for r,n in distinct_observations if n==resolution['accepted_amount']]
                        direct_provenance[field]=direct_gain_proof(
                            resolution['accepted_amount'],complete,
                            basis='training_gain_prefix_recovered')
                        prefix_resolutions[field]=resolution
                    else:
                        from .training_gain_resolution import resolve_occluded_prefix
                        occluded=resolve_occluded_prefix(observations)
                        if occluded:
                            deltas[field]=occluded['accepted_amount']
                            proofs[field]=[p['evidence'] for p in occluded['complete_observations']]
                            complete=[(r,n) for r,n in distinct_observations if n==occluded['accepted_amount']]
                            direct_provenance[field]=direct_gain_proof(
                                occluded['accepted_amount'],complete,
                                basis='training_gain_occluded_prefix_recovered')
                            prefix_resolutions[field]=occluded
                        # A digit added for a moment ("301" beside "+30") and
                        # a digit hidden for many frames ("3" of 36 under a
                        # parked cursor) look alike by frame counts; the reads
                        # stay a conflict for the learned reader and the stat
                        # bars to settle.
                        else:conflicts[field]=sorted(values)
        evidence=next(iter(proofs.values()),[group['rows'][0]['evidence']])[0]
        events.append(dict(id=f'training-{len(events)+1:04d}',kind='training',training_option=group['option'],
            first_seen_ms=group['first_seen_ms'],last_seen_ms=group['last_seen_ms'],deltas=deltas,
            evidence=evidence,field_evidence=proofs,conflicting_readings=conflicts,
            repeated_fields=[field for field,paths in proofs.items() if len(paths)>=2],
            direct_gain_provenance=direct_provenance,
            result_group=_training_result_group(group),
            effect_coverage_verified=False,action_time_ms=None))
        if group.get('continuation_rows'):
            events[-1]['result_continuation_observations']=[
                dict(source_timestamp_ms=r.get('source_timestamp_ms'),evidence=r.get('evidence'),screen=r.get('screen'))
                for r in group['continuation_rows']]
        identity = summarize_training_identity(group['rows'], group['option'], source_rows=readings)
        if identity:
            events[-1].update(identity)
        if phase_candidates:events[-1]['gain_phase_candidates']=phase_candidates
        if prefix_resolutions:events[-1]['gain_prefix_resolutions']=prefix_resolutions
        if candidate_recovery_provenance:
            events[-1]['candidate_gain_recovery_provenance']=candidate_recovery_provenance
        if source_clipped_resolutions:
            events[-1]['source_clipped_gain_resolutions']=source_clipped_resolutions
        # A skipped result animation can leave no badge at all.  The committed
        # card's preview, confirmed field by field against the surrounding
        # totals, supplies the remaining gains as state-derived contributions.
        confirmed=preview_confirmed_gains(readings,group,deltas)
        # A field whose direct badge readings disagree keeps its recorded
        # conflict unless the preview amount is itself one of those readings
        # and the surrounding totals confirm it: then two independent
        # witnesses (the card preview and the result/home totals) agree with
        # one of the badge readings, and the others are recorded as
        # superseded rather than left as an open conflict.  A preview amount
        # that matches none of the readings never overrides them.
        superseded={}
        kept={}
        for f,p in confirmed.items():
            readings_for_field=conflicts.get(f)
            if f not in conflicts:
                kept[f]=p
            elif isinstance(readings_for_field,list) and p.get('value') in readings_for_field:
                superseded[f]=list(readings_for_field)
                kept[f]=dict(p,superseded_conflicting_readings=list(readings_for_field))
        confirmed=kept
        for f in superseded:
            conflicts.pop(f,None)
        if superseded:
            events[-1]['conflicting_readings_superseded_by_preview_confirmation']=superseded
        if confirmed:
            derived=events[-1].setdefault('result_state_derived_fields',[])
            # The applied result is observed on the group's own result frames;
            # the preview frames are the supporting witnesses.  Listing the
            # result frames first gives the accounting a field timing inside
            # the event window instead of a parent-window-only placement.
            applied=[r['evidence'] for r in group['rows'] if isinstance(r.get('evidence'),str)]
            for field,proof in confirmed.items():
                deltas[field]=proof['value']
                proofs[field]=applied+[e for e in proof['evidence'] if e not in applied]
                direct_provenance[field]=dict(proof,applied_result_evidence=applied)
                if field not in derived:derived.append(field)
            events[-1]['preview_confirmed_gains']=confirmed
            events[-1]['repeated_fields']=[field for field,paths in proofs.items() if len(paths)>=2]
        # A badge that reads more than the panels before and after the training
        # allow is a misread digit, not a gain: it becomes a conflict, which
        # the dense re-read revisits and the review queue shows.
        from .preview_confirmed_gains import badge_contradictions
        contradicted=badge_contradictions(readings,group,{f:v for f,v in deltas.items() if f not in confirmed})
        for field,proof in contradicted.items():
            conflicts[field]=sorted({deltas.pop(field),proof['allowed']})
            proofs.pop(field,None);direct_provenance.pop(field,None)
            events[-1].setdefault('gain_contradictions',{})[field]=proof
        # The badges can be hidden by the outcome animation on every sampled
        # frame while the panel's totals are readable: those totals minus the
        # last full snapshot before the training are the applied gains.
        from .preview_confirmed_gains import result_total_gains
        totals=result_total_gains(readings,group,deltas)
        totals={f:p for f,p in totals.items() if f not in conflicts}
        if totals:
            derived=events[-1].setdefault('result_state_derived_fields',[])
            applied=[r['evidence'] for r in group['rows'] if isinstance(r.get('evidence'),str)]
            for field,proof in totals.items():
                deltas[field]=proof['value']
                proofs[field]=applied+[e for e in proof['evidence'] if e not in applied]
                direct_provenance[field]=dict(proof,applied_result_evidence=applied)
                if field not in derived:derived.append(field)
            events[-1]['result_total_gains']=totals
            events[-1]['repeated_fields']=[field for field,paths in proofs.items() if len(paths)>=2]
        events[-1]['_group_rows']=group['rows']
        performance={};performance_proofs={};performance_candidates={}
        for field in CURRENCIES:
            candidates=[_performance_gain_candidate(row, field)
                        for row in group['rows']]
            candidates=[candidate for candidate in candidates if candidate is not None]
            if candidates:
                performance_candidates[field]=[
                    dict(source_timestamp_ms=candidate['source_timestamp_ms'],
                         evidence=candidate['evidence'], value=candidate['value'],
                         text=candidate['text'], confidence=candidate['confidence'],
                         box=candidate['box'], basis=candidate['basis'])
                    for candidate in candidates]
            observations=[
                (r, r['facts']['awarded_performance_gains'][field])
                for r in group['rows']
                if is_committed_training_result_row(r)
                and field in r.get('facts', {}).get('awarded_performance_gains', {})
            ]
            values={value for _,value in observations}
            if len(values)==1:
                value=next(iter(values));performance[field]=value
                performance_proofs[field]=[r['evidence'] for r,_ in observations]
                resolution=_corroborated_performance_gain(group,field,observations)
                if resolution:
                    performance_proofs[field]=resolution['evidence']
                    events[-1].setdefault('performance_reading_resolutions',{})[field]=dict(
                        observed_amounts=sorted(values|{candidate['value'] for candidate in candidates}),
                        accepted_amount=value,
                        basis=resolution['basis'],
                        weak_observations=resolution['weak_observations'],
                        corroborating_observations=resolution['corroborating_observations'])
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
        if performance_candidates:
            events[-1]['performance_reading_candidates']=performance_candidates
        performance_confirmed=preview_confirmed_gains(readings,group,performance,channel='performance')
        performance_conflicts=events[-1].get('performance_reading_conflicts',{})
        performance_superseded={}
        performance_kept={}
        for f,p in performance_confirmed.items():
            readings_for_field=performance_conflicts.get(f) if isinstance(performance_conflicts,dict) else None
            if f not in performance_conflicts:
                performance_kept[f]=p
            elif isinstance(readings_for_field,list) and p.get('value') in readings_for_field:
                performance_superseded[f]=list(readings_for_field)
                performance_kept[f]=dict(p,superseded_conflicting_readings=list(readings_for_field))
        performance_confirmed=performance_kept
        if performance_superseded and isinstance(performance_conflicts,dict):
            for f in performance_superseded:
                performance_conflicts.pop(f,None)
            events[-1]['performance_reading_conflicts_superseded_by_preview_confirmation']=performance_superseded
        if performance_confirmed:
            applied=[r['evidence'] for r in group['rows'] if isinstance(r.get('evidence'),str)]
            derived=events[-1].setdefault('result_state_derived_fields',[])
            for field,proof in performance_confirmed.items():
                performance[field]=proof['value']
                performance_proofs[field]=applied+[e for e in proof['evidence'] if e not in applied]
                if field not in derived:derived.append(field)
            events[-1]['preview_confirmed_performance_gains']=performance_confirmed
        # A row the card never read (a badge over it, or a merged read under
        # the confidence floor on every frame) is unknown, not zero: the
        # accounting may let this training own that field's turn residual.
        # A row read as its current value alone was read, and gave nothing.
        provenances=[r['facts'].get('performance_panel_provenance') for r in group['rows']
                     if is_committed_training_result_row(r) and isinstance(r['facts'].get('performance_panel_provenance'),dict)
                     and r['facts']['performance_panel_provenance']]
        unread=[]
        if provenances:
            for field in CURRENCIES:
                if field in performance:continue
                statuses={(p.get(field) or {}).get('status') for p in provenances}
                if all(s is None or str(s).startswith('unresolved') for s in statuses):
                    unread.append(field)
        events[-1]['performance_rows_unread']=unread
        events[-1].update(performance_deltas=performance,performance_evidence=performance_proofs,
                         action_identity_evidence=[r['evidence'] for r in group['rows']
                             if group['option'] is not None and r.get('training_option')==group['option']],
                         action_identity_observations=len({r['source_timestamp_ms'] for r in group['rows']
                             if group['option'] is not None and r.get('training_option')==group['option']}))
        from .training_outcome import summarize as summarize_training_outcome
        events[-1].update(summarize_training_outcome(group['rows']))
        failures=[r for r in group['rows'] if r['facts'].get('training_outcome')=='failure'
                  or r['facts'].get('failure_banner')]
        if failures:
            events[-1].update(failure_evidence=[r['evidence'] for r in failures],
                             unawarded_performance_projection=dict(performance),performance_deltas={},performance_evidence={})
            for row in failures:events[-1]['unawarded_performance_projection'].update(row['facts'].get('unawarded_performance_projection',{}))
        elif (performance and not events[-1].get('deltas') and events[-1].get('training_outcome')!='success'
              and not any(r['facts'].get('training_gains') for r in group['rows'])):
            # No frame of this training shows a stat badge or an outcome
            # banner: the side panel's "+N" beside a performance row is the
            # preview's projection, still on screen through the training
            # scene, and awards nothing yet. Kept aside as a failed result's is.
            events[-1].update(unawarded_performance_projection=dict(performance),performance_deltas={},performance_evidence={},
                             unawarded_performance_basis='no_stat_badge_or_banner_on_any_frame')
        before=[s for s in states if 0<group['first_seen_ms']-s['last_seen_ms']<=5000]
        before=before[-1] if before else None
        earlier_awards=before and any(before['last_seen_ms']<r['source_timestamp_ms']<group['first_seen_ms'] and any(e['kind']=='stat_change' for e in r.get('effects',[])) for r in readings)
        state_crosschecks={};state_derived_provenance={};state_constrained_provenance={}
        if before and not earlier_awards:
            for field in FIELDS:
                before_value=before.get('values',{}).get(field)
                if type(before_value) is not int or before_value<0:continue
                # Select the final source counter first. The claimed gain is
                # recomputed only after that selection; it cannot choose an
                # earlier matching intermediate total.
                suffix=stable_trailing_result_suffix(group['rows'],field)
                counter=stable_trailing_result_counter_suffix(
                    group['rows'],field,require_complete_after_partial=True)
                selected=suffix or counter
                candidate_observations=[(row,value) for row in group['rows']
                    for value in row.get('facts',{}).get('training_gain_candidates',{}).get(field,[])
                    if type(value) is int and value>=0]
                candidate_values=sorted({value for _,value in candidate_observations})
                # A single complete result total may support a single visible
                # gain candidate when it is later in time and independently
                # bound to its own result-value candidate.  Select that total
                # before comparing arithmetic; never search for a total that
                # matches the claimed amount.  Repeated/partial result totals
                # continue through the stable-suffix helpers below.
                if selected is None and len(candidate_values)==1:
                    result_rows=[row for row in group['rows']
                        if type(row.get('facts',{}).get('result_values',{}).get(field)) is int
                        and row.get('facts',{}).get('result_value_candidates',{}).get(field)
                           == row.get('facts',{}).get('result_values',{}).get(field)]
                    if len(result_rows)==1:
                        result_row=result_rows[0]
                        if (all(result_row['source_timestamp_ms']>row['source_timestamp_ms']
                                for row,_ in candidate_observations)
                                and result_row['source_timestamp_ms']>before['last_seen_ms']):
                            selected=dict(
                                value=result_row['facts']['result_values'][field],
                                observations=[dict(source_timestamp_ms=result_row['source_timestamp_ms'],
                                                   evidence=result_row['evidence'],
                                                   value=result_row['facts']['result_values'][field])],
                                first_seen_ms=result_row['source_timestamp_ms'],
                                last_seen_ms=result_row['source_timestamp_ms'],
                                observation_count=1,
                                basis='single_source_result_total_after_visible_gain_candidate')
                if selected is None:continue
                selected_value=selected['value']
                delta=selected_value-before_value
                if delta<=0 or selected['first_seen_ms']<=before['last_seen_ms']:continue
                direct_value=deltas.get(field)
                crosscheck=dict(
                    field=field,amount=delta,before_value=before_value,
                    after_value=selected_value,before_evidence=before.get('evidence'),
                    before_supporting_evidence=before.get('supporting_frames',[]),
                    after_source_timestamp_ms=selected['first_seen_ms'],
                    after_evidence=selected['observations'][0]['evidence'],
                    after_observations=selected['observations'],
                    basis=selected['basis'],
                    source_timestamp_order=selected['first_seen_ms']>before['last_seen_ms'],
                )
                if direct_value is not None:
                    if direct_value==delta:
                        crosscheck['status']='agrees_with_direct'
                        state_crosschecks[field]=crosscheck
                        events[-1].setdefault('cross_checked_gain_fields',[]).append(field)
                    else:
                        crosscheck['status']='disagrees_with_direct'
                        state_crosschecks[field]=crosscheck
                        events[-1].setdefault('gain_reading_disagreements',{})[field]=dict(
                            animated_gain=direct_value,stable_result_change=delta,
                            animated_candidates=sorted(set(
                                [direct_value]+candidate_values)))
                    # A state comparison cannot replace a direct source proof.
                    continue
                if field in conflicts:
                    crosscheck['status']='blocked_by_conflicting_gain_observations'
                    state_crosschecks[field]=crosscheck
                    continue
                if candidate_values and delta not in candidate_values:
                    # A visible candidate that disagrees with the independently
                    # selected suffix remains an explicit mismatch.
                    crosscheck['status']='candidate_disagrees_with_recomputed_change'
                    crosscheck['candidate_values']=candidate_values
                    state_crosschecks[field]=crosscheck
                    continue
                deltas[field]=delta
                proofs[field]=[before['evidence']]+[
                    observation['evidence'] for observation in selected['observations']]
                constrained=bool(candidate_values)
                state_crosschecks[field]=dict(crosscheck,
                    status='state_constrained_candidate' if constrained else 'state_only')
                provenance=dict(
                    amount=delta,before=dict(value=before_value,
                        source_timestamp_ms=before.get('last_seen_ms'),
                        evidence=before.get('evidence')),
                    after=dict(value=selected_value,
                        source_timestamp_ms=selected['first_seen_ms'],
                        evidence=selected['observations'][0]['evidence'],
                        observations=selected['observations']),
                    recomputed_amount=delta,basis=selected['basis'],
                    candidate_values=candidate_values,
                    independent_effect_verification=False)
                if constrained:
                    state_constrained_provenance[field]=provenance
                else:
                    state_derived_provenance[field]=provenance
                    events[-1].setdefault('result_state_derived_fields',[]).append(field)
                if field not in events[-1]['repeated_fields']:events[-1]['repeated_fields'].append(field)
                if counter and selected is counter:
                    partial_proofs=[]
                    for observation in counter['observations']:
                        readings_for_field=next((row.get('facts',{}).get('partial_result_counter_readings',{}).get(field)
                            for row in group['rows']
                            if row.get('source_timestamp_ms')==observation['source_timestamp_ms']
                            and field in row.get('facts',{}).get('partial_result_counter_readings',{})),None)
                        if readings_for_field:
                            partial_proofs.append(dict(
                                source_timestamp_ms=observation['source_timestamp_ms'],
                                evidence=observation['evidence'],readings=readings_for_field))
                    if partial_proofs:
                        events[-1].setdefault('partial_result_counter_evidence',{})[field]=partial_proofs
                if candidate_values:
                    events[-1].setdefault('state_supported_candidate_resolutions',[]).append(dict(
                        field=field,amount=delta,visual_candidates=candidate_values,
                        gain_evidence=[row['evidence'] for row,_ in candidate_observations],
                        before_evidence=before['evidence'],
                        after_evidence=[observation['evidence'] for observation in selected['observations']],
                        basis='visible_gain_candidate_and_stable_result_counter' if counter and selected is counter
                              else 'visible_gain_candidate_and_stable_result_suffix',
                        independent_effect_verification=False))
        for field,resolution in prefix_resolutions.items():
            if deltas.get(field)!=resolution['accepted_amount']:
                conflicts[field]=sorted(set(resolution['observed_amounts']+[deltas[field]]))
                continue
            # A later counter cross-check does not turn an already observed
            # badge into a gain inferred from those counters.
            resolution['counter_crosscheck_evidence']=list(proofs.get(field,[]))
            proofs[field]=[p['evidence'] for p in resolution['complete_observations']]
            derived=events[-1].get('result_state_derived_fields',[])
            if field in derived:derived.remove(field)
        if state_crosschecks:events[-1]['state_crosschecks']=state_crosschecks
        if state_derived_provenance:events[-1]['state_derived_provenance']=state_derived_provenance
        if state_constrained_provenance:events[-1]['state_constrained_provenance']=state_constrained_provenance
    for event in events:event.pop('_group_rows',None)
    # A training committed so fast that no result frame was sampled leaves
    # only its preview run and the next panels.  When those confirm the
    # preview, the training is recorded with its preview frames as the
    # evidence window and an explicit marker that no result screen was seen.
    confirmed_panels=set();committed=[]
    for group in preview_only_training_groups(readings,events):
        confirmed=preview_confirmed_gains(readings,group,{})
        if not confirmed:continue
        # Two preview runs of the same option confirmed by the same after
        # panel are one training browsed twice; keep only the later run.
        panel_key=(group['option'],next(iter(confirmed.values()))['after'].get('source_timestamp_ms'))
        if panel_key in confirmed_panels:
            events[:]=[e for e in events if not (e.get('preview_only') and e['training_option']==panel_key[0]
                       and next(iter(e['preview_confirmed_gains'].values()))['after'].get('source_timestamp_ms')==panel_key[1])]
        # A difference measured from a panel read before an earlier such
        # training was committed holds that training's gains, not new ones.
        elif any(next(iter(confirmed.values()))['before']['source_timestamp_ms']<time for time in committed):continue
        confirmed_panels.add(panel_key)
        performance_confirmed=preview_confirmed_gains(readings,group,{},channel='performance')
        # The gain is applied when the card is clicked, i.e. after the last
        # preview frame.  The first reading sampled after the run stands for
        # that moment: it keeps the contribution inside the state comparison
        # that starts at the last preview panel instead of crossing it.
        last_preview=group['preview_rows'][-1]['source_timestamp_ms']
        following=[r for r in readings if type(r.get('source_timestamp_ms')) is int and r['source_timestamp_ms']>last_preview]
        applied_row=min(following,key=lambda r:r['source_timestamp_ms']) if following else group['preview_rows'][-1]
        committed.append(applied_row['source_timestamp_ms'])
        applied=[applied_row['evidence']] if isinstance(applied_row.get('evidence'),str) else []
        deltas={f:p['value'] for f,p in confirmed.items()}
        proofs={f:applied+[e for e in p['evidence'] if e not in applied] for f,p in confirmed.items()}
        performance={f:p['value'] for f,p in performance_confirmed.items()}
        performance_proofs={f:applied+[e for e in p['evidence'] if e not in applied] for f,p in performance_confirmed.items()}
        events.append(dict(id=f'training-{len(events)+1:04d}',kind='training',training_option=group['option'],
            first_seen_ms=applied_row['source_timestamp_ms'],last_seen_ms=applied_row['source_timestamp_ms'],deltas=deltas,
            preview_window_ms=[group['first_seen_ms'],group['last_seen_ms']],
            evidence=applied_row.get('evidence'),field_evidence=proofs,conflicting_readings={},
            repeated_fields=[f for f,paths in proofs.items() if len(paths)>=2],
            direct_gain_provenance=dict(confirmed),result_group=None,
            effect_coverage_verified=False,action_time_ms=None,
            result_screen_observed=False,preview_only=True,
            preview_confirmed_gains=confirmed,preview_confirmed_performance_gains=performance_confirmed,
            result_state_derived_fields=sorted(set(deltas)|set(performance)),
            performance_deltas=performance,performance_evidence=performance_proofs))
    events.extend(_banner_only_training_events(readings,events))
    events.sort(key=lambda e:e['first_seen_ms'])
    return events


_BANNER_FOLLOW_MS=8000
_BANNER_RUN_GAP_MS=1500


def _banner_heading_option(row):
    """The option named by the identity heading itself ("Guts Lvl 1")."""
    proof=(row.get('facts') or {}).get('training_identity_evidence')
    heading=proof.get('heading') if isinstance(proof,dict) else None
    text=heading.get('text','') if isinstance(heading,dict) else ''
    m=re.match(r'(Speed|Stamina|Power|Guts|Wit)\b',text.strip())
    return m[1].lower() if m else None


def _banner_runs(readings):
    """Consecutive frames reading the same training heading and name outside a preview or result screen."""
    rows=[]
    for r in readings:
        if not isinstance(r,dict) or type(r.get('source_timestamp_ms')) is not int:continue
        if r.get('screen') not in ('unknown','training','training_result_candidate'):continue
        facts=r.get('facts') or {}
        name=facts.get('training_name');proof=facts.get('training_identity_evidence')
        if (not isinstance(name,str) or not name.strip() or not isinstance(proof,dict)
                or proof.get('basis')!='same_frame_training_heading_and_name'):continue
        option=_banner_heading_option(r)
        if option is None:continue
        rows.append((r['source_timestamp_ms'],name.strip(),option,r))
    rows.sort(key=lambda x:x[0])
    runs=[];current=None
    for t,name,option,row in rows:
        if current and t-current['last']<=_BANNER_RUN_GAP_MS and (name,option)==(current['name'],current['option']):
            current['rows'].append(row);current['last']=t
        else:
            current=dict(name=name,option=option,first=t,last=t,rows=[row]);runs.append(current)
    return runs


def _banner_follower(readings,run):
    """What completes the banner: a result, a result candidate, or the training's own after-event.

    The same heading and name are shown while the player browses the menu, so
    another heading before the follower, or a follower naming another option
    or training, means the run was a browse and nothing is returned. A result
    candidate inside the run is its own follower. The after-event is the one
    titled with the training's name, or the untitled energy-cost receipt that
    follows a training directly.
    """
    inside=[r for r in run['rows'] if r.get('screen')=='training_result_candidate']
    if inside:return inside[-1]
    for r in sorted((r for r in readings if isinstance(r,dict) and type(r.get('source_timestamp_ms')) is int
                     and run['last']<r['source_timestamp_ms']<=run['last']+_BANNER_FOLLOW_MS),key=lambda r:r['source_timestamp_ms']):
        screen=r.get('screen');facts=r.get('facts') or {}
        name=facts.get('training_name');name=name.strip() if isinstance(name,str) else None
        proof=facts.get('training_identity_evidence')
        identified=isinstance(proof,dict) and proof.get('basis')=='same_frame_training_heading_and_name'
        if screen in ('unknown','training'):
            if identified and (name,_banner_heading_option(r))!=(run['name'],run['option']):return None
            continue
        if screen=='training_result':
            if r.get('training_option') not in (None,run['option']) or (name and name!=run['name']):return None
            return r
        if screen=='training_result_candidate':
            if (name and name!=run['name']) or (identified and _banner_heading_option(r) not in (None,run['option'])):return None
            return r
        if screen=='event_outcome':
            title=r.get('context_title')
            if _same_title(title or '',run['name']):return r
            if not title and any(isinstance(e,dict) and e.get('kind')=='energy_change' and type(e.get('amount')) is int
                                 and e['amount']<0 for e in r.get('effects') or ()):return r
            return None
        return None
    return None


def _banner_only_training_events(readings,events):
    """A training whose animation banner was read but whose result card never classified as a result.

    The option heading and the training name at the top left are shown while
    the chosen training plays. Three or more frames of one heading/name pair,
    followed by a result, a result candidate or the training's own after-event
    with nothing else between, identify the action; the gains stay unread
    unless a panel supplied them. A run next to a result group of the same
    option lends the group its name instead of becoming a second training.
    """
    found=[]
    for run in _banner_runs(readings):
        frames=len({r['source_timestamp_ms'] for r in run['rows']})
        if frames<2:continue
        follower=_banner_follower(readings,run)
        if follower is None:continue
        named_follower=(follower.get('screen')=='event_outcome' and _same_title(follower.get('context_title') or '',run['name'])
                        or (follower.get('screen')=='training_result_candidate'
                            and (follower.get('facts') or {}).get('training_name','').strip()==run['name']))
        if frames<3 and not named_follower:continue
        observations=[dict(name=run['name'],source_timestamp_ms=r['source_timestamp_ms'],evidence=r.get('evidence'),
                           proof=deepcopy(r['facts']['training_identity_evidence']),basis='training_banner')
                      for r in run['rows']]
        follower_time=follower['source_timestamp_ms']
        # The heading stays on screen a moment after the result and is shown
        # while the same card is browsed before it: a run next to a training
        # event of the same option belongs to that event. A result that starts
        # well after the run's own follower is another training.
        def same_training(e):
            if e.get('kind')!='training' or e.get('banner_only'):return False
            if e.get('training_option') not in (None,run['option']) or e.get('training_name') not in (None,run['name']):return False
            if e['first_seen_ms']<=follower_time+2000 and e['last_seen_ms']+3000>=run['first']:return True
            # A preview-only training is dated at the first reading after its
            # previews, which can lie a few seconds past the banner; when its
            # previews precede the banner it is this training.
            # Its preview window may run past the banner's first frame when the
            # state that confirmed the gains was read after the animation began.
            window=e.get('preview_window_ms') if e.get('preview_only') else None
            return (isinstance(window,(list,tuple)) and len(window)==2 and window[0]<=run['first']
                    and run['last']<e['first_seen_ms']<=run['last']+10000)
        compatible=[e for e in events if same_training(e)]
        if compatible:
            owner=min(compatible,key=lambda e:abs(e['first_seen_ms']-run['first']))
            if owner.get('training_name_conflicts'):continue
            if owner.get('training_option') is None:
                owner['training_option']=run['option'];owner['training_option_basis']='training_banner'
            existing=owner.setdefault('training_name_observations',[])
            seen={o.get('source_timestamp_ms') for o in existing if isinstance(o,dict)}
            existing.extend(o for o in observations if o['source_timestamp_ms'] not in seen)
            owner['training_name']=run['name']
            owner['training_name_evidence']=list(dict.fromkeys(list(owner.get('training_name_evidence') or [])
                                                           +[o['evidence'] for o in observations]))
            # A preview-only training is dated by the first reading after its
            # previews, which is where its gains were applied; the animation
            # banner is the earlier witness of the decision itself and puts
            # the action inside its own turn without moving the gains.
            if owner.get('preview_only') and run['first']<=owner['first_seen_ms']:
                owner['action_time_ms']=run['first'];owner['action_time_basis']='training_banner'
            continue
        if follower.get('screen')=='training_result':continue
        if any(e.get('kind')=='training' and not e.get('banner_only') and e['first_seen_ms']<=follower_time<=e['last_seen_ms']
               for e in events):continue
        found.append(dict(id=f'training-{len(events)+len(found)+1:04d}',kind='training',training_option=run['option'],
            first_seen_ms=run['first'],last_seen_ms=run['last'],deltas={},evidence=run['rows'][0].get('evidence'),
            field_evidence={},conflicting_readings={},repeated_fields=[],direct_gain_provenance={},result_group=None,
            effect_coverage_verified=False,action_time_ms=None,result_screen_observed=False,banner_only=True,
            training_name=run['name'],training_name_evidence=[r.get('evidence') for r in run['rows']],
            training_name_observations=observations,training_outcome='unknown'))
        # The result card was never read, but the card's own preview, confirmed
        # field by field by the next home panel, supplies the gains exactly as
        # it does for a preview-only training.
        from .preview_confirmed_gains import preview_confirmed_gains
        pseudo=dict(option=run['option'],first_seen_ms=run['first'],last_seen_ms=run['last'],rows=[])
        confirmed=preview_confirmed_gains(readings,pseudo,{})
        event=found[-1]
        event['action_time_ms']=run['first'];event['action_time_basis']='training_banner'
        if confirmed:
            # The gains were applied after the animation: date the event at the
            # first reading after the banner, as a preview-only training is,
            # so its contributions carry their timing; the decision keeps the
            # banner's time.
            following=[r for r in readings if isinstance(r,dict) and type(r.get('source_timestamp_ms')) is int and r['source_timestamp_ms']>run['last']]
            applied_row=min(following,key=lambda r:r['source_timestamp_ms']) if following else run['rows'][-1]
            event['first_seen_ms']=event['last_seen_ms']=applied_row['source_timestamp_ms']
            event['banner_window_ms']=[run['first'],run['last']]
            if isinstance(applied_row.get('evidence'),str):event['evidence']=applied_row['evidence']
            applied=[applied_row['evidence']] if isinstance(applied_row.get('evidence'),str) else []
            event['deltas']={f:p['value'] for f,p in confirmed.items()}
            # The applied frame leads each field's evidence so the accounting
            # can time the contribution inside the event window.
            event['field_evidence']={f:applied+[e for e in p['evidence'] if e not in applied] for f,p in confirmed.items()}
            event['direct_gain_provenance']=dict(confirmed)
            event['preview_confirmed_gains']=confirmed
            event['result_state_derived_fields']=sorted(confirmed)
            event['repeated_fields']=[f for f,paths in event['field_evidence'].items() if len(paths)>=2]
        performance=preview_confirmed_gains(readings,pseudo,{},channel='performance')
        if performance:
            applied=[event['evidence']] if isinstance(event.get('evidence'),str) else []
            found[-1]['performance_deltas']={f:p['value'] for f,p in performance.items()}
            found[-1]['performance_evidence']={f:applied+[e for e in p['evidence'] if e not in applied] for f,p in performance.items()}
            found[-1]['preview_confirmed_performance_gains']=performance
    return found


def _race_name_variants(observations):
    """The race name read on most frames, and the spellings a glyph off it, or None.

    Every other spelling must be within one edit of it and read on fewer
    frames than it; two spellings read as often as each other, or one that
    differs by more, leave the conflict as it is.
    """
    from collections import Counter
    from .gameplay import _edit_distance
    counts=Counter(str(value) for value in observations if isinstance(value,str))
    if len(counts)<2:return None
    (name,best),*rest=counts.most_common()
    if any(count>=best or _edit_distance(name,other)>1 for other,count in rest):return None
    return name,[other for other,_ in rest]


def _same_title(left,right):
    """One receipt title read with and without stray whitespace is one title."""
    return ' '.join(str(left).split())==' '.join(str(right).split())


_CAPTION_PREFIX_MIN=12


def _same_caption(left,right):
    """A story caption read whole and read with its tail cut off is one caption.

    The reader drops the last word of a long caption on some frames
    ('After the Mainichi Okan: Onward, to' beside '... to Light'). The
    shorter read must be at least twelve characters and end at a word
    boundary of the longer one; short training or card names never qualify.
    """
    if _same_title(left,right):return True
    a=' '.join(str(left).split());b=' '.join(str(right).split())
    short,full=sorted((a,b),key=len)
    # The cut can also fall inside the last word ('A Sharp Turn! Nowhe'
    # beside '... Nowhere.'): a long caption missing at most three glyphs
    # at its end is the same caption.
    if (len(short)>=_CAPTION_PREFIX_MIN and full.startswith(short)
            and (full[len(short):len(short)+1] in (' ',':',',',';','!','?','.') or len(full)-len(short)<=3)):return True
    # One or two misread letters inside a long caption ('Effciency' beside
    # 'Efficiency') are the same caption; short names get no such allowance.
    from .gameplay import _edit_distance
    return len(short)>=_CAPTION_PREFIX_MIN and abs(len(a)-len(b))<=2 and _edit_distance(a.casefold(),b.casefold())<=2


def _caption_head_cut(current,row):
    """The caption read with its head gone, on the same pixels, is the caption still on screen.

    On an event's last frame the caption wipes off from the left: 'Ready for
    a Challenge' reads 'Challenge'. The text alone cannot say so, a suffix may
    be another event's caption, but this read sits where the fuller caption
    sat, the same right edge and the same rows, within the same half second.
    """
    if not current or elapsed(current['last_seen_ms'],row['source_timestamp_ms'])>500:return False
    title=row.get('context_title');previous=current.get('context_title')
    box=row.get('context_title_box');anchor=current.get('context_title_box')
    if not title or not previous or not _caption_box(box) or not _caption_box(anchor):return False
    full=' '.join(str(previous).split());tail=' '.join(str(title).split())
    if not full.endswith(' '+tail):return False
    return all(abs(box[i]-anchor[i])<=6 for i in (1,2,3))


def _caption_box(box):
    return isinstance(box,list) and len(box)==4 and all(type(v) in (int,float) for v in box)


def continued_title(current,row):
    """A weaker full caption can link a truncated title, never invent an award."""
    if not current or elapsed(current['last_seen_ms'],row['source_timestamp_ms'])>500:return None
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
    # How often the whole run spelled a skill that way. A name only one frame
    # ever produced is not corroborated by anything, wherever it turns up.
    hint_name_sightings=Counter(
        effect.get('name') for row in readings for effect in (row.get('effects') or [])
        if effect.get('kind')=='skill_hint_change')
    # The same count by kind, for the recipient and circle spellings the
    # identity passes leave as candidates.
    name_sightings=Counter(
        (effect.get('kind'),effect.get('name')) for row in readings for effect in (row.get('effects') or [])
        if effect.get('kind') in ('skill_hint_change','inheritance_spark','friendship_change','friendship_status'))
    for row in readings:
        # Event reconciliation annotates accepted alternatives. Keep those
        # annotations separate from the original parsed observations.
        time=row['source_timestamp_ms'];effects=deepcopy(row.get('effects',[]));pending=deepcopy(row.get('facts',{}).get('effect_candidates',[]))
        if not effects and not pending:
            # Recognized dialogue or a different screen is evidence of a boundary.
            # A malformed receipt is uncertainty, not positive dialogue evidence.
            # It does not extend the 500 ms gap or supply any accepted effect.
            from .mechanics_audit import plausible_receipt_line
            lines=row.get('ocr',{}).get('neural',[])
            band_top,band_bottom=receipt_rows(790,950)
            long_lines=[l for l in lines if l['confidence']>=95 and band_top<(l['box'][1]+l['box'][3])/2<band_bottom and len(l['text'])>25]
            def repeats_receipt(line):
                if not current or not plausible_receipt_line(line):return False
                text=' '.join(line['text'].casefold().split())
                hint=re.fullmatch(r'gained (\d+) hint level[()s ]*f(?:or|r|o)\s*(.+?)\.?',text)
                if not hint:return False
                ambiguous={c['field'] for c in current['conflicting_readings']}
                for key,effect in current['effects'].items():
                    if key in ambiguous:continue
                    name=' '.join(effect.get('name','').casefold().split())
                    amount=effect.get('amount')
                    # Only damaged grammar may differ: the visible terminal
                    # identity and amount must still match an accepted receipt.
                    # A similar name or different number cannot anchor a gap.
                    if (effect['kind']=='skill_hint_change' and name and type(amount) is int
                            and int(hint[1])==amount and hint[2]==name):
                        return True
                return False
            anchored=any(repeats_receipt(l) for l in long_lines)
            def uncertain_companion(line):
                # Missing letters in the fixed word "went" can leave a
                # friendship receipt unparsed. Its name/amount stay unknown;
                # arbitrary receipt-like prose cannot preserve a boundary.
                text=' '.join(line['text'].casefold().split())
                return plausible_receipt_line(line) and bool(re.fullmatch(
                    r'friendship with .+? [went ]{1,5}up by \d+\.',text))
            narrative=bool(long_lines) and (not anchored or any(
                not repeats_receipt(l) and not uncertain_companion(l) for l in long_lines))
            changed_title=(current and row.get('context_title') and current['context_title']
                           and not _same_caption(row['context_title'],current['context_title']) and not continued_title(current,row)
                           and not _caption_head_cut(current,row))
            if current and (narrative or changed_title or row['screen'] not in ('unknown','event_outcome') or elapsed(current['last_seen_ms'],time)>500):current=None
            elif current and long_lines:
                current.setdefault('receipt_continuity_evidence',[]).append(dict(
                    source_timestamp_ms=time,evidence=row['evidence'],
                    raw_texts=[l['text'] for l in long_lines],basis='matching_named_amount_receipt_ocr_gap',
                    accepted_as_effect=False))
            continue
        title=row.get('context_title')
        continuation=continued_title(current,row)
        if continuation:title=continuation
        head_cut=_caption_head_cut(current,row)
        # The half second a receipt may go unseen is counted in sampling
        # steps (``source_clock``).
        if current is None or elapsed(current['last_seen_ms'],time)>500 or (title and current['context_title'] and not _same_caption(title,current['context_title']) and not continuation and not head_cut):
            current=dict(id=f'outcome-{len(events)+1:04d}',kind='outcome',first_seen_ms=time,last_seen_ms=time,evidence=row['evidence'],
                         context_title=title,context_title_box=row.get('context_title_box'),effects={},field_evidence={},
                         conflicting_readings=[],action_time_ms=None,pending_effects={},effect_observations={})
            events.append(current)
        current['last_seen_ms']=time
        if title and (not current['context_title'] or len(' '.join(title.split()))>=len(' '.join(str(current['context_title']).split()))):
            current['context_title']=title
            if row.get('context_title_box') is not None:current['context_title_box']=row['context_title_box']
        current['context_title_candidate']=row.get('context_title_candidate')
        if head_cut:
            current.setdefault('title_continuation_evidence',[]).append(dict(evidence=row['evidence'],
                observed_title=row.get('context_title'),retained_title=current['context_title'],basis='caption_head_cut_on_same_pixels'))
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
            # A receipt whose wording was repaired is a weaker reading than a
            # clean one, so it corroborates an award the event already holds
            # under a name one glyph away rather than becoming a second award
            # of the same thing.
            if (effect.get('kind')=='skill_hint_change' and key not in current['effects']
                    and effect.get('text_normalization')=='obstructed_hint_wording'):
                from .receipt_names import _identity_single_glyph_variant
                twin=next((other for other,held in current['effects'].items()
                           if held.get('kind')=='skill_hint_change' and held.get('amount')==effect.get('amount')
                           and _identity_single_glyph_variant(held.get('name'),effect.get('name'))),None)
                if twin:
                    current['field_evidence'].setdefault(twin,[]).append(row['evidence'])
                    continue
            current['effect_observations'].setdefault(key,[]).append((time,row['evidence'],effect))
            prior=current['effects'].get(key)
            # A number read from the gain popup stands in only until the
            # receipt line itself reads its number on a frame of the same
            # box: the line outranks the popup, whichever came first.
            popup_new=effect.get('amount_basis')=='gain_popup_on_same_frame'
            popup_prior=bool(prior) and prior.get('amount_basis')=='gain_popup_on_same_frame'
            if prior and popup_prior!=popup_new:
                superseded=prior if popup_prior else effect
                current.setdefault('superseded_gain_popups',[]).append(dict(
                    field=key,popup_amount=superseded.get('amount'),
                    evidence=list(current['field_evidence'].get(key,[])) if popup_prior else [row['evidence']]))
                if popup_prior:
                    current['effects'][key]=effect
                    current['field_evidence'][key]=[row['evidence']]
                continue
            if prior and (prior.get('amount'),prior.get('direction'),prior.get('value'))!=(effect.get('amount'),effect.get('direction'),effect.get('value')):
                current['conflicting_readings'].append(dict(reason='changing_effect_value',field=key,evidence=row['evidence']))
            else:
                current['effects'][key]=effect
                current['field_evidence'].setdefault(key,[]).append(row['evidence'])
    for event in events:
        event.pop('context_title_box',None)
        receipt_observations=event.pop('effect_observations')
        for key,observations in receipt_observations.items():
            conflicts=[c for c in event['conflicting_readings'] if c['field']==key]
            if not conflicts or any(c['reason']!='changing_effect_value' for c in conflicts):continue
            amounts=Counter(e.get('amount') for _,_,e in observations)
            # A number read whole on two frames or more outvotes every read
            # that is it cut short (a leading or trailing digit under the
            # cursor or a particle), however many frames the cut read lasted:
            # a parked cursor keeps a cut read on frame after frame, while
            # nothing on a receipt line adds a digit to a number followed by
            # its full stop. Any other disagreement stays a conflict.
            winners=[v for v,n in amounts.items() if type(v) is int and n>=2 and all(other==v or (type(other) is int and other!=v and (str(v).startswith(str(other)) or str(v).endswith(str(other)))) for other,count in amounts.items())]
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
        from .condition_removal_banner import normalize_condition_removal_event
        event.update(normalize_condition_removal_event(event,rows_by_evidence))
        from .friendship_suffix import resolve as resolve_friendship_suffix
        resolve_friendship_suffix(event,rows_by_evidence)
        from .receipt_names import flag_friendship_identity_conflicts
        flag_friendship_identity_conflicts(event,rows_by_evidence)
        from .receipt_names import flag_inheritance_identity_conflicts
        flag_inheritance_identity_conflicts(event,rows_by_evidence)
        from .hint_identity_fallback import preserve_valid_circle_effect
        preserve_valid_circle_effect(event,rows_by_evidence,effect_kind='inheritance_spark')
        from .inheritance_spark_identity import resolve as resolve_spark_identity
        resolve_spark_identity(event,rows_by_evidence)
        from .receipt_names import collapse_visual_hint_variants
        collapse_visual_hint_variants(event,{r['evidence']:r['source_timestamp_ms'] for r in readings},rows_by_evidence)
        from .receipt_names import collapse_punctuated_hint_variants
        collapse_punctuated_hint_variants(event,rows_by_evidence)
        from .hint_identity_fallback import preserve_valid_circle_effect
        preserve_valid_circle_effect(event,rows_by_evidence)
        # Missing circle glyphs must not turn one visible hint into two awards.
        # Only collapse a suffix alternative when the exact base was also read
        # in this receipt with the same amount; retain the identity uncertainty.
        hints={e['name']:e for e in event['effects'] if e['kind']=='skill_hint_change'}
        removed=[]
        for name,effect in hints.items():
            base=re.sub(r'\s*[O○◯◎]$','',name).strip()
            if base==name or base not in hints or hints[base]['amount']!=effect['amount']:continue
            if effect.get('visual_symbol_observation'):
                # Insufficient continuity cannot invalidate an existing pixel
                # observation. Retain both identities and their unresolved
                # relationship instead of downgrading the observed symbol.
                for field in ('skill_hint_change||'+base,'skill_hint_change||'+name):
                    event['conflicting_readings'].append(dict(field=field,
                        reason='unresolved_circle_variant_relation',name_candidates=[base,name],
                        evidence=list(dict.fromkeys(event['field_evidence'].get('skill_hint_change||'+base,[])
                                                    +event['field_evidence'].get('skill_hint_change||'+name,[])))))
                continue
            target=hints[base]
            target.setdefault('observed_name_candidates',[base]).append(name)
            target['circle_variant_verified']=False
            base_key='skill_hint_change||'+base;variant_key='skill_hint_change||'+name
            event['field_evidence'].setdefault(base_key,[]).extend(event['field_evidence'].get(variant_key,[]))
            removed.append(effect)
        event['effects']=[e for e in event['effects'] if e not in removed]
        from .receipt_names import collapse_uncorroborated_hint_variants
        collapse_uncorroborated_hint_variants(event,hint_name_sightings)
        from .receipt_names import collapse_separator_hint_variants
        collapse_separator_hint_variants(event,rows_by_evidence)
        from .receipt_names import collapse_song_variants
        collapse_song_variants(event,{r['evidence']:r['source_timestamp_ms'] for r in readings})
        ambiguous={c['field'] for c in event['conflicting_readings']}
        event['deltas']={e['field']:e['amount'] for e in event['effects'] if e['kind']=='stat_change' and f'stat_change|{e["field"]}|' not in ambiguous}
    # A supporter or skill name read a glyph off on one frame folds into the
    # name the run read many times; the same award under two spellings on one
    # box becomes one award.
    from .name_vocabulary import build_vocabulary,repair_event_names
    vocabulary=build_vocabulary(readings,events=events)
    for event in events:repair_event_names(event,vocabulary)
    events=collapse_cross_event_hint_duplicates(events,readings)
    events=collapse_cross_event_stat_duplicates(events,readings)
    from .hint_identity_quarantine import quarantine_receipt_identity_conflicts
    from .receipt_names import collapse_uncorroborated_recipient_variants,collapse_uncorroborated_circle_base_variants
    for event in events:
        quarantine_receipt_identity_conflicts(event, rows_by_evidence, effect_kind='skill_hint_change')
        # A candidate the run corroborates nowhere else joins the reading it
        # damaged; the rest stay candidates for the reader to decide.
        collapse_uncorroborated_recipient_variants(event,name_sightings,vocabulary,rows_by_evidence)
        collapse_uncorroborated_circle_base_variants(event,name_sightings)
        # One line of a scrolling list read three ways is still one award.
        from .receipt_names import collapse_same_list_item_hint_variants
        collapse_same_list_item_hint_variants(event,rows_by_evidence)
        ambiguous={c['field'] for c in event['conflicting_readings']}
        event['deltas']={e['field']:e['amount'] for e in event['effects'] if e['kind']=='stat_change' and f'stat_change|{e["field"]}|' not in ambiguous}
        attach_inheritance_occurrences(event,rows_by_evidence)
    attach_supplemental_receipt_evidence(events,readings)
    from .receipt_evidence_projection import project_numeric_receipt_evidence
    project_numeric_receipt_evidence(events,readings)
    return events


def attach_supplemental_receipt_evidence(events,readings):
    """Expose exact alternate proofs without counting OCR views as observations."""
    def signature(effect):
        return tuple(effect.get(k) for k in ('kind','field','name','amount','direction','value'))
    supplemental=[]
    for row in readings:
        sources=list(row.get('supplemental_receipt_observations',[]))
        recovery=row.get('facts',{}).get('numeric_receipt_recovery')
        if recovery:sources.append(recovery)
        for source in list(sources):
            nested=source.get('facts',{}).get('numeric_receipt_recovery')
            if nested:sources.append(nested)
        for source in sources:
            for effect in source.get('effects',[]):
                supplemental.append((row['source_timestamp_ms'],signature(effect),source['evidence']))
    for event in events:
        for effect in event['effects']:
            key='|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))
            proofs=[proof for time,identity,proof in supplemental
                    if event['first_seen_ms']<=time<=event['last_seen_ms'] and identity==signature(effect)]
            if proofs:
                event['field_evidence'][key]=list(dict.fromkeys(event['field_evidence'].get(key,[])+proofs))


def attach_inheritance_occurrences(event,rows_by_evidence):
    """Keep simultaneous receipt evidence separate from unique effect names."""
    from .inheritance_occurrences import summarize
    event.pop('inheritance_scroll_evidence',None)
    evidence=summarize(event,rows_by_evidence)
    if not evidence['by_key']:return
    event['inheritance_occurrence_evidence']=evidence
    retained=[]
    for conflict in event.get('conflicting_readings',[]):
        if conflict.get('reason')!='multiple_same_field_lines':
            retained.append(conflict);continue
        entry=evidence['by_key'].get(conflict.get('field'))
        proof=conflict.get('evidence')
        row=rows_by_evidence.get(proof) if isinstance(proof,str) else None
        if not entry or entry['uncertain'] or not row:
            retained.append(conflict);continue
        same_field=[effect for effect in row.get('effects',[]) if
                    '|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))==conflict['field']]
        payload=entry.get('payload',{})
        matching=all(all(effect.get(k)==v for k,v in payload.items()) for effect in same_field)
        counts=[frame['minimum_observed_count'] for frame in entry.get('frame_counts',[])
                if frame['evidence']==row['evidence'] and frame['source_timestamp_ms']==row['source_timestamp_ms']]
        if len(same_field)<2 or not matching or counts!=[len(same_field)]:
            retained.append(conflict);continue
        event.setdefault('resolved_occurrence_conflicts',[]).append(dict(
            field=conflict['field'],evidence=row['evidence'],source_timestamp_ms=row['source_timestamp_ms'],
            minimum_observed_count=counts[0],basis='simultaneous_disjoint_exact_receipt_lines',
            total_count=None,count_complete=False))
    event['conflicting_readings']=retained
    from .inheritance_scroll import track
    scroll_evidence={}
    for effect in event.get('effects',[]):
        key='|'.join(str(effect.get(k) or '') for k in ('kind','field','name'))
        entry=evidence['by_key'].get(key)
        if (not entry or entry.get('uncertain') is not False
                or entry.get('conflicting_payloads') is not False
                or any(c.get('field')==key for c in retained)):
            continue
        observed=track(event,effect,rows_by_evidence)
        if observed['minimum_observed_count']>entry['minimum_observed_count']:
            scroll_evidence[key]=observed
    if scroll_evidence:
        event['inheritance_scroll_evidence']={'by_key':scroll_evidence}


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
        values={value for _,value in observations if type(value) is int and value>0}
        if not values:continue
        # Prefer an independent source-only phase decision when one exists.
        # The endpoint residual validates it; it never chooses a member of an
        # unresolved numeric set.
        from .training_gain_resolution import resolve_prefix
        phase=resolve_full_component_phase(observations) or resolve_prefix(observations)
        target=event['deltas'].get(field,0)+residual
        if phase is not None:
            accepted=phase['accepted_amount']
            if accepted!=target or accepted<=0:continue
            proofs=[r['evidence'] for r,value in observations if value==accepted]
            event['deltas'][field]=accepted;event['field_evidence'][field]=proofs
            event.setdefault('direct_gain_provenance',{})[field]=direct_gain_proof(
                accepted,[(r,value) for r,value in observations if value==accepted],
                basis='training_gain_source_phase')
            resolutions.append(dict(field=field,amount=accepted,visual_candidates=sorted(values),
                gain_evidence=proofs,before_evidence=before['evidence'],after_evidence=after['evidence'],
                basis='visible_complete_gain_and_source_phase_with_state_crosscheck',
                independent_effect_verification=False))
            continue
        # A singleton visible candidate can be checked against the endpoint;
        # two or more same-shape alternatives remain ambiguous even if one is
        # numerically larger or happens to balance the interval.
        if len(values)!=1 or target!=next(iter(values)):continue
        accepted=next(iter(values));proofs=[r['evidence'] for r,value in observations if value==accepted]
        event['deltas'][field]=accepted;event['field_evidence'][field]=proofs
        resolution=dict(field=field,amount=accepted,visual_candidates=sorted(values),
                        gain_evidence=proofs,before_evidence=before['evidence'],after_evidence=after['evidence'],
                        basis='single_visible_gain_candidate_and_surrounding_state_constraints',independent_effect_verification=False)
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
    from .concert_bonus_info import concert_bonus_events
    events+=concert_bonus_events(readings)
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
    from .rest_actions import reconstruct as rest_actions
    actions+=rest_actions(readings,events)
    actions+=outing_actions(readings,events)
    from .infirmary_actions import reconstruct as infirmary_actions
    actions+=infirmary_actions(readings,events)
    from .race_completion import annotate as annotate_race_completion
    race_results=annotate_race_completion(races(readings,race_reward_observations),readings)
    from .race_action_receipts import assemble_race_action_receipts
    actions += assemble_race_action_receipts(race_results)
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


def _named_identity_observations(event):
    """Distinct frames on which the completed training's own name was read."""
    name=event.get('training_name')
    if not name:return 0
    return len({o.get('source_timestamp_ms') for o in event.get('training_name_observations') or []
                if isinstance(o,dict) and o.get('name')==name and o.get('source_timestamp_ms') is not None})


def training_actions(events):
    """Completed training identity does not depend on readable reward digits.

    A completed training is the turn's action when its gains were read, when
    its result card was sampled on two frames, or when the card's own training
    name was read on two frames: a result card skipped after a single sample
    still names the training it belongs to.
    """
    actions=[]
    for e in events:
        if e['kind']!='training' or not e['training_option']:
            continue
        result_frames=e.get('action_identity_observations',0)>=2
        named_frames=_named_identity_observations(e)>=2
        if not (e['deltas'] or result_frames or named_frames):
            continue
        decided=e.get('action_time_ms') if type(e.get('action_time_ms')) is int else e['first_seen_ms']
        action=dict(kind='training',training_option=e['training_option'],source_timestamp_ms=decided,
                    identity_basis='observed_gains' if e['deltas'] else 'repeated_result_frames' if result_frames
                    else 'training_banner' if e.get('banner_only') else 'repeated_training_name',
                    evidence=e['evidence'],event_id=e['id'],click_timestamp_ms=None,
                    action_identity_evidence=e.get('action_identity_evidence',[]),
                    training_outcome=e.get('training_outcome','unknown'),failure_evidence=e.get('failure_evidence',[]),
                    success_evidence=e.get('success_evidence',[]),outcome_observations=e.get('outcome_observations',[]),
                    training_outcome_conflicts=e.get('training_outcome_conflicts',[]),
                    direct_gain_provenance=e.get('direct_gain_provenance',{}),
                    state_derived_provenance=e.get('state_derived_provenance',{}),
                    state_constrained_provenance=e.get('state_constrained_provenance',{}),
                    effect_coverage_verified=e['effect_coverage_verified'])
        for field in ('training_name','training_level','training_name_evidence',
                      'training_name_observations','training_name_conflicts'):
            if field in e:
                action[field]=deepcopy(e[field])
        if 'result_group' in e:
            action['result_group']=deepcopy(e['result_group'])
        actions.append(action)
    # One training committed twice from frames a moment apart is one commit:
    # the result animation and its transition frames belong to one decision,
    # and a player cannot train twice in two seconds.
    kept=[]
    for action in actions:
        previous=kept[-1] if kept else None
        if (previous is not None and previous['training_option']==action['training_option']
                and abs(action['source_timestamp_ms']-previous['source_timestamp_ms'])<=2000):
            previous.setdefault('repeated_commit_event_ids',[]).append(action['event_id'])
            continue
        kept.append(action)
    return kept


def outing_actions(readings,events):
    actions=[];used=set()
    for event in events:
        if event['kind']!='outcome':continue
        recovery=any(e['kind']=='energy_change' and e['amount']>0 for e in event['effects'])
        companions={e['name'] for e in event['effects'] if e['kind'] in ('friendship_change','friendship_status')}
        mood=any(e['kind']=='mood_change' and e.get('direction')=='up' for e in event['effects'])
        # A scenario outing awards what its own titled scene says (a group
        # outing's stat gains) rather than energy or a companion's mood; the
        # turn transition observed after the scene is its proof below.
        titled=bool(str(event.get('context_title') or '').strip()) and bool(event['effects'])
        if not recovery and not (mood and len(companions)==1) and not titled:continue
        requests=[r for r in readings if r['screen']=='outing_confirmation' and 0<event['first_seen_ms']-r['source_timestamp_ms']<=30000]
        # The confirmation is a dialog dismissed with one more click, and at
        # four frames a second it can fall between two samples. The Recreation
        # menu before it waits for the player, so it is on a frame: with no
        # confirmation sampled, the last menu is the request. The hub check
        # below still rejects a menu the player backed out of.
        confirmed=bool(requests)
        if not confirmed and recovery:
            requests=[r for r in readings if r['screen']=='outing_selection' and 0<event['first_seen_ms']-r['source_timestamp_ms']<=30000]
        if not requests:continue
        request=requests[-1];time=request['source_timestamp_ms']
        intervening=[r for r in readings if time<r['source_timestamp_ms']<event['first_seen_ms']]
        if any(r['screen'] in ('training_result','training_preview','race_result','rest_confirmation') for r in intervening):continue
        # A titled scene is the outing's own dialogue over the hub, not a return
        # to it. The hub check guards the menu-only request, which the player
        # may have backed out of; a sampled confirmation followed by the
        # recovery receipt with no other action between is the outing, and the
        # game shows the hub for a moment before the outing's own scene.
        hubs=[r for r in intervening if r['screen']=='unknown' and r.get('stats',{}).get('values') and not r.get('context_title')]
        extra=[]
        if not recovery:
            extra=outing_turn_evidence(readings,event,request)
            if not extra:continue
        # An outing without a recovery receipt is proven the same way once its
        # confirmation was sampled: by its scene following within moments and
        # the next date after it (``outing_turn_evidence``).
        if not (confirmed and (recovery or extra)) and any(0<elapsed(a['source_timestamp_ms'],b['source_timestamp_ms'])<=500 and a['stats']['values']==b['stats']['values']
               for a,b in zip(hubs,hubs[1:])):continue
        if time in used:continue
        used.add(time)
        action=dict(kind='outing',source_timestamp_ms=event['first_seen_ms'],event_id=event['id'],
                            companion=companions.pop() if len(companions)==1 else None,
                            evidence=list(dict.fromkeys([request['evidence'],event['evidence']]+extra)),click_timestamp_ms=None,
                            basis=('outing_request_followed_by_recovery_receipt_without_another_turn_action' if confirmed
                                   else 'outing_menu_followed_by_recovery_receipt_without_a_sampled_confirmation') if recovery else
                                  'outing_request_support_event_and_observed_next_date')
        if not confirmed:
            action['identity_basis']='outing_menu_and_receipt'
        # A visible confirmation is a request, not proof of the click. Keep
        # the receipt as the execution witness and preserve both source times.
        action['request_observed_at_ms']=time
        action['execution_observed_at_ms']=event['first_seen_ms']
        title=event.get('context_title')
        if isinstance(title,str) and title.strip():
            action['name']=title.strip()
            action['name_basis']='linked_outcome_context_title'
            action['name_evidence']=[event['evidence']]
        actions.append(action)
    return actions


def outing_turn_evidence(readings,event,request):
    """Support outings need not award energy; require an observed turn transition."""
    from .calendar_coverage import date_key
    time=request['source_timestamp_ms'];title=event.get('context_title')
    if not title:return []
    confirmations=[r for r in readings if r['screen']=='outing_confirmation' and 0<=elapsed(r['source_timestamp_ms'],time)<=1000]
    if len({r['source_timestamp_ms'] for r in confirmations})<2:return []
    narrative=[r for r in readings if time<r['source_timestamp_ms']<event['first_seen_ms'] and r.get('context_title')==title]
    if len({r['source_timestamp_ms'] for r in narrative})<2 or narrative[0]['source_timestamp_ms']-time>3000:return []
    # The scene fades the calendar on some of its frames; the frames that
    # show it must agree on one date.
    current={date_key(r.get('stats',{}).get('calendar_text')) for r in narrative}-{None}
    if len(current)!=1:return []
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
        if group and (values!=group[-1]['facts']['performance_points'] or elapsed(group[-1]['source_timestamp_ms'],row['source_timestamp_ms'])>500):
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


PANEL_REAPPEARANCE_MS=10000


def _same_settled_panel(group,row,gap_ms):
    """The result panel is read again after frames the classifier could not read.

    Nothing else was shown in between (an interruption starts a new group
    before this is asked), the gap is short, and the race name, the fan total
    and the gained fans are the settled values already on record. A fan total
    cannot repeat after another race, so this is the same panel.
    """
    if gap_ms>PANEL_REAPPEARANCE_MS:return False
    fans,gained=group['_key'];facts=row['facts']
    if type(fans) is not int or type(gained) is not int or gained<=0:return False
    if (facts.get('fans'),facts.get('fans_gained'))!=(fans,gained):return False
    name=facts.get('race_name')
    return name is None or group['_name'] is None or name==group['_name']


def _counts_on(earlier,later):
    """`later` continues the fan counter that `earlier` caught mid-count.

    The result panel's fan total counts up when it appears: each intermediate
    total is on screen for one sampled frame, the gained-fans figure does not
    change, and nothing else is shown in between.
    """
    if later.get('_interrupted') or len(earlier['rows'])!=1:return False
    if elapsed(earlier['last_seen_ms'],later['first_seen_ms'])>1000:return False
    fans_a,gained_a=earlier['_key'];fans_b,gained_b=later['_key']
    return (type(fans_a) is int and type(fans_b) is int and type(gained_a) is int
            and gained_a==gained_b and fans_b>fans_a)


def _settled(group):
    """The panel's final total stayed on screen for more than one frame."""
    totals=[r['facts'].get('fans') for r in group['rows']]
    return len(totals)>=2 and totals[-1]==totals[-2] and type(totals[-1]) is int


def _merge_fan_counters(groups):
    """Fold the single frames of a counting fan total into the settled panel that follows."""
    out=[];i=0
    while i<len(groups):
        chain=[groups[i]];j=i+1
        while j<len(groups) and _counts_on(chain[-1],groups[j]):
            chain.append(groups[j]);j+=1
        if len(chain)>1 and _settled(chain[-1]):
            merged=chain[0]
            for g in chain[1:]:
                merged['rows'].extend(g['rows']);merged['last_seen_ms']=g['last_seen_ms'];merged['_key']=g['_key']
            out.append(merged);i=j
        else:
            out.append(groups[i]);i+=1
    for index,group in enumerate(out,1):group['id']=f'race-{index:03d}'
    return out


def _settled_fan_total(rows):
    """The last fan total of a non-decreasing counter, with the earlier readings kept."""
    observed=[(r['source_timestamp_ms'],r['facts'].get('fans'),r['evidence']) for r in rows if type(r['facts'].get('fans')) is int]
    if len({v for _,v,_ in observed})<2:return None
    values=[v for _,v,_ in observed]
    if any(b<a for a,b in zip(values,values[1:])):return None
    return dict(fans=values[-1],observations=[dict(source_timestamp_ms=t,fans=v,evidence=e) for t,v,e in observed])


def races(readings,reward_observations=()):
    from .race_reward_sections import annotate as annotate_reward_sections
    groups=[];interrupted=False
    for row in readings:
        if row['screen']!='race_result':
            if row['screen']!='unknown':interrupted=True
            continue
        facts=row['facts'];key=(facts.get('fans'),facts.get('fans_gained'))
        gap=row['source_timestamp_ms']-groups[-1]['last_seen_ms'] if groups else None
        if (not groups or interrupted or key!=groups[-1]['_key']
                or (gap>1000 and not _same_settled_panel(groups[-1],row,gap))):
            groups.append(dict(id=f'race-{len(groups)+1:03d}',first_seen_ms=row['source_timestamp_ms'],last_seen_ms=row['source_timestamp_ms'],
                               _key=key,_name=facts.get('race_name'),_interrupted=bool(interrupted or not groups),rows=[]))
        elif groups[-1]['_name'] is None:groups[-1]['_name']=facts.get('race_name')
        groups[-1]['last_seen_ms']=row['source_timestamp_ms'];groups[-1]['rows'].append(row)
        interrupted=False
    groups=_merge_fan_counters(groups)
    from .race_result_continuity import join_dialog_returns
    groups=join_dialog_returns(groups,readings)
    for group in groups:
        rows=group.pop('rows');group.pop('_key');group.pop('_name',None);group.pop('_interrupted',None);fields={};conflicts={}
        grade_provenance=[]
        for field in ('race_name','placing','fans','fans_gained','course','race_grade'):
            raw_observations=[r['facts'].get(field) for r in rows if r['facts'].get(field) is not None]
            invalid_grade = []
            if field == 'race_grade':
                observations=[]
                for value in raw_observations:
                    normalized = _normalized_race_grade(value)
                    if normalized is None:
                        invalid_grade.append(value)
                    else:
                        observations.append(normalized)
                for row in rows:
                    facts = row.get('facts', {})
                    value = facts.get('race_grade') if isinstance(facts, dict) else None
                    if _normalized_race_grade(value) is None:
                        continue
                    proof = dict(source_timestamp_ms=row.get('source_timestamp_ms'),
                                 evidence=deepcopy(row.get('evidence')))
                    if isinstance(facts, dict) and isinstance(facts.get('race_grade_provenance'), dict):
                        proof['observation'] = deepcopy(facts['race_grade_provenance'])
                    grade_provenance.append(proof)
            else:
                observations=raw_observations
            unique=[]
            for value in observations:
                if value not in unique:unique.append(value)
            if field=='race_name' and len(unique)>1:
                # The header read a glyph off on one frame ('Queen Elizabeth
                # IIl Cup' beside six 'Queen Elizabeth II Cup') is the name
                # the other frames read: one edit, and read on fewer frames.
                variants=_race_name_variants(observations)
                if variants is not None:
                    unique=[variants[0]];group['race_name_variants']=variants[1]
            fields[field]=unique[0] if len(unique)==1 and not invalid_grade else None
            if field=='fans' and len(unique)>1:
                settled=_settled_fan_total(rows)
                if settled is not None:
                    fields['fans']=settled['fans']
                    group['fan_counter_observations']=settled['observations']
                    continue
            if len(unique)>1 or invalid_grade:
                conflicts[field]=raw_observations if field == 'race_grade' else unique
        conditions=list(dict.fromkeys(r['facts']['course_condition'] for r in rows if r['facts'].get('course_condition')))
        if fields['course'] is not None:
            fields['course']=dict(fields['course'],condition=conditions[0] if len(conditions)==1 else None)
        if len(conditions)>1:conflicts['course.condition']=conditions
        group.update(fields,evidence=[r['evidence'] for r in rows],conflicting_readings=conflicts,completed_action='race',item_rewards_complete=False,verified=False)
        if grade_provenance:
            group['race_grade_provenance'] = grade_provenance
        # Preserve stable visible snapshots; scrolling cannot establish item identity
        # or authorize summing repeated quantities into an inventory transaction.
        snapshots=[];pending=[]
        interruptions=group.pop('_visibility_interruptions',[])
        reward_rows=rows+[dict(r,facts=dict(r.get('facts',{}),visible_item_quantities=[])) for r in interruptions]
        group_fan_totals={o['fans'] for o in group.get('fan_counter_observations',[])}
        if reward_observations:
            from .race_reward_inspection import merge_reward_rows
            extras=[]
            for observation in reward_observations:
                if not group['first_seen_ms']<=observation['source_timestamp_ms']<=group['last_seen_ms']:continue
                facts=observation.get('facts',{})
                if (observation['screen']=='race_result' and facts.get('fans_gained')==group['fans_gained']
                        and (facts.get('fans')==group['fans'] or facts.get('fans') in group_fan_totals)):
                    extras.append(observation)
                else:
                    # A non-result or different race interrupts quantity continuity.
                    extras.append(dict(observation,facts=dict(facts,visible_item_quantities=[])))
            reward_rows=merge_reward_rows(reward_rows,extras)
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
            if pending and (not same or elapsed(pending[-1]['source_timestamp_ms'],row['source_timestamp_ms'])>500):
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
        if result and elapsed(result[-1]['last_seen_ms'],span['first_seen_ms'])<=1000:
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
