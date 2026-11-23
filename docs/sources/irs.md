# IRS exempt-organization records

Public files from the IRS Tax Exempt and Government Entities division. Not
used by DAT at all; the research found them to be the only outside check on
FAC's identifiers, and they turn out to hold two more things we need:
fiscal-year end and group-exemption structure.

- **Exempt Organizations Business Master File (EO BMF):** every organization
  with current federal tax exemption, by EIN. 1,984,891 records as of
  2026-11-09, extracted monthly. Four region files (`eo1.csv`–`eo4.csv`), plus
  one file per state and DC (`eo_xx.csv`), `eo_pr.csv`, and an international
  file. All under `https://www.irs.gov/pub/irs-soi/`, no key.
  <https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf>
- **Auto-revocation list:** organizations whose exemption was revoked for not
  filing Form 990 for three years. Pipe-delimited, about 1.25M rows, updated
  monthly (second Tuesday).
  <https://www.irs.gov/charities-non-profits/tax-exempt-organization-search-bulk-data-downloads>
- **Form 990-N (e-Postcard) file:** small organizations' names, other names
  used, principal officer, website, and tax year. Updated weekly. Same page.
- **Form 990 series e-file XML:** full returns, including Schedule R, as
  monthly zips with yearly index CSVs (2019–2026 on irs.gov).
- Access: bulk downloads only, no key. The MCP design keeps a local copy.

## What the MCP uses

- **EO BMF**, all four region files (`eo1.csv`–`eo4.csv`, 1,984,891 rows on
  2026-11-09), loaded into the `eo_bmf` table with the IRS's column names.
  `irs_search_exempt_orgs` looks up EINs and searches `NAME` and `SORT_NAME`
  by word, decodes `SUBSECTION`, `STATUS`, `FOUNDATION`, `AFFILIATION`, and
  `FILING_REQ_CD`, and names a group member's central organization. Given an
  EIN and a name together, it says whether the EIN's IRS name matches and
  lists what the IRS has under that name.
- **Form 990 Schedule R Parts I–IV** for the EINs in
  `scripts/schedule_r_eins.txt`, found through the 2024–2026 e-file indexes
  and read by `scripts/fetch_schedule_r.py` into `form990_returns` and
  `schedule_r`. `irs_get_related_orgs` returns them by relationship, marks
  each organization not listed on every stored return with the years it
  was (`tax_years_listed`), lists those dropped from the year shown
  (`not_in_year_shown`), and gives which filers list a searched EIN. That
  includes a filer listing it one digit off, when the IRS has no record of
  the EIN as typed and the listed name matches the searched EIN's
  (`ein_as_listed`): S16's subsidiary lists its parent as 431036725 on every
  return. Part V (transactions) and Part VI
  (unrelated partnerships) aren't loaded.
