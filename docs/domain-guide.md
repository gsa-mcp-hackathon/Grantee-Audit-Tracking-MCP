# Domain guide

1. How the Single Audit requirement works, in plain terms
2. The vocabulary you'll see in the data and the docs
3. What each public source holds that matters to us, and what it can't tell you
4. How the sources connect

---

## 1. The requirement, start to finish

The chain of events behind every "did this grantee file?" question:

1. **An agency makes an award.** HRSA awards a grant to an organization, the
   **recipient**. The recipient may pass some of the money to other
   organizations as **subawards**; those are **subrecipients**.
2. **The organization spends federal money during its own fiscal year.** What
   counts is money **expended**, not money awarded or received. Expenditure is
   tied to when the activity happens (2 CFR 200.502), so a grant awarded in one
   year can be spent in another.
3. **If it spends enough, it needs a Single Audit.** The threshold is
   **$1,000,000** in federal awards expended for fiscal years starting on or
   after October 1, 2024, and **$750,000** before that (2 CFR 200.501). The
   total includes all federal money from every agency, direct and passed
   through, not just HRSA's.
4. **An independent auditor performs the audit.** It covers the financial
   statements, internal controls, and compliance with the rules of the largest
   or riskiest federal programs (the **major programs**).
5. **The organization responds to the findings.** When the audit is complete,
   the organization writes a **corrective action plan** for each finding: what
   it will do, who is responsible, and by when, or why it disagrees (2 CFR
   200.511(c)).
6. **The organization submits it all to the Federal Audit Clearinghouse
   (FAC).** The audit, the corrective action plan, and the data collection form
   go in together, due the earlier of 30 days after the auditor's report or
   **9 months after the fiscal year ends** (2 CFR 200.512). FAC records the date
   it **accepted** the submission.
7. **Agencies decide on the findings.** Within six months of FAC acceptance, the
   awarding agency (or the pass-through entity, for a subaward) issues a
   **management decision** on each finding: whether it's sustained, and what the
   organization must do, such as repay disallowed costs (2 CFR 200.521). Next
   year's audit reports the status of prior findings.

Where this goes wrong in practice, and why a simple lookup isn't enough:

- **Not everyone who receives HRSA money files a Single Audit.** The rules apply
  to states, local governments, tribes, colleges and universities, and
  nonprofits (the "non-Federal entities" defined in 2 CFR 200.1). For-profit
  organizations and federal agencies are outside them. HHS separately requires
  for-profit recipients above the threshold, including for-profit hospitals, to
  get either an audit of their HHS awards or a Single Audit-style audit
  (2 CFR 300.218(d)), so a for-profit grantee isn't unaudited, just outside what
  FAC tracks. Payments to a **contractor** (a vendor providing goods or
  services) aren't subject to audit; only recipients and subrecipients are
  (2 CFR 200.501(g)).
- **One audit can cover many organizations.** A health system, state, or
  university system can file one audit that covers its hospitals, departments,
  or campuses. The covered organization has no FAC filing of its own.
- **Below the threshold means no audit, and that's normal.** A missing audit in
  a year when the organization spent less than the threshold isn't a
  delinquency. Deciding which side of the line a year falls on requires knowing
  when money was spent.
- **Late isn't always noncompliant.** The cognizant or oversight agency can
  extend the deadline, and the Council on Federal Financial Assistance issues
  blanket extensions after disasters (for example, a March 2025 memo for
  Hurricanes Helene and Milton and the California wildfires).

---

## 2. Vocabulary

### The audit

| Term | Meaning |
|---|---|
| **Single Audit** | The organization-wide audit required by 2 CFR 200 Subpart F. Sometimes called an "A-133 audit," after the OMB circular that governed it until the Uniform Guidance replaced it in 2014. |
| **Program-specific audit** | An alternative allowed when an organization spends money from only one federal program. Rare; FAC labels it in `audit_type`. |
| **Reporting package** | What gets submitted: financial statements, the SEFA, auditor's reports, findings, the summary schedule of prior findings, and the corrective action plan. |
| **SF-SAC** | The data collection form submitted with the audit. FAC's structured data (the tables in section 3) is the SF-SAC; the audit PDF is the rest of the reporting package. |
| **SEFA** | Schedule of Expenditures of Federal Awards: the organization's list of federal money spent that year, by program. Its **notes** explain what the schedule covers, which is often where affiliates are named. |
| **Major program** | A program the auditor tested in depth, chosen by size (**Type A** programs, above a threshold that starts at $1,000,000) and risk. |
| **Low-risk auditee** | An organization with two clean, on-time years of audits; it qualifies for less audit coverage. Losing that status is a mild signal. |
| **Opinion** | The auditor's conclusion. **Unmodified** is clean. **Qualified**, **adverse**, or **disclaimer** means something went wrong. FAC records one opinion for the financial statements and one per major program. |
| **Going concern** | The auditor has substantial doubt the organization can keep operating for the next year. A serious signal. |

