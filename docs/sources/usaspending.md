# USAspending

Treasury's public record of federal awards, built from agency financial and
award systems. Organized around prime awards to a recipient UEI.

- API: `https://api.usaspending.gov/api/v2/`
- Docs: <https://api.usaspending.gov/>
- Access: no key or auth is mentioned anywhere in the API contracts. No rate
  limit or maximum page size is documented for search endpoints.

## What DAT uses

`POST search/spending_by_category/cfda`: HHS obligations by program for each
UEI and federal fiscal year, compared against the audit threshold to decide
whether an audit was required.

## What the MCP could use

| Endpoint | What it adds |
|---|---|
| `POST search/spending_by_award_count/` | How many awards a UEI has in each group (grants, direct payments, loans, contracts, other). A cheap first call before fetching details. |
| `POST search/spending_by_award/` | A recipient's awards (`recipient_search_text` takes one UEI or name) with `Award ID` (the FAIN), `Award Amount`, `Total Outlays`, `Start Date`, `End Date`, `CFDA Number`, `Awarding Agency`, `Recipient UEI`, `def_codes`, `COVID-19 Obligations`/`Outlays`. FAINs match lines in the FAC SEFA. |
| `POST search/spending_by_award/` with `spending_level: "subawards"` | Subawards, with `Sub-Recipient UEI`, `Sub-Award Amount`, `Prime Award ID`, and `Prime Award Recipient UEI`. The only route to pass-through money in USAspending. |
| `POST awards/funding/` | **Obligations and outlays by accounting period** (`reporting_fiscal_year`, `reporting_fiscal_month`, `transaction_obligated_amount`, `gross_outlay_amount`, `federal_account`). Shows when money was actually drawn: a $1M HRSA grant obligated in September 2023 was drawn February–May 2025. |
| `GET awards/{generated_id}/` | Award detail: `recipient` (name, UEI, `parent_recipient_uei`, `parent_recipient_name`, `business_categories`), `period_of_performance`, `total_obligation`, `total_account_outlay`, `cfda_info[]` (program title, agency, objectives), `subaward_count`, `funding_opportunity`. |
| `POST recipient/`, `GET recipient/{id}/`, `GET recipient/children/{uei}/` | Recipient profiles with `recipient_level` (`P` parent, `C` child, `R` neither), `parent_uei`, `parents[]`, and the list of child recipients under a parent UEI. Another view of organizational structure, from the parent UEI reported on awards. |
| `GET references/assistance_listing/{code}` | ALN codes and descriptions under an agency prefix (no agency field; use `cfda_info` on the award for that). |

## Limits and quirks

- **Obligations aren't expenditures.** The audit threshold is measured on what
  the entity spent in its own fiscal year. USAspending reports by federal
  fiscal year, and obligations are commitments, not spending. Outlays are the
  closest public proxy.
- **`gross_outlay_amount` is a running total, not a period amount.** It is
  cumulative within one federal fiscal year and resets each October, and it
  is tracked separately for each funding source (each period of
  availability). Adding the rows up counts the same dollars once per period.
  What was drawn in a period is the rise since the previous reported period
  of the same fiscal year and funding source. `awards/funding/` does not
  return the TAS that separates those sources, so where one federal account
  reports several rows for the same period, only their total behaves as a
  running total; a source that stops reporting shows up as a negative.
- **`Award Amount` and `Total Outlays` in `spending_by_award` are lifetime
  totals** for the whole award, not figures for a year. The `time_period`
  filter chooses which awards come back; it does not apportion their amounts.
  Use `awards/funding/` for anything time-bounded.
- **`awards/funding/` caps `limit` at 100.** Page through it rather than
  asking for more, and page from the oldest period, because a period's
  outlay is only meaningful next to the periods before it in the same year.
  A page is 100 rows, not 100 periods: the endpoint splits each period by
  federal account, object class, program activity and DEFC, so a complex
  award can report dozens of rows a month and a long history can run past
  any sane paging cap. Paging oldest-first means it is the newest periods
  that go missing, and the fiscal year they fall in is then overstated in
  its first surviving period, so a server that stops paging has to say so.
- **Outlay reporting was only required from FY2022** (FY2020 for COVID
  supplemental awards). Earlier awards may show obligations with no outlays.
- **Provider Relief Fund and pass-through money don't appear** as prime awards
  to the grantee's UEI, so spending totals undercount for hospitals and
  subrecipients. Subaward search helps for pass-through money that was
  reported to FSRS.
- `award_type_codes` is required in `spending_by_award` and codes are OR'd,
  so mixing groups is allowed. But type-specific fields (`CFDA Number`,
  `Award Type`, `Loan Value`, `NAICS`) are only valid for their group, so query
  one group at a time when requesting those fields. Grants are `02`–`05`,
  direct payments `06` and `10`, loans `07`–`08`, other assistance `09` and `11`.
- **Each group also has F-series codes, and recent awards carry only those.**
  `GET references/award_types/` lists them: grants add `F001` and `F002`,
  loans `F003` and `F004`, direct payments `F006` and `F007`, other assistance
  `F005`, `F008`–`F010` and `-1`. Current HRSA Health Center Program awards are
  `F001`, so a grants search on `02`–`05` alone returned a fraction of a
  health center's money, with no error. The award detail endpoint still calls
  them "PROJECT GRANT (B)".
- **Loans take different fields.** `spending_by_award` has no `Award Amount`,
  `Total Outlays`, `Start Date` or `End Date` for loans and returns HTTP 400
  if asked for them or sorted by them; use `Loan Value`, `Subsidy Cost` and
  `Issued Date`.
- `time_period` has an odd default: with no `date_type`, `action_date` is
  compared to `start_date` and `date_signed` to `end_date`. Set `date_type`
  explicitly. Earliest allowed date is 2007-10-01.
- `recipient_search_text` takes exactly one value. `recipient_id` is a
  response field, not a filter.
- `awards/funding_rollup/` has no outlay total. The award detail endpoint
  `GET awards/{generated_id}/` has `total_account_outlay`, but it is a
  lifetime figure like the others; the MCP sums periods instead and so makes
  no GET calls to USAspending at all.
- Award descriptions are often too vague to say what the money was for; the
  SEFA and the Assistance Listing say more.
