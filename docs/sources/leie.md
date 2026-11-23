# LEIE (HHS-OIG List of Excluded Individuals/Entities)

HHS Office of Inspector General's list of individuals and entities barred
from federal health care programs (Medicare, Medicaid, and others). Not used
by DAT. Separate from SAM.gov exclusions, which cover federal awards
government-wide.

- Full database: `https://oig.hhs.gov/exclusions/downloadables/UPDATED.csv`,
  replaced monthly. It holds every exclusion currently in effect; reinstated
  individuals and entities are removed.
- Monthly supplements: `{YYMM}excl.csv` (new exclusions) and
  `{YYMM}rein.csv` (reinstatements) under `/exclusions/downloadables/{YYYY}/`,
  archived for the previous 12 months only. OIG recommends the full download
  instead.
- Online search: <https://exclusions.oig.hhs.gov/>, the only way to verify a
  specific individual or entity.
- Download page: <https://oig.hhs.gov/exclusions/exclusions_list.asp>
- Access: no key.

Based on OIG's download page and record layout in `reference/future-sources/`
(research branch, retrieved October 2026, database version 09-2026). We have
not yet matched the list against grantees.

## Fields

18 fields per record (the record layout gives names and lengths only, no
definitions):

| Field | Length | Notes |
|---|---|---|
| `LASTNAME`, `FIRSTNAME`, `MIDNAME` | 20, 15, 15 | Individuals |
| `BUSNAME` | 30 | Entities. Truncated at 30 characters |
| `GENERAL`, `SPECIALTY` | 20, 20 | Provider type |
| `UPIN`, `NPI` | 6, 10 | Provider identifiers |
| `DOB` | 8 | Individuals |
| `ADDRESS`, `CITY`, `STATE`, `ZIP CODE` | 30, 20, 2, 5 | |
| `EXCLTYPE` | 9 | Statutory basis for the exclusion |
| `EXCLDATE`, `REINDATE` | 8, 8 | Exclusion and reinstatement dates |
| `WAIVERDATE`, `WAIVERSTATE` | 8, 2 | Waivers |

## What the MCP could use

| Data | What it adds |
|---|---|
| `BUSNAME` + address | Whether a grantee, or an affiliate named in its audit, is excluded from federal health care programs. HHS-specific risk context for an HRSA analyst. |
| `NPI` | A provider identifier that could link an excluded entity to provider-level records. |
| `EXCLDATE`, `EXCLTYPE` | When and why, to set against the grantee's award and audit timeline. |
| Individuals' names | Possibly officers or principals named in audit text or SAM.gov points of contact. Higher risk of false matches; treat with care. |

## Limits and quirks

- **No EIN, TIN, or UEI.** Entity matching is by business name and address
  only, and `BUSNAME` is cut off at 30 characters. Every match needs
  corroboration before it means anything.
- **SSNs are withheld** under the Privacy Act, so OIG says the online search
  is the only way to verify a specific individual or entity.
- **Current exclusions only.** Reinstated parties are removed from the full
  file, and supplements older than 12 months aren't kept, so history is
  limited.
- The file is dated by the prior month: the October update contains actions
  through the end of September.
- A match is a lead for an analyst, not a finding, and individual records
  are personal data. Keep any name matching to entities unless there's a
  clear reason to go further.

## Still to test

- How many FAC auditees or their named affiliates match by name and state.
- Whether `NPI` values can be tied to grantee UEIs or EINs through another
  public source.
- How much LEIE overlaps with SAM.gov exclusions, so we know whether checking
  both adds anything.
