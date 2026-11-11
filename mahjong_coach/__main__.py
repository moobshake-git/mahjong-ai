"""Command line: ``python -m mahjong_coach serve`` or ``python -m mahjong_coach advise ...``."""
from __future__ import annotations

import argparse
import json
import sys

from .api import advise_from_json
from .env import load_dotenv


def _format_needs(needs) -> str:
    parts = []
    for n in needs:
        s = f"{n['tile']} x{n['live']}"
        if n["live"] == 0:
            s += " (dead)"
        elif n.get("pays") is False:
            s += " (won't pay)"
        elif "tai_self_drawn" in n:
            on_discard = f"{n['tai']} tai on discard" if n["pays_on_discard"] else "self-draw only"
            s += f" ({on_discard}, {n['tai_self_drawn']} self-drawn)"
        elif n.get("tai") is not None:
            s += f" (up to {n['tai']} tai)"
        parts.append(s)
    return ", ".join(parts) or "-"


def format_advice(r: dict) -> str:
    lines = [r["headline"]]
    for w in r.get("warnings", []):
        lines.append(f"  ! {w}")
    by = r.get("decided_by")
    if by:
        if by["source"] == "jev":
            agree = "agrees with" if by["agrees_with_engine"] else "differs from"
            lines.append(f"  (Picked by Jev, confidence {by.get('confidence')}; {agree} the engine.)")
        else:
            lines.append(f"  ({by['note']})")
    if r["mode"] == "waiting":
        lines.append(f"  Watch for: {_format_needs(r['needs'])}")
        return "\n".join(lines)
    lines.append("")
    for o in r["options"][:6]:
        mark = "*" if o.get("recommended") else " "
        what = o["action"] if "tile" not in o else f"{o['action']} {o['tile']}"
        if o["action"] == "chow":
            what = f"chow {'-'.join(o['uses'])} + {o['tile']}"
        lines.append(f" {mark} {what:<18} {o['summary']}")
        if o.get("needs"):
            lines.append(f"     needs: {_format_needs(o['needs'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mahjong_coach", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the web app")
    s.add_argument("--host", default="127.0.0.1",
                   help="Use 0.0.0.0 to open it from a phone on the same Wi-Fi")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--cert", help="TLS certificate (PEM) to serve https://, needed for a phone's camera")
    s.add_argument("--key", help="Private key (PEM) for --cert")

    a = sub.add_parser("advise", help="Get advice for one moment of a turn")
    a.add_argument("hand", help="Concealed tiles, e.g. '123m 456p 789s 11z 55z'")
    g = a.add_mutually_exclusive_group()
    g.add_argument("--drawn", help="Tile you just drew")
    g.add_argument("--thrown", help="Tile another player just threw")
    a.add_argument("--from-left", action="store_true", help="Thrown tile came from your left (chow allowed)")
    a.add_argument("--meld", action="append", default=[],
                   help="Called meld as kind:tile, e.g. pung:5z or chow:3m (repeatable)")
    a.add_argument("--seen", default="", help="Tiles visible elsewhere (discards, others' melds)")
    a.add_argument("--bonus", default="", help="Your bonus tiles: f1-f4 s1-s4 cat rat rooster centipede")
    a.add_argument("--seat", default="E", help="Your seat wind (E/S/W/N)")
    a.add_argument("--round", default="E", help="Prevailing wind (E/S/W/N)")
    a.add_argument("--min-tai", type=int, default=1)
    a.add_argument("--max-tai", type=int, default=5)
    a.add_argument("--no-jev", action="store_true", help="Use only the rules engine")
    a.add_argument("--json", action="store_true", help="Print the raw JSON result")

    args = ap.parse_args(argv)
    load_dotenv()
    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port, open_browser=not args.no_browser, cert=args.cert, key=args.key)
        return 0

    melds = []
    for m in args.meld:
        kind, _, tile = m.partition(":")
        melds.append({"kind": kind, "tile": tile})
    payload = {"hand": args.hand, "drawn": args.drawn, "thrown": args.thrown,
               "from_left": args.from_left, "melds": melds, "seen": args.seen,
               "bonus": args.bonus, "seat_wind": args.seat, "round_wind": args.round,
               "min_tai": args.min_tai, "max_tai": args.max_tai}
    try:
        result = advise_from_json(payload, use_jev=not args.no_jev)
    except (ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else format_advice(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
