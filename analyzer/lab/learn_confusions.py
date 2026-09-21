"""Learn the recognizer's character confusions from analyzed careers, and judge the learned resolver.

Every rare spelling of a supporter or skill name in a career is paired with
the one known name of the same run it can only be (by plain edits, which
is what the hand-set resolver did); the alignment of each pair says what
the recognizer did to each character. The counts become the probabilities
in ``tracen_replay/data/confusions.json``, and the two decision numbers
are set from the training pairs: the limit is the costliest pair still
accepted, the margin the cost of one typical confusion.

Usage:
  learn_confusions.py REPORT...            learn from all, write the table
  learn_confusions.py --evaluate REPORT... leave-one-career-out: for each
        career, learn from the others and count its rare names resolved by
        the hand-set rules and by the learned ones, and where they disagree
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from tracen_replay import confusions, name_vocabulary as nv  # noqa: E402

TABLE = os.path.join(os.path.dirname(__file__), '..', 'tracen_replay', 'data', 'confusions.json')
REACH = 3  # plain edits within which a rare spelling can only be one known name


def load(path):
    with open(path, encoding='utf-8') as f:
        r = json.load(f)
    g = r['gameplay_tracking']
    return r['source'].get('name', path), nv.build_vocabulary(g['readings'], g.get('owned_skills') or (), events=g.get('events'))


def pairs_of(vocabulary):
    """(rare shape, known shape, group) for every rare spelling that can only be one known name."""
    out = []
    for group in ('supporter', 'skill'):
        sightings = vocabulary[group]
        known = {name: nv.letters(name) for name, count in sightings.items() if count >= nv.KNOWN_MIN[group]}
        for name, count in sightings.items():
            if count >= nv.KNOWN_MIN[group]:
                continue
            shape = nv.letters(name)
            if not shape:
                continue
            together = vocabulary.get('together', {}).get(name, ())
            near = [(nv.edits(shape, kshape), kname) for kname, kshape in known.items()
                    if kname not in together and nv.circles(kname) == nv.circles(name)]
            near = [(d, k) for d, k in near if d <= REACH]
            if len(near) == 1 and near[0][0] > 0:
                out.append((shape, known[near[0][1]], group))
    return out


def learn(vocabularies):
    """The table from the pairs and the known names of these careers."""
    sub, match, delete, insert = defaultdict(Counter), Counter(), Counter(), Counter()
    seen = Counter()
    pair_count = 0
    for vocabulary in vocabularies:
        for group in ('supporter', 'skill'):
            for name, count in vocabulary[group].items():
                if count >= nv.KNOWN_MIN[group]:
                    for c in nv.letters(name):
                        match[c] += count
                        seen[c] += count
        for read, truth, _ in pairs_of(vocabulary):
            pair_count += 1
            for t, r in confusions.align(read, truth):
                if t is None:
                    insert[r] += 1
                elif r is None:
                    delete[t] += 1
                    seen[t] += 1
                elif t == r:
                    match[t] += 1
                    seen[t] += 1
                else:
                    sub[t][r] += 1
                    seen[t] += 1
    alphabet = set(seen) | set(insert) | {r for t in sub for r in sub[t]}
    k = 0.5  # add-k smoothing so a confusion never seen is unlikely, not impossible
    floor = 1e-4
    table = dict(schema='tracen-replay/confusions-v1', pairs=pair_count, careers=len(vocabularies), floor=floor,
                 substitution={}, match={}, deletion={}, insertion={})
    for t in sorted(alphabet):
        total = seen[t] + k * (len(alphabet) + 1)
        table['match'][t] = round((match[t] + k) / total, 6)
        table['deletion'][t] = round((delete[t] + k) / total, 6)
        subs = {r: round((n + k) / total, 6) for r, n in sub[t].items()}
        if subs:
            table['substitution'][t] = subs
    total_insert = sum(insert.values()) + k * len(alphabet)
    table['insertion'] = {r: round((n + k) / total_insert, 6) for r, n in insert.items()}
    table['insertion_rate'] = round((sum(insert.values()) + k) / (sum(seen.values()) + k), 6)
    table['default_match'] = round(sum(match.values()) / max(1, sum(seen.values())), 6)
    table['default_deletion'] = round((sum(delete.values()) + k) / (sum(seen.values()) + k), 6)
    # The decision numbers, from the training pairs under the table itself.
    pairs = [(read, truth) for vocabulary in vocabularies for read, truth, _ in pairs_of(vocabulary)]
    costs = sorted(confusions.distance(read, truth, table) for read, truth in pairs)
    short = sorted(confusions.distance(read, truth, table) for read, truth in pairs if len(read) <= nv.SHORT_NAME_LETTERS)
    single = sorted(-math.log(p) for t in table['substitution'] for p in table['substitution'][t].values())
    limit = costs[-1] if costs else 4.0
    # A short name has few letters to tell it by, so its limit is learned
    # from the short pairs alone (the hand-set rule allowed one edit
    # instead of two); without any, half the long limit.
    short_limit = short[-1] if short else limit / 2
    margin = single[len(single) // 2] if single else 3.0
    table['decision'] = dict(limit=round(limit, 4), short_limit=round(short_limit, 4), short_letters=nv.SHORT_NAME_LETTERS,
                             margin=round(margin, 4), short_pairs=len(short),
                             note='limit: the costliest training pair; short_limit: the costliest short pair; margin: the median cost of one confusion')
    return table


def resolve_learned(name, sightings, group, together, table):
    """The learned resolver: the same shape as name_vocabulary.repair with learned costs and limits."""
    if not name or (sightings.get(name) or 0) >= nv.KNOWN_MIN[group]:
        return None
    shape = nv.letters(name)
    if not shape:
        return None
    limit, short_limit, short_letters, margin = table['decision']['limit'], table['decision']['short_limit'], table['decision']['short_letters'], table['decision']['margin']
    bound = short_limit if len(shape) <= short_letters else limit
    near = []
    for known, count in sightings.items():
        if known == name or count < nv.KNOWN_MIN[group] or known in together:
            continue
        cost = confusions.distance(shape, nv.letters(known), table)
        if cost <= bound + margin:
            if nv.circles(known) != nv.circles(name):
                return None
            near.append((cost, known))
    if len(near) != 1 or near[0][0] > bound:
        return None
    return near[0][1]


def resolve_rules(name, sightings, group, together):
    """The hand-set resolver as it was: unit edits, two (one for a short name), a margin of three."""
    if not name or (sightings.get(name) or 0) >= nv.KNOWN_MIN[group]:
        return None
    shape = nv.letters(name)
    if not shape:
        return None
    limit = 1 if len(shape) <= nv.SHORT_NAME_LETTERS else 2
    near = []
    for known, count in sightings.items():
        if known == name or count < nv.KNOWN_MIN[group] or known in together:
            continue
        distance = nv.edits(shape, nv.letters(known))
        if distance <= 3:
            if nv.circles(known) != nv.circles(name):
                return None
            near.append((distance, known))
    if len(near) != 1 or near[0][0] > limit:
        return None
    return near[0][1]


def evaluate(paths):
    loaded = [load(p) for p in paths]
    print(f"{'career':44} {'rare':>5} {'rules':>6} {'learned':>8} {'both':>5} {'differ':>6}")
    totals = Counter()
    for i, (name, vocabulary) in enumerate(loaded):
        table = learn([v for j, (_, v) in enumerate(loaded) if j != i])
        rare = rules = learned = both = differ = 0
        for group in ('supporter', 'skill'):
            for spelling, count in vocabulary[group].items():
                if count >= nv.KNOWN_MIN[group]:
                    continue
                rare += 1
                together = vocabulary.get('together', {}).get(spelling, ())
                a = resolve_rules(spelling, vocabulary[group], group, together)
                b = resolve_learned(spelling, vocabulary[group], group, together, table)
                rules += a is not None
                learned += b is not None
                both += a is not None and b is not None and a == b
                if a is not None and b is not None and a != b:
                    differ += 1
                    print(f"   differ: {spelling!r}: rules {a!r}, learned {b!r}")
                elif b is not None and a is None:
                    print(f"   learned only: {spelling!r} -> {b!r} (cost {confusions.distance(nv.letters(spelling), nv.letters(b), table):.2f}, limit {table['decision']['limit']})")
        print(f"{name[:44]:44} {rare:5} {rules:6} {learned:8} {both:5} {differ:6}")
        totals.update(dict(rare=rare, rules=rules, learned=learned, both=both, differ=differ))
    print(f"{'total':44} {totals['rare']:5} {totals['rules']:6} {totals['learned']:8} {totals['both']:5} {totals['differ']:6}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('reports', nargs='+')
    ap.add_argument('--evaluate', action='store_true')
    args = ap.parse_args()
    if args.evaluate:
        evaluate(args.reports)
        return
    vocabularies = [load(p)[1] for p in args.reports]
    table = learn(vocabularies)
    os.makedirs(os.path.dirname(TABLE), exist_ok=True)
    with open(TABLE, 'w', encoding='utf-8') as f:
        json.dump(table, f, ensure_ascii=False, indent=1, sort_keys=True)
    print(f"{table['pairs']} pairs from {table['careers']} careers; decision {table['decision']}; written {TABLE}")


if __name__ == '__main__':
    main()
