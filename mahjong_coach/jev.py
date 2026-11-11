"""Jev decision layer.

The rules engine (``advisor.py``) works out every legal move and its numbers:
how close it leaves the hand, which tiles it needs, how many are still live
and what a win would pay. Jev (TypeSafe AI's decision model) is then asked a
single Choice question: which of those moves should the player make?

Jev only ever chooses between moves the engine has already checked, so it
cannot suggest an illegal play. If no API key is set, or the call fails,
the engine's own ranking is used and the result says why.

Configuration (environment variables or the git-ignored ``.env`` file; see
``.env.example``, which points at Jev on OpenCode):
    JEV_API_KEY   required to use Jev
    JEV_MODEL     default "typesafe/jev-1.14"
    JEV_API_URL   default "https://thejevai.com/v1/systemone"
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_URL = "https://thejevai.com/v1/systemone"
DEFAULT_MODEL = "typesafe/jev-1.14"
MAX_OPTIONS = 12
USER_AGENT = "mahjong-coach/0.1"
INSTRUCTIONS = (
    "You are coaching a new Singapore mahjong player during their turn. "
    "Pick the move that gives the best chance of completing a hand that pays at least the "
    "minimum tai. Prefer moves whose needed tiles are still live (not already thrown or held "
    "by others), then higher tai. Each option lists the engine's numbers for that move."
)


def is_configured() -> bool:
    return bool(os.environ.get("JEV_API_KEY"))


def option_id(opt: dict) -> str:
    if opt["action"] == "chow":
        return f"chow:{opt['meld']['tile']}"
    if opt["action"] in ("win", "pass") and "tile" not in opt:
        return opt["action"]
    return f"{opt['action']}:{opt.get('tile', '')}"


def _describe(opt: dict) -> dict:
    """What Jev sees for one option: the move and the engine's numbers."""
    desc = {"move": opt.get("summary", "")}
    if "score" in opt:
        desc["win_tai"] = opt["score"]["tai"]
        desc["pays"] = opt["score"]["meets_minimum"]
    for key in ("stage", "shanten", "useful_live", "paying_live", "best_tai", "then_discard"):
        if opt.get(key) is not None:
            desc[key] = opt[key]
    if opt.get("needs"):
        desc["needs"] = [{"tile": n["tile"], "live": n["live"], "pays": n["pays"]}
                         for n in opt["needs"][:8]]
    return desc


def build_request(result: dict, table: dict) -> dict:
    options = result["options"][:MAX_OPTIONS]
    return {
        "model": os.environ.get("JEV_MODEL", DEFAULT_MODEL),
        "state": {"table": table, "situation": result["mode"],
                  "min_tai": result["rules"]["min_tai"]},
        "questions": {
            "move": {
                "type": "choice",
                "instructions": INSTRUCTIONS,
                "criteria": {option_id(o): _describe(o) for o in options},
            }
        },
    }


def _post(body: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        os.environ.get("JEV_API_URL", DEFAULT_URL),
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['JEV_API_KEY']}",
                 "Content-Type": "application/json",
                 # OpenCode's firewall blocks Python's default "Python-urllib" agent (403, code 1010).
                 "User-Agent": USER_AGENT},
        method="POST",
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 529) and attempt == 0:
                time.sleep(1.0)
                continue
            raise
    raise RuntimeError("unreachable")


def decide(result: dict, table: dict, timeout: float = 8.0) -> dict:
    """Ask Jev to choose a move; annotate ``result`` in place and return it.

    Only called for situations with a real choice (discard or call).
    """
    if result.get("mode") not in ("discard", "call"):
        return result
    if not is_configured():
        result["decided_by"] = {"source": "engine", "note": "Set JEV_API_KEY to let Jev choose."}
        return result
    try:
        body = _post(build_request(result, table), timeout)
        # TypeSafe nests answers under data.result; OpenCode returns them at the top level.
        answers = body["answers"] if "answers" in body else body["data"]["result"]["answers"]
        answer = answers["move"]
        choice = answer["choice"]
        probs = answer.get("probabilities", {})
    except (urllib.error.URLError, OSError, KeyError, TypeError, ValueError) as e:
        result["decided_by"] = {"source": "engine",
                                "note": f"Jev was unavailable ({type(e).__name__}); "
                                        "showing the engine's pick."}
        return result

    by_id = {option_id(o): o for o in result["options"]}
    if choice not in by_id:
        result["decided_by"] = {"source": "engine",
                                "note": "Jev returned an unknown move; showing the engine's pick."}
        return result

    engine_pick = next((o for o in result["options"] if o.get("recommended")), result["options"][0])
    for o in result["options"]:
        o["jev_probability"] = probs.get(option_id(o))
        o["engine_pick"] = o is engine_pick
        o["recommended"] = o is by_id[choice]
    picked = by_id[choice]
    result["options"] = [picked] + [o for o in result["options"] if o is not picked]
    result["decided_by"] = {"source": "jev", "model": os.environ.get("JEV_MODEL", DEFAULT_MODEL),
                            "confidence": answer.get("confidence"),
                            "agrees_with_engine": picked is engine_pick}
    if picked is not engine_pick:
        result["headline"] = _headline(picked, result)
    return result


def _headline(opt: dict, result: dict) -> str:
    if opt["action"] == "discard":
        return f"Discard {opt['tile']} ({opt['label']}). {opt['summary']}"
    if opt["action"] == "win":
        return opt["summary"]
    if opt["action"] == "pass":
        return f"Pass on {result.get('thrown')}. {opt['summary']}"
    return f"Call {opt['action']} on {result.get('thrown')}. {opt['summary']}"
