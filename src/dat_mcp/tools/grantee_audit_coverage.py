"""grantee_audit_coverage: one grantee's Single Audit coverage, year by year."""

import calendar
import datetime
import re

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac, names
from dat_mcp.sourcetext import SOURCE_NOTICE
from dat_mcp.tools.fac_find_linked_identifiers import SENTINEL, _real
from dat_mcp.tools.fac_get_awards import fac_get_awards
from dat_mcp.tools.grantee_identity_resolution import (
    LISTINGS_NOT_ASKED,
    _audit,
    _covered_by,
    _date,
    _fac_names,
    _irs,
    _irs_findings,
    _registration,
    _sam,
    _sefa_mentions,
    _usual_type,
    _with_mentions,
    entity_block,
    identity_questions,
    link,
    link_notes,
    nine_months_after,
    own_rows,
    registration_notes,
    shared_uei_note,
    start,
    tie_notes,
    unbridged,
    with_asked,
    year_end_notes,
)
from dat_mcp.tools.usaspending_sum_outlays import (
    _entity_fy_start,
    usaspending_sum_outlays,
)

MAX_GAPS_EXPLAINED = 3  # missing years whose prior-year programs are fetched

TOP_PROGRAMS = 5

SEFA_LINES_READ = 1000  # fac_get_awards's most; program totals need every line

# One-time pandemic money. A prior year dominated by it says little about what
# the gap year's spending was.
ONE_TIME_PROGRAMS = {"93.498"}  # Provider Relief Fund and ARP Rural

ONE_TIME_NOTE = (
    "The prior year's largest program, {aln}, was one-time pandemic money, so "
    "that year's total is a poor guide to the gap year. Use "
    "usaspending_sum_outlays for what was drawn in "
    "the gap year, and fac_get_awards on the following year's report for what "
    "carried over."
)

CAVEATS = [
    "This assembles FAC, SAM.gov, and local copies of FAC and IRS data for an "
    "analyst; it makes no determination of whether an audit was required or "
    "late.",
    "due_date is nine months after the fiscal year end, the Uniform Guidance "
    "outside date. Extensions (COVID-era and others) are not applied, and the "
    "accepted date is when FAC accepted the filing, not when it was submitted.",
    "Identifiers are linked by shared UEIs, EINs, and one-character typos in "
    "FAC filings, and by the same name at the same address when none is "
    "shared, which is evidence of the same organization, not proof.",
    "No FAC record is not the same as no audit required; see notes for what "
    "the entity type suggests.",
]

ENTITY_RULES = [
    # (predicate on the entity description, note, never-filers only). Checked
    # in order. The counts in these notes, and in the notes below, are from FAC,
    # SAM.gov, and USAspending data as of November 2026.
    (
        # SAM.gov's "Other Not For Profit Organization" contains "for profit".
        lambda text: bool(re.search(r"(?<!not )for[- ]profit", text)),
        "SAM.gov registers this entity as for-profit. For-profit recipients of "
        "HHS awards follow HHS's own audit requirements for commercial "
        "organizations, which are not filed to FAC, so a missing FAC audit may "
        "be expected.",
        False,
    ),
    (
        lambda text: "federal" in text,
        "This looks like a federal agency. Federal agencies do not file Single "
        "Audits to FAC.",
        False,
    ),
    (
        # Only for an entity that has never filed: one that files its own
        # audits and skips a year is listed by a parent in under 1 in 10 cases.
        lambda text: "higher-ed" in text or "university" in text or "college" in text,
        "Of 209 public universities and colleges with HRSA awards, 115 were "
        "listed as covered by a state or system audit and 80 filed their own. "
        "Search covered entities and report text under the system's name.",
        True,
    ),
    (
        lambda text: "tribal" in text,
        "Tribal entities may withhold their audit reports and notes from public "
        "FAC data, so a filing can exist with little visible.",
        False,
    ),
    (
        # FAC's entity_type says state or local; SAM.gov's "U.S. Government
        # Entity" structure covers federal, state, local, and tribal bodies.
        # Never-filers only, for the same reason as universities.
        lambda text: (
            any(w in text for w in ("state", "local", "county", "city"))
            or "u.s. government entity" in text
        ),
        "This is a government body (FAC entity_type or SAM.gov structure). Among "
        "HRSA recipients with no audit under their own UEI, a parent "
        "government's audit covered 181 of 190 state bodies and 72 of 100 local "
        "ones; 48 of those 72 were under another UEI of the same government, "
        "with no listing. Search fac_search_audits by the government's name. A "
        "federal agency does not file to FAC at all.",
        True,
    ),
]

ROUTE_CAVEAT = (
    "route says how each year's audit was found, or that none was. It is not "
    "a finding that a year is covered, late, or missing an audit that was "
    "required."
)
NOT_LISTED = "parent's audit on file, entity not listed"
NO_PARENT_AUDIT = "parent has no audit on file"
# On the row itself: Haiku read "not listed" as "not covered" and answered No.
NOT_LISTED_MEANS = (
    "This audit doesn't list the entity, and a parent can cover an entity "
    "without listing it, so the missing listing settles nothing either way. "
    "What shows coverage is the report itself: its text or notes naming the "
    "entity as part of what was audited (fac_search_audit_text on "
    "parent_audit), or the entity's awards on its SEFA (fac_get_awards). "
    "The same parent's listing in an earlier year doesn't show it."
)
MAX_FUNDED_YEARS = 6  # fiscal years with_funding fetches when years are asked for
MAX_FUNDED_UEIS = 4  # UEIs with_funding sums for each of those years
NOT_FOUND = "no audit found"


