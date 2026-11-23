## What DAT does today

DAT is a batch pipeline. For each active grantee and each of the last three
fiscal years, it runs a fixed sequence of checks and stops at the first one
that settles the year:

1. **An audit under the grantee's own UEI** in FAC for that fiscal year.
2. **A parent's audit that lists the grantee's UEI** in FAC's `additional_ueis`
   for the same year.
3. **HRSA's internal grants records** for evidence of an audit (internal; not
   used in this project).
4. **Spending below the threshold**, from USAspending HHS obligations, means
   no audit was required.
5. **Past the due date** (nine months after fiscal-year end) with nothing
   found: delinquent, or manual review if the grantee has never filed under its
   own UEI, since it may be covered by a parent's audit that wasn't found.

## Where the rules stop

| Question | DAT's pipeline | What the public data can also show |
|---|---|---|
| **Which identifiers belong to this grantee?** | Exact UEI match. EIN is displayed but not searched. | Filings under a mistyped or changed UEI, found by EIN; typos confirmed by SAM.gov (the wrong UEI isn't registered) and EINs confirmed by IRS records (S1, S2, S16); an automatically revoked exemption explains an EIN missing from IRS records (S15). |
| **Is it covered by another organization's audit?** | Parent lists the grantee's UEI for the same year. | Parents that list the grantee's EIN instead; audits whose SEFA notes or PDF name the grantee with no structured link; statewide audits that include a state agency's programs; parent lists that shrink while the audit still covers the entity (S9–S12, S17–S19); parents that report the grantee as related on Form 990 Schedule R (S4, S17, S19). |
| **Was an audit required that year?** | HHS obligations by federal fiscal year against the threshold. | When the money was actually paid out (outlays by period), mapped to the grantee's own fiscal year; one-time awards obligated in one year and spent in another (S14). |
| **Was it filed on time?** | An audit existing for the year counts as compliant; the acceptance date isn't compared with the due date. | Months late for every filing, and the trend over years (S14, S20). |
| **What does the record show about risk?** | Not used in classification. | Going concern, material weaknesses, questioned costs and repeat findings on HRSA programs, auditor changes (S20). |


## What the MCP does with one of those cases

It gives an AI agent tools to do the investigation an analyst would, for one
grantee at a time, using public data only: resolve every identifier that
belongs to the grantee, find the audits that could cover it for a given year,
and lay out its filing, findings, and spending history.
[scenarios.md](scenarios.md) shows the cases this is built for.

Every answer carries its sources (report IDs, quotes, links) and caveats, and
says what's still unresolved. **The agent gathers the evidence; the analyst
makes the call.**

## Why it matters

- **Fewer false alarms.** Grantees wrongly treated as delinquent cost staff time
  and the grantee's goodwill. Settling them with evidence frees time for real
  cases.
- **Real problems ranked sooner.** For grantees that truly haven't filed, the
  public record gives a basis for deciding which to act on first.
- **Decisions with a record.** Each conclusion points to public documents a
  supervisor or auditor can check.

## What it won't do

- **Use internal data.** The hackathon version uses only public sources, so it
  can't see what HRSA's own grants records show.
- **See audits outside FAC.** For-profit grantees' audits and anything not
  submitted to FAC are invisible to it.
- **Account for all money.** Provider Relief Fund and pass-through money
  mostly don't appear in USAspending as awards to the grantee, so spending
  checks can undercount.
- **Decide compliance.** It reports what the evidence shows and what it
  can't show; it never labels a grantee compliant or delinquent.
