"""Tests for the dict grantee_audit_coverage is built from (assemble), on a
fixture database with FAC and SAM.gov faked. The tool's own rows are tested in
test_grantee_tools.py."""

import copy
import datetime

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import fac, sam
from dat_mcp.tools import grantee_audit_coverage as module
from dat_mcp.tools import grantee_identity_resolution as identity
from dat_mcp.tools.grantee_audit_coverage import assemble as grantee_resolve
from dat_mcp.tools.grantee_identity_resolution import nine_months_after

UEI, TYPO_UEI, EIN = "JQNLHK8YLTU6", "JQNLHK8YLTUB", "953246624"


def audit(report_id, uei, ein, fy_end, accepted, spent, name="INLAND BEHAVIORAL"):
    return {
        "report_id": report_id,
        "auditee_uei": uei,
        "auditee_ein": ein,
        "audit_year": fy_end[:4],
        "auditee_name": name,
        "fy_end_date": fy_end,
        "fac_accepted_date": accepted,
        "total_amount_expended": spent,
        "entity_type": "non-profit",
    }


# Modeled on S1 and S14: a typo UEI in the latest year, and FY2024 missing.
GENERAL = [
    audit("2022-06-GSAFAC-0000000001", UEI, EIN, "2022-06-30", "2023-03-01", "4000000"),
    audit("2023-06-GSAFAC-0000000002", UEI, EIN, "2023-06-30", "2024-05-15", "3000000"),
    audit("2025-06-GSAFAC-0000000003", TYPO_UEI, EIN, "2025-06-30", "2026-01-08", "5e6"
          ),
    audit("2025-06-GSAFAC-0000000009", "ZZZZZZZZZZZ9", "111111111", "2025-06-30",
          "2026-01-08", "1", "OTHER"),
]  # fmt: skip

REGISTRATION = {
    "uei": UEI,
    "legal_business_name": "INLAND BEHAVIORAL AND HEALTH SERVICES, INC.",
    "dba_name": None,
    "registration_status": "Active",
    "registration_expiration_date": "2027-02-18",
    "entity_structure": "Corporate Entity (Not Tax Exempt)",
    "profit_structure": "Non-Profit Organization",
    "fiscal_year_end": "06/30",
    "physical_address": {"state": "CA"},
    "source_url": "https://sam.gov/entity/JQNLHK8YLTU6/coreData",
}


def award(ref, aln, program, amount, direct):
    prefix, extension = aln.split(".")
    return {
        "award_reference": ref,
        "federal_agency_prefix": prefix,
        "federal_award_extension": extension,
        "federal_program_name": program,
        "additional_award_identification": None,
        "amount_expended": amount,
        "is_direct": direct,
        "is_major": "N",
        "cluster_name": None,
    }


def general_row(name, uei, ein, city, state, year):
    return {
        "auditee_name": name,
        "auditee_uei": uei,
        "auditee_ein": ein,
        "auditee_city": city,
        "auditee_state": state,
        "audit_year": year,
    }


class FakeFac:
    """Answers fac.get by table and records every request."""

    def __init__(self):
        self.calls = []
        self.answers = {
            "additional_ueis": [],
            "additional_eins": [],
            # The general table is asked three different questions.
            "name_search": [],
            "gap_check": [],
            "parents": [],
            "federal_awards": [
                award("A-1", "93.498", "Provider Relief Fund", 2500000, "Y"),
                award("A-2", "16.575", "Crime Victim Assistance", 120000, "N"),
            ],
            "passthrough": [],
        }

    def get(self, table, params):
        self.calls.append((table, params))
        if table == "general":
            if "auditee_name" in params:
                table = "name_search"
            elif "audit_year" in params:
                table = "gap_check"
            else:
                table = "parents"
        # Tools edit the rows they get (fac_get_awards pops columns), so copy.
        return copy.deepcopy(self.answers[table])


@pytest.fixture
def fakes(database, monkeypatch):
    database({"general": GENERAL})
    fake = FakeFac()
    monkeypatch.setattr(fac, "get", fake.get)
    monkeypatch.setattr(
        sam,
        "registrations_by_uei",
        lambda ueis: ([REGISTRATION], [u for u in ueis if u != UEI], True),
    )
    monkeypatch.setattr(module, "today", lambda: datetime.date(2026, 11, 5))
    return fake


def test_history_covers_every_linked_identifier_with_due_dates(fakes):
    result = grantee_resolve(uei=UEI)

    years = [a["audit_year"] for a in result["audit_history"]]
    assert years == [2022, 2023, 2025]  # the typo-UEI filing is included
    fy2023 = result["audit_history"][1]
    assert fy2023["due_date"] == "2024-03-31"
    assert fy2023["days_late"] == 45
    assert result["audit_history"][0]["days_late"] == 0
    assert result["audit_history"][2]["filed_under"]["uei"] == TYPO_UEI
    assert {g["uei"] for g in result["identifiers"]} == {UEI, TYPO_UEI}


def test_missing_years_carry_the_prior_years_programs(fakes):
    result = grantee_resolve(ein="95-3246624")

    [gap] = result["years_without_filing"]
    assert gap["audit_year"] == 2024
    assert gap["prior_year_report_id"] == "2023-06-GSAFAC-0000000002"
    # Which year the programs are from, so they aren't read as the gap year's.
    assert gap["prior_year_audit_year"] == 2023
    assert gap["prior_year_fy_end_date"] == "2023-06-30"
    assert gap["prior_year_total_expended"] == 3000000
    assert [p["aln"] for p in gap["prior_year_programs"]] == ["93.498", "16.575"]
    assert gap["prior_year_programs"][1]["direct"] is False
    # S14: the prior year was mostly Provider Relief Fund, which says little.
    assert "one-time pandemic money" in gap["note"]
    # Every year since the first was checked against the live API, under
    # either kind of identifier, in one request.
    [check] = [p for t, p in fakes.calls if t == "general" and "audit_year" in p]
    assert check["audit_year"] == "in.(2022,2023,2024,2025,2026)"
    assert check["or"] == (f"(auditee_uei.in.({UEI},{TYPO_UEI}),auditee_ein.eq.{EIN})")


def test_a_year_not_asked_for_costs_no_requests(fakes):
    """The FY2024 gap's prior-year SEFA isn't read when only 2022 is asked."""
    module.grantee_audit_coverage(uei=UEI, audit_year=2022)
    assert not [t for t, p in fakes.calls if t == "federal_awards"]

    [row] = module.grantee_audit_coverage(uei=UEI, audit_year=2024)["years"]
    assert [p["aln"] for p in row["prior_year_programs"]] == ["93.498", "16.575"]


def test_prior_year_programs_add_up_each_programs_sefa_lines(fakes):
    # FAC's order, largest line first. Pass-through money is often several
    # lines per program, and the largest line here isn't the largest program.
    fakes.answers["federal_awards"] = [
        award("A-1", "21.027", "Fiscal Recovery Funds", 900, "N"),
        award("A-2", "93.917", "HIV Care Formula Grants", 700, "N"),
        award("A-3", "14.241", "Housing for Persons with AIDS", 650, "N"),
        award("A-4", "93.224", "Health Center Program", 600, "Y"),
        award("A-5", "93.917", "HIV CARE FORMULA GRANTS", 500, "N"),
        award("A-6", "14.241", "Housing for Persons with AIDS", 400, "N"),
        award("A-7", "93.917", "HIV Care Formula Grants", 300, "N"),
        award("A-8", "93.224", "Health Center Program", 200, "N"),
        award("A-9", "10.557", "WIC", 100, "Y"),
        award("A-10", "16.575", "Crime Victim Assistance", 50, "N"),
    ]
    [gap] = grantee_resolve(uei=UEI)["years_without_filing"]

    programs = gap["prior_year_programs"]
    assert [(p["aln"], p["amount_expended"], p["sefa_lines"]) for p in programs] == [
        ("93.917", 1500, 3),
        ("14.241", 1050, 2),
        ("21.027", 900, 1),
        ("93.224", 800, 2),
        ("10.557", 100, 1),
    ]
    assert programs[0]["program"] == "HIV Care Formula Grants"  # its largest line's
    assert programs[0]["direct"] is False
    assert programs[4]["direct"] is True
    # Direct and passed through at once: the amount each way, no flag.
    assert "direct" not in programs[3]
    assert programs[3]["direct_expended"] == 600
    assert programs[3]["pass_through_expended"] == 200
    not_shown = gap["prior_year_programs_not_shown"]
    assert not_shown == {"programs": 1, "amount_expended": 50}
    assert "prior_year_programs_incomplete" not in gap
    [request] = [p for t, p in fakes.calls if t == "federal_awards"]
    assert request["limit"] == module.SEFA_LINES_READ


@pytest.mark.parametrize(
    ("relief_lines", "other_lines", "noted"),
    [
        ([900], [600, 500], False),  # largest line, not the largest program
        ([500, 500], [900], True),  # no single largest line, but the largest program
    ],
)
def test_one_time_money_note_follows_the_largest_program(
    fakes, relief_lines, other_lines, noted
):
    lines = [award(f"R-{i}", "93.498", "Provider Relief Fund", amount, "Y")
             for i, amount in enumerate(relief_lines)]  # fmt: skip
    lines += [award(f"H-{i}", "93.224", "Health Center Program", amount, "Y")
              for i, amount in enumerate(other_lines)]  # fmt: skip
    lines.sort(key=lambda a: -a["amount_expended"])
    fakes.answers["federal_awards"] = lines

    [gap] = grantee_resolve(uei=UEI)["years_without_filing"]

    assert ("one-time pandemic money" in gap.get("note", "")) is noted


def test_a_sefa_longer_than_the_lines_read_is_flagged(fakes, monkeypatch):
    monkeypatch.setattr(module, "SEFA_LINES_READ", 2)  # the fixture SEFA has 2

    [gap] = grantee_resolve(uei=UEI)["years_without_filing"]

    assert "program totals may be low" in gap["prior_year_programs_incomplete"]


def test_a_year_filed_since_the_snapshot_closes_the_gap(fakes):
    fakes.answers["gap_check"] = [
        audit("2024-06-GSAFAC-0000000004", UEI, EIN, "2024-06-30", "2026-11-01", "2e6")
    ]
    result = grantee_resolve(uei=UEI)

    assert result["years_without_filing"] == []
    fy2024 = next(a for a in result["audit_history"] if a["audit_year"] == 2024)
    assert fy2024["filed_since_snapshot"] is True
    assert fy2024["days_late"] > 500


def test_an_audit_filed_since_the_snapshot_for_a_year_not_yet_due_is_shown(fakes):
    """An on-time filer's newest audit is for a year that isn't missing yet."""
    fakes.answers["gap_check"] = [
        audit("2026-06-GSAFAC-0000000006", UEI, EIN, "2026-06-30", "2026-10-26", "2e6")
    ]
    result = grantee_resolve(uei=UEI)

    newest = result["audit_history"][-1]
    assert newest["audit_year"] == 2026 and newest["filed_since_snapshot"] is True
    assert [g["audit_year"] for g in result["years_without_filing"]] == [2024]


