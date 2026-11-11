import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from mahjong_coach import Meld, Rules, TableState, WinContext, advise, parse_tile, parse_tiles, score_win
from mahjong_coach.api import advise_from_json
from mahjong_coach.hand import shanten
from mahjong_coach.tiles import SOUTH, to_counts


def counts(text):
    return to_counts(parse_tiles(text))


def score(text, melds=(), win="", self_drawn=False, seat="E", rnd="E", bonus=()):
    ctx = WinContext(parse_tile(win) if win else None, self_drawn,
                     parse_tile(seat), parse_tile(rnd), tuple(bonus))
    return score_win(counts(text), [Meld(k, parse_tile(t)) for k, t in melds], ctx, Rules())


class TileTests(unittest.TestCase):
    def test_parse_notation_and_words(self):
        self.assertEqual(parse_tiles("123m 9p 1s 77z"), [0, 1, 2, 17, 18, 33, 33])
        self.assertEqual(parse_tiles("E S W N Wh G R"), list(range(27, 34)))

    def test_rejects_bad_tiles(self):
        for bad in ("0m", "8z", "xyz"):
            with self.assertRaises(ValueError):
                parse_tiles(bad)


class ShantenTests(unittest.TestCase):
    def test_complete_ready_and_far(self):
        self.assertEqual(shanten(counts("123m456p789s11z55z5z"), seven_pairs=False), -1)
        self.assertEqual(shanten(counts("123m456p789s11z13s"), seven_pairs=False), 0)
        self.assertGreaterEqual(shanten(counts("19m19p19s1234567z"), seven_pairs=False), 4)

    def test_called_melds_reduce_tiles_needed(self):
        self.assertEqual(shanten(counts("11m"), called=4, seven_pairs=False), -1)
        self.assertEqual(shanten(counts("1m"), called=4, seven_pairs=False), 0)


class SingaporeScoringTests(unittest.TestCase):
    def test_chicken_hand_scores_zero_and_does_not_pay(self):
        s = score("111m234m456p789s11p", win="1m")
        self.assertEqual(s.tai, 0)
        self.assertFalse(s.meets_minimum)

    def test_ping_hu_needs_two_sided_wait_and_no_bonus(self):
        self.assertEqual(score("123m456m456p789s22p", win="1m").tai, 4)          # 23m waited on 1m/4m
        self.assertEqual(score("123m456m456p789s22p", win="2m").tai, 0)          # closed wait 13m
        self.assertEqual(score("123m456m456p789s22p", win="1m", bonus=("cat",)).tai, 2)  # lesser 1 + cat 1

    def test_flush_pungs_and_dragons(self):
        self.assertEqual(score("111m222m333m555m99m", win="9m").tai, 5)  # full flush 4 + all pungs 2, capped at 5
        self.assertEqual(score("234m789p77z", melds=[("pung", "5z"), ("pung", "6z")], win="2m").tai, 3)  # small three dragons
        s = score("123m456p", melds=[("pung", "5z"), ("pung", "6z")], win="1m")
        self.assertEqual(s, None)  # incomplete (only 8 tiles of 14)

    def test_winds_and_bonus_tiles(self):
        s = score("123m456p789s111z22p", win="1m", seat="E", rnd="E")
        self.assertEqual(s.tai, 2)  # East pung: seat + prevailing
        s = score("123m456p789s111z22p", win="1m", seat="S", rnd="E", bonus=("f2", "s1"))
        self.assertEqual(s.tai, 2)  # prevailing East 1 + seat flower (Orchid=2 for South) 1

    def test_payout_doubles(self):
        r = Rules()
        self.assertEqual(r.payout(3, self_drawn=True)["each_player_pays"], 8)
        self.assertEqual(r.payout(3, self_drawn=False)["discarder_pays"], 8)
        self.assertEqual(r.payout(9, self_drawn=True)["each_player_pays"], 32)  # capped at 5


def state(hand, **kw):
    kw.setdefault("seat_wind", 27)
    kw.setdefault("round_wind", 27)
    if "melds" in kw:
        kw["melds"] = [Meld(k, parse_tile(t)) for k, t in kw["melds"]]
    if "seen" in kw:
        kw["seen"] = parse_tiles(kw["seen"])
    return TableState(parse_tiles(hand), **kw)


