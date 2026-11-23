// Run with: node tests/ui_network.test.cjs
// The network view's real inline scripts: the layout functions directly, and
// the view against a small DOM and host double. Not a check of how it looks.
const {readFileSync} = require("node:fs");
const {resolve} = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const assert = require("node:assert/strict");
const ui = resolve(__dirname, "../src/dat_mcp/ui");
const html = readFileSync(resolve(ui, "grantee_network.html"), "utf8");
const fixture = () => JSON.parse(readFileSync(resolve(ui, "demo_network.json"), "utf8"));
const plain = value => JSON.parse(JSON.stringify(value));
const ctx = {window: {}, URL};
vm.runInNewContext(html.match(/<script id="network-data">([\s\S]*?)<\/script>/)[1], ctx);
const {layout, edgeStyle, spans, safeUrl} = ctx.window.DatNetwork;
const BELOW = "controlled or covered by this entity";
const many = n => Array.from({length: n}, (_, i) => ({name: `CHILD ${i}`, eins: [String(i).padStart(9, "0")], ueis: [], tier: BELOW, evidence: [{source: "IRS Form 990 Schedule R"}]}));

test("a line's style is the sources behind the organization", () => {
  const net = fixture(), style = name => edgeStyle(net.organizations.find(o => o.name === name));
  assert.equal(style("RIVERBEND HEALTH NETWORK"), "both");
  assert.equal(style("RIVERBEND HOUSING SERVICES"), "irs");
  assert.equal(style(null), "fac");
  assert.equal(style("RIVERBEND DENTAL CLINIC"), "unconfirmed");
});
test("who lists the entity is above it, what it controls below, related last", () => {
  const plan = layout(fixture(), 920, {}), y = name => plan.boxes.find(b => b.org && b.org.name === name).y;
  const center = plan.boxes.find(b => b.center);
  assert.ok(y("RIVERBEND HEALTH NETWORK") < center.y);
  assert.ok(center.y < y("RIVERBEND PHARMACY LLC"));
  assert.ok(y("RIVERBEND PHARMACY LLC") < y("RIVERBEND HEALTH FOUNDATION"));
  assert.deepEqual(plain(plan.labels.map(l => l.text)), ["Lists this entity (1)", "Controlled or covered by this entity (3)", "Related on Schedule R, control not stated (1)"]);
});
test("an unconfirmed match sits beside the entity, joined only by a dotted line", () => {
  const plan = layout(fixture(), 920, {}), center = plan.boxes.find(b => b.center);
  const lead = plan.boxes.find(b => b.org && b.org.tier === "possible match");
  assert.equal(lead.y, center.y);
  assert.ok(lead.x > center.x + center.w);
  assert.equal(plan.lines.filter(l => l.style === "unconfirmed").length, 2);
});
test("no box overlaps another and no line runs through a box", () => {
  for (const [net, open] of [[fixture(), {}], [{entity: {}, organizations: many(40)}, {[BELOW]: true}]]) {
    const plan = layout(net, 920, open), inside = (l, b) => l.x + l.w > b.x && l.x < b.x + b.w && l.y + l.h > b.y && l.y < b.y + b.h;
    plan.boxes.forEach((a, i) => plan.boxes.slice(i + 1).forEach(b => assert.ok(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y)));
    for (const l of plan.lines) for (const b of plan.boxes) assert.ok(!inside(l, b), JSON.stringify([l, b]));
    for (const b of plan.boxes) assert.ok(b.x + b.w <= plan.width && b.y + b.h <= plan.height);
  }
});
test("a large tier shows two rows and a count, and opens in full", () => {
  const net = {entity: {}, organizations: many(40), counts: {[BELOW]: {total: 148, shown: 40}}};
  const shut = layout(net, 920, {}), open = layout(net, 920, {[BELOW]: true});
  assert.equal(shut.cols, 5);
  assert.equal(shut.boxes.filter(b => b.org).length, 9);
  assert.equal(shut.boxes.find(b => b.more).text, "+31 more");
  assert.equal(shut.labels[0].text, "Controlled or covered by this entity (148) · 40 returned");
  assert.equal(open.boxes.filter(b => b.org).length, 40);
  assert.equal(open.boxes.find(b => b.more).text, "Show fewer");
});
test("a narrow view wraps to fewer columns instead of overflowing", () => {
  const plan = layout({entity: {}, organizations: many(7)}, 600, {});
  assert.equal(plan.cols, 3);
  assert.ok(Math.max(...plan.boxes.map(b => b.x + b.w)) <= 600);
});
test("an entity with nothing around it is still drawn", () => {
  const plan = layout({entity: {identifiers: []}, organizations: []}, 920, {});
  assert.equal(plan.boxes.length, 1);
  assert.equal(plan.lines.length, 0);
  assert.equal(layout(null, undefined, null).boxes.length, 1);
});
test("years are joined into runs, and links stay on the public data hosts", () => {
  assert.equal(spans([2024, "2022", 2023, 2019, 2022]), "2019, 2022–2024");
  assert.equal(spans(null), "");
  assert.equal(safeUrl("https://apps.irs.gov/app/eos/details/?ein=000000001"), "https://apps.irs.gov/app/eos/details/?ein=000000001");
  for (const url of ["javascript:alert(1)", "http://app.fac.gov/x", "https://app.fac.gov.evil.example/x", "https://evil@sam.gov/x", null]) assert.equal(safeUrl(url), null);
});

