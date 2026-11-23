"""fac_search_covered_entities: UEIs and EINs that audits list as covered."""

from fastmcp.exceptions import ToolError

from dat_mcp import fac, identifiers

FIELDS = "report_id,audit_year,auditee_uei,fac_accepted_date"
MAX_ROWS = 1000  # most rows asked of FAC in one request

TRUNCATED = (
    "more_available is true: a list was cut off at {limit} rows, so some reports'"
    " covered identifiers are incomplete. Raise limit, or query fewer report ids"
    " at a time, before counting what a report lists."
)

CAVEATS = [
    "A listing means the parent's audit says it covers that UEI or EIN. FAC "
    "doesn't validate listed identifiers, so a typo can break the link.",
]

# Counts in these caveats are from FAC audit years 2022-2025 as of November 2026.
NOT_LISTED = (
    "No listing doesn't mean the entity isn't covered: 67% of audits filed as "
    "'... and subsidiaries' or '... and affiliates' list no covered UEIs or "
    "EINs. Search the report text (fac_search_audit_text) for the entity's "
    "name."
)

ONE_IDENTIFIER = (
    "Only the {given} was searched. A parent may list an entity by its {other} "
    "alone, so search again with both when you know both."
)

FOLLOW_PARENT = (
    "Of 37 entities dropped from a parent's list, 33 were still in its next "
    "audit report and 3 had been sold or deconsolidated. Before saying a year "
    "is uncovered, check the "
    "listing auditee's audits for that year with "
    "fac_search_audits(uei=listed_by_auditee_uei) and search their report text "
    "for the entity's name."
)


def fac_search_covered_entities(
    report_id: str | list[str] | None = None,
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    audit_year: int | None = None,
    limit: int = 100,
):
    """Search the UEIs and EINs that FAC audits list as covered by the audit.

    Works both ways. Give uei, ein, or both to find parent audits that list
    that entity as covered; give both when you know both, because a parent
    may list an entity by only one of them. Give report_id to see every UEI
    and EIN that one audit lists. Each of the three takes one value or a list
    of up to 20, so check several subsidiaries' EINs, or several reports, in
    one call. Returns {"count", "more_available", "reports", "caveats"}: count
    is the number of covered identifiers found, and each report has its
    report_id, audit_year, the listed_by_auditee_uei whose audit it is, a
    source_url to cite, and the covered_ueis and covered_eins it lists. If
    more_available is true a list was cut off; do not count a report's
    identifiers until it is false. Look up the auditee with
    fac_search_audits(report_id=...).

    Args:
        report_id: FAC report ID, e.g. 2024-06-GSAFAC-0000123456, or a list.
        uei: Covered entity's 12-character UEI, or a list of them.
        ein: Covered entity's 9-digit EIN, e.g. 112223333, or a list of them.
        audit_year: Year the parent's fiscal year ends, e.g. 2024.
        limit: Most listings to read per report id from each of the UEI and
            EIN lists, 1 to 500. Large parents list up to a few hundred.
    """
    if not (report_id or uei or ein):
        raise ToolError("Give at least one of report_id, uei, or ein.")
    if not 1 <= limit <= 500:
        raise ToolError("limit must be between 1 and 500.")

    # FAC keeps covered UEIs and covered EINs in two separate tables. One
    # request serves every report id given, so the limit is per report id.
    report_ids = (
        identifiers.many(report_id, fac.report_id, "report_id") if report_id else []
    )
    request_limit = min(limit * max(len(report_ids), 1), MAX_ROWS)
    params = {"order": "audit_year.desc", "limit": request_limit}
    if report_ids:
        params["report_id"] = fac.one_of(report_ids)
    if audit_year:
        params["audit_year"] = fac.equals(identifiers.audit_year(audit_year))

    reports = {}
    count = 0
    truncated = False
    # report_id alone lists everything the report covers. With a uei or ein
    # too, search only for that identifier within the report: an unfiltered
    # list of the other kind reads as a match for the identifier given.
    whole_report = bool(report_id) and not (uei or ein)
    if uei or whole_report:
        uei_params = {**params, "select": FIELDS + ",additional_uei"}
        if uei:
            ueis = identifiers.many(uei, identifiers.uei, "uei")
            uei_params["additional_uei"] = fac.one_of(ueis)
        rows = fac.get("additional_ueis", uei_params)
        truncated = truncated or len(rows) >= request_limit
        for row in rows:
            _report(reports, row)["covered_ueis"].append(row["additional_uei"])
            count += 1
    if ein or whole_report:
        ein_params = {**params, "select": FIELDS + ",additional_ein"}
        if ein:
            eins = identifiers.many(ein, identifiers.ein, "ein")
            ein_params["additional_ein"] = fac.one_of(eins)
        rows = fac.get("additional_eins", ein_params)
        truncated = truncated or len(rows) >= request_limit
        for row in rows:
            _report(reports, row)["covered_eins"].append(row["additional_ein"])
            count += 1

    caveats = list(CAVEATS)
    if truncated:
        caveats.append(TRUNCATED.format(limit=request_limit))
    if not report_id and count == 0:
        caveats.append(NOT_LISTED)
    if not report_id and count:
        caveats.append(FOLLOW_PARENT)
    if not report_id and bool(uei) != bool(ein):
        given, other = ("UEI", "EIN") if uei else ("EIN", "UEI")
        caveats.append(ONE_IDENTIFIER.format(given=given, other=other))

    return {
        "count": count,
        "more_available": truncated,
        "reports": list(reports.values()),
        "caveats": caveats,
    }


def _report(reports, row):
    """Return the entry for this row's report, creating it on first sight."""
    return reports.setdefault(
        row["report_id"],
        {
            "report_id": row["report_id"],
            "audit_year": row["audit_year"],
            "listed_by_auditee_uei": row["auditee_uei"],
            "fac_accepted_date": row.get("fac_accepted_date"),
            "source_url": fac.summary_url(row["report_id"]),
            "covered_ueis": [],
            "covered_eins": [],
        },
    )


def register(mcp):
    mcp.tool(
        fac_search_covered_entities,
        name="fac_search_covered_entities",
        annotations={
            "title": "Search entities covered by FAC audits",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
