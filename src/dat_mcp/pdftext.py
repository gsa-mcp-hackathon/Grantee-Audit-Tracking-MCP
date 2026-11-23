"""Text of audit report PDFs, one string per page, for the database.

Used by scripts/extract_pdf_text.py at build time and by the fetch tool at
run time. Pages with no text layer (scans) come out empty; there is no OCR.
"""

from pypdf import PdfReader


def pages(source):
    """[(page_number, text), ...] for a PDF path or file object, 1-based."""
    reader = PdfReader(source)
    return [
        (number, (page.extract_text() or "").strip())
        for number, page in enumerate(reader.pages, start=1)
    ]


def store(connection, report_id, page_texts):
    """Insert a report's pages into pdf_pages and its FTS index (not stored before)."""
    count = len(page_texts)
    connection.executemany(
        "INSERT INTO pdf_pages (report_id, page, page_count, text) VALUES (?, ?, ?, ?)",
        [(report_id, n, count, text) for n, text in page_texts],
    )
    # An external-content FTS table indexes only what it's told about.
    connection.execute(
        "INSERT INTO pdf_pages_fts (rowid, text) SELECT rowid, text FROM pdf_pages"
        " WHERE report_id = ?",
        [report_id],
    )
    connection.commit()
