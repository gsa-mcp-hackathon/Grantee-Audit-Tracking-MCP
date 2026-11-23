"""irs_get_related_orgs: related organizations from Form 990 Schedule R."""

import re

from fastmcp.exceptions import ToolError

from dat_mcp import db, identifiers, irs
from dat_mcp.tools.irs_search_exempt_orgs import same_name

CAVEATS = [
    "Only full Form 990 filers report Schedule R. Organizations filing 990-EZ, "
    "990-PF, or 990-N, government bodies, and for-profits report none.",
    "Group-exemption members need not be listed: a central organization and "
    "its subordinates may leave each other out. irs_search_exempt_orgs shows "
    "an organization's group exemption and its central organization.",
    "Schedule R is the filer's own account: lists change from year to year, so "
    "an organization missing from one isn't proof it's unrelated, and EINs are "
    "as typed. Check a related EIN with irs_search_exempt_orgs before "
    "searching FAC by it.",
    f"Stored returns are those the IRS processed from {irs.FIRST_INDEX_YEAR} "
    f"on, about tax year {irs.FIRST_INDEX_YEAR - 2} on. The IRS publishes "
    "e-filed returns it processed from 2019 on, about tax year 2017 on, and "
    "none earlier. A return or a listing missing for an earlier year wasn't "
    "loaded or isn't published: it doesn't show the organizations were "
    "unrelated then.",
]

NOT_STORED = (
    "No return is stored for {eins}: returns are fetched only for selected "
    "organizations, so this doesn't mean none was filed. The IRS lists an "
    "organization's e-filed returns at "
    "https://apps.irs.gov/app/eos/details/?ein=<EIN>."
)

NO_SCHEDULE_R = (
    "{eins} filed a full Form 990 without Schedule R for the year shown, which "
    "means it reported no related organizations."
)

NOT_A_990 = (
    "{eins} filed a {forms} for the year shown, which has no Schedule R. Its "
    "related organizations, if any, aren't reported to the IRS."
)

TRUNCATED = (
    "more_available is true for {eins}: the list was cut off at limit={limit} "
    "of related_count. Raise limit, or search for one organization by its EIN "
    "(listed_by), before saying an organization isn't listed."
)

LISTINGS_DIFFER = "Listings differ across {ein}'s stored returns: {parts}."
ONLY_SOME_YEARS = (
    "{count} of the organizations shown {verb} listed in only some years "
    "(tax_years_listed)"
)
MISSING_FROM_YEAR = (
    "{count} listed in another year {verb} missing from the year shown "
    "(not_in_year_shown)"
)

NEAR_LISTING = (
    "{filer} ({filer_ein}) lists {name_as_listed!r} under EIN {ein_as_listed}, "
    "one digit from {ein}: the IRS has no record of {ein_as_listed}, and the "
    "name matches {ein}'s (listed_by, ein_as_listed)."
)

# Another stored filer "lists the same organizations" when its returns include
# at least this share of the EINs on the return shown. Stored filers in the
# same system overlap 91-100%; the next pair down overlaps 77%.
SAME_LIST_SHARE = 0.9
# Fewest shared EINs for that to count, so a tiny list (1 of 1) isn't a
# lead; every same-system pair stored shares 10 or more.
SAME_LIST_MIN = 5
SAME_LIST = (
    "Other stored filers list at least {share:.0%} of the organizations on "
    "{ein}'s return on their own returns: {others} (same_list_as; not_listed "
    "names the ones each leaves out)."
)

SAME_TAX_YEAR = (
    "{ein} has more than one return stored for tax year {year} (periods ending "
    "{periods}), as happens when an organization changes its year end. The one "
    "ending latest is shown; the others can't be selected by tax_year."
)

YEAR_NOT_STORED = (
    "No return for tax year {year} is stored for {eins}; the latest stored "
    "year is shown. tax_years_stored lists the others."
)
YEAR_BEFORE_STORED = (
    "Tax year {year} is earlier than any return stored for {eins}, so it "
    "wasn't loaded, which says nothing about who was related then; the latest "
    "stored year is shown. tax_years_stored lists the years stored."
)


