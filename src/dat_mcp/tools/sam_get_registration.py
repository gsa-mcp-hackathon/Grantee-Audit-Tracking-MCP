"""sam_get_registration: SAM.gov records for specific UEIs."""

from fastmcp.exceptions import ToolError

from dat_mcp import identifiers, sam

# Kept short: caveats are carried in the agent's context for the rest of the
# conversation. Add wording when an eval shows it's needed.
CAVEATS = [
    "Names, address, structure, and fiscal_year_end (month/day, no year) are "
    "self-reported and can differ from FAC and IRS.",
    "SAM.gov's public data has no EINs and no parent or subsidiary links.",
]
# Only sent when they apply.
NOT_FOUND = (
    "not_found: no public SAM.gov record, registered or not. Of 1,904 such "
    "UEIs in FAC filings, 41% were typos and 22% real UEIs not shown "
    "publicly; all 594 one character from a UEI SAM.gov has were typos of it, "
    "so look up a suspect and its neighbor together."
)
CUT_OFF = (
    "not_found may be incomplete: duplicate SAM.gov records filled every page "
    "before all were read. Look these UEIs up again on their own."
)
ID_ASSIGNED = (
    "registration_status ID Assigned: a real UEI whose entity never "
    "registered, so most fields are empty."
)


def sam_get_registration(ueis: list[str]):
    """Look up SAM.gov records for UEIs, registered or not.

    Use this to tell a mistyped UEI from a real one (look up both), or for an
    entity's registered names, address, and fiscal-year end. Returns
    {"count", "registrations", "not_found", "caveats"}; not_found lists UEIs
    SAM.gov has no public record of.

    Args:
        ueis: 1 to 10 12-character UEIs, e.g. ["A1B2C3D4E5F6"].
    """
    if not 1 <= len(ueis) <= sam.PAGE_SIZE:
        raise ToolError(f"Give 1 to {sam.PAGE_SIZE} UEIs, e.g. ['A1B2C3D4E5F6'].")
    values = list(dict.fromkeys(identifiers.uei(u) for u in ueis))

    registrations, not_found, complete = sam.registrations_by_uei(values)
    caveats = list(CAVEATS)
    if any(r.get("registration_status") == "ID Assigned" for r in registrations):
        caveats.insert(0, ID_ASSIGNED)
    if not_found:
        caveats.insert(0, NOT_FOUND if complete else CUT_OFF)
    return {
        "count": len(registrations),
        "registrations": registrations,
        "not_found": not_found,
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        sam_get_registration,
        name="sam_get_registration",
        annotations={
            "title": "Get SAM.gov registrations by UEI",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