class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.style = {}; this.hidden = false; this._text = ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = nodes; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(" "); }
  set innerHTML(_) { throw new Error("Source text must not be set as HTML"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  getBoundingClientRect() { return {height: 440}; }
  all(predicate, found = []) { for (const c of this.children) { if (predicate(c)) found.push(c); c.all(predicate, found); } return found; }
}
async function render(data, capabilities = {serverTools: {}, openLinks: {}}) {
  const nodes = {};
  for (const match of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"[^>]*>/g)) nodes[match[2]] = new Node(match[1]);
  const posted = [], handlers = {}, parent = {postMessage: m => posted.push(m)};
  const document = {getElementById: id => nodes[id], createElement: tag => new Node(tag), querySelector: () => new Node("main"), addEventListener: () => {}};
  const context = {window: {parent, addEventListener: (event, fn) => { handlers[event] = fn; }}, document, URL, setTimeout: () => 1, clearTimeout: () => {}, requestAnimationFrame: () => {}};
  vm.createContext(context);
  for (const match of html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)) vm.runInContext(match[1], context);
  const message = m => handlers.message({source: parent, data: {jsonrpc: "2.0", ...m}});
  message({id: posted[0].id, result: {hostCapabilities: capabilities}});
  await new Promise(done => setImmediate(done));
  message({method: "ui/notifications/tool-result", params: {structuredContent: data}});
  const box = label => nodes.graph.children.find(n => n.tag === "button" && n.textContent.includes(label));
  return {nodes, posted, message, box};
}

test("the entity is drawn once, with its grouped identifiers", async () => {
  const v = await render(fixture());
  assert.equal(v.nodes.name.textContent, "Riverbend Community Health");
  assert.equal(v.nodes.subtitle.textContent, "RIVERBEND, MN · 2 identifiers grouped · 6 organizations around it");
  const center = v.box("Found in FAC · SAM.gov · IRS");
  assert.match(center.textContent, /UEI DEMOHEALTH01 · EIN 000000001 UEI DEMOHEALTH0I · EIN 000000001/);
  assert.equal(v.nodes.graph.children.filter(n => n.tag === "button").length, 7);
  // Selected by default: what links each identifier, the differing character marked.
  assert.match(v.nodes.detail.textContent, /UEI one character from DEMOHEALTH01, with the same EIN/);
  assert.deepEqual(v.nodes.detail.all(n => n.tag === "mark").map(n => n.textContent), ["I"]);
});
test("selecting an organization shows each source's evidence", async () => {
  const v = await render(fixture());
  assert.match(v.box("RIVERBEND HEALTH NETWORK").textContent, /EIN 000000009 · FAC \+ 990/);
  v.box("RIVERBEND HEALTH NETWORK").onclick();
  const shown = v.nodes.detail.textContent;
  assert.match(shown, /EIN 000000009 · UEI DEMOPARENT01/);
  assert.match(shown, /its audit lists this entity as covered, by UEI FAC covered-entity listing · 2022/);
  assert.match(shown, /IRS Form 990 Schedule R · 2022–2023 · reported as controlled/);
  assert.equal(v.box("RIVERBEND HEALTH NETWORK").attributes["aria-pressed"], "true");
  assert.match(v.box("DEMOCHILD001").textContent, /^UEI DEMOCHILD001 FAC$/);
  assert.match(v.box("RIVERBEND DENTAL CLINIC").textContent, /possible match/);
});
test("centering on an organization calls this tool with its EIN, outside the demo", async () => {
  const demo = await render(fixture());
  demo.box("RIVERBEND HOUSING SERVICES").onclick();
  assert.equal(demo.nodes.detail.all(n => n.textContent === "Center on this organization").length, 0);
  const data = fixture();
  data.demo = false;
  const v = await render(data);
  v.box("RIVERBEND HOUSING SERVICES").onclick();
  v.nodes.detail.all(n => n.tag === "button" && n.textContent === "Center on this organization")[0].onclick();
  assert.deepEqual(plain(v.posted.at(-1).params), {name: "grantee_show_network", arguments: {ein: "000000005"}});
});
test("source text stays text, and notes and caveats are both shown", async () => {
  const data = fixture();
  data.entity.name = "<img src=x onerror=alert(1)>";
  data.organizations[0].name = "<script>alert(1)</script>";
  const v = await render(data);
  assert.equal(v.nodes.name.textContent, "<img src=x onerror=alert(1)>");
  assert.ok(v.box("<script>alert(1)</script>"));
  assert.equal(v.nodes.notesLabel.textContent, "Notes and caveats (3)");
  for (const line of [...data.notes, ...data.caveats]) assert.ok(v.nodes.notes.textContent.includes(line));
});
test("candidates are listed, and any other result changes nothing", async () => {
  const v = await render({candidates: [{name: "RIVERBEND A", uei: "DEMOHEALTH01", ein: "000000001"}], caveats: ["Pick one."]});
  assert.equal(v.nodes.candidates.hidden, false);
  assert.match(v.nodes.candidates.textContent, /RIVERBEND A.*Pick one\./);
  const w = await render(fixture());
  w.message({method: "ui/notifications/tool-result", params: {structuredContent: {coverage: {}}}});
  assert.equal(w.nodes.name.textContent, "Riverbend Community Health");
  assert.match(w.nodes.status.textContent, /could not be shown/);
});
