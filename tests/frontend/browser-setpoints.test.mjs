// Card B circuit setpoints in a real browser: the main setpoint of the
// selected mode with the other one of the pair below it, the "active" marker,
// coalesced number.set_value writes on the role's entity, bounds, cooling
// pair, the correction chip, read-only entities; room readings on cards A/B.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { CHROME, browser, on, sleep, writes } from "./harness/browser-helpers.mjs";

const skip = !CHROME && "no chromium";
let b;
before(async () => { if (CHROME) b = await browser(); });
after(async () => { if (b) await b.close(); });

const tile = (n) => `.hc[data-key="hc${n}"]`;
const main = (n) => `${tile(n)} .sp.main`;
const alt = (n) => `${tile(n)} .sp.alt`;
const eid = (page, key) => page.eval(`harness.plant.entities()[${JSON.stringify(key)}].entity_id`);
const disabled = (page, c, sel) => page.eval(`${c.root}.querySelector(${JSON.stringify(sel)}).disabled`);
const attr = (page, c, sel, name) => page.eval(`${c.root}.querySelector(${JSON.stringify(sel)}).getAttribute(${JSON.stringify(name)})`);
/** [setpoint key, value text, badge text] of a circuit's main or second row. */
const rowOf = (page, c, sel) => page.eval(`(() => { const e = ${c.root}.querySelector(${JSON.stringify(sel)}); if (!e) return null; return [e.dataset.sp, e.querySelector(".v > span").textContent, (e.querySelector(".spbadge") || {}).textContent || ""]; })()`);
/** Rows carrying the "active" marker, as "hc<n>.<key>:main|alt". */
const active = (page, c) => page.eval(`[...${c.root}.querySelectorAll(".hc .sp")].filter((e) => e.querySelector(".sptag")).map((e) => e.closest(".hc").dataset.key + "." + e.dataset.sp + ":" + (e.classList.contains("main") ? "main" : "alt"))`);
/** Change feed values and push an update (like a coordinator refresh). */
const setValues = (page, vals) => page.eval(`(() => { Object.assign(harness.plant.values, ${JSON.stringify(vals)}); harness.plant.push(); return true; })()`);