def irs_get_related_orgs(
    ein: str | list[str],
    tax_year: int | None = None,
    limit: int = 100,
):
    """List the related organizations a nonprofit reports on Form 990 Schedule R.

    Use it to find a grantee's subsidiaries, parents, and affiliates by EIN,
    with each one's EIN to search FAC by, or to find which filers list a given
    EIN as related, e.g. a subsidiary its parent still reports. Schedule R
    Parts I-IV are disregarded
    entities (single-member LLCs), related tax-exempt organizations, and
    related partnerships, corporations, and trusts. Returns {"filers",
    "listed_by", "not_stored", "caveats"}. Each filer has its name, the tax
    year shown (the latest stored unless tax_year is given), its form,
    whether Schedule R was attached, related_count, and related organizations
    grouped by relationship, each with name, EIN, state, primary activity, and
    direct controlling entity, plus a source_url to cite. listed_by gives each
    filer whose Schedule R lists a searched EIN, the relationship, and the tax
    years it was listed in, with the month each of those years ended
    (periods_ending), to compare with FAC audit years, and control: the
    direct controlling entity the filer reported and whether it marked the
    organization controlled, with the tax years each was reported for (more
    than one entry when it changed). A filer that lists a
    searched EIN with one digit different, under a matching name and an EIN
    the IRS has no record of, is in listed_by too, with ein_as_listed and
    name_as_listed. When a filer has
    several stored returns, an organization not listed on all of them has
    tax_years_listed, and not_in_year_shown lists those on other returns but
    not the one shown, so a change from year to year can't be missed. An
    organization without tax_years_listed is listed on every stored full
    Form 990 in tax_years_stored. same_list_as gives each other stored filer
    whose own returns list at least 90% of the same EINs, with the count
    shared and the organizations it doesn't list (not_listed); any other
    organization shown is on that filer's returns too. A parent or another
    member of the same system may list the same organizations: check a
    related organization's listed_by (search its EIN) or that filer's own
    return.

    Args:
        ein: 9-digit EIN, e.g. 112223333, or a list of up to 20.
        tax_year: Tax year of the return, the year the fiscal year starts,
            e.g. 2023 for a year ending June 2024. Default: latest stored.
        limit: Most related organizations to list per filer, 1 to 500.
    """
    eins = identifiers.many(ein, identifiers.ein, "ein")
    if tax_year is not None and not 2015 <= tax_year <= 2035:
        raise ToolError("tax_year must be between 2015 and 2035.")
    if not 1 <= limit <= 500:
        raise ToolError("limit must be between 1 and 500.")

    with db.connect() as connection:
        returns = _by_ein(
            connection.execute(
                "SELECT * FROM form990_returns WHERE ein IN"
                f" ({','.join('?' * len(eins))}) ORDER BY tax_year, form",
                eins,
            ).fetchall()
        )
        filers, year_missing, year_before = [], [], []
        for e in eins:
            if e not in returns:
                continue
            shown = _pick(returns[e], tax_year)
            if tax_year is not None and shown["tax_year"] != tax_year:
                earliest = min(r["tax_year"] for r in returns[e])
                (year_before if tax_year < earliest else year_missing).append(e)
            related = connection.execute(
                "SELECT * FROM schedule_r WHERE object_id = ? ORDER BY rowid",
                [shown["object_id"]],
            ).fetchall()
            # Compare years by the return each shows, as _pick chooses it: with
            # two returns in one tax year, the hidden one isn't compared.
            shown_by_year = {
                _pick(returns[e], year)["object_id"]
                for year in {r["tax_year"] for r in returns[e]}
            }
            history = [
                row
                for row in connection.execute(
                    "SELECT * FROM schedule_r WHERE filer_ein = ?"
                    " ORDER BY tax_year, rowid",
                    [e],
                )
                if row["object_id"] in shown_by_year
            ]
            filer = _filer(shown, returns[e], related, history, limit)
            filer["same_list_as"] = _same_list(connection, e, related)
            filers.append(filer)
        listed = connection.execute(
            "SELECT s.ein AS searched_ein, s.filer_ein, s.relationship, s.tax_year,"
            " s.direct_controlling_entity, s.controlled, r.tax_period_end,"
            " (SELECT name FROM form990_returns n WHERE n.ein = s.filer_ein"
            "  ORDER BY tax_year DESC LIMIT 1) AS filer_name"
            " FROM schedule_r s"
            " LEFT JOIN form990_returns r ON r.object_id = s.object_id"
            f" WHERE s.ein IN ({','.join('?' * len(eins))})"
            " ORDER BY s.filer_ein, s.tax_year",
            eins,
        ).fetchall()
        near = _near_listings(connection, eins)
        snapshot = db.snapshot_caveat(connection, "irs")

    not_stored = [e for e in eins if e not in returns]
    caveats = list(CAVEATS)
    if not_stored:
        caveats.insert(0, NOT_STORED.format(eins=", ".join(not_stored)))
    if year_missing:
        caveats.append(
            YEAR_NOT_STORED.format(year=tax_year, eins=", ".join(year_missing))
        )
    if year_before:
        caveats.append(
            YEAR_BEFORE_STORED.format(year=tax_year, eins=", ".join(year_before))
        )
    without = [
        f["ein"] for f in filers if f["form"] == "990" and not f["has_schedule_r"]
    ]
    if without:
        caveats.append(NO_SCHEDULE_R.format(eins=", ".join(without)))
    other_forms = [f for f in filers if f["form"] != "990"]
    if other_forms:
        caveats.append(
            NOT_A_990.format(
                eins=", ".join(f["ein"] for f in other_forms),
                forms=" or ".join(sorted({f["form"] for f in other_forms})),
            )
        )
    for f in filers:
        if difference := _difference(f):
            caveats.append(LISTINGS_DIFFER.format(ein=f["ein"], parts=difference))
    for f in filers:
        if f["same_list_as"]:
            others = "; ".join(
                f"{o['name'] or o['ein']} ({o['ein']}), {o['shared']} of {o['of']}"
                for o in f["same_list_as"]
            )
            caveats.append(
                SAME_LIST.format(share=SAME_LIST_SHARE, ein=f["ein"], others=others)
            )
    for f in filers:
        same_year = sorted(
            r["tax_period_end"]
            for r in returns[f["ein"]]
            if r["tax_year"] == f["tax_year"] and r["form"] == f["form"]
        )
        if len(same_year) > 1:
            caveats.append(
                SAME_TAX_YEAR.format(
                    ein=f["ein"], year=f["tax_year"], periods=", ".join(same_year)
                )
            )
    truncated = [f["ein"] for f in filers if f["more_available"]]
    if truncated:
        caveats.append(TRUNCATED.format(eins=", ".join(truncated), limit=limit))
    near_entries = _listed_by(near)
    for entry in near_entries:
        caveats.append(NEAR_LISTING.format(filer=entry["filer_name"], **entry))
    caveats.append(snapshot)
    return {
        "filers": filers,
        "listed_by": _listed_by(listed) + near_entries,
        "not_stored": not_stored,
        "caveats": caveats,
    }


