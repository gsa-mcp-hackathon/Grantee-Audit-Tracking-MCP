"""Tests for sam_get_registration and the SAM.gov client.

SAM.gov is replaced by a fake, so these run offline.
"""

import httpx
import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import sam
from dat_mcp.config import settings
from dat_mcp.tools.sam_get_registration import sam_get_registration


def row(uei, name="Inland Behavioral and Health Services"):
    return {
        "entityRegistration": {
            "ueiSAM": uei,
            "legalBusinessName": name,
            "registrationStatus": "Active",
        },
        "coreData": {
            "entityInformation": {"fiscalYearEndCloseDate": "06/30"},
            "physicalAddress": {"city": "San Bernardino", "stateOrProvinceCode": "CA"},
        },
    }


@pytest.fixture
def pages(monkeypatch):
    """Answer each SAM request with the next canned page, recording params."""
    canned = []
    sent = []

    def fake_get(params):
        sent.append(params)
        rows = canned[len(sent) - 1] if len(sent) <= len(canned) else []
        return {"totalRecords": sum(len(p) for p in canned), "entityData": rows}

    monkeypatch.setattr(sam, "get", fake_get)
    return canned, sent


def test_lookup_builds_uei_filter_and_flattens_record(pages):
    canned, sent = pages
    canned.append([row("JQNLHK8YLTU6")])

    result = sam_get_registration(["jqnlhk8yltu6"])

    [params] = sent
    assert params["ueiSAM"] == "[JQNLHK8YLTU6]"
    [record] = result["registrations"]
    assert record["registration_status"] == "Active"
    assert record["fiscal_year_end"] == "06/30"
    assert record["physical_address"]["state"] == "CA"
    assert record["source_url"] == "https://sam.gov/entity-information?v=1"
    assert "dba_name" not in record  # empty fields are left out
    assert result["not_found"] == []
    assert not any("not_found" in c for c in result["caveats"])


def test_unknown_uei_is_reported_as_not_found(pages):
    canned, sent = pages
    canned.append([row("JQNLHK8YLTU6")])

    result = sam_get_registration(["JQNLHK8YLTUB", "JQNLHK8YLTU6"])

    # One short page settles the registered lookup; then one retry for the
    # missing UEI among assigned-but-unregistered ones.
    first, retry = sent
    assert first["ueiSAM"] == "[JQNLHK8YLTUB~JQNLHK8YLTU6]"
    assert "samRegistered" not in first
    assert retry["ueiSAM"] == "[JQNLHK8YLTUB]"
    assert retry["samRegistered"] == "No"
    assert [r["uei"] for r in result["registrations"]] == ["JQNLHK8YLTU6"]
    assert result["not_found"] == ["JQNLHK8YLTUB"]
    assert result["caveats"][0].startswith("not_found:")


def test_assigned_but_unregistered_uei_is_found(pages):
    """A real UEI the entity never registered only comes back on the retry."""
    canned, sent = pages
    canned.append([row("HCKZW5VKNU93", name="Baylor Scott & White Holdings")])
    unregistered = row("XRJ2DMLNMM34", name="Baylor Scott & White Health")
    unregistered["entityRegistration"]["registrationStatus"] = "ID Assigned"
    canned.append([unregistered])

    result = sam_get_registration(["HCKZW5VKNU93", "XRJ2DMLNMM34"])

    assert sent[1]["samRegistered"] == "No"
    assert [r["uei"] for r in result["registrations"]] == [
        "HCKZW5VKNU93",
        "XRJ2DMLNMM34",
    ]
    assert result["not_found"] == []
    assert result["caveats"][0].startswith("registration_status ID Assigned")


def test_duplicates_on_first_page_dont_hide_a_uei(pages):
    """DAT's paging fix: copies of one UEI fill page 0, the other UEI is on page 1."""
    canned, sent = pages
    canned.append([row("JQNLHK8YLTU6")] * 10)
    canned.append([row("KUWQTKN683P6", name="AxessPointe")])

    result = sam_get_registration(["JQNLHK8YLTU6", "KUWQTKN683P6"])

    assert [p["page"] for p in sent] == [0, 1]
    assert [r["uei"] for r in result["registrations"]] == [
        "JQNLHK8YLTU6",
        "KUWQTKN683P6",
    ]
    assert result["not_found"] == []


