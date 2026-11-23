"""Tests for what the two grantee tools add to the linked identifiers: filers
joined by name and place, other organizations' filings set aside, and
grantee_audit_coverage's rows by fiscal year. FAC and SAM.gov are faked."""

import datetime
import sqlite3
import sys
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import db, fac, names, sam
from dat_mcp.tools import grantee_audit_coverage as coverage
from dat_mcp.tools import grantee_identity_resolution as identity
from dat_mcp.tools.grantee_audit_coverage import grantee_audit_coverage
from dat_mcp.tools.grantee_identity_resolution import grantee_identity_resolution

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_db  # noqa: E402

UEI, EIN, OLD_EIN = "A1B2C3D4E5F6", "300444724", "030444724"
STREET, CITY, ZIP = "105 West 13th Street", "Hays", "67601"


def audit(year, uei, ein, name="VALLEY CLINIC, INC.", street=STREET, city=CITY,
          state="KS", zip_code=ZIP):  # fmt: skip
    return {
        "report_id": f"{year}-06-GSAFAC-{abs(hash((uei, ein, name))) % 10**10:010d}",
        "auditee_uei": uei,
        "auditee_ein": ein,
        "audit_year": str(year),
        "auditee_name": name,
        "auditee_address_line_1": street,
        "auditee_city": city,
        "auditee_state": state,
        "auditee_zip": zip_code,
        "fy_end_date": f"{year}-06-30",
        "fac_accepted_date": f"{year + 1}-01-15",
        "total_amount_expended": "1000000",
        "entity_type": "non-profit",
    }


# A clinic whose EIN was entered with two digits swapped through 2020: the
# earlier audits have no UEI, so nothing but the name and address joins them.
SPLIT = [
    *(audit(y, "GSA_MIGRATION", OLD_EIN) for y in (2019, 2020)),
    audit(2021, "GSA_MIGRATION", EIN, "VALLEY CLINIC"),
    *(audit(y, UEI, EIN, "Valley Clinic") for y in (2022, 2023, 2024, 2025)),
]


def registration(uei=UEI, name="VALLEY CLINIC INC", street=STREET, city=CITY):
    return {
        "uei": uei,
        "legal_business_name": name,
        "dba_name": None,
        "registration_status": "Active",
        "fiscal_year_end": "06/30",
        "physical_address": {
            "line1": street,
            "city": city,
            "state": "KS",
            "zip": ZIP,
        },
        "source_url": f"https://sam.gov/entity/{uei}/coreData",
    }


def irs(ein, name, secondary="", street=STREET, city=CITY, zip_code=ZIP):
    return {
        "EIN": ein,
        "NAME": name,
        "SORT_NAME": secondary,
        "STREET": street,
        "CITY": city.upper(),
        "STATE": "KS",
        "ZIP": f"{zip_code}-1234",
        "GROUP": "0000",
        "SUBSECTION": "03",
        "AFFILIATION": "3",
        "STATUS": "01",
        "ACCT_PD": "06",
    }


class FakeFac:
    """Answers fac.get and records each request. general is asked two things:
    who filed a report that lists the entity (parents), and whether anything
    was filed for a year since the local copy (live, nothing by default)."""

    def __init__(self):
        self.calls = []
        self.parents = []
        self.live = []

    def get(self, table, params):
        self.calls.append((table, params))
        if table != "general":
            return []
        return list(self.live if "audit_year" in params else self.parents)


@pytest.fixture
def world(database, monkeypatch):
    """Build a database, and fake FAC, SAM.gov, and the date."""
    fake = FakeFac()
    fake.sam_calls = []

    def registrations(ueis):
        fake.sam_calls.append(list(ueis))
        found = [r for r in fake.registrations if r["uei"] in ueis]
        return found, [u for u in ueis if u not in {r["uei"] for r in found}], True

    def build(general, registered=(), **tables):
        database({"general": list(general), **tables})
        fake.registrations = list(registered)
        return fake

    monkeypatch.setattr(fac, "get", fake.get)
    monkeypatch.setattr(sam, "registrations_by_uei", registrations)
    monkeypatch.setattr(coverage, "today", lambda: datetime.date(2026, 11, 5))
    return build


def listings(monkeypatch, fake, reports, parent):
    """Make reports the audits that list the entity as covered."""
    monkeypatch.setattr(
        identity,
        "fac_search_covered_entities",
        lambda **kw: {"reports": reports, "more_available": False},
    )
    fake.parents = [{"report_id": r["report_id"], **parent} for r in reports]


def listing(year, ueis=(), eins=(), parent_uei="PARENTUEI001"):
    return {
        "audit_year": str(year),
        "report_id": f"{year}-06-GSAFAC-0000009{year}",
        "listed_by_auditee_uei": parent_uei,
        "covered_ueis": list(ueis),
        "covered_eins": list(eins),
        "source_url": "https://app.fac.gov/",
    }


# --- names.py and the fac_filers table


@pytest.mark.parametrize(
    "a, b",
    [
        ("County of Union", "UNION COUNTY"),
        ("Gay Men's Health Crisis, Inc. and Affiliates", "GAY MENS HEALTH CRISIS INC"),
        ("State of Hawaii Department of Health", "HEALTH, HAWAII DEPARTMENT OF"),
        ("A & B Services Corp.", "A AND B SERVICES"),
    ],
)
def test_the_same_name_written_differently_has_one_key(a, b):
    assert names.name_key(a) == names.name_key(b) != ""


