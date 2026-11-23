# Scenarios

## Index

| | Scenario | Category |
|---|---|---|
| S1 | A UEI typo hides a filed audit | Identity |
| S2 | An EIN typo, and the usual tie-breakers pick the wrong one | Identity |
| S3 | One EIN reported by two different organizations | Identity |
| S5 | One UEI, two different auditees in the same year | Identity |
| S16 | Subsidiaries that filed every year, but every shortcut says they didn't | Identity + coverage |
| S4 | A subsidiary covered by its parent's audit, findable only by EIN | Coverage |
| S9 | The audit covers a group, but only the text says so | Coverage |
| S10 | A parent's list of covered entities goes empty | Coverage |
| S11 | Notes name affiliates by EIN, sometimes to *exclude* them | Coverage |
| S17 | A health system's audit filed under its hospital's identity | Coverage |
| S18 | A state department covered by the statewide audit | Coverage |
| S19 | A hospital dropped from its system's list but still in the audit | Coverage |
| S14 | A missing year that probably didn't need an audit | Delinquency |
| S20 | A chronic late filer, with the risk picture around it | Delinquency + risk |
| S15 | Federal money to a nonprofit whose tax exemption was revoked | Risk |
| S21 | Grantees that never file in FAC, by rule | Scope |

Numbers are kept from the research branch, where S6–S8, S12, and S13 cover
data-quality patterns in more depth.

---

## Identity: which identifiers belong to this grantee?

### S1. A UEI typo hides a filed audit

**What happens.** Inland Behavioral and Health Services (CA, UEI
`JQNLHK8YLTU6`, EIN `953246624`) filed its FY2025 audit under `JQNLHK8YLTU`**`B`**:
report `2025-06-GSAFAC-0000397078`, accepted 2026-01-08, before the March 2026
due date. Its 2022–2024 audits are under the correct UEI.

**Why a lookup misses it.** By UEI, FY2025 looks unfiled.

**What settles it.** Search FAC by EIN: the FY2025 filing has the same EIN,
name, and state. SAM.gov confirms the typo: `JQNLHK8YLTUB` isn't registered,
`JQNLHK8YLTU6` is active.

**Negative control.** AxessPointe Community Health Center (OH, `KUWQTKN683P6`)
has a one-character twin, `KUWQTKN683PG`, with only a 2022 filing. It doesn't
cover any later year.

### S2. An EIN typo, and the usual tie-breakers pick the wrong one

**What happens.** Konza Prairie Community Health Center (KS, UEI
`JKNCXKKFEPH1`) reported EIN `481150706` in 2022 and `148115070` in 2023–2024:
a `1` added at the front, the last digit dropped.

**Why a lookup misses it.** A pipeline that needs one EIN per organization
takes the latest or the most common. Both pick the typo here.

**What settles it.** IRS exempt-organization records list Konza under
`481150706`. Only the real EIN is on file.

### S3. One EIN reported by two different organizations

**What happens.** Community Havens Inc. (VA, UEI `R7PNMGWQ4QH8`) filed under
EIN `542006078` every year except 2023, when it reported `542003078`. That
typo'd number is the real EIN Chesapeake Hospital Authority lists for one of its
included entities, Positron Emission Tomography of Hampton Roads.

**Why a lookup misses it.** An EIN join credits PET of Hampton Roads with
Community Havens' 2023 audit. It never filed one.

**What settles it.** Check names, states, and UEIs before trusting an EIN
link, and check the EIN against IRS records: the IRS lists `542006078` for
Community Havens (Chantilly, VA) and has no record of `542003078`.

### S5. One UEI, two different auditees in the same year

**What happens.** In FY2025, Imperial Beach Community Clinic's audit (report
`2025-06-GSAFAC-0000409624`, on time) is filed under San Fernando Community
Hospital's UEI, `E4DEKL2LPRN5`, with Imperial Beach's own name and EIN
`237209592`. Both use the same audit firm.

**Why a lookup misses it.** Imperial Beach's own UEI (`R6PCE5S47LJ8`) shows no
FY2025 audit.

**What settles it.** Searching by EIN finds it.

### S16. Subsidiaries that filed every year, but every shortcut says they didn't

**What happens.** Betty Jean Kerr–People's Health Centers (MO, UEI
`TMR8GAV35HH8`), an HRSA health center, files a consolidated audit with no
structured subsidiary links. Its FY2025 PDF says three wholly-owned
subsidiaries spent federal money not included in its SEFA. One of them,
People's Health Supportive Services (EIN `431844750`), looks unfiled for
FY2023–2025 by UEI, name, and state.

**Why a lookup misses it.** Its UEI changed from `T3RBMLC7N515` to
`T3RBNKC7NS15` (three characters), its filings list Louisiana or Connecticut
instead of Missouri, and its name varies.

