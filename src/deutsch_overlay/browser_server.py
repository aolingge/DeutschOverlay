"""Loopback HTTP transport for the ASR bridge.

Bound to ``127.0.0.1`` only, and it stays that way: three checks run before a
request is allowed near the recognizer, because a local HTTP port is reachable
by every process and every page on this machine.

* The client address must be loopback.
* The ``Host`` header must name a loopback host or ``localhost``. This is what
  stops a DNS-rebinding page from talking to the bridge through its own name.
* A bearer token created at startup must match. The token is what the
  extension is configured with; it is not an authentication system, it is a
  "this really is your extension" check plus a barrier against drive-by
  requests from other local software.

The server deliberately uses the standard library: the desktop app has no web
framework dependency and this is one localhost endpoint, not a service.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .browser_bridge import AsrBridgeService, new_token
from .browser_protocol import (
    AudioRequest,
    ErrorCode,
    ProtocolError,
    SessionRequest,
    TranscriptQuery,
)

DEFAULT_MAX_BODY_BYTES = 3 * 1024 * 1024
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})
LOOPBACK_ORIGIN_SCHEMES = ("chrome-extension", "moz-extension", "safari-web-extension")
SESSION_PATH = re.compile(r"^/v1/session/([0-9a-f]{8,64})(?:/(audio|transcript|finish|retry))?$")


@dataclass(slots=True)
class BridgeServerConfig:
    host: str = "127.0.0.1"
    port: int = 8766
    token: str = ""
    token_path: Path | None = None
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    allow_others: bool = False


def write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(token, encoding="utf-8")
    os.replace(temporary, path)


def read_token_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_token(config: BridgeServerConfig) -> str:
    """Reuse the on-disk token when present so a running extension survives restarts."""
    if config.token:
        return config.token
    if config.token_path is not None:
        existing = read_token_file(config.token_path)
        if existing:
            config.token = existing
            return existing
    token = new_token()
    config.token = token
    if config.token_path is not None:
        try:
            write_token_file(config.token_path, token)
        except OSError:
            pass
    return token


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: BridgeServerConfig, service: AsrBridgeService) -> None:
        self.config = config
        self.service = service
        self.last_error = ""
        super().__init__((config.host, config.port), BridgeRequestHandler)
        self.config.port = self.server_address[1]


class BridgeRequestHandler(BaseHTTPRequestHandler):
    server_version = "DeutschOverlayBridge/1"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ plumbing

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        # No request logging: URLs carry the token and audio payloads are large.
        return

    @property
    def bridge(self) -> BridgeHTTPServer:
        return self.server  # type: ignore[return-value]

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _client_is_loopback(self) -> bool:
        address = self.client_address[0] if self.client_address else ""
        return address.startswith("127.") or address in ("::1", "localhost")

    def _host_is_loopback(self) -> bool:
        header = (self.headers.get("Host") or "").strip()
        if not header:
            return False
        parsed = urlparse(f"//{header}")
        host = (parsed.hostname or "").lower()
        return host in {"127.0.0.1", "localhost", "::1"} or host.startswith("127.")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin:
            return True
        scheme = urlparse(origin).scheme.lower()
        return scheme in LOOPBACK_ORIGIN_SCHEMES

    def _authorized(self) -> bool:
        expected = self.bridge.config.token
        if not expected:
            return False
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        supplied = header[len(prefix) :].strip()
        # Constant-time comparison: a timing oracle on a loopback port is cheap
        # to exploit from another local process.
        return hmac.compare_digest(supplied, expected)

    def _guard(self, *, token_required: bool = True) -> bool:
        if not self._client_is_loopback():
            self._send_error_json(403, ErrorCode.FORBIDDEN_ORIGIN, "bridge only serves loopback clients")
            return False
        if not self._host_is_loopback():
            self._send_error_json(
                403, ErrorCode.FORBIDDEN_ORIGIN, "Host header must name a loopback address"
            )
            return False
        if not self._origin_allowed():
            self._send_error_json(
                403, ErrorCode.FORBIDDEN_ORIGIN, "Origin must be a browser extension"
            )
            return False
        if token_required and not self._authorized():
            self._send_error_json(
                401, ErrorCode.UNAUTHORIZED, "missing or wrong bridge token"
            )
            return False
        return True

    def _read_json(self) -> dict[str, Any] | None:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header) if length_header else 0
        except ValueError:
            self._send_error_json(400, ErrorCode.BAD_REQUEST, "invalid Content-Length")
            return None
        if length < 0 or length > self.bridge.config.max_body_bytes:
            self._send_error_json(
                413,
                ErrorCode.TOO_LARGE,
                f"body larger than {self.bridge.config.max_body_bytes} bytes",
            )
            return None
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send_error_json(400, ErrorCode.BAD_REQUEST, "invalid JSON body")
            return None
        if not isinstance(payload, dict):
            self._send_error_json(400, ErrorCode.BAD_REQUEST, "body must be a JSON object")
            return None
        return payload

    def _fail(self, exc: ProtocolError) -> None:
        status = {
            ErrorCode.BAD_REQUEST: 400,
            ErrorCode.UNSUPPORTED_VERSION: 400,
            ErrorCode.LANGUAGE_UNSUPPORTED: 400,
            ErrorCode.TIMELINE_GAP: 409,
            ErrorCode.TIMELINE_REGRESSION: 409,
            ErrorCode.UNAUTHORIZED: 401,
            ErrorCode.FORBIDDEN_ORIGIN: 403,
            ErrorCode.NOT_FOUND: 404,
            ErrorCode.CONFLICT: 409,
            ErrorCode.CAPTIONS_PRESENT: 409,
            ErrorCode.ENGINE_BUSY: 429,
            ErrorCode.ENGINE_UNAVAILABLE: 503,
            ErrorCode.TOO_LARGE: 413,
            ErrorCode.INTERNAL: 500,
        }.get(exc.code, 400)
        self._send_error_json(status, exc.code, exc.message)

    # -------------------------------------------------------------------- routes

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/v1/health":
                # Health is the one endpoint a probe may call without a token;
                # it exposes no audio, no captions, and no session identifiers.
                if not self._guard(token_required=False):
                    return
                self._send_json(200, self.bridge.service.health())
                return
            if path == "/v1/settings":
                if not self._guard():
                    return
                self._send_json(200, self.bridge.service.settings())
                return
            match = SESSION_PATH.match(path)
            if match and match.group(2) is None:
                if not self._guard():
                    return
                session = self.bridge.service.get(match.group(1))
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "sessionId": session.session_id,
                        "recognize": session.recognize,
                        "reason": session.reason,
                        "language": session.language,
                        "revision": session.revision,
                        "status": session.status(),
                        "gapSamples": session.gap_samples,
                        "warning": session.warning,
                        "metrics": {**session.metrics, "queuedClips": len(session.jobs), "queuedTranslations": len(session.translation_jobs)},
                    },
                )
                return
            self._send_error_json(404, ErrorCode.NOT_FOUND, f"no route for GET {path}")
        except ProtocolError as exc:
            self._fail(exc)

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        """Answer the CORS preflight an extension's JSON POST always triggers.

        A request with a JSON content type and an ``Authorization`` header is
        never a simple request, so the browser sends ``OPTIONS`` first. The
        guard still runs: a page cannot use this bridge just because it can
        reach the port.
        """
        if not self._guard(token_required=False):
            return
        if not self._origin_allowed():
            self._send_error_json(403, ErrorCode.FORBIDDEN_ORIGIN, "Origin must be a browser extension")
            return
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Authorization, Content-Type"
        )
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_cors_headers(self) -> None:
        # Echo only an origin the guard would accept anyway: a refused page must
        # not receive the one header its browser needs to read the refusal.
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).scheme.lower() in LOOPBACK_ORIGIN_SCHEMES:
            self.send_header("Access-Control-Allow-Origin", origin)

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/v1/settings":
                if not self._guard():
                    return
                payload = self._read_json()
                if payload is not None:
                    self._send_json(200, self.bridge.service.update_settings(payload))
                return
            if path == "/v1/session":
                if not self._guard():
                    return
                payload = self._read_json()
                if payload is None:
                    return
                response = self.bridge.service.create_session(SessionRequest.from_dict(payload))
                self._send_json(200, response.to_dict())
                return
            match = SESSION_PATH.match(path)
            if not match:
                self._read_json()
                self._send_error_json(404, ErrorCode.NOT_FOUND, f"no route for POST {path}")
                return
            if not self._guard():
                return
            session_id, action = match.group(1), match.group(2)
            # Read (and size-check) the body before the session lookup so an
            # oversized upload is refused for what it is, not as a 404.
            payload = self._read_json()
            if payload is None:
                return
            session = self.bridge.service.get(session_id)
            if action == "retry":
                self._send_json(200, self.bridge.service.retry_segment(session, payload))
                return
            if action == "audio":
                response = self.bridge.service.feed(session, AudioRequest.from_dict(payload))
                self._send_json(200, response.to_dict())
                return
            if action == "transcript":
                query = TranscriptQuery.from_dict(payload)
                response = self.bridge.service.transcripts(session, query)
                self._send_json(200, response.to_dict())
                return
            if action == "finish":
                response = self.bridge.service.feed(
                    session, AudioRequest.from_dict({**payload, "streamComplete": True})
                )
                self._send_json(200, response.to_dict())
                return
            self._send_error_json(404, ErrorCode.NOT_FOUND, f"no route for POST {path}")
        except ProtocolError as exc:
            self._fail(exc)
        except Exception as exc:  # pragma: no cover - defensive
            self._send_error_json(500, ErrorCode.INTERNAL, "local bridge request failed")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib naming
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            match = SESSION_PATH.match(path)
            if not match or match.group(2) is not None:
                self._send_error_json(404, ErrorCode.NOT_FOUND, f"no route for DELETE {path}")
                return
            if not self._guard():
                return
            removed = self.bridge.service.close_session(match.group(1))
            self._send_json(200, {"ok": True, "closedSegments": removed})
        except ProtocolError as exc:
            self._fail(exc)


class BridgeServer:
    """Context-managed bridge server, for the CLI and for tests."""

    def __init__(self, service: AsrBridgeService, config: BridgeServerConfig | None = None) -> None:
        self.config = config or BridgeServerConfig()
        self.token = load_token(self.config)
        self.service = service
        self.httpd: BridgeHTTPServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        port = self.httpd.config.port if self.httpd else self.config.port
        return f"http://{self.config.host}:{port}"

    def start(self) -> "BridgeServer":
        self.httpd = BridgeHTTPServer(self.config, self.service)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="bridge-http", daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=5.0)
        self.service.close_all()

    def __enter__(self) -> "BridgeServer":
        return self.start()

    def __exit__(self, *_exc: object) -> bool:
        self.stop()
        return False
