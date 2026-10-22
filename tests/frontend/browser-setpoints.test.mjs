// Card B setpoint fold-out and room sensors in a real browser: folding,
// coalesced number.set_value writes on the role's entity, bounds, the
// "active" tag, cooling rows, read-only entities; room readings on cards A/B.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { CHROME, browser, on, sleep, writes } from "./harness/browser-helpers.mjs";

const skip = !CHROME && "no chromium";
let b;
before(async () => { if (CHROME) b = await browser(); });
after(async () => { if (b) await b.close(); });

const tile = (n) => `.hc[data-key="hc${n}"]`;
const row = (n, key) => `${tile(n)} .sprow[data-key="hc${n}.${key}"]`;
const btn = (n, key, d) => `${row(n, key)} [data-s="${d}"]`;
const eid = (page, key) => page.eval(`harness.plant.entities()[${JSON.stringify(key)}].entity_id`);
const rowKeys = (page, c, n) => page.eval(`[...${c.root}.querySelectorAll('${tile(n)} .sprow')].map((e) => e.dataset.key.split(".")[1])`);
const activeRows = (page, c) => page.eval(`[...${c.root}.querySelectorAll('.sprow')].filter((e) => e.querySelector(".sptag")).map((e) => e.dataset.key)`);
const disabled = (page, c, sel) => page.eval(`${c.root}.querySelector(${JSON.stringify(sel)}).disabled`);
/** Change feed values and push an update (like a coordinator refresh). */
const setValues = (page, vals) => page.eval(`(() => { Object.assign(harness.plant.values, ${JSON.stringify(vals)}); harness.plant.push(); return true; })()`);

test("setpoint fold-out: closed by default, toggles, remembered per circuit for the session", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  for (const n of [1, 2, 3]) {
    assert.equal(await page.eval(`${c.root}.querySelector('${tile(n)} .sptoggle').getAttribute("aria-expanded")`), "false");
    assert.ok(!(await c.exists(`${tile(n)} .sprow`)));
  }
  assert.match(await c.text(`${tile(3)} .sptoggle`), /Sollwerte.*25,0° \/ 10,0°/);
  await c.click(`${tile(3)} .sptoggle`);
  assert.equal(await page.eval(`${c.root}.querySelector('${tile(3)} .sptoggle').getAttribute("aria-expanded")`), "true");
  assert.deepEqual(await rowKeys(page, c, 3), ["setpoint_normal_heat", "setpoint_reduced_heat"]);
  assert.match(await c.text(row(3, "setpoint_normal_heat")), /Normal \(Komfort, Tag\)\s*−\s*25,0°/);
  assert.match(await c.text(row(3, "setpoint_reduced_heat")), /Reduziert \(Eco, Nacht\)aktiv\s*−\s*10,0°/);
  assert.ok(!(await c.exists(`${tile(1)} .sprow`)), "other circuits stay closed");
  // a new card instance (e.g. the dashboard re-renders it) and a reload keep it open
  await page.eval("(() => { const p = harness.el.parentNode; harness.el.remove(); p.appendChild(harness.el); return true; })()");
  await sleep(100);
  assert.ok(await c.exists(row(3, "setpoint_reduced_heat")));
  await page.eval("location.reload(), true");
  await page.eval("new Promise((r) => { const t = () => (window.__ready ? r(true) : setTimeout(t, 30)); t(); })");
  assert.ok(await c.exists(row(3, "setpoint_reduced_heat")), "remembered across a reload (session)");
  assert.ok(!(await c.exists(`${tile(1)} .sprow`)));
  await c.click(`${tile(3)} .sptoggle`);
  assert.ok(!(await c.exists(`${tile(3)} .sprow`)));
  assert.deepEqual(await writes(page), [], "folding writes nothing");
  assert.deepEqual(page.errors, []);
});

