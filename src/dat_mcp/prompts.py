"""MCP prompts: the questions analysts ask about one grantee, as workflows.

A client shows each prompt as a command the analyst picks (in Claude Code,
/mcp__dat-mcp__check_filing). The analyst fills in the grantee, and the
prompt becomes the message sent to the agent: the question, then the tools to
call in order and what to report. Like the server instructions, a prompt asks
for evidence and never for a compliance or risk determination.
"""

import re

from fastmcp.exceptions import PromptError, ToolError

from dat_mcp import identifiers

# Closes every prompt; repeats the server instructions' rules where an answer
# most often slips.
REPORTING = """\
Cite each record's source_url and report ID, report the caveats the tools
return, and say what could not be resolved. Lay out the evidence; don't
state whether the grantee is compliant, delinquent, or at risk. That call is
the analyst's."""

CANDIDATES = (
    "If it returns candidates instead, list them and ask which one I mean"
    " before going on."
)


def fiscal_year_of(value, required=True):
    """The fiscal year as text, e.g. "2024", or "" when optional and blank.

    A word here is usually part of a name: Claude Code splits arguments on
    spaces, so a name with spaces runs into the next argument.
    """
    value = value.strip()
    if not value and not required:
        return ""
    if not re.fullmatch(r"\d{4}", value):
        raise PromptError(
            f"fiscal_year must be the year the fiscal year ends, e.g. 2024, not"
            f" {value!r}. If that's part of the grantee's name, join the name's"
            " words with underscores, e.g. Inland_Behavioral, or give its UEI or"
            " EIN: Claude Code splits arguments on spaces."
        )
    try:
        identifiers.audit_year(int(value))
    except ToolError as error:
        raise PromptError(str(error).replace("audit_year", "fiscal_year")) from error
    return value


def named(grantee):
    """The grantee as typed, with underscores read as spaces."""
    return " ".join(grantee.replace("_", " ").split())


def grantee_args(grantee, state=""):
    """How to pass what the analyst typed to the grantee tools, e.g. uei="X".

    Nine digits (with or without a hyphen) is an EIN, twelve letters and
    digits with no space is a UEI, and anything else is a name. A mistyped
    UEI still goes as uei, so the tool's error says what's wrong with it.
    Underscores in a name are spaces: Claude Code splits arguments on spaces,
    so Inland_Behavioral is how a name gets there in one piece.
    """
    value = named(grantee)
    if re.fullmatch(r"\d{2}-?\d{7}", value):
        args = f'ein="{value.replace("-", "")}"'
    elif re.fullmatch(r"[A-Za-z0-9]{12}", value):
        args = f'uei="{value.upper()}"'
    else:
        args = f'name="{value}"'
    if state.strip():
        try:
            args += f', state="{identifiers.state(state)}"'
        except ToolError as error:
            raise PromptError(str(error)) from error
    return args


def check_filing(grantee: str, fiscal_year: str = "", state: str = ""):
    """Is a grantee current on its Single Audits?

    Each fiscal year's audit, how it was found, its due date, and days late.

    Args:
        grantee: The grantee's UEI, EIN, or name, e.g. JQNLHK8YLTU6. Underscores in
            a name are spaces, e.g. Inland_Behavioral.
        fiscal_year: Only this fiscal year, by the year it ends, e.g. 2025.
            Every year when left blank.
        state: 2-letter state code to narrow a name, e.g. CA.
    """
    args = grantee_args(grantee, state)
    year = fiscal_year_of(fiscal_year, required=False)
    asked = f" for its fiscal year ending in {year}" if year else ""
    year_arg = f", audit_year={year}" if year else ""
    return f"""\
Is the grantee {named(grantee)} current on its Single Audit filings{asked}?

1. Call grantee_audit_coverage with {args}{year_arg}.
   {CANDIDATES}
2. For each fiscal year, give the fiscal year end, report ID, how the audit
   was found (the row's route), FAC accepted date, due date, and days late.
   For a year with no audit found, give the expected due date and whether it
   has passed.
3. For an audit found under another identifier, linked by name and place, or
   through a parent's listing, give the evidence that ties it to this
   grantee; call grantee_identity_resolution when the row doesn't show it. A
   filing linked by name and place shares no identifier with the grantee: say
   so.
4. For a year with no audit found, say what was searched, and list the
   open_questions the tool returned. A year with no audit is not by itself a
   year that needed one.

{REPORTING}"""


