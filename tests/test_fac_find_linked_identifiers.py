"""Tests for fac_find_linked_identifiers, on a fixture database."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp.tools.fac_find_linked_identifiers import fac_find_linked_identifiers


def audit(report_id, uei, ein, year, name="INLAND BEHAVIORAL"):
    return {
        "report_id": report_id,
        "auditee_uei": uei,
        "auditee_ein": ein,
        "audit_year": year,
        "auditee_name": name,
        "fac_accepted_date": f"{int(year) + 1}-01-08",
    }


GENERAL = [
    audit("2021-06-CENSUS-0000000001", "GSA_MIGRATION", "953246624", "2021"),
    audit("2024-06-GSAFAC-0000000002", "JQNLHK8YLTU6", "953246624", "2024"),
    # FY2025 filed under a UEI one character off, same EIN.
    audit("2025-06-GSAFAC-0000000003", "JQNLHK8YLTUB", "953246624", "2025"),
    # Reached only through the shared EIN: a different UEI entirely.
    audit("2023-06-GSAFAC-0000000004", "ABCDEFGHJKL1", "953246624", "2023", "INLAND"),
    # One character off too, but nothing else in common: a different organization.
    audit("2025-06-GSAFAC-0000000005", "JQNLHK8YLTUC", "111111111", "2025", "OTHER"),
    # Its own EIN is one digit off the searched EIN: also a different organization.
    audit("2025-06-GSAFAC-0000000006", "ZZZZZZZZZZZ9", "953246625", "2025", "NEIGHBOR"),
    # A placeholder UEI typed by two unrelated filers must not link them.
    audit("2022-06-GSAFAC-0000000007", "XXXXXXXXXXXX", "953246624", "2022"),
    audit("2022-06-GSAFAC-0000000008", "XXXXXXXXXXXX", "226016230", "2022", "CARTERET"),
    # A placeholder EIN likewise: ours is kept, the unrelated filer is not reached.
    audit("2020-06-CENSUS-0000000009", "JQNLHK8YLTU6", "000000000", "2020"),
    audit(
        "2020-06-CENSUS-0000000010", "QQQQQQQQQQQ1", "000000000", "2020", "ELSEWHERE"
    ),
    # OTHER's earlier filing under another UEI: reachable only by expanding OTHER.
    audit("2024-06-GSAFAC-0000000011", "OTHERUEI0001", "111111111", "2024", "OTHER"),
]


@pytest.fixture
def fac_db(database):
    database({"general": GENERAL})


def test_by_uei_finds_typo_and_shared_ein_filings(fac_db):
    result = fac_find_linked_identifiers(uei="jqnlhk8yltu6")

    by_uei = {g["uei"]: g for g in result["identifiers"]}
    assert set(by_uei) == {
        "GSA_MIGRATION",
        "ABCDEFGHJKL1",
        "JQNLHK8YLTU6",
        "JQNLHK8YLTUB",
        "XXXXXXXXXXXX",
    }
    # The placeholder row with our EIN is included; the other filer using it is not.
    assert by_uei["XXXXXXXXXXXX"]["names"] == ["INLAND BEHAVIORAL"]
    # Same for the placeholder EIN filed under our UEI.
    assert {g["ein"] for g in result["identifiers"]} == {"953246624", "000000000"}
    assert "ELSEWHERE" not in {n for g in result["identifiers"] for n in g["names"]}
    assert by_uei["JQNLHK8YLTU6"]["links"] == ["searched UEI JQNLHK8YLTU6"]
    assert by_uei["JQNLHK8YLTUB"]["links"] == [
        "one character different from UEI JQNLHK8YLTU6"
    ]
    assert by_uei["ABCDEFGHJKL1"]["links"] == [
        "shares a UEI or EIN with a matched filing"
    ]
    assert by_uei["ABCDEFGHJKL1"]["names"] == ["INLAND"]
    assert by_uei["JQNLHK8YLTUB"]["audits"][0]["source_url"].endswith(
        "2025-06-GSAFAC-0000000003"
    )
    # Groups are ordered by their latest audit year; the typo filing is newest.
    assert result["identifiers"][-1]["uei"] == "JQNLHK8YLTUB"
    assert any("downloaded" in c for c in result["caveats"])


def test_by_ein_excludes_neighboring_ein_of_another_organization(fac_db):
    result = fac_find_linked_identifiers(ein="95-3246624")

    assert result["searched"] == {"ueis": [], "eins": ["953246624"]}
    eins = {g["ein"] for g in result["identifiers"]}
    assert eins == {"953246624", "000000000"}
    assert {tuple(g["links"]) for g in result["identifiers"]} == {
        ("searched EIN 953246624",),
        ("shares a UEI or EIN with a matched filing",),
    }


def test_several_identifiers_are_searched_in_one_call(fac_db):
    """Two unrelated organizations' EINs: both come back, each labelled."""
    result = fac_find_linked_identifiers(ein=["953246624", "111111111"])

    assert result["searched"]["eins"] == ["953246624", "111111111"]
    by_ein = {}
    for g in result["identifiers"]:
        by_ein.setdefault(g["ein"], []).append(g)
    assert "111111111" in by_ein and "953246624" in by_ein
    # OTHER's other UEI is reached through its EIN, as a single search would.
    assert {g["uei"] for g in by_ein["111111111"]} == {"JQNLHK8YLTUC", "OTHERUEI0001"}
    assert result["caveats"][0].startswith("Several identifiers")
    assert result["leads_only"] is False
    # The searched-identifier labels say which one each group came from.
    assert ["searched EIN 111111111"] in [g["links"] for g in by_ein["111111111"]]