class AdvisorTests(unittest.TestCase):
    def test_discard_isolated_tile_and_name_needed_tile(self):
        r = advise(state("123m456p789s11z4z13s"))
        best = r["options"][0]
        self.assertEqual((best["action"], best["tile"]), ("discard", "4z"))
        self.assertEqual([n["tile"] for n in best["needs"]], ["2s"])

    def test_waiting_mode_lists_tiles_to_watch(self):
        r = advise(state("123m456p789s11z13s"))
        self.assertEqual(r["mode"], "waiting")
        self.assertEqual(r["shanten"], 0)

    def test_dead_wait_warns(self):
        r = advise(state("123m456p789s111z9m", seen="9m9m9m"))
        self.assertEqual(r["needs"][0]["live"], 0)
        self.assertTrue(r["warnings"])

    def test_self_drawn_win_recommended(self):
        r = advise(state("123m456p789s111z9m"), drawn=parse_tile("9m"))
        self.assertEqual(r["options"][0]["action"], "win")

    def test_pong_recommended_when_it_makes_discard_wins_pay(self):
        r = advise(state("34m456p789s55z666s"), thrown=parse_tile("5z"))
        self.assertEqual(r["options"][0]["action"], "pong")

    def test_chow_only_from_left(self):
        actions = lambda r: {o["action"] for o in r["options"]}
        self.assertNotIn("chow", actions(advise(state("13m456p789s55z666s"), thrown=parse_tile("2m"))))
        self.assertIn("chow", actions(advise(state("13m456p789s55z666s"), thrown=parse_tile("2m"),
                                             from_left=True)))

    def test_rejects_too_many_copies(self):
        with self.assertRaises(ValueError):
            advise(state("1111m456p789s11z9m", seen="1m"))


def clean_env(**values):
    """A cleared environment for patch.dict, keeping what Windows needs for SSL."""
    keep = {k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR") if k in os.environ}
    return {**keep, **values}


class FakeJev(BaseHTTPRequestHandler):
    last_body = None
    opencode_shape = False

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeJev.last_body = body
        FakeJev.auth = self.headers.get("Authorization")
        ids = list(body["questions"]["move"]["criteria"])
        pick = ids[-1]  # deliberately pick something other than the engine's first choice
        FakeJev.agent = self.headers.get("User-Agent")
        answers = {"move": {
            "type": "choice", "choice": pick, "probabilities": {i: (0.9 if i == pick else 0.0) for i in ids},
            "confidence": 0.8}}
        if FakeJev.opencode_shape:
            out = {"model": body["model"], "answers": answers}
        else:
            out = {"code": 0, "data": {"result": {"answers": answers}}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class JevTests(unittest.TestCase):
    payload = {"hand": "123m456p789s11z13s", "drawn": "4z"}

    def test_engine_fallback_without_key(self):
        with mock.patch.dict(os.environ, clean_env(), clear=True):
            r = advise_from_json(dict(self.payload))
        self.assertEqual(r["decided_by"]["source"], "engine")
        self.assertEqual(r["options"][0]["tile"], "4z")

    def test_jev_choice_is_applied(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeJev)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {"JEV_API_KEY": "test-key", "JEV_API_URL": f"http://127.0.0.1:{srv.server_port}/v1/systemone"}
        try:
            with mock.patch.dict(os.environ, clean_env(**env), clear=True):
                r = advise_from_json(dict(self.payload))
        finally:
            srv.shutdown()
        self.assertEqual(FakeJev.auth, "Bearer test-key")
        self.assertEqual(FakeJev.last_body["model"], "typesafe/jev-1.14")
        self.assertEqual(r["decided_by"]["source"], "jev")
        self.assertFalse(r["decided_by"]["agrees_with_engine"])
        self.assertTrue(r["options"][0]["recommended"])
        self.assertTrue(any(o.get("engine_pick") for o in r["options"][1:]))

    def test_opencode_response_shape(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeJev)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {"JEV_API_KEY": "test-key", "JEV_MODEL": "jev-1.13",
               "JEV_API_URL": f"http://127.0.0.1:{srv.server_port}/inference/systemone/v1/systemone"}
        FakeJev.opencode_shape = True
        try:
            with mock.patch.dict(os.environ, clean_env(**env), clear=True):
                r = advise_from_json(dict(self.payload))
        finally:
            FakeJev.opencode_shape = False
            srv.shutdown()
        self.assertEqual(FakeJev.last_body["model"], "jev-1.13")
        self.assertNotIn("Python-urllib", FakeJev.agent)
        self.assertEqual(r["decided_by"]["source"], "jev")

    def test_jev_unreachable_falls_back(self):
        env = {"JEV_API_KEY": "test-key", "JEV_API_URL": "http://127.0.0.1:9/v1/systemone"}
        with mock.patch.dict(os.environ, clean_env(**env), clear=True):
            r = advise_from_json(dict(self.payload))
        self.assertEqual(r["decided_by"]["source"], "engine")
        self.assertIn("unavailable", r["decided_by"]["note"])


class DotenvTest(unittest.TestCase):
    def test_reads_file_and_keeps_real_env(self):
        import tempfile
        from mahjong_coach.env import load_dotenv
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w", encoding="utf-8") as f:
                f.write("# comment\n\nJEV_API_KEY=\"from-file\"\n"
                        "JEV_MODEL=jev-1.13-free  # note\nexport JEV_API_URL=https://x/y\n")
            with mock.patch.dict(os.environ, {"JEV_API_KEY": "from-shell"}, clear=True):
                self.assertTrue(load_dotenv(path))
                self.assertEqual(os.environ["JEV_API_KEY"], "from-shell")
                self.assertEqual(os.environ["JEV_MODEL"], "jev-1.13-free")
                self.assertEqual(os.environ["JEV_API_URL"], "https://x/y")
            self.assertFalse(load_dotenv(os.path.join(d, "missing.env")))


if __name__ == "__main__":
    unittest.main()
