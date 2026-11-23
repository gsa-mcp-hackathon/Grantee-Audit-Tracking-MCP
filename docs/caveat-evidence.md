# Evidence behind the caveats

Tool notes and caveats that steer a conclusion cite a count: the
unregistered-UEI notes, the missing-EIN and revocation notes, the parent-drop
and no-listing caveats, the government and university notes, and the gap-year
note. Caveats that only warn
say what can happen, with no count; the counts behind their wording are here
as background. This page records where each count comes from, so a caveat can
be traced to its source and re-checked when the data changes.

Sources: the local FAC snapshot (downloaded 2026-10-22), SAM.gov's public
monthly entity extract of 2026-11-01 and its Entity API, and USAspending. All
public data. Counted 2026-11-09 and 2026-11-10.

## UEIs that SAM.gov doesn't find

Used in `grantee_identity_resolution` (notes and open questions), `sam_get_registration`.

FAC has 60,149 distinct auditee UEIs (excluding `GSA_MIGRATION`). A lookup of
registered entities only finds no registration for 3,918 of them:

| What it turned out to be | UEIs | Share |
|---|---|---|
| Real: assigned by SAM.gov but never registered ("ID Assigned") | 2,014 | 51% |
| Real: no public SAM.gov record, but USAspending has a recipient with it | 427 | 11% |
| Typo of a UEI SAM.gov has, 1 to 3 characters off | 781 | 20% |
| Not close to any real UEI; the same EIN files under a UEI SAM.gov has | 375 | 10% |
| Placeholder | 4 | 0% |
| Unresolved | 317 | 8% |

Since 2026-11-06 the SAM tools also ask for ID Assigned UEIs, so those 2,014
come back as records and `not_found` holds the other 1,904: no public record,
registered or not. Of those, 41% are typos, 20% made-up or wrong, 22% real and
17% unresolved. Grouped by what `grantee_identity_resolution` can see, which is how its
note is chosen:

| What the entity's other identifiers show | UEIs | Typo | Made-up or wrong | Real | Unresolved |
|---|---|---|---|---|---|
| One character from a UEI SAM.gov has, same EIN or name | 594 | 100% | 0% | 0% | 0% |
| Same EIN files under a UEI SAM.gov has, further away | 566 | 22% | 66% | 12% | 0% |
| No UEI of this EIN in SAM.gov | 744 | 9% | 0% | 48% | 43% |

By year: of unfound UEIs last used in audit year 2022 or earlier, 46% are
typos; of those used in 2023 or later, 2% are. About 12% of all UEIs last used
in 2022 are mistyped, and under 0.1% of later ones (background for
`fac_find_linked_identifiers`).

How: UEIs not in the monthly extract were looked up through the Entity API as
our client does, then again with `samRegistered=No`. A UEI one or two edits
from a SAM.gov UEI, or within three of one its EIN or name files under, counts
as a typo. Those with no SAM.gov record were checked against USAspending's
recipient search; none of the 781 typos matched there. Control: 100 FAC UEIs
known to be registered all came back from the same lookup.

## Parents that list covered entities

Quoted in `fac_search_covered_entities` (the dropped-entity and no-listing
caveats) and `grantee_audit_coverage` (gap-year and never-filed notes). The relisting,
EIN-only, SEFA-note and report-text counts are background.

- **Relisted next year:** 85.8% of 22,908 listed EINs and 88.1% of 18,805
  listed UEIs, for parents that filed the next year (audit years 2022 to 2025,
  parent identified by EIN).
- **Dropped entities:** a sample of 72, one per parent, with no own filing and
  no other parent the next year. In 37 the entity could be found in the
  parent's report and wasn't the parent itself under another identifier. 33 of
  those 37 were still described as part of the group in the parent's next
  report, 3 had been sold or deconsolidated, and 1 wasn't named.
- **No list at all:** 1,925 of 2,865 audits (67%) whose auditee name ends "and
  subsidiaries", "and affiliates" or similar list no covered UEIs or EINs.
