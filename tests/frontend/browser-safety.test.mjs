// Write-safety tests in a real browser: queued writes never outlive the
// card, the plant or the mode they were made for; curve Apply revalidates
// and orders its writes; unit conversion at the service-call boundary.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { CHROME, browser, on, sleep, writes } from "./harness/browser-helpers.mjs";

const skip = !CHROME && "no chromium";
let b;
before(async () => { if (CHROME) b = await browser(); });
after(async () => { if (b) await b.close(); });

test("removing the card cancels a queued −/+ write", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .sp.main .v'), /22,0°/);
  await page.eval("harness.el.remove()");
  await sleep(2200);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

/** Record pending setTimeouts with the frame that created them. */
const trackTimers = (page) => page.eval(`(() => {
  const live = new Map();
  window.__timers = live;
  const st = window.setTimeout, ct = window.clearTimeout;
  window.setTimeout = function (fn, ms, ...args) {
    const where = (new Error().stack.split("\\n")[2] || "").trim();
    const id = st.call(window, function () { live.delete(id); return fn.apply(this, arguments); }, ms, ...args);
    live.set(id, where);
    return id;
  };
  window.clearTimeout = function (id) { live.delete(id); return ct.call(window, id); };
  return true;
})()`);
// Timers of the card and its views (the shared store keeps its own, documented stop delay).
const cardTimers = (page) => page.eval(`[...__timers.values()].filter((w) => /\\/frontend\\/(views|lib)\\//.test(w) && !/store\\.js/.test(w))`);

test("removing the card during a preset request leaves no timers behind", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=300");
  const c = on(page);
  await trackTimers(page);
  await c.click('[data-act="hp"][data-v="3"]'); // arms the off confirmation (timer)
  await c.click('.hc[data-key="hc2"] .sp.main [data-s="1"]'); // queued write (timer)
  await c.click('[data-act="preset"][data-c="1"][data-v="eco"]'); // request in flight
  assert.ok((await cardTimers(page)).length >= 2, JSON.stringify(await cardTimers(page)));
  await page.eval("harness.el.remove()");
  assert.deepEqual(await cardTimers(page), [], "cleared on disconnect");
  await sleep(500); // the preset request completes after the removal
  assert.deepEqual(await writes(page), ["climate.set_preset_mode climate.fussboden eco"]);
  assert.deepEqual(await cardTimers(page), [], "no timer scheduled by the late completion");
  assert.deepEqual(page.errors, []);
});

const CARD = 'harness.el.shadowRoot.querySelector("ochsner-heatpump-card").shadowRoot';

async function panelTab(page, host) {
  const p = on(page, "harness.el.shadowRoot");
  await p.click(`.tabs button[data-p$="${host}"]`);
  await sleep(150);
}

test("control state is per plant: switching plants cancels A's queued edits and never writes to B", { skip }, async () => {
  const page = await b.open("variant=multi&view=panel&theme=light&latency=30", { width: 1280, height: 1600 });
  const c = on(page, CARD);
  // A: stepper burst + a pending curve edit
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight", "ArrowRight");
  assert.ok(await c.exists(".pendbar"));
  await panelTab(page, "192.0.2.81");
  assert.match(await c.text(".card-plant .ttl"), /Ochsner Wärmepumpe/);
  // B shows nothing pending, Enter on a B handle applies nothing
  assert.ok(!(await c.exists(".pendbar")), "no pending bar on B");
  assert.ok(!(await c.exists(".stepper .v.pend")), "no pending stepper on B");
  await c.focusKey('[data-h="heat_limit"]', "Enter");
  await sleep(1800);
  assert.deepEqual(await writes(page), [], "A's queued write was cancelled, nothing went to B");
  // B's own stepper writes to B's entity only
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  await page.waitFor("harness.writes().length === 1", 3000);
  assert.deepEqual(await writes(page), ["number.set_value number.wp2_erdgeschoss_raumsollwert_normal_kuhlen 24.5"]);
  // back on A: the cancelled edits stay gone
  await panelTab(page, "192.0.2.80");
  assert.ok(!(await c.exists(".pendbar")));
  assert.ok(!(await c.exists(".stepper .v.pend")));
  assert.deepEqual(page.errors, []);
});

