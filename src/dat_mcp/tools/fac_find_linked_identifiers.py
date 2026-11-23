"""fac_find_linked_identifiers: every UEI and EIN an entity has filed under."""

import re

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac, identifiers
from dat_mcp.sourcetext import SOURCE_NOTICE, fts_query

UEI_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789"  # no O or I, as SAM.gov issues them
DIGITS = "0123456789"
SENTINEL = "GSA_MIGRATION"  # Census-era rows with no UEI
PLACEHOLDER = re.compile(r"(.)\1+")  # XXXXXXXXXXXX, 000000000: one repeated character

CAVEATS = [
    "Sharing an EIN or UEI across filings is evidence the identifiers belong to "
    "the same organization or its affiliates, not proof. A near match (one "
    "character different) is a lead to confirm with sam_get_registration.",
    "FAC doesn't validate UEIs or EINs, so filings can carry typos and "
    "placeholders. GSA_MIGRATION marks "
    "Census-era rows that had no UEI; placeholders like XXXXXXXXXXXX or "
    "000000000 are never treated as shared.",
    "Audits filed by other organizations that list this entity as covered are "
    "found with fac_search_covered_entities, not here.",
]
LEADS_ONLY = (
    "Nothing has filed under the searched identifiers. The results are "
    "one-character neighbors only, each possibly a different organization; "
    "confirm one before searching again with its identifiers."
)
SOME_NOT_FOUND = (
    "Nothing has filed under {missing}. Groups linked to them only by one "
    "character are neighbors, each possibly a different organization; confirm "
    "one before searching again with its identifiers."
)
NAMED_IN_NOTES = (
    "Other audits' SEFA notes name {eins} (named_in_sefa_notes). A note gives "
    "the EIN to the organization it names, which may not be the one that filed "
    "under it, and may include that organization or exclude it (audited "
    "separately). Read the snippet, then fac_get_sefa_notes for the full note."
)
NOTES_TRUNCATED = (
    "More notes name {eins} than are shown (notes_more_available); search "
    "fac_search_sefa_notes for the EIN to see the rest."
)
NOTE_LIMIT = 10  # per searched EIN
SEVERAL_SEARCHED = (
    "Several identifiers were searched at once, so the groups below may belong "
    "to more than one organization. Read each group's links to see which "
    "searched identifier it came from."
)


