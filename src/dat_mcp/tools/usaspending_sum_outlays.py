"""usaspending_sum_outlays: outlays over a grantee's own fiscal year."""

import datetime

from fastmcp.exceptions import ToolError

from dat_mcp import identifiers, usaspending

FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Award Amount",
    "Total Outlays",
    "CFDA Number",
    "Awarding Agency",
    "generated_internal_id",
]

# Always returned. The rest are added only when they apply to this answer.
CAVEATS = [
    "Outlays are Treasury's record of cash drawn down. A SEFA states its own "
    "basis (accounting_policies in fac_get_sefa_notes), often accrual, so the "
    "two can differ for timing reasons alone; do not treat this total as a "
    "SEFA amount. fac_get_awards has what the auditee reported.",
    "Only prime awards to this recipient, in one award_group, are counted. "
    "Provider Relief Fund and pass-through money do not appear here.",
    "This is evidence for an analyst, not a determination of whether a Single "
    "Audit was required.",
]

PARTIAL_MONTHS = (
    "Reporting periods are whole calendar months, so a window that does not "
    "start on the first or end on the last day of a month is rounded to the "
    "months ending inside it."
)
OUTLAYS_NOT_REQUIRED_BEFORE = datetime.date(2021, 10, 1)
EARLY_YEARS = (
    "Outlay reporting was only required from federal FY2022 (FY2020 for COVID "
    "supplemental awards), so this total is low or zero for earlier periods "
    "even where money was spent."
)
MORE_AWARDS = (
    "more_awards_available is true: the recipient has further awards, highest "
    "award amount first, that were not counted. Raise max_awards, up to 50, or "
    "narrow the search."
)
SKIPPED_AWARDS = (
    "Awards that never reported an outlay were not fetched, so any obligations "
    "they made inside the window are missing from total_obligated_in_period."
)
TRUNCATED = (
    "any_funding_history_truncated is true: at least one award reports more "
    "funding rows than the server pages through, so its newest periods are "
    "missing and the totals are understated. Check the awards with "
    "funding_history_truncated set on usaspending.gov."
)
DECREASE_CAVEAT = (
    "periods_with_decrease is set on at least one award: a funding source's "
    "running total fell in those periods, so that award's outlays_in_period "
    "is net of the drop, and a period can be negative. That happens when an "
    "agency corrects an earlier figure or a funding source stops reporting. "
    "Treat it as a question for the agency, not as money returned; "
    "usaspending_get_award_funding shows the award's full history."
)
SEVERAL_RECIPIENTS = (
    "recipients_matched lists {count} recipients with different UEIs, and "
    "total_outlays_in_period adds them all. A name search matches every "
    "recipient whose name contains the words, so these may be unrelated "
    "organizations, or one organization filing under several UEIs. Check "
    "which belong to the organization asked about (sam_get_registration gives "
    "each UEI's address; FAC audits show a shared EIN) before using any "
    "total. If the question doesn't say which one, ask, naming each with its "
    "UEI and location. Rerun with uei for one recipient's total."
)
QUARTERLY_CAVEAT = (
    "quarterly: at least one counted period was reported in a quarterly "
    "submission and covers the three months ending on its period_end_date. A "
    "quarter is counted whole if it ends inside the window and not at all if "
    "it ends after it, so the total can be off by up to two months at either "
    "edge. Set include_periods to see which periods are quarterly."
)


def _brief(period):
    """The fields of a counted period worth showing the agent."""
    brief = {
        "period_end_date": period["period_end_date"],
        "period_outlay_amount": period["period_outlay_amount"],
    }
    for flag in ("includes_decrease", "quarterly"):
        if period.get(flag):
            brief[flag] = True
    return brief


def _entity_fy_start(fiscal_year_end):
    """Return the first day of the 12-month year ending on fiscal_year_end."""
    try:
        same_day_last_year = fiscal_year_end.replace(year=fiscal_year_end.year - 1)
    except ValueError:  # 29 February in a year the previous one did not have
        same_day_last_year = fiscal_year_end.replace(
            year=fiscal_year_end.year - 1, day=28
        )
    return same_day_last_year + datetime.timedelta(days=1)


def _share(uei):
    """One recipient's line in recipients_matched, before its awards are added."""
    return {"uei": uei, "name": None, "awards_considered": 0, "outlays_in_period": 0.0}


