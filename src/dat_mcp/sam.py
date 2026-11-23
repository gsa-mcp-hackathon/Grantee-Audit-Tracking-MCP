"""SAM.gov Entity API client (api.sam.gov), ported from DAT's src/sam.py."""

import logging

import httpx
from fastmcp.exceptions import ToolError

from dat_mcp import net
from dat_mcp.config import settings

# httpx logs every request URL at INFO, and SAM's URLs hold the API key.
logging.getLogger("httpx").setLevel(logging.WARNING)

ENTITY_API = "https://api.sam.gov/entity-information/v4/entities"
# Use SAM's public search entry point; /entity/{uei}/coreData links return 404.
ENTITY_SEARCH_URL = "https://sam.gov/entity-information?v=1"

# SAM returns at most 10 records per page, whatever size asks for. ueiSAM takes
# up to 100 UEIs, so tools look up at most PAGE_SIZE at a time.
PAGE_SIZE = 10
# SAM can return the same UEI several times (DAT saw up to 3 copies), which
# pushes other requested UEIs onto later pages. 4 pages covers a batch of 10
# duplicated 4 times; past that, stop rather than page forever. A UEI lookup
# can send one more request after these, for unregistered UEIs.
MAX_PAGES = 4


def get(params):
    """One page of entity records: {"totalRecords", "entityData": [...]}.

    Filter values must use SAM's syntax; ueis() below builds the UEI one.
    """
    if not settings.sam_api_key:
        raise ToolError("SAM_API_KEY isn't set; add it to .env (see docs/setup.md).")
    try:
        response = httpx.get(
            ENTITY_API,
            params={**params, "api_key": settings.sam_api_key},
            timeout=30,
            follow_redirects=False,
            trust_env=False,  # ignore proxy settings from the environment
            verify=net.SSL_CONTEXT,
        )
    except httpx.HTTPError:
        # The exception text includes the URL, which holds the API key.
        raise ToolError("Could not reach the SAM.gov API.") from None
    # Report the status only; upstream error bodies aren't passed through.
    if response.status_code in (401, 403):
        raise ToolError(
            f"SAM.gov API returned HTTP {response.status_code}; check that "
            "SAM_API_KEY is set and hasn't expired (keys last about 90 days)."
        )
    if response.status_code != 200:
        raise ToolError(f"SAM.gov API returned HTTP {response.status_code}.")
    return response.json()


def ueis(values):
    """SAM filter for several UEIs: [UEI1~UEI2]. Bracketed commas mean a date range."""
    return "[" + "~".join(values) + "]"


def registrations_by_uei(values):
    """Records for up to PAGE_SIZE UEIs: (found, not_found, complete).

    Keeps DAT's paging fix: duplicate records can push a requested UEI off the
    first page, where it would look unregistered, so keep paging until every
    UEI is seen or the pages run out. Duplicates are deduped by UEI, last one
    wins (DAT found no conflicting values across 299 duplicated UEIs).
    complete is False when the pages ran out with records left unread, so a
    UEI in not_found may still be registered.
    """
    params = {
        "ueiSAM": ueis(values),
        "includeSections": "entityRegistration,coreData",
        "size": PAGE_SIZE,
    }
    wanted = set(values)
    seen = {}
    complete = True
    for page in range(MAX_PAGES):
        if not wanted - set(seen):
            break
        response = get({**params, "page": page})
        rows = response.get("entityData") or []
        if not rows:
            break
        for row in rows:
            record = registration(row)
            if record["uei"] in wanted:
                seen[record["uei"]] = record
        # A short last page means a missing UEI won't turn up on the next one.
        if (page + 1) * PAGE_SIZE >= response.get("totalRecords", 0):
            break
    else:
        # No break: every page was full and more records were left.
        complete = False
    # A UEI SAM.gov assigned but the entity never registered ("ID Assigned")
    # only comes back with samRegistered=No, so ask once more for the rest.
    missing = [u for u in values if u not in seen]
    if missing:
        response = get({**params, "ueiSAM": ueis(missing), "samRegistered": "No"})
        for row in response.get("entityData") or []:
            record = registration(row)
            if record["uei"] in wanted:
                seen[record["uei"]] = record
        # Only one page is read here, so records past it leave the rest unsettled.
        if response.get("totalRecords", 0) > PAGE_SIZE:
            complete = False
    found = [seen[u] for u in values if u in seen]
    return found, [u for u in values if u not in seen], complete


def registration(row):
    """Flatten one entityData row into the fields our tools return.

    Empty fields are left out, since every field is carried in the agent's
    context for the rest of the conversation. Add a field back when an eval
    needs it.
    """
    reg = row.get("entityRegistration") or {}
    core = row.get("coreData") or {}
    info = core.get("entityInformation") or {}
    general = core.get("generalInformation") or {}
    record = {
        "uei": reg.get("ueiSAM"),
        "legal_business_name": reg.get("legalBusinessName"),
        "dba_name": reg.get("dbaName"),
        "division_name": info.get("entityDivisionName"),
        "registration_status": reg.get("registrationStatus"),
        "registration_expiration_date": reg.get("registrationExpirationDate"),
        "uei_status": reg.get("ueiStatus"),
        "entity_structure": general.get("entityStructureDesc"),
        "profit_structure": general.get("profitStructureDesc"),
        "fiscal_year_end": info.get("fiscalYearEndCloseDate"),
        "physical_address": _address(core.get("physicalAddress")),
        "source_url": reg.get("ueiSAM") and ENTITY_SEARCH_URL,
    }
    # uei is kept even when empty: callers key records by it.
    return {k: v for k, v in record.items() if v or k == "uei"}


def _address(address):
    address = address or {}
    fields = {
        "line1": address.get("addressLine1"),
        "line2": address.get("addressLine2"),
        "city": address.get("city"),
        "state": address.get("stateOrProvinceCode"),
        "zip": address.get("zipCode"),
        "country": address.get("countryCode"),
    }
    return {k: v for k, v in fields.items() if v}
