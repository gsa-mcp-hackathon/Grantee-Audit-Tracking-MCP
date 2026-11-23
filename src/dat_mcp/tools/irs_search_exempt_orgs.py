"""irs_search_exempt_orgs: look up EINs, or search by name, in the IRS EO BMF."""

import difflib
import re
import unicodedata

from fastmcp.exceptions import ToolError

from dat_mcp import db, identifiers, irs

# Words that vary across sources' versions of a name, so they're not required.
IGNORED_WORDS = {"inc", "incorporated", "the", "of", "and", "corp", "corporation", "co"}

CAVEATS = [
    "The EO BMF lists organizations with current federal tax exemption only: "
    "no for-profits, few government bodies, and none whose exemption was "
    "revoked. Not being in it isn't proof an EIN is wrong.",
    "The address is the one the organization files from, which may not be "
    "where it operates.",
]

NOT_FOUND = (
    "Not in the EO BMF: {missing}. Each may be a typo, or an organization the "
    "file leaves out (below). Search by the organization's name and state to "
    "see which EIN the IRS has for it. An EIN missing here can belong to "
    "another organization: fac_find_linked_identifiers(ein=...) shows the FAC "
    "audits filed under it and other audits' SEFA notes that name it."
)

MISMATCH = (
    "matches_name is false for {eins}: the IRS name doesn't share most of its "
    "words with {name!r}. name_candidates lists what the IRS has under that name."
)

REVOKED = (
    "On the IRS's automatic revocation list (revocations): {eins}. The IRS "
    "revokes an exemption automatically when an organization files no Form 990 "
    "series return or notice for three consecutive years, and removes it from "
    "the EO BMF until it is reinstated; a reinstatement_date means it was "
    "reinstated. Of 352 FAC auditee EINs on the list and not in the EO BMF, "
    "205 were government bodies and 99 private nonprofits under the same "
    "name."
)

COVID_DATES = (
    "corrected_revocation_date: the IRS says revocation dates from April 1 to "
    "July 14, 2020 should read July 15, 2020."
)

NAME_SEARCH = (
    "Name matches are candidates, not identifications. Related organizations "
    "can share most of a name and have consecutive EINs, so confirm one by "
    "its address and the source that named it before using its EIN."
)

NO_NAME_MATCH = (
    "Nothing matched. The IRS spells out AND, drops punctuation, and "
    "abbreviates freely; try fewer words, another form of the name, or no "
    "state. A trade or chapter name is matched as secondary_name."
)

TRUNCATED = (
    "more_available is true: more organizations match than limit={limit}. Add "
    "words or a state, or raise limit, before saying an organization isn't here."
)

GROUP_MEMBER = (
    "In a group exemption (group_exemption): {eins}. A subordinate "
    "shares its central organization's exemption, is sometimes listed under "
    "the central's name, and need not appear in the central's Schedule R."
)


