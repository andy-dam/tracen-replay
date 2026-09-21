"""The recognizer's character confusions, learned from the careers it has read.

A rare spelling of a supporter or skill name is the recognizer's damage to
a known name. How likely each kind of damage is (an "l" read as "I", a
dropped letter, a doubled one) was learned by ``analyzer/lab/learn_confusions.py``
from every rare spelling paired with the known name it stands for across
the analyzed careers, and lives in ``data/confusions.json`` with the two
numbers the resolver needs: how costly a spelling may be to still be a
name, and how much cheaper it must be than the runner-up. The code holds
no thresholds of its own.

The distance between a read spelling and a candidate name is the cheapest
alignment under those probabilities, in nats: a match costs almost
nothing, a confusion the recognizer often makes costs little, one it never
makes costs a lot. Without the table the distance falls back to unit edits
and the limits to the ones the hand-set rules used.
"""
import json
import math
import os
from functools import lru_cache

_TABLE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'confusions.json')

# The hand-set rules this replaces, kept as the fallback when no table
# exists: two unit edits (one for a short name) and a margin of three.
_FALLBACK = dict(limit=2.0, short_limit=1.0, short_letters=6, margin=3.0)


@lru_cache(maxsize=1)
def table():
    """The learned table, or None when the package ships without one."""
    try:
        with open(_TABLE_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or 'substitution' not in data:
        return None
    return data


def _costs(data):
    """Cost functions from the table: -log probabilities, smoothed."""
    floor = float(data.get('floor', 1e-4))
    sub = data['substitution']
    match = data.get('match', {})
    delete = data.get('deletion', {})
    insert = data.get('insertion', {})
    default_match = float(data.get('default_match', 0.97))
    default_delete = float(data.get('default_deletion', 0.01))
    insert_rate = float(data.get('insertion_rate', 0.01))

    def sub_cost(truth, read):
        if truth == read:
            return -math.log(float(match.get(truth, default_match)))
        return -math.log(float(sub.get(truth, {}).get(read, floor)))

    def del_cost(truth):
        return -math.log(float(delete.get(truth, default_delete)))

    def ins_cost(read):
        return -math.log(insert_rate * float(insert.get(read, floor)))

    return sub_cost, del_cost, ins_cost


def limits():
    """(limit, short_limit, short_letters, margin) the resolver applies."""
    data = table()
    if not data:
        return _FALLBACK['limit'], _FALLBACK['short_limit'], _FALLBACK['short_letters'], _FALLBACK['margin']
    d = data.get('decision', {})
    return (float(d.get('limit', _FALLBACK['limit'])), float(d.get('short_limit', d.get('limit', _FALLBACK['short_limit']))),
            int(d.get('short_letters', _FALLBACK['short_letters'])), float(d.get('margin', _FALLBACK['margin'])))


def distance(read, truth, data=None):
    """The cost, in nats, of the recognizer having produced ``read`` for ``truth``.

    With no table: the Levenshtein distance, so the callers' fallback limits
    keep their old meaning.
    """
    data = data if data is not None else table()
    if not data:
        return _levenshtein(read, truth)
    sub_cost, del_cost, ins_cost = _costs(data)
    n, m = len(truth), len(read)
    previous = [0.0]
    for j in range(1, m + 1):
        previous.append(previous[j - 1] + ins_cost(read[j - 1]))
    for i in range(1, n + 1):
        current = [previous[0] + del_cost(truth[i - 1])]
        for j in range(1, m + 1):
            current.append(min(previous[j] + del_cost(truth[i - 1]),
                               current[j - 1] + ins_cost(read[j - 1]),
                               previous[j - 1] + sub_cost(truth[i - 1], read[j - 1])))
        previous = current
    return previous[-1]


def _levenshtein(a, b):
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def align(read, truth):
    """The cheapest alignment as (truth_char or None, read_char or None) steps, by unit edits.

    Used by the learning script to count what the recognizer did to each
    character; the learned table is not consulted, so learning does not
    feed on itself.
    """
    n, m = len(truth), len(read)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + (truth[i - 1] != read[j - 1]))
    steps = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (truth[i - 1] != read[j - 1]):
            steps.append((truth[i - 1], read[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            steps.append((truth[i - 1], None))
            i -= 1
        else:
            steps.append((None, read[j - 1]))
            j -= 1
    return steps[::-1]
