"""grantee_identity_resolution: which UEIs, EINs, and names are one grantee."""

import calendar
import copy
import datetime
import re
import time

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac, identifiers, names, sam
from dat_mcp.sourcetext import SOURCE_NOTICE
from dat_mcp.tools.fac_find_linked_identifiers import (
    SENTINEL,
    _note_mentions,
    _real,
    fac_find_linked_identifiers,
)
from dat_mcp.tools.fac_search_covered_entities import fac_search_covered_entities
from dat_mcp.tools.irs_get_related_orgs import irs_get_related_orgs
from dat_mcp.tools.irs_search_exempt_orgs import CAVEATS as IRS_CAVEATS
from dat_mcp.tools.irs_search_exempt_orgs import irs_search_exempt_orgs, same_name

CAVEATS = [
    "This assembles FAC, SAM.gov, and local copies of FAC and IRS data for an "
    "analyst; it decides nothing about whether an audit was required or late.",
    "identifiers are linked by a shared UEI or EIN, a one-character typo, or "
    "the same name at the same address. Each entry's links says which. That is "
    "evidence of the same organization, not proof.",
    "unconfirmed_leads and other_organizations are kept out of identifiers and "
    "out of grantee_audit_coverage's years; confirm a lead before using it.",
    "audit_years are from the local FAC copy and stop at its date. For what "
    "was filed, and anything filed since, use grantee_audit_coverage, which "
    "also asks FAC's live API.",
]

# How many filers a name-and-place search may link before it stops being
# evidence of one organization, and how many leads are listed.
MAX_BRIDGED = 6
MAX_LEADS = 5
MAX_AT_ADDRESS = 12  # more filers than this at one address is an office tower
IRS_NAME_LIMIT = 100  # IRS records read per name searched
STRAY_FILINGS = 2  # filings under someone else's identifier that count as stray
# The newest filings read for a search by name. One row per filing, so a
# common name's organizations can take several rows each ("Family Health
# Centers, Inc." matches 53).
NAME_FILINGS_READ = 100

# grantee_audit_coverage resolves the same grantee again, usually seconds
# after this tool did. Remember the linking and the SAM.gov lookups (which
# count against a daily limit) for a few minutes.
CACHE_SECONDS = 300
CACHE_ENTRIES = 32
_cache = {}


def _cached(key, make):
    key = (str(db.settings.dat_db_path), *key)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return copy.deepcopy(hit[1])
    value = make()
    _cache.pop(key, None)
    while len(_cache) >= CACHE_ENTRIES:
        del _cache[next(iter(_cache))]  # the oldest entry
    _cache[key] = (time.monotonic(), copy.deepcopy(value))
    return value


def _registrations(ueis):
    """sam.registrations_by_uei, remembered for CACHE_SECONDS."""
    ueis = tuple(ueis)
    return _cached(
        ("sam", ueis, id(sam.registrations_by_uei)),
        lambda: sam.registrations_by_uei(list(ueis)),
    )


def grantee_identity_resolution(
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
):
    """Resolve which UEIs, EINs, and names belong to one grantee, with the evidence.

    Start here for any question about who a grantee is. From a UEI, EIN, or
    name it finds every UEI and EIN the organization has filed Single Audits
    under: by a shared UEI or EIN, by a one-character typo, and by the same
    name at the same address when no identifier is shared (an EIN mistyped
    for years, a new legal entity, a parent that took over the filing). For a
    UEI with no EIN on file it looks for the EIN in IRS records by the
    SAM.gov name and city. It checks each UEI in SAM.gov and each EIN against
    the IRS's exempt-organization records, and puts every source's fiscal
    year end side by side. Use grantee_audit_coverage for what was filed each
    year; this tool returns no audit rows.

    Returns {"searched", "identifiers", "unconfirmed_leads",
    "other_organizations", "listed_in_parent_audits", "sam_registrations",
    "irs_records", "entity", "open_questions", "notes", "caveats"}. Each
    identifiers entry has uei, ein, names, audit_years, and links: the
    evidence that ties it to the searched identifier. unconfirmed_leads are
    possible matches that fall short: one-character neighbors that never
    filed, a matching name in the same state with no address in common, or a
    different name at the same address. other_organizations are filers that
    used one of this entity's identifiers on a filing or two but otherwise
    file under their own; their audits are not this entity's.
    listed_in_parent_audits names each audit filer that lists the entity as
    covered, with the years and whether by UEI or EIN. irs_records has each
    EIN's IRS record (name_matches_fac is false when a name FAC filings used
    doesn't match the IRS name), EINs the IRS has no record of, and
    listed_by: Schedule R filers that report the entity as related.
    entity.fiscal_year_end shows each source's year end, the one used, and
    whether they disagree. When a name matches more than one organization it
    returns {"candidates"} instead; call again with one of their
    identifiers.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333, or a list of them.
        name: Words from the auditee's name, used when no identifier is
            known; case and punctuation are ignored.
        state: 2-letter state code to narrow a name search, e.g. CA.
    """
    uei, ein, candidates = start(uei, ein, name, state)
    if candidates:
        return candidates
    linked, found = link(uei, ein)
    groups = [g for g in linked["identifiers"] if not g["lead"]]
    leads = [g for g in linked["identifiers"] if g["lead"]]
    filed = bool(groups) and not linked["leads_only"]
    if filed:
        ueis = sorted(_real({g["uei"] for g in groups}))
        eins = sorted(_real({g["ein"] for g in groups}))
    else:
        groups = []
        ueis, eins = linked["searched"]["ueis"], linked["searched"]["eins"]
    with db.connect() as connection:
        rows = own_rows(connection, ueis, eins, found) if filed else []
        snapshot_caveat = db.snapshot_caveat(connection)
    history = [_audit(row) for row in rows]
    covered_by, covered_truncated, covered_error = _covered_by(ueis, eins)
    registrations = _sam(with_asked(ueis, found))
    irs_records, irs_caveat = _irs(eins, _fac_names(rows))
    entity = entity_block(
        rows[-1]["entity_type"] if rows else None,
        registrations,
        history,
        covered_by,
        irs_records,
    )
    if filed:
        mentions, more_mentions = _sefa_mentions(irs_records)
    else:
        mentions, more_mentions = {}, []
    irs_notes, irs_questions = _irs_findings(
        irs_records,
        groups,
        history,
        covered_by,
        _usual_type(rows),
        mentions,
        more_mentions,
    )

    notes = link_notes(linked, found, filed) + tie_notes(found, covered_by)
    shared = unbridged(eins, found) if filed else []
    if len(shared) > 1:
        notes.append(shared_uei_note(shared, groups))
    notes += year_end_notes(entity["fiscal_year_end"])
    filed_ueis = {g["uei"] for g in groups}
    notes += registration_notes(registrations, filed_ueis)
    if covered_truncated:
        notes.append(
            "The parent listings were cut off at the search limit, so "
            "listed_in_parent_audits may leave some parents out."
        )
    if covered_error:
        notes.append(LISTINGS_NOT_ASKED.format(error=covered_error))
    notes += irs_notes
    questions = [
        *lead_questions(found),
        *identity_questions(registrations, entity, shared, filed_ueis),
        *irs_questions,
    ]
    if not filed and not covered_by:
        questions.insert(
            0,
            "Has this organization ever filed to FAC under another identifier, "
            "or is it covered by a parent's audit?",
        )

    result = {
        "searched": found["asked"],
        "identifiers": _with_mentions(
            [
                {k: g[k] for k in ("uei", "ein", "names", "links", "audit_years")}
                for g in groups
            ],
            mentions,
            more_mentions,
        )
        # An EIN found for an entity with no filing has no group of its own.
        + ([] if filed else [{**b, "audit_years": []} for b in found["bridged"]]),
        "unconfirmed_leads": [
            {k: g[k] for k in ("uei", "ein", "names", "links", "audit_years")}
            for g in leads
        ]
        + found["leads"],
        "other_organizations": found["other_organizations"],
        "listed_in_parent_audits": _parents(covered_by),
        "sam_registrations": registrations,
        "irs_records": irs_records,
        "entity": entity,
        "open_questions": questions,
        "notes": notes,
        "caveats": [*CAVEATS, snapshot_caveat, *irs_caveat],
    }
    if not filed and linked["named_in_sefa_notes"]:
        result["named_in_sefa_notes"] = linked["named_in_sefa_notes"]
        result["notes_more_available"] = linked["notes_more_available"]
    if mentions or result.get("named_in_sefa_notes"):
        result["source_notice"] = SOURCE_NOTICE
    return result


def start(uei, ein, name, state):
    """(uei, ein, None) to resolve, or (None, None, a candidates response)
    when only a name was given and it doesn't settle on one organization."""
    if not (uei or ein or name):
        raise ToolError("Give a uei, an ein, or a name.")
    if not name or uei or ein:
        return uei, ein, None
    found, cut_off = _candidates(name, state)
    if len(found) == 1 and not cut_off:
        return found[0]["uei"], found[0]["ein"], None
    if not found:
        caveat = (
            "No FAC filing matched that name. Try fewer words, no state "
            "(auditee_state is unreliable), or sam_search_entities to find the "
            "UEI first."
        )
    elif cut_off:
        # A state's departmental audits under one EIN can fill the page, with
        # other organizations of the name behind them.
        caveat = (
            f"More filings match the name than the {NAME_FILINGS_READ} newest "
            "read here, so other organizations may match too. Pick one and "
            "call again with its uei or ein, or give more of the name or a "
            "state; nothing is guessed."
        )
    else:
        caveat = (
            "The name matched several organizations. Pick one and call again "
            "with its uei or ein; nothing is guessed."
        )
    return (
        None,
        None,
        {
            "searched": {"name": name, "state": state},
            "candidates": found,
            "caveats": [caveat],
        },
    )