### Findings

| Term | Meaning |
|---|---|
| **Finding** | A problem the auditor reports: a control weakness, noncompliance, or questioned costs. Each has a reference number such as `2024-001`. |
| **Material weakness** | The most serious control problem: a reasonable possibility that a material error won't be caught. |
| **Significant deficiency** | A control problem less serious than a material weakness but worth governance attention. |
| **Questioned costs** | Spending the auditor believes may be unallowed or unsupported. Must be reported above $25,000 per type of compliance requirement for a major program. |
| **Repeat finding** | The same problem reported in the prior year's audit. A sign the corrective action didn't work. |
| **Compliance requirement** | A lettered category of program rules, set in the OMB **Compliance Supplement**: A (activities allowed), B (allowable costs), C (cash management), E (eligibility), I (procurement and debarment), L (reporting), M (subrecipient monitoring), N (special tests), and others. Each finding names the letters it affects. |
| **Corrective action plan (CAP)** | The organization's written response to each finding, submitted with the audit and before the agency's management decision. Its text is in FAC and, like all FAC free text, is self-reported and unverified. |
| **Management decision** | The awarding agency's ruling on each finding, due within six months of FAC acceptance: sustained or not, and what the organization must do. Not in FAC. |

### Money

| Term | Meaning |
|---|---|
| **Obligation** | A legal commitment by the agency to pay, recorded when the award is made or amended. USAspending's headline amounts are obligations. |
| **Outlay** | Money actually paid out to the recipient. USAspending reports outlays by period, which is the closest public proxy for when the money was spent. |
| **Expenditure** | What the organization spent, as reported in its SEFA. This is what the audit threshold measures. Outlays and expenditures usually line up, but not exactly. |
| **Prime award** | An award directly from a federal agency. USAspending is organized around prime awards. |
| **Subaward / pass-through** | Money a recipient passes on to another organization. In the SEFA it shows as `is_direct = N`, with the pass-through entity named. It usually doesn't appear in USAspending as an award to the subrecipient. |
| **ALN** | Assistance Listing Number, formerly CFDA: a federal program code such as `93.224` (Health Center Program). `93` is HHS. FAC splits it into `federal_agency_prefix` and `federal_award_extension`. |
| **Cluster** | A group of related programs treated as one for audit purposes, such as Research and Development or Student Financial Assistance. |
| **Provider Relief Fund (PRF)** | HRSA's COVID-era payments to providers (`93.498`). Large in hospital SEFAs for 2020–2023, but not reported in USAspending as awards to the grantee's UEI, so it distorts spending checks. |

### Organizations and roles

| Term | Meaning |
|---|---|
| **Auditee** | The organization a FAC report is filed under. Not always the grantee you're looking for. |
| **Recipient / subrecipient** | Who received money directly from the agency, and who received it through another organization. |
| **Parent / subsidiary / affiliate** | Organizations under common control. A parent's consolidated audit may cover its subsidiaries, or may explicitly exclude some ("audited separately"). |
| **Cognizant agency for audit** | The agency that oversees an organization's audits, assigned when it spends more than $50 million a year (usually the largest direct funder). Smaller organizations have an **oversight agency** instead. FAC records both as two-digit codes; HHS is `93`. |
| **Group exemption** | An IRS arrangement under which a central organization's subordinates share its tax exemption. Explains why IRS sometimes lists a subordinate under the parent's name. |

### Identifiers

| ID | What it is | Notes |
|---|---|---|
| **UEI** | 12-character Unique Entity ID, assigned by SAM.gov since April 2022 (replaced the DUNS number) | The main key across FAC, SAM.gov, and USAspending. One organization can hold several. Typos are common. |
| **EIN / TIN** | IRS Employer Identification Number (TIN is the general term) | In FAC and IRS records; not in SAM.gov's public data. The best key for finding filings under the wrong UEI. |
| **ALN** | Program number | See above. |
| **FAC report ID** | e.g. `2024-06-GSAFAC-0000364621`: audit year, fiscal-year-end month, source, sequence | The citation for any audit. Records migrated from the Census-era FAC say `CENSUS` instead of `GSAFAC`. The year and month are set when the record is created and occasionally disagree with the corrected dates, so read `audit_year` and `fy_end_date` instead of parsing the ID. |
| **Award ID (FAIN)** | The grant number, e.g. HRSA's `CE152817` | In USAspending, and sometimes in the SEFA's `additional_award_identification`. |

