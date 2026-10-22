// Contract tests against the real backend. fixtures/backend-payload.json is
// produced by running the backend's own plant_api (async_build_plants +
// ws_subscribe) inside Home Assistant against tests/fixtures/airhawk518_snapshot.json
// ("house" = the snapshot as is, "dhw_cool_pool" = DHW + cooling HC2 + pool).
// The harness fake must emit exactly those payloads, and every role the
// frontend reads must exist there (or be an agreed, optional role).
// The fixture is the backend's real payload. tests/test_frontend_contract.py
// fails when the backend's output no longer matches it; regenerate with
// OTS_UPDATE_FRONTEND_FIXTURE=1 pytest tests/test_frontend_contract.py
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { FakePlant, computeCapabilities, fakeHass, retag, variantValues } from "./harness/fake-backend.js";
import { CURVE_KEYS } from "../../custom_components/ochsner_local_ots/frontend/lib/curve.js";
import { FRONTEND, fixtures } from "./helpers.mjs";

const CAP_KEYS = ["bms", "dhw", "buffer", "buffer_temp", "aux", "aux_temp", "buffer_aux_heater", "pool", "silent", "emergency", "power", "cooling"].sort();
// Agreed in the contract as optional (the backend sends it only when the plant has the point).
const OPTIONAL_ROLES = ["dhw.hysteresis"];

const withoutIds = (roles) => Object.fromEntries(Object.entries(roles).map(([k, r]) => [k, { ...r, entity_id: !!r.entity_id }]));
const circuitShape = (cs) => cs.map(({ climate, device_id, ...c }) => ({ ...c, climate: !!climate }));

test("the fixture holds both backend-generated plant variants", () => {
  const { backend } = fixtures();
  assert.deepEqual(Object.keys(backend).filter((k) => !k.startsWith("_")).sort(), ["dhw_cool_pool", "house"]);
});

for (const variant of ["house", "dhw_cool_pool"]) {
  test(`fake payload and event equal the backend's (${variant})`, () => {
    const { catalog, snapshot, backend } = fixtures();
    const real = backend[variant];
    const rp = real.plants.plants[0];
    const fp = new FakePlant({ catalog, snapshot, backend, variant });
    const res = fp.plantPayload("de");
    assert.equal(real.plants.version, 1);
    assert.deepEqual(Object.keys(real.plants.settings), ["color_scheme"]);
    assert.deepEqual(Object.keys(res).sort(), Object.keys(rp).sort());
    assert.equal(rp.plant_id, `${rp.entry_id}:${rp.host}`);
    assert.deepEqual(res.capabilities, rp.capabilities);
    assert.deepEqual(Object.keys(rp.capabilities).sort(), CAP_KEYS);
    assert.deepEqual(circuitShape(res.circuits), circuitShape(rp.circuits));
    assert.deepEqual(res.dhw && Object.keys(res.dhw).sort(), rp.dhw && Object.keys(rp.dhw).sort());
    assert.deepEqual(res.badges, rp.badges);
    assert.deepEqual(withoutIds(res.roles), withoutIds(rp.roles));
    const ev = fp.event();
    assert.deepEqual(Object.keys(ev).sort(), Object.keys(real.event).sort());
    assert.deepEqual(ev.capabilities, real.event.capabilities);
    assert.deepEqual(ev.values, real.event.values);
    assert.ok(!Object.values(real.event.values).some(Array.isArray), "values are first elements, not [v, v]");
    assert.equal(typeof real.event.values["hc1.name"], "string");
    // English labels too
    const en = fp.plantPayload("en").roles;
    for (const [k, labels] of Object.entries(real.labels_en)) assert.deepEqual(en[k].labels, labels, k);
  });
}