def test_a_filing_the_local_copy_holds_is_not_added_twice(fakes):
    fakes.answers["gap_check"] = [dict(GENERAL[2])]
    result = grantee_resolve(uei=UEI)

    assert len(result["audit_history"]) == 3
    assert not any("filed_since_snapshot" in a for a in result["audit_history"])


def test_when_the_live_api_fails_the_local_copy_answers_and_says_so(fakes, monkeypatch):
    def get(table, params):
        if table == "general" and "audit_year" in params:
            raise ToolError("FAC API returned HTTP 429.")
        return fakes.get(table, params)

    monkeypatch.setattr(fac, "get", get)
    result = grantee_resolve(uei=UEI)

    assert [g["audit_year"] for g in result["years_without_filing"]] == [2024]
    assert result["notes"][0].startswith(
        "FAC's live API couldn't be asked (FAC API returned HTTP 429.)"
    )


def test_the_next_year_is_missing_only_once_its_due_date_passes(fakes, monkeypatch):
    def gaps():
        return [
            g["audit_year"] for g in grantee_resolve(uei=UEI)["years_without_filing"]
        ]

    monkeypatch.setattr(module, "today", lambda: datetime.date(2027, 3, 1))
    assert gaps() == [2024]  # FY2026 is due 2027-03-31

    monkeypatch.setattr(module, "today", lambda: datetime.date(2027, 4, 1))
    assert gaps() == [2026, 2024]


def test_sam_registration_and_typo_uei_are_reported(fakes):
    result = grantee_resolve(uei=UEI)

    [reg] = result["sam_registrations"]["registrations"]
    assert reg["legal_business_name"].startswith("INLAND")
    assert reg["state"] == "CA"
    assert result["sam_registrations"]["not_found"] == [TYPO_UEI]
    [note] = [n for n in result["notes"] if n.startswith("SAM.gov has no")]
    assert f"{TYPO_UEI}, registered or not. It is one character from {UEI}" in note
    assert "mistyped" in note
    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["sam"] == {
        "value": "06/30",
        "uei": UEI,
        "registration_status": "Active",
    }
    assert year_end["fac"] == {
        "value": "2025-06-30",
        "report_id": "2025-06-GSAFAC-0000000003",
    }
    assert year_end["used"] == {"source": "FAC's latest audit", "value": "2025-06-30"}
    assert year_end["sources_disagree"] is False
    assert not any("changed its year end" in n for n in result["notes"])


def test_an_unregistered_uei_is_described_by_the_entitys_other_ueis(
    fakes, database, monkeypatch
):
    """Only a one-character neighbor of a UEI SAM.gov has is called mistyped."""
    far = "ABCDEFGH1234"
    database({"general": [
        *GENERAL[:2],
        audit("2025-06-GSAFAC-0000000003", far, EIN, "2025-06-30", "2026-01-08", "5e6"),
    ]})  # fmt: skip
    result = grantee_resolve(uei=UEI)

    [note] = [n for n in result["notes"] if n.startswith("SAM.gov has no")]
    assert f"{far}, registered or not; this entity also files under {UEI}" in note
    assert "mistyped" not in note
    assert f"Is {far} a real UEI not shown publicly" in " ".join(
        result["open_questions"]
    )

    # No other UEI in SAM.gov: about half are real, so no typo is implied.
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )
    notes = [
        n for n in grantee_resolve(uei=UEI)["notes"] if n.startswith("SAM.gov has no")
    ]
    assert len(notes) == 2  # one per unregistered UEI
    assert all("no other UEI of this entity is in SAM.gov" in n for n in notes)


def test_a_government_body_with_a_gap_year_gets_no_parent_audit_note(fakes, database):
    """The statewide-audit note is for bodies that never filed, not for a gap."""
    rows = [{**row, "entity_type": "local"} for row in GENERAL[:3]]
    database({"general": rows})
    result = grantee_resolve(uei=UEI)

    assert [g["audit_year"] for g in result["years_without_filing"]] == [2024]
    assert not any("government body" in n for n in result["notes"])
    assert any("skipped a year" in n for n in result["notes"])


def test_unregistered_uei_with_few_fields_is_reported(fakes, monkeypatch):
    """SAM records leave out empty fields; an ID Assigned UEI has few."""
    assigned = {
        "uei": UEI,
        "legal_business_name": "INLAND BEHAVIORAL AND HEALTH SERVICES, INC.",
        "registration_status": "ID Assigned",
        "physical_address": {"state": "CA"},
    }
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([assigned], [], True)
    )
    result = grantee_resolve(uei=UEI)

    [reg] = result["sam_registrations"]["registrations"]
    assert reg["registration_status"] == "ID Assigned"
    assert reg["dba_name"] is None and reg["fiscal_year_end"] is None


def test_unsettled_not_found_is_not_called_a_typo(fakes, monkeypatch):
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), False)
    )
    result = grantee_resolve(uei=UEI)

    assert result["sam_registrations"]["not_found_incomplete"] is True
    assert any("too many duplicate records" in n for n in result["notes"])
    assert not any("usually a typo" in n for n in result["notes"])


def test_parent_listings_come_with_the_parents_name(fakes):
    parent_id = "2024-12-GSAFAC-0000000100"
    fakes.answers["additional_eins"] = [
        {
            "report_id": parent_id,
            "audit_year": "2024",
            "auditee_uei": "PARENTUEI001",
            "fac_accepted_date": "2025-06-01",
            "additional_ein": EIN,
        }
    ]
    fakes.answers["parents"] = [
        {
            "report_id": parent_id,
            "auditee_name": "PARENT HEALTH",
            "auditee_ein": "999999999",
        }
    ]
    result = grantee_resolve(uei=UEI)

    [parent] = result["covered_by"]
    assert parent["listed_by_name"] == "PARENT HEALTH"
    assert parent["listed_identifiers"]["eins"] == [EIN]
    # 2024 has no own filing, but the parent's listing covers it: not a gap.
    assert result["years_without_filing"] == []


def test_years_after_a_parent_listing_are_gaps_that_point_at_the_parent(
    fakes, database, monkeypatch
):
    """S17: listed by Covenant in 2021-22, nothing for 2023-24, own audit in 2025."""
    hospital, hospital_ein, parent = "NH6BKNCW8ZN3", "010212435", "W8HKMHJKCLN6"
    database({"general": [
        audit("2025-12-GSAFAC-0000000500", hospital, hospital_ein, "2025-12-31",
              "2026-09-30", "6e6", "SAINT JOSEPH HOSPITAL"),
    ]})  # fmt: skip
    fakes.answers["additional_eins"] = [
        {"report_id": f"{y}-12-CENSUS-0000000600", "audit_year": str(y),
         "auditee_uei": parent, "fac_accepted_date": f"{y + 1}-09-28",
         "additional_ein": hospital_ein}
        for y in (2022, 2021)  # FAC returns newest audit year first
    ] + [
        # Its own 2025 audit lists its EIN too; that is not a parent.
        {"report_id": "2025-12-GSAFAC-0000000500", "audit_year": "2025",
         "auditee_uei": hospital, "fac_accepted_date": "2026-09-30",
         "additional_ein": hospital_ein}
    ]  # fmt: skip
    covenant = "COVENANT HEALTH, INC."
    fakes.answers["parents"] = [
        {"report_id": f"{y}-12-CENSUS-0000000600", "auditee_name": covenant,
         "auditee_ein": "222484505"}
        for y in (2021, 2022)
    ]  # fmt: skip
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    result = grantee_resolve(uei=hospital, ein=hospital_ein)

    assert [c["audit_year"] for c in result["covered_by"]] == ["2022", "2021"]
    assert [g["audit_year"] for g in result["years_without_filing"]] == [2024, 2023]
    notes = [n for n in result["notes"] if n.startswith("No own filing")]
    assert len(notes) == 2  # one per gap year
    assert (
        "for 2023. COVENANT HEALTH, INC. listed this entity as covered in 2022"
        in (notes[1])
    )
    assert f"fac_search_audits(uei={parent})" in notes[1]


def test_an_unknown_uei_returns_leads_and_the_sam_entity_type(fakes, monkeypatch):
    for_profit = {
        **REGISTRATION,
        "uei": "ZZZZZZZZZZZ8",
        "profit_structure": "For Profit",
    }
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([for_profit], [], True)
    )

    result = grantee_resolve(uei="ZZZZZZZZZZZ8")  # one character off OTHER's UEI

    assert result["audit_history"] == []
    assert result["sam_registrations"]["registrations"][0]["profit_structure"] == (
        "For Profit"
    )
    assert result["notes"][0].startswith("Nothing has filed")
    # The entity type still speaks when nothing has filed.
    assert any("registers this entity as for-profit" in n for n in result["notes"])


def test_not_for_profit_is_not_read_as_for_profit(fakes, monkeypatch):
    nonprofit = {
        **REGISTRATION,
        "uei": "ZZZZZZZZZZZ8",
        "entity_structure": "Corporate Entity (Tax Exempt)",
        "profit_structure": "Other Not For Profit Organization",
    }
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([nonprofit], [], True)
    )

    result = grantee_resolve(uei="ZZZZZZZZZZZ8")

    assert not any("for-profit" in n for n in result["notes"])


def test_a_never_filed_entity_is_found_in_a_parents_ein_listing(fakes, monkeypatch):
    """A subsidiary with no filing of its own, listed by a parent by EIN only."""
    child, child_ein, parent = "ZZZZZZZZZZZ8", "900000123", "PARENTUEI001"
    parent_id = "2025-12-GSAFAC-0000000800"
    fakes.answers["additional_eins"] = [
        {"report_id": parent_id, "audit_year": "2025", "auditee_uei": parent,
         "fac_accepted_date": "2026-09-30", "additional_ein": child_ein}
    ]  # fmt: skip
    fakes.answers["parents"] = [
        {"report_id": parent_id, "auditee_name": "PARENT HEALTH SYSTEM",
         "auditee_ein": "900000456"}
    ]  # fmt: skip
    government = {
        **REGISTRATION,
        "uei": child,
        "entity_structure": "U.S. Government Entity",
        "profit_structure": None,
    }
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([government], [], True)
    )

    result = grantee_resolve(uei=child, ein=child_ein)

    [listing] = result["covered_by"]
    assert listing["listed_by_name"] == "PARENT HEALTH SYSTEM"
    assert listing["listed_identifiers"]["eins"] == [child_ein]
    assert any("Parent audits list this entity as covered (2025)" in n
               for n in result["notes"])  # fmt: skip
    assert any("government body" in n for n in result["notes"])
    assert not any("not here" in c for c in result["caveats"])