def link(uei, ein):
    """(linked, found): fac_find_linked_identifiers' answer for the searched
    identifiers plus those the name-and-place search confirms, and what that
    search found. found has asked (the identifiers as searched), bridged
    (confirmed, now in linked), leads, other_organizations, and foreign (the
    (uei, ein) pairs of other organizations' filings, to leave out)."""
    asked = fac_find_linked_identifiers(uei=uei, ein=ein)
    key = (
        "link",
        tuple(asked["searched"]["ueis"]),
        tuple(asked["searched"]["eins"]),
        id(sam.registrations_by_uei),
    )
    linked, found = _cached(key, lambda: _link(asked))
    if found.get("sam_skipped"):
        # Linked without SAM.gov's name and address: ask again next time.
        _cache.pop((str(db.settings.dat_db_path), *key), None)
    return linked, found


def _link(linked):
    searched = copy.deepcopy(linked["searched"])
    found = {
        "asked": searched,
        "bridged": [],
        "leads": [],
        "leads_not_shown": 0,
        "other_organizations": [],
        "foreign": [],
    }
    with db.connect() as connection:
        indexed = _has_table(connection, "fac_filers")
    if not indexed:
        found["skipped"] = (
            "The local database has no fac_filers table, so filings were linked "
            "by UEI and EIN only, not by name and address. Add it with: python "
            "scripts/build_db.py --derived"
        )
        return linked, found
    # Set other organizations' stray filings aside first, so their names and
    # addresses aren't searched as this entity's.
    found["other_organizations"], found["foreign"] = _other_organizations(linked)
    bridged, leads = _bridge(linked, found)
    found["leads"] = leads[:MAX_LEADS]
    found["leads_not_shown"] = len(leads) - len(found["leads"])
    if bridged:
        ueis = list(searched["ueis"])
        eins = list(searched["eins"])
        for entry in bridged:
            ueis += [u for u in _real({entry["uei"]}) if u not in ueis]
            eins += [e for e in _real({entry["ein"]}) if e not in eins]
        ueis, eins = ueis[: identifiers.MAX_BATCH], eins[: identifiers.MAX_BATCH]
        linked = fac_find_linked_identifiers(uei=ueis or None, ein=eins or None)
        for group in linked["identifiers"]:
            if group["uei"] in searched["ueis"] or group["ein"] in searched["eins"]:
                continue
            basis = [
                b["links"][0]
                for b in bridged
                if b["uei"] in _real({group["uei"]}) or b["ein"] == group["ein"]
            ]
            if basis:
                group["links"] = basis[:1]
        found["bridged"] = bridged
        found["other_organizations"], found["foreign"] = _other_organizations(linked)
    return linked, found


def own_rows(connection, ueis, eins, found):
    """general rows under the identifiers, without other organizations' stray
    filings (found["foreign"])."""
    foreign = {tuple(pair) for pair in found["foreign"]}
    return [
        row
        for row in _general_rows(connection, ueis, eins)
        if (row["auditee_uei"], row["auditee_ein"]) not in foreign
    ]


def _bridge(linked, found):
    """(confirmed, leads): FAC filers and IRS records that match the entity by
    name and place but share no identifier with it. Each is {"uei", "ein",
    "names", "links", "audit_years"}, like an identifiers entry.

    Confirmed takes two things that agree: the same name (word for word,
    order aside) and the same street address, city, or ZIP. A one-word name,
    or one that matches only up to where it is cut off, needs the same
    street address. Anything less is a lead: the same name elsewhere in the
    state, a similar name at the same address (a foundation, an alumni
    association), or a different name at the same address. IRS records
    aren't searched for a government body (found["irs_skipped"]). The
    measured cases are in docs/caveat-evidence.md, "Filers linked by name
    and place".
    """
    groups = [g for g in linked["identifiers"] if not g["lead"]]
    searched = linked["searched"]
    ueis = sorted(_real({g["uei"] for g in groups} | set(searched["ueis"])))
    eins = sorted(_real({g["ein"] for g in groups} | set(searched["eins"])))
    registrations = []
    if ueis:
        try:
            registrations = _registrations(ueis[: sam.PAGE_SIZE])[0]
        except ToolError as error:
            # The search goes on with FAC and IRS names; link_notes says so.
            found["sam_skipped"] = str(error)
    with db.connect() as connection:
        known = _known(connection, groups, eins, registrations)
        if not known["names"]:
            return [], []
        matches = _fac_matches(connection, known, ueis, eins)
        if _government(connection, groups, registrations):
            # The IRS file lists about 5% of government EINs, so a record
            # under a county's name is far more often a union or Farm Bureau
            # chapter than the county (352 such matches across FAC).
            found["irs_skipped"] = (
                "this is a government body, and the IRS's exempt-organization "
                "file lists few governments, so a record there under its name "
                "is more likely a chapter or association named for the place"
            )
        else:
            matches += _irs_matches(connection, known, eins)
        confirmed, leads = {}, {}
        for match in matches:
            pile = confirmed if match.pop("confirmed") else leads
            key = (match["uei"] if match["uei"] != SENTINEL else None, match["ein"])
            if key in pile:
                pile[key]["links"] += match["links"]
            else:
                pile[key] = match
        # An EIN the IRS search confirmed and a FAC filer under it are one.
        for key in [k for k in confirmed if k[0] is None]:
            twins = [k for k in confirmed if k[0] and k[1] == key[1]]
            if twins:
                confirmed[twins[0]]["links"] += confirmed.pop(key)["links"]
        # A filer whose audit lists the searched identifier as covered is
        # its parent (a county and the county office registered under its own
        # UEI): the listing is in the parent audits, and its audits aren't
        # this entity's own.
        for key in [k for k in confirmed if _lists(connection, k, searched)]:
            del confirmed[key]
        if len(confirmed) > MAX_BRIDGED:
            # Too many filers share the name and place to call them one entity.
            leads = {**confirmed, **leads}
            confirmed = {}
        # A different name at the entity's own address is a lead only if its
        # filings stop where the entity's start, or the reverse (a successor).
        years = known["years"] + [
            y for m in confirmed.values() for y in m.pop("years", [])
        ]
        first, last = (min(years), max(years)) if years else (0, 0)
        kept = []
        for key, lead in leads.items():
            its = lead.pop("years", [])
            if key in confirmed or any(key[1] and key[1] == c[1] for c in confirmed):
                continue
            if lead.pop("neighbor", False) and its and years:
                before = its[-1] < first <= its[-1] + 2
                after = last < its[0] <= last + 2
                if not (before or after):
                    continue
            if listings := _lead_listings(connection, lead):
                lead["listed_as_covered_by"] = listings
            kept.append(lead)
    kept.sort(key=lambda m: (-m.pop("rank", 0), m["names"]))
    for match in confirmed.values():
        match.pop("rank", None)
        match.pop("neighbor", None)
    return list(confirmed.values()), kept


def _lists(connection, filer, searched):
    """Whether any audit filed under the (uei, ein) pair lists a searched
    UEI or EIN as covered."""
    uei, ein = filer
    for table, column, values in (
        ("additional_ueis", "additional_uei", searched["ueis"]),
        ("additional_eins", "additional_ein", searched["eins"]),
    ):
        if (
            values
            and connection.execute(
                f"SELECT 1 FROM {table} a JOIN general g ON g.report_id = a.report_id"
                f" WHERE a.{column} IN ({','.join('?' * len(values))})"
                " AND (g.auditee_uei = ? OR g.auditee_ein = ?) LIMIT 1",
                [*values, uei or "", ein or ""],
            ).fetchone()
        ):
            return True
    return False


def _lead_listings(connection, lead):
    """The audit filers that list a lead's UEI or EIN as covered, with the
    years: what FAC would show for the lead if it is this entity."""
    found = {}
    for table, column, value in (
        ("additional_eins", "additional_ein", lead["ein"]),
        ("additional_ueis", "additional_uei", lead["uei"]),
    ):
        if not _real({value}):
            continue
        for row in connection.execute(
            f"SELECT g.auditee_name, a.audit_year FROM {table} a"
            " JOIN general g ON g.report_id = a.report_id"
            f" WHERE a.{column} = ? ORDER BY a.audit_year DESC",
            [value],
        ):
            found.setdefault(names.name_key(row["auditee_name"]), []).append(row)
    return [
        {
            "name": rows[0]["auditee_name"],
            "audit_years": sorted({r["audit_year"] for r in rows}),
        }
        for rows in found.values()
    ]


def _known(connection, groups, eins, registrations):
    """The entity's names and places, from its FAC filings, SAM.gov
    registrations, and IRS records. Places are street addresses ((zip,
    address key) -> the source that gave it), cities ((state, city)), ZIPs,
    and states."""
    known = {
        "names": [],
        "keys": set(),
        "addresses": {},
        "cities": set(),
        "zips": set(),
        "states": set(),
        "years": [],
    }

    def add(name, state, city, zip5, address, source):
        if name and name not in known["names"]:
            known["names"].append(name)
            if key := names.name_key(name):
                known["keys"].add(key)
        zip5 = (zip5 or "")[:5]
        if state:
            known["states"].add(state.upper())
        if state and city:
            known["cities"].add((state.upper(), names.city_key(city)))
        if zip5:
            known["zips"].add(zip5)
        if zip5 and address:
            known["addresses"].setdefault((zip5, address), source)

    # By (UEI, EIN) pair, not by either: another organization's stray filing
    # under this entity's EIN would bring its own name and address.
    for group in groups:
        for row in connection.execute(
            "SELECT * FROM fac_filers WHERE auditee_uei = ? AND auditee_ein = ?",
            [group["uei"], group["ein"]],
        ):
            add(
                row["auditee_name"],
                row["auditee_state"],
                row["auditee_city"],
                row["zip5"],
                row["address_key"],
                "its FAC filings",
            )
            known["years"] += [row["first_year"], row["last_year"]]
    for reg in registrations:
        address = reg.get("physical_address") or {}
        source = f"SAM.gov's registration of UEI {reg['uei']}"
        for name in (reg.get("legal_business_name"), reg.get("dba_name")):
            add(
                name,
                address.get("state"),
                address.get("city"),
                address.get("zip"),
                names.address_key(address.get("line1")),
                source,
            )
    if eins and _has_table(connection, "eo_bmf"):
        for row in connection.execute(
            "SELECT EIN, NAME, SORT_NAME, STREET, CITY, STATE, ZIP FROM eo_bmf"
            f" WHERE EIN IN ({','.join('?' * len(eins))})",
            eins,
        ):
            for name in (row["NAME"], row["SORT_NAME"]):
                add(
                    name,
                    row["STATE"],
                    row["CITY"],
                    row["ZIP"],
                    names.address_key(row["STREET"]),
                    f"the IRS record of EIN {row['EIN']}",
                )
    return known


