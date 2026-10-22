// Room setpoints (normal/reduced heating and cooling) and room sensors:
// pure helpers behind card B's setpoint fold-out and the room readings.
import test from "node:test";
import assert from "node:assert/strict";
import { activeSetpoint, roomReading, setpointRows } from "../../custom_components/ochsner_local_ots/frontend/lib/model.js";
import { fitParts } from "../../custom_components/ochsner_local_ots/frontend/lib/plant-svg.js";
import { translate } from "../../custom_components/ochsner_local_ots/frontend/lib/i18n.js";
import { fixtures } from "./helpers.mjs";

const house = () => {
  const { backend } = fixtures();
  return { plant: backend.house.plants.plants[0], values: { ...backend.house.event.values } };
};
const circuit = (plant, n) => plant.circuits.find((c) => c.index === n);
const keys = (rows) => rows.map((r) => r.key);

test("setpoint rows: heating always, cooling only on cool-capable circuits, missing roles left out", () => {
  const { plant } = house();
  assert.deepEqual(keys(setpointRows(plant, circuit(plant, 1))), ["setpoint_normal_heat", "setpoint_reduced_heat"]);
  const cool = { ...circuit(plant, 1), cool: true };
  assert.deepEqual(keys(setpointRows(plant, cool)), ["setpoint_normal_heat", "setpoint_reduced_heat", "setpoint_normal_cool", "setpoint_reduced_cool"]);
  assert.deepEqual(keys(setpointRows(plant, { ...cool, heat: false })), ["setpoint_normal_cool", "setpoint_reduced_cool"]);
  const roles = { ...plant.roles };
  delete roles["hc1.setpoint_reduced_heat"];
  assert.deepEqual(keys(setpointRows({ ...plant, roles }, circuit(plant, 1))), ["setpoint_normal_heat"]);
  // the real backend payload for a cool-capable circuit (dhw_cool_pool, HC2)
  const p2 = fixtures().backend.dhw_cool_pool.plants.plants[0];
  assert.equal(setpointRows(p2, circuit(p2, 2)).length, 4);
  assert.equal(setpointRows(p2, circuit(p2, 1)).length, 2);
});

test("active setpoint: value + offset = current target (±0.05), else none", () => {
  const { plant, values } = house();
  const live = { values };
  // Komfort by day: target 21.5 = normal 21.5 + 0
  assert.equal(activeSetpoint(plant, live, circuit(plant, 1)), "setpoint_normal_heat");
  // the legacy "Reduziert" program (Wintergarten): target 10 = reduced 10
  assert.equal(activeSetpoint(plant, live, circuit(plant, 3)), "setpoint_reduced_heat");
  // Komfort at night: target = reduced + offset
  const night = { values: { ...values, "hc1.room_offset": 0.5, "hc1.room_target_heat": 19.5 } };
  assert.equal(activeSetpoint(plant, night, circuit(plant, 1)), "setpoint_reduced_heat");
  // within the tolerance, and just outside it
  assert.equal(activeSetpoint(plant, { values: { ...values, "hc1.room_target_heat": 21.54 } }, circuit(plant, 1)), "setpoint_normal_heat");
  assert.equal(activeSetpoint(plant, { values: { ...values, "hc1.room_target_heat": 21.56 } }, circuit(plant, 1)), null);
  // frost protection (or party/holiday): the target matches neither
  assert.equal(activeSetpoint(plant, { values: { ...values, "hc1.room_target_heat": 12 } }, circuit(plant, 1)), null);
  // no target known
  const none = { ...values };
  delete none["hc1.room_target_heat"];
  assert.equal(activeSetpoint(plant, { values: none }, circuit(plant, 1)), null);
});

test("active setpoint follows target_selector: cooling setpoints for a cooling target", () => {
  const { plant, values } = house();
  const c = { ...circuit(plant, 1), cool: true };
  const cooling = { values: { ...values, "hc1.target_selector": 3, "hc1.room_target_cool": 26, "hc1.room_target_heat": 21.5 } };
  assert.equal(activeSetpoint(plant, cooling, c), "setpoint_reduced_cool");
  cooling.values["hc1.room_target_cool"] = 24;
  assert.equal(activeSetpoint(plant, cooling, c), "setpoint_normal_cool");
  // heating target with cooling values that happen to match: heating rows only
  const heating = { values: { ...values, "hc1.room_target_heat": 26 } };
  assert.equal(activeSetpoint(plant, heating, c), null);
});