def test_every_past_due_year_since_the_last_filing_is_a_gap(fakes, monkeypatch):
    # Latest filing FY ending 2025-06-30. FY2026 was due 2027-03-31 and FY2027
    # 2028-03-31; both have passed. FY2028 is not due until 2029-03-31.
    monkeypatch.setattr(module, "today", lambda: datetime.date(2028, 4, 1))

    gaps = grantee_resolve(uei=UEI)["years_without_filing"]

    assert [g["audit_year"] for g in gaps] == [2027, 2026, 2024]


def test_two_organizations_sharing_a_uei_are_called_out(fakes, database, monkeypatch):
    """S10: Mercy Housing, Inc. and Mercy Housing Corporation share a UEI."""
    shared = "C4PCPEMHEUR6"
    database({"general": [
        audit("2023-12-GSAFAC-0000000701", shared, "470646706", "2023-12-31",
              "2024-09-01", "9e6", "MERCY HOUSING, INC."),
        audit("2023-12-GSAFAC-0000000702", shared, "382376561", "2023-12-31",
              "2024-09-01", "1e6", "MERCY HOUSING CORPORATION"),
    ]})  # fmt: skip
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    result = grantee_resolve(uei=shared)

    assert len(result["audit_history"]) == 2
    assert result["notes"][0].startswith("The identifiers span 2 EINs")
    assert "a UEI can be shared by unrelated organizations" in result["notes"][0]
    assert "auditee_name" not in result["audit_history"][0]  # rows stay small
    # The note names nothing that grantee_identity_resolution's answer lacks.
    [note] = [
        n
        for n in identity.grantee_identity_resolution(uei=shared)["notes"]
        if n.startswith("The identifiers span 2 EINs")
    ]
    assert "audit_history" not in note
    assert "filed_under on each of grantee_audit_coverage's rows" in note


def test_name_matching_several_organizations_returns_candidates(fakes):
    shared = "C4PCPEMHEUR6"
    fakes.answers["name_search"] = [
        general_row("MERCY HOUSING INC", shared, "470646706", "DENVER", "CO", "2024"),
        general_row("MERCY HOUSING CORP", shared, "382376561", "LANSING", "MI", "2024"),
    ]
    result = grantee_resolve(name="Mercy Housing")

    assert len(result["candidates"]) == 2
    assert "audit_history" not in result
    assert "Pick one" in result["caveats"][0]


def test_a_unique_name_resolves_through_to_the_history(fakes):
    fakes.answers["name_search"] = [
        general_row("INLAND BEHAVIORAL", UEI, EIN, "SAN BERNARDINO", "CA", "2023")
    ]
    result = grantee_resolve(name="Inland Behavioral", state="ca")

    assert [a["audit_year"] for a in result["audit_history"]] == [2022, 2023, 2025]


def test_a_name_search_cut_off_at_one_organization_is_not_settled(fakes):
    # A page of one EIN's filings can hide other organizations of the name.
    fakes.answers["name_search"] = [
        general_row("INLAND BEHAVIORAL", UEI, EIN, "SAN BERNARDINO", "CA", "2023")
    ] * identity.NAME_FILINGS_READ
    result = grantee_resolve(name="Inland")

    assert [c["ein"] for c in result["candidates"]] == [EIN]
    assert "other organizations may match too" in result["caveats"][0]


def test_open_questions_name_what_is_unresolved(fakes):
    result = grantee_resolve(uei=UEI)

    questions = " ".join(result["open_questions"])
    assert "required for fiscal year 2024" in questions
    assert f"Is {TYPO_UEI} a mistyped form" in questions
    assert "change its fiscal year end" not in questions  # FAC and SAM agree
    assert not any(w in questions.lower() for w in ("delinquent", "compliant"))


def test_with_funding_adds_usaspending_to_gaps_and_recent_years(fakes, monkeypatch):
    asked = []

    def fake_sum_outlays(fiscal_year_end, uei, max_awards, period_start=None):
        asked.append((fiscal_year_end, uei))
        return {
            "total_outlays_in_period": 1234.5,
            "awards_examined": 2,
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": f"year ending {fiscal_year_end}"},
            "awards": [
                {
                    "award_id": "H-1",
                    "outlays_in_period": 1234.5,
                    "source_url": "https://www.usaspending.gov/award/H-1",
                },
                {
                    "award_id": "H-2",
                    "outlays_in_period": 0.0,
                    "source_url": "https://www.usaspending.gov/award/H-2",
                },
            ],
        }

    monkeypatch.setattr(module, "usaspending_sum_outlays", fake_sum_outlays)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    # Three most recent audit years plus the FY2024 gap, under each UEI the
    # entity filed under, the latest filing's first.
    assert asked == [
        (fy, uei)
        for fy in ("2025-06-30", "2023-06-30", "2022-06-30", "2024-06-30")
        for uei in (TYPO_UEI, UEI)
    ]
    by_year = {row["audit_year"]: row for row in result["years"]}
    gap = by_year[2024]
    assert gap["route"] == "no audit found"
    # An award returned under both UEIs is counted once.
    assert gap["usaspending"]["total_outlays_in_period"] == 1234.5
    assert gap["usaspending"]["ueis"] == [TYPO_UEI, UEI]
    # The awards that drew money, not a count of the awards examined: two
    # examined, one drew, was read as two awards with drawdowns.
    assert gap["usaspending"]["awards_with_outlays"] == [
        {
            "award_id": "H-1",
            "outlays_in_period": 1234.5,
            "uei": TYPO_UEI,
            "source_url": "https://www.usaspending.gov/award/H-1",
        }
    ]
    assert "awards_examined" not in gap["usaspending"]
    # The audits either side give the twelve months to the expected year end.
    assert "period_source" not in gap["usaspending"]
    assert by_year[2025]["usaspending"]["period"].endswith("2025-06-30")

    plain = module.grantee_audit_coverage(uei=UEI)
    assert not any("usaspending" in row for row in plain["years"])


def test_requires_something_to_search(fakes):
    with pytest.raises(ToolError):
        grantee_resolve()


def test_nine_months_after_handles_month_ends():
    # A year that ends on the last day of a month is due at the end of a month.
    assert nine_months_after(datetime.date(2025, 6, 30)) == datetime.date(2026, 3, 31)
    assert nine_months_after(datetime.date(2025, 1, 31)) == datetime.date(2025, 10, 31)
    assert nine_months_after(datetime.date(2024, 5, 31)) == datetime.date(2025, 2, 28)
    assert nine_months_after(datetime.date(2025, 12, 31)) == datetime.date(2026, 9, 30)
    assert nine_months_after(datetime.date(2024, 9, 30)) == datetime.date(2025, 6, 30)
    # Mid-month year ends keep their day.
    assert nine_months_after(datetime.date(2025, 6, 15)) == datetime.date(2026, 3, 15)


def test_leads_only_holds_when_several_identifiers_are_searched(fakes, monkeypatch):
    """Two identifiers put SEVERAL_SEARCHED first; neighbors are still only leads."""
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    # ZZZZZZZZZZZ8 is one character off OTHER's UEI; neither identifier filed.
    result = grantee_resolve(uei="ZZZZZZZZZZZ8", ein="123456780")

    assert result["audit_history"] == []
    assert result["notes"][0].startswith("Nothing has filed")
    # A UEI that never filed gets the possibilities, not counts from FAC filers.
    [note] = [n for n in result["notes"] if n.startswith("SAM.gov has no")]
    assert "real but not shown publicly" in note


def test_a_leap_day_year_end_does_not_crash(fakes, database):
    database({"general": [
        audit("2024-02-GSAFAC-0000000800", UEI, EIN, "2024-02-29", "2024-11-01", "1e6"),
    ]})  # fmt: skip
    result = grantee_resolve(uei=UEI)

    # FY ending 2025-02-28 was due 2025-11-30, before today (2026-11-05).
    assert [g["audit_year"] for g in result["years_without_filing"]] == [2025]


def test_a_15_month_year_is_summed_from_its_own_start(fakes, database, monkeypatch):
    """usaspending-changed-year-end: March year ends, then a 15-month FY2024.
    FAC gives each audit's fy_start_date, so the row carries it and the
    year's outlays are summed from it."""
    database({"general": [
        {**audit("2023-03-GSAFAC-0000000811", UEI, EIN, "2023-03-31", "2023-12-01",
                 "1"), "fy_start_date": "2022-04-01"},
        {**audit("2024-06-GSAFAC-0000000812", UEI, EIN, "2024-06-30", "2025-03-01",
                 "1"), "fy_start_date": "2023-04-01"},
        {**audit("2025-06-GSAFAC-0000000813", UEI, EIN, "2025-06-30", "2026-03-01",
                 "1"), "fy_start_date": "2024-07-01"},
    ]})  # fmt: skip
    asked = []

    def fake_sum_outlays(fiscal_year_end, uei, max_awards, period_start=None):
        asked.append((fiscal_year_end, period_start))
        return {
            "total_outlays_in_period": 1.0,
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": fiscal_year_end},
            "awards": [],
        }

    monkeypatch.setattr(module, "usaspending_sum_outlays", fake_sum_outlays)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    by_year = {row["audit_year"]: row for row in result["years"]}
    fy2024 = by_year[2024]
    assert (fy2024["fy_start_date"], fy2024["period_months"]) == ("2023-04-01", 15)
    # The years either side are 12 months and get nothing.
    assert "period_months" not in by_year[2023]
    assert "period_months" not in by_year[2025]
    assert ("2024-06-30", "2023-04-01") in asked
    assert ("2025-06-30", None) in asked


def _asked_outlays(monkeypatch):
    """Fake usaspending_sum_outlays; returns the (year end, period_start) asked."""
    asked = []

    def fake_sum_outlays(fiscal_year_end, uei, max_awards, period_start=None):
        asked.append((fiscal_year_end, period_start))
        return {
            "total_outlays_in_period": 1.0,
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": fiscal_year_end},
            "awards": [],
        }

    monkeypatch.setattr(module, "usaspending_sum_outlays", fake_sum_outlays)
    return asked


def test_a_gap_year_runs_between_the_audits_either_side(fakes, database, monkeypatch):
    """FY2023 is missing, and the year end moved from March to June after it.
    The latest audit puts the gap's expected end at June 30, but FY2022 ended
    March 31 and FY2024 began April 1: twelve months to June 30 would count
    April to June 2023 in two years and leave April to June 2022 out."""
    database({"general": [
        audit("2022-03-GSAFAC-0000000821", UEI, EIN, "2022-03-31", "2022-11-01", "1"),
        audit("2024-03-GSAFAC-0000000822", UEI, EIN, "2024-03-31", "2024-11-01", "1"),
        {**audit("2025-06-GSAFAC-0000000823", UEI, EIN, "2025-06-30", "2026-01-15",
                 "1"), "fy_start_date": "2024-04-01"},
    ]})  # fmt: skip
    asked = _asked_outlays(monkeypatch)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    [gap] = [row for row in result["years"] if row["route"] == "no audit found"]
    assert (gap["audit_year"], gap["expected_fy_end_date"]) == (2023, "2023-06-30")
    assert ("2023-03-31", None) in asked
    assert ("2023-06-30", None) not in asked
    assert gap["usaspending"]["period_source"] == (
        "the day after FY2022's audit ended to the day before FY2024's audit began"
    )
    assert "period_months" not in gap["usaspending"]  # still twelve months