### Time

| Term | Meaning |
|---|---|
| **Entity fiscal year** | The organization's own accounting year, commonly July–June or January–December. Every audit covers one. |
| **Federal fiscal year** | October 1 to September 30, named for the year it ends. USAspending reports by this year. |
| **FAC `audit_year`** | The calendar year in which the audit's fiscal year **ends**. A July 2024–June 2025 audit has `audit_year` 2025. |
| **Due date** | Nine months after fiscal-year end (or 30 days after the auditor's report, if earlier). |
| **`fac_accepted_date`** | When FAC accepted the submission. Months between fiscal-year end and acceptance is the lateness measure used in [scenarios.md](scenarios.md). |

---

## 3. The sources

One-page notes on each source, including what DAT uses today and what the MCP
could add, are in [sources/](sources/).

### Federal Audit Clearinghouse (FAC)

**Who runs it.** GSA, since 2023 (previously the Census Bureau). It's the
government's official repository for Single Audits.

**What's in it.** Every submitted audit from 2016 on in structured form, plus
the audit PDFs. Tribal governments can choose to keep their audits out of
public view, so those don't appear in public data. The structured data is a
set of tables keyed by `report_id`:

| Table | What it tells us |
|---|---|
| `general` | One row per audit: auditee name, UEI, EIN, address, fiscal-year dates, accepted date, auditor firm, opinions, going concern, material weakness flags, total federal spending, entity type, and whether the audit covers other UEIs or EINs |
| `federal_awards` | The SEFA as data: one row per award, with program number, amount spent, direct or pass-through, major program, and findings count |
| `findings` | One row per finding per award: compliance requirement letters, and flags for material weakness, significant deficiency, questioned costs, repeat |
| `findings_text`, `corrective_action_plans` | The full text of each finding and the organization's response |
| `notes_to_sefa` | The SEFA notes as text; they can say which affiliates an audit covers, though the report text names more of them |
| `additional_ueis`, `additional_eins` | Other identifiers the auditee says the audit covers |
| `passthrough`, `secondary_auditors` | Who passed money to the auditee; other audit firms involved |

**What it can't tell you.**
- Whether an audit *should* exist. "No FAC record" doesn't mean "no audit
  required."
- Whether its own identifiers are right. FAC doesn't validate UEIs or EINs
  against anything else; most entries in the `additional_*` lists never appear
  elsewhere in FAC.
- Which affiliates the audit covers, when only the PDF or notes say so.
- Whether the free text is accurate. Findings, CAPs, and notes are written by
  auditors and auditees; we treat them as untrusted input.

**What to watch for.** FAC doesn't validate identifiers, so these are common:
- UEIs mistyped by one character, usually look-alikes (5/S, 2/Z, 6/G, 8/B),
  mostly in 2022, the first year after UEIs replaced DUNS numbers. Typos of two
  or more characters exist too; only a shared EIN reveals them.
- One UEI reporting different EINs in different years, one digit apart. Neither
  "latest" nor "most common" reliably picks the real one.
- Placeholders: `GSA_MIGRATION` and `XXXXXXXXXXXX` as UEIs, `111111111` as an
  EIN. Entities sharing one aren't related.
- `auditee_state` is unreliable: some filings carry the audit firm's state.
- A parent's list of covered entities can vanish from one year to the next
  while the audit text still covers them.
- `fac_accepted_date` can be years after the fiscal year ended.
- FAC publishes its own known issues (duplicate submissions, migrated dates off
  by a day, incorrect identifiers) at fac.gov.

**Access.** Free API key from api.data.gov; bulk CSV downloads; PDFs without a
key.

### SAM.gov

**Who runs it.** GSA. Every organization that wants to receive federal awards
must register and renew yearly; registration is where its UEI comes from.

**What's useful to us.** Whether a UEI exists and is active, the legal and
"doing business as" names, physical address, fiscal-year end (month and day),
entity structure (corporation, nonprofit, government), what the entity reports
about its tax status, and whether it has an **exclusion** (it's barred from
receiving federal money).

**What it can't tell you.** Parent/subsidiary hierarchy and EINs are
controlled data, not public, so our public key doesn't return them.
A UEI can be real and still unregistered ("ID Assigned"), or hidden from
public display, so a missing registration is not by itself a typo. Names are self-reported and can differ from FAC and IRS.

**What to watch for.**
- A UEI SAM.gov has no record of is a typo only when other evidence says so.
  One character from a UEI of the same organization: 594 of 594 were typos.
  With no other UEI of its EIN in SAM.gov: 48% were real, 9% typos. See
  [caveat-evidence.md](caveat-evidence.md).
- Registered names differ from FAC names (about a third by more than
  punctuation). Exact-name search fails on abbreviated legal names; free-text
  search with a state works better.
- `fiscalYearEndCloseDate` is month and day only ("06/30"), no year.
- At most 10 records per request; about 1,000 requests a day.

**Access.** Personal API key, regenerated every 90 days. The best use is
confirming a specific UEI, not browsing.

### USAspending

**Who runs it.** Treasury, from agency financial and award systems.

**What's useful to us.** Prime awards to a UEI by agency, program, and year;
obligations and, separately, **outlays by period**, which show when money was
actually paid out; and recipient profiles, which group UEIs into parent and
child recipients.

**What it can't tell you.** Expenditure as the audit measures it (outlays are
a proxy). Most pass-through money (it's an award to the pass-through entity,
not the subrecipient). PRF payments to the grantee's UEI.

