"""USAspending API client (api.usaspending.gov).

Also holds the outlay arithmetic both USAspending tools need. Outlays are the
closest public proxy for when a grantee actually drew federal money down, but
the API reports them as running totals, so turning them into "what was spent
between these two dates" takes more than addition. See outlays_by_period.
"""

import datetime

import httpx
from fastmcp.exceptions import ToolError

from dat_mcp import net

USASPENDING_API = "https://api.usaspending.gov/api/v2"

# awards/funding/ rejects a page size above 100.
MAX_PAGE_SIZE = 100

# Stop paging one award's funding history here. A page is 100 rows, not 100
# periods: the endpoint splits each period by federal account, object class,
# program activity and DEFC, so a complex award can report dozens of rows per
# month. An award that hits this cap comes back flagged, because the rows that
# go missing are the newest ones and the totals would be wrong without saying
# so. Raising it costs one request per page per award.
MAX_FUNDING_PAGES = 10

# gross_outlay_amount is a running total kept separately for each funding
# source. These fields are the coarsest split the funding endpoint reports;
# the period of availability (TAS) that actually separates sources is not in
# the response, and the rows are split finer than this besides (object class,
# program activity). Summing the rows that share these fields is therefore the
# safe move: only their total behaves as one running total.
FUNDING_SOURCE_FIELDS = ("federal_account", "disaster_emergency_fund_code")

# spending_by_award requires award_type_codes. Codes are OR'd, but
# type-specific fields are only valid for their own group, so ask for one
# group at a time. Each group holds the older two-digit codes and the F-series
# codes added later, grouped as references/award_types/ groups them. Recent
# awards, including most current HRSA grants, carry only an F-series code, so a
# group without those codes drops them silently.
AWARD_TYPE_GROUPS = {
    "grants": ["02", "03", "04", "05", "F001", "F002"],
    "direct_payments": ["06", "10", "F006", "F007"],
    "loans": ["07", "08", "F003", "F004"],
    "other": ["09", "11", "-1", "F005", "F008", "F009", "F010"],
}


def post(endpoint, payload):
    """POST to a USAspending endpoint and return the parsed JSON.

    USAspending search endpoints accept POST with a JSON body.
    No API key is required.
    """
    try:
        response = httpx.post(
            f"{USASPENDING_API}/{endpoint}",
            json=payload,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            verify=net.SSL_CONTEXT,
        )
    except httpx.HTTPError:
        raise ToolError("Could not reach the USAspending API.") from None
    if response.status_code != 200:
        raise ToolError(f"USAspending API returned HTTP {response.status_code}.")
    return response.json()


