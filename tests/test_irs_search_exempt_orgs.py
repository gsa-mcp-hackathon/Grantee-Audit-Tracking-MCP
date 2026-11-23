"""Tests for irs_search_exempt_orgs, on a fixture database."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp.tools.irs_search_exempt_orgs import _name_query, irs_search_exempt_orgs


def org(ein, name, state, **fields):
    """An eo_bmf row as the IRS posts it, independent unless fields say not."""
    return {
        "EIN": ein,
        "NAME": name,
        "CITY": "JUNCTION CITY",
        "STATE": state,
        "GROUP": "0000",
        "SUBSECTION": "03",
        "AFFILIATION": "3",
        "FOUNDATION": "15",
        "STATUS": "01",
        "RULING": "199507",
        "TAX_PERIOD": "202512",
        "FILING_REQ_CD": "01",
        "ACCT_PD": "12",
        "REVENUE_AMT": "20453683",
        "ASSET_AMT": "14310140",
        **fields,
    }


EO_BMF = [
    org("481150706", "KONZA PRAIRIE COMMUNITY HEALTH CENTER INC", "KS"),
    org("481238655", "KONZA PRAIRIE VOLLEYBALL CLUB INC", "KS", REVENUE_AMT=""),
    org("943480131", "LOUISIANA CHILDRENS MEDICAL CENTER", "LA", FOUNDATION="12"),
    org("953246624", "INLAND BEHAVIORAL AND HEALTH SERVICES INC", "CA", ACCT_PD="06"),
    # Covenant is a subordinate in the Catholic group exemption, whose
    # central organization is the US Catholic Conference.
    org(
        "222484505",
        "COVENANT HEALTH INC",
        "MA",
        GROUP="0928",
        AFFILIATION="9",
        FOUNDATION="16",
    ),
    org(
        "530196617",
        "UNITED STATES CATHOLIC CONFERENCE",
        "DC",
        GROUP="0928",
        AFFILIATION="8",
    ),
    org("232552707", "NUEVA ESPERANZA", "PA", SORT_NAME="NUEVA ESPERANZA INC"),
]


def revoked(ein, name, date, reinstated=""):
    return {
        "ein": ein,
        "name": name,
        "city": "SHOW LOW",
        "state": "AZ",
        "subsection": "03",
        "revocation_date": date,
        "revocation_posting_date": "2022-05-11",
        "reinstatement_date": reinstated,
    }


REVOCATIONS = [
    revoked("860215065", "CHANGEPOINT INTEGRATED HEALTH", "2022-02-15"),
    # Revoked in the IRS's 2020 window, then reinstated, and still not in the BMF.
    revoked("010019708", "A REINSTATED CHARITY", "2020-05-15", "2021-01-01"),
    # On the list but back in the EO BMF: not reported.
    revoked("481150706", "KONZA PRAIRIE COMMUNITY HEALTH CENTER", "2015-05-15",
            "2016-01-01"),
]  # fmt: skip


@pytest.fixture
def irs_db(database):
    database({"eo_bmf": EO_BMF, "irs_revocations": REVOCATIONS})


def test_ein_lookup_finds_the_record_and_names_eins_the_irs_lacks(irs_db):
    """S2: Konza's real EIN is on file; the typo FAC has for 2023-2024 isn't."""
    result = irs_search_exempt_orgs(ein=["48-1150706", "148115070"])

    [konza] = result["organizations"]
    assert konza["ein"] == "481150706"
    assert konza["name"] == "KONZA PRAIRIE COMMUNITY HEALTH CENTER INC"
    assert konza["exemption"] == "501(c)(3)"
    assert konza["exemption_status"] == "unconditional exemption"
    assert konza["ruling_date"] == "1995-07"
    assert konza["latest_return_period"] == "2025-12"
    assert konza["fiscal_year_end_month"] == 12
    assert konza["revenue_amount"] == 20453683
    assert konza["group_exemption"] is None
    assert konza["source_url"].endswith("?ein=481150706")
    assert result["not_found"] == ["148115070"]
    assert result["caveats"][0].startswith("Not in the EO BMF: 148115070")
    assert result["caveats"][-1].startswith("Results come from a local copy of IRS")


def test_ein_lookup_keeps_the_order_asked(irs_db):
    result = irs_search_exempt_orgs(ein=["953246624", "481150706"])

    assert [o["ein"] for o in result["organizations"]] == ["953246624", "481150706"]
    assert result["organizations"][0]["fiscal_year_end_month"] == 6
    assert result["not_found"] == []
    assert not any(c.startswith("Not in") for c in result["caveats"])


def test_group_subordinate_names_its_central_organization(irs_db):
    result = irs_search_exempt_orgs(ein=["222484505", "530196617"])

    covenant, conference = result["organizations"]
    assert covenant["group_exemption"] == {
        "number": "0928",
        "role": "subordinate in a group exemption",
        "central_ein": "530196617",
        "central_name": "UNITED STATES CATHOLIC CONFERENCE",
    }
    # The central organization isn't its own central.
    assert "central_ein" not in conference["group_exemption"]
    assert any(c.startswith("In a group exemption") for c in result["caveats"])


def test_name_search_matches_names_as_the_irs_writes_them(irs_db):
    """The IRS drops apostrophes and spells out AND."""
    result = irs_search_exempt_orgs(name="Louisiana Children's Medical Center")
    assert [o["ein"] for o in result["organizations"]] == ["943480131"]
    assert result["organizations"][0]["foundation_status"] == (
        "hospital or medical research organization"
    )

    result = irs_search_exempt_orgs(name="Inland Behavioral & Health Services, Inc.")
    assert [o["ein"] for o in result["organizations"]] == ["953246624"]
    assert any(c.startswith("Name matches are candidates") for c in result["caveats"])


def test_name_search_matches_the_secondary_name(irs_db):
    result = irs_search_exempt_orgs(name="Nueva Esperanza Inc", state="pa")

    [record] = result["organizations"]
    assert record["secondary_name"] == "NUEVA ESPERANZA INC"


def test_name_search_narrows_by_state_and_flags_more_available(irs_db):
    assert irs_search_exempt_orgs(name="Konza Prairie", state="LA")["count"] == 0

    result = irs_search_exempt_orgs(name="Konza Prairie", state="KS", limit=1)
    assert result["count"] == 1
    assert result["more_available"] is True
    assert any(c.startswith("more_available is true") for c in result["caveats"])


def test_no_name_match_suggests_how_to_search_again(irs_db):
    result = irs_search_exempt_orgs(name="Konza Valley")

    assert result["organizations"] == []
    assert any(c.startswith("Nothing matched") for c in result["caveats"])


def test_name_words_are_searched_not_run_as_operators():
    assert _name_query("Scott & White NOT Temple") == '"scott" "white" "not" "temple"'
    assert _name_query("The Children's Hospital of Philadelphia") == (
        '"childrens" "hospital" "philadelphia"'
    )


def test_ein_and_name_together_check_a_suspected_typo(irs_db):
    """S2 in one call: is 148115070 Konza's, and which EIN does Konza have?"""
    result = irs_search_exempt_orgs(
        ein=["148115070", "953246624"],
        name="Konza Prairie Community Health",
        state="KS",
    )

    # The EIN lookups, each checked against the name given.
    [inland] = result["organizations"]
    assert inland["ein"] == "953246624"
    assert inland["matches_name"] is False
    assert result["not_found"] == ["148115070"]
    # And what the IRS has under that name.
    assert [o["ein"] for o in result["name_candidates"]] == ["481150706"]
    assert result["caveats"][0].startswith("matches_name is false for 953246624")


