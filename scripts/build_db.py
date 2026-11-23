"""Build the local SQLite database from bulk CSV downloads.

    python scripts/build_db.py                 # data/*.csv -> data/dat.sqlite
    python scripts/build_db.py --data other/   # CSVs elsewhere
    python scripts/build_db.py --derived       # refresh derived tables only

The database goes where the server reads it: DAT_DB_PATH in .env, or
data/dat.sqlite under the repo. CSVs are read from that directory.

The schema is the SQL in scripts/schema/, one file per source (fac.sql,
irs.sql, ...), and the snapshot table records each table's source by file
name. Each "-- load: <csv glob>" comment names the CSVs that fill the CREATE
TABLE after it, matched by column name; CSV columns the table doesn't declare
are ignored, and a declared column missing from the CSV is an error.
A CSV that isn't downloaded is skipped and its table stays empty.

fac_filers is derived from general after loading (see derive); --derived
refills it in an existing database without reloading the CSVs.

To add a table: write its CREATE TABLE (and indexes or FTS5 table) in the
source's .sql file with a "-- load:" line, drop the CSV in data/, rebuild.
The build replaces the database file, so a schema change is a rebuild, never
a migration. Share this script and the schema, not the file.
"""

import argparse
import csv
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dat_mcp import names
from dat_mcp.config import settings

csv.field_size_limit(2**31 - 1)  # notes and finding text can run long

SCHEMA_DIR = Path(__file__).parent / "schema"
LOAD = re.compile(r"--\s*load:\s*(\S+)\s*\n\s*CREATE TABLE (\w+)", re.IGNORECASE)
BATCH = 50_000


def loads(schema_sql):
    """(csv glob, table) pairs declared by '-- load:' comments in schema SQL."""
    return LOAD.findall(schema_sql)


def split_schema(schema_sql):
    """(table statements, index and FTS statements): the latter run after loading."""
    bare = re.sub(r"--.*", "", schema_sql)  # comments may contain semicolons
    statements = [s.strip() for s in bare.split(";") if s.strip()]
    after = re.compile(
        r"^\s*CREATE (INDEX|VIRTUAL TABLE)", re.IGNORECASE | re.MULTILINE
    )
    tables = [s for s in statements if not after.search(s)]
    indexes = [s for s in statements if after.search(s)]
    return ";\n".join(tables) + ";", ";\n".join(indexes) + ";"


def load_csv(connection, table, path):
    """Insert a CSV's rows into `table`, matching columns by name."""
    columns = [r[1] for r in connection.execute(f"PRAGMA table_info({table})")]
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        missing = [c for c in columns if c not in header]
        if missing:
            sys.exit(f"{path.name} lacks columns {table} declares: {missing}")
        positions = [header.index(c) for c in columns]
        width = len(header)
        # Quoted: some sources' column names are SQL keywords (IRS's GROUP).
        names = ", ".join(f'"{c}"' for c in columns)
        marks = ", ".join("?" * len(columns))
        insert = f"INSERT INTO {table} ({names}) VALUES ({marks})"
        count = 0
        batch = []
        for row in reader:
            if len(row) < width:
                sys.exit(
                    f"{path.name} line {reader.line_num}: {len(row)} fields,"
                    f" header has {width}; the file is truncated or malformed."
                )
            batch.append([row[i] for i in positions])
            if len(batch) >= BATCH:
                connection.executemany(insert, batch)
                count += len(batch)
                batch = []
        if batch:
            connection.executemany(insert, batch)
            count += len(batch)
    return count


