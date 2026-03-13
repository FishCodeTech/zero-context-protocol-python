#!/usr/bin/env python3
"""Official ASGI host runner for the ZCP backend template.

This is only a host runner for the API backend template. It does not serve docs.
"""

from __future__ import annotations

import asyncio
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.zcp_server_template import application


class ASGIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._handle()

    def do_POST(self) -> None:
        self._handle()

    def log_message(self, format: str, *args) -> None:
        return

    def _handle(self) -> None:
        body = b""
        if self.command in {"POST", "PUT", "PATCH"}:
            length = int(self.headers.get("Content-Length", "0") or "0")
            body = self.rfile.read(length) if length > 0 else b""

        response_started = {"done": False}
        response_status = {"code": 500}
        response_headers: list[tuple[bytes, bytes]] = []
        response_chunks: list[tuple[bytes, bool]] = []
        body_sent = {"done": False}

        async def receive():
            if body_sent["done"]:
                return {"type": "http.disconnect"}
            body_sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                response_started["done"] = True
                response_status["code"] = message["status"]
                response_headers.extend(message.get("headers", []))
                self.send_response(response_status["code"])
                sent_content_length = False
                for key, value in response_headers:
                    header_name = key.decode("latin1")
                    header_value = value.decode("latin1")
                    if header_name.lower() == "content-length":
                        sent_content_length = True
                    self.send_header(header_name, header_value)
                if not sent_content_length:
                    self.send_header("Connection", "close")
                self.end_headers()
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                more_body = message.get("more_body", False)
                response_chunks.append((chunk, more_body))
                if chunk:
                    self.wfile.write(chunk)
                    self.wfile.flush()

        scope = {
            "type": "http",
            "http_version": "1.1",
            "method": self.command,
            "path": urlsplit(self.path).path,
            "query_string": urlsplit(self.path).query.encode("latin1"),
            "headers": [
                (key.lower().encode("latin1"), value.encode("latin1"))
                for key, value in self.headers.items()
            ],
            "client": self.client_address,
            "server": self.server.server_address,
        }

        asyncio.run(application(scope, receive, send))

        if not response_started["done"]:
            self.send_error(500, "ASGI app did not start a response")


def main() -> None:
    host = "0.0.0.0"
    port = 8000
    server = ThreadingHTTPServer((host, port), ASGIHandler)
    print(f"ZCP API server listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