def _has_table(connection, table):
    return bool(
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?", [table]
        ).fetchone()
    )


def _government(connection, groups, registrations):
    """Whether SAM.gov or the entity's FAC filings call it a government."""
    if any(
        "government" in (reg.get("entity_structure") or "").lower()
        for reg in registrations
    ):
        return True
    for group in groups:
        row = connection.execute(
            "SELECT entity_type FROM general WHERE auditee_uei = ? AND"
            " auditee_ein = ? ORDER BY audit_year DESC LIMIT 1",
            [group["uei"], group["ein"]],
        ).fetchone()
        if row and (row["entity_type"] or "").lower() in ("state", "local", "tribal"):
            return True
    return False


def _name_queries(name):
    """FTS5 queries for a name, the first requiring each of its words. A name
    long enough to have been cut off at a field's width gets two more: its
    last word as a prefix (this name was cut), and without its last two
    words (the record's name was cut); _name_match checks what they return.
    Empty for a name with no word of four letters or more to search on."""
    found = [w for w in names.tokens(name) if len(w) > 1 and w not in names.IGNORED]
    if not found or (len(found) == 1 and len(found[0]) < 4):
        return []
    quoted = [f'"{w}"' for w in found]
    queries = [" ".join(quoted)]
    if len(name) >= names.MIN_CUT_OFF and len(found) >= 3:
        queries.append(" ".join(quoted[:-1]) + f" {quoted[-1]}*")
        queries.append(" ".join(quoted[:-2]))
    return queries


SAME, ONE_WORD, CUT_OFF = "same", "one word", "cut off"
MATCHED = {
    SAME: "",
    ONE_WORD: " (a one-word name)",
    CUT_OFF: " (the same up to where one name is cut off)",
}


def _name_match(known, candidate_names):
    """How a record's names match the entity's: SAME (word for word, two
    words or more), ONE_WORD, CUT_OFF, or None. The last two are weaker, and
    link only at the same street address."""
    hits = [k for k in map(names.name_key, candidate_names) if k in known["keys"]]
    if any(" " in key for key in hits):
        return SAME
    if hits:
        return ONE_WORD
    if any(names.cut_off(a, b) for a in known["names"] for b in candidate_names):
        return CUT_OFF
    return None


def _place(known, state, city, zip5, address):
    """How a record's place agrees with the entity's: (the source of a
    matching street address or None, whether the city or ZIP matches,
    whether the state does). With no state known for the entity, no state
    matches: a record can't be said to be in the same one."""
    zip5 = (zip5 or "")[:5]
    source = known["addresses"].get((zip5, address)) if address else None
    city = (state or "").upper(), names.city_key(city)
    near = (city in known["cities"]) or (bool(zip5) and zip5 in known["zips"])
    return source, near, city[0] in known["states"]


def _fac_matches(connection, known, ueis, eins):
    """FAC filers that match the entity's names or street addresses and share
    none of its identifiers."""
    rows = {}

    def collect(sql, params):
        for row in connection.execute(sql, params):
            rows[row["rowid"]] = row

    keys = sorted(known["keys"])
    if keys:
        collect(
            "SELECT rowid, * FROM fac_filers"
            f" WHERE name_key IN ({','.join('?' * len(keys))})",
            keys,
        )
    for name in known["names"][:4]:
        for query in _name_queries(name)[1:]:  # only the cut-off searches
            collect(
                "SELECT f.rowid, f.* FROM fac_filers_fts"
                " JOIN fac_filers f ON f.rowid = fac_filers_fts.rowid"
                " WHERE fac_filers_fts MATCH ? LIMIT 100",
                [query],
            )
    crowded = set()
    for zip5, address in known["addresses"]:
        found = connection.execute(
            "SELECT rowid, * FROM fac_filers WHERE zip5 = ? AND address_key = ?"
            " LIMIT 200",
            [zip5, address],
        ).fetchall()
        if len({(r["auditee_uei"], r["auditee_ein"]) for r in found}) > MAX_AT_ADDRESS:
            crowded.add((zip5, address))
        for row in found:
            rows[row["rowid"]] = row

    filers = {}
    for row in rows.values():
        if row["auditee_uei"] in ueis or row["auditee_ein"] in eins:
            continue
        if not _real({row["auditee_uei"], row["auditee_ein"]}):
            continue
        filers.setdefault((row["auditee_uei"], row["auditee_ein"]), []).append(row)

    matches = []
    for (uei, ein), found in filers.items():
        filed_as = sorted({r["auditee_name"] for r in found})
        kind = _name_match(known, filed_as)
        places = [
            _place(
                known,
                r["auditee_state"],
                r["auditee_city"],
                r["zip5"],
                r["address_key"],
            )
            for r in found
        ]
        address = next((p[0] for p in places if p[0]), None)
        near = any(p[1] for p in places)
        years = sorted({y for r in found for y in (r["first_year"], r["last_year"])})
        span = f"{years[0]}-{years[-1]}" if years[0] != years[-1] else str(years[0])
        match = {
            "uei": uei,
            "ein": ein,
            "names": filed_as,
            "audit_years": span,
            "years": years,
        }
        if (kind == SAME and (address or near)) or (kind and address):
            if address:
                at = next(r for r, p in zip(found, places) if p[0])
                where = (
                    f"the same street address ({at['address_key']}, "
                    f"{at['auditee_city']} {at['zip5']}) as {address}"
                )
            else:
                where = (
                    f"the same city ({found[0]['auditee_city']}) as its other records"
                )
            match["links"] = [
                f"filed as {filed_as[0]}{MATCHED[kind]} from {where}; no UEI or "
                "EIN in common" + _irs_says(connection, ein)
            ]
            matches.append({**match, "confirmed": True})
        elif kind == SAME and any(p[2] for p in places):
            match["links"] = [
                "the same name in the same state, with no address in common: may "
                "be another organization"
            ]
            matches.append({**match, "confirmed": False, "rank": 2})
        elif kind and near:
            match["links"] = [
                f"a matching name{MATCHED[kind]} in the same city, at another "
                "street address: may be another organization"
            ]
            matches.append({**match, "confirmed": False, "rank": 2})
        elif address and not kind:
            if any((r["zip5"], r["address_key"]) in crowded for r in found):
                continue
            similar = same_name(known["names"], filed_as)
            match["links"] = [
                f"a {'similar' if similar else 'different'} name at the same "
                f"street address as {address}: possibly a parent, successor, or "
                "related organization"
            ]
            matches.append(
                {
                    **match,
                    "confirmed": False,
                    "rank": 3 if similar else 1,
                    # At the entity's own filing address, only a predecessor
                    # or successor is worth listing; _bridge checks the years.
                    "neighbor": address == "its FAC filings" and not similar,
                }
            )
    return matches


def _irs_says(connection, ein):
    """What the IRS has for an EIN, as the end of a sentence: whose it is,
    or that it has no record. Empty when the IRS file isn't loaded."""
    if not _real({ein}) or not _has_table(connection, "eo_bmf"):
        return ""
    row = connection.execute(
        "SELECT NAME, CITY, STATE FROM eo_bmf WHERE EIN = ?", [ein]
    ).fetchone()
    if not row:
        loaded = connection.execute("SELECT 1 FROM eo_bmf LIMIT 1").fetchone()
        return f". The IRS has no record of EIN {ein}" if loaded else ""
    return f". The IRS lists EIN {ein} as {row['NAME']}, {row['CITY']}, {row['STATE']}"


