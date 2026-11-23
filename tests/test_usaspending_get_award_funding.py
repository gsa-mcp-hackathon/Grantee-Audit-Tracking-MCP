"""Tests for usaspending_get_award_funding. USAspending is replaced by a fake."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dat_mcp import usaspending
from dat_mcp.app import mcp
from dat_mcp.tools.usaspending_get_award_funding import usaspending_get_award_funding

# One award drawn down over FY2024 and FY2025. gross_outlay_amount is the
# running total within each federal fiscal year, so it restarts in FY2025.
FUNDING_ROWS = [
    {
        "reporting_fiscal_year": 2024,
        "reporting_fiscal_month": 3,
        "transaction_obligated_amount": 1000000,
        "gross_outlay_amount": 0,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    {
        "reporting_fiscal_year": 2025,
        "reporting_fiscal_month": 5,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 250000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    {
        "reporting_fiscal_year": 2025,
        "reporting_fiscal_month": 8,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 750000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
]


@pytest.fixture
def requests(monkeypatch):
    """Record each USAspending request and answer with canned funding rows."""
    sent = []

    def fake_post(endpoint, payload):
        sent.append((endpoint, payload))
        return {"results": list(FUNDING_ROWS)}

    monkeypatch.setattr(usaspending, "post", fake_post)
    return sent


def test_builds_payload_with_award_id(requests):
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    [(endpoint, payload)] = requests
    assert endpoint == "awards/funding/"
    assert payload["award_id"] == "ASST_NON_CE152817_7529"
    # Oldest first, because a period's outlay is read against the ones before it.
    assert payload["order"] == "asc"
    assert payload["limit"] == usaspending.MAX_PAGE_SIZE
    assert len(result["periods"]) == 3
    assert result["source_url"].endswith("ASST_NON_CE152817_7529")


def test_raw_rows_are_not_passed_to_the_agent(requests):
    """Raw rows run to 25,000 tokens on a health center award, and their
    gross_outlay_amount is a running total that misleads when added up."""
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert "transactions" not in result
    assert all("gross_outlay_amount" not in period for period in result["periods"])


def test_federal_accounts_are_listed_once_for_the_award(requests):
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert result["federal_accounts"] == ["075-0350"]
    assert all("federal_accounts" not in period for period in result["periods"])


def test_periods_report_what_was_drawn_not_the_running_total(requests):
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    drawn = {
        (p["reporting_fiscal_year"], p["reporting_fiscal_month"]): p[
            "period_outlay_amount"
        ]
        for p in result["periods"]
    }
    # The second FY2025 period rose by 500,000; it is not another 750,000.
    assert drawn[(2025, 5)] == 250000
    assert drawn[(2025, 8)] == 500000
    assert sum(drawn.values()) == 750000


def test_periods_carry_the_calendar_month_they_cover(requests):
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    ends = {p["period_end_date"] for p in result["periods"]}
    assert {"2023-12-31", "2025-02-28", "2025-05-31"} == ends


def test_federal_fiscal_year_totals_are_included(requests):
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    totals = {
        row["federal_fiscal_year"]: row for row in result["federal_fiscal_year_totals"]
    }
    assert totals[2024]["obligated_amount"] == 1000000
    assert totals[2024]["outlay_amount"] == 0
    assert totals[2025]["outlay_amount"] == 750000


def test_conditional_caveats_are_left_out_when_they_do_not_apply(requests):
    """No negative period and a complete history: those warnings are noise."""
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    caveats = " ".join(result["caveats"]).lower()
    assert "obligations are commitments" in caveats
    assert "negative" not in caveats
    assert "quarterly" not in caveats
    assert "funding_history_truncated" not in caveats


def answer_with(monkeypatch, rows):
    """Make awards/funding/ reply with these rows."""
    monkeypatch.setattr(
        usaspending, "post", lambda endpoint, payload: {"results": list(rows)}
    )


def test_a_drop_hidden_by_another_sources_draw_still_gets_the_caveat(monkeypatch):
    """The period nets to +300,000, but one source's running total fell."""
    answer_with(
        monkeypatch,
        [
            {**FUNDING_ROWS[1], "gross_outlay_amount": 500000},
            {**FUNDING_ROWS[2], "gross_outlay_amount": 300000},
            {**FUNDING_ROWS[2], "gross_outlay_amount": 500000, "federal_account": "B"},
        ],
    )
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert [p["period_outlay_amount"] for p in result["periods"]] == [500000, 300000]
    assert result["periods"][1]["includes_decrease"] is True
    assert "includes_decrease is set" in " ".join(result["caveats"])


def test_disaster_codes_are_listed_once_when_they_do_not_vary(monkeypatch):
    answer_with(
        monkeypatch,
        [{**r, "disaster_emergency_fund_code": "Q"} for r in FUNDING_ROWS],
    )
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert result["disaster_emergency_fund_codes"] == ["Q"]
    assert all("disaster_emergency_fund_codes" not in p for p in result["periods"])


