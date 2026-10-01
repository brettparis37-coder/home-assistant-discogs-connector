"""Small, bearer-authenticated MCP surface for Discogs track facts."""

from __future__ import annotations

import hmac
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlparse

MCP_PORT = 8100
MAX_REQUEST_BYTES = 1_000_000
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {"2025-03-26", "2025-06-18", PROTOCOL_VERSION}


class ToolInputError(ValueError):
    """A safe, user-correctable tool argument or database validation error."""


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        database: Any,
        token: str,
        release_loader: Callable[[int], Any] | None = None,
    ) -> None:
        self.database = database
        self.token = token
        self.release_loader = release_loader
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/mcp":
            self._send_json(404, {"error": "Not found"})
            return
        if not hmac.compare_digest(
            self.headers.get("Authorization", ""), f"Bearer {self.server.token}"
        ):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Bearer realm="discogs-facts"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.headers.get("Origin"):
            # This endpoint is for native MCP clients, not browser-origin requests.
            self._send_json(403, {"error": "Browser-origin requests are not accepted."})
            return
        accept = self.headers.get("Accept", "")
        if "application/json" not in accept or "text/event-stream" not in accept:
            self._send_json(406, {"error": "Accept must list application/json and text/event-stream."})
            return
        if not self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "application/json":
            self._send_json(415, {"error": "MCP requests must use application/json."})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0:
            self._send_json(411, {"error": "Content-Length is required."})
            return
        if length > MAX_REQUEST_BYTES:
            self._send_json(413, {"error": "Request body is too large."})
            return
        try:
            request = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}})
            return

        method = request["method"]
        request_id = request.get("id")
        if method.startswith("notifications/"):
            self._send_empty(202)
            return
        if request_id is None:
            self._send_json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Requests must include an id."}})
            return

        params = request.get("params") or {}
        if not isinstance(params, dict):
            self._send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": "params must be an object"}})
            return
        if method != "initialize":
            protocol_header = self.headers.get("MCP-Protocol-Version", "2025-03-26")
            if protocol_header not in SUPPORTED_PROTOCOL_VERSIONS:
                self._send_json(400, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "Unsupported MCP protocol version"}})
                return
        try:
            if method == "initialize":
                requested_version = str(params.get("protocolVersion") or PROTOCOL_VERSION)
                result = {
                    "protocolVersion": requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "discogs-connector", "version": "0.7.9"},
                    "instructions": (
                        "Use the Discogs Connector tools to select owned releases, load cached track context, "
                        "save five researched, sourced facts for every song, and verify the completed rows. "
                        "Never send SQL through these tools."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                tool_name = str(params.get("name") or "")
                arguments = params.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise ToolInputError("Tool arguments must be an object.")
                payload = self._call_tool(tool_name, arguments)
                result = {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "structuredContent": payload,
                }
            else:
                self._send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
                return
        except ValueError as exc:
            if method == "tools/call":
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                }
            else:
                self._send_json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})
                return
        except sqlite3.Error:
            if method == "tools/call":
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": "The Discogs database operation failed; no fact update was committed."}],
                }
            else:
                result = None
                self._send_json(500, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Database operation failed"}})
                return
        except Exception:
            if method == "tools/call":
                result = {
                    "isError": True,
                    "content": [{"type": "text", "text": "The Discogs Connector could not complete that tool call."}],
                }
            else:
                self._send_json(500, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal error"}})
                return

        response_headers: dict[str, str] = {}
        if method != "initialize":
            response_headers["MCP-Protocol-Version"] = self.headers.get("MCP-Protocol-Version", "2025-03-26")
        self._send_json(200, {"jsonrpc": "2.0", "id": request_id, "result": result}, response_headers)

    def do_GET(self) -> None:  # noqa: N802
        if urlparse(self.path).path == "/mcp":
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(404, {"error": "Not found"})

    def do_DELETE(self) -> None:  # noqa: N802
        self.send_response(405)
        self.send_header("Allow", "POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "list_releases_needing_facts":
            limit = arguments.get("limit", 25)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
                raise ToolInputError("limit must be a whole number from 1 to 100.")
            return {"releases": self.server.database.facts_releases_needing_work(limit)}

        if name in ("get_release_tracks", "verify_release_facts"):
            release_id = arguments.get("release_id")
            if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id <= 0:
                raise ToolInputError("release_id must be a positive integer.")
            if name == "get_release_tracks":
                context = self.server.database.facts_release_tracks(release_id)
                if not context["tracks"] and self.server.release_loader is not None:
                    try:
                        self.server.release_loader(release_id)
                    except Exception as exc:
                        print("Discogs facts MCP: tracklist fetch from Discogs failed", flush=True)
                        raise ToolInputError(
                            "The cached tracklist was empty and Discogs could not refresh it. Check the app log."
                        ) from exc
                    context = self.server.database.facts_release_tracks(release_id)
                return context
            return self.server.database.verify_release_track_facts(release_id)

        if name == "save_release_facts":
            release_id = arguments.get("release_id")
            if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id <= 0:
                raise ToolInputError("release_id must be a positive integer.")
            tracks = arguments.get("tracks")
            if not isinstance(tracks, list):
                raise ToolInputError("tracks must be an array containing every song track and its five facts.")
            model = arguments.get("model", "")
            if not isinstance(model, str):
                raise ToolInputError("model must be a string.")
            prompt_version = arguments.get("prompt_version", "discogs-track-facts-v2")
            if not isinstance(prompt_version, str):
                raise ToolInputError("prompt_version must be a string.")
            return self.server.database.save_release_track_facts(
                release_id,
                tracks,
                model=model,
                prompt_version=prompt_version,
            )

        raise ToolInputError(f"Unknown Discogs Connector tool: {name}.")

    def _send_json(self, status: int, payload: dict[str, Any], headers: dict[str, str] | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:
        # The standard log includes only the HTTP request line and never headers or bodies.
        print("Discogs facts MCP: " + fmt % args, flush=True)


TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_releases_needing_facts",
        "description": "List owned Discogs releases where one or more song tracks do not yet have five saved facts. Releases without a cached tracklist are included last; call get_release_tracks to load their Discogs tracklist. Use this when the user asks you to choose an album.",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_release_tracks",
        "description": "Fetch the exact stored track keys and Discogs track, album, artist, genre, style, and label context for one owned release. If no tracklist is cached, it is fetched from Discogs and cached first. Use every returned song track when saving facts.",
        "inputSchema": {
            "type": "object",
            "properties": {"release_id": {"type": "integer", "minimum": 1}},
            "required": ["release_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_release_facts",
        "description": "Replace saved facts for every song track on an owned release in one SQLite transaction. Supply exactly five distinct researched facts per exact track_key. Each fact should be about 2–3 sentences and include a direct source title, URL, and publisher. The service marks all saved sets complete and returns saved track/fact counts. This tool writes only the two discogs_track_fact_* tables for the selected release.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "release_id": {"type": "integer", "minimum": 1},
                "model": {"type": "string", "maxLength": 120},
                "prompt_version": {"type": "string", "maxLength": 120, "default": "discogs-track-facts-v2"},
                "tracks": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "track_key": {"type": "string", "minLength": 1},
                            "facts": {
                                "type": "array",
                                "minItems": 5,
                                "maxItems": 5,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "fact_text": {"type": "string", "minLength": 1},
                                        "source_title": {"type": "string", "minLength": 1},
                                        "source_url": {"type": "string", "minLength": 8},
                                        "source_publisher": {"type": "string", "minLength": 1},
                                    },
                                    "required": ["fact_text", "source_title", "source_url", "source_publisher"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["track_key", "facts"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["release_id", "tracks"],
            "additionalProperties": False,
        },
    },
    {
        "name": "verify_release_facts",
        "description": "Read back saved fact-set status, fact counts, and source-linked fact rows for a release after writing.",
        "inputSchema": {
            "type": "object",
            "properties": {"release_id": {"type": "integer", "minimum": 1}},
            "required": ["release_id"],
            "additionalProperties": False,
        },
    },
]


def start_facts_mcp(
    database: Any,
    token: str,
    port: int = MCP_PORT,
    *,
    release_loader: Callable[[int], Any] | None = None,
) -> ThreadingHTTPServer | None:
    """Start the narrow MCP endpoint only when a strong token is configured."""
    token = str(token or "").strip()
    if len(token) < 32:
        return None
    server = Server(("0.0.0.0", int(port)), database, token, release_loader)
    thread = threading.Thread(target=server.serve_forever, name="discogs-facts-mcp", daemon=True)
    thread.start()
    return server
