"""fac_search_sefa_notes: full-text search across every audit's SEFA notes."""

from fastmcp.exceptions import ToolError

from dat_mcp import db, fac, identifiers
from dat_mcp.sourcetext import SOURCE_NOTICE, fts_query

CAVEATS = [
    "A note naming an organization may include it or exclude it (audited "
    "separately). Read the passage, then fac_get_sefa_notes for the full note.",
    "Only notes filed as structured data are searched. Notes can leave out "
    "entities the audit PDF names.",
]


def fac_search_sefa_notes(
    text: str,
    state: str | None = None,
    audit_year: int | None = None,
    limit: int = 20,
):
    """Search the text of all FAC audits' SEFA notes for an organization or phrase.

    Use this when an organization has no UEI or EIN to search by, to find
    audits whose notes name it: a subsidiary covered by a parent's consolidated
    audit, an affiliate excluded because it's audited separately, a component
    unit. Every word must appear in the same note. Returns {"count",
    "source_notice", "passages", "caveats"}; each passage has the report_id,
    audit year, auditee that filed it, note title, a snippet with the matched
    words marked [like this], and a source_url.

    Args:
        text: Words to find, e.g. "Valley Clinic". Quote a phrase to match it
            exactly: '"Valley Community Clinic"'.
        state: Two-letter state of the auditee that filed the note, e.g. TX.
        audit_year: Year the auditee's fiscal year ends, e.g. 2025.
        limit: Most passages to return, 1 to 100.
    """
    query = fts_query(text)
    if state:
        state = identifiers.state(state)
    if audit_year:
        audit_year = str(identifiers.audit_year(audit_year))
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100.")

    sql = (
        "SELECT n.report_id, g.audit_year, g.auditee_name, g.auditee_uei,"
        " g.auditee_state, n.title,"
        " snippet(notes_to_sefa_fts, -1, '[', ']', '...', 40) AS snippet"
        " FROM notes_to_sefa_fts"
        " JOIN notes_to_sefa n ON n.rowid = notes_to_sefa_fts.rowid"
        " LEFT JOIN general g ON g.report_id = n.report_id"
        " WHERE notes_to_sefa_fts MATCH ?"
    )
    params = [query]
    if state:
        sql += " AND g.auditee_state = ?"
        params.append(state)
    if audit_year:
        sql += " AND g.audit_year = ?"
        params.append(audit_year)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    with db.connect() as connection:
        rows = connection.execute(sql, params).fetchall()
        snapshot = db.snapshot_caveat(connection)

    passages = [
        {
            "report_id": r["report_id"],
            "audit_year": r["audit_year"],
            "auditee_name": r["auditee_name"],
            "auditee_uei": r["auditee_uei"],
            "auditee_state": r["auditee_state"],
            "note_title": r["title"],
            "snippet": r["snippet"],
            "source_url": fac.summary_url(r["report_id"]),
        }
        for r in rows
    ]
    return {
        "count": len(passages),
        "source_notice": SOURCE_NOTICE,
        "passages": passages,
        "caveats": [*CAVEATS, snapshot],
    }


def register(mcp):
    mcp.tool(
        fac_search_sefa_notes,
        name="fac_search_sefa_notes",
        annotations={
            "title": "Search all audits' SEFA notes",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
