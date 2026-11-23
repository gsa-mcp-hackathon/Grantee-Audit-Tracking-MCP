"""Tools, one per file. Each module exposes register(mcp); call it below."""

from dat_mcp.tools import (
    fac_fetch_audit_report,
    fac_find_linked_identifiers,
    fac_get_audit_pages,
    fac_get_awards,
    fac_get_sefa_notes,
    fac_search_audit_text,
    fac_search_audits,
    fac_search_covered_entities,
    fac_search_sefa_notes,
    grantee_audit_coverage,
    grantee_identity_resolution,
    grantee_show_network,
    grantee_show_profile,
    irs_get_related_orgs,
    irs_search_exempt_orgs,
    sam_get_registration,
    sam_search_entities,
    usaspending_get_award_funding,
    usaspending_search_awards,
    usaspending_sum_outlays,
)


def register_tools(mcp):
    grantee_identity_resolution.register(mcp)
    grantee_audit_coverage.register(mcp)
    grantee_show_profile.register(mcp)
    grantee_show_network.register(mcp)
    fac_search_audits.register(mcp)
    fac_search_covered_entities.register(mcp)
    fac_get_awards.register(mcp)
    fac_get_sefa_notes.register(mcp)
    fac_find_linked_identifiers.register(mcp)
    fac_search_sefa_notes.register(mcp)
    fac_search_audit_text.register(mcp)
    fac_get_audit_pages.register(mcp)
    fac_fetch_audit_report.register(mcp)
    sam_get_registration.register(mcp)
    sam_search_entities.register(mcp)
    irs_search_exempt_orgs.register(mcp)
    irs_get_related_orgs.register(mcp)
    usaspending_search_awards.register(mcp)
    usaspending_get_award_funding.register(mcp)
    usaspending_sum_outlays.register(mcp)
