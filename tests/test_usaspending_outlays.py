"""Tests for the outlay arithmetic in dat_mcp.usaspending."""

import datetime

from dat_mcp import usaspending


def row(
    fiscal_year,
    month,
    cumulative_outlay,
    obligated=0.0,
    account="075-0350",
    defc=None,
    quarterly=False,
):
    return {
        "reporting_fiscal_year": fiscal_year,
        "reporting_fiscal_month": month,
        "gross_outlay_amount": cumulative_outlay,
        "transaction_obligated_amount": obligated,
        "federal_account": account,
        "disaster_emergency_fund_code": defc,
        "is_quarterly_submission": quarterly,
    }


def test_period_end_date_maps_federal_fiscal_months_to_calendar_months():
    # Month 1 is October of the previous calendar year, month 12 is September.
    assert usaspending.period_end_date(2025, 1) == datetime.date(2024, 10, 31)
    assert usaspending.period_end_date(2025, 3) == datetime.date(2024, 12, 31)
    assert usaspending.period_end_date(2025, 4) == datetime.date(2025, 1, 31)
    assert usaspending.period_end_date(2025, 9) == datetime.date(2025, 6, 30)
    assert usaspending.period_end_date(2025, 12) == datetime.date(2025, 9, 30)
    assert usaspending.period_end_date(2024, 5) == datetime.date(2024, 2, 29)


def test_period_end_date_rejects_months_outside_the_fiscal_year():
    for month in (0, 13, None, "5"):
        assert usaspending.period_end_date(2025, month) is None
    assert usaspending.period_end_date(None, 5) is None


def test_rows_that_name_no_calendar_month_are_dropped():
    """A period with no end date could not be placed in a date window.

    Keeping it would let the fiscal year totals and the window totals
    disagree with nothing to show why, so it is left out of both.
    """
    periods = usaspending.outlays_by_period(
        [
            row(2025, 0, 10_000, obligated=5_000),
            row(2025, None, 20_000),
            row(2025, 4, 100_000),
        ]
    )

    assert [p["reporting_fiscal_month"] for p in periods] == [4]
    assert all(p["period_end_date"] for p in periods)
    assert usaspending.federal_fiscal_year_totals(periods) == [
        {
            "federal_fiscal_year": 2025,
            "outlay_amount": 100_000.0,
            "obligated_amount": 0.0,
        },
    ]


def test_a_corrected_figure_shows_as_a_negative_period():
    """The tools caveat negatives rather than hiding them, so keep the sign."""
    periods = usaspending.outlays_by_period(
        [
            row(2025, 4, 500_000),
            row(2025, 5, 300_000),
        ]
    )

    assert [p["period_outlay_amount"] for p in periods] == [500_000, -200_000]
    assert (
        usaspending.federal_fiscal_year_totals(periods)[0]["outlay_amount"] == 300_000
    )
    assert [p.get("includes_decrease") for p in periods] == [None, True]


def test_a_drop_in_one_source_is_flagged_when_another_source_hides_it():
    """The net figure rises, but one source's running total fell."""
    periods = usaspending.outlays_by_period(
        [
            row(2025, 4, 500_000, account="075-0350"),
            row(2025, 5, 300_000, account="075-0350"),  # fell by 200,000
            row(2025, 5, 500_000, account="075-0361"),  # first draw, 500,000
        ]
    )

    assert [p["period_outlay_amount"] for p in periods] == [500_000, 300_000]
    assert [p.get("includes_decrease") for p in periods] == [None, True]


def test_float_noise_from_summed_rows_is_not_read_as_a_drop():
    """100.1 + 200.2 is 300.29999999999995 in floating point, not below 300.3."""
    periods = usaspending.outlays_by_period(
        [
            row(2025, 4, 300.3),
            row(2025, 5, 100.1),
            row(2025, 5, 200.2),
        ]
    )

    assert not any(p.get("includes_decrease") for p in periods)


def test_quarterly_submissions_are_flagged():
    """A quarterly figure covers three months, not the one its end date names."""
    periods = usaspending.outlays_by_period(
        [
            row(2021, 3, 90_000, quarterly=True),
            row(2022, 1, 10_000),
        ]
    )

    assert [p.get("quarterly") for p in periods] == [True, None]


def test_disaster_codes_are_recorded_for_each_period():
    periods = usaspending.outlays_by_period(
        [
            row(2021, 6, 50_000, defc="Q"),
            row(2021, 6, 20_000, defc="V"),
            row(2021, 7, 80_000, defc="Q"),
        ]
    )

    codes = [p["disaster_emergency_fund_codes"] for p in periods]
    assert codes == [["Q", "V"], ["Q"]]