def usaspending_sum_outlays(
    fiscal_year_end: str,
    uei: str | list[str] | None = None,
    name: str | None = None,
    period_start: str | None = None,
    award_group: str = "grants",
    agency: str | None = None,
    max_awards: int = 10,
    include_periods: bool = False,
):
    """Total what a grantee drew down across its awards over its own fiscal year.

    Answers "how much federal money did this organization spend in the year
    ending 30 June 2025?" The window is the 12 months ending on
    fiscal_year_end, not the federal fiscal year. The tool turns USAspending's
    running totals into per-period amounts and adds those inside the window.

    USAspending has no year-end date. Take fy_end_date from the entity's
    latest FAC audit (fac_search_audits), or fiscal_year_end from
    sam_get_registration, which gives month and day only. If they disagree,
    the year end may have changed: say so, and use period_start for the
    first year after the change, which can be shorter or longer than 12
    months. It starts the day after the previous audit's fy_end_date.

    Returns total_outlays_in_period with each award's outlays_in_period and
    source_url, plus caveats. Check more_awards_available and
    any_funding_history_truncated before calling the total complete. Each
    award examined costs at least one request, so keep max_awards as low as
    the question allows. A name search also returns recipients_matched, the
    UEIs it found; if there are several, the total adds them all. With
    several UEIs given, recipients_matched gives each one's part.

    Args:
        fiscal_year_end: Last day of the entity's fiscal year, e.g. 2025-06-30.
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of up to 20. Give
            every UEI the grantee has filed under (grantee_audit_coverage's
            identity lists them): its awards can sit under any of them, and
            the total adds them up. Give uei or name.
        name: Recipient name to search for, if there is no UEI. Matches every
            recipient whose name contains it.
        period_start: First day of a fiscal year that isn't 12 months long,
            e.g. 2024-10-01.
        award_group: grants, direct_payments, or other. Not loans.
        agency: Awarding agency name, e.g. Department of Health and Human Services.
        max_awards: Most awards to total, 1 to 50, highest award amount
            first, across all the UEIs given.
        include_periods: Also list each award's outlay by month.
    """
    if not (uei or name):
        raise ToolError("Give at least one of uei or name.")
    if uei and name:
        raise ToolError("Give uei or name, not both.")
    ueis = identifiers.many(uei, identifiers.uei, "uei") if uei else []
    if award_group not in usaspending.AWARD_TYPE_GROUPS:
        raise ToolError("award_group must be grants, direct_payments, loans, or other.")
    if award_group == "loans":
        raise ToolError(
            "usaspending_sum_outlays does not total loans: USAspending's award "
            "search reports no outlays for them. Use usaspending_search_awards "
            "with award_group loans to list a recipient's loans."
        )
    if not 1 <= max_awards <= 50:
        raise ToolError("max_awards must be between 1 and 50.")

    end = usaspending.parse_date(fiscal_year_end, "fiscal_year_end")
    start = (
        usaspending.parse_date(period_start, "period_start")
        if period_start
        else _entity_fy_start(end)
    )
    if start > end:
        raise ToolError("period_start must be on or before fiscal_year_end.")

    def search(text):
        filters = {
            "award_type_codes": usaspending.AWARD_TYPE_GROUPS[award_group],
            # USAspending matches one search text, so one search per UEI.
            "recipient_search_text": [text],
        }
        if agency:
            filters["agencies"] = [
                {"type": "awarding", "tier": "toptier", "name": agency}
            ]
        # No time filter on the search: money obligated years earlier is often
        # drawn down inside the window, and that is the whole point of the tool.
        return usaspending.post(
            "search/spending_by_award/",
            {
                "filters": filters,
                "fields": FIELDS,
                "sort": "Award Amount",
                "order": "desc",
                "limit": max_awards + 1,
                "page": 1,
                "subawards": False,
            },
        ).get("results", [])

    if ueis:
        # A grantee's awards can sit under any UEI it has filed under: one
        # total across them, each award once, the largest first as for one.
        found, seen, more_awards_available = [], set(), False
        for one in ueis:
            rows = search(one)
            more_awards_available = more_awards_available or len(rows) > max_awards
            for row in rows[:max_awards]:
                key = row.get("generated_internal_id") or row.get("Award ID")
                if key not in seen:
                    seen.add(key)
                    found.append(row)
        found.sort(key=lambda award: -(award.get("Award Amount") or 0))
        more_awards_available = more_awards_available or len(found) > max_awards
    else:
        found = search(name)
        more_awards_available = len(found) > max_awards
    found = found[:max_awards]
    awards_considered = len(found)

    # A name search matches every recipient whose name contains the words,
    # so one total can mix unrelated organizations. Keep each recipient's
    # share, so the agent can see the mix and split it.
    # With several UEIs given, each is listed, at $0 if nothing is under it.
    recipients = {one: _share(one) for one in (ueis if len(ueis) > 1 else [])}
    for award in found:
        key = award.get("Recipient UEI")
        entry = recipients.setdefault(key, _share(key))
        entry["name"] = entry["name"] or award.get("Recipient Name")
        entry["awards_considered"] += 1
    several_recipients = len(recipients) > 1
    # Recipients nobody asked for: all of a name search's, or another
    # organization a UEI search turned up.
    unasked = [key for key in recipients if key not in ueis]

    awards = []
    skipped = 0
    total_outlays = 0.0
    total_obligated = 0.0
    any_truncated = False
    any_decrease = False
    any_quarterly = False

    for award in found:
        internal_id = award.get("generated_internal_id")
        lifetime_outlays = award.get("Total Outlays")
        # An award that never reported an outlay has nothing in any window,
        # so skip the request rather than pay for an empty answer.
        if not internal_id or not lifetime_outlays:
            skipped += 1
            continue

        rows, truncated = usaspending.fetch_award_funding(internal_id)
        periods = usaspending.outlays_by_period(rows)
        window = usaspending.sum_periods(periods, start, end)
        counted = window["periods"]
        decreases = [period for period in counted if period.get("includes_decrease")]
        any_truncated = any_truncated or truncated
        any_decrease = any_decrease or bool(decreases)
        any_quarterly = any_quarterly or any(p.get("quarterly") for p in counted)

        total_outlays += window["outlay_amount"]
        total_obligated += window["obligated_amount"]
        recipients[award.get("Recipient UEI")]["outlays_in_period"] += window[
            "outlay_amount"
        ]
        # Lifetime amounts are left out: they invite reading them as the
        # period's, and usaspending_search_awards has them. Recipient fields
        # are too when there is one recipient, since it's given at the top.
        record = {
            "award_id": award.get("Award ID"),
            "generated_internal_id": internal_id,
            "cfda_number": award.get("CFDA Number"),
            "awarding_agency": award.get("Awarding Agency"),
            "outlays_in_period": window["outlay_amount"],
            "obligated_in_period": window["obligated_amount"],
            "funding_history_truncated": truncated,
            "source_url": f"https://www.usaspending.gov/award/{internal_id}",
        }
        if several_recipients:
            record["recipient_uei"] = award.get("Recipient UEI")
            record["recipient_name"] = award.get("Recipient Name")
        if include_periods:
            record["periods_counted"] = [
                _brief(period)
                for period in counted
                if period["period_outlay_amount"] or period.get("includes_decrease")
            ]
        # Shown even without include_periods: a drop is what makes an award's
        # total need explaining, and it is otherwise invisible.
        if decreases:
            record["periods_with_decrease"] = [_brief(period) for period in decreases]
        awards.append(record)

    caveats = list(CAVEATS)
    if start.day != 1 or (end + datetime.timedelta(days=1)).day != 1:
        caveats.append(PARTIAL_MONTHS)
    if start < OUTLAYS_NOT_REQUIRED_BEFORE:
        caveats.append(EARLY_YEARS)
    if more_awards_available:
        caveats.append(MORE_AWARDS)
    if skipped:
        caveats.append(SKIPPED_AWARDS)
    if any_truncated:
        caveats.append(TRUNCATED)
    if any_decrease:
        caveats.append(DECREASE_CAVEAT)
    if any_quarterly:
        caveats.append(QUARTERLY_CAVEAT)
    if several_recipients and unasked:
        caveats.append(SEVERAL_RECIPIENTS.format(count=len(recipients)))

    recipient = {
        "uei": (ueis[0] if len(ueis) == 1 else ueis) if ueis else None,
        "name_searched": name,
    }
    # Always shown for a name search, so the agent can confirm it found the
    # organization it meant; for a UEI search only when it matched several.
    if name or several_recipients:
        recipient["recipients_matched"] = [
            {**r, "outlays_in_period": round(r["outlays_in_period"], 2)}
            for r in sorted(recipients.values(), key=lambda r: -r["outlays_in_period"])
        ]

    return {
        "recipient": recipient,
        "period": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "description": (
                f"{start.isoformat()} to {end.isoformat()}, the entity fiscal "
                f"year ending {end.isoformat()}"
                if not period_start
                else f"{start.isoformat()} to {end.isoformat()}, a part year "
                f"within the entity fiscal year ending {end.isoformat()}"
            ),
        },
        "total_outlays_in_period": round(total_outlays, 2),
        "total_obligated_in_period": round(total_obligated, 2),
        "awards_considered": awards_considered,
        "awards_examined": len(awards),
        "awards_skipped_no_outlays_reported": skipped,
        "more_awards_available": more_awards_available,
        "any_funding_history_truncated": any_truncated,
        "awards": awards,
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        usaspending_sum_outlays,
        name="usaspending_sum_outlays",
        annotations={
            "title": "Sum outlays over an entity fiscal year",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