def test_a_gap_year_longer_than_twelve_months_says_so(fakes, database, monkeypatch):
    """June year ends, then FY2023 missing, then September year ends: the gap
    year ran 15 months, from July 2022 to September 2023."""
    database({"general": [
        audit("2022-06-GSAFAC-0000000831", UEI, EIN, "2022-06-30", "2023-01-01", "1"),
        audit("2024-09-GSAFAC-0000000832", UEI, EIN, "2024-09-30", "2025-03-01", "1"),
        audit("2025-09-GSAFAC-0000000833", UEI, EIN, "2025-09-30", "2026-03-01", "1"),
    ]})  # fmt: skip
    asked = _asked_outlays(monkeypatch)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    [gap] = [row for row in result["years"] if row["route"] == "no audit found"]
    assert gap["expected_fy_end_date"] == "2023-09-30"
    assert ("2023-09-30", "2022-07-01") in asked
    assert gap["usaspending"]["period_months"] == 15
    assert gap["usaspending"]["period_source"].startswith(
        "the day after FY2022's audit ended to the day before FY2024's audit began"
    )


def test_a_gap_year_absorbed_by_a_long_year_sums_nothing(fakes, database, monkeypatch):
    """Helping Harvest Fresh Food Bank (C4LRU9MWKKC8): FY2022 ended
    2022-12-31 and FY2024 ran 18 months from 2023-01-01, so FY2023 has no
    months. Twelve months to June 30 would count half of each audit's year."""
    database({"general": [
        audit("2022-12-GSAFAC-0000000851", UEI, EIN, "2022-12-31", "2023-06-01", "1"),
        {**audit("2024-06-GSAFAC-0000000852", UEI, EIN, "2024-06-30", "2025-03-01",
                 "1"), "fy_start_date": "2023-01-01"},
        audit("2025-06-GSAFAC-0000000853", UEI, EIN, "2025-06-30", "2026-03-01", "1"),
    ]})  # fmt: skip
    asked = _asked_outlays(monkeypatch)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    [gap] = [row for row in result["years"] if row["route"] == "no audit found"]
    assert gap["audit_year"] == 2023
    assert gap["usaspending"] == {
        "period_source": (
            "none: the day after FY2022's audit ended (2023-01-01) is after the "
            "day before FY2024's audit began (2022-12-31), so no months fall in "
            "FY2023 and nothing was summed"
        )
    }
    # Only the three audits' own years were asked about.
    assert {end for end, _ in asked} == {"2022-12-31", "2024-06-30", "2025-06-30"}


def test_a_gap_year_ignores_impossible_dates_either_side(fakes, database, monkeypatch):
    """A start date years off on the next audit, or a year before that ended
    after the gap's end, is a data error: the gap keeps its twelve months."""
    database({"general": [
        {**audit("2022-06-GSAFAC-0000000841", UEI, EIN, "2023-08-31", "2023-01-01",
                 "1"), "audit_year": "2022"},
        {**audit("2024-06-GSAFAC-0000000842", UEI, EIN, "2024-06-30", "2025-01-01",
                 "1"), "fy_start_date": "2015-07-01"},
        audit("2025-06-GSAFAC-0000000843", UEI, EIN, "2025-06-30", "2026-01-01", "1"),
    ]})  # fmt: skip
    asked = _asked_outlays(monkeypatch)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    [gap] = [row for row in result["years"] if row["route"] == "no audit found"]
    assert gap["audit_year"] == 2023
    assert ("2023-06-30", None) in asked
    assert "period_source" not in gap["usaspending"]


def test_audits_with_no_year_end_date_do_not_crash(fakes, database):
    row = audit("2023-06-GSAFAC-0000000801", UEI, EIN, "2023-06-30", "2024-01-01", "1")
    database({"general": [{**row, "fy_end_date": None}]})
    result = grantee_resolve(uei=UEI)

    assert [a["audit_year"] for a in result["audit_history"]] == [2023]
    assert result["years_without_filing"] == []


def test_with_funding_covers_at_most_three_gap_years(fakes, database, monkeypatch):
    database({"general": [
        audit("2015-06-GSAFAC-0000000901", UEI, EIN, "2015-06-30", "2016-01-01", "1"),
        audit("2025-06-GSAFAC-0000000902", UEI, EIN, "2025-06-30", "2026-01-01", "1"),
    ]})  # fmt: skip
    asked = []

    def fake_sum_outlays(fiscal_year_end, uei, max_awards, period_start=None):
        asked.append(fiscal_year_end)
        return {
            "total_outlays_in_period": 1.0,
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": fiscal_year_end},
            "awards": [],
        }

    monkeypatch.setattr(module, "usaspending_sum_outlays", fake_sum_outlays)
    result = module.grantee_audit_coverage(uei=UEI, with_funding=True)

    gaps = [row for row in result["years"] if row["route"] == "no audit found"]
    assert len(gaps) == 9  # 2016 through 2024
    # Both audits, plus the three most recent gap years.
    assert sorted(asked) == [
        "2015-06-30", "2022-06-30", "2023-06-30", "2024-06-30", "2025-06-30"
    ]  # fmt: skip


def test_every_parent_is_named_and_a_cut_off_listing_is_flagged(fakes, monkeypatch):
    reports = [
        {
            "audit_year": "2022",
            "report_id": f"2022-12-GSAFAC-{i:010d}",
            "listed_by_auditee_uei": "PARENTUEI001",
            "covered_ueis": [],
            "covered_eins": [EIN],
            "source_url": "https://app.fac.gov/",
        }
        for i in range(25)
    ]
    monkeypatch.setattr(
        identity,
        "fac_search_covered_entities",
        lambda **kw: {"reports": reports, "more_available": True},
    )
    fakes.answers["parents"] = [
        {"report_id": r["report_id"], "auditee_name": "PARENT", "auditee_ein": "1"}
        for r in reports
    ]
    result = grantee_resolve(uei=UEI)

    lookups = [p for t, p in fakes.calls if t == "general" and "report_id" in p]
    assert len(lookups) == 2  # 25 report ids, 20 at a time
    assert all(c["listed_by_name"] == "PARENT" for c in result["covered_by"])
    assert result["notes"][0].startswith("The parent listings were cut")


def test_leads_for_an_unfiled_identifier_stay_out_of_the_history(fakes, database):
    """The UEI filed; the EIN given with it never did, but has a neighbor."""
    database({"general": [
        *GENERAL,
        audit("2024-06-GSAFAC-0000000010", "NEIGHBORUEI1", "123456789", "2024-06-30",
              "2025-01-01", "1", "NEIGHBOR"),
    ]})  # fmt: skip
    result = grantee_resolve(uei=UEI, ein="123456780")

    assert [a["audit_year"] for a in result["audit_history"]] == [2022, 2023, 2025]
    [lead] = result["unconfirmed_leads"]
    assert lead["ein"] == "123456789"
    assert result["notes"][0].startswith("Nothing has filed under 123456780")


def bmf(ein, name, state="CA", month="06"):
    """An eo_bmf row: an independent 501(c)(3) with the given year-end month."""
    return {
        "EIN": ein,
        "NAME": name,
        "CITY": "SAN BERNARDINO",
        "STATE": state,
        "GROUP": "0000",
        "SUBSECTION": "03",
        "AFFILIATION": "3",
        "STATUS": "01",
        "ACCT_PD": month,
    }


def test_a_missing_ein_one_digit_from_the_irs_record_names_it(
    fakes, database, monkeypatch
):
    """The EIN on the filing is one digit off the one the IRS lists by its name."""
    database({
        "general": [
            audit("2024-06-GSAFAC-0000000901", UEI, "953246625", "2024-06-30",
                  "2025-01-01", "1e6", "INLAND BEHAVIORAL AND HEALTH SERVICES"),
        ],
        "eo_bmf": [bmf("953246624", "INLAND BEHAVIORAL AND HEALTH SERVICES INC")],
    })  # fmt: skip
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )
    result = grantee_resolve(uei=UEI)

    note = next(n for n in result["irs_notes"] if n.startswith("EIN 953246625"))
    assert "one digit from 953246624, which the IRS lists as INLAND" in note
    assert "compare the addresses" in note


def test_a_government_bodys_missing_ein_says_little(fakes, database, monkeypatch):
    database({
        "general": [{**audit("2024-06-GSAFAC-0000000902", UEI, "956000000",
                             "2024-06-30", "2025-01-01", "1e6", "COUNTY OF INLAND"),
                     "entity_type": "local"}],
        "eo_bmf": [bmf("123456789", "SOMEONE ELSE")],
    })  # fmt: skip
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )
    result = grantee_resolve(uei=UEI)

    note = next(n for n in result["irs_notes"] if n.startswith("EIN 956000000"))
    assert "5% of government auditees' EINs" in note
    assert "one digit" not in note


def test_the_entity_type_is_the_one_most_filings_give(fakes, database, monkeypatch):
    """Konza: FAC says non-profit for years, then local once; still a nonprofit."""
    rows = [
        audit(f"{y}-06-GSAFAC-000000091{i}", UEI, "956000001", f"{y}-06-30",
              f"{y + 1}-01-01", "1e6", "INLAND HEALTH CENTER")
        for i, y in enumerate((2022, 2023, 2024))
    ]  # fmt: skip
    rows[-1]["entity_type"] = "local"
    database({"general": rows, "eo_bmf": [bmf("123456789", "SOMEONE ELSE")]})
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )
    result = grantee_resolve(uei=UEI)

    note = next(n for n in result["irs_notes"] if n.startswith("EIN 956000001"))
    assert "92% of nonprofit auditees' EINs" in note


def test_without_irs_files_the_skipped_check_is_said_out_loud(fakes):
    result = grantee_resolve(uei=UEI)

    assert result["irs_records"]["skipped"].startswith("the IRS EO BMF isn't")
    assert any(n.startswith("The IRS check didn't run") for n in result["irs_notes"])
    assert not any("IRS" in q for q in result["irs_questions"])


def test_an_irs_error_is_reported_not_raised(fakes, database, monkeypatch):
    database({"general": GENERAL, "eo_bmf": [bmf(EIN, "INLAND BEHAVIORAL")]})

    def broken(**kwargs):
        raise ToolError("The local database is locked.")

    monkeypatch.setattr(identity, "irs_search_exempt_orgs", broken)
    result = grantee_resolve(uei=UEI)

    assert result["irs_records"]["skipped"] == "The local database is locked."
    assert result["audit_history"]  # the rest of the answer is still there