- **`grantee_identity_resolution`** checks every EIN it finds against the EO BMF (whether
  the IRS lists it, and whether each name FAC filings used under it matches
  the IRS name: every word of the shorter name must match, allowing the IRS's
  abbreviations like CTR and SVCS, so Baylor Scott & White Health filing
  under Holdings' EIN is flagged), adds the IRS
  fiscal-year-end month as a source, and lists Schedule R filers that report
  the grantee as related, flagging gap years and parents that disagree (see
  `docs/mcp-design.md`).
- **Automatic revocation list**, all 1,247,443 rows (posted 2026-09-08),
  converted by `scripts/convert_revocations.py` into `irs_revocations` with
  ISO dates. For an EIN not in the EO BMF, `irs_search_exempt_orgs` and
  `grantee_identity_resolution` report each revocation with its posting and any
  reinstatement date. A revocation dated 2020-04-01 to 2020-07-14 keeps its
  posted date and gets `corrected_revocation_date` 2020-07-15, per the IRS.
  19,351 EINs appear more than once (revoked again after reinstatement).
- Citations go to the organization's Tax Exempt Organization Search page,
  `https://apps.irs.gov/app/eos/details/?ein={ein}`, which opens in a browser
  (scripts get HTTP 403) and lists its e-filed returns.

## What the MCP could use

| Data | What it adds |
|---|---|
| EO BMF by `EIN` (`NAME`, `SORT_NAME`, `CITY`, `STATE`) | Is this EIN real, and whose is it? Checked against FAC's nonprofit auditees it confirmed 86% by name and found about 270 likely EIN typos, 100 of which nothing inside FAC could catch. |
| EO BMF by name and state | Turns subsidiary names from audit PDFs into EINs, which then find their FAC filings. `SORT_NAME` holds trade names and chapter names. |
| EO BMF `ACCT_PD` | **The organization's fiscal-year-end month.** A third source for fiscal year alongside FAC and SAM.gov. `TAX_PERIOD` (YYYYMM of the latest return) shows how recently it filed. |
| EO BMF `GROUP` and `AFFILIATION` | **Group-exemption structure.** `AFFILIATION` 6 or 8 marks a group-ruling parent, 9 a subordinate; subordinates share the parent's `GROUP` number. The only bulk source of this, since Schedule R doesn't require group members to be listed. `FILING_REQ_CD` 03 means the organization is covered by a group return. |
| EO BMF `STATUS`, `FOUNDATION`, `RULING`, `REVENUE_AMT`, `ASSET_AMT` | Anything but `STATUS` 01 (unconditional exemption) is worth flagging. `FOUNDATION` 17/21–24 marks a supporting organization, which depends on another entity by definition. Revenue and assets give size. |
| Revocation list (`TIN`, `Organization Name`, `Sort Name`, `Sub Section Code`, `Revocation Date`, `Revocation Posting Date`, `Exemption Reinstatement Date`) | Grantees that report as nonprofits but have lost their exemption and keep spending federal money. After filtering, about 50 private nonprofits remain as leads, 8 spending HHS money. A lead for an analyst, not a finding. A reinstatement date means currently exempt. |
| 990 e-file index CSVs (EIN, tax period, submission date, name) | A filing history and fiscal-year check without parsing XML. |
| 990 Schedule R (in the XML) | Related organizations with EINs, by relationship: disregarded entities, related exempt organizations, partnerships, corporations, and transactions between them. Form 990 filers only. |

## Limits and quirks

- **No for-profits**, including many hospital LLCs, and few government bodies.
  A missing EIN isn't proof of an error.
- **Address is the filing address.** The IRS says it "may or may not represent
  the state(s) in which an organization has operations."
- Revoked organizations drop out of the EO BMF, so an EIN missing from it
  should be checked against the revocation list.
- Of 352 FAC auditee EINs (2022-2025) on the revocation list and not in the
  EO BMF, 205 are government bodies, 99 private nonprofits under the same
  name, 28 under another name, 10 Puerto Rico organizations, 6 LLCs and 4 EIN
  typos. The list excludes revocations from IRS examination, and
  revocation dates between April 1 and July 14, 2020 should read July 15, 2020.
- **Schedule R needn't show group-exemption parents or subordinates**; the
  instructions don't require them to be listed as related organizations
  (some filers list them anyway; see below). Use the EO BMF
  `GROUP`/`AFFILIATION` fields for those. Schedule R is only filed
  with the full Form 990, not 990-EZ or 990-PF.
- Related entities can have consecutive EINs, so "one digit off" is not
  always a typo. Of 255 FAC EINs missing from the EO BMF and one digit from an
  IRS EIN under a matching name, 7 had a neighbor that files to FAC as a
  separate organization; for 159 the same entity also filed under it.
- **The BMF writes names its own way:** no apostrophes (`LOUISIANA
  CHILDRENS`), AND spelled out, heavy abbreviation, while the e-file index
  keeps apostrophes. Name search drops apostrophes and treats `&`, `and`,
  `inc`, `incorporated`, `corp`, `corporation`, `co`, `the`, and `of` as
  optional.
- **Group members can still appear in Schedule R.** Covenant Health and St.
  Joseph Hospital of Nashua are both subordinates in group 0928 (US Catholic
  Conference), and Covenant lists St. Joseph for 2023 and 2024. The
  instructions don't require it, so absence proves nothing either way.
- **Schedule R lists change and carry typos.** Betty Jean Kerr People's
  Health Centers lists People's Health Supportive Services only for tax year
  2024, not 2022 or 2023. People's Health Supportive Services lists its
  parent under EIN 431036725 every year 2022–2025; the IRS has no such EIN,
  and the parent's is 431036785.
- **Schedule R can be long.** Mercy Housing, Inc. lists about 490 related
  organizations a year, Baylor Scott & White Health 113.

### E-file download quirks

- Only the 2024 and later indexes (`index_{year}.csv`, by the year the IRS
  processed the return) have `XML_BATCH_ID`, the zip each return is in.
  Earlier years would mean scanning every zip's directory.
- `XML_BATCH_ID` isn't exact. Some are lowercase (`2024_TEOS_XML_05a`) where
  the file is uppercase; the lowercase URL redirects to the IRS 404 page. And
  a month split across zips (`2025_TEOS_XML_05A` and `05B`) lists every
  return under the first.
- The zips are 100–500 MB but serve byte ranges, so one return can be read
  without the rest. Some zips use Deflate64 (zip method 9), which Python's
  `zipfile` can't decompress; the `inflate64` package can.
- The index's `TAX_PERIOD` is the period's end (YYYYMM). The IRS's tax year
  is the year the period starts: a June 2024 year end is tax year 2023.
