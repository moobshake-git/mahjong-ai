"""Turn a stream of tile detections into game events and table state.

A detector (trained model, vision LLM or the simulator) reports, for each
camera frame, the tiles it can see. One camera sits behind the player's
shoulder, so every detection carries a zone and a seat:

    {"tile": "5p", "zone": "hand", "seat": "me", "conf": 0.93, "box": [x, y, w, h]}

    zone  "hand"     the player's own standing tiles (concealed hand + drawn tile)
          "discard"  face-up discards in the middle, grouped by who threw them
          "meld"     face-up called sets (pong/chow/kong) in front of a player;
                     "group" numbers the set so tiles of one meld stay together
          "bonus"    the player's own flowers, seasons and animals
    seat  "me", "right", "across", "left"  (play goes me -> right -> across -> left)

Detections are noisy: a tile is misread for a frame, an arm covers half the
table, a far discard is read with low confidence. The tracker only accepts a
view of the table once it shows up in at least ``stable_frames`` of the last
``window`` frames (a majority vote, so a one-frame misread can't break or fake
it) with nobody reaching over the table. It then compares that view with the
last accepted one to work out what happened.

Standard library only.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from ..scoring import BONUS_LABELS
from ..tiles import parse_tile, tile_name

SEATS = ("me", "right", "across", "left")
# What the app calls each player, from your seat.
SEAT_NAMES = {"me": "Me", "left": "Beside me (left)", "across": "Oppo", "right": "Beside me (right)"}
ZONES = ("hand", "discard", "meld", "bonus")


def norm_tile(code: str) -> str | None:
    """Canonical code for a detected tile (``5p``, ``7z``, ``f1``, ``cat``), or None."""
    key = str(code).strip().lower()
    if key in BONUS_LABELS:
        return key
    try:
        return tile_name(parse_tile(key))
    except (ValueError, KeyError, IndexError):
        return None


def tile_sort_key(t: str) -> tuple:
    return (t[-1], t[0]) if t[-1] in "mpsz" and t[0].isdigit() else ("~", t)


def meld_of(tiles: list[str]) -> dict | None:
    """Pong / kong / chow from a group of 3-4 tiles, in the engine's meld format."""
    ts = sorted(tiles, key=tile_sort_key)
    if len(ts) in (3, 4) and len(set(ts)) == 1:
        return {"kind": "kong" if len(ts) == 4 else "pung", "tile": ts[0]}
    if len(ts) == 3 and ts[0][1] in "mps" and len({t[1] for t in ts}) == 1:
        n = [int(t[0]) for t in ts]
        if n[1] == n[0] + 1 and n[2] == n[1] + 1:
            return {"kind": "chow", "tile": ts[0]}
    return None


def meld_tiles(m: dict) -> list[str]:
    if m["kind"] == "chow":
        n, s = int(m["tile"][0]), m["tile"][1]
        return [f"{n + i}{s}" for i in range(3)]
    return [m["tile"]] * (4 if m["kind"] == "kong" else 3)


@dataclass(frozen=True)
class View:
    """What one frame shows, after dropping low-confidence detections."""
    hand: tuple            # own hand, left to right as seen
    bonus: tuple           # own bonus tiles, sorted
    discards: tuple        # ((seat, (tiles...)), ...) for every seat, tiles sorted
    melds: tuple           # ((seat, ((tiles...), ...)), ...) for every seat

    def key(self) -> tuple:
        """Identity for stability checks: the hand's order on the rack doesn't matter."""
        return (tuple(sorted(self.hand, key=tile_sort_key)), self.bonus, self.discards, self.melds)

    def discards_of(self, seat: str) -> tuple:
        return dict(self.discards)[seat]

    def melds_of(self, seat: str) -> tuple:
        return dict(self.melds)[seat]


