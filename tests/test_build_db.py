"""Tests for scripts/build_db.py and dat_mcp.db, on fixture CSVs."""

import csv
import sqlite3

import pytest
from conftest import build_db, schema_columns, write_csv
from fastmcp.exceptions import ToolError

from dat_mcp import db

GENERAL = [
    {
        "report_id": "2025-06-GSAFAC-0000000001",
        "auditee_uei": "JQNLHK8YLTU6",
        "auditee_ein": "953246624",
        "audit_year": "2025",
        "auditee_name": "INLAND BEHAVIORAL",
    },
    {
        "report_id": "2024-06-GSAFAC-0000000002",
        "auditee_uei": "JQNLHK8YLTUB",
        "auditee_ein": "953246624",
        "audit_year": "2024",
        "auditee_name": "INLAND BEHAVIORAL",
    },
]
NOTES = [
    {
        "report_id": "2025-06-GSAFAC-0000000001",
        "title": "Basis",
        "content": "The SEFA includes Round Rock Medical Center.",
    }
]


def test_build_loads_declared_columns_and_records_snapshot(tmp_path):
    write_csv(tmp_path / "general.csv", "general", GENERAL, extra=["auditee_email"])
    write_csv(tmp_path / "notes_to_sefa.csv", "notes_to_sefa", NOTES)
    database = tmp_path / "dat.sqlite"

    build_db.build(tmp_path, database)

    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT auditee_name FROM general WHERE auditee_ein = '953246624'"
        " ORDER BY audit_year DESC"
    ).fetchone()
    assert row == ("INLAND BEHAVIORAL",)
    # An exported column the schema doesn't declare is left out.
    assert "auditee_email" not in schema_columns("general")
    # Full-text search over SEFA notes.
    hit = connection.execute(
        "SELECT report_id FROM notes_to_sefa WHERE rowid IN (SELECT rowid"
        " FROM notes_to_sefa_fts WHERE notes_to_sefa_fts MATCH 'round rock')"
    ).fetchone()
    assert hit == ("2025-06-GSAFAC-0000000001",)
    # Tables without a CSV exist but are empty and not in the snapshot.
    assert connection.execute("SELECT count(*) FROM passthrough").fetchone() == (0,)
    loaded = {r[0]: r for r in connection.execute("SELECT * FROM snapshot")}
    assert set(loaded) == {"general", "notes_to_sefa"}
    assert loaded["general"][3] == 2  # row_count
    assert not (tmp_path / "dat.building").exists()


def test_build_stops_at_a_short_row_and_names_the_line(tmp_path):
    write_csv(tmp_path / "general.csv", "general", GENERAL)
    with open(tmp_path / "general.csv", "a", newline="", encoding="utf-8") as f:
        f.write("2023-06-GSAFAC-0000000003,JQNLHK8YLTU6\n")
    with pytest.raises(SystemExit, match="general.csv line 4: 2 fields"):
        build_db.build(tmp_path, tmp_path / "dat.sqlite")


def test_build_stops_when_a_declared_column_is_missing(tmp_path):
    with open(tmp_path / "general.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([["report_id"], ["2025-06-GSAFAC-0000000001"]])
    with pytest.raises(SystemExit, match="auditee_uei"):
        build_db.build(tmp_path, tmp_path / "dat.sqlite")


def test_db_connect_is_read_only_and_reports_snapshot(database):
    database({"general": GENERAL})

    with db.connect() as connection:
        assert connection.execute("SELECT count(*) FROM general").fetchone()[0] == 2
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM general")
        assert db.snapshot(connection)[0]["table_name"] == "general"
        assert "downloaded" in db.snapshot_caveat(connection)
    with pytest.raises(sqlite3.ProgrammingError):  # closed on exit
        connection.execute("SELECT 1")


def test_snapshot_caveat_reports_only_its_own_sources_dates(database):
    """IRS files loaded alone: FAC tools mustn't report the IRS download date."""
    database({"eo_bmf": [{"EIN": "481150706", "GROUP": "0000"}]})

    with db.connect() as connection:
        assert connection.execute('SELECT "GROUP" FROM eo_bmf').fetchone()[0] == "0000"
        assert db.snapshot(connection)[0]["source"] == "irs"
        assert "FAC bulk data downloaded an unknown date" in db.snapshot_caveat(
            connection
        )
        assert "IRS files downloaded 20" in db.snapshot_caveat(connection, "irs")


def test_db_connect_to_an_old_database_says_to_rebuild(tmp_path, monkeypatch):
    """A database built before snapshot.source and the IRS tables existed."""
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute(
        "CREATE TABLE snapshot (table_name TEXT PRIMARY KEY, source_file TEXT,"
        " source_date TEXT, row_count INTEGER, loaded_at TEXT)"
    )
    old.close()
    monkeypatch.setattr(db.settings, "dat_db_path", str(path))

    with pytest.raises(ToolError, match="predates.*build_db"), db.connect():
        pass


def test_db_connect_without_database_explains_how_to_build(tmp_path, monkeypatch):
    monkeypatch.setattr(db.settings, "dat_db_path", str(tmp_path / "missing.sqlite"))
    with pytest.raises(ToolError, match="build_db"), db.connect():
        pass