@pytest.mark.parametrize(
    "a, b",
    [
        ("Albany Medical College", "Albany Medical College Alumni Association"),
        ("Union County", "Union County General Health District"),
        ("Ohio State University", "Ohio University"),
    ],
)
def test_a_longer_name_is_a_different_key(a, b):
    assert names.name_key(a) != names.name_key(b)


def test_address_keys_ignore_suites_and_spelled_out_street_words():
    assert names.address_key("838 Penn Street, Suite 2") == "838 PENN ST"
    assert names.address_key("838 PENN ST") == "838 PENN ST"
    assert names.address_key("P.O. Box 249") == ""
    assert names.city_key("St. Petersburg") == names.city_key("ST  PETERSBURG")


def test_the_build_derives_one_row_per_name_and_address_a_filer_used(world):
    world(SPLIT)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT auditee_ein, name_key, zip5, address_key, first_year,"
            " last_year, audits FROM fac_filers ORDER BY first_year"
        ).fetchall()
        search = connection.execute(
            "SELECT count(*) FROM fac_filers_fts WHERE fac_filers_fts MATCH 'valley'"
        ).fetchone()[0]

    assert [tuple(r) for r in rows] == [
        (OLD_EIN, "CLINIC VALLEY", ZIP, "105 W 13TH", 2019, 2020, 2),
        (EIN, "CLINIC VALLEY", ZIP, "105 W 13TH", 2021, 2021, 1),
        (EIN, "CLINIC VALLEY", ZIP, "105 W 13TH", 2022, 2025, 4),
    ]
    assert search == 3


def test_derived_adds_the_table_to_a_database_built_before_it(world, tmp_path):
    world(SPLIT)
    path = Path(db.settings.dat_db_path)
    connection = sqlite3.connect(path)
    connection.executescript("DROP TABLE fac_filers_fts; DROP TABLE fac_filers;")
    connection.close()

    result = grantee_identity_resolution(uei=UEI)
    assert "no fac_filers table" in result["notes"][0]
    assert [e["ein"] for e in result["identifiers"]] == [EIN, EIN]

    build_db.refresh_derived(path)
    identity._cache.clear()
    result = grantee_identity_resolution(uei=UEI)
    assert OLD_EIN in [e["ein"] for e in result["identifiers"]]


# --- linking by name and place


def test_filings_under_another_ein_at_the_same_address_are_linked(world):
    world(SPLIT, [registration()])
    result = grantee_identity_resolution(uei=UEI)

    old = next(e for e in result["identifiers"] if e["ein"] == OLD_EIN)
    assert old["audit_years"] == ["2019", "2020"]
    assert "same street address (105 W 13TH, HAYS 67601)" in old["links"][0]
    assert "no UEI or EIN in common" in old["links"][0]
    assert result["unconfirmed_leads"] == []
    assert result["notes"][0].startswith(f"EIN {OLD_EIN} (2019-2020) shares no")
    # Two EINs joined by name and place aren't "two organizations on one UEI".
    assert not any("identifiers span" in n for n in result["notes"])
    assert any(OLD_EIN in q for q in result["open_questions"])


def test_coverage_marks_those_years_as_linked_by_name_and_place(world):
    world(SPLIT, [registration()])
    result = grantee_audit_coverage(uei=UEI)

    routes = {row["audit_year"]: row["route"] for row in result["years"]}
    assert routes == {
        **dict.fromkeys((2025, 2024, 2023, 2022, 2021), "own audit"),
        **dict.fromkeys((2020, 2019), "audit linked by name and place"),
    }
    linked = result["years"][-1]
    assert linked["filed_under"]["ein"] == OLD_EIN
    assert linked["filed_as"] == "VALLEY CLINIC, INC."
    assert "same street address" in linked["linked_by"][0]
    assert "audit_history" not in result and "irs_records" not in result
    assert result["caveats"][0].startswith("route says how")


def test_funding_says_which_ueis_are_linked_by_name_and_place(world, monkeypatch):
    """The earlier filer has its own UEI and EIN, and only the name and
    address in common: its outlays are summed, shown apart, and flagged."""
    earlier = "B2B2B2B2B2B2"
    world(
        [
            audit(2023, earlier, OLD_EIN),
            *(audit(y, UEI, EIN, "Valley Clinic") for y in (2024, 2025)),
        ],
        [registration()],
    )

    def outlays(fiscal_year_end, uei, max_awards, period_start=None):
        return {
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": f"year ending {fiscal_year_end}"},
            "awards": [
                {
                    "award_id": f"H-{uei}",
                    "outlays_in_period": 100.0 if uei == UEI else 40.0,
                    "source_url": "https://www.usaspending.gov/",
                }
            ],
        }

    monkeypatch.setattr(coverage, "usaspending_sum_outlays", outlays)
    result = grantee_audit_coverage(uei=UEI, with_funding=True)

    block = result["years"][0]["usaspending"]
    assert block["total_outlays_in_period"] == 140.0
    assert block["outlays_by_uei"] == {UEI: 100.0, earlier: 40.0}
    [note] = [n for n in result["notes"] if n.startswith("with_funding's totals")]
    assert f"include UEI {earlier}, linked to this entity by name and place" in note


