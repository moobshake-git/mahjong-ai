"""Singapore-style tai scoring for a winning hand.

Values follow the common Singapore table: 1 tai minimum to win, capped at
5 tai, payouts doubling with each tai. Rare or situational hands (Thirteen
Wonders, Nine Gates, winning on a kong replacement, robbing the kong, the
last tile and so on) are deliberately left out of the first version.
Tables vary from group to group, so the knobs live in ``Rules``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .hand import decompositions
from .tiles import DRAGONS, EAST, HONOR_NAMES, WINDS


@dataclass(frozen=True)
class Meld:
    """A called (exposed) meld, or a concealed kong. A chow's tile is its lowest."""
    kind: str  # "chow", "pung" or "kong"
    tile: int
    concealed: bool = False

    def __post_init__(self):
        if self.kind not in ("chow", "pung", "kong"):
            raise ValueError(f"Meld kind must be chow, pung or kong, got '{self.kind}'")
        if self.kind == "chow" and (self.tile >= 27 or self.tile % 9 > 6):
            raise ValueError("A chow is three suited tiles in a row; give its lowest tile (1-7)")

    def tiles(self) -> list[int]:
        if self.kind == "chow":
            return [self.tile, self.tile + 1, self.tile + 2]
        return [self.tile] * (4 if self.kind == "kong" else 3)


# --- Bonus tiles (flowers, seasons, animals) -------------------------------

FLOWERS = ("f1", "f2", "f3", "f4")      # Plum, Orchid, Chrysanthemum, Bamboo
SEASONS = ("s1", "s2", "s3", "s4")      # Spring, Summer, Autumn, Winter
ANIMALS = ("cat", "rat", "rooster", "centipede")
BONUS_LABELS = {
    "f1": "Plum (flower 1)", "f2": "Orchid (flower 2)",
    "f3": "Chrysanthemum (flower 3)", "f4": "Bamboo (flower 4)",
    "s1": "Spring (season 1)", "s2": "Summer (season 2)",
    "s3": "Autumn (season 3)", "s4": "Winter (season 4)",
    "cat": "Cat", "rat": "Rat", "rooster": "Rooster", "centipede": "Centipede",
}


def parse_bonus(text_or_list) -> list[str]:
    """Bonus tiles: f1-f4 (flowers), s1-s4 (seasons), cat, rat, rooster, centipede."""
    items = text_or_list.replace(",", " ").split() if isinstance(text_or_list, str) else text_or_list
    out = []
    for item in items:
        key = str(item).strip().lower()
        if key not in BONUS_LABELS:
            raise ValueError(f"Unknown bonus tile '{item}'. Use f1-f4, s1-s4, cat, rat, "
                             f"rooster or centipede.")
        if key in out:
            raise ValueError(f"Bonus tile '{key}' entered twice; there is only one.")
        out.append(key)
    return out


@dataclass
class Rules:
    name: str = "Singapore"
    min_tai: int = 1
    max_tai: int = 5
    seven_pairs: bool = False   # not part of the usual Singapore table

    def payout(self, tai: int, self_drawn: bool) -> dict:
        """Base units owed, doubling per tai. Multiply by your table's stake."""
        tai = min(tai, self.max_tai)
        if self_drawn:
            each = 2 ** tai
            return {"self_drawn": True, "each_player_pays": each, "total": 3 * each}
        other = 2 ** max(tai - 1, 0)
        return {"self_drawn": False, "discarder_pays": 2 * other,
                "others_pay": other, "total": 4 * other}


@dataclass
class WinContext:
    win_tile: int | None
    self_drawn: bool
    seat_wind: int = EAST
    round_wind: int = EAST
    bonus: tuple = ()


@dataclass
class Score:
    tai: int
    items: list  # [(pattern name, tai), ...]
    payout: dict
    meets_minimum: bool

    def to_dict(self) -> dict:
        return {
            "tai": self.tai,
            "items": [{"name": n, "tai": f} for n, f in self.items],
            "payout": self.payout,
            "meets_minimum": self.meets_minimum,
        }


def _bonus_items(bonus, seat_wind) -> list:
    items = []
    seat_no = seat_wind - EAST + 1
    for group, name in ((FLOWERS, "flowers"), (SEASONS, "seasons")):
        held = [b for b in bonus if b in group]
        if len(held) == 4:
            items.append((f"Complete set of {name}", 2))
        elif f"{group[0][0]}{seat_no}" in held:
            items.append((f"Seat {name[:-1]} ({BONUS_LABELS[group[0][0] + str(seat_no)].split(' (')[0]})", 1))
    animals = [b for b in bonus if b in ANIMALS]
    if len(animals) == 4:
        items.append(("All four animals", 5))
    else:
        items += [(f"Animal: {BONUS_LABELS[a]}", 1) for a in animals]
    return items


