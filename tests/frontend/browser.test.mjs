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

test("−/+ burst: value updates at once, one coalesced climate write after 1.5 s", { skip: !CHROME && "no chromium" }, async () => {
  const page = await open("variant=house&view=controls&latency=50");
  const plus = '.hc[data-key="hc1"] [data-d="1"]';
  for (let i = 0; i < 3; i++) await click(page, plus);
  assert.match(await text(page, '.hc[data-key="hc1"] .stepper .v'), /23,0°/);
  assert.match(await text(page, '.hc[data-key="hc1"]'), /Korrektur \+1,5 K/);
  assert.match(await text(page, '.hc[data-key="hc1"] .note'), /wird gesendet, sobald du aufhörst/);
  await sleep(900);
  assert.deepEqual(await writes(page), []);
  await page.waitFor("harness.writes().length === 1", 2000);
  assert.deepEqual(await writes(page), ["climate.set_temperature climate.fussboden 23"]);
  await page.waitFor(`/gespeichert/.test((${sr}.querySelector('.hc[data-key="hc1"] .note') || {}).textContent)`, 2000);
  // + then − back to the current value → no write at all
  await click(page, plus);
  await click(page, '.hc[data-key="hc1"] [data-d="-1"]');
  await sleep(1900);
  assert.equal((await writes(page)).length, 1);
  assert.match(await text(page, '.hc[data-key="hc1"] .stepper .v'), /23,0°/);
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
