"""Tests for fac_search_audits. FAC is replaced by a fake, so these run offline."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dat_mcp import fac
from dat_mcp.app import mcp
from dat_mcp.tools.fac_search_audits import fac_search_audits


@pytest.fixture
def requests(monkeypatch):
    """Record each FAC request and answer with one canned audit."""
    sent = []

    def fake_get(table, params):
        sent.append((table, params))
        return [{"report_id": "2025-06-GSAFAC-0000397078"}]

    monkeypatch.setattr(fac, "get", fake_get)
    return sent


def test_search_by_ein_builds_fac_filters(requests):
    result = fac_search_audits(ein="953246624", audit_year=2025)

    [(table, params)] = requests
    assert table == "general"
    assert params["auditee_ein"] == "eq.953246624"
    assert params["audit_year"] == "eq.2025"
    assert result["audits"][0]["source_url"].endswith("2025-06-GSAFAC-0000397078")


def test_name_search_sends_a_word_pattern(requests):
    fac_search_audits(name="Inland Behavioral", state="ca")

    [(_, params)] = requests
    assert params["auditee_name"] == "imatch.Inland[^A-Za-z0-9]+Behavioral"
    assert params["auditee_state"] == "eq.CA"


def _matches(query, filed_name):
    """Whether FAC would match a filing's name, using Python's re for its regex.

    The pattern uses only bracket expressions and ?, *, +, which PostgreSQL's
    case-insensitive imatch reads the same way.
    """
    import re

    pattern = fac.contains(query).removeprefix("imatch.")
    return re.search(pattern, filed_name, re.IGNORECASE) is not None


@pytest.mark.parametrize(
    "query, filed_name",
    [
        # Orangeburg, SC filed FY2025 without the period FY2024 had.
        ("Family Health Centers, Inc.", "Family Health Centers, Inc"),
        ("Family Health Centers, Inc.", "FAMILY HEALTH CENTERS, INC."),
        ("Family Health Centers, Inc.", "Tampa Family Health Centers, Inc."),
        ("Family Health Centers Inc", "FAMILY HEALTH CENTERS, INC."),
        ("Kerr-People's Health", "BETTY JEAN KERR - PEOPLE'S HEALTH CENTERS"),
        ("People's Health Supportive", "PEOPLES HEALTH SUPPORTIVE SERVICES"),
        ("Peoples Health Supportive", "PEOPLE'S HEALTH SUPPORTIVE SERVICES"),
        ("Inland Behavioral", "Inland Behavioral and Health Services, Inc."),
    ],
)
def test_name_search_ignores_case_and_punctuation(query, filed_name):
    assert _matches(query, filed_name)


@pytest.mark.parametrize(
    "query, filed_name",
    [
        # Words must be adjacent: these are other organizations.
        ("Family Health Centers", "Family Health Care Centers of Los Angeles"),
        ("Family Health Centers, Inc.", "Family Health Centers of Clark County, Inc."),
        ("Inland Behavioral", "Inlandbehavioral Services"),
    ],
)
def test_name_search_keeps_words_in_order_and_apart(query, filed_name):
    assert not _matches(query, filed_name)


def test_a_name_with_no_letters_or_digits_is_rejected(requests):
    with pytest.raises(ToolError, match="letter or digit"):
        fac_search_audits(name="&, .")
    assert requests == []


def test_several_identifiers_go_in_one_request(requests):
    fac_search_audits(
        uei=["JQNLHK8YLTU6", "jqnlhk8yltub", "JQNLHK8YLTU6"],
        ein=["95-3246624", "341735884"],
        limit=100,
    )

    [(_, params)] = requests
    assert params["auditee_uei"] == "in.(JQNLHK8YLTU6,JQNLHK8YLTUB)"  # deduped
    assert params["auditee_ein"] == "in.(953246624,341735884)"


def test_hitting_the_limit_is_flagged(requests):
    full = fac_search_audits(ein="953246624", limit=1)  # the fake returns one row
    assert full["more_available"] is True
    assert "more_available is true" in " ".join(full["caveats"])

    room = fac_search_audits(ein="953246624", limit=2)
    assert room["more_available"] is False
    assert "more_available" not in " ".join(room["caveats"])


def test_a_single_item_list_is_an_equals_filter(requests):
    fac_search_audits(report_id=["2025-06-GSAFAC-0000397078"])

    [(_, params)] = requests
    assert params["report_id"] == "eq.2025-06-GSAFAC-0000397078"


def test_too_many_identifiers_are_rejected(requests):
    with pytest.raises(ToolError, match="at most 20"):
        fac_search_audits(ein=[f"95324{n:04}" for n in range(21)])
    with pytest.raises(ToolError, match="at least one"):  # [] counts as nothing
        fac_search_audits(ein=[])
    assert requests == []


def test_bad_input_is_rejected_before_calling_fac(requests):
    with pytest.raises(ToolError):
        fac_search_audits(state="CA")
    with pytest.raises(ToolError):
        fac_search_audits(uei="JQNLHK8")
    assert requests == []


def test_agent_sees_tool_with_described_parameters():
    async def list_tools():
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tool = asyncio.run(list_tools())["fac_search_audits"]
    assert tool.description.startswith("Search Federal Audit Clearinghouse")
    assert "12-character UEI" in tool.input_schema["properties"]["uei"]["description"]