def irs_search_exempt_orgs(
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
    limit: int = 10,
):
    """Look up EINs, search organizations by name, or both at once, in the
    IRS's list of tax-exempt organizations (the EO BMF).

    Use it to check an EIN from FAC or a SEFA note: whether the IRS has it,
    and under what name. Search by name and state to find the EIN the IRS has
    for an organization, such as a subsidiary named in an audit. Give both
    ein and name to check a suspected typo in one call: each EIN's record says
    whether its IRS name matches the name given (matches_name), and
    name_candidates lists what the IRS has under that name. ein takes one EIN
    or a list of up to 20; state narrows the name search. Returns {"count",
    "more_available", "organizations", "not_found", "revocations",
    "name_candidates", "caveats"}; organizations are the EIN lookups, or the
    name matches when
    no ein is given. Each has its EIN, name, secondary (trade or chapter)
    name, address, exemption type and status, group exemption with its central
    organization, filing requirement, latest return period, fiscal-year-end
    month, revenue, assets, and a source_url to cite. not_found lists searched
    EINs the IRS doesn't have; revocations gives any of them on the IRS's
    automatic revocation list, with the revocation and any reinstatement date.

    Args:
        ein: 9-digit EIN, e.g. 112223333 or 11-2223333, or a list of them.
        name: Words from the organization's name, e.g. Valley Community Health
            Clinic. Every word must appear; inc, the, and of are optional.
        state: 2-letter state of the organization's address, for a name
            search, e.g. OH.
        limit: Most organizations to return for a name search, 1 to 50.
    """
    if not (ein or name):
        raise ToolError("Give ein, name (with an optional state), or both.")
    if state and not name:
        raise ToolError("state narrows a name search; an EIN lookup doesn't need it.")
    if not 1 <= limit <= 50:
        raise ToolError("limit must be between 1 and 50.")
    eins = identifiers.many(ein, identifiers.ein, "ein") if ein else []
    query = _name_query(name) if name else None
    state = identifiers.state(state) if state else None

    with db.connect() as connection:
        by_ein, named, more = [], [], False
        if eins:
            marks = ",".join("?" * len(eins))
            rows = connection.execute(
                f"SELECT * FROM eo_bmf WHERE EIN IN ({marks})", eins
            ).fetchall()
            found = {r["EIN"]: r for r in rows}
            by_ein = [found[e] for e in eins if e in found]
        if name:
            sql = (
                "SELECT b.* FROM eo_bmf_fts JOIN eo_bmf b ON b.rowid = eo_bmf_fts.rowid"
                " WHERE eo_bmf_fts MATCH ?"
            )
            params = [query]
            if state:
                sql += " AND b.STATE = ?"
                params.append(state)
            named = connection.execute(
                sql + " ORDER BY rank LIMIT ?", [*params, limit + 1]
            ).fetchall()
            more = len(named) > limit
            named = named[:limit]
        centrals = _centrals(connection, {r["GROUP"] for r in by_ein + named})
        missing = [e for e in eins if e not in {r["EIN"] for r in by_ein}]
        revoked = []
        if missing:
            revoked = connection.execute(
                "SELECT * FROM irs_revocations WHERE ein IN"
                f" ({','.join('?' * len(missing))}) ORDER BY ein, revocation_date",
                missing,
            ).fetchall()
        snapshot = db.snapshot_caveat(connection, "irs")

    def flatten(rows):
        return [irs.organization(r, centrals.get(r["GROUP"])) for r in rows]

    candidates = flatten(named)
    if eins:
        organizations = flatten(by_ein)
        if name:
            for org in organizations:
                org["matches_name"] = same_name(
                    [org["name"], org["secondary_name"]], [name]
                )
    else:
        organizations, candidates = candidates, []
    not_found = [e for e in eins if e not in {o["ein"] for o in organizations}]

    revocations = [irs.revocation(r) for r in revoked]
    revoked_eins = list(dict.fromkeys(r["ein"] for r in revocations))
    unexplained = [e for e in not_found if e not in revoked_eins]
    caveats = list(CAVEATS)
    if unexplained:
        caveats.insert(0, NOT_FOUND.format(missing=", ".join(unexplained)))
    if revoked_eins:
        caveats.insert(0, REVOKED.format(eins=", ".join(revoked_eins)))
    if any("corrected_revocation_date" in r for r in revocations):
        caveats.append(COVID_DATES)
    mismatched = [o for o in organizations if o.get("matches_name") is False]
    if mismatched:
        caveats.insert(
            0,
            MISMATCH.format(
                eins=", ".join(f"{o['ein']} ({o['name']})" for o in mismatched),
                name=name,
            ),
        )
    if name:
        caveats.append(NAME_SEARCH if organizations or candidates else NO_NAME_MATCH)
    if more:
        caveats.append(TRUNCATED.format(limit=limit))
    members = [o["ein"] for o in organizations + candidates if o["group_exemption"]]
    if members:
        caveats.append(GROUP_MEMBER.format(eins=", ".join(dict.fromkeys(members))))
    caveats.append(snapshot)
    return {
        "count": len(organizations),
        "more_available": more,
        "organizations": organizations,
        "not_found": not_found,
        "revocations": revocations,
        "name_candidates": candidates,
        "caveats": caveats,
    }


