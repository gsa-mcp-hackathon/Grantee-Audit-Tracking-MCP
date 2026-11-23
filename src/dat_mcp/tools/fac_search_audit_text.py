"""fac_search_audit_text: full-text search of stored audit report PDFs by page."""

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac
from dat_mcp.sourcetext import SOURCE_NOTICE, fts_query

CAVEATS = [
    "Only audit reports whose PDFs have been downloaded are searched; the "
    "response says how many. An audit missing here may still name the "
    "organization. Read the whole page with fac_get_audit_pages before "
    "concluding what the passage means.",
    "Text comes from the PDF's text layer. Scanned pages have no text and can't "
    "match; tables may read out of order.",
]


def fac_search_audit_text(
    text: str,
    report_id: str | None = None,
    limit: int = 20,
):
    """Search the text of audit report PDFs for an organization or phrase, by page.

    Use this when an organization has no UEI or EIN to search by, or when an
    audit's structured data (covered UEIs and EINs, SEFA notes) doesn't say
    whether it covers an affiliate: the report's financial statement notes
    can list the entities consolidated, as "controlled affiliates", "member
    organizations", or "component units". Every word must appear on
    the same page. Returns {"count", "indexed_reports", "source_notice",
    "passages", "caveats"}; each passage has the report_id, page number, the
    auditee that filed the report, a snippet with matched words marked [like
    this], and a source_url for the PDF.

    Args:
        text: Words to find, e.g. "Valley Clinic". Quote a phrase to match it
            exactly: '"member organizations"'.
        report_id: Search one report only, e.g. 2024-06-GSAFAC-0000123456.
        limit: Most passages to return, 1 to 100.
    """
    query = fts_query(text)
    if report_id:
        report_id = fac.report_id(report_id)
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100.")

    sql = (
        "SELECT p.report_id, p.page, p.page_count, g.auditee_name, g.audit_year,"
        " snippet(pdf_pages_fts, 0, '[', ']', '...', 48) AS snippet"
        " FROM pdf_pages_fts"
        " JOIN pdf_pages p ON p.rowid = pdf_pages_fts.rowid"
        " LEFT JOIN general g ON g.report_id = p.report_id"
        " WHERE pdf_pages_fts MATCH ?"
    )
    params = [query]
    if report_id:
        sql += " AND p.report_id = ?"
        params.append(report_id)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with db.connect() as connection:
        rows = connection.execute(sql, params).fetchall()
        indexed = connection.execute(
            "SELECT count(DISTINCT report_id) FROM pdf_pages"
        ).fetchone()[0]
        snapshot = db.snapshot_caveat(connection)

    return {
        "count": len(rows),
        "indexed_reports": indexed,
        "source_notice": SOURCE_NOTICE,
        "passages": [
            {
                "report_id": r["report_id"],
                "page": r["page"],
                "page_count": r["page_count"],
                "auditee_name": r["auditee_name"],
                "audit_year": r["audit_year"],
                "snippet": r["snippet"],
                "source_url": fac.pdf_url(r["report_id"]),
            }
            for r in rows
        ],
        "caveats": [*CAVEATS, snapshot],
    }


def register(mcp):
    mcp.tool(
        fac_search_audit_text,
        name="fac_search_audit_text",
        annotations={
            "title": "Search audit report PDFs",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