def test_an_ein_the_irs_lacks_is_stated_as_fact(fakes, database, monkeypatch):
    """S2: Konza's real EIN in 2022, a typo of it in 2023 and 2024."""
    konza = "JKNCXKKFEPH1"
    database({
        "general": [
            audit("2022-12-GSAFAC-0000000801", konza, "481150706", "2022-12-31",
                  "2023-09-01", "4e6", "KONZA PRAIRIE COMMUNITY HEALTH CENTER, INC."),
            audit("2023-12-GSAFAC-0000000802", konza, "148115070", "2023-12-31",
                  "2024-09-01", "4e6", "KONZA PRAIRIE COMMUNITY HEALTH CENTER, INC."),
            audit("2024-12-GSAFAC-0000000803", konza, "148115070", "2024-12-31",
                  "2025-09-01", "4e6", "KONZA PRAIRIE COMMUNITY HEALTH CENTER, INC."),
        ],
        "eo_bmf": [
            bmf("481150706", "KONZA PRAIRIE COMMUNITY HEALTH CENTER INC", "KS", "12")
        ],
    })  # fmt: skip

    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )
    result = grantee_resolve(uei=konza)

    irs = result["irs_records"]
    [record] = irs["records"]
    assert record["ein"] == "481150706"
    assert record["name_matches_fac"] is True
    assert irs["not_found"] == ["148115070"]
    note = next(n for n in result["irs_notes"] if n.startswith("EIN 148115070"))
    assert note.startswith(
        "EIN 148115070 (FAC filings for 2023, 2024) is not in the IRS's list"
    )
    assert "The IRS does list 481150706" in note
    assert "typo" not in note  # a possibility, not a conclusion
    assert any(q.startswith("Why isn't EIN 148115070") for q in result["irs_questions"])
    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["irs"] == [{"ein": "481150706", "month": 12}]
    assert year_end["sources_disagree"] is False  # FAC and the IRS: December
    assert irs["listed_by_skipped"] == "Schedule R isn't in the local database"
    assert any(n.startswith("listed_by is empty because") for n in result["irs_notes"])
    assert any("local copy of IRS" in c for c in result["irs_caveats"])


def test_sefa_notes_naming_a_linked_ein_the_irs_lacks_are_on_its_entry(
    fakes, database, monkeypatch
):
    """irs-shared-ein: searched by UEI, the entity also filed under 542003078,
    which another auditee's SEFA notes give to its subsidiary."""
    havens = "R7PNMGWQ4QH8"
    database({
        "general": [
            audit("2022-06-GSAFAC-0000000020", havens, "542006078", "2022-06-30",
                  "2023-03-01", "1e6", "COMMUNITY HAVENS INC"),
            audit("2023-06-GSAFAC-0000000021", havens, "542003078", "2023-06-30",
                  "2024-03-01", "1e6", "COMMUNITY HAVENS INC"),
            # A second entry under the same EIN: mentions go on the latest only.
            audit("2024-06-GSAFAC-0000000022", "R7PNMGWQ4QH9", "542003078",
                  "2024-06-30", "2025-03-01", "1e6", "COMMUNITY HAVENS INC"),
            audit("2023-06-GSAFAC-0000000030", "GMFLJZLNKK34", "237133975",
                  "2023-06-30", "2024-03-01", "9e7", "CHESAPEAKE HOSPITAL AUTHORITY"),
        ],
        "eo_bmf": [bmf("542006078", "COMMUNITY HAVENS INC", "VA")],
        "notes_to_sefa": [
            {"report_id": "2023-06-GSAFAC-0000000030",
             "title": "5. Entities included in the Schedule",
             "content": "54-2003078 Positron Emission Tomography of Hampton Roads"},
            # The entity's own note naming its EIN isn't another's mention.
            {"report_id": "2023-06-GSAFAC-0000000021", "title": "1. Basis",
             "content": "Community Havens, EIN 542003078, prepared this schedule."},
        ],
    })  # fmt: skip
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    result = grantee_resolve(uei=havens)

    entries = [g for g in result["identifiers"] if g["ein"] == "542003078"]
    assert [g["uei"] for g in entries] == [havens, "R7PNMGWQ4QH9"]
    [mention] = entries[-1]["named_in_sefa_notes"]
    assert mention["report_id"] == "2023-06-GSAFAC-0000000030"
    assert mention["auditee_name"] == "CHESAPEAKE HOSPITAL AUTHORITY"
    assert "Positron Emission Tomography of Hampton Roads" in mention["snippet"]
    assert "ein" not in mention  # it's on the entry
    assert all("named_in_sefa_notes" not in g for g in entries[:-1])
    assert all(
        "named_in_sefa_notes" not in g
        for g in result["identifiers"]
        if g["ein"] == "542006078"  # the IRS has this one
    )
    assert "source_notice" in result
    note = next(n for n in result["irs_notes"] if n.startswith("EIN 542003078"))
    assert note.endswith(
        "Other audits' SEFA notes name EIN 542003078: CHESAPEAKE HOSPITAL "
        "AUTHORITY (2023); see named_in_sefa_notes on its entry in identifiers. "
        "A note gives the EIN to the organization it names, which may not be the "
        "one that filed under it."
    )


def test_a_never_filed_entity_gets_the_notes_that_name_its_ein(fakes, database):
    """Surgery Center of Chesapeake never filed; Chesapeake Hospital
    Authority's notes list it among the entities in its schedule."""
    database({
        "general": [
            audit("2023-06-GSAFAC-0000000030", "GMFLJZLNKK34", "237133975",
                  "2023-06-30", "2024-03-01", "9e7", "CHESAPEAKE HOSPITAL AUTHORITY"),
        ],
        "notes_to_sefa": [
            {"report_id": "2023-06-GSAFAC-0000000030",
             "title": "5. Entities included in the Schedule",
             "content": "54-2010320 Surgery Center of Chesapeake, LLC"},
        ],
    })  # fmt: skip

    result = grantee_resolve(ein="542010320")

    [mention] = result["named_in_sefa_notes"]
    assert mention["ein"] == "542010320"
    assert mention["auditee_name"] == "CHESAPEAKE HOSPITAL AUTHORITY"
    assert "Surgery Center of Chesapeake" in mention["snippet"]
    assert "source_notice" in result
    # The caveat that points at the field now has something to point at.
    assert any("(named_in_sefa_notes)" in c for c in result["caveats"])


def test_no_sefa_mentions_when_every_ein_is_in_the_irs_file(fakes, database):
    database({"general": GENERAL, "eo_bmf": [bmf(EIN, "INLAND BEHAVIORAL"),
                                             bmf("111111111", "OTHER")]})  # fmt: skip

    result = grantee_resolve(uei=UEI)

    assert not any("named_in_sefa_notes" in g for g in result["identifiers"])
    assert "source_notice" not in result


def test_an_irs_name_and_year_end_that_disagree_are_flagged(fakes, database):
    """The EIN belongs to someone else in IRS records, with a December year end."""
    database({
        "general": GENERAL,
        "eo_bmf": [bmf(EIN, "POSITRON EMISSION TOMOGRAPHY OF HAMPTON ROADS",
                       "VA", "12")],
    })  # fmt: skip

    result = grantee_resolve(uei=UEI)

    [record] = result["irs_records"]["records"]
    assert record["name_matches_fac"] is False
    assert any(
        n.startswith(f"The IRS lists EIN {EIN} as POSITRON EMISSION")
        for n in result["irs_notes"]
    )
    assert any(f"Does EIN {EIN} belong" in q for q in result["irs_questions"])
    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["sources_disagree"] is True
    assert year_end["used"]["source"] == "FAC's latest audit"
    note = next(n for n in result["notes"] if n.startswith("Fiscal year ends disagree"))
    assert (
        "FAC's latest audit: 2025-06-30; SAM.gov: 06/30; the IRS (EIN 953246624): "
        "December" in note
    )
    assert any(
        q.startswith("Did the entity change its fiscal year end? FAC's latest audit, ")
        for q in result["open_questions"]
    )


def test_without_a_fac_audit_sam_and_the_irs_are_compared(fakes, database, monkeypatch):
    """No FAC filing: SAM.gov's year end is used and checked against the IRS's."""
    new_uei, new_ein = "ABCDEFGH1234", "999888777"
    database({
        "general": GENERAL,
        "eo_bmf": [bmf(new_ein, "NEW CLINIC", month="12")],
    })  # fmt: skip
    registration = {**REGISTRATION, "uei": new_uei, "fiscal_year_end": "06/30"}
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([registration], [], True)
    )

    result = grantee_resolve(uei=new_uei, ein=new_ein)

    assert result["audit_history"] == []
    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["fac"] is None
    assert year_end["used"] == {"source": "SAM.gov", "value": "06/30"}
    assert year_end["sources_disagree"] is True
    assert any("fiscal_year_end.used is SAM.gov's" in n for n in result["notes"])


def test_every_ein_is_checked_past_the_batch_limit(fakes, database):
    eins = [f"{n:09d}" for n in range(100000001, 100000022)]  # 21 EINs
    database({"general": GENERAL, "eo_bmf": [bmf(eins[-1], "THE TWENTY FIRST")]})

    irs, _ = identity._irs(eins, {})

    assert [r["ein"] for r in irs["records"]] == [eins[-1]]
    assert irs["not_found"] == eins[:-1]


def test_a_schedule_r_listing_in_a_gap_year_points_at_the_filer(fakes, database):
    """S19: a parent's Schedule R lists the entity in a year no FAC audit covers."""
    parent = "463131350"
    database({
        "general": GENERAL,
        "eo_bmf": [bmf(EIN, "INLAND BEHAVIORAL AND HEALTH SERVICES INC")],
        "form990_returns": [
            {"ein": parent, "name": "Regional Health System", "tax_year": 2023,
             "tax_period_end": "2024-06", "form": "990",
             "object_id": "202500000000000001", "has_schedule_r": "Y"},
        ],
        "schedule_r": [
            {"filer_ein": parent, "tax_year": 2023, "object_id": "202500000000000001",
             "part": "II", "relationship": "related tax-exempt organization",
             "name": "Inland Behavioral", "ein": EIN},
        ],
    })  # fmt: skip

    result = grantee_resolve(uei=UEI)  # FY2024 has no filing and no FAC listing

    irs = result["irs_records"]
    [entry] = irs["listed_by"]
    assert entry["filer_ein"] == parent
    assert entry["periods_ending"] == ["2024-06"]
    assert irs["schedule_r_filers_stored"] == 1
    assert any(
        n.startswith("listed_by searches only the Schedule R returns stored locally")
        for n in result["irs_notes"]
    )
    note = next(
        n for n in result["irs_notes"] if n.startswith("Regional Health System")
    )
    assert "lists this entity on its Form 990 Schedule R" in note
    assert "including 2024, when no FAC audit" in note
    assert "doesn't say which of the two controls the other" in note
    assert "isn't audit coverage" in note
    # The filer has no FAC filing, and the only organization its Schedule R
    # lists is this entity, so there's no EIN to send the agent to.
    assert note.endswith(
        "FAC has no filing under EIN 463131350, and none of the organizations on "
        "its stored Schedule R files to FAC under its own EIN."
    )
    assert any(
        q.startswith("Does Regional Health System's audit cover this entity for 2024")
        for q in result["irs_questions"]
    )


