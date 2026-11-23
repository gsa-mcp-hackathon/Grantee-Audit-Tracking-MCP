"""usaspending_search_awards: search a recipient's federal awards."""

from fastmcp.exceptions import ToolError

from dat_mcp import identifiers, usaspending

GRANT_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Start Date",
    "End Date",
    "Award Amount",
    "Total Outlays",
    "Awarding Agency",
    "Awarding Sub Agency",
    "CFDA Number",
    "Award Type",
    "generated_internal_id",
]

# Loans have no Award Amount, Total Outlays, or start and end dates in
# spending_by_award, and the API rejects a request that names them.
LOAN_FIELDS = [
    "Award ID",
    "Recipient Name",
    "Recipient UEI",
    "Issued Date",
    "Loan Value",
    "Subsidy Cost",
    "Awarding Agency",
    "Awarding Sub Agency",
    "CFDA Number",
    "Award Type",
    "generated_internal_id",
]

LOAN_CAVEATS = [
    "For loans, Loan Value is the face value of the loan and Subsidy Cost is "
    "the government's estimated long-term cost. Neither is money spent in a "
    "year, and the search reports no outlays for loans.",
]

CAVEATS = [
    "Award Amount and Total Outlays are lifetime totals for the whole award, "
    "from its start to the latest reporting, not amounts for any one year. "
    "Do not add them up for a fiscal year.",
    "fiscal_year selects which awards come back (those with an action in "
    "that federal fiscal year). It does not break the amounts down by year. "
    "For money drawn inside one year, use usaspending_get_award_funding for "
    "a single award or usaspending_sum_outlays for a recipient.",
    "Federal fiscal years run October 1 to September 30 and are not the "
    "entity's own fiscal year, which is what the audit threshold is measured "
    "on.",
    "Obligations are commitments, not expenditures. Outlays are the closest "
    "public proxy for spending; outlay reporting was only required from "
    "FY2022.",
    "Provider Relief Fund and pass-through money may not appear as prime "
    "awards to this UEI; check subawards for pass-through.",
]


def usaspending_search_awards(
    uei: str | None = None,
    name: str | None = None,
    fiscal_year: int | None = None,
    award_group: str = "grants",
    agency: str | None = None,
    limit: int = 25,
):
    """Search a recipient's federal awards on USAspending by UEI or name.

    Returns each award's FAIN, recipient, dates, amounts, agency, CFDA
    number, generated_internal_id and source_url. Award Amount and Total
    Outlays are lifetime totals, not a year's spending, and fiscal_year only
    picks awards with an action in that federal year. For when money was
    drawn, pass generated_internal_id to usaspending_get_award_funding, or use
    usaspending_sum_outlays for a recipient's own fiscal year. Loans show
    Loan Value and Subsidy Cost instead.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6.
        name: Recipient name to search for, if there is no UEI. Partial matches work.
        fiscal_year: Federal fiscal year, e.g. 2025 (Oct 2024 to Sep 2025).
        award_group: grants, direct_payments, loans, or other.
        agency: Awarding agency name, e.g. Department of Health and Human Services.
        limit: Most awards to return, 1 to 100, highest amounts first.
    """
    if not (uei or name):
        raise ToolError("Give at least one of uei or name.")
    if award_group not in usaspending.AWARD_TYPE_GROUPS:
        raise ToolError("award_group must be grants, direct_payments, loans, or other.")
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100.")

    type_codes = usaspending.AWARD_TYPE_GROUPS[award_group]

    filters = {"award_type_codes": type_codes}
    if uei:
        filters["recipient_search_text"] = [identifiers.uei(uei)]
    if name:
        if uei:
            raise ToolError(
                "Give uei or name, not both. recipient_search_text takes one value."
            )
        filters["recipient_search_text"] = [name]
    if fiscal_year:
        if not 2008 <= fiscal_year <= 2035:
            raise ToolError("fiscal_year must be between 2008 and 2035.")
        filters["time_period"] = [
            {**usaspending.federal_fy_period(fiscal_year), "date_type": "action_date"}
        ]
    if agency:
        filters["agencies"] = [{"type": "awarding", "tier": "toptier", "name": agency}]

    if award_group == "loans":
        fields, sort, caveats = LOAN_FIELDS, "Loan Value", CAVEATS + LOAN_CAVEATS
    else:
        fields, sort, caveats = GRANT_FIELDS, "Award Amount", CAVEATS

    payload = {
        "filters": filters,
        "fields": fields,
        "sort": sort,
        "order": "desc",
        "limit": limit,
        "page": 1,
        "subawards": False,
    }

    data = usaspending.post("search/spending_by_award/", payload)
    # The API adds bookkeeping fields nobody asked for (internal_id,
    # awarding_agency_id, agency_slug) to every row; keep only the requested
    # ones so a long award list doesn't carry them into the agent's context.
    awards = [
        {field: row.get(field) for field in fields} for row in data.get("results", [])
    ]
    # The API says whether another page exists; len(awards) == limit would
    # also be true when exactly limit awards exist.
    more_available = bool((data.get("page_metadata") or {}).get("hasNext"))

    for award in awards:
        internal_id = award.get("generated_internal_id")
        if internal_id:
            award["source_url"] = f"https://www.usaspending.gov/award/{internal_id}"

    return {
        "count": len(awards),
        "more_available": more_available,
        "awards": awards,
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        usaspending_search_awards,
        name="usaspending_search_awards",
        annotations={
            "title": "Search USAspending awards",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