def _irs_matches(connection, known, eins):
    """IRS exempt-organization records that match the entity's names, when
    the entity has no EIN the IRS lists: a UEI with no FAC filing has no EIN
    at all, and an EIN the IRS lacks may be mistyped."""
    if not _has_table(connection, "eo_bmf"):
        return []
    if (
        eins
        and connection.execute(
            f"SELECT 1 FROM eo_bmf WHERE EIN IN ({','.join('?' * len(eins))}) LIMIT 1",
            eins,
        ).fetchone()
    ):
        return []
    rows, counts = {}, []
    for name in known["names"][:4]:
        for number, query in enumerate(_name_queries(name)):
            found = connection.execute(
                "SELECT b.EIN, b.NAME, b.SORT_NAME, b.STREET, b.CITY, b.STATE,"
                " b.ZIP FROM eo_bmf_fts JOIN eo_bmf b ON b.rowid = eo_bmf_fts.rowid"
                f" WHERE eo_bmf_fts MATCH ? LIMIT {IRS_NAME_LIMIT}",
                [query],
            ).fetchall()
            if number == 0:
                counts.append(len(found))
            for row in found:
                rows[row["EIN"]] = row
    # The searches returned every IRS record with the name's words, unless
    # one hit its limit: then how many share the exact name is unknown.
    complete = all(count < IRS_NAME_LIMIT for count in counts)
    # By primary name only. A secondary name is often a chapter's local name
    # under a statewide organization's EIN: of 352 "only record in the
    # state" matches on it across FAC, nearly all were wrong.
    primary = [
        r["EIN"] for r in rows.values() if _name_match(known, [r["NAME"]]) == SAME
    ]
    matches = []
    for row in rows.values():
        if row["EIN"] in eins:
            continue
        irs_names = [n for n in (row["NAME"], row["SORT_NAME"]) if n]
        kind = _name_match(known, irs_names)
        address, near, state = _place(
            known,
            row["STATE"],
            row["CITY"],
            row["ZIP"],
            names.address_key(row["STREET"]),
        )
        listed = f"the IRS lists EIN {row['EIN']} as {row['NAME']}" + (
            f" (secondary name {row['SORT_NAME']})" if row["SORT_NAME"] else ""
        )
        where = f"{row['CITY']}, {row['STATE']}"
        match = {"uei": None, "ein": row["EIN"], "names": irs_names, "audit_years": ""}
        if (kind == SAME and (address or near)) or (kind and address):
            agrees = (
                f"at the same street address as {address}"
                if address
                else "in the same city"
            )
            match["links"] = [
                f"{listed}, {where}: this entity's name{MATCHED[kind]}, {agrees}"
            ]
            matches.append({**match, "confirmed": True})
        elif kind == SAME and state and complete and primary == [row["EIN"]]:
            match["links"] = [
                f"{listed}, {where}: this entity's name, in the same state, and "
                "the only IRS record under that name; the city differs"
            ]
            matches.append({**match, "confirmed": True})
        elif kind == SAME and state:
            match["links"] = [
                f"{listed}, {where}: this entity's name in the same state, with "
                "no address in common"
            ]
            matches.append({**match, "confirmed": False, "rank": 2})
        elif kind and near:
            match["links"] = [
                f"{listed}, {where}: a matching name{MATCHED[kind]} in the same "
                "city, at another street address"
            ]
            matches.append({**match, "confirmed": False, "rank": 2})
        elif address and same_name(irs_names, known["names"]):
            match["links"] = [
                f"{listed}, {where}: a similar name at the same street address "
                f"as {address}"
            ]
            matches.append({**match, "confirmed": False, "rank": 3})
    return matches


def _other_organizations(linked):
    """(other organizations, their (uei, ein) pairs), removing them from
    linked["identifiers"].

    FAC doesn't validate identifiers, so a filer can put another
    organization's EIN or UEI on a report. Linking by shared identifier then
    pulls that report, and the filer's UEI, into this entity's history (a
    housing nonprofit's FY2023 audit carries a hospital system's EIN). A
    group of identifiers is another organization's when its names match none
    of the entity's, it has an identifier of its own that the entity never
    used, and at most STRAY_FILINGS of its filings carry one of the entity's.
    Two organizations that share an identifier year after year stay together
    and are flagged in the notes instead.
    """
    groups = [g for g in linked["identifiers"] if not g["lead"]]
    searched = linked["searched"]
    families = []  # groups whose names match each other
    for group in groups:
        for family in families:
            if any(same_name(group["names"], other["names"]) for other in family):
                family.append(group)
                break
        else:
            families.append([group])
    if len(families) < 2:
        return [], []

    def weight(family):
        asked = any(
            g["uei"] in searched["ueis"] or g["ein"] in searched["eins"] for g in family
        )
        return asked, sum(len(g["audit_years"]) for g in family)

    core = max(families, key=weight)
    core_ueis = _real({g["uei"] for g in core})
    core_eins = _real({g["ein"] for g in core})
    others, foreign = [], []
    with db.connect() as connection:
        for family in families:
            if family is core:
                continue
            own_ueis = sorted(_real({g["uei"] for g in family}) - core_ueis)
            own_eins = sorted(_real({g["ein"] for g in family}) - core_eins)
            if not (own_ueis or own_eins):
                continue
            clauses, params = [], []
            if own_ueis:
                clauses.append(f"auditee_uei IN ({','.join('?' * len(own_ueis))})")
                params += own_ueis
            if own_eins:
                clauses.append(f"auditee_ein IN ({','.join('?' * len(own_eins))})")
                params += own_eins
            total = connection.execute(
                f"SELECT count(*) FROM general WHERE {' OR '.join(clauses)}", params
            ).fetchone()[0]
            stray = [
                g for g in family if g["uei"] in core_ueis or g["ein"] in core_eins
            ]
            shared = sum(len(g["audit_years"]) for g in stray)
            if not stray or shared > STRAY_FILINGS or total <= shared:
                continue
            used = sorted(
                {f"UEI {g['uei']}" for g in stray if g["uei"] in core_ueis}
                | {f"EIN {g['ein']}" for g in stray if g["ein"] in core_eins}
            )
            own = [f"UEI {u}" for u in own_ueis] + [f"EIN {e}" for e in own_eins]
            # The EINs its other filings carry, which the stray one doesn't.
            usual = [
                row[0]
                for row in connection.execute(
                    "SELECT DISTINCT auditee_ein FROM general WHERE"
                    f" ({' OR '.join(clauses)}) ORDER BY 1",
                    params,
                )
                if row[0] not in core_eins and row[0] not in own_eins
            ]
            elsewhere = "".join(
                f" Those filings give EIN {e}{_irs_says(connection, e)}."
                for e in _real(set(usual))
            )
            filed_as = sorted({n for g in family for n in g["names"]})
            years = sorted({y for g in stray for y in g["audit_years"]})
            others.append(
                {
                    "names": filed_as,
                    "ueis": own_ueis,
                    "eins": own_eins,
                    "audits_under_this_entitys_identifier": [
                        a for g in stray for a in g["audits"]
                    ],
                    "why": (
                        f"{filed_as[0]} filed for {', '.join(years)} under this "
                        f"entity's {' and '.join(used)}. Its other "
                        f"{total - shared} filings are under {', '.join(own)}, "
                        "and its name matches none of this entity's." + elsewhere
                    ),
                }
            )
            foreign += [[g["uei"], g["ein"]] for g in family]
    keys = {tuple(pair) for pair in foreign}
    linked["identifiers"] = [
        g for g in linked["identifiers"] if (g["uei"], g["ein"]) not in keys
    ]
    return others, foreign


def link_notes(linked, found, filed, coverage=False):
    """Notes on how the identifiers were linked: what was joined by name and
    place, what was set aside, and searched identifiers that found nothing.
    For grantee_audit_coverage (coverage=True), whose answer has no
    other_organizations or unconfirmed_leads lists, only the first two, and
    pointing at this tool for the detail."""
    notes = []
    if found.get("skipped"):
        notes.append(found["skipped"])
    if found.get("sam_skipped"):
        notes.append(
            f"SAM.gov couldn't be asked ({found['sam_skipped']}), so filings "
            "were linked by name and place without the registration's name and "
            "address. A filing under another identifier that only those would "
            "link is missing here; call again to retry."
        )
    for entry in found["bridged"]:
        label = " / ".join(
            [f"UEI {u}" for u in _real({entry["uei"]})] + [f"EIN {entry['ein']}"]
        )
        years = f" ({entry['audit_years']})" if entry["audit_years"] else ""
        notes.append(
            f"{label}{years} shares no identifier with the searched ones and was "
            f"linked by name and place: {entry['links'][0]}. Check that before "
            "relying on it."
        )
    for other in found["other_organizations"]:
        where = (
            "grantee_identity_resolution lists its filings."
            if coverage
            else "See other_organizations."
        )
        notes.append(f"Left out as another organization's: {other['why']} {where}")
    if coverage:
        return notes
    if found["leads"]:
        more = (
            f", and {found['leads_not_shown']} more not shown"
            if found["leads_not_shown"]
            else ""
        )
        notes.append(
            f"{len(found['leads'])} possible matches by name or address are in "
            f"unconfirmed_leads{more}; none is treated as this entity."
        )
    if linked["not_found"] and filed:
        notes.append(
            f"Nothing has filed under {', '.join(linked['not_found'])}. Any "
            "one-character neighbors are in unconfirmed_leads; confirm one with "
            "sam_get_registration before treating it as this entity."
        )
    elif not filed:
        notes.append(
            "Nothing has filed to FAC under these identifiers. Any one-character "
            "neighbors are in unconfirmed_leads, each possibly another "
            "organization."
        )
    return notes


def tie_notes(found, covered_by):
    """Notes for identifiers joined by name and place that a parent's audit
    also lists beside a searched one: FAC itself ties the two."""
    asked = found["asked"]
    notes = []
    for entry in found["bridged"]:
        for row in covered_by:
            listed = row["listed_identifiers"]
            both = [
                (f"UEI {u}", f"EIN {entry['ein']}")
                for u in asked["ueis"]
                if u in listed["ueis"] and entry["ein"] in listed["eins"]
            ] + [
                (f"EIN {e}", f"UEI {entry['uei']}")
                for e in asked["eins"]
                if e in listed["eins"] and entry["uei"] in listed["ueis"]
            ]
            if both:
                name = row["listed_by_name"] or row["listed_by_auditee_uei"]
                notes.append(
                    f"FAC ties {both[0][1]} to {both[0][0]} directly: {name}'s "
                    f"{row['audit_year']} audit lists both as covered "
                    f"({row['report_id']})."
                )
                break
    return notes


def lead_questions(found):
    questions = []
    for entry in found["bridged"]:
        label = entry["ein"] and f"EIN {entry['ein']}" or f"UEI {entry['uei']}"
        questions.append(
            f"Why do this organization's filings under {label} share no "
            "identifier with its others: a mistyped identifier, a related "
            "organization's, or a new legal entity?"
        )
    if found["leads"]:
        questions.append(
            "Is any organization in unconfirmed_leads this entity, its parent, "
            "or its successor?"
        )
    return questions