def grantee_audit_coverage(
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
    audit_year: int | list[int] | None = None,
    with_funding: bool = False,
):
    """One grantee's Single Audit coverage: a row per fiscal year and how it was found.

    Use this for any question about what a grantee filed, whether a year is
    covered, or how late its audits were. From a UEI, EIN, or name it
    resolves the grantee's identifiers (the same way
    grantee_identity_resolution does) and returns years: one row per audit
    or parent listing, and one for each fiscal year with neither. Each row's
    route says how it was found:
    - "own audit": filed under the searched identifier.
    - "own audit, under another UEI" (or EIN, or both): filed under an
      identifier linked by a shared UEI or EIN or a one-character typo.
    - "audit linked by name and place": the filer has the same name at the
      same address and no identifier in common; linked_by gives the evidence.
    - "listed as covered in another audit, by UEI" (or EIN): a parent's audit
      lists the entity; listed_by names the parent.
    - "parent's audit on file, entity not listed": no listing that year, but
      the parent that listed it before filed an audit (parent_audit); its
      report text or SEFA notes may still name the entity.
    - "parent has no audit on file": that parent hasn't filed for the year.
    - "no audit found": nothing under any linked identifier and no parent.
    For an entity with no filing and no parent listing, the "no audit found"
    rows are the years asked for, or the three most recent fiscal years past
    their due date. A year whose audit isn't due yet has no such row.
    Audit rows have fy_end_date, report_id, fac_accepted_date, due_date,
    days_late, total_amount_expended, filed_under, and source_url, and when
    the year isn't twelve months, fy_start_date and period_months: use that
    start for spending over the year. Rows with
    no audit have expected_fy_end_date and expected_due_date, and the three
    most recent have prior_year_programs: the five largest programs in the
    audit of the year before, which are that year's figures, not the missing
    year's. A year can have several rows (a resubmission, or an audit and a
    parent listing).

    Returns {"searched", "identity", "entity", "fiscal_year_end", "years",
    "open_questions", "notes", "caveats"}. identity lists the identifiers
    the rows were gathered under and counts the leads and other
    organizations left out; call grantee_identity_resolution for that
    evidence and for the SAM.gov and IRS records. When a name matches more
    than one organization it returns {"candidates"} instead; call again with
    one of their identifiers. Follow up with fac_get_awards,
    fac_get_sefa_notes, and fac_search_audit_text for the detail behind a
    row.

    with_funding adds a usaspending block: outlays drawn from Treasury over
    that entity fiscal year, added up across every UEI the entity has filed
    under (ueis), with each UEI's part (outlays_by_uei) and each award that
    drew money and the UEI it is under. A row with no audit is summed from
    the day after the year before's audit ended to the day before the next
    year's audit began; when that isn't the twelve months to its
    expected_fy_end_date, as after a change of year end, the block's
    period_source says so, and period_months gives a length other than 12.
    A year the audits either side leave no months has period_source alone.
    With audit_year it covers those years (the six latest at most); without,
    the three most recent audits and the three most recent years without
    one. It costs several USAspending requests per UEI per year, so leave it
    off unless the question is about spending.

    Every row has a source_url: the audit's FAC page, the parent's audit or
    listing, or for a year with nothing found, FAC's search page, where the
    absence can be checked.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333, or a list of them.
        name: Words from the auditee's name, used when no identifier is
            known; case and punctuation are ignored.
        state: 2-letter state code to narrow a name search, e.g. CA.
        audit_year: Return only these fiscal years' rows, e.g. 2024 or
            [2023, 2024]. Every year is returned when omitted. A year asked
            for that has no row has a note saying why.
        with_funding: Also fetch USAspending outlays per fiscal year (slower).
    """
    wanted = _years(audit_year)
    assembled = assemble(uei, ein, name, state, wanted)
    if "candidates" in assembled:
        return assembled
    rows = _year_rows(assembled, wanted)
    several = _several_filers(rows, assembled)  # a note on every such year
    if wanted:
        # Before the parents are followed: each one followed is a request to
        # FAC, which has an hourly limit.
        rows = [row for row in rows if row["audit_year"] in wanted]
    _follow_parents(rows, assembled)
    for row in rows:
        # A row with no audit cites where its absence can be checked.
        row.setdefault("source_url", fac.SEARCH_URL)
    filed = bool(assembled["audit_history"])
    entity = assembled["entity"]
    gathered = list(assembled["identifiers"]) if filed else []
    # An EIN found in IRS records by name and place has no filing, so no
    # entry of its own, but parent listings were searched under it.
    gathered += [
        found
        for found in assembled["bridged"]
        if not any(
            entry["ein"] == found["ein"]
            and (entry["uei"] == found["uei"] or not _real({found["uei"]}))
            for entry in gathered
        )
    ]
    funding_notes = []
    if with_funding:
        funding_notes = _add_funding(
            rows,
            _funding_ueis(rows, assembled),
            entity["fiscal_year_end"],
            wanted,
            _real({found["uei"] for found in assembled["bridged"]})
            - set(assembled["searched"]["ueis"]),
            assembled["audit_history"],
        )
    notes = list(assembled["notes"])
    questions = assembled["open_questions"]
    identity_open = len(assembled["irs_questions"]) + len(assembled["bridged"])
    notes += several + _listing_notes(assembled["covered_by"]) + funding_notes
    notes += _no_row_notes(wanted, rows, assembled)
    for lead in assembled["unconfirmed_leads"]:
        for parent in lead.get("listed_as_covered_by", []):
            label = lead["ein"] and f"EIN {lead['ein']}" or f"UEI {lead['uei']}"
            notes.append(
                f"A possible match, {label} ({lead['names'][0]}), is listed as "
                f"covered by {parent['name']} for "
                f"{', '.join(parent['audit_years'])}. It is a lead, not a "
                "confirmed identifier, so years leaves it out: "
                f"{lead['links'][0]}. See grantee_identity_resolution."
            )
    return {
        "searched": assembled["searched"],
        "identity": {
            "identifiers": [
                {k: entry[k] for k in ("uei", "ein", "names", "links")}
                for entry in gathered
            ],
            "sam_registrations": [
                {
                    k: reg.get(k)
                    for k in (
                        "uei",
                        "legal_business_name",
                        "registration_status",
                        "address",
                        "city",
                        "state",
                    )
                }
                for reg in assembled["sam_registrations"].get("registrations", [])
            ],
            "ueis_not_in_sam": assembled["sam_registrations"].get("not_found", []),
            "unconfirmed_leads": len(assembled["unconfirmed_leads"]),
            "other_organizations": len(assembled["other_organizations"]),
            "open_identity_questions": identity_open,
            "detail": (
                "grantee_identity_resolution returns the evidence behind each "
                "identifier, the leads and other organizations left out of "
                "years, and the SAM.gov and IRS records."
            ),
        },
        "entity": {k: v for k, v in entity.items() if k != "fiscal_year_end"},
        "fiscal_year_end": {
            k: entity["fiscal_year_end"][k] for k in ("used", "sources_disagree")
        },
        "years": rows,
        "open_questions": questions,
        "notes": notes,
        "caveats": [ROUTE_CAVEAT, *assembled["caveats"]],
    }


def _spans(years):
    """Years as text, with runs joined: 2016-2021, 2023."""
    years = sorted({int(y) for y in years})
    runs = []
    for year in years:
        if runs and year == runs[-1][1] + 1:
            runs[-1][1] = year
        else:
            runs.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in runs)


def _several_filers(rows, assembled):
    """A note when a year has audits by more than one filer, and filed_as on
    those rows. Linking by name and place can join a large government's
    departmental audits (a county's transit, housing, and county-wide
    audits), and only one of them may hold the grantee's awards."""
    filers = {}
    for row in rows:
        if "filed_under" in row:
            under = row["filed_under"]
            filers.setdefault(row["audit_year"], set()).add(
                (under["uei"], under["ein"])
            )
    years = sorted(year for year, pairs in filers.items() if len(pairs) > 1)
    if not years:
        return []
    filed_as = set()
    for row in rows:
        if "filed_under" in row and row["audit_year"] in years:
            row["filed_as"] = assembled["filed_as"].get(row["report_id"])
            filed_as.add(row["filed_as"])
    listed = sorted(filter(None, filed_as))
    return [
        f"{_spans(years)}: more than one filer's audit is linked for the same "
        f"year ({'; '.join(listed[:6])}{'; and more' if len(listed) > 6 else ''}"
        "). They may be separate audits of parts of one organization, or of "
        "organizations that share an identifier. Use filed_as on each row to "
        "pick the audit the question is about, and its SEFA (fac_get_awards) "
        "to see whose awards it holds."
    ]