def sr_database(database, general=GENERAL, schedule_r=()):
    """A filer (463131350) whose return lists the entity, plus extra rows."""
    database({
        "general": general,
        "form990_returns": [
            {"ein": "463131350", "name": "Regional Health System", "tax_year": 2023,
             "tax_period_end": "2024-06", "form": "990",
             "object_id": "202500000000000001", "has_schedule_r": "Y"},
        ],
        "schedule_r": [
            {"filer_ein": "463131350", "tax_year": 2023,
             "object_id": "202500000000000001", "part": "II",
             "relationship": "related tax-exempt organization",
             "name": "Inland Behavioral", "ein": EIN},
            *schedule_r,
        ],
    })  # fmt: skip


def listed_by_note(**entry):
    """The Schedule R note for one listed_by entry, for an entity with no
    audit or FAC listing in FY2024."""
    records = {"records": [], "not_found": [], "revocations": [],
               "listed_by": [{"ein": EIN, "filer_ein": "463131350",
                              "filer_name": "Regional Health System",
                              "relationship": "related tax-exempt organization",
                              "periods_ending": ["2024-06"], **entry}]}  # fmt: skip
    groups = [{"ein": EIN, "names": ["INLAND BEHAVIORAL"], "audit_years": []}]
    notes, _ = identity._irs_findings(records, groups, [], [])
    return next(n for n in notes if n.startswith("Regional Health System"))


def test_a_filer_with_fac_filings_is_searched_by_its_own_ein(database):
    sr_database(database, general=[
        *GENERAL,
        audit("2024-06-GSAFAC-0000000500", "PARENTUEI001", "463131350",
              "2024-06-30", "2025-03-01", "9e8", "REGIONAL HEALTH SYSTEM"),
    ])  # fmt: skip

    assert listed_by_note().endswith(
        "If Regional Health System files a consolidated audit, check it for "
        "those years (fac_search_audits(ein=463131350)) and its report text for "
        "this entity."
    )


def test_a_filer_with_no_fac_filing_names_listed_organizations_that_file(database):
    """S19: BSW Health has no FAC filing under its EIN; its system audit is
    filed under Holdings' EIN, which BSW Health's Schedule R lists."""
    holdings = "463130985"
    sr_database(
        database,
        general=[
            *GENERAL,
            audit("2025-06-GSAFAC-0000000600", "HOLDINGSUEI1", holdings,
                  "2025-06-30", "2026-03-01", "9e9", "REGIONAL HEALTH HOLDINGS"),
        ],
        schedule_r=[
            {"filer_ein": "463131350", "tax_year": 2023,
             "object_id": "202500000000000001", "part": "II",
             "relationship": "related tax-exempt organization",
             "name": "Regional Health Holdings", "ein": holdings},
            {"filer_ein": "463131350", "tax_year": 2023,  # files nothing to FAC
             "object_id": "202500000000000001", "part": "II",
             "relationship": "related tax-exempt organization",
             "name": "Regional Clinic", "ein": "555555555"},
        ],
    )  # fmt: skip

    assert listed_by_note().endswith(
        "FAC has no filing under EIN 463131350. Of the organizations on its "
        "stored Schedule R, Regional Health Holdings (EIN 463130985) files to "
        "FAC: fac_search_audits(ein=463130985)."
    )


def test_a_listing_under_a_one_digit_typo_says_so(database):
    sr_database(database)

    note = listed_by_note(ein_as_listed="953246625", name_as_listed="Inland Behav")

    assert note.endswith("It lists the entity under EIN 953246625, one digit from "
                         "953246624.")  # fmt: skip


def test_a_never_filed_entity_with_no_ein_says_the_ein_search_did_not_run(fakes):
    """S19 by UEI: a parent listing by EIN only can't be found from a UEI."""
    result = grantee_resolve(uei="ZZZZZZZZZZZ8")

    [note] = [n for n in result["notes"] if n.startswith("No parent audit lists")]
    # This fixture has no IRS file, so the tool says it didn't search one.
    assert note.startswith(
        "No parent audit lists UEI ZZZZZZZZZZZ8. No EIN is known for this entity, "
        "and IRS records weren't searched by its name."
    )
    assert "listings by EIN weren't searched" in note
    assert "irs_search_exempt_orgs with its distinctive words and state" in note


def test_a_never_filed_entity_says_what_the_irs_name_search_found(fakes, database):
    """With the IRS file loaded, the note says the tool's own search found no
    match, so the agent knows a by-hand search is a second, looser try."""
    database({"general": GENERAL, "eo_bmf": [bmf("990000001", "UNRELATED CLINIC")]})

    result = grantee_resolve(uei="ZZZZZZZZZZZ8")

    [note] = [n for n in result["notes"] if n.startswith("No parent audit lists")]
    assert (
        "searching IRS records by its names found none with the same name in the "
        "same place." in note
    )
    assert "abbreviated or cut off" in note


def test_a_never_filed_entity_gets_rows_for_the_last_three_past_due_years(fakes):
    """No filing and no parent listing: SAM.gov's 06/30 year end gives the
    rows. On 2026-11-05 FY2026 (due 2027-03-31) isn't due yet."""
    result = module.grantee_audit_coverage(uei="ZZZZZZZZZZZ8")

    assert [(r["audit_year"], r["route"]) for r in result["years"]] == [
        (2025, "no audit found"),
        (2024, "no audit found"),
        (2023, "no audit found"),
    ]
    fy2025 = result["years"][0]
    assert fy2025["expected_fy_end_date"] == "2025-06-30"
    assert fy2025["expected_due_date"] == "2026-03-31"
    assert fy2025["fy_end_source"] == "SAM.gov"
    # audit_year still narrows the rows.
    narrowed = module.grantee_audit_coverage(uei="ZZZZZZZZZZZ8", audit_year=2024)
    assert [r["audit_year"] for r in narrowed["years"]] == [2024]


def test_a_first_audit_filed_since_the_local_copy_is_found(fakes):
    """Nothing in the local copy, but the copy is dated: the live API is
    asked before saying nothing has filed."""
    never = "ZZZZZZZZZZZ8"
    fakes.answers["gap_check"] = [
        audit("2025-06-GSAFAC-0000000077", never, "990000009", "2025-06-30",
              "2026-10-01", "9e5", "NEW CLINIC")
    ]  # fmt: skip
    result = module.grantee_audit_coverage(uei=never)

    [row] = result["years"]
    assert (row["audit_year"], row["route"]) == (2025, "own audit")
    assert row["filed_since_snapshot"] is True
    [entry] = result["identity"]["identifiers"]
    assert (entry["ein"], entry["names"]) == ("990000009", ["NEW CLINIC"])
    assert result["notes"][0].startswith("The local FAC copy has no filing")
    assert not any(n.startswith("Nothing has filed") for n in result["notes"])
    [check] = [p for t, p in fakes.calls if t == "general" and "audit_year" in p]
    assert check["audit_year"].startswith("in.(2016,") and never in check["auditee_uei"]


def test_a_never_filed_entity_says_when_the_live_api_could_not_be_asked(
    fakes, monkeypatch
):
    def get(table, params):
        if table == "general" and "audit_year" in params:
            raise ToolError("FAC API returned HTTP 429.")
        return fakes.get(table, params)

    monkeypatch.setattr(fac, "get", get)
    result = grantee_resolve(uei="ZZZZZZZZZZZ8")

    assert (
        "FAC's live API couldn't be asked (FAC API returned HTTP 429.)"
        in (result["notes"][0])
    )


def test_a_never_filed_entity_gets_a_row_for_an_earlier_year_asked_for(fakes):
    result = module.grantee_audit_coverage(uei="ZZZZZZZZZZZ8", audit_year=[2019, 2021])

    assert [(r["audit_year"], r["route"]) for r in result["years"]] == [
        (2021, "no audit found"),
        (2019, "no audit found"),
    ]
    assert result["years"][0]["expected_due_date"] == "2022-03-31"


@pytest.mark.parametrize(
    "uei, year, why",
    [
        ("ZZZZZZZZZZZ8", 2026, "one isn't due until 2027-03-31 for a year ending 2026"),
        ("ZZZZZZZZZZZ8", 2010, "FAC's public data starts with audit year 2016"),
        (UEI, 2019, "it is before 2022, the first year this entity appears in FAC"),
    ],
)
def test_a_year_asked_for_with_no_row_says_why(fakes, uei, year, why):
    result = module.grantee_audit_coverage(uei=uei, audit_year=year)

    assert result["years"] == []
    [note] = [n for n in result["notes"] if n.startswith("No row for fiscal year")]
    assert str(year) in note and why in note


def outlays_by_uei(amounts):
    """A usaspending_sum_outlays that gives each UEI one award of its amount."""

    def fake(fiscal_year_end, uei, max_awards, period_start=None):
        return {
            "total_outlays_in_period": amounts.get(uei, 0),
            "more_awards_available": False,
            "any_funding_history_truncated": False,
            "period": {"description": f"year ending {fiscal_year_end}"},
            "awards": [
                {
                    "award_id": f"H-{uei}",
                    "outlays_in_period": amounts[uei],
                    "source_url": f"https://www.usaspending.gov/award/{uei}",
                }
            ]
            if uei in amounts
            else [],
        }

    return fake


def test_funding_is_added_up_across_the_entitys_ueis(fakes, monkeypatch):
    """Most of the money can sit under a UEI other than the one searched."""
    monkeypatch.setattr(
        module,
        "usaspending_sum_outlays",
        outlays_by_uei({UEI: 43688.29, TYPO_UEI: 1468276.57}),
    )
    result = module.grantee_audit_coverage(uei=UEI, audit_year=2025, with_funding=True)

    spending = result["years"][0]["usaspending"]
    assert spending["total_outlays_in_period"] == 1511964.86
    assert {a["uei"] for a in spending["awards_with_outlays"]} == {UEI, TYPO_UEI}


def test_one_ueis_error_gives_no_total(fakes, monkeypatch):
    def failing(fiscal_year_end, uei, max_awards):
        if uei == UEI:
            raise ToolError("USAspending timed out")
        return outlays_by_uei({TYPO_UEI: 5.0})(fiscal_year_end, uei, max_awards)

    monkeypatch.setattr(module, "usaspending_sum_outlays", failing)
    result = module.grantee_audit_coverage(uei=UEI, audit_year=2025, with_funding=True)

    spending = result["years"][0]["usaspending"]
    assert spending["error"] == f"{UEI}: USAspending timed out"
    assert "total_outlays_in_period" not in spending


