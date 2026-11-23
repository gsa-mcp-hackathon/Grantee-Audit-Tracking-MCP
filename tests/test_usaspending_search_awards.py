"""Tests for usaspending_search_awards. USAspending is replaced by a fake."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dat_mcp import usaspending
from dat_mcp.app import mcp
from dat_mcp.tools.usaspending_search_awards import (
    GRANT_FIELDS,
    usaspending_search_awards,
)


@pytest.fixture
def requests(monkeypatch):
    """Record each USAspending request and answer with one canned award."""
    sent = []

    def fake_post(endpoint, payload):
        sent.append((endpoint, payload))
        return {
            "results": [
                {
                    "Award ID": "H80CS12345",
                    "Recipient Name": "Inland Behavioral",
                    "Recipient UEI": "JQNLHK8YLTU6",
                    "Award Amount": 1000000,
                    "Total Outlays": 750000,
                    "CFDA Number": "93.224",
                    "generated_internal_id": "ASST_NON_H80CS12345_7530",
                }
            ]
        }

    monkeypatch.setattr(usaspending, "post", fake_post)
    return sent


def test_search_by_uei_builds_payload(requests):
    result = usaspending_search_awards(uei="JQNLHK8YLTU6", fiscal_year=2025)

    [(endpoint, payload)] = requests
    assert endpoint == "search/spending_by_award/"
    assert payload["filters"]["recipient_search_text"] == ["JQNLHK8YLTU6"]
    assert payload["filters"]["time_period"] == [
        {
            "start_date": "2024-10-01",
            "end_date": "2025-09-30",
            "date_type": "action_date",
        }
    ]
    assert result["count"] == 1
    assert "usaspending.gov/award/" in result["awards"][0]["source_url"]


def test_fields_the_api_adds_unasked_are_dropped(monkeypatch):
    """spending_by_award appends internal_id, awarding_agency_id and
    agency_slug to every row; they only take up the agent's context."""
    row = {
        "Award ID": "H80CS12345",
        "Award Amount": 1000000,
        "generated_internal_id": "ASST_NON_H80CS12345_075",
        "internal_id": 260155882,
        "awarding_agency_id": 806,
        "agency_slug": "department-of-health-and-human-services",
    }
    monkeypatch.setattr(usaspending, "post", lambda e, p: {"results": [dict(row)]})

    [award] = usaspending_search_awards(uei="JQNLHK8YLTU6")["awards"]

    assert set(award) == set(GRANT_FIELDS) | {"source_url"}
    assert award["Award ID"] == "H80CS12345"
    assert award["source_url"].endswith("ASST_NON_H80CS12345_075")


def test_search_by_name(requests):
    usaspending_search_awards(name="Inland Behavioral")

    [(_, payload)] = requests
    assert payload["filters"]["recipient_search_text"] == ["Inland Behavioral"]


def test_agency_filter(requests):
    usaspending_search_awards(
        uei="JQNLHK8YLTU6",
        agency="Department of Health and Human Services",
    )

    [(_, payload)] = requests
    assert payload["filters"]["agencies"] == [
        {
            "type": "awarding",
            "tier": "toptier",
            "name": "Department of Health and Human Services",
        }
    ]


def test_grant_type_codes_are_default(requests):
    usaspending_search_awards(uei="JQNLHK8YLTU6")

    [(_, payload)] = requests
    assert payload["filters"]["award_type_codes"] == [
        "02",
        "03",
        "04",
        "05",
        "F001",
        "F002",
    ]


def test_every_group_includes_its_f_series_codes():
    """Recent awards carry only an F-series code; without it they vanish.

    Current HRSA Health Center Program awards are F001, so a grants search on
    02 to 05 alone returned a fraction of a health center's money.
    """
    groups = usaspending.AWARD_TYPE_GROUPS
    assert {"F001", "F002"} <= set(groups["grants"])
    assert {"F006", "F007"} <= set(groups["direct_payments"])
    assert {"F003", "F004"} <= set(groups["loans"])
    assert {"F005", "F008", "F009", "F010"} <= set(groups["other"])


def test_loans_ask_for_loan_fields_and_sort_by_loan_value(requests):
    """The API rejects Award Amount and Total Outlays for loans with HTTP 400."""
    result = usaspending_search_awards(uei="JQNLHK8YLTU6", award_group="loans")

    [(_, payload)] = requests
    assert payload["sort"] == "Loan Value"
    assert "Loan Value" in payload["fields"]
    assert "Award Amount" not in payload["fields"]
    assert "Total Outlays" not in payload["fields"]
    assert any("Loan Value" in caveat for caveat in result["caveats"])


def test_grants_do_not_carry_the_loan_caveat(requests):
    result = usaspending_search_awards(uei="JQNLHK8YLTU6")

    [(_, payload)] = requests
    assert payload["sort"] == "Award Amount"
    assert not any("Loan Value" in caveat for caveat in result["caveats"])


def test_more_available_comes_from_the_api_page_flag(monkeypatch):
    """A full page is not proof of another one; the API says which."""
    pages = {
        True: {"results": [{"Award ID": "A"}], "page_metadata": {"hasNext": True}},
        False: {"results": [{"Award ID": "A"}], "page_metadata": {"hasNext": False}},
    }
    for has_next, response in pages.items():
        monkeypatch.setattr(usaspending, "post", lambda e, p, r=response: r)
        result = usaspending_search_awards(uei="JQNLHK8YLTU6", limit=1)
        assert result["more_available"] is has_next


def test_bad_input_is_rejected_before_calling_api(requests):
    with pytest.raises(ToolError):
        usaspending_search_awards()
    with pytest.raises(ToolError):
        usaspending_search_awards(uei="JQNLHK8")
    with pytest.raises(ToolError):
        usaspending_search_awards(uei="JQNLHK8YLTU6", name="both")
    assert requests == []


def test_agent_sees_tool_with_described_parameters():
    async def list_tools():
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tool = asyncio.run(list_tools())["usaspending_search_awards"]
    assert "USAspending" in tool.description
    assert "12-character UEI" in tool.input_schema["properties"]["uei"]["description"]
