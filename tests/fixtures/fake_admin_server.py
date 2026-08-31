"""Fake HTTP server that mimics engine.exe's /admin loopback endpoint.

This fixture provides the HTTP responses needed by EngineAdminClient tests
without requiring the actual Windows engine binary. It binds to an ephemeral
localhost port and can queue custom responses to exercise error cases.
"""

import json
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Any, Literal
from collections import deque

log = logging.getLogger(__name__)


class FakeAdminHandler(BaseHTTPRequestHandler):
    """HTTP request handler that serves queued responses from the fake server."""

    def do_GET(self):
        """Handle GET requests for /health endpoint."""
        self.server.request_paths.append(self.path)
        if self.path == "/admin/health":
            response = self.server.dequeue_response()
            if response is None:
                # No queued response, return default health
                response = {
                    "status": 200,
                    "body": {
                        "state": self.server.state,
                        "generation": self.server.generation,
                        "width": self.server.width,
                        "height": self.server.height,
                    },
                }
            status = response["status"]
            body = response["body"]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if isinstance(body, bytes):
                self.wfile.write(body)
            else:
                self.wfile.write(json.dumps(body).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        """Handle POST requests for /reconnect and /keyframe endpoints."""
        # Consume request body
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        self.server.request_paths.append(self.path)

        if self.path == "/admin/reconnect":
            response = self.server.dequeue_response()
            if response is None:
                # No queued response, return error
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "no response queued"}).encode())
                return

            status = response["status"]
            response_body = response["body"]

            # If reconnect was accepted, update server state
            if status == 200 and isinstance(response_body, dict) and response_body.get("accepted"):
                self.server.generation = response_body.get("generation", self.server.generation)

            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            if isinstance(response_body, bytes):
                self.wfile.write(response_body)
            else:
                self.wfile.write(json.dumps(response_body).encode())

        elif self.path == "/admin/keyframe":
            response = self.server.dequeue_response()
            if response is None:
                # No queued response, return error
                self.send_response(500)
                self.end_headers()
                return

            status = response["status"]
            self.send_response(status)
            if status == 204:
                self.end_headers()
            else:
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                response_body = response.get("body", {})
                if isinstance(response_body, bytes):
                    self.wfile.write(response_body)
                else:
                    self.wfile.write(json.dumps(response_body).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Suppress HTTP server logging."""
        pass


class FakeAdminServer:
    """Fake engine.exe /admin HTTP server for testing EngineAdminClient.

    Usage:
        with FakeAdminServer(generation=5) as server:
            server.queue_reconnect(200, {"accepted": True, "generation": 7})
            client = EngineAdminClient()
            new_gen = client.reconnect(server.port, 27183, 5)
            assert new_gen == 7
    """

    def __init__(
        self,
        generation: int = 0,
        state: Literal["connected", "stalled", "disconnected"] = "connected",
        width: int = 1920,
        height: int = 1080,
    ):
        self.generation = generation
        self.state = state
        self.width = width
        self.height = height
        self.port: Optional[int] = None
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None
        self._response_queue: deque = deque()

    def __enter__(self):
        """Start the fake server and return self."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop the fake server."""
        self.stop()
        return False

    def start(self):
        """Start the HTTP server on an ephemeral port."""
        # Create handler class with reference to self
        handler_class = FakeAdminHandler

        # Create HTTPServer on ephemeral port
        self.server = HTTPServer(("127.0.0.1", 0), handler_class)
        self.server.state = self.state
        self.server.generation = self.generation
        self.server.width = self.width
        self.server.height = self.height
        self.server.dequeue_response = self._dequeue_response
        self.server.request_paths = []
        self.port = self.server.server_address[1]

        # Start server in background thread
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        """Stop the HTTP server and wait for thread shutdown."""
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join()
        self.server = None
        self.thread = None

    def queue_raw_health(self, status: int, body: bytes):
        """Queue a raw health response (used for testing malformed JSON)."""
        self._response_queue.append({
            "status": status,
            "body": body,
        })

    def queue_reconnect(self, status: int, body: dict):
        """Queue a reconnect response."""
        self._response_queue.append({
            "status": status,
            "body": body,
        })

    def queue_keyframe(self, status: int):
        """Queue a keyframe response."""
        self._response_queue.append({
            "status": status,
            "body": b"" if status == 204 else {},
        })

    def _dequeue_response(self) -> Optional[dict]:
        """Dequeue and return next queued response, or None."""
        try:
            return self._response_queue.popleft()
        except IndexError:
            return None
