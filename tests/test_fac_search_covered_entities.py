"""Tests for fac_search_covered_entities. FAC is replaced by a fake."""

import pytest

from dat_mcp import fac
from dat_mcp.tools.fac_search_covered_entities import fac_search_covered_entities

LISTING = {
    "report_id": "2024-06-GSAFAC-0000344864",
    "audit_year": "2024",
    "auditee_uei": "HCKZW5VKNU93",
    "fac_accepted_date": "2025-03-01",
}


@pytest.fixture
def rows():
    """What the fake FAC returns from each table; tests may edit it."""
    return {
        "additional_ueis": [{**LISTING, "additional_uei": "Z16URERPGVE5"}],
        "additional_eins": [
            {**LISTING, "additional_ein": "203749695"},
            {**LISTING, "additional_ein": "741234567"},
        ],
    }


@pytest.fixture
def requests(monkeypatch, rows):
    sent = []

    def fake_get(table, params):
        sent.append((table, params))
        return rows[table]

    monkeypatch.setattr(fac, "get", fake_get)
    return sent


def test_searches_the_ein_list_by_covered_ein(requests):
    result = fac_search_covered_entities(ein="20-3749695")

    [(table, params)] = requests
    assert table == "additional_eins"
    assert params["additional_ein"] == "eq.203749695"
    [report] = result["reports"]
    assert report["listed_by_auditee_uei"] == "HCKZW5VKNU93"
    assert "203749695" in report["covered_eins"]


def test_by_report_reads_both_lists(requests):
    fac_search_covered_entities(report_id=LISTING["report_id"])
    assert [table for table, _ in requests] == ["additional_ueis", "additional_eins"]


def test_a_uei_within_a_report_does_not_return_the_reports_eins(requests):
    """Checking one UEI against one report: the report's EIN list, unfiltered,
    was read as a match for that UEI."""
    result = fac_search_covered_entities(
        report_id=LISTING["report_id"], uei="Z16URERPGVE5"
    )

    [(table, params)] = requests
    assert table == "additional_ueis"
    assert params["additional_uei"] == "eq.Z16URERPGVE5"
    assert result["reports"][0]["covered_eins"] == []


def test_listings_are_grouped_under_their_report(requests):
    """One report entry carries the id and URL for all its identifiers."""
    result = fac_search_covered_entities(report_id=LISTING["report_id"])

    assert result["count"] == 3
    [report] = result["reports"]
    assert report["report_id"] == LISTING["report_id"]
    assert report["source_url"].endswith(LISTING["report_id"])
    assert report["covered_ueis"] == ["Z16URERPGVE5"]
    assert report["covered_eins"] == ["203749695", "741234567"]


def test_several_eins_are_checked_in_one_request(requests):
    """S13: every TIN a note names, in one call instead of one each."""
    fac_search_covered_entities(ein=["36-2170840", "364085751", "363235315"])

    [(table, params)] = requests
    assert table == "additional_eins"
    assert params["additional_ein"] == "in.(362170840,364085751,363235315)"


def test_the_limit_is_per_report_id_and_truncation_is_flagged(requests, rows):
    """S10: seven report ids at limit 100 returned 8 EINs for one report, silently."""
    two_reports = ["2024-06-GSAFAC-0000344864", "2024-12-GSAFAC-0000367814"]
    result = fac_search_covered_entities(report_id=two_reports)

    assert all(params["limit"] == 200 for _, params in requests)
    assert result["more_available"] is False

    rows["additional_eins"] = [
        {**LISTING, "additional_ein": f"20{n:07}"} for n in range(200)
    ]
    result = fac_search_covered_entities(report_id=two_reports)
    assert result["more_available"] is True
    assert "cut off at 200 rows" in " ".join(result["caveats"])


def test_uei_and_ein_can_be_searched_together(requests):
    fac_search_covered_entities(uei="Z16URERPGVE5", ein="203749695")

    assert [table for table, _ in requests] == ["additional_ueis", "additional_eins"]
    uei_params, ein_params = (params for _, params in requests)
    assert uei_params["additional_uei"] == "eq.Z16URERPGVE5"
    assert ein_params["additional_ein"] == "eq.203749695"


def test_searching_by_one_identifier_says_to_try_the_other(requests):
    caveats = " ".join(fac_search_covered_entities(uei="Z16URERPGVE5")["caveats"])
    assert "Only the UEI was searched" in caveats

    caveats = " ".join(
        fac_search_covered_entities(uei="Z16URERPGVE5", ein="203749695")["caveats"]
    )
    assert "was searched" not in caveats


def test_no_listing_points_to_the_text_tools(requests, rows):
    rows["additional_eins"] = []
    result = fac_search_covered_entities(ein="203749695")

    assert result["count"] == 0
    assert result["reports"] == []
    assert "fac_search_audit_text" in " ".join(result["caveats"])


def test_a_report_with_listings_gets_no_not_listed_caveat(requests):
    caveats = " ".join(fac_search_covered_entities(ein="203749695")["caveats"])
    assert "No listing" not in caveats


def test_a_found_parent_comes_with_the_advice_to_follow_it(requests):
    """S17: the agent found the 2022 listing and stopped instead of checking 2023."""
    caveats = " ".join(fac_search_covered_entities(ein="203749695")["caveats"])
    assert "fac_search_audits(uei=listed_by_auditee_uei)" in caveats

    by_report = fac_search_covered_entities(report_id=LISTING["report_id"])
    assert "listed_by_auditee_uei)" not in " ".join(by_report["caveats"])