def _listing_notes(covered_by):
    """One note per audit filer that lists the entity as covered: the years,
    and whether by UEI or EIN. A parent that lists by UEI one year and by
    EIN the next looks, to a search by UEI, as if it dropped the entity."""
    parents = {}
    for row in covered_by:
        listed = row["listed_identifiers"]
        by = " and ".join(k for k, v in (("UEI", "ueis"), ("EIN", "eins")) if listed[v])
        name = row["listed_by_name"] or row["listed_by_auditee_uei"]
        # By name: a parent's own EIN or UEI can change between years.
        key = names.name_key(name) or name
        parents.setdefault(key, (name, {}))[1].setdefault(by, []).append(
            row["audit_year"]
        )
    notes = []
    for name, by_basis in parents.values():
        parts = [f"by {by} for {_spans(years)}" for by, years in by_basis.items()]
        notes.append(
            f"{name} lists this entity as covered {' and '.join(parts)}. A "
            "listing by EIN isn't found by a search by UEI, or the reverse."
        )
    return notes


def _years(audit_year):
    """The audit_year argument as a set of ints; empty for every year."""
    if audit_year is None:
        return set()
    values = audit_year if isinstance(audit_year, list) else [audit_year]
    try:
        years = {int(value) for value in values}
    except (TypeError, ValueError):
        years = {0}
    if any(not 2000 <= year <= 2100 for year in years):
        raise ToolError("audit_year must be a four-digit year, e.g. 2024.")
    return years


def _year_rows(assembled, wanted=()):
    """assemble()'s audits, parent listings, and missing years as one list,
    latest year first, each with the route that found it. wanted is the
    fiscal years asked for, if any."""
    asked = assembled["searched"]
    filed = bool(assembled["audit_history"])
    entries = assembled["identifiers"] if filed else []
    # The identifiers on the searched identifier's own filings are the
    # entity's too: a UEI search shouldn't call its usual EIN "another EIN".
    own_ueis, own_eins = set(asked["ueis"]), set(asked["eins"])
    links = {}
    for entry in entries:
        links[(entry["uei"], entry["ein"])] = entry["links"]
        if entry["uei"] in asked["ueis"]:
            own_eins.add(entry["ein"])
        if entry["ein"] in asked["eins"]:
            own_ueis.add(entry["uei"])
    bridged_ueis = _real({b["uei"] for b in assembled["bridged"]})
    bridged_eins = _real({b["ein"] for b in assembled["bridged"]})

    rows = []
    for audit in assembled["audit_history"]:
        under = audit["filed_under"]
        other_uei = under["uei"] != SENTINEL and under["uei"] not in own_ueis
        other_ein = under["ein"] not in own_eins
        if (under["uei"] in bridged_ueis and other_uei) or (
            under["ein"] in bridged_eins and other_ein
        ):
            route = "audit linked by name and place"
        elif other_uei and other_ein:
            route = "own audit, under another UEI and EIN"
        elif other_uei:
            route = "own audit, under another UEI"
        elif other_ein:
            route = "own audit, under another EIN"
        else:
            route = "own audit"
        row = {"audit_year": audit["audit_year"], "route": route}
        row.update({k: v for k, v in audit.items() if k != "audit_year"})
        if route != "own audit":
            row["filed_as"] = assembled["filed_as"].get(audit["report_id"])
            row["linked_by"] = links.get((under["uei"], under["ein"]), [])
        rows.append(row)
    for listing in assembled["covered_by"]:
        listed = listing["listed_identifiers"]
        by = " and ".join(k for k, v in (("UEI", "ueis"), ("EIN", "eins")) if listed[v])
        rows.append(
            {
                "audit_year": int(listing["audit_year"]),
                "route": f"listed as covered in another audit, by {by}",
                "report_id": listing["report_id"],
                "listed_by": {
                    "name": listing["listed_by_name"],
                    "uei": listing["listed_by_auditee_uei"],
                    "ein": listing["listed_by_ein"],
                    "fy_end_date": listing["listed_by_fy_end"],
                },
                "listed_identifiers": listed,
                "source_url": listing["source_url"],
            }
        )
    for gap in assembled["years_without_filing"]:
        row = {"audit_year": gap["audit_year"], "route": NOT_FOUND}
        row.update({k: v for k, v in gap.items() if k != "audit_year"})
        rows.append(row)
    block = assembled["entity"]["fiscal_year_end"]
    if not filed and assembled["covered_by"]:
        # An entity with no audit of its own has no history to find gaps in:
        # count the years no parent lists it, from its first listing on.
        listed = {int(c["audit_year"]) for c in assembled["covered_by"]}
        year = min(listed)
        while expected := _expected_year_end(block, year):
            if year > max(listed) and today() <= _date(expected["expected_due_date"]):
                break
            if year not in listed:
                rows.append({"audit_year": year, "route": NOT_FOUND, **expected})
            year += 1
    elif not filed:
        # Nothing filed and no parent listing: the years asked for, or the
        # most recent years past due (DAT's lookback is three years).
        years = sorted((y for y in wanted if y >= FIRST_FAC_YEAR), reverse=True)
        rows += [
            {"audit_year": year, "route": NOT_FOUND, **expected}
            for year, expected in (
                _past_due(block, years) if wanted else _recent_years_past_due(block)
            )
        ]
    order = {"o": 0, "a": 1, "l": 2}  # own audits, linked audits, listings
    rows.sort(key=lambda r: (-(r["audit_year"] or 0), order.get(r["route"][0], 3)))
    return rows


RECENT_YEARS = 3  # rows for an entity with no filing and no parent listing
FIRST_FAC_YEAR = 2016  # the earliest audit year in FAC's public data


def _past_due(block, years):
    """(fiscal year, its expected year end and due date) for those of years
    whose due date has passed. Empty when no source gives a year end."""
    expected = ((year, _expected_year_end(block, year)) for year in years)
    return [
        (year, found)
        for year, found in expected
        if found and _date(found["expected_due_date"]) < today()
    ]


def _no_row_notes(wanted, rows, assembled):
    """Why a fiscal year asked for has no row, a note for each such year: an
    empty answer otherwise reads as nothing being known."""
    block = assembled["entity"]["fiscal_year_end"]
    appears = [a["audit_year"] for a in assembled["audit_history"] if a["audit_year"]]
    appears += [int(c["audit_year"]) for c in assembled["covered_by"]]
    notes = []
    for year in sorted(set(wanted) - {row["audit_year"] for row in rows}):
        expected = _expected_year_end(block, year)
        if year < FIRST_FAC_YEAR:
            why = f"FAC's public data starts with audit year {FIRST_FAC_YEAR}"
        elif not expected:
            why = (
                "no audit was found, and no source gives this entity's fiscal "
                "year end, so when one would be due can't be worked out"
            )
        elif today() <= _date(expected["expected_due_date"]):
            why = (
                f"no audit was found, and one isn't due until "
                f"{expected['expected_due_date']} for a year ending "
                f"{expected['expected_fy_end_date']}"
            )
        elif appears and year < min(appears):
            why = (
                f"no audit was found, and it is before {min(appears)}, the "
                "first year this entity appears in FAC; years with no audit "
                "are counted from there"
            )
        else:
            why = "no audit was found"
        notes.append(f"No row for fiscal year {year}: {why}.")
    return notes


