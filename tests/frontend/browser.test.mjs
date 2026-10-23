// Real-browser interaction tests (headless Chromium via DevTools protocol):
// heating-curve drag with mouse/touch/keyboard, apply/discard, −/+ bursts.
// Skipped when no Chromium binary is available.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { findChrome, launch } from "./harness/cdp.mjs";
import { startServer } from "./harness/serve.mjs";

const CHROME = findChrome();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let server, chrome, base;

before(async () => {
  if (!CHROME) return;
  server = await startServer(0);
  base = `http://127.0.0.1:${server.address().port}/tests/frontend/harness/index.html?`;
  chrome = await launch();
});
after(async () => {
  if (chrome) await chrome.close();
  if (server) server.close();
});

async function open(query, { width = 760, height = 1000, mobile = false } = {}) {
  const page = await chrome.newPage();
  await page.init();
  await page.viewport(width, height, mobile);
  await page.goto(base + query);
  await sleep(150);
  return page;
}

const sr = "harness.el.shadowRoot";
const center = (page, sel) => page.eval(`(() => { const e = ${sr}.querySelector(${JSON.stringify(sel)}); if (!e) return null; const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
const text = (page, sel) => page.eval(`(${sr}.querySelector(${JSON.stringify(sel)}) || {}).textContent || ""`);
const writes = (page) => page.eval("harness.writes()");
const pending = (page, n = 1) => page.eval(`({ ...harness.el._views.efficiency.curve.st.get(${n})?.pending })`);

async function mouseDrag(page, [x, y], [dx, dy], steps = 8) {
  await page.mouse("mouseMoved", x, y);
  await page.mouse("mousePressed", x, y, 1);
  for (let i = 1; i <= steps; i++) {
    await page.mouse("mouseMoved", x + (dx * i) / steps, y + (dy * i) / steps, 1);
    await sleep(16);
  }
  await page.mouse("mouseReleased", x + dx, y + dy);
  await sleep(80);
}

async function click(page, sel) {
  const c = await center(page, sel);
  assert.ok(c, `element ${sel}`);
  await page.mouse("mouseMoved", c[0], c[1]);
  await page.mouse("mousePressed", c[0], c[1], 1);
  await page.mouse("mouseReleased", c[0], c[1]);
  await sleep(60);
}

test("mouse drag previews live, writes nothing until Apply, then only changed entities", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=efficiency&theme=dark&latency=150");
  const h = await center(page, '[data-h="heat_norm"] .dotv');
  assert.ok(h);
  // live preview while dragging: floating label + readout appear before release
  await page.mouse("mouseMoved", h[0], h[1]);
  await page.mouse("mousePressed", h[0], h[1], 1);
  await page.mouse("mouseMoved", h[0], h[1] - 20, 1);
  await sleep(60);
  await page.mouse("mouseMoved", h[0], h[1] - 40, 1);
  await sleep(80);
  assert.match(await text(page, ".flab"), /−15,0° → \d\d,\d°/);
  assert.match(await text(page, ".readout"), /bei 12,2° außen: Vorlauf 26,8° → 2\d,\d°/);
  assert.ok(await page.eval(`${sr}.querySelector("svg.ch").classList.contains("dragging")`));
  await page.mouse("mouseReleased", h[0], h[1] - 40);
  await sleep(80);
  let p = await pending(page);
  assert.deepEqual(Object.keys(p), ["heat_curve_norm_flow"], "pure vertical drag changes only the flow");
  assert.ok(p.heat_curve_norm_flow > 38 && p.heat_curve_norm_flow < 46, JSON.stringify(p));
  assert.equal(Math.round(p.heat_curve_norm_flow * 2), p.heat_curve_norm_flow * 2, "drag snaps to 0.5 K");
  assert.match(await text(page, ".pendbar"), /Norm-Vorlauf 36 → \d\d(,\d)? °C/);
  assert.match(await text(page, ".pendbar"), /1 Schreibzugriff/);
  // a second drag accumulates
  const b = await center(page, '[data-h="heat_base"] .dotv');
  await mouseDrag(page, b, [-30, 0]);
  p = await pending(page);
  assert.deepEqual(Object.keys(p).sort(), ["heat_curve_base_outdoor", "heat_curve_norm_flow"]);
  assert.match(await text(page, ".pendbar"), /2 Schreibzugriffe/);
  assert.deepEqual(await writes(page), [], "nothing written before Apply");
  // feed updates must not overwrite pending values
  await page.eval("harness.plant.values['hc1.flow_target'] = 27.1; harness.plant.push()");
  await sleep(80);
  assert.deepEqual(await pending(page), p);
  await click(page, '[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 2", 3000);
  const w = await writes(page);
  assert.deepEqual(w, [
    `number.set_value number.fussboden_vorlauftemperatur_bei_norm_aussentemperatur ${p.heat_curve_norm_flow}`,
    `number.set_value number.fussboden_fusspunkt_aussentemperatur ${p.heat_curve_base_outdoor}`,
  ]);
  await page.waitFor(`!${sr}.querySelector(".pendbar")`, 3000);
  assert.deepEqual(await pending(page), {});
  await sleep(300);
  assert.equal((await writes(page)).length, 2, "no extra writes");
});

test("keyboard: arrows move one step, Shift ×5, Escape discards, Enter applies", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=efficiency&theme=light&latency=50");
  await page.eval(`${sr}.querySelector('[data-h="heat_limit"]').focus()`);
  await sleep(50);
  await page.key("ArrowRight");
  await page.key("ArrowRight");
  await page.key("ArrowRight");
  await sleep(50);
  assert.deepEqual(await pending(page), { heat_limit: 17 });
  assert.match(await text(page, ".readout"), /Vorlauf/);
  await page.key("Escape");
  await sleep(50);
  assert.deepEqual(await pending(page), {});
  await page.eval(`${sr}.querySelector('[data-h="heat_norm"]').focus()`);
  await page.key("ArrowUp", { shift: true });
  await page.key("ArrowLeft");
  await sleep(50);
  assert.deepEqual(await pending(page), { heat_curve_norm_flow: 36.5, heat_curve_norm_outdoor: -15.1 });
  await page.key("Enter");
  await page.waitFor("harness.writes().length === 2", 3000);
  assert.deepEqual(await writes(page), [
    "number.set_value number.fussboden_norm_aussentemperatur -15.1",
    "number.set_value number.fussboden_vorlauftemperatur_bei_norm_aussentemperatur 36.5",
  ]);
  // focus stayed on the handle through all re-renders
  assert.equal(await page.eval(`${sr}.activeElement && ${sr}.activeElement.getAttribute("data-h")`), "heat_norm");
});

test("touch drag on a phone works and does not scroll the page", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=full&view=efficiency&theme=dark&scheme=ochsner", { width: 390, height: 520, mobile: true });
  assert.ok(await page.eval("document.documentElement.scrollHeight > innerHeight + 200"), "page is scrollable");
  await page.eval("scrollTo(0, 0)");
  // switch to the second circuit tab by tap
  const tab = await center(page, '.tabs button[data-tab="2"]');
  await page.touch("touchStart", [tab]);
  await page.touch("touchEnd", []);
  await sleep(120);
  assert.match(await text(page, ".tabs button.on"), /Obergeschoss/);
  const c = await center(page, '[data-h="cool_norm"] .dotv');
  assert.ok(c, "cooling handles exist on a cool-capable circuit");
  await page.touch("touchStart", [c]);
  for (let i = 1; i <= 8; i++) {
    await page.touch("touchMove", [[c[0] + i * 3, c[1] - i * 4]]);
    await sleep(16);
  }
  await page.touch("touchEnd", []);
  await sleep(100);
  const p = await pending(page, 2);
  assert.ok("cool_curve_norm_flow" in p || "cool_curve_norm_outdoor" in p, JSON.stringify(p));
  if ("cool_curve_norm_outdoor" in p) assert.ok(p.cool_curve_norm_outdoor <= 34, "keeps ≥ 1 K below the other point");
  assert.equal(await page.eval("scrollY"), 0);
  assert.deepEqual(await writes(page), []);
  await click(page, '[data-cact="discard"]');
  assert.deepEqual(await pending(page, 2), {});
  // a swipe on the chart outside the handles still scrolls the page
  const bg = await page.eval(`(() => { const r = ${sr}.querySelector("svg.ch .plotbg").getBoundingClientRect(); return [r.left + 40, r.top + 30]; })()`);
  await page.touch("touchStart", [bg]);
  for (let i = 1; i <= 10; i++) {
    await page.touch("touchMove", [[bg[0], bg[1] - i * 12]]);
    await sleep(16);
  }
  await page.touch("touchEnd", []);
  await sleep(150);
  assert.ok((await page.eval("scrollY")) > 0, "page scrolled");
});

test("read-only handle (disabled entity) cannot be dragged and says why", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=efficiency&disable=hc1.heat_limit");
  assert.ok(await page.eval(`${sr}.querySelector('[data-h="heat_limit"]').classList.contains("ro")`));
  assert.equal(await page.eval(`${sr}.querySelector('[data-h="heat_limit"]').getAttribute("tabindex")`), null);
  assert.match(await text(page, '[data-h="heat_limit"] title'), /Nicht änderbar: Heizgrenze/);
  const c = await center(page, '[data-h="heat_limit"] .hit');
  await mouseDrag(page, c, [40, 0]);
  assert.deepEqual(await pending(page), {});
});

test("apply error is shown and the remaining pending values are kept", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=efficiency&latency=30");
  await page.eval(`${sr}.querySelector('[data-h="heat_max"]').focus()`);
  await page.key("ArrowUp");
  await page.key("ArrowUp");
  await sleep(50);
  assert.deepEqual(await pending(page), { heat_curve_max_flow: 42 });
  await page.eval("harness.plant.failNext = 'Controller antwortet nicht'");
  await click(page, '[data-cact="apply"]');
  await page.waitFor(`(${sr}.querySelector(".pendbar .err") || {}).textContent`, 2000);
  assert.match(await text(page, ".pendbar .err"), /Controller antwortet nicht/);
  assert.deepEqual(await pending(page), { heat_curve_max_flow: 42 });
  await click(page, '[data-cact="apply"]');
  await page.waitFor(`!${sr}.querySelector(".pendbar")`, 2000);
  assert.equal((await writes(page)).length, 2, "failed attempt + successful retry");
});

test("−/+ burst: value updates at once, one coalesced setpoint write after 1.5 s", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=controls&latency=50");
  const plus = '.hc[data-key="hc1"] .sp.main [data-s="1"]';
  for (let i = 0; i < 3; i++) await click(page, plus);
  assert.match(await text(page, '.hc[data-key="hc1"] .sp.main .v'), /23,0°/);
  assert.match(await text(page, '.hc[data-key="hc1"] .note'), /wird gesendet, sobald du aufhörst/);
  await sleep(900);
  assert.deepEqual(await writes(page), []);
  await page.waitFor("harness.writes().length === 1", 2000);
  assert.deepEqual(await writes(page), ["number.set_value number.fussboden_raumsollwert_normal_heizen 23"]);
  await page.waitFor(`/gespeichert/.test((${sr}.querySelector('.hc[data-key="hc1"] .note') || {}).textContent)`, 2000);
  // + then − back to the current value → no write at all
  await click(page, plus);
  await click(page, '.hc[data-key="hc1"] .sp.main [data-s="-1"]');
  await sleep(1900);
  assert.equal((await writes(page)).length, 1);
  assert.match(await text(page, '.hc[data-key="hc1"] .sp.main .v'), /23,0°/);
});

test("heat pump off needs a confirmation tap; presets map to climate services", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=controls&latency=30");
  await click(page, '[data-act="hp"][data-v="3"]');
  assert.match(await text(page, '[data-act="hp"][data-v="3"]'), /Wirklich aus\?/);
  assert.deepEqual(await writes(page), []);
  await click(page, '[data-act="hp"][data-v="3"]');
  await page.waitFor("harness.writes().length === 1", 2000);
  assert.deepEqual(await writes(page), ["select.select_option select.betriebswahl_warmepumpe Abgeschaltet"]);
  await page.waitFor(`${sr}.querySelector('[data-act="hp"][data-v="3"]').classList.contains("on")`, 2000);
  await click(page, '[data-act="preset"][data-c="2"][data-v="eco"]');
  await click(page, '[data-act="preset"][data-c="3"][data-v="off"]');
  await page.waitFor("harness.writes().length === 3", 2000);
  assert.deepEqual((await writes(page)).slice(1), [
    "climate.set_preset_mode climate.radiatoren eco",
    "climate.set_hvac_mode climate.wintergarten off",
  ]);
  await page.waitFor(`${sr}.querySelector('[data-act="preset"][data-c="2"][data-v="eco"]').classList.contains("on")`, 2000);
});

test("legacy operating program (HC3 Wintergarten = Reduziert): shown instead of Komfort/Eco, chips explain the switch", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=all&latency=30", { width: 1280, height: 1400 });
  const hc = (n) => `.hc[data-key="hc${n}"]`;
  const chipState = (n) => page.eval(`[...${sr}.querySelectorAll('${hc(n)} [data-act="preset"]')].map((b) => [b.dataset.v, b.classList.contains("on"), b.getAttribute("aria-pressed"), b.getAttribute("title")])`);
  const rowChip = (eid) => page.eval(`(${sr}.querySelector('svg.plant [data-more="${eid}"] text.chip') || {}).textContent`);
  // the fixture's HC3 runs op_program 2 while comfort_eco = 0 and the badge says auto_comfort
  assert.equal(await page.eval("harness.plant.values['hc3.op_program']"), 2);
  assert.match(await text(page, `${hc(3)} .hcname .sub`), /\| Reduziert$/);
  assert.equal(await rowChip("climate.wintergarten"), "Reduziert");
  const legacy = await chipState(3);
  assert.deepEqual(legacy.map(([v, on, pressed]) => [v, on, pressed]), [["comfort", false, "false"], ["eco", false, "false"], ["off", false, "false"]]);
  assert.match(legacy[0][3], /Läuft im Programm „Reduziert“\. Komfort schaltet den Heizkreis auf die Komfort\/Eco-Steuerung der App um/);
  assert.match(legacy[1][3], /^Läuft im Programm „Reduziert“\. Eco schaltet/);
  assert.equal(legacy[2][3], null, "Aus keeps its plain meaning");
  // circuits on the app's control are unchanged
  assert.match(await text(page, `${hc(1)} .hcname .sub`), /\| Komfort$/);
  assert.equal(await rowChip("climate.fussboden"), "Komfort");
  assert.deepEqual((await chipState(1)).map(([v, on, , title]) => [v, on, title]), [["comfort", true, null], ["eco", false, null], ["off", false, null]]);
  // other legacy programs show their own label; Aus (1) stays "Aus"
  for (const [raw, label] of [[3, "Normalbetrieb"], [4, "Handbetrieb Heizen"], [7, "Party"], [8, "Urlaub"]]) {
    await page.eval(`harness.plant.values['hc3.op_program'] = ${raw}; harness.plant.push(); true`);
    await sleep(80);
    assert.equal(await rowChip("climate.wintergarten"), label);
    assert.match(await text(page, `${hc(3)} .hcname .sub`), new RegExp(`\\| ${label}$`));
  }
  await page.eval("harness.plant.values['hc3.op_program'] = 1; harness.plant.values['hc3.badge'] = 0; harness.plant.push(); true"); // off_grey
  await sleep(80);
  assert.equal(await rowChip("climate.wintergarten"), "Aus");
  assert.deepEqual((await chipState(3)).map(([v, on, , title]) => [v, on, title]), [["comfort", false, null], ["eco", false, null], ["off", true, null]]);
  // choosing Komfort under a legacy program writes the app's control (op_program 0)
  await page.eval("harness.plant.values['hc3.op_program'] = 2; harness.plant.values['hc3.badge'] = 3; harness.plant.push(); true");
  await sleep(80);
  await click(page, `[data-act="preset"][data-c="3"][data-v="comfort"]`);
  await page.waitFor("harness.writes().length === 1", 2000);
  assert.deepEqual(await writes(page), ["climate.set_preset_mode climate.wintergarten comfort"]);
  assert.equal(await page.eval("harness.plant.values['hc3.op_program']"), 0);
  await page.waitFor(`${sr}.querySelector('[data-act="preset"][data-c="3"][data-v="comfort"]').classList.contains("on")`, 2000);
  assert.match(await text(page, `${hc(3)} .hcname .sub`), /\| Komfort$/);
  assert.equal(await rowChip("climate.wintergarten"), "Komfort");
  assert.deepEqual(page.errors, []);
});

test("DHW + cooling plant: season chips, water heater modes, boost, −/+ on DHW", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=full&view=controls&latency=30");
  await click(page, '[data-act="season"][data-c="1"][data-v="cool"]');
  await click(page, '[data-act="dhwmode"][data-v="eco"]');
  await page.waitFor("harness.writes().length === 2", 2000);
  await click(page, '[data-act="boost"]');
  await page.waitFor("harness.writes().length === 3", 2000);
  await page.waitFor(`/Boost läuft/.test(${sr}.querySelector('[data-act="boost"]').textContent)`, 2000);
  await click(page, '[data-act="dhwstep"][data-d="1"]');
  await click(page, '[data-act="dhwstep"][data-d="1"]');
  await page.waitFor("harness.writes().length === 4", 3000);
  assert.deepEqual(await writes(page), [
    "climate.set_hvac_mode climate.erdgeschoss cool",
    "water_heater.set_operation_mode water_heater.warmwasser eco",
    "button.press button.warmwasser_boost",
    "water_heater.set_temperature water_heater.warmwasser 61",
  ]);
});

test("schematic: the heat pump loop moves only while the heat pump runs, a circuit's pipe only while its pump runs", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=plant&latency=30", { width: 1280, height: 900 });
  const set = (vals) => page.eval(`(() => { Object.assign(harness.plant.values, ${JSON.stringify(vals)}); harness.plant.push(); return true; })()`);
  // wide layout: the heat pump's flow/return pipes start at x 140, the circuit branches run from the trunk (336) to the rows (372)
  const state = () => page.eval(`(() => { const ps = [...${sr}.querySelectorAll("svg.plant path")];
    const lit = (p) => p.classList.contains("flow");
    const hp = ps.filter((p) => /^M140 /.test(p.getAttribute("d")) && p.getAttribute("stroke") !== "var(--ohp-pipe)");
    const rows = ps.filter((p) => /^M336 [\\d.]+ H372$/.test(p.getAttribute("d")) && p.getAttribute("stroke") !== "var(--ohp-pipe)");
    return { hp: hp.map(lit), hpStroke: hp.map((p) => p.getAttribute("stroke")), rows: rows.map(lit) }; })()`);
  // standby (12) while the primary pump circulates: the loop stands still and is grey
  await set({ "hp.status": 12, "hp.flow_rate": 1.3, "hc1.pump": 1, "hc2.pump": 1, "hc3.pump": 0 });
  await sleep(60);
  let s = await state();
  assert.deepEqual(s.hp, [false, false], "standby: no motion between heat pump and buffer");
  assert.ok(s.hpStroke.every((c) => c === "var(--ohp-pipe-off)"), "standby: grey");
  assert.deepEqual(s.rows, [true, true, false], "Wintergarten pump off: its pipe stands still");
  for (const code of [11, 13]) { // pump pre-run / overrun: water moves, no heat
    await set({ "hp.status": code });
    await sleep(40);
    assert.deepEqual((await state()).hp, [false, false], `status ${code}`);
  }
  await set({ "hp.status": 1, "hc3.pump": 1 });
  await sleep(60);
  s = await state();
  assert.deepEqual(s.hp, [true, true], "heating: flow and return move");
  assert.deepEqual(s.rows, [true, true, true]);
  // defrost: same direction (the return keeps its reverse animation), defrost colour
  await set({ "hp.status": 4 });
  await sleep(60);
  s = await state();
  assert.deepEqual(s.hp, [true, true]);
  assert.ok(s.hpStroke.every((c) => c === "var(--ohp-defrost)"), "defrost colour");
  assert.equal(await page.eval(`${sr}.querySelectorAll("svg.plant path.flow.rev").length`), 1, "only the return runs backwards");
  assert.deepEqual(page.errors, []);
});

test("schematic trunk: water moves from the buffer feed to the farthest running circuit on each side, in its direction", { skip: !CHROME && "no chromium" }, async () => {
  // Coloured vertical trunk segments and the circuit branch heights, read from the paths
  const trunkOf = (page) => page.eval(`(() => { const ps = [...${sr}.querySelectorAll("svg.plant path")].filter((p) => p.getAttribute("stroke") !== "var(--ohp-pipe)");
    const q = (p) => p.getAttribute("d").split(" ");
    // branches are "M<trunk x> <y> H<row x>", trunk segments "M<trunk x> <from> V<to>"; the last branch belongs to the bottom row
    const br = ps.map(q).filter((d) => d.length === 3 && d[0][0] === "M" && d[2][0] === "H");
    const x = br[br.length - 1][0];
    const rowsY = br.filter((d) => d[0] === x).map((d) => +d[1]);
    const segs = ps.filter((p) => { const d = q(p); return d.length === 3 && d[0] === x && d[2][0] === "V"; })
      .map((p) => { const d = q(p); return { from: +d[1], to: +d[2].slice(1), on: p.classList.contains("flow") }; });
    return { rowsY, segs }; })()`);
  const reach = (t) => {
    const on = t.segs.filter((g) => g.on);
    return { top: Math.min(...on.map((g) => Math.min(g.from, g.to))), bot: Math.max(...on.map((g) => Math.max(g.from, g.to))), n: on.length };
  };
  for (const width of [1280, 360]) {
    const page = await open("variant=house&view=plant&latency=30", { width, height: 1000, mobile: width < 500 });
    const set = (vals) => page.eval(`(() => { Object.assign(harness.plant.values, ${JSON.stringify(vals)}); harness.plant.push(); return true; })()`);
    // Wintergarten (bottom) stopped: the trunk moves only down to Radiatoren
    await set({ "hp.status": 12, "hc1.pump": 1, "hc2.pump": 1, "hc3.pump": 0 });
    await sleep(60);
    let t = await trunkOf(page);
    assert.equal(t.rowsY.length, 3, `${width}: three branches`);
    assert.equal(reach(t).bot, t.rowsY[1], `${width}: moving part ends at Radiatoren`);
    assert.ok(t.segs.some((g) => !g.on && Math.max(g.from, g.to) === t.rowsY[2]), `${width}: the stub to Wintergarten stands still`);
    // every moving segment starts at the feed and runs away from it (up above it, down below it)
    const feed = t.segs.filter((g) => g.on).map((g) => g.from);
    assert.ok(feed.every((y) => y === feed[0]), `${width}: moving segments start at the feed`);
    if (width >= 500) {
      const upSeg = t.segs.find((g) => g.on && g.to < g.from);
      assert.ok(upSeg && upSeg.to === t.rowsY[0], "wide: the part above the feed runs up to Fußboden");
    }
    // Radiatoren (middle) stopped, Wintergarten running: water still passes Radiatoren
    await set({ "hc2.pump": 0, "hc3.pump": 1 });
    await sleep(60);
    t = await trunkOf(page);
    assert.equal(reach(t).bot, t.rowsY[2], `${width}: moving part reaches Wintergarten`);
    // nothing running: the trunk stands still
    await set({ "hc1.pump": 0, "hc2.pump": 0, "hc3.pump": 0 });
    await sleep(60);
    assert.equal(reach(await trunkOf(page)).n, 0, `${width}: no motion without a running pump`);
    assert.deepEqual(page.errors, []);
  }
});

test("tapping the schematic opens more-info for the related entity", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=plant");
  await click(page, 'svg.plant [data-more="climate.radiatoren"] rect');
  await click(page, 'svg.plant [data-more="sensor.puffertemperatur_1_top"] rect');
  const ev = await page.eval("harness.events.filter((e) => e.type === 'hass-more-info').map((e) => e.detail.entityId)");
  assert.deepEqual(ev, ["climate.radiatoren", "sensor.puffertemperatur_1_top"]);
  const sent = await page.eval("harness.fake.conn.sent.map((m) => m.type + ':' + (m.language || ''))");
  assert.deepEqual(sent, ["ochsner_local_ots/plants:de", "ochsner_local_ots/subscribe:"], "one plants call with language, one subscription");
  assert.deepEqual(page.errors, []);
});

test("panel: menu button, plant tabs and footer links", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=multi&view=panel&theme=light", { width: 390, height: 900, mobile: true });
  const psr = "harness.el.shadowRoot";
  const pc = (sel) => page.eval(`(() => { const e = ${psr}.querySelector(${JSON.stringify(sel)}); e.scrollIntoView({ block: "center" }); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  const tap = async (sel) => {
    const c = await pc(sel);
    await page.mouse("mousePressed", c[0], c[1], 1);
    await page.mouse("mouseReleased", c[0], c[1]);
    await sleep(120);
  };
  await tap(".menu");
  assert.equal(await page.eval("harness.events.filter((e) => e.type === 'hass-toggle-menu').length"), 1);
  const title = () => page.eval(`${psr}.querySelector("ochsner-heatpump-card").shadowRoot.querySelector(".card-plant .ttl").textContent`);
  assert.equal(await title(), "AirHawk Wärmepumpe");
  await tap('.tabs button[data-p$="192.0.2.81"]');
  await page.waitFor(`${psr}.querySelector("ochsner-heatpump-card").shadowRoot.querySelector(".card-plant .ttl").textContent === "Ochsner Wärmepumpe"`, 2000);
  await tap(".footer a");
  assert.equal(await page.eval("location.pathname"), "/config/devices/device/dev_main_full");
  assert.equal(await page.eval("harness.events.filter((e) => e.type === 'location-changed').length"), 1);
  assert.deepEqual(page.errors, []);
});

test("changing the configured view keeps the theme variables", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=all&theme=dark");
  const heat = () => page.eval(`getComputedStyle(harness.el).getPropertyValue("--ohp-heat").trim()`);
  assert.ok(await heat(), "scheme variables set");
  await page.eval(`harness.el.setConfig({ type: "custom:ochsner-heatpump-card", view: "controls" })`);
  await sleep(120);
  assert.ok(await page.eval(`!!${sr}.querySelector(".card-controls") && !${sr}.querySelector(".card-plant")`));
  assert.ok(await heat(), "variables survive the skeleton rebuild");
  assert.match(await page.eval(`${sr}.querySelector("style.vars").textContent`), /--ohp-heat/);
});

