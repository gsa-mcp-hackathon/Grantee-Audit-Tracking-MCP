"""grantee_show_profile: the tool's result and its MCP App resource. The two
tools it calls are replaced, so nothing here reaches FAC, SAM.gov, or the db."""

import asyncio
import json

import pytest
from fastmcp import Client

from dat_mcp.app import mcp
from dat_mcp.tools import grantee_show_profile as module
from dat_mcp.tools.grantee_show_profile import UI_PATH, UI_URI, grantee_show_profile


@pytest.fixture
def demo(monkeypatch):
    demo = json.loads(
        (UI_PATH.parent / "demo_profile.json").read_text(encoding="utf-8")
    )
    calls = []

    def identity(**args):
        calls.append(("identity", args))
        return demo["identity"]

    def coverage(**args):
        calls.append(("coverage", args))
        return demo["coverage"]

    monkeypatch.setattr(module, "grantee_identity_resolution", identity)
    monkeypatch.setattr(module, "grantee_audit_coverage", coverage)
    demo["calls"] = calls
    return demo


def test_returns_both_tools_results_unchanged(demo):
    result = grantee_show_profile(uei="DEMOHEALTH01")
    assert result.structured_content == {
        "identity": demo["identity"],
        "coverage": demo["coverage"],
    }
    args = {"uei": "DEMOHEALTH01", "ein": None, "name": None, "state": None}
    assert demo["calls"] == [("identity", args), ("coverage", args)]


def test_text_lists_each_row_with_its_source_and_the_caveats(demo):
    text = grantee_show_profile(uei="DEMOHEALTH01").content[0].text
    assert "- 2025: no audit found. https://app.fac.gov/dissemination/search/" in text
    assert (
        "- 2024: own audit, under another UEI, accepted 2025-10-23, 206 days "
        "after due date 2025-03-31." in text
    )
    for caveat in demo["coverage"]["caveats"]:
        assert caveat in text
    assert demo["coverage"]["open_questions"][0] in text


def test_several_name_matches_return_candidates_and_skip_coverage(monkeypatch):
    candidates = {
        "searched": {"name": "riverbend", "state": None},
        "candidates": [{"name": "RIVERBEND A", "uei": "DEMOHEALTH01"}],
        "caveats": ["The name matched several organizations."],
    }
    monkeypatch.setattr(module, "grantee_identity_resolution", lambda **_: candidates)
    monkeypatch.setattr(
        module, "grantee_audit_coverage", lambda **_: pytest.fail("not called")
    )
    result = grantee_show_profile(name="riverbend")
    assert result.structured_content == candidates
    assert json.loads(result.content[0].text) == candidates


def test_tool_links_to_the_ui_resource_over_mcp(demo):
    async def exercise():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "grantee_show_profile")
            assert tool.meta["ui"]["resourceUri"] == UI_URI
            resources = await client.list_resources()
            resource = next(r for r in resources if str(r.uri) == UI_URI)
            assert resource.mime_type == "text/html;profile=mcp-app"
            html = await client.read_resource(UI_URI)
            assert "ui/initialize" in html[0].text
            result = await client.call_tool(
                "grantee_show_profile", {"uei": "DEMOHEALTH01"}
            )
            assert result.structured_content["coverage"] == demo["coverage"]

    asyncio.run(exercise())
