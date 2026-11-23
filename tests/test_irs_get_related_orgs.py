"""Tests for irs_get_related_orgs, on a fixture database."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp.tools.irs_get_related_orgs import irs_get_related_orgs

BSW = "463131350"
ROUND_ROCK = "203749695"


def filing(ein, name, tax_year, object_id, form="990", has_schedule_r="Y"):
    return {
        "ein": ein,
        "name": name,
        "tax_year": tax_year,
        "tax_period_end": f"{tax_year + 1}-06",
        "form": form,
        "object_id": object_id,
        "xml_batch_id": "2025_TEOS_XML_05A",
        "has_schedule_r": has_schedule_r if form == "990" else "",
    }


def related(object_id, tax_year, part, name, ein, **fields):
    relationships = {
        "I": "disregarded entity",
        "II": "related tax-exempt organization",
        "III": "related organization taxable as a partnership",
        "IV": "related organization taxable as a corporation or trust",
    }
    return {
        "filer_ein": BSW,
        "tax_year": tax_year,
        "object_id": object_id,
        "part": part,
        "relationship": relationships[part],
        "name": name,
        "ein": ein,
        "state": "TX",
        **fields,
    }


RETURNS = [
    filing(BSW, "Baylor Scott & White Health", 2022, "202431349349301513"),
    filing(BSW, "Baylor Scott & White Health", 2023, "202531339349307668"),
    filing(
        "481150706",
        "KONZA PRAIRIE COMMUNITY HEALTH CENTER INC",
        2024,
        "202543169349302289",
        has_schedule_r="N",
    ),  # fmt: skip
    filing("820000001", "SMALL FOUNDATION", 2024, "202500000000000001", form="990PF"),
]
SCHEDULE_R = [
    related(
        "202431349349301513",
        2022,
        "II",
        "Scott & White Hospital-Round Rock",
        ROUND_ROCK,
        exempt_code_section="501(c)(3)",
        controlled="Y",
    ),  # fmt: skip
    related(
        "202531339349307668",
        2023,
        "II",
        "Scott & White Hospital-Round Rock",
        ROUND_ROCK,
        exempt_code_section="501(c)(3)",
        controlled="Y",
        direct_controlling_entity="Scott & White Healthcare",
    ),  # fmt: skip
    related(
        "202531339349307668",
        2023,
        "III",
        "Arlington Ortho & Spine Hospital LLC",
        "261578178",
        primary_activity="Hospital",
    ),  # fmt: skip
    related(
        "202531339349307668",
        2023,
        "IV",
        "BSW Condo Owners Assoc Inc",
        "261661900",
        entity_type="corporation",
        controlled="Y",
    ),  # fmt: skip
    related("202531339349307668", 2023, "I", "No EIN LLC", ""),
]


@pytest.fixture
def irs_db(database):
    database({"form990_returns": RETURNS, "schedule_r": SCHEDULE_R})


def test_lists_the_latest_years_related_orgs_by_relationship(irs_db):
    result = irs_get_related_orgs(ein="46-3131350")

    [bsw] = result["filers"]
    assert bsw["tax_year"] == 2023
    assert bsw["tax_years_stored"] == [2022, 2023]
    assert bsw["related_count"] == 4
    assert bsw["more_available"] is False
    assert bsw["source_url"].endswith("?ein=463131350")
    groups = {g["part"]: g for g in bsw["related_organizations"]}
    assert groups["II"]["relationship"] == "related tax-exempt organization"
    [round_rock] = groups["II"]["organizations"]
    assert round_rock == {
        "name": "Scott & White Hospital-Round Rock",
        "ein": ROUND_ROCK,
        "state": "TX",
        "direct_controlling_entity": "Scott & White Healthcare",
        "exempt_code_section": "501(c)(3)",
        "controlled": True,
    }
    # Fields a part doesn't have are left out; a missing EIN is null.
    # Listed on the 2023 return only, so its years are shown; Round Rock,
    # on both, has none.
    assert groups["III"]["organizations"][0] == {
        "name": "Arlington Ortho & Spine Hospital LLC",
        "ein": "261578178",
        "state": "TX",
        "primary_activity": "Hospital",
        "tax_years_listed": [2023],
    }
    assert bsw["not_in_year_shown"] == []
    assert groups["IV"]["organizations"][0]["entity_type"] == "corporation"
    assert groups["I"]["organizations"][0]["ein"] is None
    caveats = " ".join(result["caveats"])
    assert "Only full Form 990 filers report Schedule R" in caveats
    assert "Group-exemption members need not be listed" in caveats
    assert "processed from 2024 on, about tax year 2022 on" in caveats
    assert "processed from 2019 on, about tax year 2017 on" in caveats
    assert result["caveats"][-1].startswith("Results come from a local copy of IRS")


def test_a_subsidiarys_ein_finds_the_filers_that_list_it(irs_db):
    """S19: the hospital is listed by its system every year."""
    result = irs_get_related_orgs(ein=ROUND_ROCK)

    assert result["filers"] == []
    assert result["not_stored"] == [ROUND_ROCK]
    assert result["listed_by"] == [
        {
            "ein": ROUND_ROCK,
            "filer_ein": BSW,
            "filer_name": "Baylor Scott & White Health",
            "relationship": "related tax-exempt organization",
            "tax_years": [2022, 2023],
            "periods_ending": ["2023-06", "2024-06"],
            "source_url": "https://apps.irs.gov/app/eos/details/?ein=463131350",
            # Who controls it, as the filer reported each year: searched
            # from the subsidiary's side, not only from the filer's.
            "control": [
                {"controlled": True, "tax_years": [2022]},
                {
                    "direct_controlling_entity": "Scott & White Healthcare",
                    "controlled": True,
                    "tax_years": [2023],
                },
            ],
        }
    ]
    assert result["caveats"][0].startswith(f"No return is stored for {ROUND_ROCK}")


def test_tax_year_picks_a_return_and_says_when_it_isnt_stored(irs_db):
    result = irs_get_related_orgs(ein=BSW, tax_year=2022)
    assert result["filers"][0]["tax_year"] == 2022
    assert result["filers"][0]["related_count"] == 1

    result = irs_get_related_orgs(ein=BSW, tax_year=2024)
    assert result["filers"][0]["tax_year"] == 2023
    assert any(c.startswith("No return for tax year 2024") for c in result["caveats"])


def test_a_year_before_the_stored_returns_is_not_loaded_not_unrelated(irs_db):
    result = irs_get_related_orgs(ein=BSW, tax_year=2020)
    assert result["filers"][0]["tax_year"] == 2023
    caveats = [c for c in result["caveats"] if c.startswith("Tax year 2020")]
    assert len(caveats) == 1
    assert "earlier than any return stored for 463131350" in caveats[0]
    assert "says nothing about who was related then" in caveats[0]
    assert not any(c.startswith("No return for tax year") for c in result["caveats"])


def test_limit_cuts_each_filers_list_and_says_so(irs_db):
    result = irs_get_related_orgs(ein=BSW, limit=2)

    [bsw] = result["filers"]
    assert sum(len(g["organizations"]) for g in bsw["related_organizations"]) == 2
    assert bsw["related_count"] == 4
    assert bsw["more_available"] is True
    assert any(
        c.startswith("more_available is true for 463131350") for c in result["caveats"]
    )


def test_says_why_a_filer_has_no_related_orgs(irs_db):
    result = irs_get_related_orgs(ein=["481150706", "820000001"])

    konza, foundation = result["filers"]
    assert konza["has_schedule_r"] is False
    assert konza["related_organizations"] == []
    assert foundation["form"] == "990PF"
    caveats = " ".join(result["caveats"])
    assert "481150706 filed a full Form 990 without Schedule R" in caveats
    assert "820000001 filed a 990PF" in caveats


BJK = "431036785"
SUBSIDIARY = "431844750"


def bjk(tax_year, name, ein):
    return related(f"BJK{tax_year}", tax_year, "II", name, ein) | {"filer_ein": BJK}


BJK_RETURNS = [
    filing(BJK, "BETTY JEAN KERR PEOPLE'S HEALTH CENTERS", year, f"BJK{year}")
    for year in (2022, 2023, 2024)
]
BJK_SCHEDULE_R = [
    # On every return, its name spelled two ways: matched by EIN.
    bjk(2022, "HOPEWELL VENTER", "431496431"),
    bjk(2023, "HOPEWELL VENTER", "431496431"),
    bjk(2024, "HOPEWELL CENTER", "431496431"),
    # S16: the subsidiary appears only on the latest return.
    bjk(2024, "PEOPLE'S HEALTH SUPPORTIVE SERVICES", SUBSIDIARY),
    # Dropped from the latest return, with no EIN: matched by name.
    bjk(2022, "Old Clinic LLC", ""),
    bjk(2023, "OLD CLINIC, LLC", ""),
]


def from_subsidiary(name, ein):
    row = related("SUB2024", 2024, "II", name, ein)
    return row | {"filer_ein": SUBSIDIARY}


SUBSIDIARY_LISTS = [
    # The parent under an EIN one digit off that the IRS has no record of.
    from_subsidiary("PEOPLE'S HEALTH CENTERS INC", "431036725"),
    # One digit off too, but the IRS has that EIN: a real, different organization.
    from_subsidiary("PEOPLES HEALTH CENTERS", "431036786"),
    # One digit off and unknown to the IRS, but under another name.
    from_subsidiary("UNRELATED HOUSING CORP", "431036775"),
]
EO_BMF = [
    {"EIN": BJK, "NAME": "BETTY JEAN KERR PEOPLES HEALTH CENTERS INC"},
    {"EIN": "431036786", "NAME": "PEOPLES HEALTH CENTERS FOUNDATION"},
]


@pytest.fixture
def bjk_db(database):
    database(
        {
            "form990_returns": BJK_RETURNS
            + [
                filing(
                    SUBSIDIARY, "PEOPLE'S HEALTH SUPPORTIVE SERVICES", 2024, "SUB2024"
                )
            ],
            "schedule_r": BJK_SCHEDULE_R + SUBSIDIARY_LISTS,
            "eo_bmf": EO_BMF,
        }
    )


def test_listed_by_includes_a_one_digit_typo_under_a_matching_name(bjk_db):
    result = irs_get_related_orgs(ein=BJK)

    [near] = [e for e in result["listed_by"] if "ein_as_listed" in e]
    assert near == {
        "ein": BJK,
        "filer_ein": SUBSIDIARY,
        "filer_name": "PEOPLE'S HEALTH SUPPORTIVE SERVICES",
        "relationship": "related tax-exempt organization",
        "tax_years": [2024],
        "periods_ending": ["2025-06"],
        "source_url": f"https://apps.irs.gov/app/eos/details/?ein={SUBSIDIARY}",
        "ein_as_listed": "431036725",
        "name_as_listed": "PEOPLE'S HEALTH CENTERS INC",
    }
    assert any(
        c.startswith("PEOPLE'S HEALTH SUPPORTIVE SERVICES (431844750) lists")
        and "the IRS has no record of 431036725" in c
        for c in result["caveats"]
    )


def test_says_which_listings_differ_across_stored_returns(bjk_db):
    result = irs_get_related_orgs(ein=BJK)

    [filer] = result["filers"]
    assert filer["tax_year"] == 2024
    [group] = filer["related_organizations"]
    by_ein = {o["ein"]: o for o in group["organizations"]}
    assert "tax_years_listed" not in by_ein["431496431"]
    assert by_ein[SUBSIDIARY]["tax_years_listed"] == [2024]
    assert filer["not_in_year_shown"] == [
        {
            "name": "OLD CLINIC, LLC",
            "ein": None,
            "relationship": "related tax-exempt organization",
            "tax_years_listed": [2022, 2023],
        }
    ]
    assert (
        "Listings differ across 431036785's stored returns: 1 of the organizations "
        "shown is listed in only some years (tax_years_listed), and 1 listed in "
        "another year is missing from the year shown (not_in_year_shown)."
    ) in result["caveats"]


def test_an_earlier_year_shows_what_later_returns_added(bjk_db):
    result = irs_get_related_orgs(ein=BJK, tax_year=2022)

    [filer] = result["filers"]
    assert [m["ein"] for m in filer["not_in_year_shown"]] == [SUBSIDIARY]


def test_one_stored_return_has_no_differences(irs_db):
    result = irs_get_related_orgs(ein="481150706")

    assert result["filers"][0]["not_in_year_shown"] == []
    assert not any(c.startswith("Listings differ") for c in result["caveats"])


def test_bad_input_is_rejected(irs_db):
    with pytest.raises(ToolError, match="9 digits"):
        irs_get_related_orgs(ein="46313135")
    with pytest.raises(ToolError, match="empty"):
        irs_get_related_orgs(ein=[])
    with pytest.raises(ToolError, match="tax_year"):
        irs_get_related_orgs(ein=BSW, tax_year=1999)
    with pytest.raises(ToolError, match="limit"):
        irs_get_related_orgs(ein=BSW, limit=0)


def test_same_list_as_names_other_filers_listing_most_of_the_same_orgs(database):
    system, holdings, partner = "463131350", "463130985", "111111111"
    members = ["200000001", "200000002", "200000003", "200000004", "200000005"]

    def lists(filer, eins):
        return [
            related(f"R{filer}", 2024, "II", f"Member {e}", e) | {"filer_ein": filer}
            for e in eins
        ]

    database(
        {
            "form990_returns": [
                filing(system, "BAYLOR SCOTT & WHITE HEALTH", 2024, f"R{system}"),
                filing(holdings, "BAYLOR SCOTT & WHITE HOLDINGS", 2024, f"R{holdings}"),
                filing(partner, "JOINT VENTURE PARTNER", 2024, f"R{partner}"),
            ],
            # The system lists Holdings too; Holdings doesn't list itself.
            "schedule_r": lists(system, members + [holdings])
            + lists(holdings, members)  # all 5 others: the same list
            + lists(partner, members[:4]),  # 4 of 6 = 67%: under the cut-off
        }
    )
    result = irs_get_related_orgs(ein=system)

    assert result["filers"][0]["same_list_as"] == [
        {
            "ein": holdings,
            "name": "BAYLOR SCOTT & WHITE HOLDINGS",
            "shared": 5,
            "of": 5,
            "not_listed": [],
        }
    ]
    assert (
        "Other stored filers list at least 90% of the organizations on 463131350's "
        "return on their own returns: BAYLOR SCOTT & WHITE HOLDINGS (463130985), "
        "5 of 5 (same_list_as; not_listed names the ones each leaves out)."
    ) in result["caveats"]


def test_same_list_as_names_what_the_other_filer_leaves_out(database):
    system, holdings = "463131350", "463130985"
    members = [f"20000000{i}" for i in range(10)]

    def lists(filer, eins):
        return [
            related(f"R{filer}", 2024, "II", f"Member {e}", e) | {"filer_ein": filer}
            for e in eins
        ]

    database(
        {
            "form990_returns": [
                filing(system, "SYSTEM", 2024, f"R{system}"),
                filing(holdings, "HOLDINGS", 2024, f"R{holdings}"),
            ],
            "schedule_r": lists(system, members) + lists(holdings, members[:9]),
        }
    )
    [other] = irs_get_related_orgs(ein=system)["filers"][0]["same_list_as"]

    assert (other["shared"], other["of"]) == (9, 10)
    assert other["not_listed"] == [{"name": "Member 200000009", "ein": "200000009"}]


def test_same_list_as_needs_five_shared(database):
    system, other = "463131350", "463130985"
    few = ["200000001", "200000002", "200000003", "200000004"]

    def lists(filer, eins):
        return [
            related(f"R{filer}", 2024, "II", f"Member {e}", e) | {"filer_ein": filer}
            for e in eins
        ]

    database(
        {
            "form990_returns": [
                filing(system, "SYSTEM", 2024, f"R{system}"),
                filing(other, "OTHER", 2024, f"R{other}"),
            ],
            "schedule_r": lists(system, few) + lists(other, few),  # 4 of 4
        }
    )
    assert irs_get_related_orgs(ein=system)["filers"][0]["same_list_as"] == []


def test_same_list_as_is_empty_without_another_filer(irs_db):
    result = irs_get_related_orgs(ein=BSW)
    assert result["filers"][0]["same_list_as"] == []
    assert not any(c.startswith("Other stored filers") for c in result["caveats"])


def test_two_returns_in_one_tax_year_show_the_later_period(database):
    """MR !10 review, finding 9. Goodwill of Central & Southern Indiana (EIN
    350893506) moved its year end from December to June: a six-month return
    ending 2024-06 and a full-year one ending 2025-06 both carry tax year
    2024. Fails on the branch as reviewed: the six-month return is shown."""
    goodwill = "350893506"
    periods = [(2023, "2023-12", "1"), (2024, "2024-06", "2"), (2024, "2025-06", "3")]
    database({
        "form990_returns": [
            {**filing(goodwill, "GOODWILL", year, digit * 18), "tax_period_end": end}
            for year, end, digit in periods
        ],
    })  # fmt: skip

    [filer] = irs_get_related_orgs(ein=goodwill)["filers"]

    assert filer["tax_period_end"] == "2025-06"


def test_year_comparison_uses_the_return_shown_for_each_tax_year(database):
    """A short-year return hidden behind a later one in the same tax year
    isn't compared: what only it lists isn't 'missing from the year shown'."""
    filer = "350893506"
    returns = [
        (2023, "2023-12", "R2023"),
        (2024, "2024-06", "SHORT"),
        (2024, "2025-06", "FULL"),
    ]
    database(
        {
            "form990_returns": [
                {**filing(filer, "GOODWILL", year, oid), "tax_period_end": end}
                for year, end, oid in returns
            ],
            "schedule_r": [
                related("R2023", 2023, "II", "Kept Org", "300000001")
                | {"filer_ein": filer},
                related("SHORT", 2024, "II", "Short Year Only", "300000002")
                | {"filer_ein": filer},
                related("FULL", 2024, "II", "Kept Org", "300000001")
                | {"filer_ein": filer},
            ],
        }
    )

    [shown] = irs_get_related_orgs(ein=filer)["filers"]

    assert shown["tax_period_end"] == "2025-06"
    [org] = shown["related_organizations"][0]["organizations"]
    assert "tax_years_listed" not in org  # on 2023 and on the 2024 return shown
    assert shown["not_in_year_shown"] == []
