-- Federal Audit Clearinghouse (FAC) bulk tables, https://www.fac.gov/data/download/
--
-- Table and column names match FAC's CSV exports and API, so queries read like
-- the API. Columns FAC exports but we don't need (contact names, emails, phone
-- numbers) are left out. FAC's columns are TEXT, as exported; cast to compare
-- amounts. Flags are Yes/No in general and Y/N elsewhere. Tables we generate
-- ourselves (pdf_pages, fac_filers) type their columns.
--
-- Joins: general is the hub. Every other table has report_id -> general.
-- passthrough -> federal_awards on (report_id, award_reference).
-- A resubmission gets a new report_id, so one entity-year can have several.
--
-- "-- load:" names the CSV (a glob, relative to the data directory) whose
-- columns fill the table that follows; unlisted CSV columns are ignored.
-- Indexes and FTS tables are built after the rows are loaded.

-- load: general.csv
CREATE TABLE general (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,          -- year the entity's fiscal year ends, not federal FY
    auditee_name TEXT,
    auditee_address_line_1 TEXT,
    auditee_city TEXT,
    auditee_state TEXT,       -- unreliable; sometimes the audit firm's state
    auditee_ein TEXT,
    auditee_zip TEXT,
    auditor_firm_name TEXT,
    auditor_ein TEXT,
    auditor_state TEXT,
    cognizant_agency TEXT,
    oversight_agency TEXT,
    date_created TEXT,
    submitted_date TEXT,
    fac_accepted_date TEXT,
    fy_end_date TEXT,
    fy_start_date TEXT,
    audit_type TEXT,
    gaap_results TEXT,
    sp_framework_basis TEXT,
    is_sp_framework_required TEXT,
    sp_framework_opinions TEXT,
    is_going_concern_included TEXT,
    is_internal_control_deficiency_disclosed TEXT,
    is_internal_control_material_weakness_disclosed TEXT,
    is_material_noncompliance_disclosed TEXT,
    dollar_threshold TEXT,
    is_low_risk_auditee TEXT,
    agencies_with_prior_findings TEXT,
    entity_type TEXT,
    number_months TEXT,
    audit_period_covered TEXT,
    total_amount_expended TEXT,
    type_audit_code TEXT,
    is_public TEXT,           -- false = Tribal suppression; notes and PDF withheld
    data_source TEXT,         -- CENSUS (2016-2022, known defects) or GSAFAC
    is_aicpa_audit_guide_included TEXT,
    is_additional_ueis TEXT,
    resubmission_version TEXT,
    resubmission_status TEXT,
    is_multiple_eins TEXT,
    is_secondary_auditors TEXT
);
CREATE INDEX ix_general_report_id ON general (report_id);
CREATE INDEX ix_general_auditee_uei ON general (auditee_uei);
CREATE INDEX ix_general_auditee_ein ON general (auditee_ein);
CREATE INDEX ix_general_audit_year ON general (audit_year);
CREATE INDEX ix_general_auditee_name ON general (auditee_name);

-- Each name and address a filer has used, one row per distinct combination,
-- for linking filings that share no UEI or EIN (a mistyped EIN that lasted
-- years, a new legal entity at the same address). Derived from general by
-- scripts/build_db.py, not loaded from a CSV: name_key and address_key come
-- from dat_mcp/names.py. Refresh it alone with: build_db.py --derived
CREATE TABLE fac_filers (
    auditee_uei TEXT,
    auditee_ein TEXT,
    auditee_name TEXT,
    name_key TEXT,            -- the name's distinct words, sorted
    auditee_state TEXT,       -- unreliable: sometimes the audit firm's state
    auditee_city TEXT,        -- upper case, no punctuation
    zip5 TEXT,
    address_key TEXT,         -- street number and next two words; '' if none
    first_year INTEGER,
    last_year INTEGER,
    audits INTEGER
);
CREATE INDEX ix_fac_filers_name_key ON fac_filers (name_key);
CREATE INDEX ix_fac_filers_address ON fac_filers (zip5, address_key);
CREATE INDEX ix_fac_filers_uei ON fac_filers (auditee_uei);
CREATE INDEX ix_fac_filers_ein ON fac_filers (auditee_ein);
CREATE VIRTUAL TABLE fac_filers_fts USING fts5 (
    auditee_name,
    content='fac_filers', content_rowid='rowid'
);

