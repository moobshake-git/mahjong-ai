"""JSON entry point shared by the web server and the CLI."""
from __future__ import annotations

from . import jev
from .advisor import TableState, advise
from .scoring import Meld, Rules, parse_bonus
from .tiles import WINDS, parse_tile, parse_tiles, tile_name, tiles_to_str


def _tiles(value) -> list[int]:
    if not value:
        return []
    if isinstance(value, str):
        return parse_tiles(value)
    return [parse_tile(str(v)) for v in value]


def _wind(value, default="E") -> int:
    t = parse_tile(str(value or default))
    if t not in WINDS:
        raise ValueError(f"'{value}' is not a wind; use E, S, W or N.")
    return t


def state_from_json(p: dict) -> TableState:
    melds = []
    for m in p.get("melds") or []:
        melds.append(Meld(str(m["kind"]).lower(), parse_tile(str(m["tile"])),
                          bool(m.get("concealed", False))))
    return TableState(
        hand=_tiles(p.get("hand")),
        melds=melds,
        seen=_tiles(p.get("seen")),
        bonus=parse_bonus(p.get("bonus") or []),
        seat_wind=_wind(p.get("seat_wind")),
        round_wind=_wind(p.get("round_wind")),
    )


def advise_from_json(p: dict, use_jev: bool = True) -> dict:
    st = state_from_json(p)
    rules = Rules()
    if p.get("min_tai") is not None:
        rules.min_tai = max(0, int(p["min_tai"]))
    if p.get("max_tai") is not None:
        rules.max_tai = max(rules.min_tai, int(p["max_tai"]))
    drawn = parse_tile(str(p["drawn"])) if p.get("drawn") else None
    thrown = parse_tile(str(p["thrown"])) if p.get("thrown") else None
    result = advise(st, rules, drawn=drawn, thrown=thrown, from_left=bool(p.get("from_left")))

    if use_jev:
        table = {
            "hand": tiles_to_str(st.hand),
            "drawn": tile_name(drawn) if drawn is not None else None,
            "thrown": tile_name(thrown) if thrown is not None else None,
            "thrown_by_left_player": bool(p.get("from_left")),
            "melds": [{"kind": m.kind, "tile": tile_name(m.tile)} for m in st.melds],
            "bonus_tiles": st.bonus,
            "seat_wind": tile_name(st.seat_wind),
            "prevailing_wind": tile_name(st.round_wind),
            "tiles_seen_elsewhere": tiles_to_str(st.seen),
        }
        jev.decide(result, table)
    return result
