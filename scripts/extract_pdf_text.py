"""Extract the text of audit PDFs, one row per page, for the database build.

    python scripts/extract_pdf_text.py   # data/pdfs/*.pdf -> data/pdf_pages.csv
    python scripts/extract_pdf_text.py --data other/

PDFs are named by report ID (2025-06-GSAFAC-0000405570.pdf), downloaded from
https://app.fac.gov/dissemination/report/pdf/{report_id}. The CSV is loaded
into pdf_pages by scripts/build_db.py; run this first, then rebuild. Pages
with no text layer (scans) come out empty and are counted; there is no OCR.
"""

import argparse
import csv
from pathlib import Path

from pypdf.errors import PyPdfError

from dat_mcp import db, fac, pdftext


def extract(data_dir):
    data_dir = Path(data_dir)
    out = data_dir / "pdf_pages.csv"
    reports = pages = empty = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["report_id", "page", "page_count", "text"])
        for pdf in sorted((data_dir / "pdfs").glob("*.pdf")):
            if not fac.REPORT_ID.fullmatch(pdf.stem):
                print(f"skip {pdf.name}: not named by report ID")
                continue
            try:
                page_texts = pdftext.pages(pdf)
            except PyPdfError as error:
                print(f"skip {pdf.name}: unreadable PDF ({error})")
                continue
            count = len(page_texts)
            for number, text in page_texts:
                empty += not text
                writer.writerow([pdf.stem, number, count, text])
            reports += 1
            pages += count
            print(f"{pdf.stem}: {count} pages")
    print(f"wrote {out}: {reports} reports, {pages} pages, {empty} without text")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--data", default=db.data_dir(), help="directory with pdfs/ (the database's)"
    )
    extract(parser.parse_args(argv).data)


if __name__ == "__main__":
    main()