test("switching plants during an Apply stops its remaining writes; nothing goes to B", { skip }, async () => {
  const page = await b.open("variant=multi&view=panel&theme=light&latency=300", { width: 1280, height: 1600 });
  const c = on(page, CARD);
  await c.focusKey('[data-h="heat_norm"]', "ArrowUp", "ArrowLeft");
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 1", 2000);
  await panelTab(page, "192.0.2.81");
  await sleep(1500);
  const w = await writes(page);
  assert.equal(w.length, 1, "only the write already on the wire: " + JSON.stringify(w));
  assert.match(w[0], /^number\.set_value number\.(?!wp2_)fussboden_/);
  assert.ok(!(await c.exists(".pendbar")), "B shows nothing of A's apply");
  assert.deepEqual(page.errors, []);
});

test("an Apply never continues on a replaced connection", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=300");
  const c = on(page);
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 1", 2000);
  // the card is handed the hass of another HA instance while write 1 runs
  await page.eval(`(() => {
    harness.detached = true;
    const other = harness.fakeHass(harness.plants).hass;
    window.otherCalls = [];
    other.callService = (d, s, data) => { otherCalls.push(d + "." + s + " " + data.entity_id); return Promise.resolve(); };
    harness.el.hass = other;
    return true;
  })()`);
  await sleep(1200);
  assert.deepEqual(await page.eval("otherCalls"), [], "nothing was sent to connection B");
  assert.deepEqual(await writes(page), ["number.set_value number.fussboden_maximale_vorlauftemperatur 41"], "and nothing more to A");
  assert.deepEqual(page.errors, []);
});

test("a mode change drops a queued DHW temperature edit (Comfort 52 → + → Eco 46)", { skip }, async () => {
  const page = await b.open("variant=full&view=controls&latency=30");
  const c = on(page);
  assert.match(await c.text('[data-key="dhw"] .stepper .v'), /52°/);
  await c.click('[data-act="dhwstep"][data-d="1"]');
  await c.click('[data-act="dhwmode"][data-v="eco"]');
  await sleep(2200);
  assert.deepEqual(await writes(page), ["water_heater.set_operation_mode water_heater.warmwasser eco"]);
  assert.equal(await page.eval("harness.plant.values['dhw.setpoint_eco']"), 46, "eco setpoint untouched");
  assert.ok(!(await c.exists('[data-key="dhw"] .stepper .v.pend')));
});

test("a queued circuit edit is dropped when the preset changes externally", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  await c.click('.hc[data-key="hc2"] .sp.main [data-s="1"]');
  // hc1: the controller switches to eco by itself (time program)
  await page.eval("harness.plant.values['hc1.comfort_eco'] = 1; harness.plant.push()");
  await sleep(2200);
  assert.deepEqual(await writes(page), ["number.set_value number.radiatoren_raumsollwert_normal_heizen 22"], "only hc2's edit is written");
  assert.match(await c.text('.hc[data-key="hc1"] .note'), /verworfen/);
  // our own preset click also drops the queued edit, silently
  await c.click('.hc[data-key="hc3"] .sp.main [data-s="1"]');
  await c.click('[data-act="preset"][data-c="3"][data-v="eco"]');
  await sleep(2200);
  assert.deepEqual((await writes(page)).slice(1), ["climate.set_preset_mode climate.wintergarten eco"]);
});

test("a queued circuit edit is dropped when a refetch maps the setpoint to another entity", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .sp.main .v'), /22,0°/);
  // same plant, new payload: HC1's normal setpoint is now served by another number entity
  await page.eval("harness.plant.roleOverrides['hc1.setpoint_normal_heat'] = { entity_id: 'number.fussboden_neu' }; harness.el.store.refetch(true); true");
  await sleep(2200);
  assert.deepEqual(await writes(page), [], "nothing goes to the old entity (nor to the new one)");
  assert.ok(!(await c.exists('.hc[data-key="hc1"] .sp.main .v.pend')));
  assert.match(await c.text('.hc[data-key="hc1"] .note'), /verworfen/);
  assert.deepEqual(page.errors, []);
});