def test_a_matching_ein_and_name_say_so(irs_db):
    result = irs_search_exempt_orgs(ein="481150706", name="Konza Prairie Community")

    assert result["organizations"][0]["matches_name"] is True
    assert not any(c.startswith("matches_name") for c in result["caveats"])
    # A name search alone still returns its matches as organizations.
    alone = irs_search_exempt_orgs(name="Konza Prairie Community")
    assert alone["organizations"][0]["ein"] == "481150706"
    assert alone["name_candidates"] == []


def test_bad_input_is_rejected(irs_db):
    with pytest.raises(ToolError, match="or both"):
        irs_search_exempt_orgs()
    with pytest.raises(ToolError, match="state narrows"):
        irs_search_exempt_orgs(ein="481150706", state="KS")
    with pytest.raises(ToolError, match="9 digits"):
        irs_search_exempt_orgs(ein="4811507")
    with pytest.raises(ToolError, match="letters"):
        irs_search_exempt_orgs(name="Konza*")
    with pytest.raises(ToolError, match="distinctive"):
        irs_search_exempt_orgs(name="The Inc")
    with pytest.raises(ToolError, match="limit"):
        irs_search_exempt_orgs(name="Konza", limit=51)


def test_a_missing_ein_on_the_revocation_list_says_so(irs_db):
    """S15: Changepoint's exemption was revoked; 148115070 is just missing."""
    result = irs_search_exempt_orgs(ein=["860215065", "148115070", "481150706"])

    assert result["not_found"] == ["860215065", "148115070"]
    [changepoint] = result["revocations"]  # Konza is in the BMF, so not listed
    assert changepoint == {
        "ein": "860215065",
        "name": "CHANGEPOINT INTEGRATED HEALTH",
        "secondary_name": None,
        "city": "SHOW LOW",
        "state": "AZ",
        "exemption": "501(c)(3)",
        "revocation_date": "2022-02-15",
        "revocation_posting_date": "2022-05-11",
        "reinstatement_date": None,
        "source_url": "https://apps.irs.gov/app/eos/details/?ein=860215065",
    }
    assert result["caveats"][0].startswith(
        "On the IRS's automatic revocation list (revocations): 860215065."
    )
    # Only the EIN the revocation list doesn't explain gets the not-found caveat.
    assert any(c.startswith("Not in the EO BMF: 148115070.") for c in result["caveats"])