def test_disaster_codes_stay_on_each_period_when_they_vary(monkeypatch):
    """Then the agent can tell which draws were COVID-19 money."""
    rows = [dict(r) for r in FUNDING_ROWS]
    rows[0]["disaster_emergency_fund_code"] = "Q"
    rows[1]["disaster_emergency_fund_code"] = "V"
    rows[2]["disaster_emergency_fund_code"] = "V"
    answer_with(monkeypatch, rows)
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert result["disaster_emergency_fund_codes"] == ["Q", "V"]
    codes = [p["disaster_emergency_fund_codes"] for p in result["periods"]]
    assert codes == [["Q"], ["V"], ["V"]]


def test_a_quarterly_period_is_flagged_with_its_caveat(monkeypatch):
    rows = [dict(r) for r in FUNDING_ROWS]
    rows[0]["is_quarterly_submission"] = True
    answer_with(monkeypatch, rows)
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert [p.get("quarterly") for p in result["periods"]] == [True, None, None]
    assert "three months" in " ".join(result["caveats"])


def test_empty_award_id_is_rejected(requests):
    with pytest.raises(ToolError):
        usaspending_get_award_funding(award_id="")
    with pytest.raises(ToolError):
        usaspending_get_award_funding(award_id="  ")
    assert requests == []


def test_a_long_funding_history_is_paged_through(monkeypatch):
    """A full page back means there is more; deltas need the earlier periods."""
    pages = []

    def fake_post(endpoint, payload):
        pages.append(payload["page"])
        if payload["page"] == 1:
            return {
                "results": [
                    {
                        "reporting_fiscal_year": 2024,
                        "reporting_fiscal_month": (index % 12) + 1,
                        "transaction_obligated_amount": 0,
                        "gross_outlay_amount": index,
                        "federal_account": f"075-{index:04d}",
                        "disaster_emergency_fund_code": None,
                    }
                    for index in range(usaspending.MAX_PAGE_SIZE)
                ]
            }
        return {"results": list(FUNDING_ROWS)}

    monkeypatch.setattr(usaspending, "post", fake_post)
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert pages == [1, 2]
    assert result["funding_history_truncated"] is False
    # Page one is all FY2024, so FY2025 can only have come from page two.
    totals = {
        row["federal_fiscal_year"]: row for row in result["federal_fiscal_year_totals"]
    }
    assert totals[2025]["outlay_amount"] == 750000


def test_a_history_past_the_paging_cap_is_flagged_as_truncated(monkeypatch):
    """Every page full means the newest periods were never fetched."""
    pages = []

    def fake_post(endpoint, payload):
        pages.append(payload["page"])
        return {
            "results": [dict(FUNDING_ROWS[1]) for _ in range(usaspending.MAX_PAGE_SIZE)]
        }

    monkeypatch.setattr(usaspending, "post", fake_post)
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    assert pages == list(range(1, usaspending.MAX_FUNDING_PAGES + 1))
    assert result["funding_history_truncated"] is True
    caveats = " ".join(result["caveats"]).lower()
    assert "funding_history_truncated" in caveats


def test_a_corrected_figure_can_make_a_period_negative(monkeypatch):
    """The caveat promises negatives are passed through, not clamped."""
    rows = [
        {
            "reporting_fiscal_year": 2025,
            "reporting_fiscal_month": 5,
            "transaction_obligated_amount": 0,
            "gross_outlay_amount": 500000,
            "federal_account": "075-0350",
            "disaster_emergency_fund_code": None,
        },
        {
            "reporting_fiscal_year": 2025,
            "reporting_fiscal_month": 8,
            "transaction_obligated_amount": 0,
            "gross_outlay_amount": 300000,  # the agency corrected it downwards
            "federal_account": "075-0350",
            "disaster_emergency_fund_code": None,
        },
    ]
    monkeypatch.setattr(
        usaspending, "post", lambda endpoint, payload: {"results": list(rows)}
    )
    result = usaspending_get_award_funding(award_id="ASST_NON_CE152817_7529")

    drawn = [p["period_outlay_amount"] for p in result["periods"]]
    assert drawn == [500000, -200000]
    assert result["federal_fiscal_year_totals"][0]["outlay_amount"] == 300000
    assert "negative" in " ".join(result["caveats"]).lower()


def test_agent_sees_tool_with_described_parameters():
    async def list_tools():
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tool = asyncio.run(list_tools())["usaspending_get_award_funding"]
    assert "obligations and outlays" in tool.description.lower()
    assert "award_id" in tool.input_schema["properties"]
    assert tool.input_schema["properties"]["award_id"].get("description")