def _recent_years_past_due(block):
    """(fiscal year, its expected year end and due date) for the
    RECENT_YEARS most recent fiscal years whose due date has passed, latest
    first. Empty when no source gives a year end: a year's due date, and so
    whether it has passed, depends on it."""
    found = []
    year = today().year
    while len(found) < RECENT_YEARS and year > today().year - RECENT_YEARS - 2:
        expected = _expected_year_end(block, year)
        if not expected:
            return []
        if _date(expected["expected_due_date"]) < today():
            found.append((year, expected))
        year -= 1
    return found


def _follow_parents(rows, assembled):
    """For each year with no audit found, look for an audit by the parent
    that last listed the entity before it, and set the row's route to
    whether that parent filed.

    A parent drops an entity from its list far more often than from its
    audit (docs/caveat-evidence.md, "Parents that list covered entities"),
    and when the parent itself hasn't filed, the late filer is the parent.
    The parent is followed across its own identifier changes: any UEI or EIN
    on a filing of its own that shares one with the filing that listed the
    entity. FAC doesn't validate identifiers, and a state's EIN is on many
    agencies' audits, so a filing is the parent's under its UEI, or under
    its EIN with its name; under the EIN alone it may be anyone's.
    """
    gaps = [row for row in rows if row["route"] == NOT_FOUND]
    listings = assembled["covered_by"]
    if not gaps or not listings:
        return
    parents = {}  # the last listing before a gap year -> the gap rows after it
    for gap in gaps:
        earlier = [c for c in listings if int(c["audit_year"]) < gap["audit_year"]]
        if earlier:
            last = max(earlier, key=lambda c: int(c["audit_year"]))
            parents.setdefault(last["report_id"], (last, []))[1].append(gap)
    select = (
        "report_id, audit_year, auditee_name, auditee_uei, auditee_ein,"
        " fy_end_date, fac_accepted_date"
    )
    for last, after in parents.values():
        ueis = _real({last["listed_by_auditee_uei"]})
        eins = _real({last["listed_by_ein"]})
        key = names.name_key(last["listed_by_name"] or "")

        def theirs(row, ueis=ueis, eins=eins, key=key):
            if row["auditee_uei"] in ueis:
                return True
            if row["auditee_ein"] not in eins:
                return False
            if not key:  # no name to check: the EIN only when it's all there is
                return not ueis
            return names.name_key(row["auditee_name"] or "") == key

        years = [str(gap["audit_year"]) for gap in after]
        with db.connect() as connection:
            own = [
                row
                for row in connection.execute(
                    "SELECT DISTINCT auditee_uei, auditee_ein, auditee_name FROM"
                    " general WHERE"
                    f" auditee_uei IN ({','.join('?' * len(ueis))})"
                    f" OR auditee_ein IN ({','.join('?' * len(eins))})",
                    [*ueis, *eins],
                ).fetchall()
                if theirs(row)
            ]
            for row in own:
                ueis |= _real({row["auditee_uei"]})
                eins |= _real({row["auditee_ein"]})
            found = connection.execute(
                f"SELECT {select} FROM general WHERE audit_year IN"
                f" ({','.join('?' * len(years))}) AND (auditee_uei IN"
                f" ({','.join('?' * len(ueis))}) OR auditee_ein IN"
                f" ({','.join('?' * len(eins))})) ORDER BY fac_accepted_date",
                [*years, *ueis, *eins],
            ).fetchall()
        audits = {int(row["audit_year"]): dict(row) for row in found if theirs(row)}
        missing = [y for y in years if int(y) not in audits]
        checked_live = True
        if missing:
            # The snapshot is dated: ask the live API before saying the
            # parent hasn't filed.
            params = {
                "select": select.replace(" ", ""),
                "audit_year": fac.one_of(missing),
            }
            try:
                live = []
                if ueis or eins:
                    live = fac.get("general", {**params, **fac.filed_under(ueis, eins)})
            except ToolError:
                live, checked_live = [], False
            for row in live:
                if theirs(row):
                    audits[int(row["audit_year"])] = row
        name = last["listed_by_name"] or last["listed_by_auditee_uei"]
        for gap in after:
            audit = audits.get(gap["audit_year"])
            if audit:
                gap["route"] = NOT_LISTED
                gap["parent_audit"] = {
                    "report_id": audit["report_id"],
                    "auditee_name": audit["auditee_name"],
                    "filed_under": {
                        "uei": audit["auditee_uei"],
                        "ein": audit["auditee_ein"],
                    },
                    "fy_end_date": audit["fy_end_date"],
                    "fac_accepted_date": audit["fac_accepted_date"],
                    "last_listed_this_entity": int(last["audit_year"]),
                    "source_url": fac.summary_url(audit["report_id"]),
                }
                gap["what_this_means"] = NOT_LISTED_MEANS
                gap["source_url"] = gap["parent_audit"]["source_url"]
            else:
                gap["route"] = NO_PARENT_AUDIT
                gap["parent"] = {
                    "name": name,
                    "ueis": sorted(ueis),
                    "eins": sorted(eins),
                    "last_listed_this_entity": int(last["audit_year"]),
                    "source_url": last["source_url"],
                }
                gap["source_url"] = last["source_url"]
                if not checked_live:
                    gap["parent"]["checked"] = (
                        "the local FAC copy only; the live check failed"
                    )