def test_a_never_filed_entity_gets_funding_on_its_rows(fakes, monkeypatch):
    never = "ZZZZZZZZZZZ8"
    monkeypatch.setattr(
        module, "usaspending_sum_outlays", outlays_by_uei({never: 900.0})
    )
    result = module.grantee_audit_coverage(uei=never, with_funding=True)

    assert [r["usaspending"]["total_outlays_in_period"] for r in result["years"]] == [
        900.0,
        900.0,
        900.0,
    ]
    assert result["years"][0]["usaspending"]["period"].endswith("2025-06-30")
    assert result["years"][0]["usaspending"]["ueis"] == [never]


def test_with_no_uei_funding_says_it_was_not_fetched(fakes, monkeypatch):
    monkeypatch.setattr(
        module, "usaspending_sum_outlays", lambda **kw: pytest.fail("not searched")
    )
    result = module.grantee_audit_coverage(ein="990000009", with_funding=True)

    assert any(n.startswith("with_funding added nothing") for n in result["notes"])


def test_a_never_filed_entity_with_no_year_end_gets_no_rows(fakes, monkeypatch):
    """Without a year end no due date can be worked out, so no year is
    claimed to be past due."""
    monkeypatch.setattr(sam, "registrations_by_uei", lambda ueis: ([], [], True))

    assert module.grantee_audit_coverage(uei="ZZZZZZZZZZZ8")["years"] == []


def test_a_never_filed_entity_searched_by_ein_keeps_the_general_note(fakes):
    result = grantee_resolve(uei="ZZZZZZZZZZZ8", ein="900000123")

    assert any(
        n.startswith("No parent audit lists these identifiers.")
        for n in result["notes"]
    )
    assert not any("No EIN was known" in n for n in result["notes"])


# Parent disagreements: a FAC parent lists the entity, a different Schedule R
# filer reports it, for the same fiscal year. See docs/mcp-design.md.
FAC_PARENT, SR_FILER = "222222222", "333333333"


def fac_listing(year=2024, fy_end="2024-06-30", ein=FAC_PARENT):
    return {
        "audit_year": str(year),
        "listed_by_ein": ein,
        "listed_by_name": "FAC PARENT",
        "listed_by_fy_end": fy_end,
    }


def sr_entry(periods=("2024-06",), filer=SR_FILER):
    return {
        "filer_ein": filer,
        "filer_name": "SCHEDULE R FILER",
        "relationship": "related tax-exempt organization",
        "periods_ending": list(periods),
    }


def returns_row(ein, object_id):
    return {"ein": ein, "tax_year": 2023, "tax_period_end": "2024-06", "form": "990",
            "object_id": object_id, "has_schedule_r": "Y"}  # fmt: skip


@pytest.fixture
def stored_returns(database):
    """Both parents' returns are stored; neither lists the other unless asked."""

    def build(schedule_r=()):
        database({
            "general": GENERAL,
            "form990_returns": [returns_row(FAC_PARENT, "1" * 18),
                                returns_row(SR_FILER, "2" * 18)],
            "schedule_r": list(schedule_r),
        })  # fmt: skip

    return build


def test_unrelated_parents_claiming_the_same_year_are_a_question(stored_returns):
    stored_returns()
    notes, questions = identity._parent_disagreements(
        [sr_entry()], [fac_listing()], [], {EIN}
    )

    [note] = notes
    assert note.startswith("For fiscal year 2024, a FAC audit by FAC PARENT")
    assert note.endswith("Neither's Schedule R lists the other.")
    assert questions == [
        "Are FAC PARENT and SCHEDULE R FILER in the same system? For 2024, FAC "
        "shows one covering this entity and Schedule R shows the other reporting "
        "it as related."
    ]


def test_a_disagreement_found_through_a_one_digit_typo_says_so(stored_returns):
    stored_returns()
    entry = {**sr_entry(), "ein": EIN, "ein_as_listed": "953246625"}
    notes, _ = identity._parent_disagreements([entry], [fac_listing()], [], {EIN})

    assert notes[0].endswith(
        f"It lists the entity under EIN 953246625, one digit from {EIN}."
    )


def test_parents_shown_as_related_are_not_flagged(stored_returns):
    """BSW Health and BSW Holdings: one's Schedule R lists the other."""
    stored_returns([{"filer_ein": SR_FILER, "tax_year": 2023, "object_id": "2" * 18,
                     "ein": FAC_PARENT, "part": "II"}])  # fmt: skip
    assert identity._parent_disagreements([sr_entry()], [fac_listing()], [], {EIN}) == (
        [],
        [],
    )


def test_a_settled_year_or_the_entity_itself_is_not_flagged(stored_returns):
    stored_returns()
    own_audit = [{"audit_year": 2024}]
    assert identity._parent_disagreements(
        [sr_entry()], [fac_listing()], own_audit, {EIN}
    ) == ([], [])
    # The "parent" is the entity under another of its identifiers.
    assert identity._parent_disagreements(
        [sr_entry()], [fac_listing(ein=EIN)], [], {EIN}
    ) == ([], [])
    # A missing Schedule R year is never a disagreement.
    assert identity._parent_disagreements(
        [sr_entry(periods=("2023-06",))], [fac_listing()], [], {EIN}
    ) == ([], [])


def test_years_ending_in_different_months_are_not_compared(stored_returns):
    stored_returns()
    notes, questions = identity._parent_disagreements(
        [sr_entry(periods=("2024-12",))], [fac_listing()], [], {EIN}
    )

    assert notes == [
        "FAC PARENT's fiscal year ends in June and SCHEDULE R FILER's in December, "
        "so FAC's 2024 listing of this entity and the Schedule R one cover "
        "different months and weren't compared."
    ]
    assert questions == []


def test_an_unstored_parent_makes_the_relationship_unknown(database):
    database({"general": GENERAL, "form990_returns": [returns_row(SR_FILER, "2" * 18)]})
    notes, _ = identity._parent_disagreements([sr_entry()], [fac_listing()], [], {EIN})

    assert notes[0].endswith(
        "FAC PARENT's Schedule R isn't stored, so whether the two are related is "
        "unknown."
    )


# Fiscal-year-end order: own latest FAC audit, SAM.gov, FAC parent, IRS.
def never_filed_child(fakes, parent_fy_end="2025-12-31"):
    """A child with no audit of its own, listed by EIN in a parent's audit."""
    child, child_ein = "ZZZZZZZZZZZ8", "900000123"
    parent_id = "2025-12-GSAFAC-0000000800"
    fakes.answers["additional_eins"] = [
        {"report_id": parent_id, "audit_year": "2025", "auditee_uei": "PARENTUEI001",
         "fac_accepted_date": "2026-09-30", "additional_ein": child_ein}
    ]  # fmt: skip
    fakes.answers["parents"] = [
        {"report_id": parent_id, "auditee_name": "PARENT HEALTH SYSTEM",
         "auditee_ein": "900000456", "fy_end_date": parent_fy_end}
    ]  # fmt: skip
    return child, child_ein


def test_with_no_own_audit_or_sam_the_parents_year_end_is_used(fakes, monkeypatch):
    child, child_ein = never_filed_child(fakes)
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    result = grantee_resolve(uei=child, ein=child_ein)

    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["fac_parent"] == {
        "value": "2025-12-31",
        "listed_by_name": "PARENT HEALTH SYSTEM",
        "report_id": "2025-12-GSAFAC-0000000800",
    }
    assert year_end["used"] == {
        "source": "a FAC parent's audit (PARENT HEALTH SYSTEM, "
        "2025-12-GSAFAC-0000000800)",
        "value": "2025-12-31",
    }
    assert "then a FAC parent's audit, then an inactive" in year_end["rule"]


def test_sam_comes_before_the_parent_and_the_two_are_compared(fakes, monkeypatch):
    child, child_ein = never_filed_child(fakes)  # parent: December
    registration = {**REGISTRATION, "uei": child}  # SAM.gov: 06/30
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([registration], [], True)
    )

    result = grantee_resolve(uei=child, ein=child_ein)

    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["used"] == {"source": "SAM.gov", "value": "06/30"}
    assert year_end["sources_disagree"] is True
    note = next(n for n in result["notes"] if n.startswith("Fiscal year ends disagree"))
    assert "SAM.gov: 06/30; a FAC parent's audit (PARENT HEALTH SYSTEM" in note


def test_the_parent_comes_before_the_irs(fakes, database, monkeypatch):
    child, child_ein = never_filed_child(fakes)
    database(
        {"general": GENERAL, "eo_bmf": [bmf(child_ein, "CHILD CLINIC", month="06")]}
    )
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([], list(ueis), True)
    )

    year_end = grantee_resolve(uei=child, ein=child_ein)["entity"]["fiscal_year_end"]

    assert year_end["used"]["source"].startswith("a FAC parent's audit")
    assert year_end["irs"] == [{"ein": child_ein, "month": 6}]
    assert year_end["sources_disagree"] is True  # December vs June


def test_sources_are_compared_by_month_as_dat_does(fakes, monkeypatch):
    mid_month = {**REGISTRATION, "fiscal_year_end": "06/15"}  # FAC says 06-30
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([mid_month], [], True)
    )

    result = grantee_resolve(uei=UEI)

    assert result["entity"]["fiscal_year_end"]["sources_disagree"] is False
    assert not any("change its fiscal year end" in q for q in result["open_questions"])


def test_a_gap_year_gets_its_expected_year_end_and_due_date(fakes):
    result = grantee_resolve(uei=UEI)

    gap = next(g for g in result["years_without_filing"] if g["audit_year"] == 2024)
    assert gap["expected_fy_end_date"] == "2024-06-30"
    assert gap["expected_due_date"] == "2025-03-31"
    assert gap["fy_end_source"] == "FAC's latest audit"


def test_a_resubmission_supplies_the_year_end_over_a_later_superseded_date(
    fakes, database
):
    """The original FY2025 filing said December; its resubmission corrects June."""
    original = audit(
        "2025-12-GSAFAC-0000000020", UEI, EIN, "2025-12-31", "2026-01-08", "5e6"
    )
    corrected = {
        **audit(
            "2025-06-GSAFAC-0000000021", UEI, EIN, "2025-06-30", "2026-02-01", "5e6"
        ),
        "resubmission_version": "2",
    }
    database({"general": [*GENERAL[:2], original, corrected]})  # fmt: skip

    result = grantee_resolve(uei=UEI)

    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["fac"] == {
        "value": "2025-06-30",
        "report_id": "2025-06-GSAFAC-0000000021",
    }
    # audit_history still shows both filings, the resubmission marked.
    fy2025 = [a for a in result["audit_history"] if a["audit_year"] == 2025]
    assert {a["report_id"] for a in fy2025} == {
        "2025-12-GSAFAC-0000000020",
        "2025-06-GSAFAC-0000000021",
    }
    assert [
        a.get("resubmission_version") for a in fy2025 if a.get("resubmission_version")
    ] == [2]