def test_near_matches_are_leads_when_nothing_matches_exactly(fac_db):
    """A UEI that never filed: the one-character neighbors are all we have."""
    result = fac_find_linked_identifiers(uei="JQNLHK8YLTU1")

    ueis = {g["uei"] for g in result["identifiers"]}
    assert ueis == {"JQNLHK8YLTU6", "JQNLHK8YLTUB", "JQNLHK8YLTUC"}
    # Leads are not expanded: OTHER's filing under another UEI stays out, and
    # every group says how it was reached.
    assert all(g["links"][0].startswith("one character") for g in result["identifiers"])
    assert result["caveats"][0].startswith("Nothing has filed")
    assert result["leads_only"] is True


def test_a_batch_keeps_the_leads_of_identifiers_that_found_nothing(fac_db):
    """111111112 never filed; OTHER's 111111111 is one digit off it."""
    alone = fac_find_linked_identifiers(ein="111111112")
    assert {g["ein"] for g in alone["identifiers"]} == {"111111111"}

    result = fac_find_linked_identifiers(ein=["953246624", "111111112"])

    eins = {g["ein"] for g in result["identifiers"]}
    assert "111111111" in eins  # the lead survives the other EIN's match
    assert "953246625" not in eins  # 953246624 matched, so its stray neighbor stays out
    # The batch keeps exactly the leads the single search found.
    lead_groups = [g for g in result["identifiers"] if g["ein"] == "111111111"]
    assert {g["uei"] for g in lead_groups} == {g["uei"] for g in alone["identifiers"]}
    assert result["not_found"] == ["111111112"]
    assert result["leads_only"] is False
    assert any(
        c.startswith("Nothing has filed under 111111112") for c in result["caveats"]
    )


def test_requires_an_identifier(fac_db):
    with pytest.raises(ToolError, match="uei or ein"):
        fac_find_linked_identifiers()
    with pytest.raises(ToolError, match="12"):
        fac_find_linked_identifiers(uei="SHORT")


NOTED_GENERAL = [
    audit("2023-06-GSAFAC-0000000020", "R7PNMGWQ4QH8", "542003078", "2023", "HAVENS"),
    audit("2024-06-GSAFAC-0000000021", "R7PNMGWQ4QH8", "542006078", "2024", "HAVENS"),
    audit("2023-06-GSAFAC-0000000022", "GMFLJZLNKK34", "237133975", "2023", "CRMC"),
]
NOTES = [
    # Another auditee's note names the EIN, hyphenated, as a different organization.
    {
        "report_id": "2023-06-GSAFAC-0000000022",
        "title": "5. Entities included in the Schedule",
        "content": "54-2003078 Positron Emission Tomography of Hampton Roads",
    },
    # A note in an audit filed under the searched EIN itself isn't another's mention.
    {
        "report_id": "2023-06-GSAFAC-0000000020",
        "title": "1. Basis",
        "content": "Community Havens, EIN 542003078, prepared this schedule.",
    },
]


@pytest.fixture
def noted_db(database):
    database({"general": NOTED_GENERAL, "notes_to_sefa": NOTES})


def test_finds_other_audits_notes_that_name_a_searched_ein(noted_db):
    result = fac_find_linked_identifiers(ein="542003078")

    assert [(m["ein"], m["report_id"]) for m in result["named_in_sefa_notes"]] == [
        ("542003078", "2023-06-GSAFAC-0000000022")
    ]
    mention = result["named_in_sefa_notes"][0]
    assert mention["auditee_name"] == "CRMC"
    assert "Positron" in mention["snippet"]
    assert "source_notice" in result
    assert result["notes_more_available"] == []
    assert "542003078" in result["caveats"][0] and "SEFA" in result["caveats"][0]


def test_a_snippet_keeps_the_name_written_after_the_ein(database):
    """Chesapeake's notes list each affiliate's EIN, then its name. FTS5's
    snippet ended on the EIN and cut the name off."""
    affiliates = "\n".join(
        f"54-10000{n:02}\tAffiliate Number {n}, LLC" for n in range(30)
    )
    database({
        "general": NOTED_GENERAL,
        "notes_to_sefa": [{
            "report_id": "2023-06-GSAFAC-0000000022",
            "title": "5. Entities included in the Schedule",
            "content": f"{affiliates}\n54-2003078\tPositron Emission Tomography of "
                       "Hampton Roads\nAll entities are included.",
        }],
    })  # fmt: skip

    [mention] = fac_find_linked_identifiers(ein="542003078")["named_in_sefa_notes"]

    snippet = mention["snippet"]
    assert "[54-2003078]\tPositron Emission Tomography of Hampton Roads" in snippet
    assert snippet.startswith("...")  # the 30 affiliates before it are trimmed
    assert len(snippet) < 400


def test_no_note_mentions_adds_no_note_caveat(noted_db):
    result = fac_find_linked_identifiers(ein="542006078")

    assert result["named_in_sefa_notes"] == []
    assert "source_notice" not in result
    assert not any("named_in_sefa_notes" in c for c in result["caveats"])
