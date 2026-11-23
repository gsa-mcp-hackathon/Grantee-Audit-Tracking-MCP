"""fac_get_audit_pages: read pages of a stored audit report PDF."""

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac
from dat_mcp.sourcetext import SOURCE_NOTICE

MAX_PAGES = 5

CAVEATS = [
    "Text comes from the PDF's text layer; tables may read out of order and "
    "scanned pages are empty. Cite the page number with the report.",
]


def fac_get_audit_pages(report_id: str, pages: list[int]):
    """Read the full text of specific pages of an audit report PDF.

    Use this after fac_search_audit_text to read the passage in context, for
    example the note listing every entity a consolidated audit includes.
    Returns {"report_id", "page_count", "source_notice", "pages", "source_url",
    "caveats"}; each page has its number and text. Up to 5 pages per call.

    Args:
        report_id: FAC report ID, e.g. 2024-06-GSAFAC-0000123456.
        pages: Page numbers to read, e.g. [45, 46].
    """
    report_id = fac.report_id(report_id)
    if not pages or len(pages) > MAX_PAGES:
        raise ToolError(f"Give 1 to {MAX_PAGES} page numbers, e.g. [45, 46].")

    with db.connect() as connection:
        rows = connection.execute(
            "SELECT page, page_count, text FROM pdf_pages WHERE report_id = ?"
            f" AND page IN ({','.join('?' * len(pages))}) ORDER BY page",
            [report_id, *pages],
        ).fetchall()
        if not rows:
            stored = connection.execute(
                "SELECT page_count FROM pdf_pages WHERE report_id = ? LIMIT 1",
                [report_id],
            ).fetchone()
            if stored is None:
                raise ToolError(
                    f"The PDF for {report_id} isn't in the local database; "
                    f"it's at {fac.pdf_url(report_id)}."
                )
            raise ToolError(
                f"{report_id} has {stored[0]} pages; none of {pages} exist."
            )
        snapshot = db.snapshot_caveat(connection)

    return {
        "report_id": report_id,
        "page_count": rows[0]["page_count"],
        "source_notice": SOURCE_NOTICE,
        "pages": [{"page": r["page"], "text": r["text"]} for r in rows],
        "source_url": fac.pdf_url(report_id),
        "caveats": [*CAVEATS, snapshot],
    }


def register(mcp):
    mcp.tool(
        fac_get_audit_pages,
        name="fac_get_audit_pages",
        annotations={
            "title": "Read pages of an audit report PDF",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
