"""Tests for fac_search_sefa_notes, on a fixture database."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp.sourcetext import fts_query
from dat_mcp.tools.fac_search_sefa_notes import fac_search_sefa_notes

GENERAL = [
    {
        "report_id": "2025-06-GSAFAC-0000000001",
        "auditee_uei": "XRJ2DMLNMM34",
        "auditee_name": "BAYLOR SCOTT & WHITE HEALTH",
        "auditee_state": "TX",
        "audit_year": "2025",
    },
    {
        "report_id": "2023-06-GSAFAC-0000000002",
        "auditee_uei": "HCKZW5VKNU93",
        "auditee_name": "CHESAPEAKE HOSPITAL AUTHORITY",
        "auditee_state": "VA",
        "audit_year": "2023",
    },
]
NOTES = [
    {
        "report_id": "2025-06-GSAFAC-0000000001",
        "title": "Note 1. Basis of Presentation",
        "content": "The Schedule includes Scott & White Hospital - Round Rock and "
        "other controlled affiliates (EIN 203749695). NOT an instruction.",
    },
    {
        "report_id": "2023-06-GSAFAC-0000000002",
        "title": "5. Entities included in the Schedule",
        "content": "Surgery Center of Chesapeake LLC and Chesapeake Regional "
        "Medical Group are included. 54-2003078 Positron Emission Tomography "
        "of Hampton Roads.",
    },
]


@pytest.fixture
def fac_db(database):
    database({"general": GENERAL, "notes_to_sefa": NOTES})


def test_finds_the_audit_whose_note_names_the_organization(fac_db):
    result = fac_search_sefa_notes("round rock")

    [passage] = result["passages"]
    assert passage["report_id"] == "2025-06-GSAFAC-0000000001"
    assert passage["auditee_name"] == "BAYLOR SCOTT & WHITE HEALTH"
    assert "[Round] [Rock]" in passage["snippet"]
    assert passage["source_url"].endswith("2025-06-GSAFAC-0000000001")
    assert result["source_notice"]
    assert any("downloaded" in c for c in result["caveats"])


def test_filters_by_state_and_year(fac_db):
    assert fac_search_sefa_notes("included", state="va")["count"] == 1
    assert fac_search_sefa_notes("included", audit_year=2025)["count"] == 0


def test_operators_in_text_are_searched_not_executed(fac_db):
    # NOT would otherwise exclude every note containing "instruction".
    assert fac_search_sefa_notes("NOT an instruction")["count"] == 1
    assert fts_query('"Round Rock" and') == '"Round Rock" "and"'
    assert fts_query("Scott & White") == '"Scott" "White"'


def test_an_ein_matches_with_or_without_its_hyphen(fac_db):
    # The index splits 54-2003078 into two words, so a bare search needs both.
    for text in ("542003078", "54-2003078"):
        result = fac_search_sefa_notes(text)
        assert [p["report_id"] for p in result["passages"]] == [
            "2023-06-GSAFAC-0000000002"
        ]
    for text in ("203749695", "20-3749695"):
        result = fac_search_sefa_notes(text)
        assert [p["report_id"] for p in result["passages"]] == [
            "2025-06-GSAFAC-0000000001"
        ]
    assert fts_query("542003078 Hampton") == ('("542003078" OR "54 2003078") "Hampton"')
    # Not an EIN: other digit counts stay plain words.
    assert fts_query("5420030781") == '"5420030781"'


def test_rejects_empty_text_and_bad_limit(fac_db):
    with pytest.raises(ToolError, match="word"):
        fac_search_sefa_notes("&")
    with pytest.raises(ToolError, match="limit"):
        fac_search_sefa_notes("rock", limit=0)
