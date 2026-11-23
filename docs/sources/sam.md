# SAM.gov

The registry every organization must join to receive federal awards. It is
where UEIs come from. Run by GSA.

- Entity API: `https://api.sam.gov/entity-information/v4/entities` (v3 is
  identical for our purposes; v4 adds one reps-and-certs field)
- Exclusions API: `https://api.sam.gov/entity-information/v4/exclusions`
  (v1–v3 retired September 2024)
- Assistance Listings API: `https://api.sam.gov/assistance-listings/v1/search`
- Docs: <https://open.gsa.gov/api/entity-api/>
- Browser source links open [Entity Information](https://sam.gov/entity-information?v=1).
  Search there using the record's UEI; the link does not select the entity.
  The former `/entity/{uei}/coreData` links were reported to return 404.

## What DAT uses

One field: `coreData.entityInformation.fiscalYearEndCloseDate`, to get a
grantee's fiscal-year end when FAC doesn't have one.

## What the MCP could use

All public tier unless noted. Section paths are under `entityData[]`.

| Data | What it adds |
|---|---|
| Whether a UEI is registered (`entityRegistration.ueiSAM`, `registrationStatus`, `registrationExpirationDate`, `samRegistered`, `ueiStatus`) | Tells a typo from a real UEI. Every mistyped UEI we checked has no record at all, registered or not. A real UEI can be unregistered: 2,014 UEIs on FAC audits are `ID Assigned` (`samRegistered=No`), including Baylor Scott & White Health's `XRJ2DMLNMM34` and City of Hope's `KM7XTKHC9D19`. A UEI with no record at all is not proof of a typo either: 41% of 1,904 were typos, but all 594 one character from a UEI SAM.gov has. See [caveat-evidence.md](../caveat-evidence.md). |
| `legalBusinessName`, `dbaName`, `coreData.physicalAddress`, `mailingAddress`, `entityInformation.entityDivisionName` | Places an entity when FAC's name or state is wrong. About a third of registered names differ from the FAC name by more than punctuation. |
| `coreData.generalInformation.profitStructureCode` (`A8` nonprofit, `2U` other not-for-profit, `2X` for-profit), `entityStructureCode` (`8H` tax-exempt corporation, `2L` not tax-exempt), `coreData.businessTypes.businessTypeList[]` | What the entity says about itself. Can be compared with IRS records (one grantee reports tax-exempt; the IRS revoked it in 2022). `8H` and `2X` can appear together, so the fields are independent self-reports. |
| `entityRegistration.purposeOfRegistrationCode` (`Z1`–`Z5`) | Whether the entity registered for assistance awards, contracts, or both. |
| `entityRegistration.exclusionStatusFlag` (`Y`/`N`) and `exclusionURL` | Whether an active exclusion exists, without a second API call. |
| `coreData.financialInformation.debtSubjectToOffset` (`Y`/`N`) | Delinquent federal debt referred to Treasury offset. A risk signal. |
| `integrityInformation` (request it explicitly with `includeSections=integrityInformation`) | Self-reported proceedings, terminations for cause including grant IDs, and `corporateRelationships`: highest and immediate owner by CAGE code and name. The only ownership data in the public tier. Also returns entities that opted out of public display. |
| `repsAndCerts.financialAssistanceCertifications` | The 2 CFR grants certification and who signed it. |
| Free-text search (`q=` with AND/OR/NOT) and filters like `legalBusinessName`, `dbaName`, `physicalAddressProvinceOrStateCode`, `physicalAddressCity`, `businessTypeCode`, `entityStructureCode` | Finds UEIs for subsidiaries named in an audit PDF. Partial name matches work; exact legal-name search fails on abbreviated names. |
| Exclusions API | Exclusion type, program, excluding agency, dates, and `moreLocations[]` and cross-references that link related excluded parties. Only active records. Daily public extract available. |
| Assistance Listings API (`assistanceListingId=93.224`) | Program title, agency, objectives, and `compliance.audit` text for an ALN. |

## Limits and quirks

- **No UEI-keyed parent/child hierarchy and no EINs** in the public tier. The
  parent chain (`entityHierarchyInformation`) is FOUO and TIN is Sensitive;
  both need a federal system account. The public bulk extract has no parent
  columns at all. Organizational structure has to come from
  `integrityInformation.corporateRelationships`, audit text, and IRS records.
- Default search returns registered entities only (`samRegistered=Yes`);
  `samRegistered=No` returns UEIs that were assigned but never registered.
  Both SAM tools ask for both, so an unregistered UEI isn't reported as
  missing. The `registrationStatus` filter takes only `A` or `E`.
- Organizations can opt out of public display (`publicDisplayFlag`); such a
  UEI has no public record at all.
- Separate multiple UEIs with `~` inside brackets, up to 100:
  `ueiSAM=[UEI1~UEI2]`. Bracketed commas are the date-range syntax.
- `fiscalYearEndCloseDate` is month and day only ("06/30"). `entityStartDate`
  is the founding date, not a fiscal-year start.
- `includeSections` takes `entityRegistration`, `coreData`, `assertions`,
  `pointsOfContact`, `repsAndCerts`, `integrityInformation`, or `All`. `All`
  excludes `integrityInformation`, and `repsAndCerts` must be asked for.
- The public entity extract is **monthly** (first Sunday); daily entity
  extracts are FOUO and Sensitive only. The public exclusions extract is daily.
- Records last updated before April 2022 carry `evsSource: D&B`, with D&B
  Open Data license terms attached.
- On HRSA's network, Windows `curl` fails certificate revocation checks;
  `--ssl-no-revoke` skips only that check.