def test_the_irs_ein_finds_later_years_filed_under_another(world):
    """Searched by the earlier EIN, the later audits are found the same way."""
    world(SPLIT, [registration()])
    result = grantee_audit_coverage(ein=OLD_EIN, audit_year=[2024, 2025])

    assert [(r["audit_year"], r["route"]) for r in result["years"]] == [
        (2025, "audit linked by name and place"),
        (2024, "audit linked by name and place"),
    ]


def test_the_same_name_in_another_city_is_only_a_lead(world):
    world(
        [
            *SPLIT,
            audit(
                2018,
                "GSA_MIGRATION",
                "481111111",
                street="9 Elm Road",
                city="Wichita",
                zip_code="67202",
            ),
        ],  # fmt: skip
        [registration()],
    )
    result = grantee_identity_resolution(uei=UEI)

    assert "481111111" not in [e["ein"] for e in result["identifiers"]]
    lead = next(e for e in result["unconfirmed_leads"] if e["ein"] == "481111111")
    assert "same name in the same state" in lead["links"][0]
    years = grantee_audit_coverage(uei=UEI)["years"]
    assert 2018 not in [row["audit_year"] for row in years]


def test_a_longer_name_at_the_same_address_is_only_a_lead(world):
    world(
        [*SPLIT, audit(2024, "FOUNDATIONU1", "481222222", "Valley Clinic Foundation")],
        [registration()],
    )
    result = grantee_identity_resolution(uei=UEI)

    assert "481222222" not in [e["ein"] for e in result["identifiers"]]
    lead = next(e for e in result["unconfirmed_leads"] if e["ein"] == "481222222")
    assert lead["links"][0].startswith("a similar name at the same street address")


def test_another_tenant_of_the_address_is_listed_only_as_a_successor(world):
    """A different name at the entity's address is a lead when its filings
    start where the entity's stop, not when they run alongside."""
    tenant = [audit(y, "TENANTUEI001", "481333333", "Prairie Arts") for y in (2023,)]
    world([*SPLIT, *tenant], [registration()])
    assert grantee_identity_resolution(uei=UEI)["unconfirmed_leads"] == []

    successor = audit(2026, "SUCCESSORUE1", "481444444", "Plains Health System")
    world([*SPLIT, successor], [registration()])
    identity._cache.clear()
    lead = grantee_identity_resolution(uei=UEI)["unconfirmed_leads"][0]
    assert lead["ein"] == "481444444"
    assert lead["links"][0].startswith("a different name at the same street")


def test_too_many_filers_with_one_name_and_city_are_all_leads(world):
    chapters = [
        audit(2024, f"CHAPTERUEI{i:02d}", f"48100000{i}", street=f"{i} Oak Street")
        for i in range(identity.MAX_BRIDGED + 1)
    ]
    world([*SPLIT, *chapters], [registration()])
    result = grantee_identity_resolution(uei=UEI)

    assert {e["ein"] for e in result["identifiers"]} == {EIN}
    assert len(result["unconfirmed_leads"]) == identity.MAX_LEADS
    assert "more not shown" in " ".join(result["notes"])


# --- another organization's filing under this entity's identifier


def stray_filing():
    """A housing nonprofit that used the clinic's EIN on one year's audit."""
    other = {"name": "RIVER HOUSING, INC.", "street": "40 Dock Road", "city": "Salina"}
    return [
        audit(2022, "HOUSINGUEI01", "481555555", **other),
        audit(2023, "HOUSINGUEI01", EIN, **other),
        audit(2024, "HOUSINGUEI01", "481555555", **other),
    ]


def test_a_stray_filing_under_the_entitys_ein_is_set_aside(world):
    world([*SPLIT, *stray_filing()], [registration()])
    result = grantee_identity_resolution(uei=UEI)

    assert "HOUSINGUEI01" not in [e["uei"] for e in result["identifiers"]]
    other = result["other_organizations"][0]
    assert other["ueis"] == ["HOUSINGUEI01"]
    assert other["why"].startswith(
        f"RIVER HOUSING, INC. filed for 2023 under this entity's EIN {EIN}. Its "
        "other 2 filings are under UEI HOUSINGUEI01"
    )
    assert other["why"].endswith("Those filings give EIN 481555555.")
    # Its sister organizations at its address aren't searched as the clinic's.
    assert result["unconfirmed_leads"] == []

    coverage_result = grantee_audit_coverage(uei=UEI, audit_year=2023)
    assert [r["route"] for r in coverage_result["years"]] == ["own audit"]
    assert coverage_result["identity"]["other_organizations"] == 1
    [note] = [n for n in coverage_result["notes"] if n.startswith("Left out as")]
    # Coverage has only a count of them, so the note points at the list.
    assert note.endswith("grantee_identity_resolution lists its filings.")
    assert any("same name at the same address" in c for c in coverage_result["caveats"])


def test_two_organizations_that_share_a_uei_every_year_stay_together(world):
    """Not a stray filing: the notes flag it instead (S10)."""
    twin = [
        audit(y, UEI, "481666666", "VALLEY CLINIC HOLDINGS") for y in (2022, 2023, 2024)
    ]
    world([*SPLIT, *twin], [registration()])
    result = grantee_identity_resolution(uei=UEI)

    assert result["other_organizations"] == []
    assert "481666666" in [e["ein"] for e in result["identifiers"]]
    assert any("identifiers span 2 EINs" in n for n in result["notes"])