def derive(connection):
    """Fill fac_filers from general: each name and address a filer has used.

    Runs after the CSVs load. The keys are computed in Python so the tools
    can compute the same ones for a name or address they look up.
    """
    connection.create_function("name_key", 1, names.name_key, deterministic=True)
    connection.create_function("address_key", 1, names.address_key, deterministic=True)
    connection.create_function("city_key", 1, names.city_key, deterministic=True)
    connection.execute("DELETE FROM fac_filers")
    count = connection.execute(
        "INSERT INTO fac_filers"
        " SELECT auditee_uei, auditee_ein, auditee_name, name_key(auditee_name),"
        " auditee_state, city_key(auditee_city), substr(auditee_zip, 1, 5),"
        " address_key(auditee_address_line_1), min(CAST(audit_year AS INTEGER)),"
        " max(CAST(audit_year AS INTEGER)), count(*)"
        " FROM general GROUP BY 1, 2, 3, 5, 6, 7, 8"
    ).rowcount
    connection.execute("INSERT INTO fac_filers_fts(fac_filers_fts) VALUES ('rebuild')")
    connection.commit()
    return count


def build(data_dir, db_path, schema_dir=SCHEMA_DIR):
    data_dir, db_path = Path(data_dir), Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    building = db_path.with_suffix(".building")
    building.unlink(missing_ok=True)

    connection = sqlite3.connect(building)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute(
        "CREATE TABLE snapshot (table_name TEXT PRIMARY KEY, source_file TEXT,"
        " source_date TEXT, row_count INTEGER, loaded_at TEXT, source TEXT)"
    )
    loaded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    for schema in sorted(schema_dir.glob("*.sql")):
        sql = schema.read_text(encoding="utf-8")
        tables, indexes = split_schema(sql)
        connection.executescript(tables)
        for pattern, table in loads(sql):
            paths = sorted(data_dir.glob(pattern))
            if not paths:
                print(f"skip {table}: no {data_dir / pattern}")
                continue
            started = time.monotonic()
            count = sum(load_csv(connection, table, p) for p in paths)
            newest = max(p.stat().st_mtime for p in paths)
            source_date = datetime.fromtimestamp(newest, UTC).strftime("%Y-%m-%d")
            connection.execute(
                "INSERT INTO snapshot VALUES (?, ?, ?, ?, ?, ?)",
                (table, pattern, source_date, count, loaded_at, schema.stem),
            )
            connection.commit()
            print(f"{table}: {count:,} rows in {time.monotonic() - started:.0f}s")
        connection.executescript(indexes)  # faster after the rows are in

    started = time.monotonic()
    count = derive(connection)
    print(f"fac_filers: {count:,} rows in {time.monotonic() - started:.0f}s")

    # External-content FTS5 tables index nothing until told to rebuild.
    for (name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE sql LIKE '%USING fts5%'"
    ).fetchall():
        connection.execute(f"INSERT INTO {name}({name}) VALUES ('rebuild')")
    connection.commit()
    connection.close()
    try:
        db_path.unlink(missing_ok=True)
        building.rename(db_path)
    except PermissionError:
        sys.exit(
            f"{db_path} is open (the MCP server?). The build finished; close it,"
            f" then move {building} to {db_path}."
        )
    print(f"wrote {db_path} ({db_path.stat().st_size / 1e9:.2f} GB)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    db_path = Path(settings.dat_db_path)
    parser.add_argument("--data", default=db_path.parent, help="directory of CSVs")
    parser.add_argument("--db", default=db_path, help="database to write")
    parser.add_argument(
        "--derived",
        action="store_true",
        help="refill the derived tables of an existing database",
    )
    args = parser.parse_args(argv)
    if args.derived:
        return refresh_derived(args.db)
    build(args.data, args.db)


def refresh_derived(db_path, schema_dir=SCHEMA_DIR):
    """Create fac_filers in a database built before it existed, and refill it."""
    if not Path(db_path).is_file():
        sys.exit(f"{db_path} doesn't exist; run a full build first.")
    connection = sqlite3.connect(db_path)
    sql = re.sub(r"--.*", "", (schema_dir / "fac.sql").read_text(encoding="utf-8"))
    for statement in sql.split(";"):
        if "fac_filers" in statement:
            connection.execute(
                re.sub(
                    r"CREATE (TABLE|INDEX|VIRTUAL TABLE)",
                    r"CREATE \1 IF NOT EXISTS",
                    statement,
                )
            )
    print(f"fac_filers: {derive(connection):,} rows")
    connection.close()


if __name__ == "__main__":
    main()