test("active setpoint: equal normal and reduced values → reduced under Eco/Reduziert, else normal", () => {
  const { plant, values } = house();
  const v = { ...values, "hc1.setpoint_reduced_heat": 21.5 };
  assert.equal(activeSetpoint(plant, { values: v }, circuit(plant, 1)), "setpoint_normal_heat");
  assert.equal(activeSetpoint(plant, { values: { ...v, "hc1.comfort_eco": 1 } }, circuit(plant, 1)), "setpoint_reduced_heat");
  assert.equal(activeSetpoint(plant, { values: { ...v, "hc1.op_program": 2 } }, circuit(plant, 1)), "setpoint_reduced_heat");
});

test("room reading: none without a sensor; climate attributes first, else the feed converted", () => {
  const values = { "hc1.room_temp": 22.4, "hc1.humidity": 48, "hc1.dew_point": 11.1 };
  const live = { values };
  assert.equal(roomReading({ index: 1, room_sensor: false, humidity: true }, live, null), null);
  assert.deepEqual(roomReading({ index: 1, room_sensor: true }, live, null), { temp: 22.4, unit: "°C", hum: undefined, dew: undefined });
  assert.deepEqual(roomReading({ index: 1, room_sensor: true, humidity: true }, live, null), { temp: 22.4, unit: "°C", hum: 48, dew: undefined });
  // dew point only on cool-capable circuits with humidity
  assert.equal(roomReading({ index: 1, room_sensor: true, humidity: true, cool: true }, live, null).dew, 11.1);
  assert.equal(roomReading({ index: 1, room_sensor: true, cool: true }, live, null).dew, undefined);
  // the climate entity (HA's unit) wins over the feed
  const st = { state: "heat", attributes: { current_temperature: 72.5, current_humidity: 50 } };
  assert.deepEqual(roomReading({ index: 1, room_sensor: true, humidity: true }, live, st, "°F"), { temp: 72.5, unit: "°F", hum: 50, dew: undefined });
  // feed fallback converted into HA's unit (°F)
  const f = roomReading({ index: 1, room_sensor: true }, live, { state: "heat", attributes: { current_temperature: null } }, "°F");
  assert.equal(Math.round(f.temp * 100) / 100, 72.32);
  // an unavailable climate entity is ignored; no value at all → null
  assert.equal(roomReading({ index: 1, room_sensor: true }, live, { state: "unavailable", attributes: { current_temperature: 30 } }).temp, 22.4);
  assert.equal(roomReading({ index: 2, room_sensor: true }, live, null), null);
});

test("schematic sub line drops the least important parts first", () => {
  const parts = [{ t: "Raum 22,4°", keep: 4 }, { t: "48 %", keep: 2 }, { t: "VL 25,9°", keep: 3 }, { t: "Soll 26,8°", keep: 1 }];
  assert.equal(fitParts(parts, 1000, 12), "Raum 22,4° · 48 % · VL 25,9° · Soll 26,8°");
  assert.equal(fitParts(parts, 200, 12), "Raum 22,4° · 48 % · VL 25,9°");
  assert.equal(fitParts(parts, 150, 12), "Raum 22,4° · VL 25,9°");
  assert.equal(fitParts(parts, 80, 12), "Raum 22,4°");
  assert.equal(fitParts("Wasser 26°", 1000, 12), "Wasser 26°");
});

test("setpoint and room labels exist in German and English", () => {
  const exp = {
    setpoints: ["Sollwerte", "Setpoints"],
    sp_setpoint_normal_heat: ["Normal (Komfort, Tag)", "Normal (Comfort, day)"],
    sp_setpoint_reduced_heat: ["Reduziert (Eco, Nacht)", "Reduced (Eco, night)"],
    sp_setpoint_normal_cool: ["Normal Kühlen", "Normal cooling"],
    sp_setpoint_reduced_cool: ["Reduziert Kühlen", "Reduced cooling"],
    sp_active: ["aktiv", "active"],
    room: ["Raum", "Room"],
  };
  for (const [k, [de, en]] of Object.entries(exp)) {
    assert.equal(translate("de", k), de, k);
    assert.equal(translate("en", k), en, k);
  }
  assert.equal(translate("de", "correction_hint", { k: "3" }), "Korrektur ±3 K (wie in der Ochsner-App)");
  assert.equal(translate("en", "correction_hint", { k: "3" }), "Correction ±3 K (as in the Ochsner app)");
});