def test_paging_stops_at_max_pages(pages):
    canned, sent = pages
    canned.extend([[row("JQNLHK8YLTU6")] * 10] * (sam.MAX_PAGES + 2))

    result = sam_get_registration(["JQNLHK8YLTU6", "KUWQTKN683P6"])

    assert len(sent) == sam.MAX_PAGES + 1  # the pages, then the unregistered retry
    assert sent[-1]["ueiSAM"] == "[KUWQTKN683P6]"
    assert result["not_found"] == ["KUWQTKN683P6"]
    # It may be on a page never read, so don't claim it has no record.
    assert result["caveats"][0].startswith("not_found may be incomplete")


def test_unread_unregistered_records_leave_not_found_unsettled(monkeypatch):
    """The unregistered retry reads one page; duplicates can fill it too."""

    def fake_get(params):
        if params.get("samRegistered") != "No":
            return {"totalRecords": 0, "entityData": []}
        return {"totalRecords": 12, "entityData": [row("XRJ2DMLNMM34")] * 10}

    monkeypatch.setattr(sam, "get", fake_get)
    result = sam_get_registration(["XRJ2DMLNMM34", "HCKZW5VKNU93"])

    assert result["not_found"] == ["HCKZW5VKNU93"]
    assert result["caveats"][0].startswith("not_found may be incomplete")


def test_record_without_uei_keeps_the_key(pages):
    canned, _ = pages
    canned.append([{"entityRegistration": {"legalBusinessName": "No UEI"}}])

    result = sam_get_registration(["JQNLHK8YLTU6"])

    assert result["not_found"] == ["JQNLHK8YLTU6"]
    assert sam.registration({"entityRegistration": {}}) == {"uei": None}


def test_bad_input_is_rejected_before_calling_sam(pages):
    _, sent = pages
    with pytest.raises(ToolError):
        sam_get_registration([])
    with pytest.raises(ToolError):
        sam_get_registration(["JQNLHK8YLTU6"] * 11)
    with pytest.raises(ToolError):
        sam_get_registration(["JQNLHK8YLTU6", "JQNLHK8"])
    assert sent == []


def test_missing_key_is_reported_before_calling_sam(monkeypatch):
    monkeypatch.setattr(settings, "sam_api_key", "")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: pytest.fail("called"))
    with pytest.raises(ToolError, match="SAM_API_KEY isn't set"):
        sam.get({"ueiSAM": "[JQNLHK8YLTU6]"})


@pytest.mark.parametrize(
    "status, message",
    [
        (403, "HTTP 403; check that SAM_API_KEY is set and hasn't expired"),
        (500, "HTTP 500.$"),
    ],
)
def test_api_errors_report_status_without_the_key(monkeypatch, status, message):
    monkeypatch.setattr(settings, "sam_api_key", "SECRET-KEY")

    def fake_httpx_get(url, params, **kwargs):
        return httpx.Response(status, text=f"bad key {params['api_key']}")

    monkeypatch.setattr(httpx, "get", fake_httpx_get)
    with pytest.raises(ToolError, match=message) as error:
        sam.get({"ueiSAM": "[JQNLHK8YLTU6]"})
    assert "SECRET-KEY" not in str(error.value)


def test_unreachable_api_error_omits_the_key(monkeypatch):
    monkeypatch.setattr(settings, "sam_api_key", "SECRET-KEY")

    def unreachable(url, params, **kwargs):
        raise httpx.ConnectError(f"failed {url}?api_key={params['api_key']}")

    monkeypatch.setattr(httpx, "get", unreachable)
    with pytest.raises(ToolError) as error:
        sam.get({"ueiSAM": "[JQNLHK8YLTU6]"})
    assert "SECRET-KEY" not in str(error.value)
