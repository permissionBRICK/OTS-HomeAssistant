import test from "node:test";
import assert from "node:assert/strict";
import { badgeFor, capabilities, circuitActive, circuitKind, circuitName, circuitsForDevice, convertDelta, convertTemp, enumLabel, haTempUnit, hpMode, isProducing, layoutSignature, liveCop, parseBadge, pickPlant, sections, toServiceValue, writableEntity } from "../../custom_components/ochsner_local_ots/frontend/lib/model.js";
import { BADGES, FakePlant } from "./harness/fake-backend.js";
import { fixtures } from "./helpers.mjs";

test("hp.status → mode (F4 mapping)", () => {
  const exp = { 0: "off", 1: "heat", 2: "cool", 3: "dhw", 4: "defrost", 5: "off", 6: "heat", 7: "cool", 8: "off", 12: "standby", 14: "fault", 16: "dhw", 17: "off", 9: "standby", 20: "standby" };
  for (const [raw, mode] of Object.entries(exp)) assert.equal(hpMode(Number(raw)), mode, `status ${raw}`);
  assert.equal(hpMode(undefined), "standby");
  assert.equal(hpMode("x"), "standby");
  assert.ok(isProducing("heat") && isProducing("dhw") && isProducing("cool"));
  assert.ok(!isProducing("defrost") && !isProducing("standby"));
});

test("badge names parse to kind/preset/activity", () => {
  const exp = {
    off_grey: ["off", null, "off"], fault_orange: ["fault", null, "fault"],
    auto_comfort_grey: ["auto", "comfort", "idle"], auto_comfort_red: ["auto", "comfort", "heating"], auto_comfort_blue: ["auto", "comfort", "cooling"],
    auto_eco_grey: ["auto", "eco", "idle"], auto_eco_red: ["auto", "eco", "heating"], auto_eco_blue: ["auto", "eco", "cooling"],
    heat_comfort_grey: ["heat", "comfort", "idle"], heat_comfort_red: ["heat", "comfort", "heating"], heat_eco_grey: ["heat", "eco", "idle"], heat_eco_red: ["heat", "eco", "heating"],
    cool_comfort_grey: ["cool", "comfort", "idle"], cool_comfort_blue: ["cool", "comfort", "cooling"], cool_eco_grey: ["cool", "eco", "idle"], cool_eco_blue: ["cool", "eco", "cooling"],
    off_frost_red: ["frost", null, "heating"], vacation_grey: ["vacation", null, "idle"], vacation_red: ["vacation", null, "heating"],
    party_grey: ["party", null, "idle"], party_red: ["party", null, "heating"], party_blue: ["party", null, "cooling"], vacation_blue: ["vacation", null, "cooling"],
  };
  assert.equal(BADGES.length, 23);
  for (const name of BADGES) {
    const b = parseBadge(name);
    assert.deepEqual([b.kind, b.preset, b.activity], exp[name], name);
  }
  assert.equal(parseBadge(null).kind, "unknown");
  assert.equal(parseBadge("whatever").kind, "unknown");
  const plant = { badges: BADGES };
  assert.equal(badgeFor(plant, 3).name, "auto_comfort_red");
  assert.equal(badgeFor(plant, 99).kind, "unknown");
  assert.equal(badgeFor(plant, undefined).kind, "unknown");
});

test("circuit icon kind is inferred from the name", () => {
  assert.equal(circuitKind("Fußboden"), "floor");
  assert.equal(circuitKind("FBH OG"), "floor");
  assert.equal(circuitKind("Underfloor"), "floor");
  assert.equal(circuitKind("Radiatoren"), "radiator");
  assert.equal(circuitKind("Heizkörper EG"), "radiator");
  assert.equal(circuitKind("Wintergarten"), "house");
  assert.equal(circuitKind(""), "house");
});

test("real AirHawk plant → 3 heat-only circuits, buffer, aux, no DHW/cooling UI", () => {
  const { catalog, snapshot, backend } = fixtures();
  const fp = new FakePlant({ catalog, snapshot, backend, variant: "house" });
  const plant = fp.plantPayload();
  const live = fp.event();
  const s = sections(plant, live);
  assert.deepEqual(s.circuits.map((c) => [c.index, c.heat, c.cool]), [[1, true, false], [2, true, false], [3, true, false]]);
  assert.equal(s.dhw, false);
  assert.equal(s.cooling, false);
  assert.equal(s.buffer, true);
  assert.equal(s.bufferTemp, true);
  assert.equal(s.aux, true);
  assert.equal(s.pool, false);
  assert.equal(s.power, true);
  assert.deepEqual(s.circuits.map((c) => circuitName(c, live)), ["Fußboden", "Radiatoren", "Wintergarten"]);
  assert.ok(liveCop(live, capabilities(plant, live)) > 9);
  assert.equal(circuitActive(plant, live, 1, "heat"), true);
  assert.equal(circuitActive(plant, live, 1, "dhw"), false, "valve on DHW → circuits idle");
  assert.equal(enumLabel(plant, "hp.status", 1), "Heizen");
  assert.equal(enumLabel(plant, "hp.program", 3), "Abgeschaltet");
  assert.equal(writableEntity(plant, "hp.program", "select"), "select.betriebswahl_warmepumpe");
  assert.equal(writableEntity(plant, "hp.program", "number"), null, "wrong domain for the service");
  assert.equal(writableEntity(plant, "hp.program"), null, "the service domain is required");
  assert.equal(writableEntity(plant, "hc1.badge", "sensor"), null);
  assert.equal(writableEntity(plant, "hc1.season", "select"), null, "registry-disabled entity is not written");
  assert.equal(writableEntity(plant, "hp.status", "sensor"), null, "not writable");
  const r = { roles: { x: { entity_id: "number.x" } } };
  assert.equal(writableEntity(r, "x", "number"), null, "writable must be explicitly true");
  r.roles.x.writable = true;
  assert.equal(writableEntity(r, "x", "number"), "number.x");
});

