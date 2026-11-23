"""Tests for fac_fetch_audit_report. FAC's download is replaced by a fake PDF."""

import pytest
from fastmcp.exceptions import ToolError

from dat_mcp import db, fac
from dat_mcp.tools.fac_fetch_audit_report import fac_fetch_audit_report
from dat_mcp.tools.fac_search_audit_text import fac_search_audit_text

REPORT = "2025-06-GSAFAC-0000405570"


def tiny_pdf(*page_texts):
    """A minimal PDF with one line of Helvetica text per page."""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_ids = []
    for text in page_texts:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        )
        page_ids.append(len(objects))
    kids = " ".join(f"{i} 0 R" for i in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}"
    out += f"{trailer}\n%%EOF\n".encode()
    return out


@pytest.fixture
def fac_db(database, monkeypatch):
    database(
        {
            "general": [
                {"report_id": REPORT, "auditee_name": "BSW", "audit_year": "2025"}
            ]
        }
    )
    downloads = []

    def fake_download(report_id):
        downloads.append(report_id)
        return tiny_pdf(
            "Cover", "Affiliates include Scott and White Hospital Round Rock"
        )

    monkeypatch.setattr(fac, "download_pdf", fake_download)
    return downloads


def test_fetch_stores_pages_and_makes_them_searchable(fac_db):
    result = fac_fetch_audit_report(REPORT.lower())

    assert fac_db == [REPORT]
    assert (result["status"], result["page_count"], result["pages_without_text"]) == (
        "fetched",
        2,
        0,
    )
    assert result["source_url"].endswith(REPORT)
    assert (db.data_dir() / "pdfs" / f"{REPORT}.pdf").read_bytes().startswith(b"%PDF")
    [passage] = fac_search_audit_text("round rock")["passages"]
    assert (passage["report_id"], passage["page"], passage["auditee_name"]) == (
        REPORT,
        2,
        "BSW",
    )


def test_fetch_does_not_download_a_stored_report_again(fac_db):
    fac_fetch_audit_report(REPORT)
    result = fac_fetch_audit_report(REPORT)
    assert result["status"] == "already stored"
    assert fac_db == [REPORT]


def test_fetch_passes_on_facs_not_found_error(fac_db, monkeypatch):
    def missing(report_id):
        raise ToolError("FAC has no public PDF")

    monkeypatch.setattr(fac, "download_pdf", missing)
    with pytest.raises(ToolError, match="no public PDF"):
        fac_fetch_audit_report("2025-06-GSAFAC-9999999999")
    assert fac_search_audit_text("cover")["indexed_reports"] == 0