def test_without_resubmissions_the_latest_accepted_filing_wins_a_tie(fakes, database):
    first = audit(
        "2025-06-GSAFAC-0000000030", UEI, EIN, "2025-06-30", "2026-01-08", "5e6"
    )
    later = audit(
        "2025-12-GSAFAC-0000000031", UEI, EIN, "2025-12-31", "2026-03-01", "5e6"
    )
    database({"general": [*GENERAL[:2], first, later]})  # fmt: skip

    year_end = grantee_resolve(uei=UEI)["entity"]["fiscal_year_end"]

    assert year_end["fac"]["report_id"] == "2025-12-GSAFAC-0000000031"


def test_an_inactive_registration_ranks_below_the_parent(fakes, monkeypatch):
    child, child_ein = never_filed_child(fakes)  # parent: December
    lapsed = {**REGISTRATION, "uei": child, "registration_status": "Inactive"}
    monkeypatch.setattr(sam, "registrations_by_uei", lambda ueis: ([lapsed], [], True))

    year_end = grantee_resolve(uei=child, ein=child_ein)["entity"]["fiscal_year_end"]

    assert year_end["used"]["source"].startswith("a FAC parent's audit")
    assert year_end["sam"]["registration_status"] == "Inactive"
    assert "then an inactive SAM.gov registration" in year_end["rule"]
    assert year_end["sources_disagree"] is True  # June vs December, both shown


def test_an_inactive_registration_still_beats_the_irs(fakes, database, monkeypatch):
    new_uei, new_ein = "ABCDEFGH1234", "999888777"
    database({"general": GENERAL, "eo_bmf": [bmf(new_ein, "NEW CLINIC", month="12")]})
    lapsed = {**REGISTRATION, "uei": new_uei, "registration_status": "Inactive"}
    monkeypatch.setattr(sam, "registrations_by_uei", lambda ueis: ([lapsed], [], True))

    year_end = grantee_resolve(uei=new_uei, ein=new_ein)["entity"]["fiscal_year_end"]

    assert year_end["used"] == {
        "source": "SAM.gov (registration Inactive)",
        "value": "06/30",
    }


def test_a_revoked_ein_is_reported_with_its_revocation(fakes, database, monkeypatch):
    """S15: an EIN that filed to FAC is missing from the EO BMF: it was revoked."""
    revoked_ein = "860215065"
    database({
        "general": [audit("2022-06-GSAFAC-0000000901", UEI, revoked_ein, "2022-06-30",
                          "2023-03-01", "1.67e6", "CHANGEPOINT INTEGRATED HEALTH")],
        "eo_bmf": [bmf("481150706", "SOMEONE ELSE")],
        "irs_revocations": [
            {"ein": revoked_ein, "name": "CHANGEPOINT INTEGRATED HEALTH",
             "city": "SHOW LOW", "state": "AZ", "subsection": "03",
             "revocation_date": "2022-02-15", "revocation_posting_date": "2022-05-11",
             "reinstatement_date": ""},
        ],
    })  # fmt: skip

    result = grantee_resolve(uei=UEI)

    assert [r["ein"] for r in result["irs_records"]["revocations"]] == [revoked_ein]
    note = next(n for n in result["irs_notes"] if n.startswith(f"EIN {revoked_ein}"))
    assert "is on the IRS's automatic revocation list" in note
    assert (
        "revoked 2022-02-15, posted 2022-05-11, no reinstatement date on record" in note
    )
    # The revocation answers why it's missing, so no "why isn't it listed" question.
    assert not any(
        q.startswith(f"Why isn't EIN {revoked_ein}") for q in result["irs_questions"]
    )


def test_each_fac_name_under_an_ein_is_checked_against_the_irs(fakes, database):
    """S19: Health's FY2025 audit uses Holdings' EIN, which earlier audits filed
    as Holdings. The earlier name matches; the 2025 one doesn't."""
    holdings_ein = "463130985"
    database({
        "general": [
            audit("2023-06-GSAFAC-0000000951", UEI, holdings_ein, "2023-06-30",
                  "2024-03-01", "9e8", "BAYLOR SCOTT & WHITE HOLDINGS"),
            audit("2024-06-GSAFAC-0000000952", UEI, holdings_ein, "2024-06-30",
                  "2025-03-01", "9e8", "BAYLOR SCOTT & WHITE HOLDINGS"),
            audit("2025-06-GSAFAC-0000000953", UEI, holdings_ein, "2025-06-30",
                  "2026-03-01", "9e8", "Baylor Scott & White Health"),
        ],
        "eo_bmf": [bmf(holdings_ein, "BAYLOR SCOTT & WHITE HOLDINGS", "TX")],
    })  # fmt: skip

    result = grantee_resolve(uei=UEI)

    [record] = result["irs_records"]["records"]
    assert record["name_matches_fac"] is False
    assert record["fac_names_not_matching"] == [
        {"name": "Baylor Scott & White Health", "audit_years": [2025]}
    ]
    note = next(
        n
        for n in result["irs_notes"]
        if n.startswith(f"The IRS lists EIN {holdings_ein}")
    )
    assert note.endswith(
        "FAC filings under that EIN use names that don't match it: "
        "Baylor Scott & White Health (2025)."
    )
    question = (
        f"Does EIN {holdings_ein} belong to the organization that filed as "
        "Baylor Scott & White Health (2025)?"
    )
    assert any(q.startswith(question) for q in result["irs_questions"])


# MR !10 review. Each test below states the behavior the review expects, and
# fails on the branch as reviewed (3442bb4).
def test_a_parent_on_another_year_end_is_not_the_entity_changing_its_own(fakes):
    """Finding 2. Geneva General Hospital (EIN 160743032): its own FAC audit,
    SAM.gov, and the IRS all say December, and the University of Rochester's
    audit that lists it ends in June. The tool asks whether Geneva changed
    its fiscal year end."""
    parent_id = "2024-12-GSAFAC-0000000800"
    fakes.answers["additional_eins"] = [
        {"report_id": parent_id, "audit_year": "2024", "auditee_uei": "PARENTUEI001",
         "fac_accepted_date": "2025-09-30", "additional_ein": EIN}
    ]  # fmt: skip
    fakes.answers["parents"] = [
        {"report_id": parent_id, "auditee_name": "PARENT UNIVERSITY",
         "auditee_ein": "900000456", "fy_end_date": "2024-12-31"}
    ]  # fmt: skip

    result = grantee_resolve(uei=UEI)

    year_end = result["entity"]["fiscal_year_end"]
    assert year_end["fac"]["value"] == "2025-06-30"  # the entity's own sources
    assert year_end["sam"]["value"] == "06/30"  # agree with each other
    assert year_end["fac_parent"]["value"] == "2024-12-31"  # still shown
    assert not any("change its fiscal year end" in q for q in result["open_questions"])
    assert not any("may have changed its year end" in n for n in result["notes"])


def test_the_entitys_own_ein_is_not_a_schedule_r_parent(stored_returns):
    """Finding 3: one of the entity's EINs lists another on Schedule R. No
    case among the 17 filers stored on 2026-11-10; this one is made up."""
    stored_returns()
    second_ein = "444444444"

    assert identity._parent_disagreements(
        [sr_entry(filer=second_ein)], [fac_listing()], [], {EIN, second_ein}
    ) == ([], [])


def test_the_entitys_own_ein_is_not_asked_to_cover_a_gap_year():
    """Finding 3, the uncovered-years loop: same made-up entity."""
    second_ein = "444444444"
    irs_records = {
        "records": [],
        "not_found": [],
        "revocations": [],
        "listed_by": [sr_entry(filer=second_ein)],  # lists EIN for FY2024
    }
    groups = [
        {"ein": EIN, "audit_years": [2023]},
        {"ein": second_ein, "audit_years": [2023]},
    ]

    _, questions = identity._irs_findings(
        irs_records, groups, [{"audit_year": 2023}], []
    )

    assert questions == []


@pytest.mark.parametrize(
    "first",
    [
        # Teach For America (EIN 133541913): KCD3LMJ2Y6V3 is ID Assigned with
        # no fields, MJDWLXZ7JJ15 is Active with 05/31.
        {"uei": TYPO_UEI, "registration_status": "ID Assigned"},
        # Marlborough Community Development Authority (EIN 046001399):
        # CCGEPNBNSZV8 is Inactive, LGL5JFM5KDB8 is Active.
        {**REGISTRATION, "uei": TYPO_UEI, "registration_status": "Inactive"},
    ],
)
def test_an_active_registration_is_read_past_an_inactive_or_blank_one(
    fakes, monkeypatch, first
):
    """Finding 6: the year end and status come from the first registration
    SAM.gov returns, whichever of the entity's UEIs that is."""
    monkeypatch.setattr(
        sam, "registrations_by_uei", lambda ueis: ([first, REGISTRATION], [], True)
    )

    year_end = grantee_resolve(uei=UEI)["entity"]["fiscal_year_end"]

    assert year_end["sam"] == {
        "value": "06/30",
        "uei": UEI,
        "registration_status": "Active",
    }


def test_the_eo_bmf_caveat_reaches_grantee_resolve(fakes, database):
    """Finding 10: only the IRS snapshot date is kept from the IRS tools'
    caveats (seen live for EIN 160743032)."""
    database({
        "general": GENERAL,
        "eo_bmf": [bmf(EIN, "INLAND BEHAVIORAL AND HEALTH SERVICES INC")],
    })  # fmt: skip

    result = grantee_resolve(uei=UEI)

    assert any("isn't proof an EIN is wrong" in c for c in result["irs_caveats"])


@pytest.mark.parametrize("irs_month", ["06", "12"])  # agrees with FAC, disagrees
def test_irs_records_dont_change_a_fac_filers_year_end_or_history(
    fakes, database, irs_month
):
    """With its own FAC audit, the entity's year end and audit history are the
    same whether or not the IRS files are loaded: the IRS ranks last."""
    without_irs = grantee_resolve(uei=UEI)
    database({
        "general": GENERAL,
        "eo_bmf": [bmf(EIN, "INLAND BEHAVIORAL AND HEALTH SERVICES", "CA", irs_month)],
        "form990_returns": [
            {"ein": "111111111", "name": "SOME PARENT", "tax_year": 2024,
             "tax_period_end": "2025-06", "form": "990",
             "object_id": "202500000000000009", "has_schedule_r": "Y"},
        ],
        "schedule_r": [
            {"filer_ein": "111111111", "tax_year": 2024,
             "object_id": "202500000000000009", "part": "II",
             "relationship": "related tax-exempt organization",
             "name": "INLAND BEHAVIORAL", "ein": EIN},
        ],
    })  # fmt: skip
    with_irs = grantee_resolve(uei=UEI)

    assert with_irs["irs_records"]["records"]  # the IRS files were read
    before = without_irs["entity"]["fiscal_year_end"]
    after = with_irs["entity"]["fiscal_year_end"]
    assert after["used"] == before["used"]
    assert after["used"]["source"] == "FAC's latest audit"
    assert after["fac"] == before["fac"]
    assert with_irs["audit_history"] == without_irs["audit_history"]