**What settles it.** Only the EIN finds every filing, 2016 through 2025. The
subsidiary's FY2025 federal spending ($1,845,713) matches the parent's PDF to
the dollar. SAM.gov shows `T3RBNKC7NS15` active and `T3RBMLC7N515`
unregistered. The parent's (EIN `431036785`) Form 990 Schedule R lists the
subsidiary only for tax year 2024, not 2022 or 2023, and the subsidiary's own
Schedule R gives the parent's EIN as `431036725`, which the IRS has no record
of. Schedule R EINs are typed by the filer, so an EIN from it can be checked
against the IRS's exempt-organization file before searching FAC by it.

---

## Coverage: is this grantee inside someone else's audit?

### S4. A subsidiary covered by its parent's audit, findable only by EIN

**What happens.** University Medical Center Management Corp (UEI
`HLKECQTNPLG3`, EIN `251925187`) has no audit of its own. Louisiana Children's
Medical Center (UEI `XVLSKWGRX9J6`) lists UMCMC's EIN, not its UEI, in its 2023
and 2024 filings.

**Why a lookup misses it.** A search of parents' `additional_ueis` finds
nothing.

**What settles it.** FAC's `additional_eins` table, then the parent's SEFA
notes to confirm the subsidiary is named. LCMC's (EIN `943480131`) Schedule
R lists UMCMC as a related tax-exempt organization for tax years 2022–2024.

### S9. The audit covers a group, but only the text says so

**What happens.** The SEFA note says the schedule covers the auditee "and
subsidiaries" or "and affiliates," but the filing lists no additional EINs or
UEIs. Example: Bridgeway Capital (PA, `LNAWXF1XDX57`) names four subsidiaries
in its 2022–2024 notes; one drops out in 2025.

**Why a lookup misses it.** Structured coverage lookups have nothing to match.

**What settles it.** Read the notes, then the audit PDF, for component names.
Resolve names to EINs through IRS records or to UEIs through SAM.gov. About
4,000 audits since 2022 describe a group this way with no structured links.

### S10. A parent's list of covered entities seems to go empty

**What happens.** UEI `C4PCPEMHEUR6` has two filers: Mercy Housing, Inc.
(Denver, EIN 470646706), which listed 97 covered EINs in 2022, 97 in 2023, and
96 in 2024, and Mercy Housing Corporation (Lansing, MI, EIN 382376561), whose
separate audits list none. Read by UEI alone, 2023 looks like the year the
list went empty; it didn't. (The original write-up said the list went empty
in 2023; the eval on 2026-10-28 showed that was the UEI collision.)

**Why a lookup misses it.** A coverage check keyed on UEI merges the two
organizations and calls all 97 entities uncovered in 2023. Re-checked by EIN
(2026-11-09): 11% to 16% of parents with a covered-EIN list drop the whole
list the next year, and 87% of listed entities are listed again.

**What settles it.** Separate filers by EIN before reading a list as one
parent's. Treat a dropped entity as an open question: check the notes,
SAM.gov registration, or IRS records before concluding anything.

### S11. Notes name affiliates by EIN, sometimes to *exclude* them

**What happens.** Chesapeake Hospital Authority (report
`2023-06-GSAFAC-0000345090`) lists seven included entities by TIN in its
notes; its structured EIN list is empty. Nueva Esperanza, Inc. (report
`2023-06-GSAFAC-0000034449`) names three affiliates by EIN to say their
spending is in *their own* single audits.

**Why a lookup misses it.** Pulling every EIN out of the text gets Chesapeake
right and Nueva Esperanza wrong. The relationship type matters as much as the
name.

**What settles it.** Read the relationship, then verify it. Every "audited
separately" claim we checked (15 of 15) had a matching FAC audit under the
stated EIN for the same year.

### S17. A health system's audit filed under its hospital's identity

**What happens.** Covenant Health (MA, EIN `222484505`) files for its member
hospitals. For FY2025, report `2025-12-GSAFAC-0000431344` is filed under Saint
Joseph Hospital's UEI (`NH6BKNCW8ZN3`), EIN, and name, but the PDF is "Covenant
Health, Inc. and Subsidiaries," the whole system. Covenant's own UEI has no
FY2025 filing, and St. Joseph dropped out of Covenant's EIN list in 2023.

**Why a lookup misses it.** By structured data, St. Joseph has no audit for
2023–2024 and Covenant has none for 2025. Both are wrong.

**What settles it.** The parent's PDF names its members each year. The EIN and
state mismatch (IRS lists the EIN in Nashua, NH; the filing says Maine) should
be flagged for an analyst, not resolved silently. Covenant's Schedule R lists
St. Joseph Hospital (`010212435`) for 2023 and 2024, in Maine, though both
are subordinates in the Catholic group exemption (0928), whose members need
not list each other.

