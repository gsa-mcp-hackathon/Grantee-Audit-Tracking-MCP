"""Tests for the Schedule R parser and scripts/fetch_schedule_r.py, offline."""

import csv
import sqlite3
from contextlib import contextmanager

import fetch_schedule_r
import pytest
from conftest import build_db

from dat_mcp import form990, irs

BSW = "463131350"
OBJECT_ID = "202531339349307668"

RETURN_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2023v6.0">
  <ReturnHeader>
    <TaxPeriodEndDt>2024-06-30</TaxPeriodEndDt>
    <ReturnTypeCd>990</ReturnTypeCd>
    <TaxYr>2023</TaxYr>
    <Filer>
      <EIN>463131350</EIN>
      <BusinessName>
        <BusinessNameLine1Txt>Baylor Scott &amp; White Health</BusinessNameLine1Txt>
      </BusinessName>
    </Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990ScheduleR>
      <IdDisregardedEntitiesGrp>
        <DisregardedEntityName>
          <BusinessNameLine1Txt>BSW Offshore</BusinessNameLine1Txt>
          <BusinessNameLine2Txt>Insurance Ltd</BusinessNameLine2Txt>
        </DisregardedEntityName>
        <EIN>981234567</EIN>
        <PrimaryActivitiesTxt>Insurance</PrimaryActivitiesTxt>
        <LegalDomicileForeignCountryCd>CJ</LegalDomicileForeignCountryCd>
      </IdDisregardedEntitiesGrp>
      <IdRelatedTaxExemptOrgGrp>
        <DisregardedEntityName>
          <BusinessNameLine1Txt>S&amp;W Hospital-Round Rock</BusinessNameLine1Txt>
        </DisregardedEntityName>
        <USAddress><StateAbbreviationCd>TX</StateAbbreviationCd></USAddress>
        <EIN>203749695</EIN>
        <PrimaryActivitiesTxt>Hospital</PrimaryActivitiesTxt>
        <LegalDomicileStateCd>TX</LegalDomicileStateCd>
        <ExemptCodeSectionTxt>501(c)(3)</ExemptCodeSectionTxt>
        <DirectControllingEntityName>
          <BusinessNameLine1Txt>Scott &amp; White Healthcare</BusinessNameLine1Txt>
        </DirectControllingEntityName>
        <ControlledOrganizationInd>1</ControlledOrganizationInd>
      </IdRelatedTaxExemptOrgGrp>
      <IdRelatedOrgTxblPartnershipGrp>
        <RelatedOrganizationName>
          <BusinessNameLine1Txt>Arlington Ortho Hospital LLC</BusinessNameLine1Txt>
        </RelatedOrganizationName>
        <EIN>261578178</EIN>
        <DirectControllingNACd>X</DirectControllingNACd>
      </IdRelatedOrgTxblPartnershipGrp>
      <IdRelatedOrgTxblCorpTrGrp>
        <RelatedOrganizationName>
          <BusinessNameLine1Txt>BSW Condo Owners Assoc Inc</BusinessNameLine1Txt>
        </RelatedOrganizationName>
        <EIN>261661900</EIN>
        <EntityTypeTxt>C</EntityTypeTxt>
        <ControlledOrganizationInd>0</ControlledOrganizationInd>
      </IdRelatedOrgTxblCorpTrGrp>
      <TransactionsRelatedOrgGrp>
        <OtherOrganizationName>
          <BusinessNameLine1Txt>Not a related-organization row</BusinessNameLine1Txt>
        </OtherOrganizationName>
      </TransactionsRelatedOrgGrp>
    </IRS990ScheduleR>
  </ReturnData>
