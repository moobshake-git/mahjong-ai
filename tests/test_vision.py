import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from types import SimpleNamespace
from unittest import mock

from mahjong_coach.api import advise_from_json
from mahjong_coach.vision import reader
from mahjong_coach.vision.simulator import DEMO_SCRIPT, simulate
from mahjong_coach.vision.tracker import Tracker, meld_of

HAND = "1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z 1s 3s".split()
DEMO_EVENTS = ["hand_set", "bonus", "thrown", "drew", "discarded", "thrown", "thrown",
               "meld", "thrown", "drew", "discarded", "thrown"]


def det(tile, zone="hand", seat="me", x=0, conf=0.95, group=None):
    d = {"tile": tile, "zone": zone, "seat": seat, "conf": conf, "box": [x, 0, 10, 10]}
    if group is not None:
        d["group"] = group
    return d


def hand_dets(tiles):
    return [det(t, x=i * 50) for i, t in enumerate(tiles)]


def feed(tr, dets, n=5, motion=False):
    events, state = [], None
    for _ in range(n):
        out = tr.feed({"detections": dets, "motion": motion})
        events += out["events"]
        state = out["state"] or state
    return events, state


class TrackerTests(unittest.TestCase):
    def test_needs_a_steady_view(self):
        tr = Tracker()
        events, _ = feed(tr, hand_dets(HAND), n=4)
        self.assertEqual(events, [])
        events, state = feed(tr, hand_dets(HAND), n=1)
        self.assertEqual([e["type"] for e in events], ["hand_set"])
        self.assertEqual(len(state["hand"]), 13)
        self.assertIsNone(state["drawn"])

    def test_motion_frames_never_count(self):
        tr = Tracker()
        events, _ = feed(tr, hand_dets(HAND), n=20, motion=True)
        self.assertEqual(events, [])
        self.assertEqual(tr.feed({"detections": [], "motion": True})["status"]["streak"], 0)

    def test_single_frame_misread_is_outvoted(self):
        tr = Tracker()
        feed(tr, hand_dets(HAND))
        misread = hand_dets(HAND[:-1] + ["2s"])
        seq = [hand_dets(HAND)] * 3 + [misread] + [hand_dets(HAND)] * 3
        events = [e for d in seq for e in tr.feed({"detections": d})["events"]]
        self.assertEqual(events, [])

    def test_low_confidence_tiles_are_ignored(self):
        tr = Tracker()
        dets = hand_dets(HAND) + [det("9p", zone="discard", seat="across", conf=0.4)]
        events, state = feed(tr, dets)
        self.assertEqual([e["type"] for e in events], ["hand_set"])
        self.assertEqual(state["seen"], [])

    def test_draw_discard_and_throw(self):
        tr = Tracker()
        feed(tr, hand_dets(HAND))
        events, state = feed(tr, hand_dets(HAND) + [det("3s", x=800)])
        self.assertEqual([e["type"] for e in events], ["drew"])
        self.assertEqual(state["drawn"], "3s")
        self.assertEqual(len(state["hand"]), 13)

        after = [t for t in HAND if t != "1s"] + ["3s"]
        events, state = feed(tr, hand_dets(after) + [det("1s", "discard")])
        self.assertEqual([e["type"] for e in events], ["discarded"])
        self.assertIsNone(state["drawn"])
        self.assertEqual(state["seen"], ["1s"])

        events, state = feed(tr, hand_dets(after) + [det("1s", "discard"), det("9p", "discard", "left")])
        self.assertEqual([(e["type"], e["seat"]) for e in events], [("thrown", "left")])
        self.assertEqual(state["thrown"], "9p")
        self.assertTrue(state["from_left"])
        self.assertEqual(state["seen"], ["1s"])          # the thrown tile isn't double counted

    def test_others_pong_clears_the_claimed_throw(self):
        tr = Tracker()
        feed(tr, hand_dets(HAND) + [det("6z", "discard", "across")])
        meld = [det("6z", "meld", "left", x=i, group=0) for i in range(3)]
        events, state = feed(tr, hand_dets(HAND) + meld)
        self.assertEqual([e["type"] for e in events], ["meld"])
        self.assertEqual(events[0]["meld"], {"kind": "pung", "tile": "6z"})
        self.assertIsNone(state["thrown"])
        self.assertEqual(state["seen"], ["6z", "6z", "6z"])

    def test_my_pong(self):
        tr = Tracker()
        hand = "1m 2m 3m 4p 5p 6p 7s 8s 9s 5z 5z 1s 3s".split()
        feed(tr, hand_dets(hand) + [det("5z", "discard", "left")])
        rest = [t for t in hand if t not in ("5z", "3s")]
        meld = [det("5z", "meld", "me", x=i, group=0) for i in range(3)]
        events, state = feed(tr, hand_dets(rest) + [det("3s", "discard")] + meld)
        self.assertEqual([e["type"] for e in events], ["called", "discarded"])
        self.assertEqual(state["melds"], [{"kind": "pung", "tile": "5z"}])
        self.assertEqual(len(state["hand"]), 10)

    def test_conflicts_are_reported_not_used(self):
        tr = Tracker()
        events, state = feed(tr, hand_dets(HAND[:11]))
        self.assertEqual([e["type"] for e in events], ["conflict"])
        self.assertIsNone(state)
        self.assertIsNone(tr.committed)
        too_many = hand_dets(HAND) + [det("1z", "discard", "right", x=i) for i in range(3)]
        events, _ = feed(tr, too_many)
        self.assertEqual([e["type"] for e in events], ["conflict"])
        self.assertIn("1z", events[0]["text"])

    def test_meld_of(self):
        self.assertEqual(meld_of(["3p", "1p", "2p"]), {"kind": "chow", "tile": "1p"})
        self.assertEqual(meld_of(["7z"] * 4), {"kind": "kong", "tile": "7z"})
        self.assertIsNone(meld_of(["1z", "2z", "3z"]))