def same_name(irs_names, other_names):
    """Whether an IRS name and another source's name match: every word of the
    shorter name matches a word of the longer one. A word matches itself or
    its abbreviation (CTR / CENTER, SVCS / SERVICES, ST / SAINT), so the IRS's
    abbreviations aren't mismatches but a different word is (HEALTH /
    HOLDINGS). The IRS drops punctuation and spells out AND, so words are
    compared, not strings, and words in IGNORED_WORDS don't count."""
    for irs_name in filter(None, irs_names):
        for other in filter(None, other_names):
            a, b = _words(irs_name), _words(other)
            if a and b and _covers(a, b):
                return True
            # LEARNINGWORKS / LEARNING WORKS: the same letters, spaced apart.
            if a and "".join(_word_list(irs_name)) == "".join(_word_list(other)):
                return True
    return False


def _plain(text):
    """Text as the IRS writes it: no accents (MENDEZ) or apostrophes (BNAI)."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"['‘’ʻʼ`]", "", text)


def _word_list(text):
    words = re.sub(r"[^a-z0-9 ]", " ", _plain(text).lower()).split()
    return [w for w in words if w not in IGNORED_WORDS]


def _words(text):
    return set(_word_list(text))


def _covers(a, b):
    """Whether every word of the shorter set matches a word of the other. A
    one-word name must be that word or a misspelling of it: an abbreviation
    counts only beside other matching words (OHIO isn't OHIOHEALTH)."""
    short, long = sorted((a, b), key=len)
    same = _same_spelling if len(short) == 1 else _same_word
    return all(any(same(word, other) for other in long) for word in short)


def _same_spelling(a, b):
    """The same word, give or take a typo (PHILADEPHIA / PHILADELPHIA)."""
    return a == b or (
        a[0] == b[0] and difflib.SequenceMatcher(None, a, b).ratio() >= 0.82
    )


def _same_word(a, b):
    """A word, a misspelling of it, or an abbreviation of it: the same first
    letter, and either the long form's start (MED / MEDICAL) or its letters
    in order with the vowels dropped (CTR / CENTER). Letters in order alone
    isn't enough: COUNTY's are all in COMMUNITY."""
    if _same_spelling(a, b):
        return True
    short, long = sorted((a, b), key=len)
    if len(short) < 2 or short[0] != long[0]:
        return False
    if long.startswith(short):
        return True
    letters = iter(long)
    return sum(c in "aeiou" for c in short[1:]) <= 1 and all(
        letter in letters for letter in short
    )


def _name_query(name):
    """An FTS5 query requiring each word of the name, as the IRS writes names.

    The IRS drops accents and apostrophes (CHILDRENS) and spells out AND, so
    those are removed, other punctuation splits words, and words that vary
    across sources are left out. Every word is quoted, so none is an FTS5
    operator.
    """
    name = _plain(name)
    if not re.fullmatch(r"[A-Za-z0-9 &.,'-]{3,80}", name):
        raise ToolError("name must be 3-80 letters, digits, spaces, or &.,'-")
    words = re.sub(r"[&.,-]", " ", name.replace("'", "")).lower().split()
    words = [w for w in words if w not in IGNORED_WORDS]
    if not words:
        raise ToolError("name must include a distinctive word, e.g. Valley Clinic.")
    return " ".join(f'"{w}"' for w in words)


def _centrals(connection, groups):
    """The central organization of each group exemption number, if on file."""
    groups = sorted(g for g in groups if g and g != irs.NO_GROUP)
    if not groups:
        return {}
    rows = connection.execute(
        f'SELECT EIN, NAME, "GROUP" FROM eo_bmf WHERE "GROUP" IN'
        f" ({','.join('?' * len(groups))}) AND AFFILIATION IN"
        f" ({','.join('?' * len(irs.GROUP_CENTRAL))}) ORDER BY EIN DESC",
        [*groups, *irs.GROUP_CENTRAL],
    ).fetchall()
    return {r["GROUP"]: r for r in rows}  # the lowest EIN wins a tie


def register(mcp):
    mcp.tool(
        irs_search_exempt_orgs,
        name="irs_search_exempt_orgs",
        annotations={
            "title": "Search IRS tax-exempt organizations",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
