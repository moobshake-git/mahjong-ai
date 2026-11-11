"""Tile model and text notation.

Every tile kind is an int from 0 to 33:

    0-8    Characters 1m-9m
    9-17   Dots       1p-9p
    18-26  Bamboo     1s-9s
    27-30  Winds      East, South, West, North  (1z-4z)
    31-33  Dragons    White, Green, Red         (5z-7z)

Text notation follows the common "digits then suit letter" style, e.g.
``123m 456p 789s 11z``. Honours can also be written as words: ``E S W N``,
``Wh G R`` or ``east``, ``white`` and so on.
"""
from __future__ import annotations

import re

NUM_KINDS = 34
SUIT_LETTERS = "mps"
SUIT_NAMES = ("Characters", "Dots", "Bamboo")
HONOR_NAMES = ("East", "South", "West", "North", "White", "Green", "Red")

EAST, SOUTH, WEST, NORTH = 27, 28, 29, 30
WHITE, GREEN, RED = 31, 32, 33
WINDS = (EAST, SOUTH, WEST, NORTH)
DRAGONS = (WHITE, GREEN, RED)

_WORDS = {
    "e": EAST, "east": EAST,
    "s": SOUTH, "south": SOUTH,
    "w": WEST, "west": WEST,
    "n": NORTH, "north": NORTH,
    "wh": WHITE, "white": WHITE,
    "g": GREEN, "green": GREEN,
    "r": RED, "red": RED,
}

_TOKEN = re.compile(r"(\d+)([mpsz])|([a-z]+)", re.IGNORECASE)


def is_honor(tile: int) -> bool:
    return tile >= 27


def tile_name(tile: int) -> str:
    """Short notation, e.g. ``5p`` or ``7z``."""
    if tile < 27:
        return f"{tile % 9 + 1}{SUIT_LETTERS[tile // 9]}"
    return f"{tile - 26}z"


def tile_label(tile: int) -> str:
    """Readable name, e.g. ``5 Dots`` or ``Red dragon``."""
    if tile < 27:
        return f"{tile % 9 + 1} {SUIT_NAMES[tile // 9]}"
    kind = "wind" if tile in WINDS else "dragon"
    return f"{HONOR_NAMES[tile - 27]} {kind}"


def parse_tiles(text: str) -> list[int]:
    """Parse notation like ``123m 45p E E Wh`` into a list of tile ints."""
    tiles: list[int] = []
    s = text.replace(",", " ")
    i = 0
    while i < len(s):
        if s[i].isspace():
            i += 1
            continue
        m = _TOKEN.match(s, i)
        if not m:
            raise ValueError(f"Can't read tiles at '{s[i:]}'")
        if m.group(1):
            suit = m.group(2).lower()
            for digit in m.group(1):
                n = int(digit)
                if suit == "z":
                    if not 1 <= n <= 7:
                        raise ValueError(f"Honour tiles are 1z-7z, got {n}z")
                    tiles.append(26 + n)
                else:
                    if not 1 <= n <= 9:
                        raise ValueError(f"Suited tiles are 1-9, got {n}{suit}")
                    tiles.append(SUIT_LETTERS.index(suit) * 9 + n - 1)
        else:
            word = m.group(3).lower()
            if word not in _WORDS:
                raise ValueError(f"Unknown tile '{m.group(3)}'")
            tiles.append(_WORDS[word])
        i = m.end()
    return tiles


def parse_tile(text: str) -> int:
    tiles = parse_tiles(text)
    if len(tiles) != 1:
        raise ValueError(f"Expected one tile, got '{text}'")
    return tiles[0]


def to_counts(tiles) -> list[int]:
    counts = [0] * NUM_KINDS
    for t in tiles:
        counts[t] += 1
    return counts


def tiles_to_str(tiles) -> str:
    """Compact notation, grouped by suit: ``123m 45p 11z``."""
    groups: dict[str, str] = {}
    for t in sorted(tiles):
        name = tile_name(t)
        groups[name[-1]] = groups.get(name[-1], "") + name[0]
    return " ".join(digits + suit for suit, digits in groups.items())