def assemble(uei=None, ein=None, name=None, state=None, wanted=()):
    """Everything the tools know about one grantee, in one dict: its linked
    identifiers, each audit with its due date, the years with no filing and
    the programs of the year before, parents that list it, its SAM.gov and
    IRS records, notes, and open questions. grantee_audit_coverage lays this
    out by fiscal year; the tests check it directly. notes, open_questions,
    and caveats are worded for that tool's answer; what the IRS records add
    is grantee_identity_resolution's to say, and is kept apart in
    irs_notes, irs_questions, and irs_caveats. A name that doesn't
    settle on one organization returns {"candidates"} instead. wanted is the
    fiscal years asked for, if any: only their prior-year programs are
    fetched."""
    uei, ein, candidates = start(uei, ein, name, state)
    if candidates:
        return candidates
    linked, found = link(uei, ein)
    groups = linked["identifiers"]
    if not groups or linked["leads_only"]:
        return _never_filed(linked, found)

    # Leads for a searched identifier that found nothing may be another
    # organization: list them, but keep them out of the history.
    leads = [g for g in groups if g["lead"]]
    groups = [g for g in groups if not g["lead"]]
    ueis = sorted(_real({g["uei"] for g in groups}))
    eins = sorted(_real({g["ein"] for g in groups}))
    with db.connect() as connection:
        rows = own_rows(connection, ueis, eins, found)
        snapshot_caveat = db.snapshot_caveat(connection)

    history = [_audit(row) for row in rows]
    fac_type = rows[-1]["entity_type"] if rows else None
    covered_by, covered_truncated, covered_error = _covered_by(ueis, eins)
    gaps, live_error, filed_since = _gaps(
        history,
        ueis,
        eins,
        {int(c["audit_year"]) for c in covered_by},
        {tuple(pair) for pair in found["foreign"]},
        wanted=wanted,
    )
    registrations = _sam(with_asked(ueis, found))
    irs_records, irs_caveat = _irs(eins, _fac_names(rows))
    entity, notes = _entity(
        history, fac_type, registrations, gaps, covered_by, irs_records, covered_error
    )
    for gap in gaps:
        gap.update(_expected_year_end(entity["fiscal_year_end"], gap["audit_year"]))
    shared = unbridged(eins, found)
    open_questions = _open_questions(
        gaps, registrations, entity, shared, covered_by, _filed_ueis(history)
    )
    mentions, more_mentions = _sefa_mentions(irs_records)
    irs_notes, irs_questions = _irs_findings(
        irs_records,
        groups,
        history,
        covered_by,
        _usual_type(rows),
        mentions,
        more_mentions,
    )
    if live_error:
        notes.insert(
            0,
            f"FAC's live API couldn't be asked ({live_error}), so the audits here "
            "are those in the local copy alone: one filed since it was "
            "downloaded isn't shown, and a year with no audit may have one by "
            "now. fac_search_audits asks the live API.",
        )
    if covered_error:
        notes.insert(0, LISTINGS_NOT_ASKED.format(error=covered_error))
    if linked["not_found"]:
        notes.insert(
            0,
            f"Nothing has filed under {', '.join(linked['not_found'])}. Any "
            "one-character neighbors are unconfirmed leads, kept out of years "
            "(grantee_identity_resolution lists them); confirm one with "
            "sam_get_registration before treating it as this entity.",
        )
    if covered_truncated and gaps:
        notes.insert(
            0,
            "The parent listings were cut off at the search limit, so some "
            'years with "no audit found" may be covered by a parent not shown. '
            "Check them with fac_search_covered_entities before reading them as "
            "gaps.",
        )
    if len(shared) > 1:
        notes.insert(0, shared_uei_note(shared, groups))
    notes[:0] = _linking_notes(found) + tie_notes(found, covered_by)

    return {
        "searched": found["asked"],
        "identifiers": _with_mentions(
            [
                {k: g[k] for k in ("uei", "ein", "names", "links", "audit_years")}
                for g in groups
            ],
            mentions,
            more_mentions,
        ),
        **({"source_notice": SOURCE_NOTICE} if mentions else {}),
        "unconfirmed_leads": [
            {k: g[k] for k in ("uei", "ein", "names", "links", "audit_years")}
            for g in leads
        ]
        + found["leads"],
        "other_organizations": found["other_organizations"],
        "audit_history": history,
        "years_without_filing": gaps,
        "covered_by": covered_by,
        "sam_registrations": registrations,
        "irs_records": irs_records,
        "entity": entity,
        "open_questions": open_questions,
        "notes": notes,
        "caveats": [*CAVEATS, snapshot_caveat],
        "bridged": found["bridged"],
        # The name on each filing, those filed since the local copy included.
        "filed_as": {
            **{row["report_id"]: row["auditee_name"] for row in rows},
            **filed_since,
        },
        "irs_notes": irs_notes,
        "irs_questions": irs_questions,
        "irs_caveats": irs_caveat,
    }


def _no_ein_text(found):
    """Why an entity with no FAC filing has no EIN: what the identity step's
    own IRS name search did, so the agent doesn't repeat it unawares."""
    with db.connect() as connection:
        loaded = {row["table_name"] for row in db.snapshot(connection)}
    if found.get("irs_skipped"):
        return (
            "No EIN is known for this entity, and IRS records weren't searched "
            f"by its name: {found['irs_skipped']}."
        )
    if found.get("skipped") or "eo_bmf" not in loaded:
        return (
            "No EIN is known for this entity, and IRS records weren't searched "
            "by its name."
        )
    leads = (
        "; the closest are in unconfirmed_leads"
        if any(not lead.get("uei") for lead in found.get("leads", []))
        else ""
    )
    return (
        "No EIN is known for this entity: nothing has filed to FAC under it, and "
        "searching IRS records by its names found none with the same name in the "
        f"same place{leads}."
    )


def _linking_notes(found):
    """Notes on filings joined by name and place, or set aside as another
    organization's: the two things the identity step does beyond shared
    identifiers."""
    return link_notes({"not_found": []}, found, True, coverage=True)


def _funding_ueis(rows, assembled):
    """The UEIs whose USAspending awards are the entity's: those of its
    filings, latest first, then the rest of its linked and searched ones. An
    entity with no filing has only the searched ones; its one-character
    neighbors are leads, possibly other organizations."""
    ueis = [row["filed_under"]["uei"] for row in rows if "filed_under" in row]
    if assembled["audit_history"]:
        ueis += sorted(entry["uei"] for entry in assembled["identifiers"])
    ueis += assembled["searched"]["ueis"]
    return [u for u in dict.fromkeys(ueis) if u in _real({u})]


def _add_funding(rows, ueis, block, years=(), by_place=(), history=()):
    """Add a usaspending block to rows: outlays over the row's fiscal year,
    summed across the entity's UEIs, since awards can sit under any of them.
    by_place are the UEIs linked by name and place alone: they are summed,
    and a note says which they are.
    With years asked for, every row of the MAX_FUNDED_YEARS latest; with
    none, the three most recent audits and the three most recent years
    without one. A row with no year end of its own (a parent's listing) uses
    the expected one, bounded by the entity's audits either side of it in
    history (see _between_audits). Returns notes on what wasn't summed."""
    if not ueis:
        return [
            "with_funding added nothing: USAspending is searched by UEI and "
            "none is known for this entity. usaspending_search_awards takes a "
            "name."
        ]
    notes = []
    if len(ueis) > MAX_FUNDED_UEIS:
        notes.append(
            f"with_funding summed {MAX_FUNDED_UEIS} of this entity's "
            f"{len(ueis)} UEIs, those of its latest filings first. Not summed: "
            f"{', '.join(ueis[MAX_FUNDED_UEIS:])}; usaspending_sum_outlays "
            "takes one at a time."
        )
        ueis = ueis[:MAX_FUNDED_UEIS]
    if weak := [uei for uei in ueis if uei in by_place]:
        # A county's transit and housing filers can be joined this way, and
        # their awards aren't the health department's.
        notes.append(
            f"with_funding's totals include UEI {', '.join(weak)}, linked to "
            "this entity by name and place alone, with no identifier in "
            "common. If its awards are another organization's, "
            "outlays_by_uei on each usaspending block gives the total "
            "without it."
        )
    audits = [row for row in rows if "filed_under" in row]
    # A listing in a year with an own audit covers the same months as the audit.
    audited = {row["audit_year"] for row in audits}
    others = [
        row
        for row in rows
        if "filed_under" not in row and row["audit_year"] not in audited
    ]
    if years:
        latest = sorted({row["audit_year"] for row in rows}, reverse=True)
        targets = [
            row
            for row in audits + others
            if row["audit_year"] in latest[:MAX_FUNDED_YEARS]
        ]
    else:  # rows are latest first
        targets = audits[:3] + others[:MAX_GAPS_EXPLAINED]
    own = _own_dates(history)
    fetched = {}
    for row in targets:
        # A year that isn't twelve months is summed from its own start.
        start = row.get("fy_start_date")
        end = (
            row.get("fy_end_date")
            or row.get("expected_fy_end_date")
            or _expected_year_end(block, row["audit_year"]).get("expected_fy_end_date")
        )
        if not end:
            continue
        window = {}
        if "filed_under" not in row:
            start, end, window = _between_audits(row["audit_year"], end, own)
            if not end:  # the audits either side leave it no months
                row["usaspending"] = window
                continue
        if (start, end) not in fetched:
            fetched[start, end] = _outlays(ueis, start, end)
        row["usaspending"] = {**fetched[start, end], **window}
    return notes


