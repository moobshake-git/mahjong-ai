"""A scripted, simulated camera for demos and tests (no camera needed).

It plays a short Singapore mahjong turn sequence and produces what a tile
detector would report for a single phone camera behind the player's shoulder,
at 5 frames per second, including the problems a real camera has:

- an arm reaching over the table while a tile is drawn or thrown (frames
  marked ``motion``, with tiles missing),
- a tile misread for a single frame (e.g. 3s read as 2s),
- far-away discards read with low confidence,
- detections that drop out for a frame.

The output has exactly the shape ``tracker.Tracker.feed`` takes, so swapping
in a real detector later doesn't change anything downstream.

Standard library only. Deterministic for a given seed.
"""
from __future__ import annotations

import random

from .tracker import SEATS, tile_sort_key

FPS = 5
WIDTH, HEIGHT = 960, 540

# Where each zone sits in the camera picture, and how big a tile looks there.
# Further from the camera means smaller and harder to read.
LAYOUT = {
    ("hand", "me"):       {"x": 120, "y": 440, "w": 50, "h": 68, "step": 54, "conf": (0.90, 0.99)},
    ("bonus", "me"):      {"x": 40,  "y": 355, "w": 36, "h": 48, "step": 40, "conf": (0.86, 0.97)},
    ("meld", "me"):       {"x": 600, "y": 355, "w": 36, "h": 48, "step": 38, "conf": (0.86, 0.97)},
    ("discard", "me"):    {"x": 350, "y": 268, "w": 28, "h": 37, "step": 31, "conf": (0.80, 0.96)},
    ("discard", "right"): {"x": 600, "y": 205, "w": 24, "h": 32, "step": 27, "conf": (0.70, 0.93)},
    ("discard", "left"):  {"x": 190, "y": 205, "w": 24, "h": 32, "step": 27, "conf": (0.70, 0.93)},
    ("discard", "across"): {"x": 380, "y": 150, "w": 21, "h": 28, "step": 24, "conf": (0.64, 0.90)},
    ("meld", "right"):    {"x": 760, "y": 120, "w": 22, "h": 29, "step": 24, "conf": (0.70, 0.92)},
    ("meld", "left"):     {"x": 50,  "y": 120, "w": 22, "h": 29, "step": 24, "conf": (0.70, 0.92)},
    ("meld", "across"):   {"x": 400, "y": 70,  "w": 18, "h": 24, "step": 20, "conf": (0.64, 0.88)},
}

ZONES_FOR_UI = [
    {"name": "Your hand", "box": [100, 425, 830, 100]},
    {"name": "Your flowers", "box": [25, 340, 200, 75]},
    {"name": "Discards", "box": [170, 135, 620, 185]},
    {"name": "Called sets", "box": [30, 55, 900, 75]},
]

# Where each player's arm comes from when they reach to the middle.
ARM_FROM = {"me": (480, 540), "right": (960, 260), "across": (480, 0), "left": (0, 260)}

DEMO_SCRIPT = [
    {"do": "deal", "hand": "1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z 1s 3s".split(), "bonus": ["f1"],
     "caption": "The camera reads your 13 tiles and your Plum flower."},
    {"do": "throw", "seat": "left", "tile": "9m",
     "caption": "The player on your left throws 9 Characters."},
    {"do": "draw", "tile": "3s", "caption": "Your turn: you draw 3 Bamboo."},
    {"do": "discard", "tile": "1s", "caption": "You discard 1 Bamboo, as the coach suggests."},
    {"do": "throw", "seat": "right", "tile": "2s", "caption": "The player on your right throws 2 Bamboo."},
    {"do": "throw", "seat": "across", "tile": "6z", "caption": "The player opposite throws Green dragon."},
    {"do": "pong", "seat": "left", "from": "across", "tile": "6z",
     "caption": "The player on your left calls pong on the Green dragon."},
    {"do": "throw", "seat": "left", "tile": "9p", "caption": "...and throws 9 Dots."},
    {"do": "draw", "tile": "7m", "caption": "Your turn: you draw 7 Characters."},
    {"do": "discard", "tile": "7m", "caption": "You throw it straight back."},
    {"do": "throw", "seat": "right", "tile": "1z", "caption": "The player on your right throws East wind!"},
]

# What a tile is most likely to be mistaken for.
LOOKALIKE = {"1s": "f4", "2s": "3s", "3s": "2s", "5s": "6s", "6s": "5s", "8s": "9s", "9s": "8s",
             "2p": "3p", "3p": "2p", "8p": "9p", "9p": "8p", "5z": "6z", "6z": "5z",
             "1m": "2m", "2m": "1m", "7m": "1m"}