test("a queued circuit edit is dropped when the feed hides the circuit controls (BMS)", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  await c.click('.hc[data-key="hc2"] .sp.main [data-s="1"]');
  // the feed reports BMS: no circuit controls any more; the climate
  // entities still exist in HA until the entry reloads
  await page.eval(`(() => {
    const p = harness.plant;
    p.values["avail.bms"] = 1;
    const ev = p.event();
    p.values["avail.bms"] = 0;
    for (const cb of p._subs) cb(ev);
    return true;
  })()`);
  await sleep(100);
  assert.ok(!(await c.exists('.hc[data-key="hc1"]')), "circuit controls hidden");
  await sleep(2100);
  assert.deepEqual(await writes(page), []);
  assert.equal(await page.eval("harness.el._views.controls.coal.size"), 0);
  assert.deepEqual(page.errors, []);
});

test("a queued edit is re-checked right before sending (binding lost between renders)", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  // the controller becomes unavailable; no render has run yet when the timer fires
  await page.eval(`(() => {
    harness.el.requestRender = () => {};
    harness.el.store.live.get(harness.plant.plantId).available = false;
    return true;
  })()`);
  await sleep(2200);
  assert.deepEqual(await writes(page), []);
  assert.equal(await page.eval("harness.el._views.controls.coal.get('hc1.setpoint_normal_heat').reason"), "invalid");
});

const curveState = (page, n = 1) => page.eval(`(() => { const s = harness.el._views.efficiency.curve.st.get(${n}); return s ? { pending: { ...s.pending }, error: s.error, applying: !!s.applying } : null; })()`);
const setPending = (page, pending, n = 1) => page.eval(`(() => { const ed = harness.el._views.efficiency.curve; ed._s(${n}).pending = ${JSON.stringify(pending)}; harness.el.requestRender(); })()`);

test("Apply orders writes so the points never cross (base point first when it widens the gap)", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=60");
  const c = on(page);
  await page.eval("harness.plant.values['hc1.heat_curve_base_outdoor'] = -5; harness.plant.push()");
  await sleep(100);
  await setPending(page, { heat_curve_norm_outdoor: 0, heat_curve_base_outdoor: 5 });
  await sleep(80);
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 2", 3000);
  assert.deepEqual(await writes(page), [
    "number.set_value number.fussboden_fusspunkt_aussentemperatur 5",
    "number.set_value number.fussboden_norm_aussentemperatur 0",
  ]);
});

test("Apply revalidates against the newest live values and refuses a crossed curve", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=60");
  const c = on(page);
  await c.focusKey('[data-h="heat_norm"]', "+ArrowRight", "+ArrowRight"); // −15 → −14
  assert.deepEqual((await curveState(page)).pending, { heat_curve_norm_outdoor: -14 });
  // meanwhile the base point was moved on the controller to −14.5
  await page.eval("harness.plant.values['hc1.heat_curve_base_outdoor'] = -14.5; harness.plant.push()");
  await sleep(80);
  await c.click('[data-cact="apply"]');
  await sleep(300);
  assert.deepEqual(await writes(page), []);
  assert.match(await c.text(".pendbar .err"), /Nicht übernommen: Die Norm-Außentemperatur muss mindestens 1 K unter/);
  // out-of-range / off-grid values are refused too
  await setPending(page, { heat_curve_max_flow: 41.5 });
  await sleep(60);
  await c.click('[data-cact="apply"]');
  await sleep(200);
  assert.match(await c.text(".pendbar .err"), /Schrittweite 1/);
  await setPending(page, { heat_curve_norm_flow: 95 });
  await sleep(60);
  await c.click('[data-cact="apply"]');
  await sleep(200);
  assert.match(await c.text(".pendbar .err"), /zwischen 20 und 90/);
  assert.deepEqual(await writes(page), []);
});

