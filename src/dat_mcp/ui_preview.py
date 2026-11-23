"""Loopback-only browser preview of the grantee MCP Apps.

A small development host, not a production one: `python -m dat_mcp.ui_preview`
serves each registered UI resource in a sandboxed iframe, does the MCP Apps
handshake, and passes it real tool results. It opens on a fictional demo; a
UEI typed into the page calls the view's tool.
"""

import argparse
import asyncio
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastmcp import Client

from dat_mcp.app import mcp
from dat_mcp.tools import grantee_show_network, grantee_show_profile

UI_DIR = grantee_show_profile.UI_PATH.parent
# view name -> (its tool, its UI resource, its fictional demo result)
VIEWS = {
    "profile": (
        "grantee_show_profile",
        grantee_show_profile.UI_URI,
        "demo_profile.json",
    ),
    "network": (
        "grantee_show_network",
        grantee_show_network.UI_URI,
        "demo_network.json",
    ),
}
VIEW_TOOLS = {tool for tool, _, _ in VIEWS.values()}
MAX_REQUEST_BYTES = 4096
LOOKUP_SECONDS = 110
IFRAME_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'none'; img-src 'none'; frame-src 'none'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'"
)
HOST_CSP = (
    "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
    "connect-src 'self'; frame-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
UEI = re.compile(r"[A-Z0-9]{12}")
EIN = re.compile(r"[0-9]{9}")


async def call_tool(name, arguments):
    async with Client(mcp) as client:
        result = await client.call_tool(name, arguments, raise_on_error=False)
        return {
            "content": [
                c.model_dump(by_alias=True, exclude_none=True) for c in result.content
            ],
            "structuredContent": result.structured_content,
            "isError": result.is_error,
        }


async def read_ui(uri):
    async with Client(mcp) as client:
        resource = await client.read_resource(uri)
        return resource[0].text


def allowed(name, args):
    """Whether the page may make this tool call: one grantee's view by
    identifier, or five of one UEI's awards. Nothing else is forwarded."""
    if not isinstance(args, dict):
        return False

    def all_match(value, pattern):
        values = value if isinstance(value, list) else [value]
        return 0 < len(values) <= 20 and all(
            isinstance(v, str) and pattern.fullmatch(v) for v in values
        )

    if name in VIEW_TOOLS:
        return bool(args) and all(
            key in ("uei", "ein") and all_match(value, UEI if key == "uei" else EIN)
            for key, value in args.items()
        )
    if name == "usaspending_search_awards":
        return (
            set(args) == {"uei", "limit"}
            and isinstance(args["uei"], str)
            and bool(UEI.fullmatch(args["uei"]))
            and type(args["limit"]) is int
            and args["limit"] == 5
        )
    return False


class PreviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=0):
        self.token = secrets.token_urlsafe(24)
        self.gate = threading.BoundedSemaphore(1)  # one lookup at a time
        self.ui, self.demo = {}, {}
        for view, (_, uri, demo) in VIEWS.items():
            self.ui[view] = asyncio.run(read_ui(uri))
            self.demo[view] = {
                "content": [{"type": "text", "text": "Fictional demo."}],
                "structuredContent": json.loads(
                    (UI_DIR / demo).read_text(encoding="utf-8")
                ),
                "isError": False,
            }
        super().__init__(("127.0.0.1", port), PreviewHandler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_address[1]}"

    @property
    def url(self):
        return f"{self.origin}/{self.token}/"


class PreviewHandler(BaseHTTPRequestHandler):
    server: PreviewServer

    def log_message(self, format, *args):
        # The path holds the session token; identifiers and errors stay unlogged.
        pass

    def _route(self):
        if self.headers.get("Host") != self.server.origin.removeprefix("http://"):
            return None
        prefix = f"/{self.server.token}/"
        if not self.path.startswith(prefix):
            return None
        return self.path[len(prefix) :]

    def _send(self, status, body, content_type="application/json", csp=HOST_CSP):
        if content_type == "application/json":
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self._route()
        kind, _, view = (route or "").partition("/")
        if route == "":
            host = (UI_DIR / "preview_host.html").read_text(encoding="utf-8")
            self._send(200, host, "text/html")
        elif kind == "view" and view in VIEWS:
            self._send(200, self.server.ui[view], "text/html", IFRAME_CSP)
        elif kind == "demo" and view in VIEWS:
            self._send(200, self.server.demo[view])
        else:
            self._send(404, {"error": "Not found."})

    def _discard_body(self):
        # Closing a socket with the request body unread resets the
        # connection, and the client can lose the response.
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < size <= MAX_REQUEST_BYTES:
            self.rfile.read(size)

    def do_POST(self):
        if self._route() != "call":
            self._discard_body()
            self._send(404, {"error": "Not found."})
            return
        # Only the preview page itself: never the sandboxed iframe's null
        # origin, another site, or a form post.
        if (
            self.headers.get("Origin") != self.server.origin
            or self.headers.get("Content-Type") != "application/json"
            or self.headers.get("Transfer-Encoding")
        ):
            self._discard_body()
            self._send(403, {"error": "Same-origin JSON requests are required."})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_REQUEST_BYTES:
                raise ValueError
            payload = json.loads(self.rfile.read(size))
            name, args = payload["name"], payload["arguments"]
        except (ValueError, UnicodeError, KeyError, TypeError):
            self._send(400, {"error": "Invalid request."})
            return
        if not allowed(name, args):
            self._send(403, {"error": "This lookup is not allowed in the preview."})
            return
        if not self.server.gate.acquire(blocking=False):
            self._send(429, {"error": "A lookup is running. Try again shortly."})
            return
        try:
            lookup = asyncio.wait_for(call_tool(name, args), LOOKUP_SECONDS)
            result = asyncio.run(lookup)
            if result["isError"]:
                # Tool errors can quote an upstream response; don't pass it on.
                self._send(
                    422,
                    {
                        "error": "The lookup failed. Check the identifier, the API "
                        "keys in .env, the local database, and network access."
                    },
                )
            else:
                self._send(200, result)
        except Exception:
            self._send(502, {"error": "The lookup is unavailable."})
        finally:
            self.server.gate.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0, help="0 chooses a free port")
    args = parser.parse_args()
    with PreviewServer(port=args.port) as server:
        print(server.url, flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