# --- a UEI with no filing and no EIN


def test_an_unfiled_ueis_ein_is_found_in_irs_records_by_name_and_city(
    world, monkeypatch
):
    """The parent lists the institute by EIN only; the IRS has that EIN under
    the parent's name, with the institute's as its secondary name."""
    fake = world(
        [],
        [registration(name="VALLEY RESEARCH INSTITUTE", street="1 Lab Way")],
        eo_bmf=[
            irs("251320493", "PLAINS HEALTH", "VALLEY RESEARCH INSTITUTE", "9 Main St"),
            irs("251999999", "VALLEY RESEARCH INSTITUTE ALUMNI", "", "1 Lab Way"),
        ],
    )
    listings(
        monkeypatch,
        fake,
        [listing(2025, eins=["251320493"])],
        {"auditee_name": "PLAINS HEALTH", "auditee_ein": "453674900"},
    )
    result = grantee_identity_resolution(uei=UEI)

    found = result["identifiers"][0]
    assert found["ein"] == "251320493" and found["uei"] is None
    assert "secondary name VALLEY RESEARCH INSTITUTE" in found["links"][0]
    assert "in the same city" in found["links"][0]
    assert [e["ein"] for e in result["unconfirmed_leads"]] == ["251999999"]
    parent = result["listed_in_parent_audits"][0]
    assert parent["listed_by_name"] == "PLAINS HEALTH"
    assert parent["audit_years"] == ["2025"] and parent["listed_by"] == "EIN"

    coverage_result = grantee_audit_coverage(uei=UEI)
    row = coverage_result["years"][0]
    assert row["route"] == "listed as covered in another audit, by EIN"
    assert row["listed_by"]["name"] == "PLAINS HEALTH"
    # The EIN the listing was found under is among the profile's identifiers.
    [entry] = coverage_result["identity"]["identifiers"]
    assert entry["ein"] == "251320493" and "in the same city" in entry["links"][0]


def test_an_irs_name_match_in_another_city_is_a_lead_with_its_listings(world):
    """Two IRS records share the name, neither in the entity's city: not
    confirmed, but the lead says which audits list its EIN, and coverage
    points at it."""
    world(
        [audit(2024, "PARENTUEI001", "061757811", "PLAINS HEALTH", "9 Main St")],
        [registration(name="VALLEY MEDICAL CENTER", street="1 Lab Way")],
        eo_bmf=[
            irs(
                "884128022", "VALLEY MEDICAL CENTER", "", "5 Far Rd", "Topeka", "66603"
            ),
            irs(
                "884199999", "VALLEY MEDICAL CENTER", "", "7 Elm St", "Wichita", "67202"
            ),
        ],
        additional_eins=[
            {
                "report_id": audit(2024, "PARENTUEI001", "061757811", "PLAINS HEALTH")[
                    "report_id"
                ],
                "auditee_uei": "PARENTUEI001",
                "audit_year": "2024",
                "additional_ein": "884128022",
            }
        ],
    )
    leads = grantee_identity_resolution(uei=UEI)["unconfirmed_leads"]
    lead = next(e for e in leads if e["ein"] == "884128022")
    assert len(leads) == 2 and "no address in common" in lead["links"][0]
    assert lead["listed_as_covered_by"] == [
        {"name": "PLAINS HEALTH", "audit_years": ["2024"]}
    ]

    result = grantee_audit_coverage(uei=UEI)
    # The lead stays out of years: only the recent years with nothing found.
    assert {row["route"] for row in result["years"]} <= {"no audit found"}
    assert (
        "EIN 884128022 (VALLEY MEDICAL CENTER), is listed as covered by"
        in (result["notes"][-1])
    )


def test_the_only_irs_record_under_the_name_is_linked_across_cities(world):
    """One IRS record in the state has the entity's exact name: enough, with
    the city differing, when no other record shares the name."""
    world(
        [],
        [registration(name="VALLEY MEDICAL CENTER", street="1 Lab Way")],
        eo_bmf=[
            irs(
                "884128022", "VALLEY MEDICAL CENTER", "", "5 Far Rd", "Topeka", "66603"
            ),
            irs("884177777", "VALLEY MEDICAL CENTER AUXILIARY", "", "5 Far Rd"),
        ],
    )
    result = grantee_identity_resolution(uei=UEI)

    assert [e["ein"] for e in result["identifiers"]] == ["884128022"]
    assert "the only IRS record under that name" in result["identifiers"][0]["links"][0]
    assert result["unconfirmed_leads"] == []


def test_with_no_state_known_an_irs_record_is_not_called_the_same_state(world):
    """A SAM.gov record with no address gives no state to compare, so the
    only IRS record under the name isn't the entity's on the name alone."""
    bare = {**registration(name="VALLEY MEDICAL CENTER"), "physical_address": {}}
    world(
        [],
        [bare],
        eo_bmf=[
            irs(
                "884128022", "VALLEY MEDICAL CENTER", "", "5 Far Rd", "Topeka", "66603"
            ),
        ],
    )
    result = grantee_identity_resolution(uei=UEI)

    assert result["identifiers"] == [] and result["unconfirmed_leads"] == []