/** Role keys the shipped frontend reads (string literals + per-circuit keys). */
function frontendRoles() {
  const keys = new Set();
  const circuit = new Set([...CURVE_KEYS, "room_target_heat", "room_target_cool"]);
  const walk = (dir) => {
    for (const f of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, f.name);
      if (f.isDirectory()) walk(p);
      else if (f.name.endsWith(".js")) {
        const src = fs.readFileSync(p, "utf8");
        for (const m of src.matchAll(/["'`]((?:hp|dhw|pool|avail)\.[a-z_]+)["'`]/g)) keys.add(m[1]);
        for (const m of src.matchAll(/hcKey\([^,()]+, "([a-z_]+)"\)/g)) circuit.add(m[1]);
      }
    }
  };
  walk(FRONTEND);
  for (const k of circuit) keys.add(`hc1.${k}`);
  return [...keys].sort();
}

test("every role the frontend reads exists in the backend payload", () => {
  const { backend } = fixtures();
  const have = new Set(["house", "dhw_cool_pool"].flatMap((v) => Object.keys(backend[v].plants.plants[0].roles)));
  const used = frontendRoles();
  assert.ok(used.length > 40, `found ${used.length} role keys`);
  const missing = used.filter((k) => !have.has(k) && !OPTIONAL_ROLES.includes(k));
  assert.deepEqual(missing, []);
  // the agreed emergency/pool roles are what the frontend reads, with the backend's labels
  for (const k of ["hp.emergency_status", "dhw.emergency_status", "pool.mode", "pool.setpoint", "pool.status", "pool.temp"]) {
    assert.ok(used.includes(k), k);
    assert.ok(have.has(k), `${k} in the backend payload`);
  }
  const pool = backend.dhw_cool_pool.plants.plants[0].roles;
  assert.equal(pool["hp.emergency_status"].labels["1"], "Reduziert, nicht bestätigt");
  assert.equal(pool["dhw.emergency_status"].labels["4"], "Anlagenstörung");
  assert.ok(!used.includes("hp.emergency") && !used.includes("pool.program") && !used.includes("pool.target"));
});

test("writable curve roles carry native unit and bounds", () => {
  const { backend } = fixtures();
  const roles = backend.house.plants.plants[0].roles;
  for (const k of CURVE_KEYS) {
    const r = roles[`hc1.${k}`];
    assert.equal(r.writable, true, k);
    assert.ok(r.entity_id.startsWith("number."), k);
    assert.equal(r.unit, "°C", k);
    for (const b of ["min", "max", "step"]) assert.equal(typeof r[b], "number", `${k}.${b}`);
  }
  assert.equal(roles["hc1.badge"].entity_id, null);
  assert.deepEqual(roles["hp.program"].options, { 0: "Automatik", 1: "Handbetrieb Heizen", 2: "Handbetrieb Kühlen", 3: "Abgeschaltet" });
  assert.equal(roles["hp.status"].labels["1"], "Heizen");
});

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

test("fake websocket serves the plants payload per language", async () => {
  const { catalog, snapshot, backend } = fixtures();
  const fp = new FakePlant({ catalog, snapshot, backend, variant: "full" });
  const fake = fakeHass([fp]);
  const res = await fake.conn.sendMessagePromise({ type: "ochsner_local_ots/plants", language: "de" });
  assert.equal(res.version, 1);
  const p = res.plants[0];
  assert.equal(p.plant_id, `${p.entry_id}:${p.host}`);
  assert.deepEqual(Object.keys(p.dhw).sort(), ["boost_button", "water_heater"]);
  assert.equal(p.roles["hp.status"].labels["1"], "Heizen");
  assert.ok(p.roles["pool.mode"] && p.roles["pool.setpoint"] && p.roles["pool.status"]);
  assert.ok(p.roles["hp.emergency_status"].labels && p.roles["dhw.emergency_status"].labels);
  const en = await fake.conn.sendMessagePromise({ type: "ochsner_local_ots/plants", language: "en" });
  assert.equal(en.plants[0].roles["hp.status"].labels["1"], "Heating");
});

test("fake controller applies B4/B5 write semantics", async () => {
  const { catalog, snapshot, backend } = fixtures();
  const fp = new FakePlant({ catalog, snapshot, backend, variant: "full", latency: 0 });
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