ONE_DAY = datetime.timedelta(days=1)


def _own_dates(history):
    """{audit year: (first day, last day)} of the entity's own audits, for
    the years whose filings agree on them. An audit row gives fy_start_date
    only when the year isn't twelve months."""
    dates = {}
    for audit in history:
        end = _date(audit["fy_end_date"])
        if audit["audit_year"] and end:
            first = _date(audit.get("fy_start_date")) or _entity_fy_start(end)
            dates.setdefault(audit["audit_year"], set()).add((first, end))
    return {year: pair for year, (pair, *more) in dates.items() if not more}


def _between_audits(year, expected_end, own):
    """A year with no audit of its own: (period_start or None for the twelve
    months to the end, the end or None for no months, what to add to its
    usaspending block).

    It runs from the day after the year before's audit ended to the day
    before the next year's audit began, where those are on file, and over
    the twelve months to expected_fy_end_date otherwise. The expected year
    end comes from the latest audit, so after a change of year end it can be
    months off, and the twelve months to it would leave months out or count
    them in two years. A date more than a year from the expected one is a
    data error and isn't used. When the audits either side agree with the
    expected year, nothing is added."""
    expected = _date(expected_end)
    start, end = _entity_fy_start(expected), expected
    starts = "twelve months before expected_fy_end_date"
    ends = "expected_fy_end_date"
    if year - 1 in own:
        day_after = own[year - 1][1] + ONE_DAY
        if abs((day_after - start).days) <= 366:
            start, starts = day_after, f"the day after FY{year - 1}'s audit ended"
    if year + 1 in own:
        day_before = own[year + 1][0] - ONE_DAY
        if abs((day_before - end).days) <= 366:
            end, ends = day_before, f"the day before FY{year + 1}'s audit began"
    if (start, end) == (_entity_fy_start(expected), expected):
        return None, expected_end, {}
    if start > end:
        # An 18-month year after a change of year end, for one, leaves the
        # year it absorbed with no months.
        none = (
            f"none: {starts} ({start}) is after {ends} ({end}), so no months "
            f"fall in FY{year} and nothing was summed"
        )
        return None, None, {"period_source": none}
    window = {"period_source": f"{starts} to {ends}"}
    twelve_months = start == _entity_fy_start(end)
    if not twelve_months:
        window["period_months"] = round((end - start).days / 30.44)
    return (None if twelve_months else start.isoformat()), end.isoformat(), window


def _outlays(ueis, start, end):
    """One fiscal year's usaspending block: each UEI's outlays, added up,
    and each UEI's part of the total."""
    total, awards, seen = 0.0, [], set()
    by_uei = dict.fromkeys(ueis, 0.0)
    more = truncated = False
    period = None
    for uei in ueis:
        try:
            spending = usaspending_sum_outlays(
                fiscal_year_end=end,
                uei=uei,
                max_awards=25,
                **({"period_start": start} if start else {}),
            )
        except ToolError as error:
            # Part of a total reads as the total, so give none.
            return {"error": f"{uei}: {error}", "ueis": ueis}
        for award in spending["awards"]:
            if award["award_id"] in seen:
                continue
            seen.add(award["award_id"])
            total += award["outlays_in_period"] or 0
            by_uei[uei] += award["outlays_in_period"] or 0
            # Which awards drew money, not how many were examined: agents
            # read "awards_examined: 2" as two awards with drawdowns.
            if award["outlays_in_period"]:
                awards.append(
                    {
                        "award_id": award["award_id"],
                        "outlays_in_period": award["outlays_in_period"],
                        "uei": uei,
                        "source_url": award["source_url"],
                    }
                )
        more = more or spending["more_awards_available"]
        truncated = truncated or spending["any_funding_history_truncated"]
        period = spending["period"]["description"]
    return {
        "total_outlays_in_period": round(total, 2),
        "outlays_by_uei": {uei: round(part, 2) for uei, part in by_uei.items()},
        "awards_with_outlays": awards,
        "more_awards_available": more,
        "any_funding_history_truncated": truncated,
        "ueis": ueis,
        "period": period,
    }


def _filed_ueis(history):
    return {audit["filed_under"]["uei"] for audit in history}


def _open_questions(gaps, registrations, entity, eins, covered_by, filed):
    """What the evidence leaves for the analyst to settle. Questions, not verdicts."""
    questions = []
    for gap in gaps:
        questions.append(
            f"Was a Single Audit required for fiscal year {gap['audit_year']}? "
            "No own filing and no parent listing was found for it."
        )
    questions += identity_questions(registrations, entity, eins, filed)
    if covered_by and gaps:
        questions.append(
            "Does the parent that listed this entity in earlier years still cover it "
            "in the gap years? Its SEFA notes or report text would say."
        )
    return questions


