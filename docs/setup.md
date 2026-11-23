# Developer setup

Getting a working copy running, then adding to it. For why the code is built
the way it is, see [mcp-design.md](mcp-design.md).

## 1. Install

Requires Python 3.12+. From the repo root:

```bash
python -m venv .venv
source .venv/bin/activate      # Windows (Git Bash): source .venv/Scripts/activate
pip install -e ".[dev]"        # add ,eval for the eval: ".[dev,eval]"
```

## 2. API keys

```bash
cp .env.example .env
```

Fill in your own keys; `.env` is gitignored.

| Variable | Where to get it |
|---|---|
| `FAC_API_KEY` | Free from <https://api.data.gov/signup/> |
| `SAM_API_KEY` | Your SAM.gov profile, Account Details, "Public API Key" |

USAspending and the IRS bulk files need no key.

The SAM.gov key lasts about 90 days; when SAM tools start returning HTTP 403,
get a new one from the same page. Use a production key (SAM.gov), not one
from alpha.sam.gov. A federal personal key allows 1,000 requests a day, and
one SAM tool call can use up to 5.

## 3. Register the server with Claude Code

The repo's `.mcp.json` registers the server as `dat-mcp`. Open a new Claude
Code session in the repo and approve the project server when asked, then type
`/mcp` to confirm it's connected and its tools are listed.

- Claude Code loads servers when a session starts; start a new session after
  changing a tool.
- macOS or Linux: `.mcp.json` uses the Windows venv path, so register with
  `claude mcp add dat-mcp -- .venv/bin/python -m dat_mcp.app` instead.
- To call tools by hand: `npx @modelcontextprotocol/inspector .venv/Scripts/python -m dat_mcp.app`

Try: "Has Inland Behavioral and Health Services (UEI JQNLHK8YLTU6) filed a
Single Audit for its fiscal year ending June 30, 2025?"

Claude Code chats also see CLAUDE.md, memory, and repo docs that contain the
scenarios' answers, so use the eval (step 5) to measure the tools.

## 4. Test and lint

```bash
pytest
ruff check .
```

Tests replace the APIs with fakes, so they run offline.

## 5. Run the eval

The eval asks Claude on Amazon Bedrock each question in `eval/cases.json`,
with only this server's tools, and saves the answers to `eval/runs/`
(gitignored). You need your own AWS SSO profile with Bedrock access.

```bash
aws sso login --profile <your-bedrock-profile>
AWS_PROFILE=<your-bedrock-profile> python eval/run_eval.py              # every case
AWS_PROFILE=<your-bedrock-profile> python eval/run_eval.py s1-typo-uei  # one case
AWS_PROFILE=<your-bedrock-profile> python eval/run_eval.py s1 sam       # ids starting s1-, sam-
```

A case costs a few cents. Expected answers are pinned to live FAC data and go
stale as audits are filed or resubmitted; re-check one before treating a
failure as a tool bug.

`DAT_EVAL_MODEL=us.anthropic.claude-haiku-5-5` runs the same cases on Haiku
for about a fortieth of the cost, which is enough to check whether a tool
change moved any case. Grade a saved run, and compare it with an earlier one:

```bash
AWS_PROFILE=<your-bedrock-profile> python eval/grade_eval.py eval/runs/<new>.json
python eval/grade_eval.py eval/runs/<new>.json --against eval/runs/<old>.json
```

Opus grades each answer pass, partial, or fail against the case's expected
answer and writes the grades into the run file.

The `safety-` cases (`python eval/run_eval.py safety`) check that the agent
makes no compliance calls and ignores instructions in source text. What they
test and how is in [safety-eval.md](safety-eval.md). Each run's results go
in `docs/safety-results/`, which is gitignored: don't push them to the
repository for now.
`--markdown` prints a graded run as a table to paste into a ticket.

## 6. Build the local database (optional)

Some tools read a local SQLite copy of bulk data for searches the APIs can't
do (near-duplicate UEIs and EINs, text search), for IRS records, which have
no API, and so evals run against a dated snapshot. Each person builds their
own; the file is gitignored.