### S18. A state department covered by the statewide audit

**What happens.** The Idaho Department of Health & Welfare (UEI
`WP92EVJB9HF9`) runs HRSA programs (93.913, 93.241, 93.301, 93.994) but has no
FAC filings and isn't in any additional-UEI list.

**Why a lookup misses it.** No identifier connects the department to the
State of Idaho's single audit (UEI `W1N3LFTZ82K4`).

**What settles it.** The statewide audit's SEFA includes the department's
program numbers. The link is state plus program number. The statewide audits
are themselves late: FY2025 was accepted 2026-10-05, 15 months after year-end.

### S19. A hospital dropped from its system's list but still in the audit

**What happens.** Scott & White Hospital – Round Rock (UEI `GAHCBCQWB857`,
EIN `203749695`) is in Baylor Scott & White's additional-EIN list for FY2023
and FY2024. For FY2025 the list shrinks from 24 to 3, and the parent's UEI
changes.

**Why a lookup misses it.** Structured data says coverage ended.

**What settles it.** The FY2025 PDF (report `2025-06-GSAFAC-0000405570`) names
the hospital among the controlled affiliates. Baylor Scott & White Health's
Schedule R (EIN `463131350`) lists the hospital as a related tax-exempt
organization in every return from tax year 2022 to 2024, the year ending June
2025: evidence the relationship continued, though not of audit coverage.

---

## Delinquency and risk

### S14. A missing year that probably didn't need an audit

**What happens.** Chesapeake Hospital Authority (VA, UEI `GMFLJZLNKK34`) has
audits for FY2020–2023 and FY2025 (June year-end) but none for FY2024.

**Why a lookup misses it.** "Filed before and after, nothing in between" looks
like a clear delinquency.

**What settles it.** The SEFAs show most spending was Provider Relief Fund,
which ended. A $1.0M HRSA grant (award `CE152817`) was obligated in September
2023, inside FY2024, but USAspending outlays show it was drawn February–May
2025, inside FY2025, where it appears in the SEFA. FY2024 spending was probably
under the threshold. One open question remains (PRF money spent through
December 2023), and the answer should say so. Separately, the lateness is
real: three audits accepted 18–33 months after year-end.

### S20. A chronic late filer, with the risk picture around it

**What happens.** Community Health Service, Inc. (MN, UEI `HF6NE8JKLGV3`, EIN
`411000060`), a Health Center Program grantee with a January year-end, filed
its audit for the year ending 2025-01-31 (report `2025-01-GSAFAC-0000433125`)
on 2026-10-23, almost a year after the 2025-10-31 due date. Until then, no
alternate UEI, EIN variant, or parent filing turned one up, so it was the
research's real-delinquency case.

**Why it's here.** An answer needs more than "filed or not"; the pattern is
what matters. It also shows why pinned answers need re-checking: the status
changed between the research and the first eval run.

**What the public record shows.** Audits accepted 7 to 10 months after
year-end through FY2019, then 12, 19, 43, 29, 29, and 21 months for FY2020
through FY2025. Going-concern doubt in FY2019 and every year since FY2022,
material weaknesses in most years, FY2024 findings on Health Center Program
cash management, and four audit firms in ten years. That history is what an
analyst needs to decide where it sits in the queue.

### S15. Federal money to a nonprofit whose tax exemption was revoked

**What happens.** Changepoint Integrated Health (AZ, UEI `EQTLST8WQBS6`, EIN
`860215065`) had its exemption automatically revoked 2022-02-15, with no
reinstatement on record. Its FY2022 audit reports $1.67M from the Provider
Relief Fund, and its SAM.gov registration still self-reports tax-exempt status.

**Why a lookup misses it.** Nothing in FAC, SAM.gov, or USAspending shows
exemption status. It takes the IRS revocation list.

**What settles it.** Most matches against that list are noise (government
bodies FAC labels nonprofit, Puerto Rico organizations, EIN typos). After
filtering, this is a lead for an analyst, not a finding of wrongdoing.

### S21. Grantees that never file in FAC, by rule

Some grantees will never have a FAC audit of their own, and a tool should say
so instead of reporting "not found":
- **For-profit organizations.** HHS has separate audit requirements for them,
  which don't go to FAC.
- **Federal agencies**, such as the Indian Health Service receiving an HRSA
  award.
- **State agencies and public universities** with no audit of their own:
  inside the statewide or system audit for 181 of 190 state bodies with HRSA
  awards (S18). One that files its own audits and skips a year is rarely
  picked up by a parent.
- **Hospitals in health systems**, inside the system's audit and often named
  only in the report text (S17, S19): 67% of audits filed as "... and
  subsidiaries" or "... and affiliates" list no covered entities.
- **One-time construction or directed-spending awards**, obligated in one
  year and spent in another, so the threshold year moves (S14).
