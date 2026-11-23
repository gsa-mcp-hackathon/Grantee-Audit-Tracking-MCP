// Run with: node tests/ui_profile.test.cjs
// The pure data functions embedded in the served UI resource, run as served.
const {readFileSync} = require("node:fs");
const {resolve} = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");
const ui = resolve(__dirname, "../src/dat_mcp/ui");
const html = readFileSync(resolve(ui, "grantee_profile.html"), "utf8");
const code = html.match(/<script id="profile-data">([\s\S]*?)<\/script>/)[1];
const ctx = {window: {}, URL};
vm.runInNewContext(code, ctx);
const {years, rowsFor, rowKind, yearStatus, overview, safeUrl, markdown, awardLinks, merged} = ctx.window.DatEvidence;
const coverage = () => JSON.parse(readFileSync(resolve(ui, "demo_profile.json"), "utf8")).coverage;
const plain = value => JSON.parse(JSON.stringify(value));
const row = (c, year) => c.years.find(r => r.audit_year === year);

test("each route maps to one kind, and lateness is the server's days_late", () => {
  const c = coverage();
  assert.deepEqual(plain(years(c)), [2025, 2024, 2023, 2022, 2021, 2020]);
  assert.deepEqual(plain(years(c)).map(y => yearStatus(c, y).kind), ["gap", "late", "unlisted", "parent", "ontime", "ontime"]);
  assert.equal(rowKind({route: "parent has no audit on file"}), "gap");
  assert.equal(rowKind({route: "a route added later"}), "unknown");
});
test("an audit with no days_late is filed, never on time", () => {
  const c = coverage();
  row(c, 2021).days_late = null;
  assert.equal(yearStatus(c, 2021).kind, "filed");
  delete row(c, 2021).days_late;
  assert.equal(yearStatus(c, 2021).kind, "filed");
});
test("an audit under a linked identifier is marked linked", () => {
  const c = coverage();
  assert.equal(yearStatus(c, 2024).linked, true);
  assert.equal(yearStatus(c, 2021).linked, false);
  assert.equal(yearStatus(c, 2022).linked, false);
});
test("a later resubmission does not make an earlier acceptance look late", () => {
  const c = coverage(), first = row(c, 2021);
  c.years.unshift({...first, report_id: "RESUBMITTED", fac_accepted_date: "2023-01-10", days_late: 285, resubmission_version: 2});
  const s = yearStatus(c, 2021);
  assert.equal(s.kind, "ontime");
  assert.equal(s.primary.report_id, "DEMO-2021");
  assert.equal(s.rows.length, 2);
});
test("audits by more than one filer in a year are not given a timing", () => {
  const c = coverage();
  c.years.push({...row(c, 2021), report_id: "OTHER", filed_under: {uei: "DEMOOTHER001", ein: "000000005"}});
  assert.equal(yearStatus(c, 2021).kind, "review");
});
test("an audit and a parent listing in one year count as the audit", () => {
  const c = coverage();
  c.years.push({...row(c, 2022), audit_year: 2021});
  const s = yearStatus(c, 2021);
  assert.equal(s.kind, "ontime");
  assert.equal(s.rows.length, 2);
});
test("the summary counts fiscal years by how their audit was found", () => {
  const o = overview(coverage());
  assert.equal(o.summary, "3 of 6 fiscal years have an audit · 1 accepted after the nine-month date · 1 with no audit found · 2 found through a parent");
});
test("empty and malformed results draw no years", () => {
  assert.deepEqual(plain(years({})), []);
  assert.deepEqual(plain(years({years: [null, {}, {audit_year: "bad"}, {audit_year: 2025.5}]})), []);
  assert.deepEqual(plain(rowsFor(null, 2025)), []);
  assert.equal(overview({}).summary, "No fiscal-year rows were returned");
});
test("source links accept only HTTPS on the public data hosts", () => {
  assert.equal(safeUrl("https://app.fac.gov/dissemination/summary/EXAMPLE"), "https://app.fac.gov/dissemination/summary/EXAMPLE");
  assert.equal(safeUrl("https://sam.gov/entity/EXAMPLE/coreData"), "https://sam.gov/entity/EXAMPLE/coreData");
  assert.equal(safeUrl("https://apps.irs.gov/app/eos/details/?ein=000000001"), "https://apps.irs.gov/app/eos/details/?ein=000000001");
  for (const url of [
    "javascript:alert(1)", "data:text/html,hi", "file:///etc/passwd",
    "http://app.fac.gov/x", "https://app.fac.gov.evil.example/x",
    "https://app.fac.gov@evil.example/x", "https://evil@app.fac.gov/x",
    "https://app.fac.gov:444/x", "https://127.0.0.1/x", "https://169.254.169.254/x",
    "https://app.fac.gov\\@evil.example/", "https://app.fac.gov/\nanything",
    "https://app.fac.gov/a b", "//app.fac.gov/x", null, 5,
  ]) assert.equal(safeUrl(url), null, String(url));
});
test("source text is escaped in the memo", () => {
  const result = markdown("<script>alert(1)</script> [click](javascript:alert(1))");
  assert.ok(result.startsWith("\\<script\\>"));
  assert.ok(result.includes("\\[click\\]\\("));
});
test("award links need the exact UEI and an allowed URL", () => {
  const match = {"Recipient UEI": "HF6NE8JKLGV3", source_url: "https://www.usaspending.gov/award/EXAMPLE"};
  const response = {awards: [match, {...match, "Recipient UEI": "WRONGMATCH12"}, {...match, source_url: "javascript:alert(1)"}]};
  assert.deepEqual(plain(awardLinks(response, "HF6NE8JKLGV3")), [match]);
});
test("both tools' caveats are shown once each", () => {
  assert.deepEqual(plain(merged(["a", "b"], ["b", "c"], null, [{not: "text"}])), ["a", "b", "c"]);
});
test("every bundled UI script parses", () => {
  for (const filename of ["grantee_profile.html", "preview_host.html"]) {
    const source = readFileSync(resolve(ui, filename), "utf8");
    for (const match of source.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)) assert.doesNotThrow(() => new vm.Script(match[1]));
  }
});
