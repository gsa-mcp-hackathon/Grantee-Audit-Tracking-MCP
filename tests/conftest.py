"""Shared fixtures: a small local database built from fixture rows."""

import csv
import re
import sqlite3
import sys
from pathlib import Path

import pytest

from dat_mcp import db

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_db  # noqa: E402


def schema_columns(table):
    """Column names a schema table declares, read from the schema SQL."""
    connection = sqlite3.connect(":memory:")
    for schema in build_db.SCHEMA_DIR.glob("*.sql"):
        connection.executescript(schema.read_text(encoding="utf-8"))
    return [r[1] for r in connection.execute(f"PRAGMA table_info({table})")]


def csv_name(table):
    """A file name the table's '-- load:' glob matches, e.g. eo1.csv for eo_bmf."""
    for schema in build_db.SCHEMA_DIR.glob("*.sql"):
        for pattern, name in build_db.loads(schema.read_text(encoding="utf-8")):
            if name == table:
                # A [1-4] character class becomes its first character.
                return re.sub(r"\[(.)[^]]*\]", lambda m: m[1], pattern)
    raise KeyError(f"no '-- load:' line for {table}")


def write_csv(path, table, rows, extra=()):
    """A CSV with every column the table declares, plus extras, as FAC exports."""
    header = schema_columns(table) + list(extra)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def database(tmp_path, monkeypatch):
    """Build a database from {table: rows} and point settings at it."""

    def build(tables):
        for table, rows in tables.items():
            write_csv(tmp_path / csv_name(table), table, rows)
        path = tmp_path / "dat.sqlite"
        build_db.build(tmp_path, path)
        monkeypatch.setattr(db.settings, "dat_db_path", str(path))
        return path

    return build