def test_a_secondary_name_alone_in_the_state_is_not_enough(world):
    """A chapter's local name under a statewide organization's EIN: the only
    IRS record with the name, in another city. On the secondary name that is
    a lead, not a link."""
    world(
        [],
        [registration(name="VALLEY MEDICAL CENTER", street="1 Lab Way")],
        eo_bmf=[
            irs("884128022", "KANSAS HOSPITAL WORKERS", "VALLEY MEDICAL CENTER",
                "5 Far Rd", "Topeka", "66603"),
        ],
    )  # fmt: skip
    result = grantee_identity_resolution(uei=UEI)

    assert result["identifiers"] == []
    assert [e["ein"] for e in result["unconfirmed_leads"]] == ["884128022"]


def test_irs_records_are_not_searched_for_a_government_body(world):
    """A county's name in the IRS file is a Farm Bureau or union chapter far
    more often than the county."""
    county = {
        **registration(name="VALLEY COUNTY"),
        "entity_structure": "U.S. Government Entity",
    }
    world([], [county], eo_bmf=[irs("884128022", "FARM BUREAU", "VALLEY COUNTY")])
    result = grantee_identity_resolution(uei=UEI)
    assert result["identifiers"] == [] and result["unconfirmed_leads"] == []

    notes = " ".join(grantee_audit_coverage(uei=UEI)["notes"])
    assert "IRS records weren't searched by its name: this is a government" in notes


def test_an_apostrophe_does_not_split_the_name_searched(world):
    world(
        [],
        [registration(name="LANAI KINA'OLE INC")],
        eo_bmf=[irs("990000001", "LANAI KINAOLE INC")],
    )
    assert [e["ein"] for e in grantee_identity_resolution(uei=UEI)["identifiers"]] == [
        "990000001"
    ]


@pytest.mark.parametrize("street, linked", [(STREET, True), ("77 Other Road", False)])
def test_a_one_word_name_links_only_at_the_same_street_address(world, street, linked):
    world(
        [],
        [registration(name="UNITEKS")],
        eo_bmf=[irs("990000002", "UNITEKS INC", "", street)],
    )
    result = grantee_identity_resolution(uei=UEI)

    assert bool(result["identifiers"]) is linked
    entry = (result["identifiers"] or result["unconfirmed_leads"])[0]
    assert entry["ein"] == "990000002" and "a one-word name" in entry["links"][0]


@pytest.mark.parametrize("street, linked", [(STREET, True), ("77 Other Road", False)])
def test_a_name_cut_off_mid_word_links_only_at_the_same_street_address(
    world, street, linked
):
    cut = "NORTH CENTRAL VALLEY MENTAL HEALTH CENTER INCORPORA"
    full = "NORTH CENTRAL VALLEY MENTAL HEALTH CENTER INCORPORATED OF KANSAS"
    world([], [registration(name=cut)], eo_bmf=[irs("990000003", full, "", street)])
    result = grantee_identity_resolution(uei=UEI)

    assert bool(result["identifiers"]) is linked
    entry = (result["identifiers"] or result["unconfirmed_leads"])[0]
    assert entry["ein"] == "990000003" and "cut off" in entry["links"][0]


def test_a_name_ending_at_a_whole_word_is_not_cut_off():
    college = "VALLEY STATE MEDICAL COLLEGE OF THE GREAT PLAINS REGION"
    assert not names.cut_off(college, college + " ALUMNI ASSOCIATION")
    assert names.cut_off(college[:-3], college)
    assert not names.cut_off("VALLEY CLIN", "VALLEY CLINIC")  # too short to be cut


# --- years with no audit: following the parent


def test_a_gap_year_says_whether_the_parent_that_listed_it_has_filed(
    world, monkeypatch
):
    """Listed by the parent in 2023; no listing in 2024 or 2025. The parent
    filed 2024 (under a new EIN, the same UEI) and hasn't filed 2025."""
    parent = [
        audit(2023, "PARENTUEI001", "061757811", "PLAINS HEALTH", "9 Main St"),
        audit(2024, "PARENTUEI001", "550664138", "PLAINS HEALTH", "9 Main St"),
    ]
    fake = world([*SPLIT[:5], *parent], [registration()])
    listings(
        monkeypatch,
        fake,
        [listing(2023, ueis=[UEI])],
        {"auditee_name": "PLAINS HEALTH", "auditee_ein": "061757811"},
    )
    result = grantee_audit_coverage(uei=UEI, audit_year=[2023, 2024, 2025])

    by_year = {row["audit_year"]: row for row in result["years"]}
    assert by_year[2023]["route"] == "listed as covered in another audit, by UEI"
    assert by_year[2024]["route"] == "parent's audit on file, entity not listed"
    assert by_year[2024]["parent_audit"]["report_id"] == parent[1]["report_id"]
    assert by_year[2024]["parent_audit"]["filed_under"]["ein"] == "550664138"
    meaning = by_year[2024]["what_this_means"]
    assert "settles nothing either way" in meaning
    assert "naming the entity as part of what was audited" in meaning
    assert "earlier year doesn't show it" in meaning
    assert by_year[2024]["source_url"] == by_year[2024]["parent_audit"]["source_url"]
    assert by_year[2025]["source_url"] == by_year[2025]["parent"]["source_url"]
    assert by_year[2025]["route"] == "parent has no audit on file"
    assert by_year[2025]["parent"]["last_listed_this_entity"] == 2023
    assert sorted(by_year[2025]["parent"]["eins"]) == ["061757811", "550664138"]
    # The live API was asked about the parent's 2025 before saying so.
    asked = [p for t, p in fake.calls if t == "general" and "audit_year" in p]
    assert any("2025" in p["audit_year"] for p in asked)