def fac_find_linked_identifiers(
    uei: str | list[str] | None = None, ein: str | list[str] | None = None
):
    """Find every UEI and EIN an organization has filed FAC audits under.

    Use this first when an organization seems to have no audit, or when its
    filings may be split across identifiers. Starting from one UEI or EIN, it
    searches the local FAC database for audits filed under that identifier,
    under any UEI or EIN those audits share, and under near matches (one
    character different) that share an EIN, UEI, or name with them, and finds
    other audits' SEFA notes that name a searched EIN. uei and
    ein each take one value or a list of up to 20, e.g. every EIN a SEFA note
    names, to check them all in one call. Returns {"searched", "identifiers",
    "not_found", "leads_only", "named_in_sefa_notes", "notes_more_available",
    "caveats"}; not_found lists the searched identifiers nothing filed under,
    whose one-character neighbors are kept as unconfirmed leads (lead: true),
    and leads_only is true when that is all of them. Each
    identifier has its uei, ein, the names filed under it, how it links to the
    search, audit years, and the report_id and source_url of each audit. Look
    up any audit with fac_search_audits(report_id=...). named_in_sefa_notes
    has each note, from an audit filed under another EIN, that names a
    searched EIN (written with or without its hyphen): the EIN, the audit's
    report_id, year and auditee, the note title, a snippet with the EIN marked
    [like this], and a source_url.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333, or a list of them.
    """
    if not (uei or ein):
        raise ToolError("Give uei or ein.")
    ueis = identifiers.many(uei, identifiers.uei, "uei") if uei else []
    eins = identifiers.many(ein, identifiers.ein, "ein") if ein else []
    uei_neighbors = _neighbors(ueis, UEI_CHARS)
    ein_neighbors = _neighbors(eins, DIGITS)

    with db.connect() as connection:
        exact = _audits(connection, set(ueis), set(eins))
        near = _audits(connection, set(uei_neighbors), set(ein_neighbors))
        # Decided per searched identifier: one that matched doesn't hide the
        # leads of another that didn't.
        filed_ueis = {r["auditee_uei"] for r in exact}
        filed_eins = {r["auditee_ein"] for r in exact}
        not_found = [u for u in ueis if u not in filed_ueis] + [
            e for e in eins if e not in filed_eins
        ]
        # A near match is a typo only if something else ties it to a match.
        matched_ueis = _real(filed_ueis)
        matched_eins = _real(filed_eins)
        names = {r["auditee_name"].upper() for r in exact}

        def tied(r):
            return (
                r["auditee_uei"] in matched_ueis
                or r["auditee_ein"] in matched_eins
                or r["auditee_name"].upper() in names
            )

        def neighbors_not_found(r):
            searched = uei_neighbors.get(r["auditee_uei"], []) + ein_neighbors.get(
                r["auditee_ein"], []
            )
            return any(s in not_found for s in searched)

        confirmed = [r for r in near if tied(r)]
        # Unconfirmed leads: don't drag in whatever else they filed under.
        leads = [r for r in near if not tied(r) and neighbors_not_found(r)]
        rows = []
        if exact:
            # Everything else filed under a UEI or EIN the matches used.
            matched = exact + confirmed
            rows = _audits(
                connection,
                _real({r["auditee_uei"] for r in matched}),
                _real({r["auditee_ein"] for r in matched}),
            )
        seen = {r["report_id"] for r in rows}
        confirmed_keys = {(r["auditee_uei"], r["auditee_ein"]) for r in rows}
        rows += [r for r in leads if r["report_id"] not in seen]
        rows.sort(key=lambda r: (r["audit_year"], r["fac_accepted_date"] or ""))
        mentions, more_notes = _note_mentions(connection, eins)
        caveats = [*CAVEATS, db.snapshot_caveat(connection)]
    if more_notes:
        caveats.insert(0, NOTES_TRUNCATED.format(eins=", ".join(more_notes)))
    if mentions:
        named = sorted({m["ein"] for m in mentions})
        caveats.insert(0, NAMED_IN_NOTES.format(eins=", ".join(named)))
    if not exact:
        caveats.insert(0, LEADS_ONLY)
    elif not_found:
        caveats.insert(0, SOME_NOT_FOUND.format(missing=", ".join(not_found)))
    if len(ueis) + len(eins) > 1:
        caveats.insert(0, SEVERAL_SEARCHED)

    groups = {}
    for row in rows:
        key = (row["auditee_uei"], row["auditee_ein"])
        if key not in groups:
            groups[key] = {
                "uei": row["auditee_uei"],
                "ein": row["auditee_ein"],
                "names": [],
                "links": _links(row, ueis, eins, uei_neighbors, ein_neighbors),
                # An unconfirmed neighbor of a searched identifier that found nothing.
                "lead": key not in confirmed_keys,
                "audit_years": [],
                "audits": [],
            }
        group = groups[key]
        if row["auditee_name"] not in group["names"]:
            group["names"].append(row["auditee_name"])
        group["audit_years"].append(row["audit_year"])
        group["audits"].append(
            {
                "report_id": row["report_id"],
                "audit_year": row["audit_year"],
                "fac_accepted_date": row["fac_accepted_date"],
                "source_url": fac.summary_url(row["report_id"]),
            }
        )

    return {
        "searched": {"ueis": ueis, "eins": eins},
        "identifiers": sorted(groups.values(), key=lambda g: g["audit_years"][-1]),
        "not_found": not_found,
        # True when nothing filed under any searched identifier itself.
        "leads_only": not exact,
        "named_in_sefa_notes": mentions,
        **({"source_notice": SOURCE_NOTICE} if mentions else {}),
        "notes_more_available": more_notes,
        "caveats": caveats,
    }


