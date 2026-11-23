-- IRS exempt-organization data.
--
-- eo_bmf is the Exempt Organizations Business Master File, as the IRS posts it:
-- https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf
-- Download eo1.csv-eo4.csv (four regions, every state plus international)
-- into the data directory. Column names are the IRS's, so queries read like
-- its field guide (https://www.irs.gov/pub/foia/ig/tege/eo-info.pdf); GROUP is
-- an SQL keyword, so quote it. Columns are TEXT, as posted. ICO (in-care-of,
-- usually a person), ACTIVITY (retired codes), and the asset and income
-- ranges (the amounts are here) are left out.
--
-- form990_returns and schedule_r come from scripts/fetch_schedule_r.py, which
-- finds returns for chosen EINs in the 990 e-file index and reads each full
-- Form 990's Schedule R. They hold only those EINs. Their columns are typed.
--
-- Joins: eo_bmf.EIN = form990_returns.ein = schedule_r.filer_ein, and
-- schedule_r.ein is the related organization's EIN (blank when not reported).

-- load: eo[1-4].csv
CREATE TABLE eo_bmf (
    EIN TEXT,
    NAME TEXT,
    STREET TEXT,
    CITY TEXT,
    STATE TEXT,             -- filing address; not necessarily where it operates
    ZIP TEXT,
    "GROUP" TEXT,           -- group exemption number; 0000 = none
    SUBSECTION TEXT,        -- 03 = 501(c)(3)
    AFFILIATION TEXT,       -- 6 or 8 = group's central organization, 9 = subordinate
    CLASSIFICATION TEXT,
    RULING TEXT,            -- YYYYMM of the exemption ruling
    DEDUCTIBILITY TEXT,
    FOUNDATION TEXT,        -- 17, 21-24 = supporting organization
    ORGANIZATION TEXT,
    STATUS TEXT,            -- 01 = unconditional exemption
    TAX_PERIOD TEXT,        -- YYYYMM of the latest return filed
    FILING_REQ_CD TEXT,     -- 03 = covered by a group return
    PF_FILING_REQ_CD TEXT,
    ACCT_PD TEXT,           -- month the fiscal year ends, 01-12
    ASSET_AMT TEXT,
    INCOME_AMT TEXT,
    REVENUE_AMT TEXT,
    NTEE_CD TEXT,
    SORT_NAME TEXT          -- secondary name: trade, chapter, or local name
);
CREATE INDEX ix_eo_bmf_ein ON eo_bmf (EIN);
CREATE INDEX ix_eo_bmf_group ON eo_bmf ("GROUP", AFFILIATION);
CREATE VIRTUAL TABLE eo_bmf_fts USING fts5 (
    NAME, SORT_NAME,
    content='eo_bmf', content_rowid='rowid'
);

-- One row per return found in the e-file index for a fetched EIN: the latest
-- filing of each form for each tax period, so an amended return replaces the
-- original. 990-T (unrelated business income) is left out.
-- load: form990_returns.csv
CREATE TABLE form990_returns (
    ein TEXT,
    name TEXT,                -- as filed
    tax_year INTEGER,         -- the year the fiscal year starts, as the IRS counts it
    tax_period_end TEXT,      -- YYYY-MM
    form TEXT,                -- 990, 990EZ, or 990PF; only 990 has Schedule R
    object_id TEXT,           -- the e-file return's id
    xml_batch_id TEXT,        -- the zip the index names for it
    has_schedule_r TEXT       -- Y or N for a 990; blank for other forms
);
CREATE INDEX ix_form990_returns_ein ON form990_returns (ein, tax_year);

-- Schedule R Parts I-IV, one row per related organization per return.
-- Names and activities are as the filer wrote them.
-- load: schedule_r.csv
CREATE TABLE schedule_r (
    filer_ein TEXT,
    tax_year INTEGER,
    object_id TEXT,
    part TEXT,                -- I, II, III, or IV
    relationship TEXT,        -- the part's heading, e.g. related tax-exempt organization
    name TEXT,
    ein TEXT,
    state TEXT,               -- US address state, blank if foreign
    primary_activity TEXT,
    legal_domicile TEXT,      -- state or country of incorporation
    exempt_code_section TEXT, -- Part II, e.g. 501(c)(3)
    entity_type TEXT,         -- Part IV: corporation or trust
    direct_controlling_entity TEXT,
    controlled TEXT           -- Parts II and IV: Y or N; blank in Parts I and III
);
CREATE INDEX ix_schedule_r_filer ON schedule_r (filer_ein, tax_year);
CREATE INDEX ix_schedule_r_ein ON schedule_r (ein);

-- The IRS's automatic revocation list: organizations whose exemption was
-- revoked for not filing a Form 990 series return or notice for three
-- consecutive years, from scripts/convert_revocations.py. One row per
-- revocation, so an EIN revoked, reinstated, and revoked again has several.
-- A revoked organization drops out of eo_bmf until it is reinstated.
-- Dates are ISO (YYYY-MM-DD). The IRS says revocation dates from 2020-04-01
-- to 2020-07-14 should read 2020-07-15; they are kept as posted.
-- load: irs_revocations.csv
CREATE TABLE irs_revocations (
    ein TEXT,
    name TEXT,
    secondary_name TEXT,
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT,
    subsection TEXT,          -- 03 = 501(c)(3)
    revocation_date TEXT,
    revocation_posting_date TEXT,  -- when the IRS published it
    reinstatement_date TEXT   -- blank when not reinstated
);
CREATE INDEX ix_irs_revocations_ein ON irs_revocations (ein);