- **EINs only:** of 7,479 audits with a list, 35% list EINs only, 22% UEIs
  only, 43% both.
- **SEFA notes:** 12.9% of 34,717 listed entities are named in that audit's
  SEFA notes; 19% of listing audits name at least one.
- **Report text:** a covered entity was found by name in 50 of 71 readable
  reports (a lower bound; names came from IRS and SAM.gov records).

## Government bodies and universities

Used in `grantee_audit_coverage`, for entities that have never filed only.

Recipients with at least $750,000 in HRSA grant obligations in federal FY2023
or FY2024, checked against FAC audit years 2023 to 2025 under their own UEI:

| | State bodies | Local governments | Public universities and colleges |
|---|---|---|---|
| Recipients | 248 | 249 | 209 |
| Own audit | 58 | 149 | 80 |
| Listed by another audit | 159 | 24 | 115 |
| Neither | 29 | 73 | 14 |

Of the "neither" rows, a same-state audit under another UEI reports the
recipient's HRSA programs for 22 of 29 state bodies and 48 of 73 local
governments. With no own audit, coverage was found for 181 of 190 state bodies
and 72 of 100 local governments.

The note isn't shown to a body that files its own audits and skips a year.
After a filed year (2022 or 2023), a parent listed the entity the next year in
3 of 165 state gaps, 17 of 5,555 local gaps, 8 of 94 higher-ed gaps and 69 of
5,489 nonprofit gaps. Of all filers that skipped a year, 55% had spent under
$1.5 million the year before.

## Background counts