test("a + burst on Reduziert writes exactly one number.set_value with the final value", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click(`${tile(3)} .sptoggle`);
  for (let i = 0; i < 4; i++) await c.click(btn(3, "setpoint_reduced_heat", 1));
  assert.match(await c.text(`${row(3, "setpoint_reduced_heat")} .v.pend`), /10,4°/, "optimistic value, role step 0.1");
  assert.match(await c.text(tile(3)), /wird gesendet, sobald du aufhörst/);
  assert.deepEqual(await writes(page), [], "nothing before the idle delay");
  await sleep(1900);
  const ent = await eid(page, "hc3.setpoint_reduced_heat");
  assert.deepEqual(await writes(page), [`number.set_value ${ent} 10.4`]);
  // the controller recomputed the target; the tag stays on the reduced setpoint
  assert.equal(await page.eval("harness.plant.values['hc3.room_target_heat']"), 10.4);
  assert.match(await c.text(`${tile(3)} .stepper .v`), /10,4°/);
  assert.deepEqual(await activeRows(page, c), ["hc3.setpoint_reduced_heat"]);
  assert.ok(!(await c.exists(`${row(3, "setpoint_reduced_heat")} .v.pend`)));
  // + then − back to the current value: no write at all
  await c.click(btn(3, "setpoint_reduced_heat", 1));
  await c.click(btn(3, "setpoint_reduced_heat", -1));
  await sleep(1900);
  assert.equal((await writes(page)).length, 1);
  assert.deepEqual(page.errors, []);
});

test("setpoint steppers respect the role's bounds", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click(`${tile(3)} .sptoggle`);
  // reduced heating at its minimum (10): − disabled
  assert.equal(await disabled(page, c, btn(3, "setpoint_reduced_heat", -1)), true);
  assert.equal(await disabled(page, c, btn(3, "setpoint_reduced_heat", 1)), false);
  // normal heating one step below its maximum (30)
  await setValues(page, { "hc3.setpoint_normal_heat": 29.9 });
  await sleep(60);
  await c.click(btn(3, "setpoint_normal_heat", 1));
  assert.match(await c.text(`${row(3, "setpoint_normal_heat")} .v`), /30,0°/);
  assert.equal(await disabled(page, c, btn(3, "setpoint_normal_heat", 1)), true, "+ disabled at the maximum");
  await sleep(1900);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc3.setpoint_normal_heat")} 30`]);
  assert.deepEqual(page.errors, []);
});

