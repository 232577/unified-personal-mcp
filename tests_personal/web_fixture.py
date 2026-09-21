from __future__ import annotations

import hashlib
import threading
from collections import Counter
from contextlib import AbstractContextManager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "fixtures/webview2/page.html"


class U4AFixtureServer(AbstractContextManager):
    def __init__(self, host="127.0.0.1", port=65441):
        self.host = host
        self.port = port
        self.counts = Counter()
        self._server = None
        self._thread = None

    @property
    def origin(self):
        return f"http://{self.host}:{self.port}"

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "BF-U4A-Fixture/1"

            def log_message(self, *_args):
                return

            def do_GET(self):
                parsed = urlsplit(self.path)
                owner.counts[parsed.path] += 1
                if parsed.path == "/page.html":
                    raw = PAGE.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if parsed.path == "/download":
                    instance = parse_qs(parsed.query).get("instance", [""])[0]
                    if not instance.startswith("bfh_") or len(instance) != 36:
                        self.send_error(400)
                        return
                    raw = f"U4A report {instance}\n".encode("utf-8")
                    name = f"u4a-report-{instance[-6:]}.txt"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("X-U4A-SHA256", hashlib.sha256(raw).hexdigest())
                    self.end_headers()
                    self.wfile.write(raw)
                    return
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_error(404)

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = self._server.server_port
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
        return False


