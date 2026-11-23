"""fac_search_audits: search FAC's audit records (the general table)."""

import re

from fastmcp.exceptions import ToolError

from dat_mcp import fac, identifiers

# Columns to return from FAC's general table, one row per audit.
FIELDS = (
    "report_id,audit_year,auditee_name,auditee_uei,auditee_ein,auditee_city,"
    "auditee_state,entity_type,fy_end_date,fac_accepted_date,total_amount_expended"
)

CAVEATS = [
    "audit_year is the year the entity's own fiscal year ends, not the federal "
    "fiscal year.",
    "No FAC record is not the same as no audit required.",
    "FAC doesn't validate UEIs or EINs, so one organization can appear under "
    "more than one of each.",
    "auditee_state is unreliable: some filings list the audit firm's state. If "
    "a search with state finds nothing, search again by UEI or EIN, or without "
    "state.",
]


def fac_search_audits(
    report_id: str | list[str] | None = None,
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
    audit_year: int | None = None,
    limit: int = 25,
):
    """Search Federal Audit Clearinghouse audit records by report ID, UEI, EIN, or name.

    Use this to find an organization's Single Audits and when FAC accepted
    them, or to look up who filed a report ID that another tool returned. Give
    at least one of report_id, uei, ein, or name; state and audit_year narrow
    the search. report_id, uei, and ein each take one value or a list of up to
    20, so look up several identifiers in one call rather than one call each;
    raise limit to fit them. Returns {"count", "audits", "caveats"}, with one
    audit per row: report ID, audit year, auditee name, UEI, EIN, location,
    entity type, fiscal-year end, accepted date, total federal spending, and a
    source_url to cite.

    Args:
        report_id: FAC report ID, e.g. 2024-06-GSAFAC-0000123456, or a list.
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333 or 11-2223333, or a list of them.
        name: Words from the auditee's name, in order; case and punctuation
            are ignored.
        state: 2-letter state code, e.g. CA.
        audit_year: Year the entity's fiscal year ends, e.g. 2025.
        limit: Most audits to return across all identifiers, 1 to 100. Newest
            audit years come first.
    """
    if not (report_id or uei or ein or name):
        raise ToolError("Give at least one of report_id, uei, ein, or name.")
    # Names go into a pattern match, so keep FAC's filter characters out.
    if name and not re.fullmatch(r"[A-Za-z0-9 &.,'-]{3,80}", name):
        raise ToolError("name must be 3-80 letters, digits, spaces, or &.,'-")
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100.")

    params = {"select": FIELDS, "order": "audit_year.desc", "limit": limit}
    if report_id:
        params["report_id"] = fac.one_of(
            identifiers.many(report_id, fac.report_id, "report_id")
        )
    if uei:
        ueis = identifiers.many(uei, identifiers.uei, "uei")
        params["auditee_uei"] = fac.one_of(ueis)
    if ein:
        eins = identifiers.many(ein, identifiers.ein, "ein")
        params["auditee_ein"] = fac.one_of(eins)
    if name:
        params["auditee_name"] = fac.contains(name)
    if state:
        params["auditee_state"] = fac.equals(identifiers.state(state))
    if audit_year:
        params["audit_year"] = fac.equals(identifiers.audit_year(audit_year))

    audits = fac.get("general", params)
    for audit in audits:
        audit["source_url"] = fac.summary_url(audit["report_id"])
    caveats = list(CAVEATS)
    if len(audits) >= limit:
        caveats.append(
            f"more_available is true: the search stopped at limit={limit}, so "
            "older audits, or some identifiers' audits, are missing. Raise limit "
            "(up to 100) or narrow the search before saying a year is missing."
        )
    return {
        "count": len(audits),
        "more_available": len(audits) >= limit,
        "audits": audits,
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        fac_search_audits,
        name="fac_search_audits",
        annotations={
            "title": "Search FAC audits",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
