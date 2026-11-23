"""fac_get_awards: one audit's federal awards (its SEFA), by program."""

import re

from fastmcp.exceptions import ToolError

from dat_mcp import fac

FIELDS = (
    "award_reference,federal_agency_prefix,federal_award_extension,"
    "federal_program_name,additional_award_identification,amount_expended,"
    "is_direct,is_major,cluster_name"
)

CAVEATS = [
    "Amounts are what the auditee reports spending in its own fiscal year, not "
    "federal obligations or payments.",
    "Pass-through awards (is_direct N) came through another organization, "
    "named in passthrough.",
]


def fac_get_awards(report_id: str, program: str | None = None, limit: int = 200):
    """List the federal awards in one FAC audit's Schedule of Expenditures (SEFA).

    Use this to see which federal programs an organization spent money on in
    that audit year, how much, and whether each award came directly from an
    agency or through another organization. Returns {"report_id", "count",
    "more_available", "awards", "source_url", "caveats"}. Each award has its
    assistance listing number (aln), program name, amount expended, direct or
    pass-through, major-program flag, cluster, and passthrough sources.

    Args:
        report_id: FAC report ID, e.g. 2024-06-GSAFAC-0000123456.
        program: Optional assistance listing number to filter on: an agency
            prefix like 93 (HHS) or a full number like 93.913.
        limit: Most awards to return, 1 to 1000.
    """
    report_id = fac.report_id(report_id)
    if program and not re.fullmatch(r"\d{2}(\.[0-9A-Za-z]{2,6})?", program):
        raise ToolError("program must look like 93 or 93.913.")
    if not 1 <= limit <= 1000:
        raise ToolError("limit must be between 1 and 1000.")

    params = {
        "select": FIELDS,
        "report_id": fac.equals(report_id),
        "order": "amount_expended.desc",
        "limit": limit,
    }
    if program:
        prefix, _, extension = program.partition(".")
        params["federal_agency_prefix"] = fac.equals(prefix)
        if extension:
            params["federal_award_extension"] = fac.equals(extension.upper())
    awards = fac.get("federal_awards", params)

    # Pass-through sources are a separate table, joined on award_reference.
    sources = {}
    if any(a["is_direct"] == "N" for a in awards):
        rows = fac.get(
            "passthrough",
            {"select": "award_reference,passthrough_name,passthrough_id",
             "report_id": fac.equals(report_id)},
        )  # fmt: skip
        for row in rows:
            sources.setdefault(row["award_reference"], []).append(
                {"name": row["passthrough_name"], "id": row["passthrough_id"]}
            )

    for award in awards:
        prefix = award.pop("federal_agency_prefix")
        award["aln"] = f"{prefix}.{award.pop('federal_award_extension')}"
        award["passthrough"] = sources.get(award["award_reference"], [])

    return {
        "report_id": report_id,
        "count": len(awards),
        "more_available": len(awards) == limit,
        "awards": awards,
        "source_url": fac.summary_url(report_id),
        "caveats": CAVEATS,
    }


def register(mcp):
    mcp.tool(
        fac_get_awards,
        name="fac_get_awards",
        annotations={
            "title": "Get an audit's federal awards",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