def test_another_filer_under_the_parents_ein_is_not_the_parents_audit(
    world, monkeypatch
):
    """A state's EIN is on many agencies' audits: one of them filing for the
    gap year doesn't mean the parent that listed the entity did."""
    filers = [
        audit(2023, "PARENTUEI001", "061757811", "PLAINS HEALTH", "9 Main St"),
        audit(2024, "OTHERUEI0002", "061757811", "PRAIRIE TRANSIT", "4 Depot Rd"),
        audit(2025, "OTHERUEI0002", "061757811", "PRAIRIE TRANSIT", "4 Depot Rd"),
    ]
    fake = world([*SPLIT[:5], *filers], [registration()])
    listings(
        monkeypatch,
        fake,
        [listing(2023, ueis=[UEI])],
        {"auditee_name": "PLAINS HEALTH", "auditee_ein": "061757811"},
    )
    result = grantee_audit_coverage(uei=UEI, audit_year=[2024, 2025])

    assert [(r["audit_year"], r["route"]) for r in result["years"]] == [
        (2025, "parent has no audit on file"),
        (2024, "parent has no audit on file"),
    ]
    assert result["years"][0]["parent"]["ueis"] == ["PARENTUEI001"]


def test_an_entity_with_no_audit_gets_the_years_since_its_last_listing(
    world, monkeypatch
):
    fake = world([], [registration()])
    listings(
        monkeypatch,
        fake,
        [listing(2023, ueis=[UEI])],
        {"auditee_name": "PLAINS HEALTH", "auditee_ein": "061757811"},
    )
    result = grantee_audit_coverage(uei=UEI)

    assert [(r["audit_year"], r["route"]) for r in result["years"]] == [
        (2025, "parent has no audit on file"),
        (2024, "parent has no audit on file"),
        (2023, "listed as covered in another audit, by UEI"),
    ]
    assert result["years"][0]["expected_due_date"] == "2026-03-31"


def test_a_year_between_two_parent_listings_has_a_row(world, monkeypatch):
    """Never filed; listed in 2021 and 2023 but not 2022."""
    fake = world([], [registration()])
    listings(
        monkeypatch,
        fake,
        [listing(2023, ueis=[UEI]), listing(2021, ueis=[UEI])],
        {"auditee_name": "PLAINS HEALTH", "auditee_ein": "061757811"},
    )
    rows = grantee_audit_coverage(uei=UEI, audit_year=[2021, 2022, 2023])["years"]

    assert [(r["audit_year"], r["route"]) for r in rows] == [
        (2023, "listed as covered in another audit, by UEI"),
        (2022, "parent has no audit on file"),
        (2021, "listed as covered in another audit, by UEI"),
    ]
    assert rows[1]["parent"]["last_listed_this_entity"] == 2021


def test_a_filer_that_lists_the_searched_uei_is_its_parent_not_the_entity(
    world, monkeypatch
):
    """A county office registered under its own UEI, with the county's name
    in the county's city. The county's 2023 audit lists that UEI as covered,
    so the county is its parent: its audits aren't linked as the office's own."""
    county = [
        audit(y, "COUNTYUEI001", "316400087", "VALLEY COUNTY", "233 W 6th St")
        for y in (2023, 2024)
    ]
    fake = world(
        county,
        [registration(name="COUNTY OF VALLEY", street="131 N Main St")],
        additional_ueis=[
            {
                "report_id": county[0]["report_id"],
                "auditee_uei": "COUNTYUEI001",
                "audit_year": "2023",
                "additional_uei": UEI,
            }
        ],
    )
    listings(
        monkeypatch,
        fake,
        [listing(2023, ueis=[UEI], parent_uei="COUNTYUEI001")],
        {"auditee_name": "VALLEY COUNTY", "auditee_ein": "316400087"},
    )
    result = grantee_identity_resolution(uei=UEI)
    assert result["identifiers"] == [] and result["unconfirmed_leads"] == []
    assert result["listed_in_parent_audits"][0]["listed_by_name"] == "VALLEY COUNTY"

    rows = grantee_audit_coverage(uei=UEI, audit_year=[2023, 2024])["years"]
    assert [(r["audit_year"], r["route"]) for r in rows] == [
        (2024, "parent's audit on file, entity not listed"),
        (2023, "listed as covered in another audit, by UEI"),
    ]
    assert rows[0]["parent_audit"]["report_id"] == county[1]["report_id"]


