"""Fetch Form 990 Schedule R for chosen EINs, for the database build.

    python scripts/fetch_schedule_r.py             # EINs in schedule_r_eins.txt
    python scripts/fetch_schedule_r.py 463131350   # add an EIN to the list, then fetch
    python scripts/fetch_schedule_r.py --refresh   # download the indexes again

Finds each EIN's returns in the IRS's 990 e-file index (one CSV per year the
IRS processed returns, from 2024, kept in data/irs990/), reads each full Form
990 out of the IRS's e-file zips, and writes form990_returns.csv and
schedule_r.csv, which scripts/build_db.py loads; run this first, then rebuild.
Return XML is kept in data/irs990/xml/, so a rerun downloads only new returns.
To cover another organization, give its EIN on the command line or add it to
schedule_r_eins.txt. EINs given on the command line are appended to the list,
so every later run, and the database built from it, keeps them.
"""

import argparse
import csv
import os
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

from fastmcp.exceptions import ToolError

from dat_mcp import db, form990, identifiers, irs

EIN_LIST = Path(__file__).parent / "schedule_r_eins.txt"
FORMS = ("990", "990EZ", "990PF")  # 990-T is filed alongside these, not instead
RETURN_COLUMNS = [
    "ein", "name", "tax_year", "tax_period_end", "form", "object_id",
    "xml_batch_id", "has_schedule_r",
]  # fmt: skip
RELATED_COLUMNS = [
    "filer_ein", "tax_year", "object_id", "part", "relationship", "name", "ein",
    "state", "primary_activity", "legal_domicile", "exempt_code_section",
    "entity_type", "direct_controlling_entity", "controlled",
]  # fmt: skip


def read_eins(path):
    """EINs from a list file: one per line, text after # is a comment."""
    eins = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        word = line.split("#", 1)[0].strip()
        if word:
            eins.append(identifiers.ein(word))
    return list(dict.fromkeys(eins))


def add_eins(path, eins):
    """Append EINs the list doesn't have yet; return the whole list."""
    listed = read_eins(path)
    new = [e for e in dict.fromkeys(eins) if e not in listed]
    if new:
        with open(path, "a", encoding="utf-8") as f:
            for ein in new:
                f.write(f"{ein}  # added from the command line\n")
        print(f"added to {Path(path).name}: {', '.join(new)}")
    return listed + new


def indexes(folder, refresh=False):
    """Every e-file index from 2024 through this year, downloading missing ones."""
    paths = []
    for year in range(irs.FIRST_INDEX_YEAR, date.today().year + 1):
        path = folder / f"index_{year}.csv"
        if refresh or not path.exists():
            print(f"downloading {path.name}")
            partial = path.with_suffix(".part")
            try:
                irs.download(irs.index_url(year), partial)
            except ToolError as error:
                if not path.exists():
                    print(f"skip {path.name}: {error}")
                    continue
                print(f"keeping the stored {path.name}: {error}")
            else:
                partial.replace(path)
        paths.append(path)
    return paths


def date_as(sources, outputs):
    """Give the CSVs the date of the newest download they were made from:
    build_db.py reports a file's date as when the IRS data was downloaded."""
    newest = max(p.stat().st_mtime for p in sources)
    for path in outputs:
        os.utime(path, (newest, newest))


def find_returns(index_paths, eins):
    """The latest filing of each form for each EIN and tax period."""
    wanted = set(eins)
    latest = {}
    for path in index_paths:
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                if row["EIN"] not in wanted or row["RETURN_TYPE"] not in FORMS:
                    continue
                key = (row["EIN"], row["TAX_PERIOD"], row["RETURN_TYPE"])
                # Object ids grow with submission, so the largest is the latest.
                if key not in latest or row["OBJECT_ID"] > latest[key]["OBJECT_ID"]:
                    latest[key] = row
    return sorted(latest.values(), key=lambda r: (r["EIN"], r["TAX_PERIOD"]))