def _by_ein(rows):
    returns = {}
    for row in rows:
        returns.setdefault(row["ein"], []).append(row)
    return returns


def _pick(returns, tax_year):
    """The return for tax_year, else the latest; a 990 over another form.
    When a change of year end puts two returns in one tax year, the one
    whose period ends later."""
    years = [r for r in returns if r["tax_year"] == tax_year] or returns
    latest = max(r["tax_year"] for r in years)
    candidates = [r for r in years if r["tax_year"] == latest]
    candidates = [r for r in candidates if r["form"] == "990"] or candidates
    return max(candidates, key=lambda r: r["tax_period_end"] or "")


def _filer(shown, returns, related, history, limit):
    """One filer's return for the year shown, its related orgs by relationship,
    and how the listing differs across the filer's other stored returns."""
    # Only years with a full 990 can list anyone; other forms have no Schedule R.
    compared = sorted({r["tax_year"] for r in returns if r["form"] == "990"})
    years = {}
    for row in history:
        years.setdefault(_key(row), set()).add(row["tax_year"])

    def listed(row):
        """The years this organization is listed in, if not every compared year."""
        found = sorted(years.get(_key(row), {row["tax_year"]}))
        return found if len(compared) > 1 and found != compared else None

    groups = {}
    for row in related[:limit]:
        group = groups.setdefault(
            row["relationship"],
            {
                "part": row["part"],
                "relationship": row["relationship"],
                "organizations": [],
            },
        )
        organization = _organization(row)
        if only := listed(row):
            organization["tax_years_listed"] = only
        group["organizations"].append(organization)
    shown_keys = {_key(row) for row in related}
    missing = {}
    if shown["tax_year"] in compared:
        for row in history:  # oldest first, so the latest listing wins
            if _key(row) not in shown_keys:
                missing[_key(row)] = row
    not_in_year_shown = [
        {
            "name": row["name"],
            "ein": row["ein"] or None,
            "relationship": row["relationship"],
            "tax_years_listed": sorted(years[key]),
        }
        for key, row in list(missing.items())[:limit]
    ]
    return {
        "ein": shown["ein"],
        "name": shown["name"],
        "tax_year": shown["tax_year"],
        "tax_period_end": shown["tax_period_end"],
        "form": shown["form"],
        "has_schedule_r": shown["has_schedule_r"] == "Y",
        "related_count": len(related),
        "more_available": len(related) > limit,
        "related_organizations": list(groups.values()),
        "not_in_year_shown": not_in_year_shown,
        "tax_years_stored": sorted({r["tax_year"] for r in returns}),
        "object_id": shown["object_id"],
        "source_url": irs.teos_url(shown["ein"]),
    }


