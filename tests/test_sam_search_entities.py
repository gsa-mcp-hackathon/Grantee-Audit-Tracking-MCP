"""Tests for sam_search_entities. SAM is replaced by a fake, so these run offline."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import sam
from dat_mcp.tools.sam_search_entities import sam_search_entities


@pytest.fixture
def requests(monkeypatch):
    """Record each SAM request and answer with one entity returned twice."""
    sent = []

    def fake_get(params):
        sent.append(params)
        record = {"entityRegistration": {"ueiSAM": "T3RBNKC7NS15"}}
        return {"totalRecords": 2, "entityData": [record, record]}

    monkeypatch.setattr(sam, "get", fake_get)
    return sent


def test_search_by_name_and_state(requests):
    result = sam_search_entities(name="Peoples Health Supportive", state="mo")

    registered, unregistered = requests  # one short page each
    assert registered["q"] == "peoples health supportive"
    assert registered["physicalAddressProvinceOrStateCode"] == "MO"
    assert registered["samRegistered"] == "Yes"
    assert unregistered["samRegistered"] == "No"
    [record] = result["registrations"]  # duplicate records collapse to one
    assert record["source_url"] == "https://sam.gov/entity-information?v=1"


def test_pages_past_duplicates_until_limit(monkeypatch):
    """A first page of one UEI's duplicates mustn't hide the next match."""
    pages = [["T3RBNKC7NS15"] * 10, ["T3RBNKC7NS15", "JQNLHK8YLTU6"], ["X" * 12]]
    sent = []

    def fake_get(params):
        sent.append(params["page"])
        rows = [{"entityRegistration": {"ueiSAM": u}} for u in pages[params["page"]]]
        return {"totalRecords": 30, "entityData": rows}

    monkeypatch.setattr(sam, "get", fake_get)
    result = sam_search_entities(name="Peoples Health", limit=2)

    assert sent == [0, 1]  # stops once limit UEIs are found
    found = [r["uei"] for r in result["registrations"]]
    assert found == ["T3RBNKC7NS15", "JQNLHK8YLTU6"]


def test_unregistered_entities_follow_registered_ones(monkeypatch):
    sent = []

    def fake_get(params):
        sent.append((params["samRegistered"], params["page"]))
        if params["samRegistered"] == "Yes":
            reg = {"ueiSAM": "HCKZW5VKNU93", "registrationStatus": "Active"}
        else:
            reg = {"ueiSAM": "XRJ2DMLNMM34", "registrationStatus": "ID Assigned"}
        return {"totalRecords": 1, "entityData": [{"entityRegistration": reg}]}

    monkeypatch.setattr(sam, "get", fake_get)
    result = sam_search_entities(name="Baylor Scott White", state="TX")

    assert sent == [("Yes", 0), ("No", 0)]
    assert [r["uei"] for r in result["registrations"]] == [
        "HCKZW5VKNU93",
        "XRJ2DMLNMM34",
    ]
    # The sparse unregistered record is explained, as in sam_get_registration.
    assert result["caveats"][0].startswith("registration_status ID Assigned")


def test_both_passes_share_the_request_budget(monkeypatch):
    sent = []

    def fake_get(params):
        sent.append(params["samRegistered"])
        return {
            "totalRecords": 100,
            "entityData": [{"entityRegistration": {"ueiSAM": "T3RBNKC7NS15"}}] * 10,
        }

    monkeypatch.setattr(sam, "get", fake_get)
    result = sam_search_entities(name="Peoples Health", limit=2)

    assert sent == ["Yes"] * sam.MAX_PAGES
    # One UEI found, records unread, and the unregistered pass never ran.
    assert result["count"] == 1
    assert result["caveats"][0].startswith("Results may be incomplete")


def test_no_match_says_how_to_search_again(monkeypatch):
    monkeypatch.setattr(
        sam, "get", lambda params: {"totalRecords": 0, "entityData": []}
    )
    result = sam_search_entities(name="Peoples Health Supportive Services", state="MO")

    assert result["count"] == 0
    assert result["caveats"][0].startswith("No match isn't proof")
    assert any("try fewer words" in c for c in result["caveats"])


def test_name_cannot_carry_query_operators(requests):
    sam_search_entities(name="Scott & White Hospital - Round Rock NOT Temple")

    assert requests[0]["q"] == "scott white hospital round rock not temple"


def test_bad_input_is_rejected_before_calling_sam(requests):
    with pytest.raises(ToolError):
        sam_search_entities(name="Rush*")
    with pytest.raises(ToolError):
        sam_search_entities(name="---")
    with pytest.raises(ToolError):
        sam_search_entities(name="Rush System", state="Illinois")
    with pytest.raises(ToolError):
        sam_search_entities(name="Rush System", limit=11)
    assert requests == []