def test_the_entitys_own_audit_from_before_ueis_is_not_its_parent(world, monkeypatch):
    """Its 2021 audit, filed with no UEI, lists its other EIN as covered."""
    fake = world(SPLIT, [registration()])
    listings(
        monkeypatch,
        fake,
        [listing(2021, eins=[OLD_EIN], parent_uei="GSA_MIGRATION")],
        {"auditee_name": "VALLEY CLINIC", "auditee_ein": EIN},
    )

    assert grantee_identity_resolution(uei=UEI)["listed_in_parent_audits"] == []
    rows = grantee_audit_coverage(uei=UEI, audit_year=2021)["years"]
    assert [row["route"] for row in rows] == ["own audit"]


def test_a_parents_latest_report_is_its_latest_whichever_identifier_it_listed():
    def row(year, **listed):
        return {
            "audit_year": str(year),
            "report_id": f"R-{year}",
            "listed_by_auditee_uei": "PARENTUEI001",
            "listed_by_name": "PLAINS HEALTH",
            "listed_by_ein": "061757811",
            "listed_identifiers": {"ueis": [], "eins": [], **listed},
            "source_url": f"https://app.fac.gov/{year}",
        }

    # Listings by UEI come first, then those by EIN.
    [parent] = identity._parents(
        [row(2020, ueis=[UEI]), row(2023, eins=[EIN]), row(2022, eins=[EIN])]
    )

    assert parent["latest_report_id"] == "R-2023"
    assert parent["source_url"] == "https://app.fac.gov/2023"
    assert parent["audit_years"] == ["2020", "2022", "2023"]
    assert parent["listed_by"] == "UEI and EIN"


def test_a_listing_of_both_identifiers_is_named_as_the_direct_tie(world, monkeypatch):
    """The EIN came from the IRS by name, but a parent's audit lists it beside
    the searched UEI: say so, since that is stronger than the name."""
    fake = world(
        [],
        [registration(name="VALLEY MEDICAL COLLEGE")],
        eo_bmf=[irs("141338310", "PLAINS HEALTH SYSTEM", "VALLEY MEDICAL COLLEGE")],
    )
    listings(
        monkeypatch,
        fake,
        [
            listing(2022, ueis=[UEI], eins=["141338310"]),
            listing(2025, eins=["141338310"]),
        ],
        {"auditee_name": "PLAINS MEDICAL CENTER", "auditee_ein": "141641730"},
    )
    notes = grantee_audit_coverage(uei=UEI)["notes"]

    assert any(
        n.startswith(f"FAC ties EIN 141338310 to UEI {UEI} directly: PLAINS MEDICAL")
        and "2022 audit lists both" in n
        for n in notes
    )
    assert any("by UEI and EIN for 2022 and by EIN for 2025" in n for n in notes)


# --- the tools' own behavior


def test_a_searched_uei_with_no_filing_is_not_said_to_have_filed(world):
    unfiled = "Q7Q7Q7Q7Q7Q7"
    world(SPLIT, [registration()])
    result = grantee_identity_resolution(uei=[UEI, unfiled])

    question = next(q for q in result["open_questions"] if unfiled in q)
    assert "Nothing has filed to FAC under it" in question
    note = next(
        n
        for n in result["notes"]
        if n.startswith(f"SAM.gov has no record of {unfiled}")
    )
    assert "nothing has filed to FAC under it" in note
    assert "also files under" not in note


def test_a_typo_uei_is_another_uei_not_another_ein(world):
    typo = audit(2026, "A1B2C3D4E5F7", EIN, "Valley Clinic")
    world([*SPLIT[2:], typo], [registration()])
    row = grantee_audit_coverage(uei=UEI, audit_year=2026)["years"][0]

    assert row["route"] == "own audit, under another UEI"
    assert row["linked_by"] == ["one character different from UEI A1B2C3D4E5F6"]


def test_a_year_that_is_not_twelve_months_says_so_on_its_own_row(world):
    """The year end moved from March to June, so FY2024 runs fifteen months.
    Asked for 2024 alone, the row still shows it."""
    long_year = {
        **audit(2024, UEI, EIN, "Valley Clinic"),
        "fy_start_date": "2023-04-01",
        "fy_end_date": "2024-06-30",
    }
    usual = {**audit(2025, UEI, EIN, "Valley Clinic"), "fy_start_date": "2024-07-01"}
    world([long_year, usual], [registration()])
    rows = grantee_audit_coverage(uei=UEI, audit_year=[2024, 2025])["years"]

    assert rows[1]["fy_start_date"] == "2023-04-01"
    assert rows[1]["period_months"] == 15
    assert "period_months" not in rows[0]


def test_a_year_with_several_filers_audits_is_pointed_out(world):
    """A county-wide audit and a department's, linked by name and place."""
    transit = [
        audit(y, "TRANS9TUE1X1", "481888888", "Valley Clinic") for y in (2024, 2025)
    ]
    world([*SPLIT, *transit], [registration()])
    result = grantee_audit_coverage(uei=UEI, audit_year=2025)

    assert len(result["years"]) == 2
    assert {row["filed_as"] for row in result["years"]} == {"Valley Clinic"}
    note = next(n for n in result["notes"] if n.startswith("2024-2025: more than"))
    assert "Use filed_as on each row" in note