def fetch(returns, xml_dir):
    """Save each full Form 990's XML under xml_dir, unless it's already there."""
    missing = [
        r
        for r in returns
        if r["RETURN_TYPE"] == "990"
        and not (xml_dir / f"{r['OBJECT_ID']}.xml").exists()
    ]
    if not missing:
        return
    with irs.efile_reader() as read:
        for r in missing:
            try:
                xml = read(r["XML_BATCH_ID"], r["OBJECT_ID"])
            except ToolError as error:
                print(f"skip {r['EIN']} {r['TAX_PERIOD']}: {error}")
                continue
            (xml_dir / f"{r['OBJECT_ID']}.xml").write_bytes(xml)
            print(f"{r['EIN']} {r['TAX_PERIOD']}: {len(xml):,} bytes")


def write(returns, xml_dir, data_dir):
    """Write form990_returns.csv and schedule_r.csv from the returns found."""
    written = related_count = 0
    with (
        open(data_dir / "form990_returns.csv", "w", newline="", encoding="utf-8") as r,
        open(data_dir / "schedule_r.csv", "w", newline="", encoding="utf-8") as s,
    ):
        returns_out = csv.DictWriter(r, fieldnames=RETURN_COLUMNS)
        related_out = csv.DictWriter(s, fieldnames=RELATED_COLUMNS)
        returns_out.writeheader()
        related_out.writeheader()
        for row in returns:
            record = {
                "ein": row["EIN"],
                "name": row["TAXPAYER_NAME"],
                "tax_year": tax_year(row["TAX_PERIOD"]),
                "tax_period_end": f"{row['TAX_PERIOD'][:4]}-{row['TAX_PERIOD'][4:]}",
                "form": row["RETURN_TYPE"],
                "object_id": row["OBJECT_ID"],
                "xml_batch_id": row["XML_BATCH_ID"],
                "has_schedule_r": "",
            }
            if row["RETURN_TYPE"] == "990":
                path = xml_dir / f"{row['OBJECT_ID']}.xml"
                if not path.exists():
                    continue  # its download failed; fetch said so
                try:
                    filing, related = form990.schedule_r(path.read_bytes())
                except (ET.ParseError, ValueError):
                    print(f"skip {path.name}: not a readable Form 990")
                    continue
                if filing["ein"] != row["EIN"]:
                    print(
                        f"skip {path.name}: filed by {filing['ein']}, not {row['EIN']}"
                    )
                    continue
                record["name"] = filing["name"]
                record["tax_year"] = int(filing["tax_year"] or record["tax_year"])
                record["has_schedule_r"] = "Y" if filing["has_schedule_r"] else "N"
                for item in related:
                    related_out.writerow(
                        {
                            "filer_ein": row["EIN"],
                            "tax_year": record["tax_year"],
                            "object_id": row["OBJECT_ID"],
                            **item,
                        }
                    )
                related_count += len(related)
            returns_out.writerow(record)
            written += 1
    print(
        f"wrote {data_dir / 'form990_returns.csv'} ({written} returns) and"
        f" {data_dir / 'schedule_r.csv'} ({related_count} related organizations)"
    )


def tax_year(tax_period):
    """The IRS's tax year for a period ending YYYYMM: the year it starts."""
    year, month = int(tax_period[:4]), int(tax_period[4:])
    return year if month == 12 else year - 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("eins", nargs="*", help=f"EINs to add to {EIN_LIST.name}")
    parser.add_argument(
        "--eins-file", default=EIN_LIST, help="the EIN list to read and add to"
    )
    parser.add_argument(
        "--data", default=db.data_dir(), help="data directory (the database's)"
    )
    parser.add_argument(
        "--refresh", action="store_true", help="download the e-file indexes again"
    )
    args = parser.parse_args(argv)
    eins = add_eins(args.eins_file, [identifiers.ein(e) for e in args.eins])
    data_dir = Path(args.data)
    xml_dir = data_dir / "irs990" / "xml"
    xml_dir.mkdir(parents=True, exist_ok=True)

    index_paths = indexes(xml_dir.parent, args.refresh)
    returns = find_returns(index_paths, eins)
    found = {r["EIN"] for r in returns}
    for ein in eins:
        if ein not in found:
            print(f"{ein}: no Form 990, 990-EZ, or 990-PF in the e-file indexes")
    fetch(returns, xml_dir)
    write(returns, xml_dir, data_dir)
    if index_paths:
        date_as(
            index_paths,
            [data_dir / "form990_returns.csv", data_dir / "schedule_r.csv"],
        )


if __name__ == "__main__":
    main()
