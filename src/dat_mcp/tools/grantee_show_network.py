"""grantee_show_network: the organizations around one grantee, as an MCP App."""

from pathlib import Path

from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult

from dat_mcp import identifiers
from dat_mcp.tools.fac_find_linked_identifiers import _real
from dat_mcp.tools.fac_search_covered_entities import fac_search_covered_entities
from dat_mcp.tools.grantee_identity_resolution import grantee_identity_resolution
from dat_mcp.tools.irs_get_related_orgs import irs_get_related_orgs
from dat_mcp.tools.irs_search_exempt_orgs import irs_search_exempt_orgs

UI_URI = "ui://dat/grantee-network/v1.html"
UI_PATH = Path(__file__).resolve().parents[1] / "ui" / "grantee_network.html"

# Where an organization sits relative to the grantee, by what the sources say.
# When sources put one organization in two tiers, the earlier one here wins.
BELOW = "controlled or covered by this entity"
ABOVE = "lists this entity"
RELATED = "related, control not stated"
POSSIBLE = "possible match"
OTHER = "other organization under its identifiers"
TIERS = [BELOW, ABOVE, RELATED, POSSIBLE, OTHER]

FAC = "FAC covered-entity listing"
SCHEDULE_R = "IRS Form 990 Schedule R"
IDENTITY = "grantee_identity_resolution"

MAX_PER_TIER = 40  # organizations returned per tier; the rest are counted

CAVEATS = [
    "This assembles FAC covered-entity listings and Form 990 Schedule R "
    "listings for an analyst. A tier says who lists whom; it decides nothing "
    "about whether one organization's audit covers another.",
    "A parent can cover an entity without listing it, and an organization "
    "with no listing here may still be related: Schedule R is read only from "
    "the returns stored locally, and only tax-exempt filers have one.",
    "The identifiers grouped as this entity are linked by a shared UEI or "
    "EIN, a one-character typo, or the same name at the same address. That is "
    "evidence of the same organization, not proof. Possible matches are kept "
    "apart.",
]


def grantee_show_network(
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
) -> ToolResult:
    """Show the organizations around one grantee as an interactive network.

    Use this when the user asks to see a grantee's parents, subsidiaries, or
    related organizations, or to answer who lists it and whom it lists. It
    resolves the grantee (as grantee_identity_resolution does), then gathers
    the audits that list it as covered, the identifiers its own latest audit
    lists, and the Form 990 Schedule R listings in both directions, and merges
    an organization found in more than one source by EIN or UEI.

    Returns {"searched", "entity", "organizations", "counts", "notes",
    "caveats"}. entity is the grantee, with every UEI and EIN grouped as it
    and what links each. Each organization has name, ueis, eins, a tier, and
    evidence: one entry per source with what it says, the years, and a
    source_url. The tiers are "lists this entity" (a parent's audit or
    another filer's Schedule R), "controlled or covered by this entity" (its
    audit lists it, or its Schedule R reports it controlled), "related,
    control not stated", "possible match" (an unconfirmed lead), and "other
    organization under its identifiers". counts gives each tier's total when
    more were found than returned. When a name matches more than one
    organization it returns {"candidates"} instead; call again with one of
    their identifiers.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333, or a list of them.
        name: Part of the auditee's name, used when no identifier is known.
        state: 2-letter state code to narrow a name search, e.g. CA.
    """
    identity = grantee_identity_resolution(uei=uei, ein=ein, name=name, state=state)
    if "candidates" in identity:
        return ToolResult(
            content="The name matched several organizations; see candidates.",
            structured_content=identity,
        )
    network = build(identity)
    return ToolResult(content=_summary(network), structured_content=network)