test("handles are frozen while Apply runs; no edit gets lost", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=400");
  const c = on(page);
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
  await c.click('[data-cact="apply"]');
  await sleep(100);
  assert.equal((await curveState(page)).applying, true);
  assert.ok(await c.exists('[data-h="heat_norm"].frozen'));
  // keyboard and pointer edits during the Apply are ignored
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight", "ArrowRight");
  await c.drag('[data-h="heat_norm"] .dotv', 0, -40);
  const mid = (await curveState(page)).pending;
  assert.ok(!("heat_curve_norm_flow" in mid) && !("heat_curve_norm_outdoor" in mid), JSON.stringify(mid));
  assert.equal(mid.heat_limit, 16.8, "the submitted value, not a later nudge");
  await page.waitFor("harness.writes().length === 2", 3000);
  await page.waitFor(`!${c.root}.querySelector(".pendbar")`, 3000);
  assert.deepEqual((await curveState(page)).pending, {});
  assert.deepEqual(await writes(page), [
    "number.set_value number.fussboden_maximale_vorlauftemperatur 41",
    "number.set_value number.fussboden_heizgrenze 16.8",
  ]);
});

// Each change hits the second write (heating limit) while the first one is on the wire.
const refetch = "harness.el.store.refetch(true); true";
for (const [what, change, why] of [
  ["its role is mapped to another entity", `harness.plant.roleOverrides["hc1.heat_limit"] = { entity_id: "number.fussboden_heizgrenze_neu" }; ${refetch}`, /Heizgrenze gehört inzwischen zu einer anderen Entität/],
  ["its role is no longer writable", `harness.plant.roleOverrides["hc1.heat_limit"] = { writable: false }; ${refetch}`, /Heizgrenze ist nicht mehr beschreibbar/],
  ["its entity is disabled", `harness.plant.roleOverrides["hc1.heat_limit"] = { enabled: false }; ${refetch}`, /Heizgrenze ist nicht mehr beschreibbar/],
  ["its bounds changed", `harness.plant.roleOverrides["hc1.heat_limit"] = { max: 16 }; ${refetch}`, /Für Heizgrenze gelten inzwischen andere Grenzen/],
  ["the circuit is gone", "harness.plant.values['hc1.availability'] = 0; harness.plant.push(); true", /Heizkreis ist nicht mehr vorhanden/],
  ["the controller became unavailable", "harness.plant.available = false; harness.plant.push(); true", /Anlage ist nicht erreichbar/],
]) {
  test(`Apply re-checks the binding before every write: stops when ${what}`, { skip }, async () => {
    const page = await b.open("variant=house&view=efficiency&latency=300");
    const c = on(page);
    await c.focusKey('[data-h="heat_max"]', "ArrowUp");
    await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
    await c.click('[data-cact="apply"]');
    await page.waitFor("harness.plant.calls.length === 1", 2000);
    await page.eval(change);
    await sleep(900);
    assert.deepEqual(await writes(page), ["number.set_value number.fussboden_maximale_vorlauftemperatur 41"], "nothing goes to the old binding");
    const st = await curveState(page);
    assert.deepEqual(st.pending, { heat_limit: 16.8 }, "the unsent edit stays pending");
    assert.equal(st.applying, false);
    assert.match(st.error, why);
    assert.match(st.error, /^Nicht übernommen: /);
    if (await c.exists(".pendbar")) assert.match(await c.text(".pendbar .err"), why);
    await sleep(600);
    assert.equal((await writes(page)).length, 1, "and nothing is retargeted later");
    assert.deepEqual(page.errors, []);
  });
}

test("Apply re-checks the binding before the first write too (changed after the last render)", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=30");
  const c = on(page);
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
  // the payload maps the role elsewhere; no render has run since
  await page.eval(`(() => {
    harness.el.requestRender = () => {};
    harness.plant.roleOverrides["hc1.heat_limit"] = { entity_id: "number.fussboden_heizgrenze_neu" };
    return harness.el.store.refetch(true).then(() => harness.el._views.efficiency.curve.apply(1)).then(() => true);
  })()`);
  await sleep(200);
  assert.deepEqual(await writes(page), []);
  const st = await curveState(page);
  assert.deepEqual(st.pending, { heat_limit: 16.8 });
  assert.match(st.error, /Heizgrenze gehört inzwischen zu einer anderen Entität/);
});