def _two_sided_win(cmelds, win_tile) -> bool:
    """Did the winning tile complete a chow from a two-sided wait?"""
    if win_tile is None:
        return True
    for kind, t in cmelds:
        if kind != "chow":
            continue
        rank = t % 9  # 0-based rank of the chow's lowest tile
        if win_tile == t and rank <= 5:          # e.g. 45 waiting on 3 or 6, got 3
            return True
        if win_tile == t + 2 and rank >= 1:      # e.g. 45 waiting on 3 or 6, got 6
            return True
    return False


def _score_standard(pair, cmelds, counts, melds, ctx, rules) -> list:
    all_melds = list(cmelds) + [("pung" if m.kind == "kong" else m.kind, m.tile) for m in melds]
    all_tiles = [i for i in range(34) for _ in range(counts[i])]
    all_tiles += [t for m in melds for t in m.tiles()]
    suits = {t // 9 for t in all_tiles if t < 27}
    has_honors = any(t >= 27 for t in all_tiles)
    pungs = [t for kind, t in all_melds if kind == "pung"]
    limit = rules.max_tai

    items = []
    if not suits:
        items.append(("All honours", limit))
    elif len(suits) == 1:
        items.append(("Half flush", 2) if has_honors else ("Full flush", 4))
    if all(t >= 27 or t % 9 in (0, 8) for t in all_tiles) and suits and has_honors:
        items.append(("Mixed terminals", 5))

    if len(pungs) == 4:
        items.append(("All pungs", 2))
        # Hidden treasure: four concealed pungs. A pung finished by a discard is not concealed.
        concealed_pungs = [t for kind, t in cmelds if kind == "pung"]
        concealed_pungs += [m.tile for m in melds if m.concealed]
        if len(concealed_pungs) == 4 and (ctx.self_drawn or ctx.win_tile == pair):
            items.append(("Hidden treasure", limit))
    elif not pungs:
        scoring_pair = pair in DRAGONS or pair in (ctx.seat_wind, ctx.round_wind)
        if not scoring_pair and _two_sided_win(cmelds, ctx.win_tile):
            if ctx.bonus:
                items.append(("Lesser ping hu", 1))
            else:
                items.append(("Ping hu", 4))

    dragon_pungs = [t for t in pungs if t in DRAGONS]
    if len(dragon_pungs) == 3:
        items.append(("Big three dragons", 5))
    elif len(dragon_pungs) == 2 and pair in DRAGONS:
        items.append(("Small three dragons", 3))
    else:
        items += [(f"{HONOR_NAMES[t - 27]} dragon pung", 1) for t in dragon_pungs]

    wind_pungs = [t for t in pungs if t in WINDS]
    if len(wind_pungs) == 4:
        items.append(("Big four winds", limit))
    elif len(wind_pungs) == 3 and pair in WINDS:
        items.append(("Small four winds", 4))
    else:
        if ctx.seat_wind in pungs:
            items.append(("Seat wind pung", 1))
        if ctx.round_wind in pungs:
            items.append(("Prevailing wind pung", 1))

    if ctx.self_drawn and all(m.concealed for m in melds):
        items.append(("Fully concealed, self-drawn", 1))
    return items + _bonus_items(ctx.bonus, ctx.seat_wind)


def score_win(counts, melds, ctx: WinContext, rules: Rules) -> Score | None:
    """Score a complete hand (concealed counts include the winning tile).

    Returns None if the tiles do not form a winning hand.
    """
    melds = list(melds)
    if sum(counts) != 14 - 3 * len(melds):
        return None
    candidates = [
        _score_standard(pair, cmelds, counts, melds, ctx, rules)
        for pair, cmelds in decompositions(counts)
    ]
    if not candidates:
        return None
    items = max(candidates, key=lambda it: sum(f for _, f in it))
    if not any(f for _, f in items):
        items = [("Chicken hand", 0)]
    tai = min(sum(f for _, f in items), rules.max_tai)
    return Score(tai, items, rules.payout(tai, ctx.self_drawn), tai >= rules.min_tai)