class _Organizations:
    """Organizations by EIN or UEI, so one found in two sources is one entry."""

    def __init__(self):
        self.found = []
        self.index = {}

    def add(self, tier, evidence, name=None, eins=(), ueis=(), **details):
        eins, ueis = sorted(_real(set(eins))), sorted(_real(set(ueis)))
        keys = [f"ein:{e}" for e in eins] + [f"uei:{u}" for u in ueis]
        keys = keys or [f"name:{name}"]
        entry = next((self.index[k] for k in keys if k in self.index), None)
        if entry is None:
            entry = {"name": None, "ueis": [], "eins": [], "tier": tier, "evidence": []}
            self.found.append(entry)
        for key in keys:
            self.index[key] = entry
        entry["name"] = entry["name"] or name
        entry["eins"] = sorted({*entry["eins"], *eins})
        entry["ueis"] = sorted({*entry["ueis"], *ueis})
        entry["tier"] = min(entry["tier"], tier, key=TIERS.index)
        entry["evidence"].append(evidence)
        for key, value in details.items():
            if value not in (None, ""):
                entry.setdefault(key, value)
        return entry


def build(identity):
    """The network around the grantee grantee_identity_resolution resolved."""
    entries = identity["identifiers"]
    own_ueis = _real({e["uei"] for e in entries} | set(identity["searched"]["ueis"]))
    own_eins = _real({e["ein"] for e in entries} | set(identity["searched"]["eins"]))
    registrations = identity["sam_registrations"].get("registrations", [])
    irs_records = identity["irs_records"].get("records", [])
    orgs = _Organizations()
    notes = []

    for parent in identity["listed_in_parent_audits"]:
        orgs.add(
            ABOVE,
            {
                "source": FAC,
                "says": "its audit lists this entity as covered, by "
                f"{parent['listed_by']}",
                "years": parent["audit_years"],
                "source_url": parent["source_url"],
            },
            name=parent["listed_by_name"],
            eins=[parent["listed_by_ein"]],
            ueis=parent["listed_by_ueis"],
        )
    _add_schedule_r(orgs, sorted(own_eins), notes)
    _add_covered(orgs, identity, own_ueis, own_eins, notes)
    for lead in identity["unconfirmed_leads"]:
        orgs.add(
            POSSIBLE,
            {"source": IDENTITY, "says": "; ".join(lead.get("links", []))},
            name=(lead.get("names") or [None])[0],
            eins=[lead.get("ein")],
            ueis=[lead.get("uei")],
        )
    for other in identity["other_organizations"]:
        orgs.add(
            OTHER,
            {"source": IDENTITY, "says": other.get("why")},
            name=(other.get("names") or [None])[0],
            eins=other.get("eins", []),
            ueis=other.get("ueis", []),
        )
    _name_by_ein(orgs.found)

    # Most evidence first, so what two sources agree on isn't the part cut off.
    found = sorted(
        orgs.found, key=lambda o: (-len({e["source"] for e in o["evidence"]}),)
    )
    organizations, counts = [], {}
    for tier in TIERS:
        in_tier = [o for o in found if o["tier"] == tier]
        if in_tier:
            counts[tier] = {
                "total": len(in_tier),
                "shown": min(len(in_tier), MAX_PER_TIER),
            }
        organizations += in_tier[:MAX_PER_TIER]
    if not organizations:
        notes.append(
            "No organization lists this entity or is listed by it in the FAC "
            "listings and stored Schedule R returns searched. That is not "
            "evidence it stands alone."
        )
    reg = next((r for r in registrations if r["uei"] in own_ueis), None)
    names = [n for e in entries for n in e["names"]]
    return {
        "searched": identity["searched"],
        "entity": {
            "name": (reg or {}).get("legal_business_name")
            or (names or [None])[0]
            or (irs_records or [{}])[0].get("name"),
            "identifiers": [
                {k: e[k] for k in ("uei", "ein", "names", "links")} for e in entries
            ],
            "found_in": [
                source
                for source, found_there in (
                    ("FAC", any(e.get("audit_years") for e in entries)),
                    ("SAM.gov", bool(registrations)),
                    ("IRS", bool(irs_records)),
                )
                if found_there
            ],
            "city": (reg or {}).get("city"),
            "state": (reg or {}).get("state"),
            "source_urls": [
                r["source_url"]
                for r in [*registrations, *irs_records]
                if r.get("source_url")
            ],
        },
        "organizations": organizations,
        "counts": counts,
        "notes": notes,
        "caveats": [*CAVEATS, *identity["caveats"][-2:]],
    }