test("Fahrenheit HA: curve maths stay native, number.set_value gets °F", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=30&fahrenheit=1");
  const c = on(page);
  // the editor shows and edits native °C values
  assert.match(await c.text('[data-h="heat_limit"] text'), /16,7°/);
  await c.focusKey('[data-h="heat_norm"]', "ArrowUp");
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
  assert.match(await c.text(".pendbar"), /Norm-Vorlauf 36 → 36,1 °C/);
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 2", 3000);
  assert.deepEqual(await writes(page), [
    "number.set_value number.fussboden_vorlauftemperatur_bei_norm_aussentemperatur 96.98",
    "number.set_value number.fussboden_heizgrenze 62.24",
  ]);
  // HA hands the integration native values again; the feed confirms them
  // (a call is recorded before the fake's latency has passed)
  await page.waitFor("harness.plant.values['hc1.heat_limit'] !== 16.7", 2000);
  assert.equal(await page.eval("harness.plant.values['hc1.heat_curve_norm_flow']"), 36.1);
  assert.equal(await page.eval("harness.plant.values['hc1.heat_limit']"), 16.8);
  await page.waitFor(`!${c.root}.querySelector(".pendbar")`, 3000);
});

test("Fahrenheit HA: setpoints stay native, the DHW gauge works in HA's unit", { skip }, async () => {
  const page = await b.open("variant=full&view=controls&latency=30&fahrenheit=1");
  const c = on(page);
  // main setpoint: normal cooling 24 °C, shown natively (°C) like the other feed values
  assert.match(await c.text('.hc[data-key="hc1"] .sp.main .v'), /^24,0°C/);
  await c.click('.hc[data-key="hc1"] .sp.main [data-s="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .sp.main .v'), /^24,5°C/);
  const scale = await page.eval(`[...${c.root}.querySelectorAll(".gscale span")].map((s) => s.textContent)`);
  assert.ok(parseInt(scale[0], 10) <= 125 && parseInt(scale[2], 10) >= 130, JSON.stringify(scale));
  assert.match(scale[1], /Soll 126°F/);
  await c.click('[data-act="dhwstep"][data-d="1"]');
  await page.waitFor("harness.writes().length === 2", 3000);
  assert.deepEqual(await writes(page), [
    "number.set_value number.erdgeschoss_raumsollwert_normal_kuhlen 76.1",
    "water_heater.set_temperature water_heater.warmwasser 126",
  ]);
});