def federal_fy_period(fy):
    """Return a USAspending time_period dict for one federal fiscal year.

    Federal FY2025 runs from 2024-10-01 through 2025-09-30.
    """
    return {"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}


def parse_date(value, field):
    """Return value as a date, or raise a ToolError naming the field."""
    try:
        return datetime.date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        raise ToolError(f"{field} must be a date like 2025-06-30.") from None


def period_end_date(fiscal_year, fiscal_month):
    """Return the last day of the calendar month a reporting period covers.

    Reporting periods are federal fiscal months: month 1 is October of the
    previous calendar year and month 12 is September. FY2025 month 9 is
    June 2025, so this returns 2025-06-30. Returns None if the year or month
    is missing or outside 1 to 12, which the callers treat as unusable.
    """
    if not isinstance(fiscal_year, int) or not isinstance(fiscal_month, int):
        return None
    if not fiscal_year or not 1 <= fiscal_month <= 12:
        return None
    calendar_month = fiscal_month + 9
    calendar_year = fiscal_year - 1
    if calendar_month > 12:
        calendar_month -= 12
        calendar_year = fiscal_year
    if calendar_month == 12:
        return datetime.date(calendar_year, 12, 31)
    first_of_next = datetime.date(calendar_year, calendar_month + 1, 1)
    return first_of_next - datetime.timedelta(days=1)


def fetch_award_funding(award_id):
    """Return every funding row for one award, plus whether the list was cut.

    Pages through awards/funding/ rather than taking the first page, because
    an outlay figure for a period is only meaningful next to the periods
    before it in the same federal fiscal year. Returns (rows, truncated).

    Paging runs oldest first so that the earliest period of each fiscal year
    is always present. The cost is that when truncated is True the rows that
    went missing are the most recent ones, and the fiscal year they fall in
    is both incomplete and wrong: its first surviving period gets differenced
    from zero and absorbs everything drawn before it. Callers must pass
    truncated on to whoever reads the totals.
    """
    rows = []
    for page in range(1, MAX_FUNDING_PAGES + 1):
        data = post(
            "awards/funding/",
            {
                "award_id": award_id,
                "limit": MAX_PAGE_SIZE,
                "page": page,
                "sort": "reporting_fiscal_date",
                "order": "asc",
            },
        )
        results = data.get("results") or []
        rows.extend(results)
        if len(results) < MAX_PAGE_SIZE:
            return rows, False
    return rows, True


def outlays_by_period(rows):
    """Turn cumulative funding rows into the amount drawn in each period.

    gross_outlay_amount is a running total within one federal fiscal year,
    kept separately for each funding source, so adding the rows up counts the
    same dollars once per period. What was drawn in a period is the rise since
    the previous reported period of the same fiscal year and funding source;
    the first reported period of a year starts from zero, and the total resets
    every October.

    transaction_obligated_amount is already a per-period change, so it is
    added rather than differenced.

    Returns a list of periods, oldest first, each {"reporting_fiscal_year",
    "reporting_fiscal_month", "period_end_date", "period_outlay_amount",
    "obligated_amount", "federal_accounts", "disaster_emergency_fund_codes"}.
    Two flags appear only on the periods they apply to:
    - "includes_decrease": True where some funding source's running total
      fell, so the period's figure is net of a drop. Checking each source
      catches a drop that another source's draw hides in the net figure.
    - "quarterly": True where the agency reported in a quarterly
      submission, so the figure covers the three months ending on
      period_end_date rather than one month.

    Rows whose year or month does not map to a calendar month are dropped: a
    period with no end date could not be placed in a date window, so counting
    it in the fiscal year totals and not in the windows would make the two
    disagree silently.
    """
    # One federal account can report several rows for the same period, one per
    # period of availability. The endpoint does not say which is which, so sum
    # them: only their total behaves as a running total.
    running_totals = {}  # (fiscal year, source) -> {month: cumulative outlay}
    obligated = {}  # (fiscal year, month) -> obligated in that period
    accounts = {}  # (fiscal year, month) -> federal accounts reporting
    codes = {}  # (fiscal year, month) -> disaster emergency fund codes reporting
    quarterly = set()  # periods reported in a quarterly submission

    for row in rows:
        fiscal_year = row.get("reporting_fiscal_year")
        month = row.get("reporting_fiscal_month")
        if period_end_date(fiscal_year, month) is None:
            continue
        period = (fiscal_year, month)

        cumulative = row.get("gross_outlay_amount")
        if cumulative is not None:
            source = tuple(row.get(field) for field in FUNDING_SOURCE_FIELDS)
            points = running_totals.setdefault((fiscal_year, source), {})
            points[month] = points.get(month, 0.0) + cumulative

        obligated[period] = obligated.get(period, 0.0) + (
            row.get("transaction_obligated_amount") or 0.0
        )
        if row.get("federal_account"):
            accounts.setdefault(period, set()).add(row["federal_account"])
        if row.get("disaster_emergency_fund_code"):
            codes.setdefault(period, set()).add(row["disaster_emergency_fund_code"])
        if row.get("is_quarterly_submission"):
            quarterly.add(period)

    drawn = {}  # (fiscal year, month) -> outlay change in that period
    decreased = set()  # periods where some funding source's running total fell
    for (fiscal_year, _source), points in running_totals.items():
        previous = 0.0
        for month in sorted(points):
            period = (fiscal_year, month)
            change = points[month] - previous
            drawn[period] = drawn.get(period, 0.0) + change
            # Rounded so float noise from summing cents is not read as a drop.
            if round(change, 2) < 0:
                decreased.add(period)
            previous = points[month]

    periods = []
    for fiscal_year, month in sorted(set(obligated) | set(drawn)):
        period = (fiscal_year, month)
        # Never None: rows that do not map to a calendar month were dropped.
        ends = period_end_date(fiscal_year, month)
        record = {
            "reporting_fiscal_year": fiscal_year,
            "reporting_fiscal_month": month,
            "period_end_date": ends.isoformat(),
            "period_outlay_amount": (
                round(drawn[period], 2) if period in drawn else None
            ),
            "obligated_amount": round(obligated.get(period, 0.0), 2),
            "federal_accounts": sorted(accounts.get(period, ())),
            "disaster_emergency_fund_codes": sorted(codes.get(period, ())),
        }
        if period in decreased:
            record["includes_decrease"] = True
        if period in quarterly:
            record["quarterly"] = True
        periods.append(record)
    return periods


def list_once(periods, field):
    """Return the values of a per-period list field, listed once for the award.

    When only one value appears across all periods, as is usual for federal
    accounts and disaster codes, the field is removed from each period so it
    is not repeated on every row. When values vary, each period keeps its own
    list, so a figure can still be tied to the account or code it came from.
    """
    values = set()
    for period in periods:
        values.update(period.get(field, ()))
    if len(values) <= 1:
        for period in periods:
            period.pop(field, None)
    return sorted(values)


def federal_fiscal_year_totals(periods):
    """Total the per-period figures into one row per federal fiscal year."""
    totals = {}
    for period in periods:
        fiscal_year = period["reporting_fiscal_year"]
        running = totals.setdefault(
            fiscal_year, {"outlay_amount": 0.0, "obligated_amount": 0.0}
        )
        running["outlay_amount"] += period["period_outlay_amount"] or 0.0
        running["obligated_amount"] += period["obligated_amount"] or 0.0
    return [
        {
            "federal_fiscal_year": fiscal_year,
            "outlay_amount": round(running["outlay_amount"], 2),
            "obligated_amount": round(running["obligated_amount"], 2),
        }
        for fiscal_year, running in sorted(totals.items())
    ]


def sum_periods(periods, start, end):
    """Total the periods whose month ends within start..end, inclusive.

    Returns {"outlay_amount", "obligated_amount", "periods"}, where periods
    are the ones counted, so the caller can show its work.
    """
    counted = [
        period
        for period in periods
        if period["period_end_date"]
        and start <= datetime.date.fromisoformat(period["period_end_date"]) <= end
    ]
    return {
        "outlay_amount": round(
            sum(period["period_outlay_amount"] or 0.0 for period in counted), 2
        ),
        "obligated_amount": round(
            sum(period["obligated_amount"] or 0.0 for period in counted), 2
        ),
        "periods": counted,
    }