def _never_filed(linked, found):
    """An entity with no filing of its own in the local FAC copy: still
    check for parents that list it and what its SAM.gov entity type implies.
    These are the grantees most likely to be covered by someone else's audit.
    The copy is dated, so FAC's live API is asked too: a first audit filed
    since the download is the entity's history."""
    ueis, eins = linked["searched"]["ueis"], linked["searched"]["eins"]
    covered_by, covered_truncated, covered_error = _covered_by(ueis, eins)
    history = []
    live_error, live = _add_filed_since(history, ueis, eins, FIRST_FAC_YEAR, ())
    gaps = []
    if history:
        covered_years = {int(c["audit_year"]) for c in covered_by}
        gaps = _gaps(history, ueis, eins, covered_years, ask_live=False)[0]
    newest = max(live, key=lambda row: row.get("fy_end_date") or "", default={})
    registrations = _sam(ueis)
    irs_records, irs_caveat = _irs(eins, {})
    entity, notes = _entity(
        history,
        newest.get("entity_type"),
        registrations,
        gaps,
        covered_by,
        irs_records,
        covered_error,
    )
    for gap in gaps:
        gap.update(_expected_year_end(entity["fiscal_year_end"], gap["audit_year"]))
    irs_notes, irs_questions = _irs_findings(irs_records, [], history, covered_by)
    neighbors = (
        "Any one-character neighbors are unconfirmed leads, each possibly "
        "another organization."
    )
    if history:
        years = _spans(a["audit_year"] for a in history if a["audit_year"])
        notes.insert(
            0,
            "The local FAC copy has no filing under these identifiers, but "
            f"FAC's live API has {len(history)} filed since it was downloaded "
            f"(fiscal year {years}), marked filed_since_snapshot. Identifiers "
            "are linked from the local copy, so the only ones here are those "
            "on these filings: one linked by a typo, or by name and place, "
            "isn't found until the copy is rebuilt.",
        )
    elif live_error:
        notes.insert(
            0,
            "Nothing has filed to FAC under these identifiers in the local "
            f"copy, and FAC's live API couldn't be asked ({live_error}) whether "
            "anything has since it was downloaded. fac_search_audits asks the "
            f"live API. {neighbors}",
        )
    else:
        notes.insert(
            0, f"Nothing has filed to FAC under these identifiers. {neighbors}"
        )
    filers = {}  # (UEI, EIN) -> the live API's rows under the pair
    for row in live:
        filers.setdefault((row["auditee_uei"], row["auditee_ein"]), []).append(row)
    if covered_error:
        # Not "no parent lists it": the listings weren't read.
        notes.append(LISTINGS_NOT_ASKED.format(error=covered_error))
    elif covered_by:
        years = sorted({c["audit_year"] for c in covered_by}, reverse=True)
        notes.append(
            f"Parent audits list this entity as covered ({', '.join(years)}); see "
            "years. A parent can list an entity by EIN only, so search by "
            "both when the EIN is known."
        )
        if covered_truncated:
            notes.append(
                "The parent listings were cut off at the search limit; more "
                "parents may list this entity."
            )
    elif eins:
        notes.append(
            "No parent audit lists these identifiers. A parent may list it by "
            "EIN only, and 67% of audits filed as '... and subsidiaries' or "
            "'... and affiliates' list no covered entities: search by EIN if "
            "known, then fac_search_audit_text with its name."
        )
    else:
        # S19 by UEI: Holdings lists Round Rock by EIN only, which a UEI-only
        # search can't find, so don't say no parent lists it.
        label = "UEI" if len(ueis) == 1 else "UEIs"
        notes.append(
            f"No parent audit lists {label} {', '.join(ueis)}. "
            + _no_ein_text(found)
            + " A parent can list an entity by EIN only, so listings by EIN "
            "weren't searched. SAM.gov's legal name can be abbreviated or cut off "
            "(MC for Medical Center, a last word ending mid-word), so "
            "irs_search_exempt_orgs with its distinctive words and state may "
            "still find the EIN; then fac_search_covered_entities(ein=...). 67% "
            "of audits filed as '... and subsidiaries' or '... and affiliates' "
            "list no covered entities, so also try fac_search_audit_text with "
            "its name."
        )
    notes[:0] = _linking_notes(found) + tie_notes(found, covered_by)
    return {
        "searched": found["asked"],
        "identifiers": [
            {
                "uei": uei,
                "ein": ein,
                "names": sorted({r["auditee_name"] for r in rows if r["auditee_name"]}),
                "links": ["filed under since the local FAC copy was downloaded"],
                "audit_years": sorted({str(r["audit_year"]) for r in rows}),
            }
            for (uei, ein), rows in filers.items()
        ]
        if history
        else linked["identifiers"],
        # With filings found, the one-character neighbors move out of
        # identifiers: those are the filings' own.
        "unconfirmed_leads": (linked["identifiers"] if history else [])
        + found["leads"],
        "other_organizations": [],
        # Other audits' SEFA notes naming a searched EIN, for any searched EIN:
        # for an entity with no audit of its own, a parent's note listing it
        # is a coverage lead (517 IRS-listed organizations that never filed to
        # FAC are named in another audit's notes; 64 are on no covered list).
        "named_in_sefa_notes": linked["named_in_sefa_notes"],
        **(
            {
                "source_notice": SOURCE_NOTICE,
                "notes_more_available": linked["notes_more_available"],
            }
            if linked["named_in_sefa_notes"]
            else {}
        ),
        "audit_history": history,
        "years_without_filing": gaps,
        "covered_by": covered_by,
        "sam_registrations": registrations,
        "irs_records": irs_records,
        "entity": entity,
        "open_questions": [
            *(
                _open_questions(
                    gaps, registrations, entity, [], covered_by, _filed_ueis(history)
                )
                if history
                else [
                    "Has this organization ever filed to FAC under another "
                    "identifier, or is it covered by a parent's audit?"
                ]
            ),
        ],
        "notes": notes,
        # Drop the linked-identifier caveat that points elsewhere for parent
        # listings: covered_by above is that search.
        "caveats": [
            *CAVEATS,
            *(c for c in linked["caveats"] if "covered_entities, not here" not in c),
        ],
        "bridged": found["bridged"],
        "filed_as": {row["report_id"]: row.get("auditee_name") for row in live},
        "irs_notes": irs_notes,
        "irs_questions": irs_questions,
        "irs_caveats": irs_caveat,
    }


def _gaps(history, ueis, eins, covered_years, foreign=(), ask_live=True, wanted=()):
    """(Fiscal years with neither an own filing nor a parent listing, why
    FAC's live API couldn't be asked or None, {report id: the name filed
    under} for the filings the live API added). ask_live is False when the
    caller has already asked. With wanted, only those years get the prior
    year's programs. The years run from the first
    one the entity appears in FAC either way, after adding to history what
    the live API has that the local copy doesn't, and each comes with the
    programs the entity spent on in the year before."""
    years = {a["audit_year"] for a in history if a["audit_year"]}
    if not years:
        return [], None, {}
    first = min(years | covered_years)
    live_error, added = None, []
    if ask_live:
        live_error, added = _add_filed_since(history, ueis, eins, first, foreign)
    filed_since = {row["report_id"]: row.get("auditee_name") for row in added}
    years = {a["audit_year"] for a in history if a["audit_year"]}
    expected_through = max(years)
    latest_end = max(
        filter(None, (_date(a["fy_end_date"]) for a in history)), default=None
    )
    if latest_end:
        # Every year since the latest filing whose due date has passed, not just
        # the next one: a filer two years behind is missing both.
        next_fy_end = _one_year_later(latest_end)
        while today() > nine_months_after(next_fy_end):
            expected_through = max(expected_through, next_fy_end.year)
            next_fy_end = _one_year_later(next_fy_end)
    missing = sorted(
        set(range(first, expected_through + 1)) - years - covered_years, reverse=True
    )

    by_year = {a["audit_year"]: a for a in history}
    gaps = []
    explained = 0  # of the years asked for, or of all of them
    for year in missing:
        gap = {
            "audit_year": year,
            "prior_year_report_id": None,
            "prior_year_programs": [],
        }
        prior = by_year.get(year - 1)
        explain = explained < MAX_GAPS_EXPLAINED and (not wanted or year in wanted)
        explained += not wanted or year in wanted
        if prior and explain:
            # The prior year's own year and dates, next to its report id: with
            # two gap years in a row, agents took FY2023's programs, listed
            # under the FY2024 gap, for an FY2024 SEFA.
            gap = {
                "audit_year": year,
                "prior_year_report_id": prior["report_id"],
                "prior_year_audit_year": prior["audit_year"],
                "prior_year_fy_end_date": prior["fy_end_date"],
                "prior_year_total_expended": prior["total_amount_expended"],
            }
            try:
                sefa = fac_get_awards(prior["report_id"], limit=SEFA_LINES_READ)
            except ToolError as error:
                gap["prior_year_programs_error"] = str(error)
                sefa = {"awards": [], "more_available": False}
            programs = _programs(sefa["awards"])
            gap["prior_year_programs"] = programs[:TOP_PROGRAMS]
            if rest := programs[TOP_PROGRAMS:]:
                gap["prior_year_programs_not_shown"] = {
                    "programs": len(rest),
                    "amount_expended": sum(p["amount_expended"] for p in rest),
                }
            if sefa["more_available"]:
                gap["prior_year_programs_incomplete"] = (
                    f"Only the SEFA's {SEFA_LINES_READ} largest lines were read, so "
                    "program totals may be low. fac_get_awards with program= "
                    "lists one program's lines."
                )
            if programs and programs[0]["aln"] in ONE_TIME_PROGRAMS:
                gap["note"] = ONE_TIME_NOTE.format(aln=programs[0]["aln"])
        gaps.append(gap)
    return gaps, live_error, filed_since


