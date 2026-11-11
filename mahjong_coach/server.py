"""Small local web server: serves the coach page and the /api/advise endpoint.

Standard library only. The Jev API key stays here on the server side and is
never sent to the browser.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import jev
from .api import advise_from_json

STATIC = Path(__file__).parent / "static"
MAX_BODY = 64 * 1024


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
        if self.path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/logo.svg":
            self._send(200, (STATIC / "logo.svg").read_bytes(), "image/svg+xml")
        elif self.path in ("/favicon.svg", "/favicon.ico"):
            self._send(200, (STATIC / "favicon.svg").read_bytes(), "image/svg+xml")
        elif self.path == "/api/status":
            self._json(200, {"jev_configured": jev.is_configured()})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/api/advise":
            return self._json(404, {"error": "Not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._json(413, {"error": "Request too large"})
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
            use_jev = bool(payload.pop("use_jev", True))
            self._json(200, advise_from_json(payload, use_jev=use_jev))
        except (ValueError, KeyError, TypeError) as e:
            self._json(400, {"error": str(e)})

    def log_message(self, fmt, *args):
        pass


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True):
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
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