Not quoted in tool text; they support plain wording ("can differ", "often
accrual").


- **Registered names** (`sam_search_entities`, `sam_get_registration`): of 53,950 UEIs in both SAM.gov
  and FAC, the legal name matches the latest FAC name for 53%, matches after
  dropping punctuation and words like "Inc." for 15%, and differs more for 32%.
- **SEFA basis** (`usaspending_sum_outlays`): of 133,548 reports with SEFA
  notes (2023 to 2025), 60% state accrual, 17% modified accrual, 3% both, 8%
  cash, modified cash or regulatory, 1% mixed, 11% not stated in those words.

- **Provider Relief Fund** (`grantee_audit_coverage`, gap years after a PRF year;
  quoting the 2023 figure led the agent to assume PRF continued into the gap
  year, so the note states only that it was one-time money):
  of auditees that reported ALN 93.498 for audit year 2022, 52% of those that
  filed for 2023 still reported it, 6% for 2024 and 1% for 2025. PRF on all
  SEFAs fell from $58.8 billion (2021) to $1.0 billion (2024).
- **SEFA notes on file** (`fac_search_sefa_notes`): 99% of audits, before and
  after 2023, have note text in FAC's structured data.
- **EINs in SEFA notes** (`fts_query`, used by `fac_search_sefa_notes`,
  `fac_search_audit_text` and `fac_find_linked_identifiers`): of 3,976
  EIN-like numbers in notes, 2,430 (61%) are written with a hyphen
  (54-2003078), which the index splits into two words, and 1,546 without;
  601 reports have only hyphenated ones. So an EIN is searched both ways.
  Counted 2026-11-12 on the FAC snapshot of that day.

## IRS records

Quoted in `grantee_identity_resolution` (the note for an EIN not in the EO BMF) and
`irs_search_exempt_orgs` (the revocation caveat). EINs on FAC audits for 2022
to 2025, against the EO BMF and automatic revocation list downloaded 2026-11-10.

| FAC entity type (most common across the EIN's filings) | EINs | In the EO BMF |
|---|---|---|
| Nonprofit | 29,071 | 92% |
| Higher ed | 1,802 | 61% |
| Government (state, local, tribal) | 27,781 | 5% |

FAC's entity type varies across years for 4,884 of 59,004 EINs, so each EIN
is typed by its most common label, as `grantee_identity_resolution` does. The note for a
missing EIN is chosen by that type, and names an IRS EIN one
digit away when the IRS lists it under a name the filings used (by the
tool's `same_name` rule). Of 255 such non-government EINs, the same entity
also filed under the IRS one for 159, the state matched for 251, and the IRS
one filed to FAC as a separate organization for 7.

On the revocation list and not in the EO BMF: 352 FAC EINs, of which 205 are
government bodies, 99 private nonprofits under the same name, 28 under
another name, 10 Puerto Rico organizations, 6 LLCs and 4 EIN typos. 60 of the
99 were revoked in 2022 or later. `docs/sources/irs.md` counts about 50
private nonprofits as leads, after further filtering.

Background, not quoted: when an entity also files under an EIN the IRS has,
35% of the missing EINs are one or two digits from it, and 47% three or more
(separate EINs, such as a foundation and its operating organization).

## Filers linked by name and place

Behind `grantee_identity_resolution`'s rule for joining filings that share no UEI or EIN, and the
`route` "audit linked by name and place" in `grantee_audit_coverage`. Counted 2026-11-16 on FAC
bulk data downloaded 2026-10-22 to 2026-11-10 (418,758 audits, FY2016 to
FY2026), grouping filings by shared UEI or EIN.

- **Why it happens.** FAC has UEIs from FY2022 only: of 234,642 audits for
  FY2016 to FY2021, all but 1,300 carry no UEI, so an earlier year links to a
  later one by EIN alone. When the EIN also differs by more than one
  character, nothing in FAC joins the two halves.
- **How often.** 88 pairs of filers have the same name and city or ZIP, no
  identifier in common, differ by more than one character, and file in
  sequence (one stops, the other starts within two years). Counting by street
  address instead, 158 filers active since FY2022 have such a predecessor
  with a similar name at the same address, 14 of them HRSA recipients. Both
  are lower bounds: they need an exact name or the same address.
- **What they are.** Of 16 checked by hand, 9 earlier EINs are not in the
  IRS's EO BMF (a mistyped EIN that lasted years, or an entity since
  dissolved), 1 belongs to a related foundation, and 6 are a hospital or
  health center whose parent system took over the filing. The IRS record
  says which EIN is the organization's; it rarely finds the other one.
- **Why following every shared identifier is wrong.** Of 647 groups of four
  or more linked identifiers, 40 hold three or more unrelated names across
  two or more states. Among 3,314 HRSA recipient UEIs with FAC filings, 95
  share an EIN with a filer whose name has no distinctive word in common;
  some are renames and some are another organization that used the EIN on one
  report. That is why a stray filing is set aside (`other_organizations`) and
  a name match needs a place to agree before it is linked.
- **Rules tightened on real cases** (2026-11-17): a longer name at the same
  address is a lead, not a link (a medical college's alumni association
  shares its street address); a name alone links nothing ("Community Health
  Center" matched three unrelated Connecticut filers).
- **The IRS "only record in the state" rule, simulated across FAC**
  (2026-11-17): 451 matches, 352 on the IRS secondary name and 99 on the
  primary. Sampled, the secondary-name matches were nearly all wrong (a
  county's name matching a Farm Bureau or union chapter) and the
  primary-name ones mostly right. The rule now reads primary names only, and
  IRS records aren't searched for government bodies.
- **Same name and same city, different street address**: 293 pairs across
  FAC; in a sample of 12 nearly all were one organization with the address
  written differently ("1ST" and "FIRST"). The sample is small, and this is
  the rule to re-check before relying on it more widely.

## Not measured

- `sam_search_entities`: how often `total_matches` exceeds `count` on a name
  search. Lookups by UEI returned 0.6% duplicate records. The description says
  "can be larger".

## Re-checking

The scripts that produced these counts are kept locally, not in the repo; they
read the local database and the downloaded SAM.gov extract. The SAM.gov lookups
used about 220 API requests. Re-check after a database rebuild or before
quoting a count outside the tools.
