"""sam_search_entities: find SAM.gov entities by name and state."""

import re

from fastmcp.exceptions import ToolError

from dat_mcp import identifiers, sam
from dat_mcp.tools.sam_get_registration import ID_ASSIGNED

# Kept short, as in sam_get_registration.
CAVEATS = [
    "Every word must match, and names differ across sources (abbreviations, "
    "doing-business-as names). If the entity isn't here, try fewer words or no "
    "state.",
    "SAM.gov's public data has no EINs and no parent or subsidiary links.",
]
# One of these leads, by whether anything matched.
CONFIRM = (
    "Free-text match: confirm a match by its address and the source that named "
    "it before using its UEI."
)
NO_MATCH = (
    "No match isn't proof there's no UEI: entities can opt out of public display."
)
# Only sent when it applies.
CUT_OFF = (
    "Results may be incomplete: duplicate SAM.gov records used up the requests "
    "before every match was read. Add name words or a state."
)


def sam_search_entities(name: str, state: str | None = None, limit: int = 10):
    """Search SAM.gov entities by name, optionally within one state.

    Use this to find the UEI of an organization known only by name, such as an
    affiliate named in an audit. Matches legal and doing-business-as names by
    words, registered entities first, then UEIs that were assigned but never
    registered. Returns {"count", "registrations", "caveats"}, with the same
    fields as sam_get_registration. If count equals limit there may be more;
    add name words or a state.

    Args:
        name: Words from the entity's name, e.g. Valley Community Clinic.
        state: 2-letter state code of the physical address, e.g. MO.
        limit: Most entities to return, 1 to 10.
    """
    if not re.fullmatch(r"[A-Za-z0-9 &.,'-]{3,80}", name):
        raise ToolError("name must be 3-80 letters, digits, spaces, or &.,'-")
    if not 1 <= limit <= sam.PAGE_SIZE:
        raise ToolError(f"limit must be between 1 and {sam.PAGE_SIZE}.")

    # q is a query language: uppercase AND/OR/NOT, a leading -, and && are
    # operators. Lowercase the words and drop & and - so a name is just words.
    words = re.sub(r"[&-]", " ", name).lower().split()
    if not words:
        raise ToolError("name must include letters or digits, e.g. Valley Clinic.")
    params = {
        "q": " ".join(words),
        "includeSections": "entityRegistration,coreData",
        "size": sam.PAGE_SIZE,
    }
    if state:
        params["physicalAddressProvinceOrStateCode"] = identifiers.state(state)

    # Duplicate records for one UEI are common and can fill a page, so page
    # like sam.registrations_by_uei until limit UEIs are found, keeping the
    # last record of each. Unregistered UEIs only come back with
    # samRegistered=No; both passes share MAX_PAGES requests.
    found = {}
    sent = 0
    cut_off = False
    for registered in ("Yes", "No"):
        page = 0
        while len(found) < limit and sent < sam.MAX_PAGES:
            response = sam.get({**params, "samRegistered": registered, "page": page})
            sent += 1
            page += 1
            rows = response.get("entityData") or []
            for row in rows:
                record = sam.registration(row)
                found[record["uei"]] = record
            if not rows or page * sam.PAGE_SIZE >= response.get("totalRecords", 0):
                break
        else:
            # No break: the requests ran out, or limit was reached (which the
            # count already shows), before this pass read every record.
            cut_off = len(found) < limit
    registrations = list(found.values())[:limit]
    caveats = [CONFIRM if registrations else NO_MATCH, *CAVEATS]
    if any(r.get("registration_status") == "ID Assigned" for r in registrations):
        caveats.insert(0, ID_ASSIGNED)
    if cut_off:
        caveats.insert(0, CUT_OFF)
    return {
        "count": len(registrations),
        "registrations": registrations,
        "caveats": caveats,
    }


def register(mcp):
    mcp.tool(
        sam_search_entities,
        name="sam_search_entities",
        annotations={
            "title": "Search SAM.gov entities by name",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
