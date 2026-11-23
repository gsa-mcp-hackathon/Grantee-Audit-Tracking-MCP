"""MCP resources: reference material an analyst or agent can read or attach.

The reference resources are sections of the docs, read when asked for, so
there is one copy of the text to keep current. The snapshot resource reads
the local database's snapshot table.
"""

import json

from fastmcp.exceptions import ResourceError, ToolError

from dat_mcp import db
from dat_mcp.config import REPO

DOCS = REPO / "docs"


def section(path, heading):
    """A markdown section: the heading line up to the next heading of the same
    or a higher level, without the trailing --- divider."""
    level = len(heading) - len(heading.lstrip("#"))
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        raise ResourceError(f"{path.name} has no section {heading!r}.")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        hashes = len(lines[i]) - len(lines[i].lstrip("#"))
        if 0 < hashes <= level and lines[i][hashes:hashes + 1] == " ":
            end = i
            break
    text = "\n".join(lines[start:end]).rstrip()
    return text.removesuffix("---").rstrip() + "\n"


def audit_rules():
    """The Single Audit requirement: the expenditure threshold, the due date,
    who must file, and who never files in FAC (for-profits, federal agencies,
    bodies inside a statewide or system audit). From the project's domain
    guide."""
    return (
        section(DOCS / "domain-guide.md", "## 1. The requirement")
        + "\n"
        + section(DOCS / "scenarios.md", "### S21.")
    )


def glossary():
    """Terms in audit records and in these tools' answers: the audit and its
    findings, money (obligation, outlay, expenditure), organizations and
    roles, identifiers (UEI, EIN, ALN, report ID), and fiscal years. From the
    project's domain guide."""
    return section(DOCS / "domain-guide.md", "## 2. Vocabulary")


def data_snapshots():
    """When each local copy of FAC bulk data and IRS files was downloaded and
    loaded, with row counts. Answers from the local database don't include
    anything filed or changed after these dates; FAC API, SAM.gov, and
    USAspending lookups are live."""
    try:
        with db.connect() as connection:
            tables = db.snapshot(connection)
    except ToolError as error:
        raise ResourceError(str(error)) from error
    return json.dumps(
        {
            "tables": tables,
            "note": "source_date is when the source file was downloaded;"
            " loaded_at is when it was loaded into the local database. FAC"
            " API, SAM.gov, and USAspending lookups are live, not from these"
            " files.",
        },
        indent=2,
    )


RESOURCES = [
    ("dat://reference/audit-rules", audit_rules, "Single Audit rules", "text/markdown"),
    ("dat://reference/glossary", glossary, "Glossary", "text/markdown"),
    (
        "dat://data/snapshots",
        data_snapshots,
        "Local data snapshot dates",
        "application/json",
    ),
]


def register(mcp):
    for uri, read, title, mime_type in RESOURCES:
        mcp.resource(uri, title=title, mime_type=mime_type)(read)