def find_covering_audit(grantee: str, fiscal_year: str, state: str = ""):
    """Is a grantee covered by another organization's Single Audit for one fiscal year?

    Args:
        grantee: The grantee's UEI, EIN, or name, e.g. HLKECQTNPLG3. Underscores in
            a name are spaces, e.g. Inland_Behavioral.
        fiscal_year: The fiscal year, by the year it ends, e.g. 2024.
        state: 2-letter state code to narrow a name, e.g. LA.
    """
    args = grantee_args(grantee, state)
    year = fiscal_year_of(fiscal_year)
    return f"""\
Is the grantee {named(grantee)} covered by another organization's Single
Audit for its fiscal year ending in {year}?

1. Call grantee_audit_coverage with {args}, audit_year={year}.
   {CANDIDATES}
   If the year has the grantee's own audit, give it and say so; the rest of
   these steps are for a year without one.
2. Call grantee_identity_resolution with the same identifiers. Note the
   parents in listed_in_parent_audits, the Schedule R filers in irs_records'
   listed_by, and the unconfirmed_leads at the same address.
3. Call irs_get_related_orgs with the grantee's EINs for the parents and
   affiliates it reports on Form 990 Schedule R. Check an EIN from Schedule R
   with irs_search_exempt_orgs before searching FAC by it.
4. For each possible parent, find its audit for the year with
   fac_search_audits, then check whether that audit names the grantee:
   - fac_search_covered_entities with the report ID: is the grantee's UEI or
     EIN listed?
   - fac_get_sefa_notes: is the grantee named, as included ("and
     subsidiaries") or as audited separately?
   - fac_search_audit_text for the grantee's name, after
     fac_fetch_audit_report if the report isn't stored.
5. If the grantee is a state agency or public university, look for the
   statewide or system audit with fac_search_audits, and check with
   fac_get_awards whether its SEFA includes the grantee's programs (ALNs).

For each possible parent, say what the evidence shows: listed as covered in
FAC's data, named in the notes or report text, or related only (on Schedule
R or at the same address), which is a relationship, not audit coverage.
Then answer the question: whether the records show an audit that covers the
grantee for the year, which one, and what that rests on.

{REPORTING}"""


def review_missing_year(grantee: str, fiscal_year: str, state: str = ""):
    """A fiscal year with no Single Audit found: was one required?

    The threshold, the spending found, and the audits either side.

    Args:
        grantee: The grantee's UEI, EIN, or name, e.g. GMFLJZLNKK34. Underscores in
            a name are spaces, e.g. Inland_Behavioral.
        fiscal_year: The fiscal year with no audit, by the year it ends,
            e.g. 2024.
        state: 2-letter state code to narrow a name, e.g. VA.
    """
    args = grantee_args(grantee, state)
    year = fiscal_year_of(fiscal_year)
    years = f"[{int(year) - 1}, {year}, {int(year) + 1}]"
    return f"""\
The grantee {named(grantee)} has no Single Audit found for its fiscal year
ending in {year}. What does the public record show about whether one was
required?

1. Call grantee_audit_coverage with {args}, audit_year={years},
   with_funding=True.
   {CANDIDATES}
   If an audit or a parent's listing turns up for the year, give it and say
   so; the rest of these steps are for a year without one. Say if the year's
   audit isn't due yet.
2. Give the threshold that applies to the year: $750,000 in federal awards
   expended for fiscal years beginning before October 1, 2024, and
   $1,000,000 for those beginning on or after (2 CFR 200.501; the
   dat://reference/audit-rules resource has the rules).
3. Give the outlays over that fiscal year from the usaspending block, the
   period it covers, and the UEIs and awards it adds up. Outlays are a proxy
   for what the grantee expended, and Provider Relief Fund and pass-through
   money mostly don't appear in USAspending as awards to the grantee, so the
   sum can undercount.
4. Compare the audits either side: their total_amount_expended, and with
   fac_get_awards the programs they report, such as a program that ended
   (Provider Relief Fund, ALN 93.498) or started.
5. For a large award, call usaspending_get_award_funding to show when its
   money was drawn, which can fall in a different fiscal year from when it
   was obligated.

Give the threshold, the spending found, the audits either side, and what
spending the public data can't show. Don't state whether an audit was
required.

{REPORTING}"""