**What to watch for.** Obligation dates don't say which fiscal year money was
spent in; a $1M grant obligated in September 2023 was drawn February–May 2025.
Use outlays by period, not obligations, for threshold questions.

**Access.** No key.

### IRS exempt-organization records

**Who runs it.** The IRS Tax Exempt and Government Entities division.

**What's useful to us.**
- The **Exempt Organizations Business Master File (EO BMF)**: every
  organization with current federal tax exemption, by EIN, with legal name,
  address, exemption type (for example 501(c)(3)), ruling date, and group
  exemption number. It's the outside check that tells us whether an EIN in FAC
  is real and whose it is.
- **Form 990 Schedule R**: the related organizations a nonprofit filing the
  full Form 990 reports each year, with their EINs: disregarded entities,
  related tax-exempt organizations, and related partnerships, corporations,
  and trusts. FAC's covered-entity lists are the other structured source of
  relationships, and SAM.gov's public data has none.
- The **auto-revocation list**: organizations whose exemption was revoked
  automatically for not filing a Form 990 for three consecutive years, with
  revocation and reinstatement dates. It explains an EIN missing from the
  EO BMF: a revoked organization is removed until it is reinstated.

**What it can't tell you.** Anything about for-profits or most government
bodies, which aren't in it. A missing EIN isn't proof of an error. Schedule R
comes only with the full Form 990 (not 990-EZ, 990-PF, or 990-N), and
group-exemption members need not list each other.

**What to watch for.** Checked against FAC's nonprofit auditees, it confirmed
86% of EINs by name and found about 270 likely EIN typos, 100 of which nothing
inside FAC could catch. Organizations under a group exemption are sometimes
listed under the parent's name. Schedule R is the filer's own account: lists
change from year to year and EINs are typed by hand, so check a related EIN
against the EO BMF before searching FAC by it.

**Access.** Bulk downloads only. The revocation list is updated monthly; the
EO BMF is a cumulative file the IRS reposts periodically (most recently
2026-11-09). Form 990s are posted as XML in monthly zips, with a yearly index.
That's why the design uses a local index rather than live calls.

Sources we may add later: the HHS-OIG exclusion list (LEIE).

---

## 4. How the sources connect

```
                  UEI                         UEI
   SAM.gov  ◄──────────────►   FAC   ◄──────────────►  USAspending
 (who is it,                (audits,                 (awards, when
  is it active,              findings,                money was paid)
  excluded?)                 SEFA, PDFs)                   │
                               │  ▲                        │ ALN, award ID
                           EIN │  │ notes and PDF text     │
                               ▼  │ name the affiliates    ▼
                       IRS EO BMF and             FAC federal_awards
                       revocation list            (same program in
                       (is the EIN real,           the SEFA)
                        whose is it?)
```

- **UEI** links all three federal systems, but only if the UEI in FAC is right.
- **EIN** links FAC to IRS records and, inside FAC, finds filings under a
  different or mistyped UEI. It's the most reliable key we have.
- **Program number and award ID** link a USAspending award to a line in a SEFA.
- **Schedule R** links a nonprofit parent's EIN to its subsidiaries' and
  affiliates' EINs, which then find their FAC filings. It says who is
  related, not whose audit covers whom.
- **Text** is the only link for many parent/subsidiary relationships: the SEFA
  notes and the audit PDF name the affiliates, usually without IDs.
- **Name and state** are a last resort. Names vary across systems and FAC's
  state field is unreliable, so a name match needs corroboration.