test("controller recovery re-enables the controls from the live feed alone", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=controls&unavailable=1&latency=30");
  assert.ok(await page.eval(`!!${sr}.querySelector(".banner")`), "unavailable banner");
  assert.ok(await page.eval(`${sr}.querySelector('.hc[data-key="hc1"] .sp.main [data-s="1"]').disabled`));
  const fetches = await page.eval("harness.fake.conn.sent.filter((m) => m.type === 'ochsner_local_ots/plants').length");
  await page.eval("harness.plant.available = true; harness.plant.push()");
  await sleep(120);
  assert.ok(await page.eval(`!${sr}.querySelector(".banner")`), "banner gone");
  assert.ok(await page.eval(`!${sr}.querySelector('.hc[data-key="hc1"] .sp.main [data-s="1"]').disabled`), "controls enabled");
  assert.equal(await page.eval("harness.fake.conn.sent.filter((m) => m.type === 'ochsner_local_ots/plants').length"), fetches, "without a refetch");
  // and the other way round: the feed reports the outage
  await page.eval("harness.plant.available = false; harness.plant.push()");
  await sleep(120);
  assert.ok(await page.eval(`!!${sr}.querySelector(".banner")`));
});

test("curve handles expose native slider values and ≥ 44 px hit areas; Auto uses HA's heat-cool colour", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=full&view=all&theme=light&scheme=ha", { width: 760 });
  const attrs = (id) => page.eval(`(() => { const e = ${sr}.querySelector('[data-h="${id}"]'); return ["role", "aria-valuenow", "aria-valuemin", "aria-valuemax", "aria-valuetext"].map((a) => e.getAttribute(a)); })()`);
  assert.deepEqual(await attrs("heat_norm"), ["slider", "35", "20", "90", "−15,0° → 35,0°"]);
  assert.deepEqual(await attrs("heat_limit"), ["slider", "16.7", "0", "30", "Heizgrenze 16,7°"]);
  assert.deepEqual((await attrs("heat_max")).slice(1, 4), ["40", "0", "70"]);
  const size = (sel) => page.eval(`(() => { const r = ${sr}.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; })()`);
  assert.ok((await size('[data-h="heat_limit"] .hit'))[0] >= 44);
  assert.ok((await size('[data-h="heat_max"] .hit'))[1] >= 44);
  assert.ok((await size('[data-h="heat_norm"] .hit'))[0] >= 44);
  // full variant: HC1 is in Auto (heat/cool) → the HA theme's heat-cool colour (amber)
  const bg = await page.eval(`getComputedStyle(${sr}.querySelector('[data-act="season"][data-c="1"][data-v="heat_cool"]')).backgroundColor`);
  assert.equal(bg, "rgb(255, 193, 7)");
});

