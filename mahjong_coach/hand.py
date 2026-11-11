"""Hand shape analysis: how far a hand is from winning, and how it splits up.

"Shanten" is the number of tile swaps a hand needs before it is one tile
from winning. -1 means the hand is already complete, 0 means it is ready
(waiting on a winning tile), 1 means one useful draw from ready, and so on.
"""
from __future__ import annotations

from functools import lru_cache


def _pareto(pairs) -> tuple:
    """Keep only (melds, partials) pairs not beaten on both counts."""
    pairs = set(pairs)
    return tuple(sorted(
        p for p in pairs
        if not any(q != p and q[0] >= p[0] and q[1] >= p[1] for q in pairs)
    ))


@lru_cache(maxsize=None)
def _suit_options(c9: tuple) -> tuple:
    """All useful (melds, partials) splits of one suit's 9 counts."""
    i = next((k for k in range(9) if c9[k]), None)
    if i is None:
        return ((0, 0),)
    results = set()

    def take(removes, dm, dt):
        cc = list(c9)
        for k in removes:
            cc[k] -= 1
        for m, t in _suit_options(tuple(cc)):
            results.add((m + dm, t + dt))

    if c9[i] >= 3:
        take((i, i, i), 1, 0)
    if i <= 6 and c9[i + 1] and c9[i + 2]:
        take((i, i + 1, i + 2), 1, 0)
    if c9[i] >= 2:
        take((i, i), 0, 1)
    if i <= 7 and c9[i + 1]:
        take((i, i + 1), 0, 1)
    if i <= 6 and c9[i + 2]:
        take((i, i + 2), 0, 1)
    take((i,), 0, 0)
    return _pareto(results)


def _group_value(counts, need: int) -> int:
    """Best 2*melds + partials, capped so melds + partials <= need."""
    opts = [_suit_options(tuple(counts[s * 9:(s + 1) * 9])) for s in range(3)]
    hm = sum(1 for k in range(27, 34) if counts[k] >= 3)
    ht = sum(1 for k in range(27, 34) if counts[k] == 2)
    best = 0
    for a in opts[0]:
        for b in opts[1]:
            for c in opts[2]:
                m = a[0] + b[0] + c[0] + hm
                t = a[1] + b[1] + c[1] + ht
                mm = min(m, need)
                best = max(best, 2 * mm + min(t, need - mm))
    return best


@lru_cache(maxsize=200_000)
def _standard_shanten(counts: tuple, called: int) -> int:
    need = 4 - called
    c = list(counts)
    best = 8 - 2 * called - _group_value(c, need)
    for p in range(34):
        if c[p] >= 2:
            c[p] -= 2
            best = min(best, 8 - 2 * called - 1 - _group_value(c, need))
            c[p] += 2
    return best


def _seven_pairs_shanten(counts) -> int:
    pairs = sum(1 for x in counts if x >= 2)
    kinds = sum(1 for x in counts if x >= 1)
    return 6 - pairs + max(0, 7 - kinds)


def shanten(counts, called: int = 0, seven_pairs: bool = True) -> int:
    """Shanten of the concealed tiles, given how many melds are already called."""
    s = _standard_shanten(tuple(counts), called)
    if seven_pairs and called == 0:
        s = min(s, _seven_pairs_shanten(counts))
    return s


def decompositions(counts) -> list[tuple[int, tuple]]:
    """Every way to split a complete set of concealed tiles into a pair plus melds.

    Returns ``(pair_tile, ((kind, tile), ...))`` where kind is ``"pung"`` or
    ``"chow"`` and a chow's tile is its lowest tile.
    """
    c = list(counts)
    results: list[tuple[int, tuple]] = []

    def melds(i: int, acc: list, pair: int):
        while i < 34 and c[i] == 0:
            i += 1
        if i == 34:
            results.append((pair, tuple(acc)))
            return
        if c[i] >= 3:
            c[i] -= 3
            acc.append(("pung", i))
            melds(i, acc, pair)
            acc.pop()
            c[i] += 3
        if i < 27 and i % 9 <= 6 and c[i + 1] and c[i + 2]:
            for k in (i, i + 1, i + 2):
                c[k] -= 1
            acc.append(("chow", i))
            melds(i, acc, pair)
            acc.pop()
            for k in (i, i + 1, i + 2):
                c[k] += 1

    for p in range(34):
        if c[p] >= 2:
            c[p] -= 2
            melds(0, [], p)
            c[p] += 2
    return results


def is_seven_pairs(counts) -> bool:
    return sum(counts) == 14 and all(x in (0, 2) for x in counts)