def _same_list(connection, ein, related):
    """Other stored filers whose returns, in any stored year, list at least
    SAME_LIST_SHARE of the EINs on this return, most shared first, each with
    the organizations it doesn't list. A filer's own EIN isn't counted
    against it: no one lists itself."""
    names = {}
    for row in related:
        if row["ein"]:
            names.setdefault(row["ein"], row["name"])
    if not names:
        return []
    rows = connection.execute(
        "SELECT DISTINCT s.filer_ein, s.ein,"
        " (SELECT name FROM form990_returns n WHERE n.ein = s.filer_ein"
        "  ORDER BY tax_year DESC LIMIT 1) AS filer_name"
        " FROM schedule_r s"
        f" WHERE s.ein IN ({','.join('?' * len(names))}) AND s.filer_ein != ?",
        [*names, ein],
    ).fetchall()
    shared, filer_names = {}, {}
    for r in rows:
        shared.setdefault(r["filer_ein"], set()).add(r["ein"])
        filer_names[r["filer_ein"]] = r["filer_name"]
    found = []
    for other, eins in shared.items():
        compared = set(names) - {other}
        if len(eins) >= SAME_LIST_MIN and len(eins) >= SAME_LIST_SHARE * len(compared):
            found.append(
                {
                    "ein": other,
                    "name": filer_names[other],
                    "shared": len(eins),
                    "of": len(compared),
                    "not_listed": [
                        {"name": names[e], "ein": e} for e in sorted(compared - eins)
                    ],
                }
            )
    return sorted(found, key=lambda f: (-f["shared"], f["ein"]))


def _key(row):
    """One related organization across years: its EIN, else its name."""
    return row["ein"] or re.sub(r"[^A-Z0-9]", "", (row["name"] or "").upper())


def _difference(filer):
    """What a filer's listing differences caveat says, or None."""
    some = sum(
        "tax_years_listed" in o
        for g in filer["related_organizations"]
        for o in g["organizations"]
    )
    missing = len(filer["not_in_year_shown"])
    parts = []
    if some:
        verb = "is" if some == 1 else "are"
        parts.append(ONLY_SOME_YEARS.format(count=some, verb=verb))
    if missing:
        verb = "is" if missing == 1 else "are"
        parts.append(MISSING_FROM_YEAR.format(count=missing, verb=verb))
    return ", and ".join(parts) or None


def _organization(row):
    """A related organization's fields, leaving out the ones its part lacks."""
    optional = {
        "state": row["state"] or row["legal_domicile"],
        "primary_activity": row["primary_activity"],
        "direct_controlling_entity": row["direct_controlling_entity"],
        "exempt_code_section": row["exempt_code_section"],
        "entity_type": row["entity_type"],
        "controlled": {"Y": True, "N": False}.get(row["controlled"]),
    }
    return {"name": row["name"], "ein": row["ein"] or None} | {
        k: v for k, v in optional.items() if v not in ("", None)
    }


