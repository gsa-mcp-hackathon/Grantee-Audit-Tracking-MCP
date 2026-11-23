"""grantee_show_profile: one grantee's identity and audit coverage as an MCP App."""

import json
from pathlib import Path

from fastmcp.tools import ToolResult

from dat_mcp.tools.grantee_audit_coverage import grantee_audit_coverage
from dat_mcp.tools.grantee_identity_resolution import grantee_identity_resolution

UI_URI = "ui://dat/grantee-profile/v2.html"
UI_PATH = Path(__file__).resolve().parents[1] / "ui" / "grantee_profile.html"


def grantee_show_profile(
    uei: str | list[str] | None = None,
    ein: str | list[str] | None = None,
    name: str | None = None,
    state: str | None = None,
) -> ToolResult:
    """Show one grantee's identity and Single Audit coverage as an interactive view.

    Use this when the user asks to see or show a grantee's profile, timeline,
    or audit history. It runs grantee_identity_resolution and
    grantee_audit_coverage and displays both in clients that support MCP
    Apps, adding nothing of its own. To answer a question in text, call those
    two tools instead.

    Returns {"identity", "coverage"}: the two tools' complete results. When a
    name matches more than one organization it returns {"candidates"}
    instead; call again with one of their identifiers.

    Args:
        uei: 12-character UEI, e.g. A1B2C3D4E5F6, or a list of them.
        ein: 9-digit EIN, e.g. 112223333, or a list of them.
        name: Part of the auditee's name, used when no identifier is known.
        state: 2-letter state code to narrow a name search, e.g. CA.
    """
    # Identity first: coverage resolves the same grantee from its cache.
    identity = grantee_identity_resolution(uei=uei, ein=ein, name=name, state=state)
    if "candidates" in identity:
        return ToolResult(
            content=json.dumps(identity, ensure_ascii=False),
            structured_content=identity,
        )
    coverage = grantee_audit_coverage(uei=uei, ein=ein, name=name, state=state)
    return ToolResult(
        content=_summary(coverage),
        structured_content={"identity": identity, "coverage": coverage},
    )


def _summary(coverage):
    """The coverage rows as text, for the model and for clients with no view."""
    lines = ["Fiscal-year rows shown (grantee_audit_coverage's years):"]
    for row in coverage.get("years", []):
        line = f"- {row['audit_year']}: {row['route']}"
        if row.get("fac_accepted_date"):
            line += f", accepted {row['fac_accepted_date']}"
            if row.get("days_late") is not None:
                line += f", {row['days_late']} days after due date {row['due_date']}"
        lines.append(f"{line}. {row.get('source_url', '')}".rstrip())
    lines += ["", "Open questions:"]
    lines += [f"- {q}" for q in coverage.get("open_questions", [])] or ["- none"]
    lines += ["", "Caveats:"]
    lines += [f"- {c}" for c in coverage.get("caveats", [])]
    lines.append(
        "The identifiers, SAM.gov and IRS records, notes, and leads are in "
        "structured content, as grantee_identity_resolution returns them."
    )
    return "\n".join(lines)


def register(mcp):
    @mcp.resource(
        UI_URI,
        name="grantee_profile",
        title="Grantee identity and audit coverage",
        mime_type="text/html;profile=mcp-app",
        meta={
            "ui": {
                "prefersBorder": True,
                # The view loads nothing and calls nothing over the network.
                "csp": {"connectDomains": [], "resourceDomains": []},
            }
        },
    )
    def grantee_profile():
        return UI_PATH.read_text(encoding="utf-8")

    mcp.tool(
        grantee_show_profile,
        name="grantee_show_profile",
        annotations={
            "title": "Show a grantee's identity and audit coverage",
            "readOnlyHint": True,
            "openWorldHint": True,
        },
        meta={"ui": {"resourceUri": UI_URI}},
    )
