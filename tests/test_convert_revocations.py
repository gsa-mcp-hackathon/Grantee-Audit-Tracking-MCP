"""Tests for scripts/convert_revocations.py, on a fixture download."""

import csv
import zipfile

import convert_revocations

# As the IRS posts it: blank lines first, no header, pipes, 15-FEB-2022 dates.
TEXT = (
    "\n\n"
    "860215065|CHANGEPOINT INTEGRATED HEALTH ||1801 W DEUCE OF CLUBS STE 100|"
    "SHOW LOW|AZ|85901-2704|US|03|15-FEB-2022|11-MAY-2022|\n"
    "010019708|A REINSTATED CHARITY|DBA NAME|1 MAIN ST|BOSTON|MA|02101|US|03|"
    "15-MAY-2020|09-SEP-2020|01-JAN-2021\n"
    "999999999|TOO|FEW|FIELDS\n"
)


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_converts_the_zip_to_a_csv_with_iso_dates(tmp_path):
    with zipfile.ZipFile(tmp_path / "data-download-revocation.zip", "w") as z:
        z.writestr("data-download-revocation.txt", TEXT)

    convert_revocations.convert(tmp_path)

    changepoint, reinstated = read(tmp_path / "irs_revocations.csv")
    assert changepoint["ein"] == "860215065"
    assert changepoint["name"] == "CHANGEPOINT INTEGRATED HEALTH"  # trimmed
    assert changepoint["revocation_date"] == "2022-02-15"
    assert changepoint["revocation_posting_date"] == "2022-05-11"
    assert changepoint["reinstatement_date"] == ""
    assert reinstated["secondary_name"] == "DBA NAME"
    assert reinstated["reinstatement_date"] == "2021-01-01"


def test_reads_an_unzipped_download_too(tmp_path):
    (tmp_path / "data-download-revocation.txt").write_text(TEXT, encoding="utf-8")

    convert_revocations.convert(tmp_path)

    assert len(read(tmp_path / "irs_revocations.csv")) == 2  # the short row skipped


# MR !10 review. These fail on the branch as reviewed (3442bb4). No row in the
# IRS file downloaded 2026-11-10 triggers finding 8; the rows here are made up.
def test_a_row_with_an_unreadable_date_is_skipped_not_fatal(tmp_path):
    """Finding 8: a short row is skipped and counted, but a bad date raises
    and leaves a partial CSV behind."""
    bad_date = (
        "123456789|BAD DATE CHARITY||1 MAIN ST|BOSTON|MA|02101|US|03|2022-02-15||\n"
    )
    (tmp_path / "data-download-revocation.txt").write_text(
        TEXT + bad_date, encoding="utf-8"
    )

    convert_revocations.convert(tmp_path)

    assert len(read(tmp_path / "irs_revocations.csv")) == 2


def test_a_name_that_isnt_utf8_does_not_stop_the_conversion(tmp_path):
    """Finding 8: fetch_schedule_r reads the IRS index with errors="replace";
    this read is strict."""
    latin1 = TEXT.replace("A REINSTATED CHARITY", "CAFÉ CHARITY").encode("latin-1")
    (tmp_path / "data-download-revocation.txt").write_bytes(latin1)

    convert_revocations.convert(tmp_path)

    assert len(read(tmp_path / "irs_revocations.csv")) == 2


def test_the_snapshot_date_is_the_downloads_not_the_conversions(tmp_path):
    """Finding 5: the caveat says the IRS files were downloaded on the day the
    CSV was last rewritten, however old the download is."""
    import os
    import sqlite3
    from datetime import UTC, datetime

    from conftest import build_db

    download = tmp_path / "data-download-revocation.txt"
    download.write_text(TEXT, encoding="utf-8")
    downloaded = datetime(2026, 1, 15, 12, tzinfo=UTC).timestamp()
    os.utime(download, (downloaded, downloaded))

    convert_revocations.convert(tmp_path)
    build_db.build(tmp_path, tmp_path / "dat.sqlite")

    connection = sqlite3.connect(tmp_path / "dat.sqlite")
    [date] = connection.execute(
        "SELECT source_date FROM snapshot WHERE table_name = 'irs_revocations'"
    ).fetchone()
    connection.close()
    assert date == "2026-01-15"