def with_asked(ueis, found):
    """The UEIs to look up in SAM.gov: the linked ones and the searched ones.
    A searched UEI with no filing under it is still the grantee's
    registration, and its name and address are what linked the rest."""
    return sorted(set(ueis) | _real(set(found["asked"]["ueis"])))


def unbridged(eins, found):
    """The EINs linked by a shared identifier, leaving out those joined by
    name and place: two EINs under one UEI may be two organizations, and the
    notes ask; an EIN joined by name and place has its own note."""
    bridged = {b["ein"] for b in found["bridged"]}
    return [e for e in eins if e not in bridged]


def shared_uei_note(eins, groups):
    """S10: two organizations filed under one UEI. Linking by shared UEI
    merges their histories, so say so before anyone reads it as one."""
    filed_as = sorted({n for g in groups for n in g["names"]})
    return (
        f"The identifiers span {len(eins)} EINs ({', '.join(eins)}) with "
        f"{len(filed_as)} names. FAC does not validate UEIs, so a UEI can be "
        "shared by unrelated organizations; check the names in identifiers, "
        "and filed_under on each of grantee_audit_coverage's rows, before "
        "reading these filings as one entity's."
    )


def entity_block(fac_type, registrations, history, covered_by, irs_records):
    """What FAC, SAM.gov, and the IRS say the entity is, and its year end."""
    regs = registrations.get("registrations", [])
    reg = _registration(regs) or (regs[0] if regs else {})
    return {
        "fac_entity_type": fac_type,
        "sam_entity_structure": reg.get("entity_structure"),
        "sam_profit_structure": reg.get("profit_structure"),
        "fiscal_year_end": _fiscal_year_end(history, regs, covered_by, irs_records),
    }


def year_end_notes(year_end):
    """The note that sources' fiscal year ends disagree, if they do."""
    if not year_end["sources_disagree"]:
        return []
    each = "; ".join(
        f"{source}: {value}" for source, _, value in _year_end_readings(year_end)
    )
    return [
        f"Fiscal year ends disagree ({each}). fiscal_year_end.used is "
        f"{year_end['used']['source']}'s, by the rule {YEAR_END_RULE}. "
        + (
            "The entity may have changed its year end; the first year after a "
            "change is short."
            if _own_sources_disagree(year_end)
            else "Only the parent's differs: a parent's audit can run on "
            "another fiscal year than the entity it lists."
        )
    ]


def registration_notes(registrations, filed):
    """Notes on UEIs SAM.gov has no record of, or couldn't settle. filed is
    the UEIs with a FAC filing: a searched UEI may have none."""
    if registrations.get("not_found_incomplete"):
        missing = ", ".join(registrations["not_found"])
        return [
            f"SAM.gov returned too many duplicate records to settle {missing}; "
            "look them up again with sam_get_registration."
        ]
    return [
        _unregistered_note(uei, registrations, filed)
        for uei in registrations.get("not_found", [])
    ]


def identity_questions(registrations, entity, eins, filed):
    """Questions about who the entity is, for the analyst. Not verdicts.
    filed is the UEIs with a FAC filing."""
    questions = []
    for uei in registrations.get("not_found", []):
        filing = (
            "It filed to FAC but"
            if uei in filed
            else "Nothing has filed to FAC under it, and"
        )
        if _one_character_from(uei, registrations):
            questions.append(
                f"Is {uei} a mistyped form of a registered UEI? {filing} SAM.gov "
                "has no public registration for it."
            )
        else:
            questions.append(
                f"Is {uei} a real UEI not shown publicly, or a wrong one? {filing} "
                "SAM.gov has no record of it."
            )
    if _own_sources_disagree(entity["fiscal_year_end"]):
        sources = ", ".join(r[0] for r in _year_end_readings(entity["fiscal_year_end"]))
        questions.append(
            f"Did the entity change its fiscal year end? {sources} disagree."
        )
    if len(eins) > 1:
        questions.append(
            f"Are the {len(eins)} EINs filing under these identifiers one organization "
            "or several sharing a UEI?"
        )
    return questions


def _parents(covered_by):
    """covered_by rows gathered by the audit filer that lists the entity:
    its names, identifiers, the years it lists the entity, and whether by
    UEI, EIN, or both."""
    parents = {}
    # Latest year first, so each parent's latest_report_id is its latest:
    # covered_by has every listing by UEI, then every listing by EIN.
    for row in sorted(covered_by, key=lambda row: -int(row["audit_year"])):
        key = row["listed_by_ein"] or row["listed_by_auditee_uei"]
        parent = parents.setdefault(
            key,
            {
                "listed_by_name": row["listed_by_name"],
                "listed_by_ein": row["listed_by_ein"],
                "listed_by_ueis": [],
                "audit_years": [],
                "listed_by": set(),
                "latest_report_id": row["report_id"],
                "source_url": row["source_url"],
            },
        )
        if row["listed_by_auditee_uei"] not in (*parent["listed_by_ueis"], SENTINEL):
            parent["listed_by_ueis"].append(row["listed_by_auditee_uei"])
        if row["audit_year"] not in parent["audit_years"]:
            parent["audit_years"].append(row["audit_year"])
        listed = row["listed_identifiers"]
        parent["listed_by"] |= {"UEI"} if listed["ueis"] else set()
        parent["listed_by"] |= {"EIN"} if listed["eins"] else set()
    for parent in parents.values():
        parent["audit_years"].sort()
        parent["listed_by"] = " and ".join(sorted(parent["listed_by"], reverse=True))
    return list(parents.values())


def _sefa_mentions(irs_records):
    """Other audits' SEFA notes that name an EIN the IRS has no record of, and
    wasn't revoked, as ({EIN: [mentions]}, [EINs with more than shown]). With
    no IRS record, a note listing the EIN beside a name is the other place
    that says whose it is (irs-shared-ein: a hospital authority's notes give
    a mistyped EIN to its imaging subsidiary)."""
    revoked = {r["ein"] for r in irs_records.get("revocations", [])}
    missing = [e for e in irs_records.get("not_found", []) if e not in revoked]
    if not missing:
        return {}, []
    with db.connect() as connection:
        found, more = _note_mentions(connection, missing)
    mentions = {}
    for mention in found:
        ein = mention.pop("ein")
        mentions.setdefault(ein, []).append(mention)
    return mentions, more


def _with_mentions(entries, mentions, more):
    """identifiers entries, with each EIN's SEFA-note mentions on the latest
    entry under that EIN (entries are oldest first), so they aren't repeated."""
    latest = {entry["ein"]: entry for entry in entries}
    for ein, found in mentions.items():
        if ein in latest:
            latest[ein]["named_in_sefa_notes"] = found
            if ein in more:
                latest[ein]["notes_more_available"] = True
    return entries


def _candidates(name, state):
    """(Distinct organizations, by EIN, whose FAC filings match a name;
    whether more filings match than the NAME_FILINGS_READ newest read)."""
    params = {
        "select": (
            "auditee_name,auditee_uei,auditee_ein,auditee_city,auditee_state,audit_year"
        ),
        "auditee_name": fac.contains(name),
        "order": "audit_year.desc",
        "limit": NAME_FILINGS_READ,
    }
    if state:
        params["auditee_state"] = fac.equals(identifiers.state(state))
    found = {}
    rows = fac.get("general", params)
    for row in rows:
        key = row["auditee_ein"] or row["auditee_uei"]
        entry = found.setdefault(
            key,
            {
                "name": row["auditee_name"],
                "uei": row["auditee_uei"] if row["auditee_uei"] != SENTINEL else None,
                "ein": row["auditee_ein"],
                "city": row["auditee_city"],
                "state": row["auditee_state"],
                "latest_audit_year": row["audit_year"],
            },
        )
        if not entry["uei"] and row["auditee_uei"] != SENTINEL:
            entry["uei"] = row["auditee_uei"]
    return list(found.values()), len(rows) >= NAME_FILINGS_READ


def _general_rows(connection, ueis, eins):
    clauses, params = [], []
    if ueis:
        clauses.append(f"auditee_uei IN ({','.join('?' * len(ueis))})")
        params += ueis
    if eins:
        clauses.append(f"auditee_ein IN ({','.join('?' * len(eins))})")
        params += eins
    return connection.execute(
        "SELECT report_id, audit_year, auditee_name, auditee_uei, auditee_ein,"
        " entity_type, fy_start_date, fy_end_date, fac_accepted_date,"
        " total_amount_expended, resubmission_version"
        f" FROM general WHERE {' OR '.join(clauses)}"
        " ORDER BY fy_end_date, fac_accepted_date",
        params,
    ).fetchall()


def _audit(row, filed_since_snapshot=False):
    """One audit_history row with its nine-month due date and days late."""
    fy_end = _date(row["fy_end_date"])
    accepted = _date(row["fac_accepted_date"])
    due = nine_months_after(fy_end) if fy_end else None
    audit = {
        "audit_year": int(row["audit_year"]) if row["audit_year"] else None,
        "fy_end_date": row["fy_end_date"],
        "report_id": row["report_id"],
        "fac_accepted_date": row["fac_accepted_date"],
        "due_date": due.isoformat() if due else None,
        "days_late": max((accepted - due).days, 0) if accepted and due else None,
        "total_amount_expended": _number(row["total_amount_expended"]),
        # Names and entity type are in identifiers and entity; rows stay small.
        "filed_under": {"uei": row["auditee_uei"], "ein": row["auditee_ein"]},
        "source_url": fac.summary_url(row["report_id"]),
    }
    # A year that isn't twelve months (the first after a change of year end)
    # says so on its own row: with one year's rows requested, the year
    # before isn't there to compare.
    start = _date(row["fy_start_date"]) if "fy_start_date" in row.keys() else None
    if start and fy_end:
        months = round((fy_end - start).days / 30.44)
        if months != 12:
            audit["fy_start_date"] = row["fy_start_date"]
            audit["period_months"] = months
    version = _version(row)
    if version:
        audit["resubmission_version"] = version
    if filed_since_snapshot:
        audit["filed_since_snapshot"] = True
    return audit


