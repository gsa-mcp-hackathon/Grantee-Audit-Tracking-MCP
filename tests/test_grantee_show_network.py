"""grantee_show_network: how the network is assembled from the identity
result, FAC listings, and Schedule R. Every tool it calls is replaced, so
nothing here reaches FAC, SAM.gov, or the db."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from dat_mcp.app import mcp
from dat_mcp.tools import grantee_show_network as module
from dat_mcp.tools.grantee_show_network import (
    ABOVE,
    BELOW,
    FAC,
    POSSIBLE,
    RELATED,
    SCHEDULE_R,
    UI_URI,
    grantee_show_network,
)

GRANTEE, PARENT = "000000001", "000000009"


def identity():
    return {
        "searched": {"ueis": ["DEMOHEALTH01"], "eins": []},
        "identifiers": [
            {
                "uei": "DEMOHEALTH01",
                "ein": GRANTEE,
                "names": ["RIVERBEND COMMUNITY HEALTH"],
                "links": ["searched UEI DEMOHEALTH01"],
                "audit_years": ["2021"],
            },
            {
                "uei": "DEMOHEALTH0I",
                "ein": GRANTEE,
                "names": ["RIVERBEND COMMUNITY HEALTH INC"],
                "links": ["UEI one character from DEMOHEALTH01, with the same EIN"],
                "audit_years": ["2024"],
            },
        ],
        "unconfirmed_leads": [
            {
                "uei": "DEMOCLINIC02",
                "ein": "000000002",
                "names": ["RIVERBEND DENTAL CLINIC"],
                "links": ["a different name at the same address"],
            }
        ],
        "other_organizations": [],
        "listed_in_parent_audits": [
            {
                "listed_by_name": "RIVERBEND HEALTH NETWORK",
                "listed_by_ein": PARENT,
                "listed_by_ueis": ["DEMOPARENT01"],
                "audit_years": ["2022"],
                "listed_by": "UEI",
                "source_url": "https://app.fac.gov/dissemination/summary/PARENT",
            }
        ],
        "sam_registrations": {
            "registrations": [
                {
                    "uei": "DEMOHEALTH01",
                    "legal_business_name": "Riverbend Community Health",
                    "city": "RIVERBEND",
                    "state": "MN",
                    "source_url": "https://sam.gov/entity/DEMOHEALTH01/coreData",
                }
            ]
        },
        "irs_records": {"records": []},
        "entity": {"fiscal_year_end": {"fac": {"report_id": "DEMO-2024"}}},
        "caveats": ["first", "FAC copy dated", "IRS copy dated"],
    }


def schedule_r(ein, limit):
    part_two = "related tax-exempt organization"
    assert GRANTEE in ein
    filer = {
        "ein": GRANTEE,
        "name": "RIVERBEND COMMUNITY HEALTH",
        "tax_year": 2024,
        "related_count": 4,
        "more_available": False,
        "source_url": "https://apps.irs.gov/app/eos/details/?ein=000000001",
        "related_organizations": [
            {
                "part": "I",
                "relationship": "disregarded entity",
                "organizations": [
                    {"name": "RIVERBEND PHARMACY LLC", "ein": "000000004"}
                ],
            },
            {
                "part": "II",
                "relationship": part_two,
                "organizations": [
                    {"name": "HOUSING", "ein": "000000005", "controlled": True},
                    {"name": "FOUNDATION", "ein": "000000006", "controlled": False},
                    {"name": "NETWORK", "ein": PARENT, "controlled": False},
                ],
            },
        ],
    }
    listed_by = [
        {
            "ein": GRANTEE,
            "filer_ein": PARENT,
            "filer_name": "RIVERBEND HEALTH NETWORK",
            "relationship": part_two,
            "tax_years": [2022, 2023],
            "source_url": "https://apps.irs.gov/app/eos/details/?ein=000000009",
            # The controlling entity changed; the latest year's is shown.
            "control": [
                {
                    "controlled": True,
                    "direct_controlling_entity": "OLD HOLDINGS",
                    "tax_years": [2022],
                },
                {
                    "controlled": True,
                    "direct_controlling_entity": "RIVERBEND HEALTH NETWORK",
                    "tax_years": [2023],
                },
            ],
        }
    ]
    return {"filers": [filer], "listed_by": listed_by, "not_stored": []}


def covered(report_id):
    assert report_id == "DEMO-2024"
    report = {
        "report_id": report_id,
        "audit_year": "2024",
        "source_url": "https://app.fac.gov/dissemination/summary/DEMO-2024",
        # The grantee's own second UEI, and an EIN Schedule R names too.
        "covered_ueis": ["DEMOCHILD001", "DEMOHEALTH0I"],
        "covered_eins": ["000000004", "000000007"],
    }
    return {"reports": [report], "more_available": False}


@pytest.fixture
def tools(monkeypatch):
    found = identity()
    monkeypatch.setattr(module, "grantee_identity_resolution", lambda **_: found)
    monkeypatch.setattr(module, "irs_get_related_orgs", schedule_r)
    monkeypatch.setattr(module, "fac_search_covered_entities", covered)
    monkeypatch.setattr(
        module,
        "irs_search_exempt_orgs",
        lambda ein: {
            "organizations": [{"ein": "000000007", "name": "RIVERBEND TRANSIT"}]
        },
    )
    return found


def network(**args):
    return grantee_show_network(**(args or {"uei": "DEMOHEALTH01"})).structured_content


def by_name(result, name):
    return next(o for o in result["organizations"] if o["name"] == name)


def test_the_grantees_identifiers_are_one_entity_not_its_children(tools):
    result = network()
    assert result["entity"]["name"] == "Riverbend Community Health"
    assert [i["uei"] for i in result["entity"]["identifiers"]] == [
        "DEMOHEALTH01",
        "DEMOHEALTH0I",
    ]
    assert result["entity"]["found_in"] == ["FAC", "SAM.gov"]
    assert not any("DEMOHEALTH0I" in o["ueis"] for o in result["organizations"])


def test_a_parent_in_fac_and_schedule_r_is_one_organization(tools):
    parent = by_name(network(), "RIVERBEND HEALTH NETWORK")
    assert (parent["eins"], parent["ueis"]) == ([PARENT], ["DEMOPARENT01"])
    assert parent["tier"] == ABOVE
    assert [e["source"] for e in parent["evidence"]] == [FAC, SCHEDULE_R, SCHEDULE_R]
    # What it last reported about controlling the grantee, from its listing.
    assert parent["evidence"][1]["controlled"] is True
    assert (
        parent["evidence"][1]["direct_controlling_entity"]
        == "RIVERBEND HEALTH NETWORK"
    )
    assert parent["evidence"][1]["years"] == [2022, 2023]


def test_tiers_follow_what_schedule_r_and_the_audit_say(tools):
    result = network()
    pharmacy = by_name(result, "RIVERBEND PHARMACY LLC")
    assert pharmacy["tier"] == BELOW  # a disregarded entity, and FAC lists it
    assert {e["source"] for e in pharmacy["evidence"]} == {FAC, SCHEDULE_R}
    assert by_name(result, "HOUSING")["tier"] == BELOW  # reported controlled
    assert by_name(result, "FOUNDATION")["tier"] == RELATED
    assert by_name(result, "RIVERBEND DENTAL CLINIC")["tier"] == POSSIBLE
    assert result["counts"][BELOW] == {"total": 4, "shown": 4}
    # What two sources agree on comes first in its tier.
    assert result["organizations"][0] is pharmacy


def test_entities_fac_lists_get_an_irs_name_or_stay_a_bare_uei(tools):
    result = network()
    transit = by_name(result, "RIVERBEND TRANSIT")
    assert (transit["eins"], transit["name_from"]) == (["000000007"], "IRS")
    bare = next(o for o in result["organizations"] if o["ueis"] == ["DEMOCHILD001"])
    assert bare["name"] is None
    assert any("shown by UEI alone" in note for note in result["notes"])


def test_an_ein_grouped_as_the_entity_is_not_drawn_as_its_parent(tools):
    # The parent's EIN is linked as one of the grantee's own identifiers.
    tools["identifiers"].append(
        {"uei": "DEMOPARENT01", "ein": PARENT, "names": ["NETWORK"], "links": ["x"]}
    )
    tools["listed_in_parent_audits"] = []
    result = network()
    assert not any(PARENT in o["eins"] for o in result["organizations"])
    assert any(
        f"EIN {PARENT} and EIN {GRANTEE} are both grouped as this entity" in note
        and "may be separate organizations" in note
        for note in result["notes"]
    )


def test_a_large_tier_is_cut_off_and_counted(tools, monkeypatch):
    monkeypatch.setattr(module, "MAX_PER_TIER", 1)
    result = network()
    assert result["counts"][BELOW] == {"total": 4, "shown": 1}
    assert len(result["organizations"]) == 4  # one per tier


def test_missing_sources_are_said_not_hidden(tools, monkeypatch):
    for entry in tools["identifiers"]:
        entry["ein"] = ""
    tools["listed_in_parent_audits"] = []
    tools["unconfirmed_leads"] = []

    def unavailable(report_id):
        raise ToolError("FAC is unavailable")

    monkeypatch.setattr(module, "fac_search_covered_entities", unavailable)
    result = network()
    assert result["organizations"] == []
    notes = " ".join(result["notes"])
    assert "Schedule R wasn't read" in notes
    assert "FAC is unavailable" in notes
    assert "not evidence it stands alone" in notes


def test_text_gives_each_organization_its_evidence_and_the_caveats(tools):
    text = grantee_show_network(uei="DEMOHEALTH01").content[0].text
    assert "UEI DEMOHEALTH01 / EIN 000000001, UEI DEMOHEALTH0I / EIN 000000001" in text
    assert "Lists this entity (1):" in text
    assert (
        "- RIVERBEND HEALTH NETWORK (EIN 000000009, UEI DEMOPARENT01): its audit "
        "lists this entity as covered, by UEI [FAC covered-entity listing, 2022]; "
        "its Schedule R reports this entity as a related tax-exempt organization "
        "[IRS Form 990 Schedule R, 2022-2023, reported as controlled]" in text
    )
    assert "decides nothing" in text
    assert "IRS copy dated" in text


def test_several_name_matches_return_candidates(monkeypatch):
    candidates = {"searched": {"name": "riverbend"}, "candidates": [{"name": "A"}]}
    monkeypatch.setattr(module, "grantee_identity_resolution", lambda **_: candidates)
    assert network(name="riverbend") == candidates


def test_tool_links_to_the_ui_resource_over_mcp(tools):
    async def exercise():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            tool = next(t for t in tools if t.name == "grantee_show_network")
            assert tool.meta["ui"]["resourceUri"] == UI_URI
            html = await client.read_resource(UI_URI)
            assert "ui/initialize" in html[0].text
            result = await client.call_tool(
                "grantee_show_network", {"uei": "DEMOHEALTH01"}
            )
            assert result.structured_content["entity"]["state"] == "MN"

    asyncio.run(exercise())
