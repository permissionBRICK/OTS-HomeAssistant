// The harness must emit exactly the B6 payload shapes the backend will send.
import test from "node:test";
import assert from "node:assert/strict";
import { FakePlant, computeCapabilities, fakeHass, retag, variantValues } from "./harness/fake-backend.js";
import { fixtures } from "./helpers.mjs";

const CAP_KEYS = ["bms", "dhw", "buffer", "buffer_temp", "aux", "aux_temp", "buffer_aux_heater", "pool", "silent", "emergency", "power", "cooling"].sort();

test("role ids: HC2–4 are the HC1 ids re-tagged", () => {
  assert.equal(retag("BCNL7o58AAE=", 19693), "BCNL7u1MAAE=");
  assert.equal(retag("ACO38I58AAE=", 23756), "ACO38MxcAAE=");
  assert.equal(retag("CyPmL458AAE=", 11307), "CyPmLyssAAE=");
});

test("live availability 27 27 27 0 → three heat-only circuits (B3)", () => {
  const { snapshot } = fixtures();
  const v = variantValues(snapshot, "house");
  assert.deepEqual([1, 2, 3, 4].map((n) => v[`hc${n}.availability`]), [27, 27, 27, 0]);
  const { caps, circuits } = computeCapabilities(v);
  assert.deepEqual(circuits.map((c) => [c.index, c.heat, c.cool]), [[1, true, false], [2, true, false], [3, true, false]]);
  assert.deepEqual(Object.keys(caps).sort(), CAP_KEYS);
  assert.equal(caps.dhw, false);
  assert.equal(caps.buffer, true);
  assert.equal(caps.buffer_temp, true);
  assert.equal(caps.aux, true);
});

test("plants payload + event have the B6 shape", async () => {
  const { catalog, snapshot } = fixtures();
  const fp = new FakePlant({ catalog, snapshot, variant: "full" });
  const fake = fakeHass([fp]);
  const res = await fake.conn.sendMessagePromise({ type: "ochsner_local_ots/plants", language: "de" });
  assert.equal(res.version, 1);
  assert.deepEqual(Object.keys(res.settings), ["color_scheme"]);
  const p = res.plants[0];
  for (const k of ["plant_id", "entry_id", "host", "device_id", "title", "model", "available", "capabilities", "circuits", "dhw", "badges", "roles", "flash_writes"]) assert.ok(k in p, k);
  assert.equal(p.plant_id, `${p.entry_id}:${p.host}`);
  assert.deepEqual(Object.keys(p.capabilities).sort(), CAP_KEYS);
  for (const c of p.circuits) for (const k of ["index", "name", "device_id", "heat", "cool", "room_sensor", "humidity", "climate"]) assert.ok(k in c, k);
  assert.deepEqual(Object.keys(p.dhw).sort(), ["boost_button", "water_heater"]);
  assert.equal(p.badges.length, 23);
  const r = p.roles["hc1.heat_curve_norm_flow"];
  assert.equal(typeof r.entity_id, "string");
  assert.equal(r.writable, true);
  for (const k of ["min", "max", "step", "unit"]) assert.ok(k in r, k);
  assert.equal(p.roles["hc1.badge"].entity_id, null);
  assert.deepEqual(p.roles["hp.program"].options, { 0: "Automatik", 1: "Handbetrieb Heizen", 2: "Handbetrieb Kühlen", 3: "Abgeschaltet" });
  assert.equal(p.roles["hp.status"].labels["1"], "Heizen");
  const en = await fake.conn.sendMessagePromise({ type: "ochsner_local_ots/plants", language: "en" });
  assert.equal(en.plants[0].roles["hp.status"].labels["1"], "Heating");
  const ev = fp.event();
  assert.deepEqual(Object.keys(ev).sort(), ["available", "capabilities", "plant_id", "updated", "values"]);
  assert.equal(typeof ev.values["hp.outdoor"], "number");
  assert.equal(typeof ev.values["hc1.name"], "string");
  assert.equal(typeof ev.values["hp.dhw_valve"], "boolean");
  assert.ok(!Object.values(ev.values).some(Array.isArray), "values are first elements, not [v, v]");
});

test("fake controller applies B4/B5 write semantics", async () => {
  const { catalog, snapshot } = fixtures();
  const fp = new FakePlant({ catalog, snapshot, variant: "full", latency: 0 });
  await fp.callService("climate", "set_temperature", { entity_id: "climate.erdgeschoss", temperature: 25 });
  assert.equal(fp.values["hc1.room_offset"], 1);
  assert.equal(fp.states()["climate.erdgeschoss"].attributes.temperature, 25);
  await fp.callService("climate", "set_temperature", { entity_id: "climate.erdgeschoss", temperature: 40 });
  assert.equal(fp.values["hc1.room_offset"], 3, "offset clamps at ±3 K");
  await fp.callService("climate", "set_hvac_mode", { entity_id: "climate.erdgeschoss", hvac_mode: "off" });
  assert.equal(fp.states()["climate.erdgeschoss"].state, "off");
  await fp.callService("climate", "set_preset_mode", { entity_id: "climate.erdgeschoss", preset_mode: "eco" });
  assert.equal(fp.values["hc1.op_program"], 0, "preset while off turns the circuit on");
  assert.equal(fp.values["hc1.comfort_eco"], 1);
  await fp.callService("water_heater", "set_operation_mode", { entity_id: "water_heater.warmwasser", operation_mode: "eco" });
  assert.equal(fp.states()["water_heater.warmwasser"].state, "eco");
  await fp.callService("button", "press", { entity_id: "button.warmwasser_boost" });
  assert.equal(fp.states()["water_heater.warmwasser"].state, "high_demand");
  fp.failNext = "boom";
  await assert.rejects(fp.callService("number", "set_value", { entity_id: "number.x", value: 1 }), /boom/);
});