def filing_pattern(grantee: str, state: str = ""):
    """How a grantee has filed its Single Audits over the years.

    Each year on time or late, the years with no audit, and changes over time.

    Args:
        grantee: The grantee's UEI, EIN, or name, e.g. HF6NE8JKLGV3. Underscores in
            a name are spaces, e.g. Inland_Behavioral.
        state: 2-letter state code to narrow a name, e.g. MN.
    """
    args = grantee_args(grantee, state)
    return f"""\
How has the grantee {named(grantee)} filed its Single Audits over the years?

1. Call grantee_audit_coverage with {args}.
   {CANDIDATES}
2. Give a table, one row per fiscal year, oldest first: fiscal year end,
   report ID, the identifier it was filed under, how it was found (route),
   FAC accepted date, due date, and days late. Include the years with no
   audit found, and every row for a year with more than one (a resubmission,
   or an audit and a parent's listing).
3. Describe the pattern from those rows: how many audits FAC accepted after
   their due date, the range of days late, whether that changed over the
   years, years with no audit found, and changes of fiscal year end or of the
   identifier filed under.
4. Due dates here are nine months after the fiscal year ends. They don't
   reflect an extension granted for one grantee or a blanket extension after
   a disaster; say so if an answer depends on it.

{REPORTING}"""


def grantee_connections(grantee: str, state: str = ""):
    """Who a grantee is connected to, and the names it has used over the years.

    Parents, covered entities, Schedule R relations, and every UEI and EIN.

    Args:
        grantee: The grantee's UEI, EIN, or name, e.g. TMR8GAV35HH8. Underscores in
            a name are spaces, e.g. Inland_Behavioral.
        state: 2-letter state code to narrow a name, e.g. MO.
    """
    args = grantee_args(grantee, state)
    return f"""\
Who is the grantee {named(grantee)} connected to, and how has it been
named over the years?

1. Call grantee_identity_resolution with {args}.
   {CANDIDATES}
2. Names: call fac_search_audits with the UEIs and EINs in identifiers (up
   to 20 per call) for the auditee_name on each year's filing. Add the
   SAM.gov legal_business_name and dba_name from sam_registrations and the
   IRS name from irs_records. Give a table of year, name, identifier, and
   source, and point out where the name changed and where a FAC name doesn't
   match the IRS name.
3. Identifiers: which UEIs and EINs it filed under in which years, with the
   evidence linking each.
4. Connections, grouped by what the records show:
   - parents whose audits list it as covered (listed_in_parent_audits);
   - organizations its own audits list as covered: fac_search_covered_entities
     with its latest report IDs;
   - related organizations on Form 990 Schedule R: irs_get_related_orgs with
     each EIN, for those it reports and the filers that list it, with the
     relationship type as reported;
   - other organizations that filed under its identifiers
     (other_organizations), which are not the grantee;
   - unconfirmed leads, and why each falls short.
5. For each connection, say what it rests on: an audit's listing, a
   relationship reported to the IRS, or a shared identifier, name, or
   address only. Don't describe a relationship the records don't state.

{REPORTING}"""


PROMPTS = {
    check_filing: "Check a grantee's Single Audit filings",
    find_covering_audit: "Find an audit that covers a grantee",
    review_missing_year: "Review a year with no audit",
    filing_pattern: "A grantee's filing pattern",
    grantee_connections: "A grantee's connections and names",
}


def register(mcp):
    for prompt, title in PROMPTS.items():
        mcp.prompt(prompt, title=title)
