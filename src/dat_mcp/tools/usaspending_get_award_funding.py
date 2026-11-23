"""usaspending_get_award_funding: obligations and outlays by accounting period."""

from fastmcp.exceptions import ToolError

from dat_mcp import usaspending

CAVEATS = [
    "Obligations are commitments, not expenditures. Outlays are the closest "
    "public proxy for when money was actually drawn.",
    "Periods are federal fiscal year months: month 1 is October, month 12 is "
    "September. period_end_date is the last day each period covers.",
    "Outlay reporting was only required from FY2022 (FY2020 for COVID "
    "supplemental awards). Earlier periods may show obligations with no "
    "outlays, and an award can show no outlays at all.",
]

# Added only when the award shows the condition, so the common case stays short.
DECREASE_CAVEAT = (
    "includes_decrease is set on at least one period: a funding source's "
    "running total fell there, so that period's outlay is net of the drop and "
    "can be negative. That happens when an agency corrects an earlier figure, "
    "or when one of an account's funding sources stops reporting. The "
    "endpoint does not identify funding sources precisely enough to separate "
    "those cases; treat these periods as a question for the agency, not as "
    "money returned."
)
QUARTERLY_CAVEAT = (
    "quarterly is set on at least one period: the agency reported it in a "
    "quarterly submission, so its figures cover the three months ending on "
    "period_end_date, not one month."
)
TRUNCATED_CAVEAT = (
    "funding_history_truncated is true: this award reports more rows than the "
    "server will page through and the newest periods are missing. The latest "
    "federal fiscal year shown is then both incomplete and overstated in its "
    "first surviving period, because that period is measured from zero. Treat "
    "the recent figures as unusable and go to the award page."
)


def usaspending_get_award_funding(award_id: str):
    """Get obligations and outlays by accounting period for one USAspending award.

    Shows when an award's money was obligated and actually drawn, e.g. "was
    it spent in FY2024 or FY2025?" award_id is the generated_internal_id from
    usaspending_search_awards. For a grantee's total over its own fiscal
    year, use usaspending_sum_outlays.

    Each period's period_outlay_amount is what was drawn that period, so
    periods add up; federal_fiscal_year_totals sums them by federal year.
    Federal accounts and disaster emergency fund codes are listed once for
    the award; code Q is regular, non-emergency funding, and COVID-19 money
    has its own codes. If funding_history_truncated is
    true, the newest periods are missing and the totals are not reliable.

    Args:
        award_id: Generated internal ID, e.g. ASST_NON_H80CS12345_075.
    """
    if not award_id or not award_id.strip():
        raise ToolError(
            "award_id is required. Get it from usaspending_search_awards "
            "(the generated_internal_id field)."
        )
    award_id = award_id.strip()

    rows, truncated = usaspending.fetch_award_funding(award_id)
    periods = usaspending.outlays_by_period(rows)

    # Usually one value for the whole award; kept per period only when it varies.
    accounts = usaspending.list_once(periods, "federal_accounts")
    codes = usaspending.list_once(periods, "disaster_emergency_fund_codes")

    caveats = list(CAVEATS)
    if any(period.get("includes_decrease") for period in periods):
        caveats.append(DECREASE_CAVEAT)
    if any(period.get("quarterly") for period in periods):
        caveats.append(QUARTERLY_CAVEAT)
    if truncated:
        caveats.append(TRUNCATED_CAVEAT)

    return {
        "award_id": award_id,
        "funding_history_truncated": truncated,
        "federal_accounts": accounts,
        "disaster_emergency_fund_codes": codes,
        "periods": periods,
        "federal_fiscal_year_totals": usaspending.federal_fiscal_year_totals(periods),
        "source_url": f"https://www.usaspending.gov/award/{award_id}",
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        usaspending_get_award_funding,
        name="usaspending_get_award_funding",
        annotations={
            "title": "Get award funding by period",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
