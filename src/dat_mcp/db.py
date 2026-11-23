"""Read-only access to the local SQLite database built by scripts/build_db.py.

The database mirrors FAC's bulk CSV tables and the IRS's exempt-organization
files (each with its source's table and column names) and adds a `snapshot`
table recording when each source file was downloaded and loaded, and which
source it belongs to. It exists for searches the live APIs can't do
(near-duplicate identifiers, text search, IRS data that has no API) and for
evals pinned to a dated snapshot. Tools that read it should say so, and
report the snapshot date, in their caveats.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastmcp.exceptions import ToolError

from dat_mcp.config import settings


@contextmanager
def connect(writable=False):
    """Open the database for a `with` block, read-only unless a tool stores data.

    Closing on exit matters: an open handle keeps scripts/build_db.py from
    replacing the file (Windows refuses the rename).
    """
    path = Path(settings.dat_db_path).resolve()
    if not path.is_file():
        raise ToolError(
            "The local database is not built. Run: python scripts/build_db.py"
        )
    mode = "rw" if writable else "ro"
    connection = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        # Databases built before the IRS tables lack snapshot.source; say so
        # rather than fail later on a missing table or column.
        columns = {r["name"] for r in connection.execute("PRAGMA table_info(snapshot)")}
        if "source" not in columns:
            raise ToolError(
                "The local database predates the current schema. Rebuild it:"
                " python scripts/build_db.py"
            )
        yield connection
    finally:
        connection.close()


def data_dir():
    """The directory holding the database and downloaded files (data/pdfs)."""
    return Path(settings.dat_db_path).resolve().parent


SNAPSHOT_CAVEATS = {  # by source: the schema file a table is declared in
    "fac": "Results come from a local copy of FAC bulk data downloaded {when};"
    " audits filed or resubmitted since then are not included.",
    "irs": "Results come from a local copy of IRS files downloaded {when};"
    " exemptions granted or revoked, and returns filed, since then are not"
    " included.",
}


def snapshot(connection):
    """One row per loaded table: source file, its date, row count, load time,
    and source (fac, irs)."""
    rows = connection.execute(
        "SELECT table_name, source_file, source_date, row_count, loaded_at, source"
        " FROM snapshot ORDER BY table_name"
    ).fetchall()
    return [dict(row) for row in rows]


def snapshot_caveat(connection, source="fac"):
    """Caveat text naming when one source's files were downloaded."""
    dates = {r["source_date"] for r in snapshot(connection) if r["source"] == source}
    when = ", ".join(sorted(dates)) or "an unknown date"
    return SNAPSHOT_CAVEATS[source].format(when=when)