class SimulatorTests(unittest.TestCase):
    def run_demo(self, **kw):
        tr, events, states = Tracker(), [], []
        for f in simulate(**kw)["frames"]:
            out = tr.feed(f)
            events += [e["type"] for e in out["events"]]
            if out["state"]:
                states.append(out["state"])
        return events, states

    def test_noise_free_demo_is_exact(self):
        events, _ = self.run_demo(noise=0)
        self.assertEqual(events, DEMO_EVENTS)

    def test_demo_seed_used_by_the_page(self):
        events, states = self.run_demo(seed=7)
        self.assertEqual(events, DEMO_EVENTS)
        final = states[-1]
        self.assertEqual(final["thrown"], "1z")
        self.assertEqual(final["bonus"], ["f1"])
        # Same table state the manual page would build -> the engine says win.
        r = advise_from_json({**final, "from_left": final["from_left"]}, use_jev=False)
        self.assertEqual(r["options"][0]["action"], "win")

    def test_robust_to_noise(self):
        exact = sum(self.run_demo(seed=s)[0] == DEMO_EVENTS for s in range(60))
        self.assertGreaterEqual(exact, 55)   # misreads, dropouts and low confidence in ~22% of frames

    def test_frames_have_tracker_shape(self):
        sim = simulate()
        self.assertEqual(len(sim["captions"]), len(DEMO_SCRIPT))
        f = sim["frames"][0]
        self.assertTrue({"tile", "zone", "seat", "conf", "box"} <= set(f["detections"][0]))


class FakeClient:
    def __init__(self, payload=None, stop_reason="end_turn"):
        self.calls = []
        self.payload, self.stop_reason = payload, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        content = [SimpleNamespace(type="thinking", thinking=""),
                   SimpleNamespace(type="text", text=json.dumps(self.payload))]
        return SimpleNamespace(stop_reason=self.stop_reason, content=content, model=kw["model"])


def table_read(**kw):
    read = {"hand": HAND, "bonus": ["f1"], "my_melds": [],
            "discards": {"me": [], "left": ["9m"], "across": [], "right": []},
            "melds": {"left": [], "across": [], "right": []}, "hands_over_table": False, "note": ""}
    read.update(kw)
    return read