test("the main setpoint follows the mode: normal in Komfort, reduced in Eco and legacy Reduziert; the other one below with a Nacht/Normal badge", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  assert.ok(!(await c.exists(".sptoggle")), "no fold-out any more");
  assert.deepEqual(await rowOf(page, c, main(1)), ["setpoint_normal_heat", "21,5°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_reduced_heat", "19,0°", "Nacht"]);
  assert.equal(await attr(page, c, `${alt(1)} .spbadge`, "title"), "Reduziert (Eco, Nacht)");
  // HC3 Wintergarten runs the legacy "Reduziert" program: reduced is main, the day value below
  assert.deepEqual(await rowOf(page, c, main(3)), ["setpoint_reduced_heat", "10,0°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(3)), ["setpoint_normal_heat", "25,0°", "Normal"]);
  assert.match(await c.text(`${tile(3)} .hcname .sub`), /Heizen \| Reduziert/, "legacy program label kept");
  // Eco under the app's control, the legacy Eco program, "Normal" and other legacy states
  await setValues(page, { "hc1.comfort_eco": 1, "hc2.op_program": 6 });
  await sleep(60);
  assert.deepEqual(await rowOf(page, c, main(1)), ["setpoint_reduced_heat", "19,0°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_normal_heat", "21,5°", "Normal"]);
  assert.equal((await rowOf(page, c, main(2)))[0], "setpoint_reduced_heat");
  for (const prog of [3, 1, 4, 7, 8]) {
    await setValues(page, { "hc2.op_program": prog, "hc2.comfort_eco": 1 });
    await sleep(40);
    assert.equal((await rowOf(page, c, main(2)))[0], "setpoint_normal_heat", `op_program ${prog}`);
  }
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
  page = await b.open("variant=house&view=controls&latency=30&lang=en");
  c = on(page);
  assert.equal((await rowOf(page, c, alt(1)))[2], "Night");
  assert.equal((await rowOf(page, c, alt(3)))[2], "Normal");
  assert.equal(await attr(page, c, `${alt(3)} .spbadge`, "title"), "Normal (Comfort, day)");
});

test("at night in Komfort the main stays the day setpoint; the active marker moves to the night row", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  assert.deepEqual(await active(page, c), ["hc1.setpoint_normal_heat:main", "hc2.setpoint_normal_heat:main", "hc3.setpoint_reduced_heat:main"]);
  assert.match(await c.text(`${main(1)} .v`), /^21,5°aktiv$/);
  // night: the controller follows reduced 19 + correction 0.5
  await setValues(page, { "hc1.room_offset": 0.5, "hc1.room_target_heat": 19.5 });
  await sleep(60);
  assert.equal((await rowOf(page, c, main(1)))[0], "setpoint_normal_heat", "no swapping by day/night");
  assert.deepEqual((await active(page, c))[0], "hc1.setpoint_reduced_heat:alt");
  // frost protection: neither
  await setValues(page, { "hc1.room_offset": 0, "hc1.room_target_heat": 12, "hc1.badge": 16 });
  await sleep(60);
  assert.ok(!(await active(page, c)).some((k) => k.startsWith("hc1.")));
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("main stepper burst on Wintergarten (reduced) writes exactly one number.set_value with the final value", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  for (let i = 0; i < 3; i++) await c.click(`${main(3)} [data-s="1"]`);
  assert.match(await c.text(`${main(3)} .v.pend span`), /^11,5°$/, "optimistic, 0.5 K per tap");
  assert.match(await c.text(tile(3)), /wird gesendet, sobald du aufhörst/);
  assert.deepEqual(await writes(page), [], "nothing before the idle delay");
  await sleep(1900);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc3.setpoint_reduced_heat")} 11.5`]);
  // the controller recomputed the target; the marker stays on the main row
  assert.equal(await page.eval("harness.plant.values['hc3.room_target_heat']"), 11.5);
  assert.deepEqual((await active(page, c))[2], "hc3.setpoint_reduced_heat:main");
  assert.ok(!(await c.exists(`${main(3)} .v.pend`)));
  // + then − back to the current value: no write at all
  await c.click(`${main(3)} [data-s="1"]`);
  await c.click(`${main(3)} [data-s="-1"]`);
  await sleep(1900);
  assert.equal((await writes(page)).length, 1);
  assert.deepEqual(page.errors, []);
});

test("main and second row coalesce separately: one write each, to the right entities", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click(`${alt(1)} [data-s="1"]`);
  await c.click(`${main(1)} [data-s="-1"]`);
  await c.click(`${alt(1)} [data-s="1"]`);
  await c.click(`${main(1)} [data-s="-1"]`);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_reduced_heat", "20,0°", "Nacht"]);
  await sleep(1900);
  assert.deepEqual((await writes(page)).sort(), [
    `number.set_value ${await eid(page, "hc1.setpoint_normal_heat")} 20.5`,
    `number.set_value ${await eid(page, "hc1.setpoint_reduced_heat")} 20`,
  ].sort());
  assert.deepEqual(page.errors, []);
});

test("setpoint steppers respect the role's bounds", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  // Wintergarten's reduced setpoint at its minimum (10): − disabled
  assert.equal(await disabled(page, c, `${main(3)} [data-s="-1"]`), true);
  assert.equal(await disabled(page, c, `${main(3)} [data-s="1"]`), false);
  // the day setpoint (second row) half a step below its maximum (30)
  await setValues(page, { "hc3.setpoint_normal_heat": 29.8 });
  await sleep(60);
  await c.click(`${alt(3)} [data-s="1"]`);
  assert.match(await c.text(`${alt(3)} .v`), /30,0°/);
  assert.equal(await disabled(page, c, `${alt(3)} [data-s="1"]`), true, "+ disabled at the maximum");
  await sleep(1900);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc3.setpoint_normal_heat")} 30`]);
  assert.deepEqual(page.errors, []);
});