</Return>
"""

NO_SCHEDULE_R = RETURN_XML.split(b"<ReturnData>")[0] + b"<ReturnData/></Return>"


def test_parser_reads_schedule_r_parts_one_to_four():
    filing, related = form990.schedule_r(RETURN_XML)

    assert filing == {
        "ein": BSW,
        "name": "Baylor Scott & White Health",
        "tax_year": "2023",
        "tax_period_end": "2024-06-30",
        "form": "990",
        "has_schedule_r": True,
    }
    assert [r["part"] for r in related] == ["I", "II", "III", "IV"]  # no Part V
    offshore, round_rock, partnership, condo = related
    assert offshore["name"] == "BSW Offshore Insurance Ltd"
    assert offshore["legal_domicile"] == "CJ"
    assert offshore["controlled"] == ""  # Part I doesn't ask
    assert round_rock["relationship"] == "related tax-exempt organization"
    assert round_rock["ein"] == "203749695"
    assert round_rock["state"] == "TX"
    assert round_rock["direct_controlling_entity"] == "Scott & White Healthcare"
    assert round_rock["controlled"] == "Y"
    assert partnership["direct_controlling_entity"] == ""  # N/A
    assert condo["entity_type"] == "corporation"
    assert condo["controlled"] == "N"


def test_parser_handles_a_990_without_schedule_r_and_rejects_other_xml():
    filing, related = form990.schedule_r(NO_SCHEDULE_R)
    assert filing["has_schedule_r"] is False
    assert related == []
    with pytest.raises(ValueError, match="ReturnHeader"):
        form990.schedule_r(b"<html><body>Page not found</body></html>")


def index_row(ein, period, form, object_id, name="BAYLOR SCOTT & WHITE HEALTH"):
    return {
        "RETURN_ID": "",
        "FILING_TYPE": "EFILE",
        "EIN": ein,
        "TAX_PERIOD": period,
        "SUB_DATE": "2025",
        "TAXPAYER_NAME": name,
        "RETURN_TYPE": form,
        "DLN": "",
        "OBJECT_ID": object_id,
        "XML_BATCH_ID": "2025_TEOS_XML_05a",
    }


INDEX = [
    index_row(BSW, "202406", "990", "202531339349300001"),
    index_row(BSW, "202406", "990", OBJECT_ID),  # amended: the later filing
    index_row(BSW, "202406", "990T", "202531339339300002"),  # filed alongside
    index_row("820000001", "202412", "990PF", "202500000000000001", "SMALL FDN"),
    index_row("999999999", "202406", "990", "202500000000000002", "NOT ASKED FOR"),
]


def write_index(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(INDEX[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_find_returns_keeps_the_latest_filing_of_each_form(tmp_path):
    write_index(tmp_path / "index_2025.csv", INDEX)

    found = fetch_schedule_r.find_returns(
        [tmp_path / "index_2025.csv"], [BSW, "820000001"]
    )

    assert [(r["EIN"], r["RETURN_TYPE"], r["OBJECT_ID"]) for r in found] == [
        (BSW, "990", OBJECT_ID),
        ("820000001", "990PF", "202500000000000001"),
    ]


def test_tax_year_is_the_year_the_period_starts():
    assert fetch_schedule_r.tax_year("202406") == 2023
    assert fetch_schedule_r.tax_year("202412") == 2024


def test_ein_list_ignores_comments_and_repeats(tmp_path):
    path = tmp_path / "eins.txt"
    path.write_text("# header\n46-3131350  # BSW\n\n463131350\n203749695\n")
    assert fetch_schedule_r.read_eins(path) == [BSW, "203749695"]
    # The checked-in list parses.
    assert len(fetch_schedule_r.read_eins(fetch_schedule_r.EIN_LIST)) > 10


def test_main_writes_csvs_the_build_loads(tmp_path, monkeypatch):
    """Index download, return fetch, and CSVs, with the IRS faked."""
    downloads, fetched = [], []

    def fake_download(url, path):
        downloads.append(url)
        write_index(path, INDEX if url.endswith("index_2025.csv") else [])

    @contextmanager
    def fake_reader():
        def read(batch_id, object_id):
            fetched.append((batch_id, object_id))
            return RETURN_XML

        yield read

    monkeypatch.setattr(irs, "download", fake_download)
    monkeypatch.setattr(irs, "efile_reader", fake_reader)
    eins_file = tmp_path / "eins.txt"
    eins_file.write_text(f"{BSW}  # already listed\n")
    args = ["820000001", "--data", str(tmp_path), "--eins-file", str(eins_file)]
    fetch_schedule_r.main(args)

    # The command-line EIN joined the list; the listed one stayed.
    assert fetch_schedule_r.read_eins(eins_file) == [BSW, "820000001"]

    assert f"{irs.EFILE}/2024/index_2024.csv" in downloads
    assert fetched == [("2025_TEOS_XML_05a", OBJECT_ID)]  # only the 990
    assert (tmp_path / "irs990" / "xml" / f"{OBJECT_ID}.xml").exists()

    # A rerun, with no EINs given, uses the list, the stored indexes, and return.
    downloads.clear()
    fetched.clear()
    fetch_schedule_r.main(["--data", str(tmp_path), "--eins-file", str(eins_file)])
    assert downloads == [] and fetched == []

    build_db.build(tmp_path, tmp_path / "dat.sqlite")
    connection = sqlite3.connect(tmp_path / "dat.sqlite")
    returns = connection.execute(
        "SELECT ein, tax_year, form, has_schedule_r FROM form990_returns ORDER BY ein"
    ).fetchall()
    assert returns == [(BSW, 2023, "990", "Y"), ("820000001", 2024, "990PF", "")]
    rows = connection.execute(
        "SELECT filer_ein, tax_year, part, ein FROM schedule_r ORDER BY rowid"
    ).fetchall()
    assert [r[2] for r in rows] == ["I", "II", "III", "IV"]
    assert rows[1] == (BSW, 2023, "II", "203749695")
    sources = dict(connection.execute("SELECT table_name, source FROM snapshot"))
    assert sources == {"form990_returns": "irs", "schedule_r": "irs"}


def test_a_failed_refresh_keeps_the_index_already_downloaded(tmp_path, monkeypatch):
    """MR !10 review, finding 4. Fails on the branch as reviewed: the year is
    dropped, and main() then rewrites both CSVs without its returns."""
    from fastmcp.exceptions import ToolError

    stored = tmp_path / f"index_{irs.FIRST_INDEX_YEAR}.csv"
    write_index(stored, INDEX)

    def unreachable(url, path):
        raise ToolError("IRS returned HTTP 503.")

    monkeypatch.setattr(irs, "download", unreachable)

    assert fetch_schedule_r.indexes(tmp_path, refresh=True) == [stored]
