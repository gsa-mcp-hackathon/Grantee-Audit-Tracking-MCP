"""Tests for usaspending_sum_outlays. USAspending is replaced by a fake."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dat_mcp import usaspending
from dat_mcp.app import mcp
from dat_mcp.tools.usaspending_sum_outlays import usaspending_sum_outlays

AWARDS = [
    {
        "Award ID": "CE152817",
        "Recipient Name": "Chesapeake Hospital Authority",
        "Recipient UEI": "GMFLJZLNKK34",
        "Award Amount": 1000000,
        "Total Outlays": 1000000,
        "CFDA Number": "93.526",
        "Awarding Agency": "Department of Health and Human Services",
        "generated_internal_id": "ASST_NON_CE152817_7529",
    },
    {
        "Award ID": "H80CS00001",
        "Recipient Name": "Chesapeake Hospital Authority",
        "Recipient UEI": "GMFLJZLNKK34",
        "Award Amount": 500000,
        "Total Outlays": None,  # never reported an outlay
        "CFDA Number": "93.224",
        "Awarding Agency": "Department of Health and Human Services",
        "generated_internal_id": "ASST_NON_H80CS00001_7530",
    },
]

# Modeled on scenario S14: obligated in FY2024 month 3 (December 2023) and
# drawn February to May 2025 (FY2025 months 5 to 8). The real award was
# obligated in September 2023; both dates fall in the entity's FY2024.
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
        "gross_outlay_amount": 400000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    {
        "reporting_fiscal_year": 2025,
        "reporting_fiscal_month": 8,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 1000000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
]

# An entity year of 1 July to 30 June straddles two federal fiscal years:
# FY2024 months 10 to 12 are July to September 2024, and FY2025 months 1 to 9
# are October 2024 to June 2025. The running total resets in between.
STRADDLING_ROWS = [
    # June 2024, one month before the window opens.
    {
        "reporting_fiscal_year": 2024,
        "reporting_fiscal_month": 9,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 100000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    # August 2024, inside the window: a rise of 50,000.
    {
        "reporting_fiscal_year": 2024,
        "reporting_fiscal_month": 11,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 150000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    # January 2025, inside the window. The total reset in October, so this
    # whole 200,000 was drawn in FY2025, not 50,000.
    {
        "reporting_fiscal_year": 2025,
        "reporting_fiscal_month": 4,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 200000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
    # July 2025, one month after the window closes.
    {
        "reporting_fiscal_year": 2025,
        "reporting_fiscal_month": 10,
        "transaction_obligated_amount": 0,
        "gross_outlay_amount": 260000,
        "federal_account": "075-0350",
        "disaster_emergency_fund_code": None,
    },
]


@pytest.fixture
def fake_usaspending(monkeypatch):
    """Record each USAspending request and answer with chosen results.

    Returns a recorder. Its .sent list holds every (endpoint, payload); set
    .funding to change what awards/funding/ replies with, .funding_pages
    to reply with a different list per page, and .by_search to answer the
    award search per search text ({text: awards}) instead of with .awards.
    """

    class Recorder:
        def __init__(self):
            self.sent = []
            self.awards = list(AWARDS)
            self.funding = list(FUNDING_ROWS)
            self.funding_pages = None
            self.by_search = None

        def __iter__(self):  # so `assert recorder == []`-style checks read well
            return iter(self.sent)

        def __len__(self):
            return len(self.sent)

        @property
        def endpoints(self):
            return [endpoint for endpoint, _ in self.sent]

    recorder = Recorder()

    def fake_post(endpoint, payload):
        recorder.sent.append((endpoint, payload))
        if endpoint == "search/spending_by_award/":
            if recorder.by_search is not None:
                [text] = payload["filters"]["recipient_search_text"]
                return {"results": list(recorder.by_search.get(text, []))}
            return {"results": list(recorder.awards)}
        if recorder.funding_pages is not None:
            page = payload.get("page", 1)
            index = page - 1
            if index < len(recorder.funding_pages):
                return {"results": list(recorder.funding_pages[index])}
            return {"results": []}
        return {"results": list(recorder.funding)}

    monkeypatch.setattr(usaspending, "post", fake_post)
    return recorder


@pytest.fixture
def requests(fake_usaspending):
    """The default fake: the S14 award plus one that never reported outlays."""
    return fake_usaspending.sent


def test_entity_fiscal_year_window_is_the_twelve_months_to_the_end_date(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert result["period"]["start"] == "2024-07-01"
    assert result["period"]["end"] == "2025-06-30"


def test_totals_the_outlays_that_fall_inside_the_entity_year(requests):
    """The whole $1,000,000 was drawn in February to May 2025, inside FY2025."""
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert result["total_outlays_in_period"] == 1000000
    assert result["awards_examined"] == 1
    assert result["awards"][0]["outlays_in_period"] == 1000000


def test_the_same_award_counts_for_nothing_in_the_year_before(requests):
    """Obligated in FY2024 but not drawn, so the prior year is empty."""
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2024-06-30")

    assert result["total_outlays_in_period"] == 0
    assert result["total_obligated_in_period"] == 1000000


def test_awards_that_never_reported_an_outlay_are_not_fetched(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    endpoints = [endpoint for endpoint, _ in requests]
    assert endpoints == ["search/spending_by_award/", "awards/funding/"]
    assert result["awards_skipped_no_outlays_reported"] == 1


def test_periods_are_left_out_unless_asked_for(requests):
    """Month-by-month detail is opt-in, to keep the default answer small."""
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert "periods_counted" not in result["awards"][0]
    assert result["awards"][0]["source_url"].endswith("ASST_NON_CE152817_7529")


def test_each_award_shows_the_periods_it_counted_when_asked(requests):
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", include_periods=True
    )

    counted = result["awards"][0]["periods_counted"]
    assert [p["period_end_date"] for p in counted] == ["2025-02-28", "2025-05-31"]
    assert [p["period_outlay_amount"] for p in counted] == [400000, 600000]
    assert result["awards"][0]["source_url"].endswith("ASST_NON_CE152817_7529")


def test_a_drop_inside_the_window_is_shown_without_include_periods(fake_usaspending):
    """Hiding the months must not hide a correction that lowers the total."""
    fake_usaspending.funding = [
        dict(FUNDING_ROWS[1], gross_outlay_amount=500000),  # February 2025
        dict(FUNDING_ROWS[2], gross_outlay_amount=300000),  # May 2025, fell
    ]
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    award = result["awards"][0]
    assert award["outlays_in_period"] == 300000
    assert "periods_counted" not in award
    assert award["periods_with_decrease"] == [
        {
            "period_end_date": "2025-05-31",
            "period_outlay_amount": -200000,
            "includes_decrease": True,
        }
    ]
    assert "periods_with_decrease is set" in " ".join(result["caveats"])


def test_a_quarterly_period_in_the_window_adds_its_caveat(fake_usaspending):
    fake_usaspending.funding = [
        dict(row, is_quarterly_submission=(index == 1))
        for index, row in enumerate(FUNDING_ROWS)
    ]
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", include_periods=True
    )

    assert "off by up to two months" in " ".join(result["caveats"])
    counted = result["awards"][0]["periods_counted"]
    assert [p.get("quarterly") for p in counted] == [True, None]


def test_period_start_can_be_given_for_a_short_fiscal_year(requests):
    """A part year starting 1 March 2025 leaves the February draw out."""
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34",
        period_start="2025-03-01",
        fiscal_year_end="2025-06-30",
    )

    assert result["period"]["start"] == "2025-03-01"
    # February 2025 drew 400,000 and is now outside; May 2025 drew 600,000.
    assert result["total_outlays_in_period"] == 600000
    assert "part year" in result["period"]["description"]


def test_a_full_entity_year_says_so_in_the_description(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert "entity fiscal year ending 2025-06-30" in result["period"]["description"]
    assert "part year" not in result["period"]["description"]


def test_a_july_to_june_year_spans_two_federal_years(fake_usaspending):
    """The running total resets in October, inside the entity's own year.

    Treating gross_outlay_amount as cumulative across the whole window would
    give 200,000 - 100,000 = 100,000. The right answer is August's rise of
    50,000 plus the whole of January's 200,000, which started from zero when
    the federal year rolled over.
    """
    fake_usaspending.funding = STRADDLING_ROWS
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", include_periods=True
    )

    assert result["total_outlays_in_period"] == 250000
    counted = result["awards"][0]["periods_counted"]
    assert [p["period_end_date"] for p in counted] == ["2024-08-31", "2025-01-31"]
    assert [p["period_outlay_amount"] for p in counted] == [50000, 200000]


def test_a_truncated_funding_history_is_flagged_at_the_top(fake_usaspending):
    """A full page every time means the history ran past the paging cap."""
    full_page = [dict(FUNDING_ROWS[1]) for _ in range(usaspending.MAX_PAGE_SIZE)]
    fake_usaspending.funding = full_page

    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert result["any_funding_history_truncated"] is True
    assert result["awards"][0]["funding_history_truncated"] is True
    caveats = " ".join(result["caveats"]).lower()
    assert "any_funding_history_truncated" in caveats
    # One request for the award search, then the paging cap's worth.
    assert fake_usaspending.endpoints.count("awards/funding/") == (
        usaspending.MAX_FUNDING_PAGES
    )


def test_a_complete_funding_history_is_not_flagged(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert result["any_funding_history_truncated"] is False
    assert result["awards"][0]["funding_history_truncated"] is False


def test_a_leap_day_fiscal_year_end_starts_the_day_after_the_year_before(
    fake_usaspending,
):
    """29 February has no counterpart in 2023, so the year starts 1 March."""
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2024-02-29")

    assert result["period"]["start"] == "2023-03-01"
    assert result["period"]["end"] == "2024-02-29"


def test_the_search_is_not_limited_by_fiscal_year(requests):
    """Money obligated years earlier is often drawn inside the window."""
    usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    [(_, search_payload), _] = requests
    assert "time_period" not in search_payload["filters"]
    assert search_payload["filters"]["recipient_search_text"] == ["GMFLJZLNKK34"]


def test_more_awards_available_is_flagged(requests):
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", max_awards=1
    )

    assert result["more_awards_available"] is True
    assert result["awards_considered"] == 1
    caveats = " ".join(result["caveats"])
    assert "more_awards_available is true" in caveats
    assert "raise max_awards" in caveats.lower()


def test_conditional_caveats_follow_the_result(requests):
    """Each conditional warning appears only when its condition holds."""
    # Default fake: both awards fit, one never reported outlays, none truncated.
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")
    caveats = " ".join(result["caveats"])
    assert "more_awards_available is true" not in caveats
    assert "any_funding_history_truncated is true" not in caveats
    assert "periods_with_decrease" not in caveats
    assert "quarterly" not in caveats
    assert "periods_with_decrease" not in result["awards"][0]
    assert "were not fetched" in caveats  # one award was skipped

    # Only the award with outlays: nothing skipped, so no skipped warning.
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", max_awards=1
    )
    assert result["awards_skipped_no_outlays_reported"] == 0
    assert "were not fetched" not in " ".join(result["caveats"])


def test_a_recipient_is_required(requests):
    with pytest.raises(ToolError):
        usaspending_sum_outlays(fiscal_year_end="2025-06-30")
    with pytest.raises(ToolError):
        usaspending_sum_outlays(
            uei="GMFLJZLNKK34", name="Chesapeake", fiscal_year_end="2025-06-30"
        )
    assert requests == []


def test_bad_arguments_are_rejected(requests):
    with pytest.raises(ToolError):
        usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="June 2025")
    with pytest.raises(ToolError):
        usaspending_sum_outlays(
            uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", award_group="widgets"
        )
    with pytest.raises(ToolError):
        usaspending_sum_outlays(
            uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", max_awards=51
        )
    with pytest.raises(ToolError):
        usaspending_sum_outlays(
            uei="GMFLJZLNKK34",
            fiscal_year_end="2025-06-30",
            period_start="2026-01-01",
        )
    assert requests == []


def test_loans_are_rejected_before_calling_the_api(requests):
    """The award search reports no outlays for loans, so there is nothing to sum."""
    with pytest.raises(ToolError, match="usaspending_search_awards"):
        usaspending_sum_outlays(
            uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", award_group="loans"
        )
    assert requests == []


def test_the_grants_search_includes_f_series_awards(requests):
    usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    [(_, search_payload), _] = requests
    assert {"F001", "F002"} <= set(search_payload["filters"]["award_type_codes"])


def test_caveats_say_this_is_not_a_sefa_figure(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    caveats = " ".join(result["caveats"]).lower()
    assert "sefa" in caveats
    assert "determination" in caveats


def test_caveats_appear_only_when_they_apply(requests):
    """A clean whole-month window with a complete history gets the basics only."""
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", max_awards=10
    )

    caveats = " ".join(result["caveats"])
    assert "more_awards_available" not in caveats
    assert "any_funding_history_truncated" not in caveats
    assert "whole calendar months" not in caveats
    assert "FY2022" not in caveats
    # One award was skipped for never reporting an outlay, so that one applies.
    assert "never reported an outlay" in caveats


def test_more_awards_caveat_appears_when_awards_were_left_out(requests):
    result = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30", max_awards=1
    )

    assert result["more_awards_available"] is True
    assert "more_awards_available is true" in " ".join(result["caveats"])


def test_partial_month_and_early_year_caveats_are_conditional(requests):
    odd_window = usaspending_sum_outlays(
        uei="GMFLJZLNKK34", fiscal_year_end="2025-06-15"
    )
    assert "whole calendar months" in " ".join(odd_window["caveats"])

    early = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2021-06-30")
    assert "FY2022" in " ".join(early["caveats"])


def test_award_rows_leave_out_recipient_and_lifetime_fields(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    [award] = result["awards"]
    assert "recipient_name" not in award
    assert "award_amount_all_time" not in award
    assert "total_outlays_all_time" not in award
    assert award["cfda_number"] == "93.526"


# Two unrelated health centers with the same legal name, as USAspending
# returns them for a name search (Louisville, KY and Orangeburg, SC).
SAME_NAME_AWARDS = [
    {
        "Award ID": "H8000102",
        "Recipient Name": "FAMILY HEALTH CENTERS, INC.",
        "Recipient UEI": "CBBHV9TLLMF8",
        "Award Amount": 57000000,
        "Total Outlays": 35000000,
        "CFDA Number": "93.224",
        "Awarding Agency": "Department of Health and Human Services",
        "generated_internal_id": "ASST_NON_H8000102_075",
    },
    {
        "Award ID": "H8000090",
        "Recipient Name": "FAMILY HEALTH CENTERS, INC",
        "Recipient UEI": "MZNXZR1WCC41",
        "Award Amount": 53000000,
        "Total Outlays": 34000000,
        "CFDA Number": "93.224",
        "Awarding Agency": "Department of Health and Human Services",
        "generated_internal_id": "ASST_NON_H8000090_075",
    },
]


def test_a_name_search_matching_two_recipients_shows_each_one(fake_usaspending):
    fake_usaspending.awards = list(SAME_NAME_AWARDS)

    result = usaspending_sum_outlays(
        name="Family Health Centers, Inc.", fiscal_year_end="2025-06-30"
    )

    # The fake gives each award the same $1,000,000 drawn in FY2025.
    assert result["total_outlays_in_period"] == 2000000
    matched = result["recipient"]["recipients_matched"]
    assert {r["uei"] for r in matched} == {"CBBHV9TLLMF8", "MZNXZR1WCC41"}
    assert all(r["outlays_in_period"] == 1000000 for r in matched)
    assert {a["recipient_uei"] for a in result["awards"]} == {
        "CBBHV9TLLMF8",
        "MZNXZR1WCC41",
    }
    caveats = " ".join(result["caveats"])
    assert "lists 2 recipients" in caveats
    assert "ask, naming each" in caveats


def test_every_matched_recipient_is_listed_and_the_shares_add_up(fake_usaspending):
    """A recipient whose awards drew nothing is still named, at $0."""
    no_outlays = {
        **SAME_NAME_AWARDS[0],
        "Award ID": "H8000407",
        "Recipient Name": "TAMPA FAMILY HEALTH CENTERS, INC.",
        "Recipient UEI": "MTEMU55RVML6",
        "Total Outlays": None,
        "generated_internal_id": "ASST_NON_H8000407_075",
    }
    fake_usaspending.awards = [*SAME_NAME_AWARDS, no_outlays]

    result = usaspending_sum_outlays(
        name="Family Health Centers, Inc.", fiscal_year_end="2025-06-30"
    )

    matched = {r["uei"]: r for r in result["recipient"]["recipients_matched"]}
    assert set(matched) == {"CBBHV9TLLMF8", "MZNXZR1WCC41", "MTEMU55RVML6"}
    assert matched["MTEMU55RVML6"]["outlays_in_period"] == 0
    assert matched["MTEMU55RVML6"]["awards_considered"] == 1
    assert (
        sum(r["outlays_in_period"] for r in matched.values())
        == result["total_outlays_in_period"]
    )
    assert "lists 3 recipients" in " ".join(result["caveats"])


def test_a_name_search_matching_one_recipient_still_says_which(requests):
    result = usaspending_sum_outlays(name="Chesapeake", fiscal_year_end="2025-06-30")

    [matched] = result["recipient"]["recipients_matched"]
    assert matched == {
        "uei": "GMFLJZLNKK34",
        "name": "Chesapeake Hospital Authority",
        "awards_considered": 2,
        "outlays_in_period": 1000000,
    }
    assert "recipient_uei" not in result["awards"][0]
    assert "recipients_matched lists" not in " ".join(result["caveats"])


def test_a_uei_search_does_not_list_recipients(requests):
    result = usaspending_sum_outlays(uei="GMFLJZLNKK34", fiscal_year_end="2025-06-30")

    assert "recipients_matched" not in result["recipient"]


# Modeled on usaspending-changed-year-end: the grantee's main award sits
# under the UEI it filed under before, and a small one under the UEI asked about.
MAIN_UEI, OTHER_UEI = "TST3H6NDBEY1", "H6A4H8YX3Z24"


def _award(award_id, uei, amount):
    return {
        "Award ID": award_id,
        "Recipient Name": "MID-ATLANTIC ASSOCIATION OF COMMUNITY HEALTH CENTERS",
        "Recipient UEI": uei,
        "Award Amount": amount,
        "Total Outlays": amount,
        "CFDA Number": "93.129",
        "Awarding Agency": "Department of Health and Human Services",
        "generated_internal_id": f"ASST_NON_{award_id}_075",
    }


def test_several_ueis_are_searched_and_added_up(fake_usaspending):
    fake_usaspending.by_search = {
        MAIN_UEI: [_award("U5F41815", MAIN_UEI, 200000)],
        OTHER_UEI: [_award("U5806852", OTHER_UEI, 3000000)],
    }

    result = usaspending_sum_outlays(
        uei=[MAIN_UEI, OTHER_UEI.lower()], fiscal_year_end="2025-06-30"
    )

    searched = [
        payload["filters"]["recipient_search_text"]
        for endpoint, payload in fake_usaspending.sent
        if endpoint == "search/spending_by_award/"
    ]
    assert searched == [[MAIN_UEI], [OTHER_UEI]]
    # The fake gives each award the same $1,000,000 drawn in FY2025.
    assert result["total_outlays_in_period"] == 2000000
    assert result["recipient"]["uei"] == [MAIN_UEI, OTHER_UEI]
    parts = {
        r["uei"]: r["outlays_in_period"]
        for r in result["recipient"]["recipients_matched"]
    }
    assert parts == {MAIN_UEI: 1000000, OTHER_UEI: 1000000}
    # Largest award first, each labelled with its UEI.
    assert [(a["award_id"], a["recipient_uei"]) for a in result["awards"]] == [
        ("U5806852", OTHER_UEI),
        ("U5F41815", MAIN_UEI),
    ]
    # The UEIs were asked for together, so this isn't a name search's mix.
    assert "recipients_matched lists" not in " ".join(result["caveats"])


def test_an_award_found_under_two_ueis_is_counted_once(fake_usaspending):
    shared = _award("U5806852", OTHER_UEI, 3000000)
    fake_usaspending.by_search = {MAIN_UEI: [shared], OTHER_UEI: [shared]}

    result = usaspending_sum_outlays(
        uei=[MAIN_UEI, OTHER_UEI], fiscal_year_end="2025-06-30"
    )

    assert result["total_outlays_in_period"] == 1000000
    assert result["awards_considered"] == 1


def test_max_awards_counts_across_the_ueis_largest_first(fake_usaspending):
    fake_usaspending.by_search = {
        MAIN_UEI: [_award("SMALL1", MAIN_UEI, 10), _award("SMALL2", MAIN_UEI, 20)],
        OTHER_UEI: [_award("BIG", OTHER_UEI, 3000000)],
    }

    result = usaspending_sum_outlays(
        uei=[MAIN_UEI, OTHER_UEI], fiscal_year_end="2025-06-30", max_awards=2
    )

    assert [a["award_id"] for a in result["awards"]] == ["BIG", "SMALL2"]
    assert result["more_awards_available"] is True


def test_a_uei_with_no_awards_is_listed_at_zero(fake_usaspending):
    fake_usaspending.by_search = {OTHER_UEI: [_award("U5806852", OTHER_UEI, 3000000)]}

    result = usaspending_sum_outlays(
        uei=[MAIN_UEI, OTHER_UEI], fiscal_year_end="2025-06-30"
    )

    parts = {r["uei"]: r for r in result["recipient"]["recipients_matched"]}
    assert parts[MAIN_UEI]["awards_considered"] == 0
    assert parts[MAIN_UEI]["outlays_in_period"] == 0
    assert parts[OTHER_UEI]["outlays_in_period"] == 1000000


def test_a_list_of_one_uei_reads_as_one(requests):
    result = usaspending_sum_outlays(uei=["GMFLJZLNKK34"], fiscal_year_end="2025-06-30")

    assert result["recipient"]["uei"] == "GMFLJZLNKK34"
    assert "recipients_matched" not in result["recipient"]
    assert "recipient_uei" not in result["awards"][0]


def test_too_many_or_bad_ueis_are_refused(requests):
    with pytest.raises(ToolError, match="at most 20"):
        usaspending_sum_outlays(uei=[MAIN_UEI] * 21, fiscal_year_end="2025-06-30")
    with pytest.raises(ToolError):
        usaspending_sum_outlays(
            uei=[MAIN_UEI, "NOT-A-UEI"], fiscal_year_end="2025-06-30"
        )
    assert requests == []


def test_agent_sees_tool_with_described_parameters():
    async def list_tools():
        async with Client(mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tool = asyncio.run(list_tools())["usaspending_sum_outlays"]
    assert "own fiscal year" in tool.description.lower()
    for name in ("fiscal_year_end", "uei", "max_awards"):
        assert tool.input_schema["properties"][name].get("description"), name
