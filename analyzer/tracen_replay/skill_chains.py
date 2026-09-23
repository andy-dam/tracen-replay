"""Joint receipt accounting when another selection hides an intermediate balance."""
from .source_clock import elapsed


def cart_bundles(changes):
    bundles={};unassigned=[]
    for change in changes:
        if len(change['candidate_names'])!=1:unassigned.append(change);continue
        name=change['candidate_names'][0]
        bundle=bundles.setdefault(name,dict(target_name=name,net_cost=0,co_selected_name_candidates=[],evidence=[]))
        bundle['net_cost']+=change['projected_charge_delta'];bundle['evidence']+=change['evidence']
        if change['projected_charge_delta']>0:
            bundle['co_selected_name_candidates']=sorted(set(bundle['co_selected_name_candidates']+change['co_changed_names']))
            bundle['selected_variant']=change['selected_variant'];bundle['variant_evidence']=change['variant_evidence']
    bundles=[b for b in bundles.values() if b['net_cost']>0]
    targets={b['target_name'] for b in bundles}
    for bundle in bundles:
        bundle['prerequisite_candidates']=[name for name in bundle['co_selected_name_candidates'] if name not in targets]
        bundle['co_selected_name_candidates']=[name for name in bundle['co_selected_name_candidates'] if name==bundle['target_name'] or name not in targets]
    return bundles,unassigned


def final_cart(readings,confirmation_ms,start_ms):
    candidates=[r for r in readings if r['screen']=='skill_selection'
                and start_ms<r['source_timestamp_ms']<confirmation_ms
                and confirmation_ms-r['source_timestamp_ms']<=2000]
    tail=[]
    for row in reversed(candidates):
        if row['facts'].get('skill_point_conflict'):return None
        value=row['facts'].get('displayed_skill_points')
        if type(value) is not int:continue
        if tail and (value!=tail[-1]['facts']['displayed_skill_points']
                     or not 0<elapsed(row['source_timestamp_ms'],tail[-1]['source_timestamp_ms'])<=500):break
        tail.append(row)
    if len({r['source_timestamp_ms'] for r in tail})<2:return None
    return dict(value=tail[0]['facts']['displayed_skill_points'],
                first_seen_ms=tail[-1]['source_timestamp_ms'],last_seen_ms=tail[0]['source_timestamp_ms'],
                evidence=[r['evidence'] for r in reversed(tail)],semantics='projected_final_cart_balance')


def reconcile_skill_chains(transactions,readings,spans):
    groups={}
    for transaction in transactions:
        anchors={s['role']:s for s in transaction['balance_evidence']}
        if set(anchors)!= {'before','after'}:continue
        before,after=anchors['before'],anchors['after']
        key=(before['last_seen_ms'],after['first_seen_ms'],before['skill_points'],after['skill_points'])
        groups.setdefault(key,[]).append(transaction)
    for (start,end,initial,settled),members in groups.items():
        if len(members)<2 or initial<=settled:continue
        members=sorted(members,key=lambda t:t['first_seen_ms'])
        receipts=[s for s in spans if s['screen']=='skill_receipt' and start<s['first_seen_ms']<end]
        if any(not any(t['first_seen_ms']<=s['first_seen_ms']<=t['last_seen_ms'] for t in members) for s in receipts):continue
        between=[r for r in readings if start<r['source_timestamp_ms']<end]
        if any(r['screen']=='training_result' or any(e['kind']=='stat_change' and e['field']=='skill_points'
                                                    for e in r['effects']) for r in between):continue
        carts=[]
        for index,transaction in enumerate(members):
            lower=members[index-1]['last_seen_ms'] if index else start
            carts.append(final_cart(readings,transaction['confirmation_first_seen_ms'],lower))
        if any(c is None for c in carts) or carts[-1]['value']!=settled:continue
        balances=[initial]+[c['value'] for c in carts]
        charges=[a-b for a,b in zip(balances,balances[1:])]
        if any(charge<=0 or t['spent_skill_points'] not in (None,charge) for t,charge in zip(members,charges)):continue
        proofs=[p for t,c in zip(members,carts) for p in t['evidence']+c['evidence']]
        for index,(transaction,cart,charge) in enumerate(zip(members,carts,charges)):
            lower=members[index-1]['last_seen_ms'] if index else start
            changes=[c for c in transaction['cart_changes'] if lower<c['first_seen_ms']<transaction['confirmation_first_seen_ms']]
            bundles,unassigned=cart_bundles(changes)
            names=set(transaction['visible_confirmation_names'])
            for change in changes:names.update(change['candidate_names']);names.update(change['co_changed_names'])
            selected=[s for s in transaction['selected_item_candidates'] if s['name'] in names]
            item_sum=sum(s['cost'] for s in selected)
            cart_sum=sum(c['projected_charge_delta'] for c in changes)
            bundle_sum=sum(b['net_cost'] for b in bundles)
            transaction.update(spent_skill_points=charge,deltas={'skill_points':-charge},
                cost_basis='receipt_cart_chain_between_observed_balances',
                charge_verification_scope='joint_receipt_chain',
                joint_charge_verification=dict(transaction_ids=[t['id'] for t in members],
                    observed_before=initial,observed_after=settled,projected_cart_balances=carts,
                    intermediate_balances_independently_observed=False),
                cart_changes=changes,committed_cart_bundles=bundles,unassigned_cart_changes=unassigned,
                selected_item_candidates=selected,item_cost_sum=item_sum,item_cost_sum_matches_charge=item_sum==charge,
                cart_net_cost=cart_sum,bundle_net_cost=bundle_sum,cart_charge_matches_receipt=cart_sum==charge,
                bundle_charge_assignment_complete=bundle_sum==charge and not unassigned,
                evidence=list(dict.fromkeys(proofs)))
    return transactions