def test_list_once_lifts_a_single_value_out_of_the_periods():
    periods = [
        {"federal_accounts": ["075-0350"]},
        {"federal_accounts": ["075-0350"]},
        {"federal_accounts": []},
    ]

    assert usaspending.list_once(periods, "federal_accounts") == ["075-0350"]
    assert all("federal_accounts" not in p for p in periods)


def test_list_once_keeps_per_period_values_when_they_vary():
    """Then each period's figure can still be tied to its account."""
    periods = [
        {"federal_accounts": ["075-0350"]},
        {"federal_accounts": ["075-0361"]},
    ]

    assert usaspending.list_once(periods, "federal_accounts") == [
        "075-0350",
        "075-0361",
    ]
    assert [p["federal_accounts"] for p in periods] == [["075-0350"], ["075-0361"]]


def test_outlays_are_the_rise_since_the_previous_period():
    """gross_outlay_amount is cumulative, so periods are differences."""
    periods = usaspending.outlays_by_period(
        [
            row(2025, 4, 100_000),
            row(2025, 5, 350_000),
            row(2025, 6, 600_000),
        ]
    )

    assert [p["period_outlay_amount"] for p in periods] == [100_000, 250_000, 250_000]
    # The naive total over-counts; the corrected one does not.
    assert sum(p["period_outlay_amount"] for p in periods) == 600_000


def test_the_running_total_restarts_each_federal_fiscal_year():
    periods = usaspending.outlays_by_period(
        [
            row(2024, 11, 400_000),
            row(2024, 12, 900_000),
            row(2025, 1, 150_000),
            row(2025, 2, 200_000),
        ]
    )

    assert [p["period_outlay_amount"] for p in periods] == [
        400_000,
        500_000,
        150_000,
        50_000,
    ]


def test_funding_sources_are_differenced_separately():
    """Two accounts each keep their own running total for the same periods."""
    periods = usaspending.outlays_by_period(
        [
            row(2025, 4, 100_000, account="075-0350"),
            row(2025, 4, 10_000, account="075-0943"),
            row(2025, 5, 250_000, account="075-0350"),
            row(2025, 5, 30_000, account="075-0943"),
        ]
    )

    assert [p["period_outlay_amount"] for p in periods] == [110_000, 170_000]
    assert periods[0]["federal_accounts"] == ["075-0350", "075-0943"]


def test_obligations_are_added_not_differenced():
    """transaction_obligated_amount is already a per-period change."""
    periods = usaspending.outlays_by_period(
        [
            row(2024, 3, 0, obligated=600_000),
            row(2024, 3, 0, obligated=400_000, account="075-0943"),
            row(2024, 4, 0, obligated=50_000),
        ]
    )

    assert [p["obligated_amount"] for p in periods] == [1_000_000, 50_000]


def test_rows_with_no_outlay_reported_do_not_become_zero_outlays():
    periods = usaspending.outlays_by_period(
        [
            row(2021, 6, None, obligated=1_000_000),
            row(2022, 6, 25_000),
        ]
    )

    assert periods[0]["period_outlay_amount"] is None
    assert periods[0]["obligated_amount"] == 1_000_000
    assert periods[1]["period_outlay_amount"] == 25_000


def test_federal_fiscal_year_totals_sum_the_periods():
    periods = usaspending.outlays_by_period(
        [
            row(2024, 11, 400_000),
            row(2024, 12, 900_000),
            row(2025, 1, 150_000),
        ]
    )

    assert usaspending.federal_fiscal_year_totals(periods) == [
        {
            "federal_fiscal_year": 2024,
            "outlay_amount": 900_000.0,
            "obligated_amount": 0.0,
        },
        {
            "federal_fiscal_year": 2025,
            "outlay_amount": 150_000.0,
            "obligated_amount": 0.0,
        },
    ]


def test_sum_periods_counts_only_months_ending_inside_the_window():
    """An entity fiscal year of July 2024 to June 2025 spans two federal years."""
    periods = usaspending.outlays_by_period(
        [
            row(2024, 9, 100_000),  # June 2024, before the window
            row(2024, 10, 300_000),  # July 2024, inside
            row(2024, 12, 500_000),  # September 2024, inside
            row(2025, 9, 250_000),  # June 2025, inside
            row(2025, 10, 400_000),  # July 2025, after the window
        ]
    )

    window = usaspending.sum_periods(
        periods, datetime.date(2024, 7, 1), datetime.date(2025, 6, 30)
    )

    # July 2024 (200k) + September 2024 (200k) + June 2025 (250k).
    assert window["outlay_amount"] == 650_000
    assert [p["period_end_date"] for p in window["periods"]] == [
        "2024-07-31",
        "2024-09-30",
        "2025-06-30",
    ]