def test_an_audit_filed_since_the_local_copy_says_who_filed_it(world):
    """filed_as comes from the live API's row when the local copy has none."""
    fake = world(SPLIT[:6], [registration()])
    fake.live = [audit(2025, "A1B2C3D4E5F8", EIN, "VALLEY CLINIC OF HAYS")]
    row = grantee_audit_coverage(uei=UEI, audit_year=2025)["years"][0]

    assert row["filed_since_snapshot"] is True
    assert row["route"] == "own audit, under another UEI"
    assert row["filed_as"] == "VALLEY CLINIC OF HAYS"


def test_a_year_with_nothing_found_cites_facs_search_page(world):
    world(SPLIT[2:5], [registration()])  # filed through 2023
    row = grantee_audit_coverage(uei=UEI, audit_year=2025)["years"][0]

    assert row["route"] == "no audit found"
    assert row["source_url"] == "https://app.fac.gov/dissemination/search/"


def test_with_funding_covers_the_years_asked_for(world, monkeypatch):
    """Not just the three latest: the year asked about, from its own start."""
    long_year = {
        **audit(2019, "GSA_MIGRATION", EIN),
        "fy_start_date": "2018-04-01",
    }
    world([long_year, *SPLIT[2:]], [registration()])
    asked = []

    def outlays(**kw):
        asked.append(kw)
        return {
            "total_outlays_in_period": 1.0,
            "awards": [],
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": "x"},
        }

    monkeypatch.setattr(coverage, "usaspending_sum_outlays", outlays)
    rows = grantee_audit_coverage(uei=UEI, audit_year=[2019, 2025], with_funding=True)

    assert all("usaspending" in row for row in rows["years"])
    assert [(k["fiscal_year_end"], k.get("period_start")) for k in asked] == [
        ("2025-06-30", None),
        ("2019-06-30", "2018-04-01"),
    ]


def test_a_sam_failure_while_linking_is_said_and_not_remembered(world, monkeypatch):
    """The earlier audits link only through SAM.gov's name and address."""
    fake = world([*SPLIT[:2], *SPLIT[3:]], [registration()])
    working, state = sam.registrations_by_uei, {"down": True}

    # One function throughout: the cache key includes the lookup function.
    def flaky(ueis):
        if state["down"]:
            raise ToolError("SAM.gov returned HTTP 429.")
        return working(ueis)

    monkeypatch.setattr(sam, "registrations_by_uei", flaky)
    failed = grantee_audit_coverage(uei=UEI)
    assert any(
        n.startswith("SAM.gov couldn't be asked (SAM.gov returned HTTP 429.)")
        for n in failed["notes"]
    )

    state["down"] = False
    retried = grantee_audit_coverage(uei=UEI)
    assert not any(n.startswith("SAM.gov couldn't be asked") for n in retried["notes"])
    assert fake.sam_calls


def test_the_second_tool_reuses_the_first_tools_sam_lookup(world):
    fake = world(SPLIT, [registration()])
    grantee_identity_resolution(uei=UEI)
    grantee_audit_coverage(uei=UEI)

    assert len(fake.sam_calls) == 1


def test_an_ambiguous_name_returns_candidates_from_both_tools(world):
    fake = world(SPLIT)
    fake.parents = [
        {"auditee_name": n, "auditee_uei": u, "auditee_ein": e, "auditee_city": "X",
         "auditee_state": "KS", "audit_year": "2024"}
        for n, u, e in (("VALLEY CLINIC", UEI, EIN), ("VALLEY CLINIC WEST", "B" * 12,
                                                     "481777777"))
    ]  # fmt: skip
    for tool in (grantee_identity_resolution, grantee_audit_coverage):
        result = tool(name="Valley Clinic")
        assert len(result["candidates"]) == 2 and "years" not in result


def test_when_fac_is_down_both_tools_answer_from_the_local_copy(world, monkeypatch):
    world([*SPLIT[:4], *SPLIT[5:]], [registration()])  # no audit for 2023

    def down(*args, **kwargs):
        raise ToolError("FAC API returned HTTP 500.")

    monkeypatch.setattr(fac, "get", down)
    monkeypatch.setattr(identity, "fac_search_covered_entities", down)

    resolved = grantee_identity_resolution(uei=UEI)
    assert resolved["listed_in_parent_audits"] == []
    assert any("couldn't be asked which audits list" in n for n in resolved["notes"])

    result = grantee_audit_coverage(uei=UEI)
    assert [row["audit_year"] for row in result["years"]][-1] == 2019
    notes = " ".join(result["notes"])
    assert "couldn't be asked which audits list this entity" in notes
    assert "the audits here are those in the local copy alone" in notes
    assert "No parent audit lists" not in notes


def test_a_never_filed_entity_is_not_said_to_have_no_parent_when_fac_is_down(
    world, monkeypatch
):
    world([], [registration()])

    def down(**kwargs):
        raise ToolError("FAC API returned HTTP 429.")

    monkeypatch.setattr(identity, "fac_search_covered_entities", down)
    notes = " ".join(grantee_audit_coverage(uei=UEI)["notes"])

    assert "couldn't be asked which audits list this entity" in notes
    assert "No parent audit lists" not in notes


def test_bad_input_is_rejected_before_any_lookup(world):
    fake = world(SPLIT)
    with pytest.raises(ToolError, match="Give a uei"):
        grantee_identity_resolution()
    with pytest.raises(ToolError, match="four-digit year"):
        grantee_audit_coverage(uei=UEI, audit_year=24)
    assert fake.calls == [] and fake.sam_calls == []