test("history covers a full rolling 24 h window", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=efficiency");
  await page.waitFor(`!!${sr}.querySelector("svg.bars")`, 3000);
  const [req] = await page.eval("harness.fake.conn.history");
  const span = Date.parse(req.end_time) - Date.parse(req.start_time);
  assert.equal(span, 24 * 3600 * 1000);
  assert.ok(Math.abs(Date.parse(req.end_time) - Date.now()) < 60000, "ends now");
  assert.equal(await page.eval(`${sr}.querySelectorAll("svg.bars .bh").length`), 24);
  const labels = await page.eval(`[...${sr}.querySelectorAll("svg.bars text")].map((t) => t.textContent).filter((t) => /:00$/.test(t))`);
  assert.ok(labels.length >= 3 && labels.every((l) => ["00:00", "06:00", "12:00", "18:00"].includes(l)), JSON.stringify(labels));
});

test("efficiency tile labels are never cut off (DE/EN, 360/760/1280 px); live COP carries the * note", { skip: !CHROME && "no chromium" }, async () => {
  for (const lang of ["en", "de"]) {
    for (const width of [360, 760, 1280]) {
      const page = await open(`variant=house&view=all&lang=${lang}&width=${width}`, { width, mobile: width < 500 });
      await page.waitFor(`${sr}.querySelectorAll(".tile").length >= 6`, 3000);
      const bad = await page.eval(`[...${sr}.querySelectorAll(".tile span")].map((s) => ({ t: s.textContent, w: s.scrollWidth > s.clientWidth + 1, h: s.getBoundingClientRect().height > 28.5, e: getComputedStyle(s).textOverflow === "ellipsis" })).filter((x) => x.w || x.h || x.e)`);
      assert.deepEqual(bad, [], `${lang} ${width}px`);
      const cop = await page.eval(`(() => { const k = [...${sr}.querySelectorAll(".card-plant .kpi")].find((x) => /COP/.test(x.textContent)); return k ? [k.querySelector("b").textContent, k.getAttribute("title")] : null; })()`);
      assert.ok(cop && cop[0].endsWith("*"), JSON.stringify(cop));
      assert.match(cop[1], lang === "de" ? /Nur Verdichterstrom/ : /Compressor power only/);
    }
  }
});

test("heat pump Auto segment: theme heat-cool colour in the HA scheme, gradient in the Ochsner scheme", { skip: !CHROME && "no chromium" }, async () => {
  const bg = (page) => page.eval(`(() => { const s = getComputedStyle(${sr}.querySelector('[data-act="hp"][data-v="0"]')); return [s.backgroundColor, s.backgroundImage]; })()`);
  const ha = await bg(await open("variant=house&view=controls&theme=light&scheme=ha"));
  assert.deepEqual(ha, ["rgb(255, 193, 7)", "none"]);
  const och = await bg(await open("variant=house&view=controls&theme=dark&scheme=ochsner"));
  assert.match(och[1], /linear-gradient/);
});