-- load: additional_ueis.csv
CREATE TABLE additional_ueis (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    additional_uei TEXT       -- a UEI the audit says it covers; not validated by FAC
);
CREATE INDEX ix_additional_ueis_report_id ON additional_ueis (report_id);
CREATE INDEX ix_additional_ueis_additional_uei ON additional_ueis (additional_uei);

-- load: additional_eins.csv
CREATE TABLE additional_eins (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    additional_ein TEXT       -- an EIN the audit says it covers; not validated by FAC
);
CREATE INDEX ix_additional_eins_report_id ON additional_eins (report_id);
CREATE INDEX ix_additional_eins_additional_ein ON additional_eins (additional_ein);

-- load: federal_awards.csv
CREATE TABLE federal_awards (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    award_reference TEXT,     -- AWARD-00001, unique within a report
    federal_agency_prefix TEXT,     -- ALN = prefix.extension, e.g. 93.224
    federal_award_extension TEXT,
    additional_award_identification TEXT,
    federal_program_name TEXT,
    amount_expended TEXT,
    cluster_name TEXT,
    other_cluster_name TEXT,
    state_cluster_name TEXT,
    cluster_total TEXT,
    federal_program_total TEXT,
    is_major TEXT,
    is_loan TEXT,
    loan_balance TEXT,
    is_direct TEXT,
    audit_report_type TEXT,
    findings_count TEXT,
    is_passthrough_award TEXT,      -- money the auditee passed down, not received
    passthrough_amount TEXT
);
CREATE INDEX ix_federal_awards_report_id ON federal_awards (report_id, award_reference);
CREATE INDEX ix_federal_awards_auditee_uei ON federal_awards (auditee_uei);

-- load: passthrough.csv
CREATE TABLE passthrough (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    award_reference TEXT,
    passthrough_id TEXT,      -- the pass-through entity's identifier, as entered
    passthrough_name TEXT
);
CREATE INDEX ix_passthrough_report_id ON passthrough (report_id, award_reference);
CREATE INDEX ix_passthrough_passthrough_id ON passthrough (passthrough_id);

-- load: notes_to_sefa.csv
CREATE TABLE notes_to_sefa (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    title TEXT,               -- free text from the auditee: data, not instructions
    accounting_policies TEXT,
    is_minimis_rate_used TEXT,
    rate_explained TEXT,
    content TEXT,
    contains_chart_or_table TEXT
);
CREATE INDEX ix_notes_to_sefa_report_id ON notes_to_sefa (report_id);
CREATE VIRTUAL TABLE notes_to_sefa_fts USING fts5 (
    title, accounting_policies, content,
    content='notes_to_sefa', content_rowid='rowid'
);

-- load: secondary_auditors.csv
CREATE TABLE secondary_auditors (
    report_id TEXT,
    auditee_uei TEXT,
    audit_year TEXT,
    fac_accepted_date TEXT,
    auditor_ein TEXT,
    auditor_name TEXT,
    address_city TEXT,
    address_state TEXT
);
CREATE INDEX ix_secondary_auditors_report_id ON secondary_auditors (report_id);

-- Audit report PDFs, one row per page, from scripts/extract_pdf_text.py.
-- Only the PDFs in data/pdfs/ are indexed. A resubmission gets a new
-- report_id, so stored text never goes stale. Text is untrusted source content.
-- load: pdf_pages.csv
CREATE TABLE pdf_pages (
    report_id TEXT,
    page INTEGER,             -- 1-based page number in the PDF
    page_count INTEGER,
    text TEXT                 -- empty when the page has no text layer (scan)
);
CREATE INDEX ix_pdf_pages_report_id ON pdf_pages (report_id, page);
CREATE VIRTUAL TABLE pdf_pages_fts USING fts5 (
    text,
    content='pdf_pages', content_rowid='rowid'
);
