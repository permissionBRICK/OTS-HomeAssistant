import test from "node:test";
import assert from "node:assert/strict";
import { hourlyEnergy, runStats, segments, statusResolver, toSeries, windowFor } from "../../custom_components/ochsner_local_ots/frontend/lib/history.js";
import { hpMode } from "../../custom_components/ochsner_local_ots/frontend/lib/model.js";

const H = 3600 * 1000;
const T0 = Date.UTC(2026, 9, 21, 12, 0, 0);

test("window: 24 full hour buckets ending with the current hour", () => {
  const w = windowFor(T0 + 23 * H + 30 * 60000);
  assert.equal(w.start, T0);
  assert.equal(w.hours, 24);
});

test("time-weighted hourly energy from a kW series", () => {
  const win = { start: T0, end: T0 + 3 * H, hours: 3 };
  const s = toSeries([
    { s: "0", lu: (T0 - H) / 1000 },
    { s: "6", lu: (T0 + 15 * 60000) / 1000 }, // 6 kW for 30 min
    { s: "0", lu: (T0 + 45 * 60000) / 1000 },
    { s: "unavailable", lu: (T0 + H) / 1000 }, // gap for an hour
    { s: "2", lu: (T0 + 2 * H) / 1000 },
  ]);
  const e = hourlyEnergy(s, win);
  assert.deepEqual(e.kwh.map((v) => Math.round(v * 1000) / 1000), [3, 0, 2]);
  assert.deepEqual(e.known, [true, false, true]);
  // a segment spanning several buckets is split correctly
  const e2 = hourlyEnergy(toSeries([{ s: "1.5", lu: (T0 + 30 * 60000) / 1000 }]), win);
  assert.deepEqual(e2.kwh, [0.75, 1.5, 1.5]);
  // partial last hour = energy so far
  const e3 = hourlyEnergy(toSeries([{ s: "4", lu: T0 / 1000 }]), { start: T0, end: T0 + 90 * 60000, hours: 2 });
  assert.deepEqual(e3.kwh, [4, 2]);
});

test("full-form history rows are accepted too", () => {
  const s = toSeries([{ state: "1", last_updated: new Date(T0).toISOString() }]);
  assert.equal(s[0].t, T0);
});

test("status labels (any language) resolve to modes; runs and mean runtime", () => {
  const resolve = statusResolver({ 0: "Aus", 1: "Heizen", 3: "Warmwasser", 4: "Abtauen", 12: "Standby" }, hpMode);
  assert.equal(resolve("Heizen"), "heat");
  assert.equal(resolve("1"), "heat");
  assert.equal(resolve("Heating"), "heat");
  assert.equal(resolve("DHW"), "dhw");
  assert.equal(resolve("Abtauen"), "defrost");
  assert.equal(resolve("unavailable"), "unknown");
  const win = { start: T0, end: T0 + 4 * H, hours: 4 };
  const segs = segments(toSeries([
    { s: "Standby", lu: T0 / 1000 },
    { s: "Heizen", lu: (T0 + H) / 1000 },
    { s: "Abtauen", lu: (T0 + H + 20 * 60000) / 1000 },
    { s: "Heizen", lu: (T0 + H + 25 * 60000) / 1000 },
    { s: "Aus", lu: (T0 + H + 40 * 60000) / 1000 },
    { s: "Warmwasser", lu: (T0 + 3 * H) / 1000 },
  ]), win).map((x) => ({ ...x, mode: resolve(x.s) }));
  assert.equal(segs.length, 6);
  const r = runStats(segs);
  assert.equal(r.runs, 2, "defrost inside a run does not split it");
  assert.equal(r.avgMinutes, (40 + 60) / 2);
});
