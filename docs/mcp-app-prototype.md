# Grantee MCP Apps (prototype)

`grantee_show_profile` shows one grantee's identity and Single Audit coverage
as an interactive view inside an MCP Apps client. It runs
`grantee_identity_resolution` and `grantee_audit_coverage` for the grantee and
returns both results; the view draws them and adds nothing of its own.

What the view shows:

- A tile per fiscal year, colored by how the year's audit was found (the row's
  `route`).
- **Year detail**: every row for the selected year, with its source link.
- **Identity**: each UEI and EIN with what links it, each source's fiscal year
  end, SAM.gov and IRS records, parents, and unconfirmed matches kept apart.
- **Questions**: both tools' open questions and notes.
- Both tools' caveats (collapsed), a memo download, and for a real grantee a
  refresh and a lookup of five USAspending award links.

Due dates and days late are `grantee_audit_coverage`'s. The view works out
none of its own, and makes no call on whether an audit was required or late.

## Network view

`grantee_show_network` shows the organizations around one grantee, and
returns the same network as data for clients with no view.

- **One box for the grantee.** Every UEI and EIN that
  `grantee_identity_resolution` links to it is grouped in that box; select it
  to see what links each, with a typo's differing character marked.
- **Above it**: organizations that list it, in a parent's FAC audit or on
  another filer's Schedule R.
- **Below it**: what its latest audit lists as covered, and what its own
  Schedule R reports as controlled or as a disregarded entity.
- **Last**: organizations on its Schedule R with no control stated (parents,
  siblings, and affiliates all land here).
- **Beside it, dashed**: unconfirmed matches and other organizations that
  filed under its identifiers. They are never merged into the grantee.
- **Line style is the source**: solid for a FAC covered-entity listing,
  dashed for Schedule R, green when both agree. An organization found in both
  is one box, merged by EIN or UEI.
- A tier wider than two rows collapses to "+N more". The tool returns at most
  40 organizations per tier and reports the total.
- Select a box for its evidence and sources, and to re-center on it.

Tiers say who lists whom, not whose audit covers whom. Limits worth knowing:
Schedule R is read only from the returns stored locally; covered entities
come from the latest audit only; and an entity FAC lists by UEI alone is
shown by its UEI, with no name.

Examples (November 2026): Betty Jean Kerr People's Health Centers
(EIN `431036785`) has four controlled affiliates, one of which lists it back
under a one-digit EIN typo. Louisiana Children's Medical Center
(EIN `943480131`) has 26 organizations across three tiers, several found in
both FAC and Schedule R.

## Try it

In an MCP Apps client, ask to see a grantee, for example "Show the profile for
UEI HF6NE8JKLGV3". Clients without MCP Apps get the same data as text and
structured content.

Community Health Service Inc. (MN, `HF6NE8JKLGV3`) has a recent year with no
audit found and several accepted well after the nine-month date. Chesapeake
Hospital Authority (`GMFLJZLNKK34`) has a year with no audit whose prior year
was mostly one-time Provider Relief Fund money. Both are as of November 2026;
look them up again rather than relying on that.

### Codex

Codex's MCP Apps support is behind a feature flag. In a trusted project, a
local `.codex/config.toml` (gitignored) turns it on and registers the server:

```toml
[features]
enable_mcp_apps = true

[mcp_servers.dat-mcp]
command = 'C:\path\to\repo\.venv\Scripts\python.exe'
args = ["-m", "dat_mcp.app"]
cwd = 'C:\path\to\repo'
startup_timeout_sec = 30
tool_timeout_sec = 180
```

`cwd` must be the repo, where `.env` is read from. Restart Codex after
changing the file.

### Browser preview

```bash
python -m dat_mcp.ui_preview
```

Open the URL it prints. It starts on a fictional demo profile; switch between
Profile and Network at the top, and type a UEI to call the real tool (needs
the keys and database from [setup.md](setup.md)).
The preview is a small development host: it serves the same registered
resource in a sandboxed iframe and does the MCP Apps handshake. It doesn't
show that another client renders the view.

## How it is built

- Each tool declares `_meta.ui.resourceUri`, pointing at its view
  (`ui://dat/grantee-profile/v2.html`, `ui://dat/grantee-network/v1.html`):
  a single HTML file with inline script
  and style, no external requests, and an empty network CSP. Needs FastMCP 4.1
  or later.
- The view speaks `ui/initialize`, `ui/notifications/tool-result`,
  `ui/open-link`, and `tools/call` (to refresh, re-center, or fetch award links only).
- Names, notes, and other source text are untrusted: they enter the page only
  as text, and are escaped in the memo. Source links are opened by the host,
  and only HTTPS links to FAC, SAM.gov, USAspending, and the IRS are offered.
- The preview binds to `127.0.0.1` under a random path, checks the Host and
  Origin headers, forwards only the two view tools by UEI or EIN and
  `usaspending_search_awards` for one UEI with a limit of five, runs one lookup
  at a time, and never returns or logs upstream error text. Don't expose its
  port.

## Tests

```bash
pytest tests/test_grantee_show_profile.py tests/test_grantee_show_network.py tests/test_ui_preview.py
node tests/ui_profile.test.cjs
node tests/ui_view.test.cjs
node tests/ui_network.test.cjs
```

The Node tests run the view's real scripts against a DOM double, using Node's
built-in test runner (no packages). They check what is drawn and what is asked
of the host, not layout; check that in a browser and in the client.