@dataclass
class Tracker:
    stable_frames: int = 5         # votes needed: ~1 second at 5 frames per second
    window: int = 7                # ...out of this many recent frames
    min_conf: float = 0.6
    committed: View | None = None
    coach: dict = field(default_factory=dict)
    _recent: deque = field(default_factory=deque)
    _views: dict = field(default_factory=dict)
    _streak: int = 0
    _last_conflict: tuple | None = None
    frames_seen: int = 0

    def reset(self) -> None:
        self.__init__(stable_frames=self.stable_frames, window=self.window, min_conf=self.min_conf)

    # ---- one frame -------------------------------------------------------

    def view_from(self, detections: list[dict]) -> tuple[View, int]:
        """Build a View from raw detections; returns it and how many were dropped."""
        hand, bonus = [], []
        discards = {s: [] for s in SEATS}
        melds = {s: {} for s in SEATS}
        dropped = 0
        for d in detections:
            tile = norm_tile(d.get("tile", ""))
            zone, seat = d.get("zone"), d.get("seat", "me")
            if tile is None or zone not in ZONES or seat not in SEATS or float(d.get("conf", 0)) < self.min_conf:
                dropped += 1
                continue
            x = (d.get("box") or [0])[0]
            if zone == "hand" and seat == "me":
                hand.append((x, tile))
            elif zone == "bonus" and seat == "me":
                bonus.append(tile)
            elif zone == "discard":
                discards[seat].append(tile)
            elif zone == "meld":
                melds[seat].setdefault(d.get("group", 0), []).append((x, tile))
            else:
                dropped += 1   # other players' bonus tiles don't affect the advice
        view = View(
            hand=tuple(t for _, t in sorted(hand)),
            bonus=tuple(sorted(bonus)),
            discards=tuple((s, tuple(sorted(discards[s], key=tile_sort_key))) for s in SEATS),
            melds=tuple((s, tuple(tuple(sorted((t for _, t in g), key=tile_sort_key))
                                  for _, g in sorted(melds[s].items(), key=lambda kv: min(kv[1])[0])))
                        for s in SEATS),
        )
        return view, dropped

    def feed(self, frame: dict) -> dict:
        """Process one frame. Returns events (usually none), the coach state when it
        changed, and a status line for the UI."""
        self.frames_seen += 1
        out = {"events": [], "state": None}
        if frame.get("motion"):
            # Someone is reaching over the table: never trust a half-covered view.
            self._recent.clear()
            self._views.clear()
            self._streak = 0
            out["status"] = self._status("hands over the table, waiting")
            return out

        view, dropped = self.view_from(frame.get("detections", []))
        self._recent.append(view.key())
        self._views[view.key()] = view
        while len(self._recent) > self.window:
            self._recent.popleft()
        key, self._streak = Counter(self._recent).most_common(1)[0]
        view = self._views[key]
        self._views = {k: v for k, v in self._views.items() if k in self._recent}

        if self._streak >= self.stable_frames and (self.committed is None or key != self.committed.key()):
            problem = self.check(view)
            if problem:
                if self._last_conflict != key:
                    self._last_conflict = key
                    out["events"].append({"type": "conflict", "text": problem})
                out["status"] = self._status("view doesn't add up, check the camera")
                return out
            out["events"] = self.diff(self.committed, view)
            self.committed = view
            out["state"] = dict(self.coach)
        out["status"] = self._status("steady" if self._streak >= self.stable_frames else "settling",
                                     dropped=dropped)
        return out

    def _status(self, text: str, dropped: int = 0) -> dict:
        return {"text": text, "streak": min(self._streak, self.stable_frames),
                "needed": self.stable_frames, "dropped": dropped, "frames": self.frames_seen}

    # ---- sanity checks ----------------------------------------------------

    def check(self, v: View) -> str | None:
        my_melds = [m for m in (meld_of(list(g)) for g in v.melds_of("me")) if m]
        if len(my_melds) != len(v.melds_of("me")):
            return "One of your called sets doesn't look like a pong, kong or chow."
        base = 13 - 3 * len(my_melds)
        if len(v.hand) not in (base, base + 1):
            return (f"I can see {len(v.hand)} tiles in your hand but expect {base} or {base + 1}. "
                    "A tile may be hidden or misread.")
        counts = Counter(v.hand)
        for _, ts in v.discards:
            counts.update(ts)
        for _, groups in v.melds:
            for g in groups:
                counts.update(g)
        over = sorted(t for t, n in counts.items() if n > 4 and t not in BONUS_LABELS)
        if over:
            return f"More than four {', '.join(over)} on the table, so one must be misread."
        return None

    # ---- what changed -------------------------------------------------------

    def diff(self, prev: View | None, cur: View) -> list[dict]:
        events: list[dict] = []
        my_melds = [meld_of(list(g)) for g in cur.melds_of("me")]
        base = 13 - 3 * len(my_melds)
        coach = self.coach

        if prev is None:
            events.append({"type": "hand_set", "tiles": list(cur.hand),
                           "text": f"Read your hand: {len(cur.hand)} tiles."})
            hand = list(cur.hand)
            coach.update(hand=hand, drawn=None, thrown=None, from_left=False)
            if len(hand) == base + 1:            # dealt as East: the 14th tile acts as the draw
                coach["drawn"] = hand.pop()
                coach["hand"] = hand
            for b in cur.bonus:
                events.append({"type": "bonus", "tile": b, "text": f"Bonus tile: {BONUS_LABELS[b]}."})
            self._finish(cur, my_melds)
            return events

        def added(a, b):
            return list((Counter(b) - Counter(a)).elements())

        for b in added(prev.bonus, cur.bonus):
            events.append({"type": "bonus", "tile": b, "text": f"You put out a bonus tile: {BONUS_LABELS[b]}."})

        new_my_melds = added(prev.melds_of("me"), cur.melds_of("me"))
        for g in new_my_melds:
            m = meld_of(list(g))
            events.append({"type": "called", "meld": m, "text": f"You called {kind_word(m)} ({' '.join(g)})."})

        for seat in SEATS[1:]:
            for g in added(prev.melds_of(seat), cur.melds_of(seat)):
                m = meld_of(list(g)) or {"kind": "set", "tile": g[0]}
                events.append({"type": "meld", "seat": seat, "meld": m,
                               "text": f"{who(seat)} called {kind_word(m)} ({' '.join(g)})."})

        hand_in = added(prev.hand, cur.hand)
        hand_out = added(cur.hand, prev.hand)
        my_discards = added(prev.discards_of("me"), cur.discards_of("me"))

        if not new_my_melds:
            for t in hand_in:
                events.append({"type": "drew", "tile": t, "text": f"You drew {t}."})
        for t in my_discards:
            events.append({"type": "discarded", "tile": t, "text": f"You discarded {t}."})
        unexplained = Counter(hand_out) - Counter(my_discards)
        if new_my_melds:
            for g in new_my_melds:
                unexplained -= Counter(g)
        if +unexplained and not new_my_melds:
            events.append({"type": "hand_changed", "tiles": list(unexplained.elements()),
                           "text": "Your hand changed in a way I didn't see happen; please check it."})

        thrown_now = []
        for seat in SEATS[1:]:
            for t in added(prev.discards_of(seat), cur.discards_of(seat)):
                thrown_now.append((seat, t))
                events.append({"type": "thrown", "seat": seat, "tile": t,
                               "text": f"{who(seat)} threw {t}."})

        # ---- update the coach state the page and /api/advise use ----
        hand = list(cur.hand)
        my_turn = bool(hand_in or my_discards or new_my_melds)
        if len(hand) == base + 1:
            drew = [e["tile"] for e in events if e["type"] == "drew"]
            pick = drew[-1] if drew else (coach.get("drawn") if coach.get("drawn") in hand else hand[-1])
            hand.remove(pick)
            coach["drawn"] = pick
        else:
            coach["drawn"] = None
        coach["hand"] = hand
        if thrown_now:
            seat, t = thrown_now[-1]
            coach["thrown"], coach["from_left"] = t, seat == "left"
        elif my_turn:
            coach["thrown"], coach["from_left"] = None, False
        elif coach.get("thrown"):
            # The last throw was claimed by someone else: it left the discard pool.
            still = any(coach["thrown"] in ts for s, ts in cur.discards if s != "me")
            if not still:
                coach["thrown"], coach["from_left"] = None, False
        self._finish(cur, my_melds)
        return events

    def _finish(self, cur: View, my_melds: list[dict]) -> None:
        seen = []
        for _, ts in cur.discards:
            seen += ts
        for seat, groups in cur.melds:
            if seat != "me":
                for g in groups:
                    seen += g
        if self.coach.get("thrown") in seen:
            seen.remove(self.coach["thrown"])
        self.coach.update(melds=my_melds, bonus=list(cur.bonus), seen=sorted(seen, key=tile_sort_key))
        self.coach.setdefault("thrown", None)
        self.coach.setdefault("from_left", False)


def kind_word(m: dict) -> str:
    return {"pung": "pong"}.get(m["kind"], m["kind"])


def who(seat: str) -> str:
    return SEAT_NAMES.get(seat, "You") if seat != "me" else "You"


ADVICE_EVENTS = {"hand_set", "drew", "thrown", "called", "bonus", "discarded"}
