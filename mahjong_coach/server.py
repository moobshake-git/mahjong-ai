"""Small local web server: serves the coach page, /api/advise and the camera endpoints.

Standard library only. The Jev API key stays here on the server side and is
never sent to the browser.
"""
from __future__ import annotations

import json
import ssl
import threading
import webbrowser
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import jev
from .api import advise_from_json
from .vision import reader
from .vision.simulator import simulate
from .vision.tracker import Tracker

STATIC = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024
MAX_IMAGE = reader.MAX_IMAGE_BYTES + 64 * 1024
MAX_SESSIONS = 20

# One tracker per open page (camera session), oldest dropped first.
_trackers: OrderedDict[str, Tracker] = OrderedDict()
_trackers_lock = threading.Lock()


def tracker_for(session: str, reset: bool = False, live: bool = False) -> Tracker:
    """The tracker for one page. The live camera sends a still frame only once the table
    stops moving and each read is slow, so it needs 2 agreeing reads out of 3 rather than
    5 of 7 video frames."""
    session = ("live:" if live else "") + str(session or "default")[:64]
    with _trackers_lock:
        if reset or session not in _trackers:
            _trackers[session] = Tracker(stable_frames=2, window=3) if live else Tracker()
            while len(_trackers) > MAX_SESSIONS:
                _trackers.popitem(last=False)
        _trackers.move_to_end(session)
        return _trackers[session]


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/logo.svg":
            self._send(200, (STATIC / "logo.svg").read_bytes(), "image/svg+xml")
        elif url.path in ("/favicon.svg", "/favicon.ico"):
            self._send(200, (STATIC / "favicon.svg").read_bytes(), "image/svg+xml")
        elif url.path == "/api/status":
            self._json(200, {"jev_configured": jev.is_configured(),
                             "camera_reader": reader.sdk_available(), "camera_model": reader.model()})
        elif url.path == "/api/vision/demo":
            try:
                seed = int(parse_qs(url.query).get("seed", ["7"])[0])
            except ValueError:
                return self._json(400, {"error": "seed must be a number"})
            self._json(200, simulate(seed=seed))
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        if path == "/api/vision/camera":
            return self._camera(length, parse_qs(urlparse(self.path).query).get("session", [""])[0])
        if path not in ("/api/advise", "/api/vision/frame", "/api/vision/reset"):
            return self._json(404, {"error": "Not found"})
        if length > MAX_BODY:
            return self._json(413, {"error": "Request too large"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            if path == "/api/advise":
                use_jev = bool(payload.pop("use_jev", True))
                return self._json(200, advise_from_json(payload, use_jev=use_jev))
            if path == "/api/vision/reset":
                tracker_for(payload.get("session"), reset=True, live=bool(payload.get("live")))
                return self._json(200, {"ok": True})
            frame = payload["frame"]
            if not isinstance(frame, dict) or not isinstance(frame.get("detections", []), list):
                raise ValueError("frame must be an object with a detections list")
            self._json(200, tracker_for(payload.get("session"), live=bool(payload.get("live"))).feed(frame))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            self._json(400, {"error": str(e)})

    def _camera(self, length: int, session: str):
        """One still frame from the live camera: Claude reads it, the tracker decides."""
        if length > MAX_IMAGE:
            return self._json(413, {"error": "That frame is over 5 MB. Lower the camera resolution."})
        media_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = self.rfile.read(length)
        try:
            got = reader.read_frame(body, media_type)
        except reader.ReadError as e:
            return self._json(400, {"error": str(e)})
        except ValueError as e:   # Claude's answer wasn't valid JSON
            return self._json(502, {"error": f"Couldn't understand the tile list: {e}"})
        tr = tracker_for(session, live=True)
        out = tr.feed(got["frame"])
        # Once two reads agree with what's already accepted, the page can stop reading
        # until something on the table moves again (saves API calls).
        view, _ = tr.view_from(got["frame"]["detections"])
        out["settled"] = tr.committed is not None and view.key() == tr.committed.key()
        out["read"] = {"hand": len(got["read"].get("hand", [])), "note": got["read"].get("note", ""),
                       "hands_over_table": got["frame"]["motion"], "model": got["model"]}
        self._json(200, out)

    def log_message(self, fmt, *args):
        pass


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True,
          cert: str | None = None, key: str | None = None):
    httpd = ThreadingHTTPServer((host, port), Handler)
    scheme = "http"
    if cert:
        # Browsers only allow the camera on https:// pages (or localhost), so a phone needs this.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        scheme = "https"
    url = f"{scheme}://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"Mahjong turn coach running at {url}  (Ctrl+C to stop)")
    print("Jev: " + ("on" if jev.is_configured() else "off (set JEV_API_KEY to enable)"))
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
