"""Turn advice: what to discard, whether to call, and which tiles to watch for.

The advisor works from what the player can see: their own concealed tiles,
their called melds, and every tile visible elsewhere (discards and other
players' melds). A tile is "live" if copies of it could still be drawn or
thrown; tiles with no copies left are reported as dead so the player can
switch plans when another player already holds what they need.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .hand import shanten
from .scoring import Meld, Rules, WinContext, score_win
from .tiles import EAST, NUM_KINDS, tile_label, tile_name, to_counts


@dataclass
class TableState:
    hand: list            # concealed tiles (ints)
    melds: list = field(default_factory=list)   # [Meld, ...]
    seen: list = field(default_factory=list)    # tiles visible elsewhere
    bonus: list = field(default_factory=list)   # your flowers, seasons and animals
    seat_wind: int = EAST
    round_wind: int = EAST

    @property
    def called(self) -> int:
        return len(self.melds)

    @property
    def base_size(self) -> int:
        """Concealed tiles held while waiting for a turn."""
        return 13 - 3 * self.called

    def counts(self) -> list:
        return to_counts(self.hand)

    def known(self, extra=()) -> list:
        known = to_counts(self.hand)
        for t in [t for m in self.melds for t in m.tiles()] + list(self.seen) + list(extra):
            known[t] += 1
        return known

    def live(self, extra=()) -> list:
        return [max(0, 4 - k) for k in self.known(extra)]

    def validate(self, extra=()):
        for t, k in enumerate(self.known(extra)):
            if k > 4:
                raise ValueError(
                    f"{tile_label(t)} ({tile_name(t)}) is entered {k} times; there are only 4."
                )


def _stage(sh: int) -> str:
    if sh < 0:
        return "Complete"
    if sh == 0:
        return "Ready: one tile from winning"
    return f"{sh} step{'s' if sh > 1 else ''} from ready"


def _tile(t: int) -> dict:
    return {"tile": tile_name(t), "label": tile_label(t)}


def _useful_tiles(counts, called, sp) -> tuple[int, list]:
    """Shanten of a waiting hand and the tile kinds that would improve it."""
    sh = shanten(counts, called, sp)
    useful = []
    for t in range(NUM_KINDS):
        if counts[t] >= 4:
            continue
        counts[t] += 1
        if shanten(counts, called, sp) < sh:
            useful.append(t)
        counts[t] -= 1
    return sh, useful


def _wait_scores(st: TableState, counts, waits, rules) -> dict:
    """For a ready hand: score each winning tile by discard and by self-draw."""
    out = {}
    for t in waits:
        counts[t] += 1
        ron = score_win(counts, st.melds, WinContext(t, False, st.seat_wind, st.round_wind, tuple(st.bonus)), rules)
        tsumo = score_win(counts, st.melds, WinContext(t, True, st.seat_wind, st.round_wind, tuple(st.bonus)), rules)
        counts[t] -= 1
        if ron and tsumo:
            out[t] = (ron, tsumo)
    return out


def _evaluate_waiting(st: TableState, counts, live, rules) -> dict:
    """Evaluate a waiting-size hand: how close it is and which tiles it needs."""
    called, sp = st.called, rules.seven_pairs
    sh, useful = _useful_tiles(counts, called, sp)
    needs = []
    paying_live = 0
    discard_paying_live = 0  # ready hands only: live tiles that pay even when thrown by others
    best_tai = None

    if sh == 0:
        scores = _wait_scores(st, counts, useful, rules)
        for t in useful:
            if t not in scores:
                continue
            ron, tsumo = scores[t]
            pays = tsumo.meets_minimum
            needs.append({**_tile(t), "live": live[t], "tai": ron.tai,
                          "tai_self_drawn": tsumo.tai, "pays": pays,
                          "pays_on_discard": ron.meets_minimum,
                          "patterns": [n for n, _ in tsumo.items]})
            if pays:
                paying_live += live[t]
            if ron.meets_minimum:
                discard_paying_live += live[t]
            best_tai = max(best_tai or 0, tsumo.tai)
    elif sh == 1:
        # Look one draw ahead: which draws lead to a ready hand that can pay?
        for t in useful:
            counts[t] += 1
            best = None  # (pays, tai)
            for d in range(NUM_KINDS):
                if not counts[d]:
                    continue
                counts[d] -= 1
                if shanten(counts, called, sp) == 0:
                    _, waits = _useful_tiles(counts, called, sp)
                    for w, (_, tsumo) in _wait_scores(st, counts, waits, rules).items():
                        cand = (tsumo.meets_minimum, tsumo.tai)
                        if best is None or cand > best:
                            best = cand
                counts[d] += 1
            counts[t] -= 1
            pays = bool(best and best[0])
            needs.append({**_tile(t), "live": live[t],
                          "tai": best[1] if best else None, "pays": pays})
            if pays:
                paying_live += live[t]
            if best:
                best_tai = max(best_tai or 0, best[1])
    else:
        needs = [{**_tile(t), "live": live[t], "tai": None, "pays": None} for t in useful]
        paying_live = sum(live[t] for t in useful)

    needs.sort(key=lambda n: (n["live"] == 0, not n["pays"], not n.get("pays_on_discard"),
                              -n["live"], n["tile"]))
    return {
        "shanten": sh,
        "stage": _stage(sh),
        "needs": needs,
        "useful_live": sum(n["live"] for n in needs),
        "paying_live": paying_live,
        "discard_paying_live": discard_paying_live,
        "best_tai": best_tai,
    }


def _summary(ev: dict, min_tai: int) -> str:
    live_needs = [n for n in ev["needs"] if n["live"] > 0]
    if not ev["needs"]:
        return "No tile improves this hand."
    if not live_needs:
        return "Every tile this hand needs is already out. Switch plans."
    if ev["shanten"] == 0:
        parts = []
        for n in ev["needs"]:
            if n["live"] == 0:
                parts.append(f"{n['tile']} (none left)")
            elif n["pays_on_discard"]:
                extra = (f", {n['tai_self_drawn']} if self-drawn"
                         if n["tai_self_drawn"] != n["tai"] else "")
                parts.append(f"{n['tile']} ({n['live']} left, {n['tai']} tai{extra})")
            elif n["pays"]:
                parts.append(f"{n['tile']} ({n['live']} left, pays {n['tai_self_drawn']} tai "
                             f"only if you draw it yourself)")
            else:
                parts.append(f"{n['tile']} ({n['live']} left, only {n['tai_self_drawn']} tai)")
        return "Waiting on " + ", ".join(parts) + "."
    if ev["shanten"] == 1:
        return (f"{ev['useful_live']} useful tiles left; {ev['paying_live']} of them lead to a "
                f"hand worth {min_tai}+ tai.")
    return f"{ev['useful_live']} useful tiles left."


def _rank_key(opt: dict):
    return (opt["shanten"], -opt["paying_live"], -opt.get("discard_paying_live", 0),
            -opt["useful_live"], -(opt["best_tai"] or 0))


def _discard_options(st: TableState, rules: Rules) -> list:
    counts = st.counts()
    live = st.live()
    options = []
    for d in range(NUM_KINDS):
        if not counts[d]:
            continue
        counts[d] -= 1
        ev = _evaluate_waiting(st, counts, live, rules)
        counts[d] += 1
        options.append({"action": "discard", **_tile(d), **ev,
                        "summary": _summary(ev, rules.min_tai)})
    options.sort(key=_rank_key)
    return options


def _warnings(best: dict, rules: Rules) -> list:
    warnings = []
    if best["needs"] and best["useful_live"] == 0:
        warnings.append("Other players already hold or have thrown every tile this hand needs. "
                        "Pick a different shape and keep watching discards.")
    elif best["shanten"] == 0 and best["paying_live"] == 0:
        warnings.append(f"You are ready, but no tile still out there makes a hand worth "
                        f"{rules.min_tai} tai. A win would not pay; aim for a bigger hand.")
    return warnings


def _discard_advice(st: TableState, rules: Rules, drawn=None) -> dict:
    options = _discard_options(st, rules)
    result = {"mode": "discard", "options": options, "warnings": []}
    counts = st.counts()
    if shanten(counts, st.called, rules.seven_pairs) == -1:
        score = score_win(counts, st.melds,
                          WinContext(drawn, True, st.seat_wind, st.round_wind, tuple(st.bonus)), rules)
        if score and score.meets_minimum:
            options.insert(0, {"action": "win", "recommended": True, "score": score.to_dict(),
                               "summary": f"Declare the win: {score.tai} tai, self-drawn."})
        elif score:
            result["warnings"].append(
                f"Your hand is complete but worth only {score.tai} tai; the minimum is "
                f"{rules.min_tai}. Keep building.")
    best = next(o for o in options if o["action"] == "discard")
    best["recommended"] = options[0] is best
    result["warnings"] += _warnings(best, rules)
    result["headline"] = (options[0]["summary"] if options[0]["action"] == "win"
                          else f"Discard {best['tile']} ({best['label']}). {best['summary']}")
    return result


def _waiting_advice(st: TableState, rules: Rules) -> dict:
    ev = _evaluate_waiting(st, st.counts(), st.live(), rules)
    summary = _summary(ev, rules.min_tai)
    return {"mode": "waiting", **ev, "summary": summary,
            "headline": f"{ev['stage']}. {summary}", "warnings": _warnings(ev, rules)}


def _call_advice(st: TableState, x: int, from_left: bool, rules: Rules) -> dict:
    counts = st.counts()
    sp = rules.seven_pairs
    pass_ev = _evaluate_waiting(st, counts, st.live(extra=[x]), rules)
    options = [{"action": "pass", **_tile(x), **pass_ev,
                "summary": _summary(pass_ev, rules.min_tai)}]

    counts[x] += 1
    if shanten(counts, st.called, sp) == -1:
        score = score_win(counts, st.melds, WinContext(x, False, st.seat_wind, st.round_wind, tuple(st.bonus)), rules)
        if score:
            options.append({"action": "win", **_tile(x), "score": score.to_dict(),
                            "summary": (f"Win on {tile_name(x)}: {score.tai} tai."
                                        if score.meets_minimum else
                                        f"Completes your hand but only {score.tai} tai "
                                        f"(minimum {rules.min_tai}), so it would not pay.")})
    counts[x] -= 1

    def after_call(remove, meld, action):
        hand = list(st.hand)
        for t in remove:
            hand.remove(t)
        new = replace(st, hand=hand, melds=st.melds + [meld])
        if meld.kind == "kong":
            ev = _evaluate_waiting(new, new.counts(), new.live(), rules)
            opt = {"action": action, **_tile(x), **ev,
                   "summary": "Draw a replacement tile next. " + _summary(ev, rules.min_tai)}
        else:
            best = next(o for o in _discard_options(new, rules))
            opt = {"action": action, **_tile(x),
                   **{k: best[k] for k in ("shanten", "stage", "needs", "useful_live",
                                           "paying_live", "discard_paying_live", "best_tai")},
                   "then_discard": best["tile"], "then_discard_label": best["label"],
                   "summary": f"Then discard {best['tile']}. {best['summary']}"}
        opt["meld"] = {"kind": meld.kind, "tile": tile_name(meld.tile)}
        opt["uses"] = [tile_name(t) for t in remove]
        options.append(opt)

    if counts[x] >= 2:
        after_call([x, x], Meld("pung", x), "pong")
    if counts[x] == 3:
        after_call([x, x, x], Meld("kong", x), "kong")
    if from_left and x < 27:
        for start in (x - 2, x - 1, x):
            if start < 0 or start // 9 != x // 9 or start % 9 > 6:
                continue
            others = [t for t in (start, start + 1, start + 2) if t != x]
            if all(counts[t] for t in others):
                after_call(others, Meld("chow", start), "chow")

    # Pick a recommendation.
    win = next((o for o in options if o["action"] == "win"), None)
    calls = sorted((o for o in options if o["action"] in ("pong", "kong", "chow")), key=_rank_key)
    pass_opt = options[0]
    rec = pass_opt
    if win and win["score"]["meets_minimum"]:
        rec = win
    elif calls:
        c = calls[0]
        closer = c["shanten"] < pass_opt["shanten"] and (c["shanten"] > 0 or c["paying_live"] > 0)
        # Already ready: call only if it lets more of the remaining tiles pay off a discard.
        pays_more = (c["shanten"] == pass_opt["shanten"] == 0
                     and c["discard_paying_live"] > pass_opt["discard_paying_live"])
        if closer or pays_more:
            rec = c
    for o in options:
        o["recommended"] = o is rec

    if rec is pass_opt:
        reason = ("No call here gets you closer to a paying hand. " if calls or win else "")
        rec["reason"] = reason + "Let it go."
    elif rec is win:
        rec["reason"] = "Take the win."
    else:
        if rec["shanten"] < pass_opt["shanten"]:
            rec["reason"] = (f"Calling moves you from {pass_opt['stage'].lower()} "
                             f"to {rec['stage'].lower()}.")
        else:
            rec["reason"] = ("You stay ready, but now a win off another player's discard "
                             "pays too, not just a self-draw.")
        if st.called == 0:
            rec["reason"] += " It gives up the +1 tai for a fully concealed self-drawn win."

    ordered = [rec] + [o for o in [win] + calls + [pass_opt] if o and o is not rec]
    headline = {
        "win": f"Win on {tile_name(x)}!",
        "pass": f"Pass on {tile_name(x)}.",
    }.get(rec["action"], f"Call {rec['action']} on {tile_name(x)}.")
    return {"mode": "call", "thrown": tile_name(x), "options": ordered,
            "headline": f"{headline} {rec['reason']}", "warnings": _warnings(rec, rules)
            if rec["action"] != "win" else []}


def advise(state: TableState, rules: Rules | None = None, drawn: int | None = None,
           thrown: int | None = None, from_left: bool = False) -> dict:
    """Advice for the current moment of a turn.

    - Hand at waiting size and a ``thrown`` tile: should you call it?
    - Hand at waiting size plus a ``drawn`` tile (or one extra tile): what to discard.
    - Hand at waiting size, nothing new: how close you are and what to watch for.
    """
    rules = rules or Rules()
    if drawn is not None and thrown is not None:
        raise ValueError("Enter either the tile you drew or the tile thrown, not both.")
    st = replace(state, hand=list(state.hand) + ([drawn] if drawn is not None else []))
    st.validate(extra=[thrown] if thrown is not None else [])
    base, n = st.base_size, len(st.hand)

    if thrown is not None:
        if n != base:
            raise ValueError(f"To react to a thrown tile your hand needs {base} tiles; it has {n}.")
        result = _call_advice(st, thrown, from_left, rules)
    elif n == base + 1:
        result = _discard_advice(st, rules, drawn)
    elif n == base:
        result = _waiting_advice(st, rules)
    else:
        raise ValueError(f"Your hand has {n} tiles. With {st.called} called meld(s) it should have "
                         f"{base} while waiting, or {base + 1} after drawing.")
    result["rules"] = {"name": rules.name, "min_tai": rules.min_tai}
    return result