def _add_filed_since(history, ueis, eins, first, foreign):
    """Add to history the filings FAC's live API has under the identifiers
    that the local copy, which is dated, doesn't: an audit of a gap year, of
    a year that isn't due yet, or a resubmission. Asks about every year from
    first on, not only the gap years: an on-time filer's newest audit is for
    a year not yet counted as missing. Returns (why the API couldn't be
    asked, or None; the API's rows for those added)."""
    params = {
        "select": (
            "report_id,audit_year,auditee_name,auditee_uei,auditee_ein,entity_type,"
            "fy_start_date,fy_end_date,fac_accepted_date,total_amount_expended,"
            "resubmission_version"
        ),
        "audit_year": fac.one_of([str(y) for y in range(first, today().year + 1)]),
    }
    try:
        live = fac.get("general", {**params, **fac.filed_under(ueis, eins)})
    except ToolError as error:
        return str(error), []
    held = {a["report_id"] for a in history}
    added = []
    for row in live:
        if row["report_id"] in held:
            continue
        if (row["auditee_uei"], row["auditee_ein"]) in foreign:
            continue  # another organization's filing under this entity's EIN
        history.append(_audit(row, filed_since_snapshot=True))
        added.append(row)
    history.sort(key=lambda a: (a["fy_end_date"] or "", a["fac_accepted_date"] or ""))
    return None, added


def _programs(lines):
    """An audit's SEFA lines added up by assistance listing, largest first.

    A SEFA lists each award on its own line, and one program often has
    several (each grant passed through a state, for one), so a line is not a
    program. The sum per listing is what FAC reports as federal_program_total.
    A program is direct or not when all its lines agree; when they don't, it
    gives the amount each way instead.
    """
    programs, direct_lines, direct_amounts = {}, {}, {}
    for line in lines:  # largest line first, so a program takes its name
        aln, amount = line["aln"], line["amount_expended"] or 0
        program = programs.setdefault(
            aln,
            {
                "aln": aln,
                "program": line["federal_program_name"],
                "amount_expended": 0,
                "sefa_lines": 0,
            },
        )
        program["amount_expended"] += amount
        program["sefa_lines"] += 1
        if line["is_direct"] == "Y":
            direct_lines[aln] = direct_lines.get(aln, 0) + 1
            direct_amounts[aln] = direct_amounts.get(aln, 0) + amount
    for aln, program in programs.items():
        direct = direct_lines.get(aln, 0)
        if direct in (0, program["sefa_lines"]):
            program["direct"] = direct > 0
        else:
            program["direct_expended"] = direct_amounts[aln]
            program["pass_through_expended"] = (
                program["amount_expended"] - direct_amounts[aln]
            )
    return sorted(programs.values(), key=lambda p: -p["amount_expended"])


def _entity(
    history, fac_type, registrations, gaps, covered_by, irs_records, covered_error=None
):
    """What FAC, SAM.gov, and the IRS say the entity is, and what that implies.
    covered_error is why the parent listings couldn't be read, if they
    couldn't: then no listing isn't said to mean no parent lists it."""
    regs = registrations.get("registrations", [])
    reg = _registration(regs) or (regs[0] if regs else {})
    sam_desc = (
        " / ".join(
            filter(None, (reg.get("entity_structure"), reg.get("profit_structure")))
        )
        or None
    )
    entity = entity_block(fac_type, registrations, history, covered_by, irs_records)

    notes = []
    text = " ".join(filter(None, (fac_type, sam_desc))).lower()
    if not history or gaps:
        for applies, note, never_filed_only in ENTITY_RULES:
            if text and applies(text):
                if not (never_filed_only and history):
                    notes.append(note)
                break
    notes += year_end_notes(entity["fiscal_year_end"])
    notes += registration_notes(registrations, _filed_ueis(history))
    late = [a for a in history if a["days_late"]]
    if late:
        worst = max(late, key=lambda a: a["days_late"])
        notes.append(
            f"{len(late)} of {len(history)} filings were accepted after the nine-month "
            f"date; the latest by {worst['days_late']} days (audit year "
            f"{worst['audit_year']})."
        )
    if gaps and covered_by:
        # Gap years have no listing by construction; point at the parent that
        # listed the entity most recently before each (S17: the hospital
        # dropped off Covenant's list but stayed in its audit).
        for gap in gaps:
            earlier = [
                c for c in covered_by if int(c["audit_year"]) < gap["audit_year"]
            ]
            if earlier:
                last = max(earlier, key=lambda c: int(c["audit_year"]))
                notes.append(
                    f"No own filing and no parent listing for {gap['audit_year']}. "
                    f"{last['listed_by_name'] or last['listed_by_auditee_uei']} listed "
                    f"this entity as covered in {last['audit_year']}. Of 37 entities "
                    "dropped from a parent's list, 33 were still in the parent's "
                    "next audit report and 3 had been sold or deconsolidated. "
                    f"Check that parent's {gap['audit_year']} audit with "
                    f"fac_search_audits(uei={last['listed_by_auditee_uei']}) and "
                    "search its report text for this entity's name."
                )
    if gaps and not covered_by and not covered_error:
        notes.append(
            "No parent audit lists this entity as covered. Of filers that skipped "
            "a year, 55% had spent under $1.5 million the year before and under "
            "1% were listed by a parent that year. Neither year's total shows "
            "the gap year's spending: usaspending_sum_outlays gives what was "
            "drawn in it. A parent can cover it without listing it: search "
            "fac_search_audit_text for the names in identifiers."
        )
    return entity, notes


def _expected_year_end(block, year):
    """For a year with no audit: its year end and due date under YEAR_END_RULE.
    An IRS month alone gives the month's last day."""
    used = block["used"]
    if not used:
        return {}
    value = used["value"]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        month, day = int(value[5:7]), int(value[8:10])
    elif re.fullmatch(r"\d{2}/\d{2}", value):
        month, day = int(value[:2]), int(value[3:])
    else:
        month = list(calendar.month_name).index(value)
        day = 31
    end = datetime.date(year, month, min(day, calendar.monthrange(year, month)[1]))
    return {
        "expected_fy_end_date": end.isoformat(),
        "expected_due_date": nine_months_after(end).isoformat(),
        "fy_end_source": used["source"],
    }


def today():
    return datetime.date.today()


def _one_year_later(day):
    """The same year end a year on; 29 February becomes 28 February."""
    last = calendar.monthrange(day.year + 1, day.month)[1]
    return datetime.date(day.year + 1, day.month, min(day.day, last))


def register(mcp):
    mcp.tool(
        grantee_audit_coverage,
        name="grantee_audit_coverage",
        annotations={
            "title": "A grantee's Single Audit coverage by fiscal year",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
    )