def _version(row):
    """A filing's resubmission version as a number; 0 when it has none.
    Works on database rows, API rows, and audit_history rows."""
    try:
        return int(row["resubmission_version"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        return 0


def _current_filing(history):
    """The filing the year end is read from: the latest audit year, and within
    it the latest resubmission, then the latest accepted. A superseded filing
    doesn't win because its year-end date is later."""
    dated = [a for a in history if a["fy_end_date"] and a["audit_year"]]
    return max(
        dated,
        key=lambda a: (a["audit_year"], _version(a), a["fac_accepted_date"] or ""),
        default=None,
    )


LISTINGS_NOT_ASKED = (
    "FAC's live API couldn't be asked which audits list this entity as "
    "covered ({error}), so no parent listing is shown: a parent may list it, "
    "and a year with no audit of its own may be covered by one. "
    "fac_search_covered_entities asks again."
)


def _covered_by(ueis, eins):
    """(Parent audits listing any of the identifiers, with the parents'
    names; whether the listing search was cut off; why FAC's API couldn't be
    asked, or None). Remembered for CACHE_SECONDS: both grantee tools ask,
    and FAC's API has an hourly request limit. A failure isn't remembered,
    and isn't raised: the rest of the answer comes from the local copy."""
    if not (ueis or eins):
        return [], False, None
    key = (
        "covered_by",
        tuple(ueis),
        tuple(eins),
        id(fac.get),
        id(fac_search_covered_entities),
    )
    try:
        rows, truncated = _cached(key, lambda: _find_covered_by(ueis, eins))
    except ToolError as error:
        return [], False, str(error)
    return rows, truncated, None


def _find_covered_by(ueis, eins):
    search = fac_search_covered_entities(
        uei=ueis[: identifiers.MAX_BATCH] or None,
        ein=eins[: identifiers.MAX_BATCH] or None,
        limit=500,
    )
    found = search["reports"]
    report_ids = [r["report_id"] for r in found]
    names = {}
    for i in range(0, len(report_ids), identifiers.MAX_BATCH):
        batch = report_ids[i : i + identifiers.MAX_BATCH]
        for parent in fac.get(
            "general",
            {
                "select": "report_id,auditee_name,auditee_ein,fy_end_date",
                "report_id": fac.one_of(batch),
            },
        ):
            names[parent["report_id"]] = parent
    rows = [
        {
            "audit_year": r["audit_year"],
            "report_id": r["report_id"],
            "listed_by_auditee_uei": r["listed_by_auditee_uei"],
            "listed_by_name": names.get(r["report_id"], {}).get("auditee_name"),
            "listed_by_ein": names.get(r["report_id"], {}).get("auditee_ein"),
            "listed_by_fy_end": names.get(r["report_id"], {}).get("fy_end_date"),
            "listed_identifiers": {
                "ueis": r["covered_ueis"],
                "eins": r["covered_eins"],
            },
            "source_url": r["source_url"],
        }
        for r in found
        # An entity's own audit can list its other identifiers; that's not a
        # parent. Audits from before UEIs have none, so check the EIN too.
        if r["listed_by_auditee_uei"] not in ueis
        and names.get(r["report_id"], {}).get("auditee_ein") not in eins
    ]
    return rows, search["more_available"]


def _sam(ueis):
    """SAM.gov registrations for the real UEIs, or why they couldn't be fetched."""
    ueis = [u for u in ueis if u and u != SENTINEL][: sam.PAGE_SIZE]
    if not ueis:
        return {"registrations": [], "not_found": [], "skipped": "no UEI to look up"}
    try:
        registrations, not_found, complete = _registrations(ueis)
    except ToolError as error:
        return {"registrations": [], "not_found": [], "skipped": str(error)}
    # Records leave out empty fields (an ID Assigned UEI has few), so use get.
    result = {
        "registrations": [
            {
                k: r.get(k)
                for k in (
                    "uei",
                    "legal_business_name",
                    "dba_name",
                    "registration_status",
                    "registration_expiration_date",
                    "entity_structure",
                    "profit_structure",
                    "fiscal_year_end",
                    "source_url",
                )
            }
            | {
                k: (r.get("physical_address") or {}).get(source)
                for k, source in (
                    ("address", "line1"),
                    ("city", "city"),
                    ("state", "state"),
                )
            }
            for r in registrations
        ],
        "not_found": not_found,
    }
    if not complete:
        # Duplicate records filled every page, so not_found wasn't settled.
        result["not_found_incomplete"] = True
    return result


def _one_character_from(uei, registrations):
    """The UEI of this entity SAM.gov has that differs from uei by one character."""
    for reg in registrations.get("registrations", []):
        other = reg["uei"]
        if len(other) == len(uei) and sum(a != b for a, b in zip(uei, other)) == 1:
            return reg
    return None


def _unregistered_note(uei, registrations, filed):
    """What a UEI with no SAM.gov record most often is, given the entity's
    other UEIs.

    Counts are of the 1,904 UEIs in FAC filings with no SAM.gov record,
    registered or ID Assigned, grouped by what else filed under the same EIN.
    """
    start = f"SAM.gov has no record of {uei}, registered or not"
    if uei not in filed:
        # The counts below are of UEIs on FAC filings; this one is on none.
        return (
            f"{start}, and nothing has filed to FAC under it. It may be "
            "mistyped, or real but not shown publicly."
        )
    near = _one_character_from(uei, registrations)
    if near:
        owner = near.get("legal_business_name") or "this entity"
        return (
            f"{start}. It is one character from {near['uei']} ({owner}). In "
            "FAC, all 594 UEIs with no record one character from a UEI of the "
            "same organization were mistyped forms of it."
        )
    known = registrations.get("registrations", [])
    if known:
        return (
            f"{start}; this entity also files under {known[0]['uei']}, which "
            "SAM.gov has. Of 566 FAC UEIs in that position, 66% were made-up or "
            "wrong values, 22% typos of two or more characters, and 12% real "
            "UEIs not shown publicly."
        )
    return (
        f"{start}, and no other UEI of this entity is in SAM.gov. Of 744 FAC "
        "UEIs in that position, 48% were real UEIs not shown publicly, 9% were "
        "typos, and 43% could not be settled. usaspending_search_awards by UEI "
        "can confirm a real one."
    )


YEAR_END_RULE = (
    "the entity's latest FAC audit, then an active SAM.gov registration, then a "
    "FAC parent's audit, then an inactive SAM.gov registration, then the IRS"
)


def _fiscal_year_end(history, regs, covered_by, irs_records):
    """Every source's fiscal year end, the one used, and whether they disagree.

    The order: the entity's latest FAC audit, then an active SAM.gov
    registration (the entity reports it itself), then the latest year end among
    FAC parents' audits that list it, then an inactive SAM.gov registration,
    whose year end may be stale, then the IRS. A registration with no status
    recorded counts as active. Sources are compared by month; SAM.gov gives a
    month and day and the IRS a month only.
    """
    current = _current_filing(history)
    reg = _registration(regs)
    parents = [c for c in covered_by if _date(c.get("listed_by_fy_end"))]
    parent = max(parents, key=lambda c: c["listed_by_fy_end"], default=None)
    block = {
        "used": None,
        "rule": YEAR_END_RULE,
        "fac": {
            "value": current["fy_end_date"][:10],
            "report_id": current["report_id"],
        }
        if current
        else None,
        "sam": {
            "value": reg["fiscal_year_end"],
            "uei": reg["uei"],
            "registration_status": reg.get("registration_status"),
        }
        if reg
        else None,
        "fac_parent": {
            "value": parent["listed_by_fy_end"][:10],
            "listed_by_name": parent["listed_by_name"],
            "report_id": parent["report_id"],
        }
        if parent
        else None,
        "irs": [
            {"ein": r["ein"], "month": r["fiscal_year_end_month"]}
            for r in irs_records.get("records", [])
            if r["fiscal_year_end_month"]
        ],
    }
    readings = _year_end_readings(block)
    if readings:
        source, _, value = readings[0]
        block["used"] = {"source": source, "value": value}
    block["sources_disagree"] = len({month for _, month, _ in readings}) > 1
    return block


def _registration(regs):
    """The registration the year end is read from, among an entity's UEIs: an
    active one that gives a year end, else any that does. An ID Assigned UEI
    has no year end, and SAM.gov returns registrations in no set order."""
    dated = [r for r in regs if r.get("fiscal_year_end")]
    active = [
        r
        for r in dated
        if (r.get("registration_status") or "active").lower() == "active"
    ]
    return next(iter(active or dated), None)


def _own_sources_disagree(block):
    """Whether the entity's own sources (its FAC audit, SAM.gov, the IRS)
    disagree with each other, leaving out a parent's audit: a parent on
    another year end doesn't mean the entity changed its own."""
    months = {
        month
        for source, month, _ in _year_end_readings(block)
        if not source.startswith("a FAC parent's audit")
    }
    return len(months) > 1


def _year_end_readings(block):
    """(source, month, value as the source gives it), in YEAR_END_RULE order."""
    readings = []
    if block["fac"]:
        fac_value = block["fac"]["value"]
        readings.append(("FAC's latest audit", int(fac_value[5:7]), fac_value))
    sam = None
    if block["sam"] and re.fullmatch(r"\d{2}/\d{2}", block["sam"]["value"]):
        status = block["sam"]["registration_status"]
        inactive = bool(status) and status.lower() != "active"
        label = f"SAM.gov (registration {status})" if inactive else "SAM.gov"
        sam = (label, int(block["sam"]["value"][:2]), block["sam"]["value"])
        if not inactive:
            readings.append(sam)
            sam = None
    if block["fac_parent"]:
        parent = block["fac_parent"]
        readings.append(
            (
                f"a FAC parent's audit ({parent['listed_by_name']}, "
                f"{parent['report_id']})",
                int(parent["value"][5:7]),
                parent["value"],
            )
        )
    if sam:  # an inactive registration, after the parent
        readings.append(sam)
    for record in block["irs"]:
        month = record["month"]
        readings.append(
            (f"the IRS (EIN {record['ein']})", month, calendar.month_name[month])
        )
    return readings


IRS_FIELDS = (
    "ein",
    "name",
    "secondary_name",
    "city",
    "state",
    "exemption",
    "exemption_status",
    "group_exemption",
    "fiscal_year_end_month",
    "latest_return_period",
    "source_url",
)


def _irs(eins, fac_names):
    """IRS records for the EINs and the Schedule R filers that list them, as
    (irs_records, caveats). fac_names is {EIN: {name FAC filings used:
    audit years}}; each record says whether every one of those names matches
    the IRS name, and lists the ones that don't. When the IRS files aren't
    loaded or can't be read, irs_records says why in skipped (or
    listed_by_skipped)."""
    result = {"records": [], "not_found": [], "revocations": [], "listed_by": []}
    if not eins:
        return {**result, "skipped": "no EIN to look up"}, []
    try:
        with db.connect() as connection:
            loaded = {row["table_name"] for row in db.snapshot(connection)}
            filers = None
            if "schedule_r" in loaded:
                filers = connection.execute(
                    "SELECT count(DISTINCT ein) FROM form990_returns"
                ).fetchone()[0]
        if "eo_bmf" not in loaded:
            return {
                **result,
                "skipped": "the IRS EO BMF isn't in the local database",
            }, []
        caveats = []
        for i in range(0, len(eins), identifiers.MAX_BATCH):
            batch = eins[i : i + identifiers.MAX_BATCH]
            found = irs_search_exempt_orgs(ein=batch)
            result["records"] += found["organizations"]
            result["not_found"] += found["not_found"]
            result["revocations"] += found["revocations"]
            # What the EO BMF leaves out, and the IRS snapshot date.
            caveats = [IRS_CAVEATS[0], found["caveats"][-1]]
            if filers is not None:
                result["listed_by"] += irs_get_related_orgs(ein=batch, limit=1)[
                    "listed_by"
                ]
    except ToolError as error:
        return {**result, "skipped": str(error)}, []
    if filers is None:
        result["listed_by_skipped"] = "Schedule R isn't in the local database"
    else:
        result["schedule_r_filers_stored"] = filers
    records = []
    for org in result["records"]:
        filed = fac_names.get(org["ein"], {})
        irs_names = [org["name"], org["secondary_name"]]
        mismatched = [
            {"name": name, "audit_years": years}
            for name, years in filed.items()
            if not same_name(irs_names, [name])
        ]
        record = {k: org[k] for k in IRS_FIELDS}
        record["name_matches_fac"] = not mismatched if filed else None
        if mismatched:
            record["fac_names_not_matching"] = mismatched
        records.append(record)
    result["records"] = records
    return result, caveats


def _fac_names(rows):
    """{EIN: {name FAC filings used under it: [audit years]}}, from general rows."""
    names = {}
    for row in rows:
        if row["auditee_ein"] and row["auditee_name"]:
            years = names.setdefault(row["auditee_ein"], {}).setdefault(
                row["auditee_name"], []
            )
            year = int(row["audit_year"]) if row["audit_year"] else None
            if year and year not in years:
                years.append(year)
    return {
        ein: {name: sorted(years) for name, years in by_name.items()}
        for ein, by_name in names.items()
    }


def _irs_findings(
    irs_records,
    groups,
    history,
    covered_by,
    fac_type=None,
    mentions=None,
    more_mentions=(),
):
    """(notes, open questions) from the IRS records: EINs the IRS lacks or
    has under another name, and Schedule R listings in years no FAC audit
    covers. mentions is {EIN: SEFA-note mentions}, from _sefa_mentions."""
    mentions = mentions or {}
    notes, questions = [], []
    years = {}
    for group in groups:
        years.setdefault(group["ein"], set()).update(group["audit_years"])

    def used_in(ein):
        filed = sorted(years.get(ein, ()))
        return f" (FAC filings for {', '.join(map(str, filed))})" if filed else ""

    if irs_records.get("skipped"):
        notes.append(
            f"The IRS check didn't run ({irs_records['skipped']}), so no EIN here "
            "was compared with IRS records."
        )
        return notes, questions
    if irs_records.get("listed_by_skipped"):
        notes.append(
            f"listed_by is empty because {irs_records['listed_by_skipped']}; no "
            "Schedule R was searched for this entity."
        )
    elif irs_records.get("schedule_r_filers_stored") is not None:
        notes.append(
            "listed_by searches only the Schedule R returns stored locally, from "
            f"{irs_records['schedule_r_filers_stored']} filers; other filers' "
            "returns weren't searched."
        )
    known = [r["ein"] for r in irs_records["records"]]
    revoked = {}
    for record in irs_records["revocations"]:
        revoked.setdefault(record["ein"], []).append(record)
    for ein, records in revoked.items():
        events = "; ".join(_revocation_text(r) for r in records)
        notes.append(
            f"EIN {ein}{used_in(ein)} is not in the IRS's list of tax-exempt "
            "organizations (EO BMF). It is on the IRS's automatic revocation list, "
            f"as {records[-1]['name']} ({records[-1]['city']}, "
            f"{records[-1]['state']}): {events}. The IRS revokes an exemption "
            "automatically after three consecutive years with no Form 990 series "
            "return or notice."
        )
    fac_names = {}
    for group in groups:
        fac_names.setdefault(group["ein"], set()).update(group.get("names", ()))
    for ein in [e for e in irs_records["not_found"] if e not in revoked]:
        others = [k for k in known if k != ein]
        also = (
            f" The IRS does list {', '.join(others)}, another EIN this entity filed "
            "under."
            if others
            else ""
        )
        notes.append(
            _missing_ein_note(ein, used_in(ein), fac_type, fac_names.get(ein))
            + also
            + _mentions_text(ein, mentions.get(ein), ein in more_mentions)
        )
        questions.append(
            f"Why isn't EIN {ein}{used_in(ein)} in the IRS's list of tax-exempt "
            "organizations?"
        )
    for record in irs_records["records"]:
        if record["name_matches_fac"] is False:
            used = "; ".join(
                f"{m['name']} ({', '.join(map(str, m['audit_years']))})"
                for m in record["fac_names_not_matching"]
            )
            notes.append(
                f"The IRS lists EIN {record['ein']} as {record['name']} "
                f"({record['city']}, {record['state']}). FAC filings under that EIN "
                f"use names that don't match it: {used}."
            )
            questions.append(
                f"Does EIN {record['ein']} belong to the organization that filed as "
                f"{used}? The IRS lists it as {record['name']}."
            )

    covered = {int(c["audit_year"]) for c in covered_by}
    filed = {a["audit_year"] for a in history}
    grantee_eins = set(years) | set(irs_records["not_found"]) | set(known)
    for entry in irs_records["listed_by"]:
        if entry["filer_ein"] in grantee_eins:
            continue  # one of the entity's EINs listing another isn't a parent
        ends = [p for p in entry["periods_ending"] if p]
        uncovered = sorted({int(p[:4]) for p in ends} - covered - filed)
        if not uncovered:
            continue
        name = entry["filer_name"] or entry["filer_ein"]
        listed = ", ".join(map(str, uncovered))
        # Schedule R lists parents, children, and sister organizations alike,
        # and its controlling-entity fields are too unreliable to infer which,
        # so the note doesn't say which way the relationship runs.
        typo = _listed_under_typo(entry)
        notes.append(
            f"{name} (EIN {entry['filer_ein']}) lists this entity on its Form 990 "
            f"Schedule R as a {entry['relationship']} for fiscal years ending "
            f"{', '.join(ends)}, including {listed}, when no FAC audit was filed "
            "under it or lists it. Schedule R doesn't say which of the two "
            "controls the other, and a relationship isn't audit coverage. "
            + _where_filer_files(entry["filer_ein"], name, grantee_eins)
            + typo
        )
        questions.append(
            f"Does {name}'s audit cover this entity for {listed}? Its Schedule R "
            "lists the entity as related, but no FAC listing does."
        )
    pair_notes, pair_questions = _parent_disagreements(
        irs_records["listed_by"], covered_by, history, grantee_eins
    )
    return notes + pair_notes, questions + pair_questions


FILERS_NAMED = 5  # organizations named when a Schedule R filer has no FAC filing


def _where_filer_files(filer_ein, name, grantee_eins):
    """Where to look for a Schedule R filer's audit. A system's audit can be
    filed under another EIN of the system than the filer's (S19: BSW Health's
    FY2025 audit is under Holdings' EIN), so when FAC has nothing under the
    filer's EIN, name the organizations on its stored Schedule R that do
    file to FAC."""
    with db.connect() as connection:
        if connection.execute(
            "SELECT 1 FROM general WHERE auditee_ein = ? LIMIT 1", [filer_ein]
        ).fetchone():
            return (
                f"If {name} files a consolidated audit, check it for those years "
                f"(fac_search_audits(ein={filer_ein})) and its report text for "
                "this entity."
            )
        rows = connection.execute(
            "SELECT s.ein, s.name FROM schedule_r s"
            " WHERE s.filer_ein = ? AND s.ein != ''"
            " AND EXISTS (SELECT 1 FROM general g WHERE g.auditee_ein = s.ein)"
            " ORDER BY s.tax_year DESC",
            [filer_ein],
        ).fetchall()
    files = {}  # EIN -> its name on the latest return that lists it
    for row in rows:
        if row["ein"] not in grantee_eins:
            files.setdefault(row["ein"], row["name"] or row["ein"])
    start = f"FAC has no filing under EIN {filer_ein}"
    if not files:
        return (
            f"{start}, and none of the organizations on its stored Schedule R "
            "files to FAC under its own EIN."
        )
    shown = sorted(files)[:FILERS_NAMED]
    names = ", ".join(f"{files[e]} (EIN {e})" for e in shown)
    more = f", and {len(files) - len(shown)} more" if len(files) > len(shown) else ""
    search = shown[0] if len(shown) == 1 else "[" + ", ".join(shown) + "]"
    verb = "files" if len(files) == 1 else "file"
    return (
        f"{start}. Of the organizations on its stored Schedule R, {names}{more} "
        f"{verb} to FAC: fac_search_audits(ein={search})."
    )


def _mentions_text(ein, found, more):
    """The sentence naming the audits whose SEFA notes give an EIN, or ''."""
    if not found:
        return ""
    years = {}
    for mention in found:
        name = mention["auditee_name"] or mention["report_id"]
        year = int(mention["audit_year"]) if mention["audit_year"] else None
        if year and year not in years.setdefault(name, []):
            years[name].append(year)
    auditees = "; ".join(
        f"{name} ({', '.join(map(str, sorted(y)))})" if y else name
        for name, y in years.items()
    )
    text = (
        f" Other audits' SEFA notes name EIN {ein}: {auditees}; see "
        "named_in_sefa_notes on its entry in identifiers. A note gives the EIN "
        "to the organization it names, which may not be the one that filed "
        "under it."
    )
    if more:
        text += (
            " More notes name it than are shown (notes_more_available); search "
            "fac_search_sefa_notes for the EIN to see the rest."
        )
    return text


def _usual_type(rows):
    """The entity_type most of the entity's FAC filings give, latest on a tie.

    FAC's label varies from year to year for about 1 in 12 EINs (a health
    center filed as non-profit for nine years and local for one).
    """
    types = [row["entity_type"] for row in rows if row["entity_type"]]
    if not types:
        return None
    counts = {t: types.count(t) for t in types}
    top = max(counts.values())
    return next(t for t in reversed(types) if counts[t] == top)


def _missing_ein_note(ein, used, fac_type, names):
    """Why an EIN may be missing from the EO BMF, by what the entity usually
    files as.

    Counts are of EINs on FAC audits for 2022-2025, each typed by its most
    common FAC entity_type: the EO BMF lists 92% of nonprofit auditees' EINs,
    61% of higher-ed and 5% of government ones.
    """
    start = (
        f"EIN {ein}{used} is not in the IRS's list of tax-exempt organizations "
        "(EO BMF)."
    )
    near = _irs_neighbor(ein, names) if names else None
    if near:
        return (
            f"{start} It is one digit from {near['EIN']}, which the IRS lists as "
            f"{near['NAME']} ({near['CITY']}, {near['STATE']}), a name FAC filings "
            f"under {ein} used. Of 255 FAC EINs in this position, 7 had a "
            "neighbor that files to FAC as a separate organization; compare the "
            "addresses before treating them as one."
        )
    kind = (fac_type or "").lower()
    if kind in ("state", "local", "tribal"):
        return (
            f"{start} It lists 5% of government auditees' EINs, so a government "
            "body's absence says little."
        )
    if kind == "higher-ed":
        return (
            f"{start} It lists 61% of higher-ed auditees' EINs; a public "
            "institution can be left out."
        )
    if kind == "non-profit":
        return (
            f"{start} It lists 92% of nonprofit auditees' EINs; a missing one can "
            "be mistyped, newly exempt, or a for-profit or LLC."
        )
    return (
        f"{start} It lists 92% of nonprofit auditees' EINs and 5% of government "
        "ones; a missing EIN can also be mistyped."
    )


def _irs_neighbor(ein, names):
    """An EO BMF record one digit from ein whose name FAC filings under ein
    used, or None."""
    variants = [
        ein[:i] + d + ein[i + 1 :]
        for i in range(9)
        for d in "0123456789"
        if d != ein[i]
    ]
    marks = ",".join("?" * len(variants))
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT EIN, NAME, SORT_NAME, CITY, STATE FROM eo_bmf"
            f" WHERE EIN IN ({marks}) ORDER BY EIN",
            variants,
        ).fetchall()
    for row in rows:
        if same_name([row["NAME"], row["SORT_NAME"]], names):
            return dict(row)
    return None


def _revocation_text(record):
    """One revocation in words, with any reinstatement and the IRS's 2020 date."""
    text = f"revoked {record['revocation_date']}"
    if record.get("corrected_revocation_date"):
        text += (
            f" (the IRS says this should read {record['corrected_revocation_date']})"
        )
    if record["revocation_posting_date"]:
        text += f", posted {record['revocation_posting_date']}"
    if record["reinstatement_date"]:
        return text + f", reinstated {record['reinstatement_date']}"
    return text + ", no reinstatement date on record"


def _parent_disagreements(listed_by, covered_by, history, grantee_eins):
    """(notes, open questions) for years when a FAC audit lists the entity as
    covered and a different organization's Schedule R reports it as related,
    and neither's stored Schedule R lists the other. The rule and its reasons
    are in docs/mcp-design.md, "Flagging parents that disagree".

    - Only listings that are present are compared. A missing Schedule R is
      never a disagreement: returns arrive months after the year ends.
    - A year the entity filed its own audit is settled, and skipped.
    - The two parents' years are compared only when their fiscal years end in
      the same month; otherwise the note says the years don't line up.
    - Parents are related when one's stored Schedule R lists the other. A
      shared group exemption doesn't count (group 0928 alone covers thousands
      of unrelated Catholic organizations).
    """
    own_years = {a["audit_year"] for a in history}
    pairs = []  # (fiscal year, FAC listing, Schedule R entry, its period end)
    for listing in covered_by:
        parent, year = listing.get("listed_by_ein"), int(listing["audit_year"])
        if not parent or parent in grantee_eins or year in own_years:
            continue
        for entry in listed_by:
            if entry["filer_ein"] == parent or entry["filer_ein"] in grantee_eins:
                continue
            for period in entry["periods_ending"]:
                if period and int(period[:4]) == year:
                    pairs.append((year, listing, entry, period))
    notes, questions = [], []
    if not pairs:
        return notes, questions
    eins = {p[1]["listed_by_ein"] for p in pairs} | {p[2]["filer_ein"] for p in pairs}
    links, stored = _schedule_r_links(eins)
    seen = set()
    for year, listing, entry, period in pairs:
        parent, filer = listing["listed_by_ein"], entry["filer_ein"]
        if (year, parent, filer) in seen or frozenset((parent, filer)) in links:
            continue
        seen.add((year, parent, filer))
        parent_name = listing["listed_by_name"] or parent
        filer_name = entry["filer_name"] or filer
        parent_end = listing.get("listed_by_fy_end") or ""
        if parent_end[5:7] != period[5:7]:
            notes.append(
                f"{parent_name}'s fiscal year ends in {_month(parent_end)} and "
                f"{filer_name}'s in {_month(period)}, so FAC's {year} listing of "
                "this entity and the Schedule R one cover different months and "
                "weren't compared."
            )
            continue
        related = (
            " Neither's Schedule R lists the other."
            if parent in stored
            else f" {parent_name}'s Schedule R isn't stored, so whether the two "
            "are related is unknown."
        )
        notes.append(
            f"For fiscal year {year}, a FAC audit by {parent_name} (EIN {parent}) "
            f"lists this entity as covered, and {filer_name} (EIN {filer}) reports "
            f"it on Schedule R as a {entry['relationship']}.{related}"
            + _listed_under_typo(entry)
        )
        questions.append(
            f"Are {parent_name} and {filer_name} in the same system? For {year}, "
            "FAC shows one covering this entity and Schedule R shows the other "
            "reporting it as related."
        )
    return notes, questions


def _listed_under_typo(entry):
    """A sentence saying a Schedule R entry matched only through an EIN one
    digit from the entity's, or nothing."""
    if not entry.get("ein_as_listed"):
        return ""
    return (
        f" It lists the entity under EIN {entry['ein_as_listed']}, one digit "
        f"from {entry['ein']}."
    )


def _schedule_r_links(eins):
    """(EIN pairs where one's stored Schedule R lists the other, the EINs
    with stored returns), among the given EINs."""
    eins = sorted(eins)
    marks = ",".join("?" * len(eins))
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT filer_ein, ein FROM schedule_r"
            f" WHERE filer_ein IN ({marks}) AND ein IN ({marks})",
            eins + eins,
        ).fetchall()
        stored = connection.execute(
            f"SELECT DISTINCT ein FROM form990_returns WHERE ein IN ({marks})", eins
        ).fetchall()
    return {frozenset(r) for r in rows}, {r[0] for r in stored}


def _month(date):
    """The month name of a YYYY-MM or YYYY-MM-DD date, or 'an unrecorded month'."""
    if len(date) >= 7 and date[5:7].isdigit() and 1 <= int(date[5:7]) <= 12:
        return calendar.month_name[int(date[5:7])]
    return "an unrecorded month"


def nine_months_after(day):
    """The Uniform Guidance outside date: nine months after the year end.

    A year ending on the last day of a month is due on the last day of a
    month (30 June -> 31 March); other days keep their day of the month.
    """
    month = day.month + 9
    year = day.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    last = calendar.monthrange(year, month)[1]
    was_month_end = day.day == calendar.monthrange(day.year, day.month)[1]
    return datetime.date(year, month, last if was_month_end else min(day.day, last))


def _date(text):
    try:
        return datetime.date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _number(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


def register(mcp):
    mcp.tool(
        grantee_identity_resolution,
        name="grantee_identity_resolution",
        annotations={
            "title": "Resolve a grantee's identifiers across FAC, SAM.gov, and the IRS",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