1. Download the CSVs into `data/`: FAC's from <https://www.fac.gov/data/download/>
   (the tables named in `scripts/schema/fac.sql`, about 2.7 GB).
2. For audit PDF search, save PDFs named by report ID in `data/pdfs/` (from
   `https://app.fac.gov/dissemination/report/pdf/{report_id}`) and run
   `python scripts/extract_pdf_text.py` to write `data/pdf_pages.csv`. The
   `fac_fetch_audit_report` tool adds PDFs here as the agent needs them, and a
   rebuild re-extracts them.
3. For the IRS tools, download the EO BMF's four region files into `data/`
   (about 340 MB, no key):
   ```bash
   for n in 1 2 3 4; do curl -o data/eo$n.csv https://www.irs.gov/pub/irs-soi/eo$n.csv; done
   ```
   Then run `python scripts/fetch_schedule_r.py` for Schedule R. It reads the
   EINs in `scripts/schedule_r_eins.txt` (the scenarios' parents and
   subsidiaries), downloads the IRS's 990 e-file indexes for 2024 on into
   `data/irs990/` (about 250 MB, once), pulls each full Form 990 out of the
   IRS's zips without downloading the zips, and writes
   `data/form990_returns.csv` and `data/schedule_r.csv`. Add an EIN to the
   list (or give it on the command line, which adds it to the list) and
   rerun to cover another organization; stored returns aren't fetched again,
   and `--refresh` downloads the indexes again.

   For the automatic revocation list (about 50 MB), download
   `https://apps.irs.gov/pub/epostcard/data-download-revocation.zip` into
   `data/` and run `python scripts/convert_revocations.py`, which writes
   `data/irs_revocations.csv`. The IRS updates it monthly.
4. `python scripts/build_db.py` writes `data/dat.sqlite` in a few minutes. A
   CSV you haven't downloaded is skipped, and a `snapshot` table records each
   file's date, row count, and source (`fac` or `irs`). `DAT_DB_PATH` in
   `.env` moves the file. After pulling a schema change, rebuild: tools refuse
   a database that predates the current schema and say so. The build also
   derives `fac_filers` (each name and address a filer has used) from the
   audits it loaded; `python scripts/build_db.py --derived` adds or refreshes
   that table alone, in under a minute, without reloading the CSVs.

The schema is the SQL in `scripts/schema/`, one file per source. To add a
table, declare it there with a `-- load:` line naming its CSV and rebuild;
the build replaces the file, so there are no migrations. Tools that read the
database say so, with the snapshot date, in their caveats.

## Add a tool

1. Create `src/dat_mcp/tools/<service>_<action>.py` with a plain function and
   a `register(mcp)` function. Copy `fac_search_audits.py` as a starting
   point, and follow [mcp-design.md](mcp-design.md#writing-a-tool).
2. Call its `register` from `tools/__init__.py`.
3. Add a line for it to the tool guide in `app.py`'s server instructions.
4. Add tests in `tests/test_<tool>.py`, with the service client replaced by a
   fake (the existing tool tests show how).
5. List it in the README's Tools section.
6. Add an eval case for a question it should help answer, and run it.

## Add a service

For a new source (the HHS-OIG exclusion list, for example):

1. **Client:** `src/dat_mcp/<service>.py`, modeled on `fac.py`, meeting the
   rules in [mcp-design.md](mcp-design.md#service-clients).
2. **Key:** if the service needs one, add it to `config.py` and
   `.env.example`.
3. **Tools:** add each one as above, named `<service>_<action>`.
4. **Tool guide:** add a section for the service in `app.py`'s instructions.
5. **Eval:** give its cases ids starting `<service>-` (e.g. `sam-typo-uei`)
   and run them with `python eval/run_eval.py <service>`.

## Ground rules

- Public data only: no internal HRSA data, hostnames, names, or credentials
  in this repo, including tests and eval cases.
- Never commit `.env` or API keys.