test("cooling pair by target_selector, only on cool-capable circuits", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  const sps = await page.eval(`[...${c.root}.querySelectorAll(".hc .sp")].map((e) => e.dataset.sp)`);
  assert.ok(sps.length === 6 && sps.every((k) => k.endsWith("_heat")), JSON.stringify(sps));
  page = await b.open("variant=full&view=controls&latency=30");
  c = on(page);
  // HC1 cools in Komfort, HC2 in Eco (25.5 + correction 0.5 = target 26)
  assert.deepEqual(await rowOf(page, c, main(1)), ["setpoint_normal_cool", "24,0°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_reduced_cool", "26,0°", "Nacht"]);
  assert.deepEqual(await rowOf(page, c, main(2)), ["setpoint_reduced_cool", "25,5°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(2)), ["setpoint_normal_cool", "22,0°", "Normal"]);
  assert.equal(await attr(page, c, `${alt(2)} .spbadge`, "title"), "Normal Kühlen");
  assert.deepEqual(await active(page, c), ["hc1.setpoint_normal_cool:main", "hc2.setpoint_reduced_cool:main"]);
  assert.ok(await c.exists(`${main(2)} .sptag.cool`));
  // the controller switches HC1's target to heating: the heating pair
  await setValues(page, { "hc1.target_selector": 2, "hc1.room_target_heat": 21.5 });
  await sleep(60);
  assert.deepEqual(await rowOf(page, c, main(1)), ["setpoint_normal_heat", "21,5°", ""]);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("correction chip only while the offset is not 0; ✕ writes 0 once", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  assert.ok(!(await c.exists(".corr")), "offset 0: no chip");
  page = await b.open("variant=house&view=controls&latency=30&set=hc1.room_offset:1,hc1.room_target_heat:22.5");
  c = on(page);
  assert.equal(await c.text(`${tile(1)} .hcinfo`), "VL 25,9° / 26,8°Korr. +1,0 K ✕");
  assert.equal(await attr(page, c, `${tile(1)} button.corr`, "title"), "Korrektur auf 0 zurücksetzen");
  assert.ok(!(await c.exists(`${tile(2)} .corr`)));
  assert.deepEqual((await active(page, c))[0], "hc1.setpoint_normal_heat:main", "21.5 + 1 = 22.5");
  // a double tap still writes once
  await page.eval(`(() => { const e = ${c.root}.querySelector('${tile(1)} button.corr'); e.click(); e.click(); return true; })()`);
  await sleep(300);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc1.room_offset")} 0`]);
  assert.ok(!(await c.exists(`${tile(1)} .corr`)), "chip gone once the feed reports 0");
  await sleep(1600);
  assert.equal((await writes(page)).length, 1);
  assert.deepEqual(page.errors, []);
  // negative offset, English
  page = await b.open("variant=house&view=controls&latency=30&lang=en&set=hc2.room_offset:-1.5");
  c = on(page);
  assert.equal(await c.text(`${tile(2)} button.corr`), "Offset −1.5 K ✕");
});

test("correction chip without ✕ when the offset entity is read-only or the plant unreachable", { skip }, async () => {
  for (const q of ["disable=hc1.room_offset", "unavailable=1"]) {
    const page = await b.open(`variant=house&view=controls&latency=30&set=hc1.room_offset:1&${q}`);
    const c = on(page);
    assert.equal(await c.text(`${tile(1)} .corr`), "Korr. +1,0 K", q);
    assert.ok(!(await c.exists(`${tile(1)} button.corr`)), q);
    assert.deepEqual(page.errors, [], q);
  }
});

test("a disabled or non-writable setpoint entity is read-only with the hint; the other stays writable", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30&disable=hc3.setpoint_reduced_heat");
  const c = on(page);
  assert.equal((await rowOf(page, c, main(3)))[1], "10,0°", "value still shown");
  assert.equal(await disabled(page, c, `${main(3)} [data-s="1"]`), true);
  assert.equal(await disabled(page, c, `${main(3)} [data-s="-1"]`), true);
  assert.match(await attr(page, c, `${main(3)} .stepper`, "title"), /Keine aktive Entität/);
  assert.equal(await disabled(page, c, `${alt(3)} [data-s="1"]`), false);
  assert.equal(await attr(page, c, `${alt(3)} .stepper`, "title"), "Raumsollwert Normal (Komfort, Tag)");
  // a role the backend marks as not writable is read-only too
  await page.eval(`harness.plant.roleOverrides["hc3.setpoint_normal_heat"] = { writable: false }; harness.el.store.refetch(true).then(() => true)`);
  await sleep(150);
  assert.equal(await disabled(page, c, `${alt(3)} [data-s="1"]`), true);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

test("a mode change drops queued setpoint edits", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click(`${main(1)} [data-s="1"]`);
  await c.click(`${alt(1)} [data-s="1"]`);
  await c.click(`[data-act="preset"][data-c="1"][data-v="eco"]`);
  await sleep(1900);
  assert.deepEqual(await writes(page), [`climate.set_preset_mode ${await page.eval("harness.plant.climateId(1)")} eco`]);
  // Eco: reduced is main now; neither value changed
  assert.deepEqual(await rowOf(page, c, main(1)), ["setpoint_reduced_heat", "19,0°", ""]);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_normal_heat", "21,5°", "Normal"]);
  assert.deepEqual(page.errors, []);
});

test("Fahrenheit HA: setpoints stay native °C on screen and are converted once at the service call", { skip }, async () => {
  const page = await b.open("variant=full&view=controls&latency=30&fahrenheit=1&lang=en");
  const c = on(page);
  assert.deepEqual(await rowOf(page, c, alt(1)), ["setpoint_reduced_cool", "26.0°C", "Night"]);
  await c.click(`${alt(1)} [data-s="1"]`);
  await sleep(1900);
  assert.deepEqual(await writes(page), [`number.set_value ${await eid(page, "hc1.setpoint_reduced_cool")} 79.7`]);
  assert.equal(await page.eval("harness.plant.values['hc1.setpoint_reduced_cool']"), 26.5);
  assert.deepEqual(page.errors, []);
});

test("the main stepper is labelled with its setpoint", { skip }, async () => {
  let page = await b.open("variant=house&view=controls&latency=30");
  let c = on(page);
  assert.equal(await attr(page, c, `${main(1)} .stepper`, "title"), "Raumsollwert Normal (Komfort, Tag)");
  assert.equal(await attr(page, c, `${main(1)} .stepper`, "aria-label"), "Fußboden: Normal (Komfort, Tag)");
  assert.equal(await attr(page, c, `${main(1)} [data-s="1"]`, "aria-label"), "Erhöhen Fußboden: Normal (Komfort, Tag)");
  assert.equal(await attr(page, c, `${main(3)} .stepper`, "title"), "Raumsollwert Reduziert (Eco, Nacht)");
  assert.doesNotMatch(await c.text(tile(1)), /Korrektur/);
  page = await b.open("variant=house&view=controls&latency=30&lang=en");
  c = on(page);
  assert.equal(await attr(page, c, `${main(1)} .stepper`, "title"), "Room setpoint Normal (Comfort, day)");
});

test("rendering writes nothing", { skip }, async () => {
  for (const q of ["variant=house&view=all", "variant=full&view=all", "variant=full&view=all&fahrenheit=1&lang=en", "variant=house&view=controls&unavailable=1", "variant=house&view=all&set=hc1.room_offset:1"]) {
    const page = await b.open(`${q}&latency=30`, { width: 1280, height: 1600 });
    const c = on(page);
    assert.ok(await c.exists(".sp.main"), q);
    await setValues(page, {});
    await sleep(1900);
    assert.deepEqual(await writes(page), [], q);
    assert.deepEqual(page.errors, [], q);
  }
});

test("unreachable plant: setpoints are read-only", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30&unavailable=1");
  const c = on(page);
  for (const sel of [main(1), alt(1)]) assert.equal(await disabled(page, c, `${sel} [data-s="1"]`), true);
  assert.deepEqual(page.errors, []);
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
  assert.equal(await c.text(`${tile(1)} .room`), "Raum 24,6° 52 % Taupunkt 15,2°");
  assert.equal(await c.text(`${tile(2)} .room`), "Raum 25,3° 48 % Taupunkt 15,6°");
  // card A: room temperature and humidity lead the circuit's second line
  assert.match(await svgText(page, c), /Raum 24,6° · 52 % · VL 18,6° · Soll 18,5°/);
  // a sensor value update reaches both cards
  await setValues(page, { "hc1.room_temp": 23.9, "hc1.humidity": 55 });
  await sleep(80);
  assert.match(await c.text(`${tile(1)} .room`), /^Raum 23,9° 55 %/);
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
  assert.equal(await c.text(`${tile(1)} .room`), "Room 76.3°F 52 % Dew point 15.2°C");
  assert.match(await svgText(page, c), /Room 76\.3°F · 52 % · Flow 18\.6°C/);
  // without the climate attribute the feed value is converted the same way
  await page.eval(`(() => { const id = harness.plant.climateId(1); const s = harness.fake.hass.states[id]; harness.fake.hass.states[id] = { ...s, attributes: { ...s.attributes, current_temperature: null } }; harness.el.hass = { ...harness.fake.hass }; return true; })()`);
  await sleep(80);
  assert.equal(await c.text(`${tile(1)} .room`), "Room 76.3°F 52 % Dew point 15.2°C");
  assert.deepEqual(page.errors, []);
});