def _add_schedule_r(orgs, eins, notes):
    """Schedule R both ways: the filers that list the grantee, and the
    organizations the grantee's own returns list."""
    if not eins:
        notes.append("No EIN is known for this entity, so Schedule R wasn't read.")
        return
    try:
        related = irs_get_related_orgs(ein=eins[: identifiers.MAX_BATCH], limit=500)
    except ToolError as error:
        notes.append(f"Schedule R wasn't read: {error}")
        return
    if related["not_stored"]:
        notes.append(
            f"No Form 990 is stored locally for {', '.join(related['not_stored'])}, "
            "so the organizations it reports as related aren't shown."
        )
    for listed in related["listed_by"]:
        if listed["filer_ein"] in eins:
            # Two EINs grouped as this entity, one reporting the other as
            # related: say so rather than draw the entity as its own parent.
            notes.append(
                f"EIN {listed['filer_ein']} and EIN {listed['ein']} are both "
                f"grouped as this entity, and {listed['filer_ein']}'s Schedule R "
                f"reports {listed['ein']} as a {listed['relationship']}: they "
                "may be separate organizations."
            )
            continue
        evidence = {
            "source": SCHEDULE_R,
            "says": f"its Schedule R reports this entity as a {listed['relationship']}",
            "years": listed["tax_years"],
            "source_url": listed["source_url"],
        }
        if "ein_as_listed" in listed:
            evidence["says"] += (
                f", under EIN {listed['ein_as_listed']} "
                f"({listed['name_as_listed']}), one digit from {listed['ein']}"
            )
        evidence |= _latest_control(listed.get("control", []))
        orgs.add(
            ABOVE, evidence, name=listed["filer_name"], eins=[listed["filer_ein"]]
        )
    for filer in related["filers"]:
        for group in filer["related_organizations"]:
            for org in group["organizations"]:
                if org["ein"] in eins:
                    continue
                controlled = org.get("controlled")
                evidence = {
                    "source": SCHEDULE_R,
                    "says": "this entity's Schedule R reports it as a "
                    f"{group['relationship']}",
                    "years": org.get("tax_years_listed") or [filer["tax_year"]],
                    "source_url": filer["source_url"],
                }
                if controlled is not None:
                    evidence["controlled"] = controlled
                if org.get("direct_controlling_entity"):
                    evidence["direct_controlling_entity"] = org[
                        "direct_controlling_entity"
                    ]
                # A disregarded entity (Part I) is its filer's by definition.
                below = controlled or group["part"] == "I"
                orgs.add(
                    BELOW if below else RELATED,
                    evidence,
                    name=org["name"],
                    eins=[org["ein"]],
                    state=org.get("state"),
                    primary_activity=org.get("primary_activity"),
                )
        if filer["more_available"]:
            notes.append(
                f"{filer['name']} reports {filer['related_count']} related "
                "organizations; not all were read."
            )


def _latest_control(control):
    """What the filer last reported about controlling the grantee: whether it
    marked it controlled, and the direct controlling entity it named."""
    latest = {}
    for key in ("controlled", "direct_controlling_entity"):
        reported = [g for g in control if key in g]
        if reported:
            latest[key] = max(reported, key=lambda g: max(g["tax_years"]))[key]
    return latest