class Table:
    """The true state of the table in the simulation."""

    def __init__(self):
        self.hand: list[str] = []
        self.drawn: str | None = None
        self.bonus: list[str] = []
        self.discards = {s: [] for s in SEATS}
        self.melds = {s: [] for s in SEATS}

    def apply(self, step: dict) -> str:
        """Change the table; returns which seat reached over it."""
        do = step["do"]
        if do == "deal":
            self.hand, self.bonus = list(step["hand"]), list(step.get("bonus", []))
            return "me"
        if do == "throw":
            self.discards[step["seat"]].append(step["tile"])
            return step["seat"]
        if do == "draw":
            self.drawn = step["tile"]
            return "me"
        if do == "discard":
            tiles = self.hand + ([self.drawn] if self.drawn else [])
            tiles.remove(step["tile"])
            self.hand, self.drawn = sorted(tiles, key=tile_sort_key), None
            self.discards["me"].append(step["tile"])
            return "me"
        if do == "pong":
            self.discards[step["from"]].remove(step["tile"])
            self.melds[step["seat"]].append([step["tile"]] * 3)
            return step["seat"]
        raise ValueError(f"unknown step {do!r}")

    def detections(self) -> list[dict]:
        out = []

        def place(zone, seat, tiles, start=0, gap=0, group=None):
            lay = LAYOUT[(zone, seat)]
            for i, t in enumerate(tiles):
                x = lay["x"] + (start + i) * lay["step"] + gap
                d = {"tile": t, "zone": zone, "seat": seat, "box": [x, lay["y"], lay["w"], lay["h"]]}
                if group is not None:
                    d["group"] = group
                out.append(d)

        hand = sorted(self.hand, key=tile_sort_key)
        place("hand", "me", hand)
        if self.drawn:
            place("hand", "me", [self.drawn], start=len(hand), gap=18)   # drawn tile sits a little apart
        place("bonus", "me", self.bonus)
        for seat in SEATS:
            place("discard", seat, self.discards[seat])
            pos = 0
            for g, meld in enumerate(self.melds[seat]):
                place("meld", seat, meld, start=pos, gap=g * 10, group=g)
                pos += len(meld)
        return out


def simulate(script=None, seed: int = 7, settle_frames: int = 14, reach_frames: int = 5,
             noise: float = 1.0) -> dict:
    """Frames for the whole script, plus layout info for drawing them."""
    rng = random.Random(seed)
    table = Table()
    frames, captions = [], []
    t = 0.0
    for i, step in enumerate(script or DEMO_SCRIPT):
        captions.append(step["caption"])
        before = table.detections()
        seat = table.apply(step)
        if step["do"] != "deal":
            # Someone's arm crosses the table: parts of it are hidden and the view is unreliable.
            for k in range(reach_frames):
                visible = [d for d in (before if k < reach_frames // 2 else table.detections())
                           if rng.random() > 0.35]
                frames.append(_frame(t, i, visible, rng, noise, motion=True,
                                     arm={"from": ARM_FROM[seat], "progress": (k + 1) / reach_frames}))
                t += 1 / FPS
        last = i == len(script or DEMO_SCRIPT) - 1
        for _ in range(settle_frames + (settle_frames // 2 if last else 0)):   # let the final view settle too
            frames.append(_frame(t, i, table.detections(), rng, noise))
            t += 1 / FPS
    return {"fps": FPS, "width": WIDTH, "height": HEIGHT, "zones": ZONES_FOR_UI,
            "captions": captions, "frames": frames}


def _frame(t, step, dets, rng, noise, motion=False, arm=None) -> dict:
    out = []
    for d in dets:
        lo, hi = LAYOUT[(d["zone"], d["seat"])]["conf"]
        d = dict(d, conf=round(rng.uniform(lo, hi), 2),
                 box=[d["box"][0] + rng.randint(-1, 1), d["box"][1] + rng.randint(-1, 1), *d["box"][2:]])
        out.append(d)
    if not motion and out:
        r = rng.random()
        if r < 0.08 * noise:
            # A single-frame misread with believable confidence.
            d = rng.choice(out)
            if d["tile"] in LOOKALIKE:
                d.update(tile=LOOKALIKE[d["tile"]], conf=round(rng.uniform(0.62, 0.78), 2), glitch="misread")
        elif r < 0.16 * noise:
            # A far tile read with low confidence (the tracker ignores it).
            d = rng.choice(out)
            d.update(conf=round(rng.uniform(0.35, 0.55), 2), glitch="low_conf")
        elif r < 0.22 * noise:
            out.remove(rng.choice(out))   # a tile missed for one frame
    frame = {"t": round(t, 2), "step": step, "motion": motion, "detections": out}
    if arm:
        frame["arm"] = arm
    return frame
