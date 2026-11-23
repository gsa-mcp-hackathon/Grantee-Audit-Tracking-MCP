"""Convert the IRS's automatic revocation list into a CSV for the database build.

    python scripts/convert_revocations.py   # data/data-download-revocation.zip
    python scripts/convert_revocations.py --data other/

Download data-download-revocation.zip from
https://apps.irs.gov/pub/epostcard/data-download-revocation.zip into the data
directory (or unzip it there; the .txt is read too). It holds one
pipe-delimited row per revocation, with no header row and dates like
15-FEB-2022. This writes irs_revocations.csv, with a header and ISO dates,
which scripts/build_db.py loads; run this first, then rebuild.
"""

import argparse
import csv
import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

from dat_mcp import db

SOURCE = "data-download-revocation"
# The IRS's field order (TEOS data dictionary).
COLUMNS = [
    "ein",
    "name",
    "secondary_name",
    "street",
    "city",
    "state",
    "zip",
    "country",
    "subsection",
    "revocation_date",
    "revocation_posting_date",
    "reinstatement_date",
]
DATES = ("revocation_date", "revocation_posting_date", "reinstatement_date")


MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")  # fmt: skip


def source(data_dir):
    """The download: the unzipped .txt if it's there, else the zip."""
    text = Path(data_dir) / f"{SOURCE}.txt"
    return text if text.exists() else text.with_suffix(".zip")


def lines(data_dir):
    """The revocation list's text lines, from the zip or the unzipped .txt.
    A byte that isn't UTF-8 is replaced, not fatal."""
    path = source(data_dir)
    if path.suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    with zipfile.ZipFile(path) as archive:
        with archive.open(f"{SOURCE}.txt") as member:
            text = io.TextIOWrapper(member, encoding="utf-8", errors="replace")
            return text.read().splitlines()


def iso(date):
    """15-FEB-2022 as 2022-02-15; blank stays blank. Month names are read
    from MONTHS, not the computer's locale; anything else is a ValueError."""
    date = date.strip()
    if not date:
        return ""
    day, month, year = date.split("-")
    month = MONTHS.index(month.upper()) + 1
    return datetime(int(year), month, int(day)).date().isoformat()


def convert(data_dir):
    data_dir = Path(data_dir)
    out = data_dir / "irs_revocations.csv"
    rows = skipped = 0
    with open(out, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        for line in lines(data_dir):
            if not line.strip():
                continue  # the file starts with blank lines
            fields = [field.strip() for field in line.split("|")]
            if len(fields) != len(COLUMNS):
                skipped += 1
                print(f"skip a row with {len(fields)} fields: {line[:60]}")
                continue
            record = dict(zip(COLUMNS, fields))
            try:
                for name in DATES:
                    record[name] = iso(record[name])
            except ValueError:
                skipped += 1
                print(f"skip a row with an unreadable date: {line[:60]}")
                continue
            writer.writerow(record[c] for c in COLUMNS)
            rows += 1
    # build_db.py reports a file's date as when the IRS data was downloaded.
    downloaded = source(data_dir).stat().st_mtime
    os.utime(out, (downloaded, downloaded))
    print(f"wrote {out}: {rows:,} revocations, {skipped} rows skipped")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--data", default=db.data_dir(), help="directory with the download"
    )
    convert(parser.parse_args(argv).data)


if __name__ == "__main__":
    main()
