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
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .stepper .v'), /22,0°/);
  await page.eval("harness.el.remove()");
  await sleep(2200);
  assert.deepEqual(await writes(page), []);
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
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
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
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  await page.waitFor("harness.writes().length === 1", 3000);
  assert.deepEqual(await writes(page), ["climate.set_temperature climate.wp2_erdgeschoss 24.5"]);
  // back on A: the cancelled edits stay gone
  await panelTab(page, "192.0.2.80");
  assert.ok(!(await c.exists(".pendbar")));
  assert.ok(!(await c.exists(".stepper .v.pend")));
  assert.deepEqual(page.errors, []);
});

test("an Apply that is running when the plant changes only writes A's entities", { skip }, async () => {
  const page = await b.open("variant=multi&view=panel&theme=light&latency=300", { width: 1280, height: 1600 });
  const c = on(page, CARD);
  await c.focusKey('[data-h="heat_norm"]', "ArrowUp", "ArrowLeft");
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.click('[data-cact="apply"]');
  await panelTab(page, "192.0.2.81");
  await page.waitFor("harness.writes().length === 3", 4000);
  await sleep(400);
  const w = await writes(page);
  assert.equal(w.length, 3);
  for (const x of w) assert.match(x, /^number\.set_value number\.(?!wp2_)fussboden_/, x);
  assert.ok(!(await c.exists(".pendbar")), "B shows nothing of A's apply");
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
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  await c.click('.hc[data-key="hc2"] [data-d="1"]');
  // hc1: the controller switches to eco by itself (time program)
  await page.eval("harness.plant.values['hc1.comfort_eco'] = 1; harness.plant.push()");
  await sleep(2200);
  assert.deepEqual(await writes(page), ["climate.set_temperature climate.radiatoren 22"], "only hc2's edit is written");
  assert.match(await c.text('.hc[data-key="hc1"] .note'), /verworfen/);
  // our own preset click also drops the queued edit, silently
  await c.click('.hc[data-key="hc3"] [data-d="1"]');
  await c.click('[data-act="preset"][data-c="3"][data-v="eco"]');
  await sleep(2200);
  assert.deepEqual((await writes(page)).slice(1), ["climate.set_preset_mode climate.wintergarten eco"]);
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
  assert.equal(await page.eval("harness.plant.values['hc1.heat_curve_norm_flow']"), 36.1);
  assert.equal(await page.eval("harness.plant.values['hc1.heat_limit']"), 16.8);
  await page.waitFor(`!${c.root}.querySelector(".pendbar")`, 3000);
});

test("Fahrenheit HA: steppers and the DHW gauge work in HA's unit", { skip }, async () => {
  const page = await b.open("variant=full&view=controls&latency=30&fahrenheit=1");
  const c = on(page);
  // climate target 24 °C = 75.2 °F (cooling target of the full variant)
  assert.match(await c.text('.hc[data-key="hc1"] .stepper .v'), /75,2°F/);
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .stepper .v'), /75,5°F/, "snaps to the 0.5° grid of HA's unit");
  assert.match(await c.text('.hc[data-key="hc1"]'), /Korrektur \+0,2 K/, "0.3 °F = 0.17 K");
  const scale = await page.eval(`[...${c.root}.querySelectorAll(".gscale span")].map((s) => s.textContent)`);
  assert.ok(parseInt(scale[0], 10) <= 125 && parseInt(scale[2], 10) >= 130, JSON.stringify(scale));
  assert.match(scale[1], /Soll 126°F/);
  await c.click('[data-act="dhwstep"][data-d="1"]');
  await page.waitFor("harness.writes().length === 2", 3000);
  assert.deepEqual(await writes(page), [
    "climate.set_temperature climate.erdgeschoss 75.5",
    "water_heater.set_temperature water_heater.warmwasser 126",
  ]);
});