class ReaderTests(unittest.TestCase):
    def test_reads_the_table_into_a_tracker_frame(self):
        client = FakeClient(table_read(hand=HAND + ["9q"], melds={"left": [["6z"] * 3], "across": [], "right": []}))
        with mock.patch.dict(os.environ, {"VISION_MODEL": "claude-opus-5-5"}):
            out = reader.read_frame(b"jpeg-bytes", "image/jpeg", client=client)
        dets = out["frame"]["detections"]
        self.assertEqual([d["tile"] for d in dets if d["zone"] == "hand"], HAND)   # unknown code dropped
        self.assertEqual([(d["seat"], d["tile"]) for d in dets if d["zone"] == "discard"], [("left", "9m")])
        self.assertEqual(sum(d["zone"] == "meld" for d in dets), 3)
        self.assertFalse(out["frame"]["motion"])
        req = client.calls[0]
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["fallbacks"], "default")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["messages"][0]["content"][0]["source"]["media_type"], "image/jpeg")
        # Same view through the tracker: hand set, bonus, a throw from beside me (left).
        tr = Tracker()
        events, state = feed(tr, out["frame"]["detections"])
        self.assertEqual([e["type"] for e in events], ["hand_set", "bonus"])
        self.assertEqual(state["seen"], ["9m", "6z", "6z", "6z"])

    def test_hands_over_table_is_motion(self):
        out = reader.read_frame(b"x", "image/jpeg", client=FakeClient(table_read(hands_over_table=True)))
        self.assertTrue(out["frame"]["motion"])

    def test_refusal_and_bad_input(self):
        with self.assertRaises(reader.ReadError):
            reader.read_frame(b"x", "image/jpeg", client=FakeClient({}, stop_reason="refusal"))
        with self.assertRaises(reader.ReadError):
            reader.read_frame(b"x", "text/plain", client=FakeClient({}))
        with self.assertRaises(reader.ReadError):
            reader.read_frame(b"x" * (reader.MAX_IMAGE_BYTES + 1), "image/png", client=FakeClient({}))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        from mahjong_coach.server import Handler
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, path, body, ctype="application/json"):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers={"Content-Type": ctype})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_demo_through_the_api(self):
        with urllib.request.urlopen(self.base + "/api/vision/demo?seed=7") as r:
            sim = json.loads(r.read())
        self.post("/api/vision/reset", {"session": "t1"})
        events = []
        for f in sim["frames"]:
            code, out = self.post("/api/vision/frame", {"session": "t1", "frame": f})
            self.assertEqual(code, 200)
            events += [e["type"] for e in out["events"]]
        self.assertEqual(events, DEMO_EVENTS)

    def test_bad_requests(self):
        self.assertEqual(self.post("/api/vision/frame", {"session": "t2"})[0], 400)
        self.assertEqual(self.post("/api/vision/frame", {"frame": {"detections": "x"}})[0], 400)
        code, out = self.post("/api/vision/camera?session=t3", b"hello", ctype="text/plain")
        self.assertEqual(code, 400)
        self.assertIn("JPEG", out["error"])

    def test_live_camera_needs_two_agreeing_reads_then_settles(self):
        fake = FakeClient(table_read())
        real = reader.read_frame
        with mock.patch.object(reader, "read_frame", lambda img, mt: real(img, mt, client=fake)):
            self.post("/api/vision/reset", {"session": "cam", "live": True})
            code, first = self.post("/api/vision/camera?session=cam", b"jpeg-bytes", ctype="image/jpeg")
            self.assertEqual(code, 200)
            self.assertEqual(first["events"], [])
            self.assertFalse(first["settled"])
            _, second = self.post("/api/vision/camera?session=cam", b"jpeg-bytes", ctype="image/jpeg")
            self.assertEqual([e["type"] for e in second["events"]], ["hand_set", "bonus"])
            self.assertTrue(second["settled"])
            self.assertEqual(second["state"]["seen"], ["9m"])


if __name__ == "__main__":
    unittest.main()