test("active tag: normal by day, reduced when target = reduced + offset, none for frost", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  for (const n of [1, 3]) await c.click(`${tile(n)} .sptoggle`);
  assert.deepEqual(await activeRows(page, c), ["hc1.setpoint_normal_heat", "hc3.setpoint_reduced_heat"]);
  assert.match(await c.text(`${row(1, "setpoint_normal_heat")} .sptag`), /^aktiv$/);
  // Komfort at night: the controller follows the reduced setpoint (19) + offset 0.5
  await setValues(page, { "hc1.room_offset": 0.5, "hc1.room_target_heat": 19.5 });
  await sleep(60);
  assert.deepEqual(await activeRows(page, c), ["hc1.setpoint_reduced_heat", "hc3.setpoint_reduced_heat"]);
  // frost protection: 12 matches neither
  await setValues(page, { "hc1.room_target_heat": 12, "hc1.badge": 16 });
  await sleep(60);
  assert.deepEqual(await activeRows(page, c), ["hc3.setpoint_reduced_heat"]);
  // status line keeps the legacy program label
  assert.match(await c.text(`${tile(3)} .hcname .sub`), /Heizen \| Reduziert/);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("cooling setpoints only on cool-capable circuits; the cooling target tags a cooling row", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  for (const n of [1, 2, 3]) {
    await c.click(`${tile(n)} .sptoggle`);
    assert.deepEqual(await rowKeys(page, c, n), ["setpoint_normal_heat", "setpoint_reduced_heat"]);
  }
  page = await b.open("variant=full&view=controls&latency=30");
  c = on(page);
  for (const n of [1, 2]) await c.click(`${tile(n)} .sptoggle`);
  assert.deepEqual(await rowKeys(page, c, 2), ["setpoint_normal_heat", "setpoint_reduced_heat", "setpoint_normal_cool", "setpoint_reduced_cool"]);
  assert.match(await c.text(row(2, "setpoint_reduced_cool")), /Reduziert Kühlen/);
  // HC1 cools at 24 = normal cooling; HC2 (Eco) at 26 = reduced cooling 25.5 + 0.5
  assert.deepEqual(await activeRows(page, c), ["hc1.setpoint_normal_cool", "hc2.setpoint_reduced_cool"]);
  assert.ok(await c.exists(`${row(2, "setpoint_reduced_cool")} .sptag.cool`));
  // the closed summary shows the cooling pair while the target follows cooling
  await c.click(`${tile(1)} .sptoggle`);
  assert.match(await c.text(`${tile(1)} .sptoggle .spsum`), /^24,0° \/ 26,0°$/);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("a disabled setpoint entity is shown read-only with the hint; the others stay writable", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30&disable=hc3.setpoint_reduced_heat");
  const c = on(page);
  await c.click(`${tile(3)} .sptoggle`);
  assert.match(await c.text(`${row(3, "setpoint_reduced_heat")} .v`), /10,0°/, "value still shown");
  assert.equal(await disabled(page, c, btn(3, "setpoint_reduced_heat", 1)), true);
  assert.equal(await disabled(page, c, btn(3, "setpoint_reduced_heat", -1)), true);
  assert.match(await page.eval(`${c.root}.querySelector('${row(3, "setpoint_reduced_heat")} .stepper').title`), /Keine aktive Entität/);
  assert.equal(await disabled(page, c, btn(3, "setpoint_normal_heat", 1)), false);
  assert.equal(await page.eval(`${c.root}.querySelector('${row(3, "setpoint_normal_heat")} .stepper').title`), "");
  // a role the backend marks as not writable is read-only too
  await page.eval(`harness.plant.roleOverrides["hc3.setpoint_normal_heat"] = { writable: false }; harness.el.store.refetch(true).then(() => true)`);
  await sleep(150);
  assert.equal(await disabled(page, c, btn(3, "setpoint_normal_heat", 1)), true);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("rendering with every fold-out open writes nothing", { skip }, async () => {
  for (const q of ["variant=house&view=all", "variant=full&view=all", "variant=full&view=all&fahrenheit=1&lang=en", "variant=house&view=controls&unavailable=1"]) {
    const page = await b.open(`${q}&latency=30`, { width: 1280, height: 1600 });
    const c = on(page);
    const n = await page.eval(`${c.root}.querySelectorAll(".sptoggle").length`);
    assert.ok(n >= 2, q);
    for (let i = 0; i < n; i++) await page.eval(`${c.root}.querySelectorAll(".sptoggle")[${i}].click()`);
    await sleep(80);
    assert.ok(await c.exists(".sprow"), q);
    await setValues(page, {});
    await sleep(1900);
    assert.deepEqual(await writes(page), [], q);
    assert.deepEqual(page.errors, [], q);
  }
});

test("unreachable plant: the fold-out opens, setpoints are read-only", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30&unavailable=1");
  const c = on(page);
  await c.click(`${tile(3)} .sptoggle`);
  assert.equal(await disabled(page, c, btn(3, "setpoint_normal_heat", 1)), true);
  assert.deepEqual(page.errors, []);
});

test("a mode change drops a queued setpoint edit", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click(`${tile(1)} .sptoggle`);
  await c.click(btn(1, "setpoint_reduced_heat", 1));
  await c.click(`[data-act="preset"][data-c="1"][data-v="eco"]`);
  await sleep(1900);
  assert.deepEqual(await writes(page), [`climate.set_preset_mode ${await page.eval("harness.plant.climateId(1)")} eco`]);
  assert.match(await c.text(`${row(1, "setpoint_reduced_heat")} .v`), /19,0°/);
  assert.deepEqual(page.errors, []);
});

test("Fahrenheit HA: setpoints stay native °C on screen and are converted once at the service call", { skip }, async () => {
  const page = await b.open("variant=full&view=controls&latency=30&fahrenheit=1&lang=en");
  const c = on(page);
  await c.click(`${tile(1)} .sptoggle`);
  assert.match(await c.text(row(1, "setpoint_normal_cool")), /Normal cooling[^]*24\.0°C/);
  await c.click(btn(1, "setpoint_normal_cool", 1));
  await sleep(1900);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc1.setpoint_normal_cool")} 75.38`]);
  assert.equal(await page.eval("harness.plant.values['hc1.setpoint_normal_cool']"), 24.1);
  assert.deepEqual(page.errors, []);
});

test("the main stepper is labelled as the app's ±3 K correction", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  const attrs = (sel) => page.eval(`(() => { const e = ${c.root}.querySelector(${JSON.stringify(sel)}); return [e.title, e.getAttribute("aria-label"), e.getAttribute("role")]; })()`);
  assert.deepEqual(await attrs(`${tile(1)} .hcrow .stepper`), ["Korrektur ±3 K (wie in der Ochsner-App)", "Fußboden: Korrektur ±3 K (wie in der Ochsner-App)", "group"]);
  page = await b.open("variant=house&view=controls&latency=30&lang=en");
  c = on(page);
  assert.equal((await attrs(`${tile(1)} .hcrow .stepper`))[0], "Correction ±3 K (as in the Ochsner app)");
  // its write semantics are unchanged: climate.set_temperature
  await c.click(`${tile(1)} [data-d="1"]`);
  await sleep(1900);
  assert.deepEqual(await writes(page), [`climate.set_temperature ${await page.eval("harness.plant.climateId(1)")} 22`]);
});