def _add_covered(orgs, identity, own_ueis, own_eins, notes):
    """The UEIs and EINs the grantee's latest FAC audit lists as covered."""
    latest = (identity["entity"].get("fiscal_year_end") or {}).get("fac") or {}
    if not latest.get("report_id"):
        return
    try:
        covered = fac_search_covered_entities(report_id=latest["report_id"])
    except ToolError as error:
        notes.append(f"The entities its latest audit lists weren't read: {error}")
        return
    nameless = 0
    for report in covered["reports"]:
        evidence = {
            "source": FAC,
            "says": "this entity's latest audit lists it as covered",
            "years": [report["audit_year"]],
            "source_url": report["source_url"],
        }
        for covered_ein in _real(set(report["covered_eins"])) - own_eins:
            orgs.add(BELOW, dict(evidence), eins=[covered_ein])
        for covered_uei in _real(set(report["covered_ueis"])) - own_ueis:
            nameless += 1
            orgs.add(BELOW, dict(evidence), ueis=[covered_uei])
    if covered["more_available"]:
        notes.append("Its latest audit lists more covered entities than were read.")
    if nameless:
        notes.append(
            f"{nameless} entities its latest audit lists by UEI are shown by UEI "
            "alone: FAC gives no name, and a UEI and an EIN it lists for one "
            "entity can't be paired. sam_get_registration names a UEI."
        )


def _name_by_ein(found):
    """IRS names for organizations FAC listed by EIN alone."""
    nameless = [o for o in found if not o["name"] and o["eins"]]
    wanted = [o["eins"][0] for o in nameless][: identifiers.MAX_BATCH]
    if not wanted:
        return
    try:
        records = irs_search_exempt_orgs(ein=wanted)["organizations"]
    except ToolError:
        return
    names = {r["ein"]: r["name"] for r in records}
    for org in nameless:
        if org["eins"][0] in names:
            org["name"] = names[org["eins"][0]]
            org["name_from"] = "IRS"


def _summary(network):
    """The network as text, for the model and for clients with no view."""
    entity = network["entity"]
    pairs = ", ".join(
        f"UEI {i['uei']} / EIN {i['ein']}" for i in entity["identifiers"]
    )
    lines = [f"{entity['name'] or 'This entity'}: {pairs or 'no filing found'}"]
    for tier in TIERS:
        in_tier = [o for o in network["organizations"] if o["tier"] == tier]
        if not in_tier:
            continue
        count = network["counts"][tier]
        more = f", {count['shown']} of {count['total']} shown" if count[
            "total"
        ] > count["shown"] else ""
        lines += ["", f"{tier.capitalize()} ({count['total']}{more}):"]
        for org in in_tier:
            ids = ", ".join(
                [f"EIN {e}" for e in org["eins"]] + [f"UEI {u}" for u in org["ueis"]]
            )
            says = "; ".join(
                f"{e['says']} [{e['source']}"
                + (f", {_years(e['years'])}" if e.get("years") else "")
                + (", reported as controlled" if e.get("controlled") else "")
                + "]"
                for e in org["evidence"]
            )
            lines.append(f"- {org['name'] or 'name not given'} ({ids}): {says}")
    lines += ["", *[f"Note: {n}" for n in network["notes"]]]
    lines += ["Caveats:", *[f"- {c}" for c in network["caveats"]]]
    return "\n".join(lines)


def _years(years):
    years = sorted({str(y) for y in years})
    return years[0] if len(years) == 1 else f"{years[0]}-{years[-1]}"


def register(mcp):
    @mcp.resource(
        UI_URI,
        name="grantee_network",
        title="Organizations around a grantee",
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "prefersBorder": True,
                # The view loads nothing and calls nothing over the network.
                "csp": {"connectDomains": [], "resourceDomains": []},
            }
        },
    )
    def grantee_network():
        return UI_PATH.read_text(encoding="utf-8")

    mcp.tool(
        grantee_show_network,
        name="grantee_show_network",
        annotations={
            "title": "Show the organizations around a grantee",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        meta={"ui": {"resourceUri": UI_URI}},
    )