def test_a_2020_revocation_date_carries_the_irss_correction(irs_db):
    result = irs_search_exempt_orgs(ein="010019708")

    [record] = result["revocations"]
    assert record["revocation_date"] == "2020-05-15"  # as posted
    assert record["corrected_revocation_date"] == "2020-07-15"
    assert record["reinstatement_date"] == "2021-01-01"
    assert any(c.startswith("corrected_revocation_date:") for c in result["caveats"])


def test_names_match_through_abbreviations_but_not_different_words():
    from dat_mcp.tools.irs_search_exempt_orgs import same_name

    # The IRS's abbreviations and punctuation aren't mismatches.
    assert same_name(["INLAND BEHAVIORAL AND HEALTH SVCS INC"],
                     ["Inland Behavioral & Health Services, Inc."])  # fmt: skip
    assert same_name(["ST JOSEPH HOSPITAL"], ["Saint Joseph Hospital"])
    assert same_name(["KONZA PRAIRIE COMMUNITY HEALTH CTR"],
                     ["Konza Prairie Community Health Center"])  # fmt: skip
    # Every word of the shorter name must match: a shorter form is fine...
    assert same_name(["INLAND BEHAVIORAL AND HEALTH SERVICES INC"],
                     ["INLAND BEHAVIORAL"])  # fmt: skip
    # ...but a different word isn't, even when most words match (S19).
    assert not same_name(["BAYLOR SCOTT & WHITE HOLDINGS"],
                         ["Baylor Scott & White Health"])  # fmt: skip
    assert not same_name(["POSITRON EMISSION TOMOGRAPHY OF HAMPTON ROADS"],
                         ["COMMUNITY HAVENS INC"])  # fmt: skip


# MR !10 review. Each test below states the behavior the review expects, and
# fails on the branch as reviewed (3442bb4).
def test_a_different_word_is_not_read_as_an_abbreviation():
    """Finding 1. The names are real (EIN 314391798) but the miss isn't: the
    IRS also gives that EIN the secondary name SOUTHEASTERN OH REG MEDICAL
    CENTER, which matches. No real miss was found in FAC and IRS data."""
    from dat_mcp.tools.irs_search_exempt_orgs import same_name

    # COUNTY's letters appear in order in COMMUNITY.
    assert not same_name(["MARION COMMUNITY HEALTH"], ["Marion County Health"])
    # OHIO is the start of OHIOHEALTH, the IRS name's only distinctive word.
    assert not same_name(
        ["OHIOHEALTH CORPORATION"],
        ["Southeastern Ohio Regional Medical Center and Subsidiaries"],
    )


def test_accents_and_curly_apostrophes_match_the_irs_spelling():
    """Finding 7. Real FAC names whose own EIN's IRS name is the same without
    the accent (412096004, 660205634) or the apostrophe (223269937);
    grantee_identity_resolution asks whether each EIN belongs to the filer."""
    from dat_mcp.tools.irs_search_exempt_orgs import same_name

    assert same_name(
        ["ANA G MENDEZ UNIVERSITY INC"], ["Ana G. Méndez University, Inc."]
    )
    assert same_name(
        ["LIGA PUERTORRIQUENA CONTRA EL CANCER"],
        ["Liga Puertorriqueña Contra el Cáncer, Inc."],
    )
    assert same_name(
        ["BNAI BRITH ELMWOOD HOUSE INC"],
        ["B’NAI B’RITH ELMWOOD HOUSE, INC."],
    )
    # A name search takes the name as FAC writes it.
    assert "ndez" in _name_query("Ana G. Méndez University")
