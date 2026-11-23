"""The local preview's HTTP boundary and tool dispatch, with the tools faked."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from dat_mcp import ui_preview


@pytest.fixture
def server():
    with ui_preview.PreviewServer() as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


def request(server, route="", payload=None, url=None, **headers):
    if payload is not None:
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Origin", server.origin)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        url or server.url + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
    )
    try:
        response = opener.open(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, dict(response.headers), response.read()


def call(server, name, arguments, **headers):
    return request(server, "call", {"name": name, "arguments": arguments}, **headers)


def test_serves_the_registered_resource_in_a_sandbox(server):
    status, headers, html = request(server)
    assert status == 200
    assert b'sandbox="allow-scripts allow-downloads"' in html
    assert b"allow-same-origin" not in html
    status, headers, html = request(server, "view/profile")
    assert status == 200
    assert request(server, "view/network")[0] == 200
    assert request(server, "view/other")[0] == 404
    assert "connect-src 'none'" in headers["Content-Security-Policy"]
    assert b"ui/notifications/tool-result" in html
    assert headers["Cache-Control"] == "no-store"


def test_demo_is_labeled_fictional(server):
    status, _, body = request(server, "demo/profile")
    result = json.loads(body)
    assert status == 200
    assert result["structuredContent"]["demo"] is True
    assert result["structuredContent"]["coverage"]["years"]
    network = json.loads(request(server, "demo/network")[2])["structuredContent"]
    assert network["demo"] is True
    assert network["organizations"]


@pytest.mark.parametrize("host", ["localhost", "attacker.example", "169.254.169.254"])
def test_rejects_another_host_header(server, host):
    assert request(server, Host=host)[0] == 404


def test_rejects_a_wrong_session_token(server):
    assert request(server, url=server.origin + "/not-the-token/")[0] == 404


@pytest.mark.parametrize("origin", ["null", "https://attacker.example", ""])
def test_rejects_cross_origin_calls(server, origin):
    status = call(
        server, "grantee_show_profile", {"uei": "JQNLHK8YLTU6"}, Origin=origin
    )[0]
    assert status == 403


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("fac_fetch_audit_report", {"report_id": "anything"}),
        ("grantee_audit_coverage", {"uei": "JQNLHK8YLTU6"}),
        ("grantee_show_profile", {}),
        ("grantee_show_profile", {"uei": "ABC"}),
        ("grantee_show_profile", {"name": "anything"}),
        ("grantee_show_profile", {"uei": "JQNLHK8YLTU6", "url": "http://x"}),
        ("grantee_show_profile", {"uei": ["JQNLHK8YLTU6"] * 21}),
        ("usaspending_search_awards", {"uei": "HF6NE8JKLGV3", "limit": 100}),
        ("usaspending_search_awards", {"uei": "HF6NE8JKLGV3", "limit": True}),
        ("usaspending_search_awards", {"uei": "HF6NE8JKLGV3", "limit": 5, "name": "x"}),
        ("usaspending_search_awards", {"uei": "https://x", "limit": 5}),
        ("usaspending_search_awards", None),
    ],
)
def test_forwards_no_other_tool_or_argument(server, name, arguments):
    assert call(server, name, arguments)[0] == 403


def test_dispatches_allowed_calls_and_hides_error_text(server, monkeypatch):
    calls = []

    async def fake(name, arguments):
        calls.append((name, arguments))
        return {"structuredContent": {"awards": []}, "isError": False}

    monkeypatch.setattr(ui_preview, "call_tool", fake)
    awards = {"uei": "HF6NE8JKLGV3", "limit": 5}
    assert call(server, "usaspending_search_awards", awards)[0] == 200
    profile = {"uei": ["JQNLHK8YLTU6"]}
    assert call(server, "grantee_show_profile", profile)[0] == 200
    assert call(server, "grantee_show_network", {"ein": "112223333"})[0] == 200
    assert calls[:2] == [
        ("usaspending_search_awards", awards),
        ("grantee_show_profile", profile),
    ]

    async def broken(*args):
        raise RuntimeError("Authorization: secret-token")

    monkeypatch.setattr(ui_preview, "call_tool", broken)
    status, _, body = call(server, "grantee_show_profile", profile)
    assert status == 502
    assert b"secret-token" not in body

    async def tool_error(*args):
        return {"content": [{"text": "upstream said secret-token"}], "isError": True}

    monkeypatch.setattr(ui_preview, "call_tool", tool_error)
    status, _, body = call(server, "grantee_show_profile", profile)
    assert status == 422
    assert b"secret-token" not in body