// ---- room sensors -------------------------------------------------------------

const svgText = (page, c) => page.eval(`[...${c.root}.querySelectorAll("svg.plant text")].map((t) => t.textContent).join(" | ")`);

test("room sensors: temperature, humidity and (cooling) dew point on card B; nothing without sensors", { skip }, async () => {
  let page = await b.open("variant=house&view=all&latency=30", { width: 1280, height: 1600 });
  let c = on(page);
  assert.ok(!(await c.exists(".hc .room")), "house plant: no room sensors, no room line");
  assert.doesNotMatch(await svgText(page, c), /Raum/);
  page = await b.open("variant=full&view=all&latency=30", { width: 1280, height: 1600 });
  c = on(page);
  assert.equal(await c.text(`${tile(1)} .room`), "Raum 24,6° · 52 % · Taupunkt 15,2°");
  assert.equal(await c.text(`${tile(2)} .room`), "Raum 25,3° · 48 % · Taupunkt 15,6°");
  // card A: room temperature and humidity lead the circuit's second line
  assert.match(await svgText(page, c), /Raum 24,6° · 52 % · VL 18,6° · Soll 18,5°/);
  // a sensor value update reaches both cards
  await setValues(page, { "hc1.room_temp": 23.9, "hc1.humidity": 55 });
  await sleep(80);
  assert.match(await c.text(`${tile(1)} .room`), /^Raum 23,9° · 55 %/);
  assert.match(await svgText(page, c), /Raum 23,9° · 55 %/);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("room sensors on narrow layouts: the schematic drops the less important parts", { skip }, async () => {
  const page = await b.open("variant=full&view=all&latency=30", { width: 360, height: 2000, mobile: true });
  const c = on(page);
  const t = await svgText(page, c);
  assert.match(t, /Raum 24,6° · 52 % · VL 18,6°/);
  assert.doesNotMatch(t, /Raum 24,6°[^|]*Soll/, "target dropped on 360 px");
  const fits = await page.eval(`[...${c.root}.querySelectorAll(".hc .room")].every((e) => e.scrollWidth <= e.clientWidth + 1)`);
  assert.ok(fits, "card B room line fits");
  assert.deepEqual(page.errors, []);
});

test("room sensors in Fahrenheit: room temperature in HA's unit, dew point labelled °C", { skip }, async () => {
  const page = await b.open("variant=full&view=all&latency=30&fahrenheit=1&lang=en", { width: 1280, height: 1600 });
  const c = on(page);
  assert.equal(await c.text(`${tile(1)} .room`), "Room 76.3°F · 52 % · Dew point 15.2°C");
  assert.match(await svgText(page, c), /Room 76\.3°F · 52 % · Flow 18\.6°C/);
  // without the climate attribute the feed value is converted the same way
  await page.eval(`(() => { const id = harness.plant.climateId(1); const s = harness.fake.hass.states[id]; harness.fake.hass.states[id] = { ...s, attributes: { ...s.attributes, current_temperature: null } }; harness.el.hass = { ...harness.fake.hass }; return true; })()`);
  await sleep(80);
  assert.equal(await c.text(`${tile(1)} .room`), "Room 76.3°F · 52 % · Dew point 15.2°C");
  assert.deepEqual(page.errors, []);
});