def _near_listings(connection, eins):
    """Schedule R rows listing an EIN one digit from a searched one, kept only
    when the IRS has no record of the EIN as listed and the listed name
    matches the searched EIN's IRS or Form 990 name: a likely typo of it,
    not a neighbor, since related organizations often have consecutive EINs."""
    variants = {}
    for ein in eins:
        for i, digit in enumerate(ein):
            for d in "0123456789":
                if d != digit:
                    variants[ein[:i] + d + ein[i + 1 :]] = ein
    variants = {v: e for v, e in variants.items() if v not in eins}
    marks = ",".join("?" * len(variants))
    rows = connection.execute(
        "SELECT s.ein AS listed_ein, s.name AS listed_name, s.filer_ein,"
        " s.relationship, s.tax_year, s.direct_controlling_entity, s.controlled,"
        " r.tax_period_end,"
        " (SELECT name FROM form990_returns n WHERE n.ein = s.filer_ein"
        "  ORDER BY tax_year DESC LIMIT 1) AS filer_name"
        " FROM schedule_r s"
        " LEFT JOIN form990_returns r ON r.object_id = s.object_id"
        f" WHERE s.ein IN ({marks})"
        f" AND s.ein NOT IN (SELECT EIN FROM eo_bmf WHERE EIN IN ({marks}))"
        " ORDER BY s.filer_ein, s.tax_year",
        [*variants, *variants],
    ).fetchall()
    names = {}
    for ein in eins:
        names[ein] = [
            n
            for row in connection.execute(
                "SELECT NAME, SORT_NAME FROM eo_bmf WHERE EIN = ?"
                " UNION ALL SELECT name, NULL FROM form990_returns WHERE ein = ?",
                [ein, ein],
            )
            for n in row
        ]
    kept = []
    for row in rows:
        ein = variants[row["listed_ein"]]
        if row["filer_ein"] != ein and same_name(names[ein], [row["listed_name"]]):
            kept.append(
                {
                    **dict(row),
                    "searched_ein": ein,
                    "ein_as_listed": row["listed_ein"],
                    "name_as_listed": row["listed_name"],
                }
            )
    return kept


def _listed_by(rows):
    """Filers whose Schedule R lists a searched EIN, one entry per filer and
    relationship, with the tax years it was listed in and the month each of
    those fiscal years ended (YYYY-MM, comparable to FAC's fiscal years)."""
    entries = {}
    for row in rows:
        key = (row["searched_ein"], row["filer_ein"], row["relationship"])
        entry = entries.setdefault(
            key,
            {
                "ein": row["searched_ein"],
                "filer_ein": row["filer_ein"],
                "filer_name": row["filer_name"],
                "relationship": row["relationship"],
                "tax_years": [],
                "periods_ending": [],
                "source_url": irs.teos_url(row["filer_ein"]),
            }
            | (
                {
                    "ein_as_listed": row["ein_as_listed"],
                    "name_as_listed": row["name_as_listed"],
                }
                if "ein_as_listed" in row.keys()
                else {}
            ),
        )
        if row["tax_year"] not in entry["tax_years"]:
            entry["tax_years"].append(row["tax_year"])
            entry["periods_ending"].append(row["tax_period_end"])
        reported = {
            "direct_controlling_entity": row["direct_controlling_entity"] or None,
            "controlled": {"Y": True, "N": False}.get(row["controlled"]),
        }
        reported = {k: v for k, v in reported.items() if v is not None}
        if reported:
            # One group per thing reported, with its years: a change of
            # controlling entity shows as two groups, not as the latest.
            control = entry.setdefault("control", [])
            group = next((g for g in control if g["reported"] == reported), None)
            if group is None:
                control.append(group := {"reported": reported, "tax_years": []})
            if row["tax_year"] not in group["tax_years"]:
                group["tax_years"].append(row["tax_year"])
    for entry in entries.values():
        if "control" in entry:
            entry["control"] = [
                {**g["reported"], "tax_years": g["tax_years"]} for g in entry["control"]
            ]
    return list(entries.values())


def register(mcp):
    mcp.tool(
        irs_get_related_orgs,
        name="irs_get_related_orgs",
        annotations={
            "title": "Get related organizations from IRS Schedule R",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