test("Fahrenheit HA: every native temperature says °C, HA-unit values say °F", { skip }, async () => {
  const texts = (page, root, sel) => page.eval(`[...${root}.querySelectorAll(${JSON.stringify(sel)})].map((e) => e.textContent)`);
  const page = await b.open("variant=house&view=all&latency=30&fahrenheit=1", { width: 1280, height: 1400 });
  const c = on(page);
  // schematic: flow/return/buffer labels and circuit rows (native feed values)
  const svg = await page.eval(`${c.root}.querySelector("svg.plant").textContent`);
  assert.match(svg, /VL 29,6°CRL 25,1°CPuffer26,3°C/);
  assert.match(svg, /Vorlauf 25,9°C · Soll 26,8°C/);
  assert.doesNotMatch(svg.replace(/°[CF]/g, ""), /°/, "no bare degree sign in the schematic");
  // controls: setpoints and feed flow values in °C
  assert.match(await c.text('.hc[data-key="hc1"] .sp.main .v span'), /^21,5°C$/);
  assert.match(await c.text('.hc[data-key="hc1"]'), /Vorlauf 25,9°C \/ 26,8°C/);
  assert.match(await c.text(".card-controls .hero .sub"), /Puffer 26,3°C · VL 29,6°C → RL 25,1°C/);
  // curve: axis ticks, handles (label, slider text), now label, floating label, crosshair
  const ticks = await texts(page, c.root, "svg.ch[data-curve] text.ax");
  assert.ok(ticks.length > 8 && ticks.every((t) => /°C$|^jetzt .*°C$/.test(t)), JSON.stringify(ticks));
  assert.match(await c.text('[data-h="heat_limit"] text'), /^Heizgrenze 16,7°C$/);
  assert.equal(await page.eval(`${c.root}.querySelector('[data-h="heat_norm"]').getAttribute("aria-valuetext")`), "−15,0°C → 36,0°C");
  await c.focusKey('[data-h="heat_norm"]', "ArrowUp");
  assert.equal(await c.text(".flab text"), "−15,0°C → 36,1°C");
  assert.match(await c.text(".readout"), /bei 12,2°C außen: Vorlauf 26,8°C → /);
  const [x, y] = await c.center("svg.ch[data-curve] .plotbg");
  await page.mouse("mouseMoved", x, y);
  await sleep(60);
  assert.match(await c.text(".tip.cur"), /^außen [−\d,]+°C → Vorlauf-Soll [\d,]+°C$/);
  // the same plant in °C keeps the short "°"
  const pc = await b.open("variant=house&view=all&latency=30", { width: 1280, height: 1400 });
  assert.match(await pc.eval(`harness.el.shadowRoot.querySelector("svg.plant").textContent`), /VL 29,6°RL 25,1°Puffer26,3°off/);
  const cTicks = await texts(pc, "harness.el.shadowRoot", "svg.ch[data-curve] text.ax");
  assert.ok(cTicks.every((t) => !/°C/.test(t)), JSON.stringify(cTicks));
  assert.deepEqual(page.errors, []);
});

const toFahrenheit = (page) => page.eval("harness.plant.fahrenheit = true; harness.fake.rebuild(); true");
const plantFetches = (page) => page.eval("harness.fake.conn.sent.filter((m) => m.type === 'ochsner_local_ots/plants').length");

test("HA switched to °F after the payload loaded: Apply refuses, reloads, then converts", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=30");
  const c = on(page);
  await c.focusKey('[data-h="heat_norm"]', "ArrowLeft"); // norm outdoor −15 → −15.1 (native)
  assert.deepEqual((await curveState(page)).pending, { heat_curve_norm_outdoor: -15.1 });
  const fetched = await plantFetches(page);
  // the entities now report °F; the loaded role metadata still says °C only
  await toFahrenheit(page);
  await sleep(60);
  await c.click('[data-cact="apply"]');
  await sleep(200);
  assert.deepEqual(await writes(page), [], "−15.1 must not go to a °F entity");
  assert.match(await c.text(".pendbar .err"), /Nicht übernommen: Norm-Außentemp\. wird inzwischen in °F angezeigt/);
  assert.deepEqual((await curveState(page)).pending, { heat_curve_norm_outdoor: -15.1 }, "the edit is kept");
  assert.ok((await plantFetches(page)) > fetched, "the payload was reloaded");
  // with the fresh metadata the same edit is converted once
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.writes().length === 1", 3000);
  assert.deepEqual(await writes(page), ["number.set_value number.fussboden_norm_aussentemperatur 4.82"]);
  assert.equal(await page.eval("harness.plant.values['hc1.heat_curve_norm_outdoor']"), -15.1);
});

test("a unit change during Apply stops before the next write", { skip }, async () => {
  const page = await b.open("variant=house&view=efficiency&latency=300");
  const c = on(page);
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight");
  await c.click('[data-cact="apply"]');
  await page.waitFor("harness.plant.calls.length === 1", 2000);
  await toFahrenheit(page);
  await sleep(900);
  assert.deepEqual(await writes(page), ["number.set_value number.fussboden_maximale_vorlauftemperatur 41"]);
  assert.match(await c.text(".pendbar .err"), /Heizgrenze wird inzwischen in °F angezeigt/);
  assert.deepEqual((await curveState(page)).pending, { heat_limit: 16.8 });
});