test("sections adapt: DHW+cooling+pool, 1 circuit/no buffer, bms, circuit filter", () => {
  const { catalog, snapshot, backend } = fixtures();
  const full = new FakePlant({ catalog, snapshot, backend, variant: "full" });
  const s = sections(full.plantPayload(), full.event());
  assert.deepEqual(s.circuits.map((c) => [c.index, c.cool]), [[1, true], [2, true]]);
  assert.ok(s.dhw && s.pool && s.cooling && s.buffer);
  const compact = new FakePlant({ catalog, snapshot, backend, variant: "compact" });
  const c = sections(compact.plantPayload(), compact.event());
  assert.equal(c.circuits.length, 1);
  assert.ok(c.dhw && !c.buffer && !c.aux && !c.power && !c.silent);
  const bmsPlant = { ...full.plantPayload(), capabilities: { ...full.capabilities, bms: true } };
  const b = sections(bmsPlant, { values: {} });
  assert.equal(b.circuits.length, 0);
  assert.equal(b.dhw, false);
  assert.equal(b.pool, false);
  assert.deepEqual(sections(full.plantPayload(), full.event(), { circuits: [2] }).circuits.map((x) => x.index), [2]);
  // live capabilities override the one-shot payload
  const live = { ...full.event(), capabilities: { ...full.capabilities, pool: false } };
  assert.equal(sections(full.plantPayload(), live).pool, false);
  assert.notEqual(layoutSignature(full.plantPayload(), live), layoutSignature(full.plantPayload(), full.event()));
});

test("pickPlant by plant id, main device or circuit device", () => {
  const plants = [
    { plant_id: "a:1", entry_id: "a", device_id: "d1", circuits: [{ index: 1, device_id: "c11" }] },
    { plant_id: "b:2", entry_id: "b", device_id: "d2", circuits: [{ index: 1, device_id: "c21" }, { index: 2, device_id: "c22" }] },
  ];
  assert.equal(pickPlant(plants, {}).plant_id, "a:1");
  assert.equal(pickPlant(plants, { plant: "b:2" }).plant_id, "b:2");
  assert.equal(pickPlant(plants, { device_id: "d2" }).plant_id, "b:2");
  assert.equal(pickPlant(plants, { device_id: "c22" }).plant_id, "b:2");
  assert.deepEqual(circuitsForDevice(plants[1], "c22"), [2]);
  assert.equal(circuitsForDevice(plants[1], "d2"), null);
  assert.equal(pickPlant([], {}), null);
});

test("units: native values are converted only at the service call", () => {
  assert.equal(convertTemp(36.5, "°C", "°F"), 97.7);
  assert.equal(convertTemp(97.7, "°F", "°C").toFixed(6), "36.500000");
  assert.equal(convertTemp(20, "°C", "°C"), 20);
  assert.equal(convertTemp(20, "K", "°F"), undefined, "K is a delta here, not an absolute unit");
  assert.equal(convertDelta(5, "K", "°F"), 9);
  assert.equal(convertDelta(0.5, "°F", "K").toFixed(4), "0.2778");
  assert.equal(convertDelta(1.5, "K", "°C"), 1.5);
  const flow = { unit: "°C", display_unit: "°F", min: 20, max: 90, step: 0.1 };
  assert.equal(toServiceValue(flow, 36.1), 96.98);
  assert.equal(toServiceValue(flow, -14), 6.8);
  assert.equal(toServiceValue({ unit: "°C", min: 20 }, 36.1), 36.1, "no display unit → native");
  assert.equal(toServiceValue({ unit: "K", display_unit: "°F" }, 1.5), 1.5, "offsets (K) are not converted");
  assert.equal(toServiceValue({ unit: "°C", display_unit: "bar" }, 2), null, "unconvertible → refuse");
  assert.equal(haTempUnit({ config: { unit_system: { temperature: "°F" } } }), "°F");
  assert.equal(haTempUnit({}), "°C");
});