def _note_mentions(connection, eins):
    """SEFA notes, from audits filed under another EIN, that name a searched
    EIN; and the searched EINs with more notes than NOTE_LIMIT."""
    mentions, more = [], []
    for ein in eins:
        if PLACEHOLDER.fullmatch(ein):
            continue
        rows = connection.execute(
            "SELECT n.report_id, g.audit_year, g.auditee_name, g.auditee_uei,"
            " g.auditee_ein, n.title, n.content,"
            " snippet(notes_to_sefa_fts, -1, '[', ']', '...', 40) AS snippet"
            " FROM notes_to_sefa_fts"
            " JOIN notes_to_sefa n ON n.rowid = notes_to_sefa_fts.rowid"
            " LEFT JOIN general g ON g.report_id = n.report_id"
            " WHERE notes_to_sefa_fts MATCH ? AND COALESCE(g.auditee_ein, '') != ?"
            " ORDER BY g.audit_year DESC, n.report_id LIMIT ?",
            [fts_query(ein), ein, NOTE_LIMIT + 1],
        ).fetchall()
        if len(rows) > NOTE_LIMIT:
            more.append(ein)
        mentions += [
            {
                "ein": ein,
                "report_id": r["report_id"],
                "audit_year": r["audit_year"],
                "auditee_name": r["auditee_name"],
                "auditee_uei": r["auditee_uei"],
                "auditee_ein": r["auditee_ein"],
                "note_title": r["title"],
                "snippet": _around(r["content"], ein) or r["snippet"],
                "source_url": fac.summary_url(r["report_id"]),
            }
            for r in rows[:NOTE_LIMIT]
        ]
    return mentions, more


SNIPPET_CHARS = 150  # on each side of the EIN


def _around(content, ein):
    """The note text on both sides of the EIN, with the EIN marked [like
    this], or None if it isn't written as the search matched it. FTS5's own
    snippet can end on the match, and notes that list affiliates put the EIN
    before the name, so the name was cut off."""
    match = re.search(rf"(?<!\d){ein[:2]}\W*{ein[2:]}(?!\d)", content or "")
    if not match:
        return None
    start = max(match.start() - SNIPPET_CHARS, 0)
    end = min(match.end() + SNIPPET_CHARS, len(content))
    before, after = content[start : match.start()], content[match.end() : end]
    if start:  # don't start or end partway through a word
        words = before.split(None, 1)
        before = "..." + (words[1] if len(words) > 1 else "")
    if end < len(content):
        words = after.rsplit(None, 1)
        after = (words[0] if len(words) > 1 else "") + "..."
    return f"{before}[{match.group()}]{after}"


def _real(values):
    """Identifiers that could name an entity: not blank, sentinel, or placeholder."""
    return {v for v in values if v and v != SENTINEL and not PLACEHOLDER.fullmatch(v)}


def _neighbors(searched, alphabet):
    """Every string one character different from a searched value, mapped to
    the searched values it is one character from."""
    neighbors = {}
    for value in searched:
        for i in range(len(value)):
            for c in alphabet:
                if c != value[i]:
                    variant = value[:i] + c + value[i + 1 :]
                    neighbors.setdefault(variant, []).append(value)
    return neighbors


def _audits(connection, ueis, eins):
    """general rows filed under any of the UEIs or EINs, oldest first."""
    clauses, params = [], []
    if ueis:
        clauses.append(f"auditee_uei IN ({','.join('?' * len(ueis))})")
        params += sorted(ueis)
    if eins:
        clauses.append(f"auditee_ein IN ({','.join('?' * len(eins))})")
        params += sorted(eins)
    if not clauses:
        return []
    return connection.execute(
        "SELECT report_id, audit_year, auditee_name, auditee_uei, auditee_ein,"
        f" fac_accepted_date FROM general WHERE {' OR '.join(clauses)}"
        " ORDER BY audit_year, fac_accepted_date",
        params,
    ).fetchall()


def _links(row, ueis, eins, uei_neighbors, ein_neighbors):
    """How this filing's identifiers relate to the ones searched for."""
    links = []
    if row["auditee_uei"] in ueis:
        links.append(f"searched UEI {row['auditee_uei']}")
    else:
        for searched in uei_neighbors.get(row["auditee_uei"], []):
            links.append(f"one character different from UEI {searched}")
    if row["auditee_ein"] in eins:
        links.append(f"searched EIN {row['auditee_ein']}")
    else:
        for searched in ein_neighbors.get(row["auditee_ein"], []):
            links.append(f"one digit different from EIN {searched}")
    return links or ["shares a UEI or EIN with a matched filing"]


def register(mcp):
    mcp.tool(
        fac_find_linked_identifiers,
        name="fac_find_linked_identifiers",
        annotations={
            "title": "Find an entity's linked UEIs and EINs in FAC",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
