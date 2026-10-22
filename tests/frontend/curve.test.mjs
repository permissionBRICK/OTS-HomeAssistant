import test from "node:test";
import assert from "node:assert/strict";
import * as C from "../../custom_components/ochsner_local_ots/frontend/lib/curve.js";

// HC1 of the real plant: (−15 °C → 36 °C), (20 °C → 25 °C), max 40, limit 16.7.
const HC1 = { heat_curve_norm_outdoor: -15, heat_curve_norm_flow: 36, heat_curve_base_outdoor: 20, heat_curve_base_flow: 25, heat_curve_max_flow: 40, heat_limit: 16.7 };
const B = {
  heat_curve_norm_outdoor: { min: -30, max: 5, step: 0.1 }, heat_curve_norm_flow: { min: 20, max: 90, step: 0.1 },
  heat_curve_base_outdoor: { min: -10, max: 30, step: 0.1 }, heat_curve_base_flow: { min: 10, max: 40, step: 0.1 },
  heat_curve_max_flow: { min: 0, max: 70, step: 1 }, heat_limit: { min: 0, max: 30, step: 0.1 },
  cool_curve_norm_outdoor: { min: 20, max: 40, step: 1 }, cool_curve_norm_flow: { min: 10, max: 20, step: 1 },
  cool_curve_base_outdoor: { min: 5, max: 40, step: 1 }, cool_curve_base_flow: { min: 10, max: 30, step: 1 }, cool_limit: { min: 5, max: 40, step: 0.1 },
};
const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`);

test("straight line through both points, clamped at max flow", () => {
  near(C.heatFlowAt(HC1, -15), 36);
  near(C.heatFlowAt(HC1, 20), 25);
  near(C.heatFlowAt(HC1, 12.2), 36 + (27.2 * -11) / 35);
  near(C.heatFlowAt(HC1, -40), 40, 1e-9); // line would be 43.9 → clamped
  near(C.heatLineAt(HC1, -40), 36 + (-25 * -11) / 35);
  assert.equal(C.heatFlowAt({}, 0), undefined);
  assert.ok(C.aboveHeatLimit(HC1, 17));
  assert.ok(!C.aboveHeatLimit(HC1, 16.7));
});

test("room-setpoint shift vs the configured line (effective curve)", () => {
  const shift = C.curveShift(HC1, 12.1841, 26.7993);
  near(shift, 26.7993 - C.heatLineAt(HC1, 12.1841));
  assert.ok(Math.abs(shift) > C.SHIFT_THRESHOLD);
  near(C.heatFlowAt(HC1, 0, shift), C.heatLineAt(HC1, 0) + shift);
  assert.equal(C.curveShift(HC1, undefined, 20), 0);
  assert.equal(C.curveShift(HC1, 12, undefined), 0);
});

test("cooling curve works for both point orders and holds flat outside", () => {
  const a = { cool_curve_base_outdoor: 20, cool_curve_base_flow: 17, cool_curve_norm_outdoor: 32, cool_curve_norm_flow: 15 };
  const b = { cool_curve_base_outdoor: 35, cool_curve_base_flow: 16, cool_curve_norm_outdoor: 25, cool_curve_norm_flow: 20 };
  near(C.coolFlowAt(a, 26), 16);
  near(C.coolFlowAt(a, 10), 17);
  near(C.coolFlowAt(a, 40), 15);
  near(C.coolFlowAt(b, 30), 18);
  near(C.coolFlowAt(b, 20), 20);
  near(C.coolFlowAt(b, 38), 16);
  assert.equal(C.coolOrder(a), "base_first");
  assert.equal(C.coolOrder(b), "norm_first");
});

test("axes default −20…35 / 10…60 and extend to fit", () => {
  assert.deepEqual(C.axes(HC1), { xmin: -20, xmax: 35, ymin: 10, ymax: 60 });
  assert.deepEqual(C.axes({ ...HC1, heat_curve_max_flow: 60 }), { xmin: -20, xmax: 35, ymin: 10, ymax: 65 });
  const wide = C.axes({ ...HC1, heat_curve_norm_outdoor: -24, heat_curve_norm_flow: 62 });
  assert.equal(wide.xmin, -30);
  assert.equal(wide.ymax, 65);
  assert.deepEqual(C.ticks(-20, 35, 10), [-20, -10, 0, 10, 20, 30]);
});

test("snapping to the entity step and bounds", () => {
  assert.equal(C.snap(36.26, 0.1, 20, 90), 36.3);
  assert.equal(C.snap(36.26, 0.5), 36.5);
  assert.equal(C.snap(36.26, 1), 36);
  assert.equal(C.snap(95, 0.1, 20, 90), 90);
  assert.equal(C.snap(-0.04, 0.1), 0);
  assert.equal(C.snap(0.30000000000000004, 0.1), 0.3);
  assert.equal(C.decimals(0.1), 1);
  assert.equal(C.decimals(1), 0);
  assert.equal(C.dragStep(0.1, 0.2), 0.5); // narrow chart: ~0.2 K/px
  assert.equal(C.dragStep(0.1, 0.02), 0.1); // very wide chart
  assert.equal(C.dragStep(1, 0.2), 1);
  assert.equal(C.dragStep(0.3, 0.2), 0.6); // 0.5/1/2/5 are no multiples of 0.3
});

test("drag constraints: gap between points, flow ≥ 10, bounds, writable axes", () => {
  // norm point can't pass base point − 1 K
  assert.deepEqual(C.constrain("heat_norm", { x: 25, y: 38 }, HC1, B), { heat_curve_norm_outdoor: 5, heat_curve_norm_flow: 38 });
  assert.deepEqual(C.constrain("heat_norm", { x: -3, y: 38 }, { ...HC1, heat_curve_base_outdoor: -5 }, B).heat_curve_norm_outdoor, -6);
  // base point keeps ≥ norm + 1 K and flow ≥ 10
  assert.deepEqual(C.constrain("heat_base", { x: -20, y: 2 }, { ...HC1, heat_curve_norm_outdoor: -12 }, B), { heat_curve_base_outdoor: -10, heat_curve_base_flow: 10 });
  assert.deepEqual(C.constrain("heat_base", { x: -20, y: 25 }, { ...HC1, heat_curve_norm_outdoor: -9 }, B).heat_curve_base_outdoor, -8);
  // entity bounds
  assert.equal(C.constrain("heat_norm", { x: -50, y: 120 }, HC1, B).heat_curve_norm_flow, 90);
  assert.equal(C.constrain("heat_norm", { x: -50, y: 120 }, HC1, B).heat_curve_norm_outdoor, -30);
  // drag snapping override + visible axis clamp
  const r = C.constrain("heat_norm", { x: -17.37, y: 37.81 }, HC1, B, { steps: { heat_curve_norm_outdoor: 0.5, heat_curve_norm_flow: 0.5 }, axes: { xmin: -20, xmax: 35, ymin: 10, ymax: 60 } });
  assert.deepEqual(r, { heat_curve_norm_outdoor: -17.5, heat_curve_norm_flow: 38 });
  assert.equal(C.constrain("heat_norm", { x: -26, y: 30 }, HC1, B, { axes: { xmin: -20, xmax: 35 } }).heat_curve_norm_outdoor, -20);
  // lines move along one axis
  assert.deepEqual(C.constrain("heat_limit", { x: 17.34 }, HC1, B), { heat_limit: 17.3 });
  assert.deepEqual(C.constrain("heat_max", { y: 44.6 }, HC1, B), { heat_curve_max_flow: 45 });
  assert.deepEqual(C.constrain("heat_max", { y: 2 }, HC1, B), { heat_curve_max_flow: 10 });
  // read-only axis stays untouched
  assert.deepEqual(C.constrain("heat_norm", { x: -10, y: 40 }, HC1, B, { writable: new Set(["heat_curve_norm_flow"]) }), { heat_curve_norm_flow: 40 });
  assert.deepEqual(C.constrain("nope", { x: 1 }, HC1, B), {});
});

test("cooling points keep their own order", () => {
  const v = { cool_curve_base_outdoor: 35, cool_curve_base_flow: 16, cool_curve_norm_outdoor: 25, cool_curve_norm_flow: 20 };
  // base is right of norm here: base can't go left of norm + 1
  assert.equal(C.constrain("cool_base", { x: 20, y: 16 }, v, B).cool_curve_base_outdoor, 26);
  assert.equal(C.constrain("cool_norm", { x: 38, y: 20 }, v, B).cool_curve_norm_outdoor, 34);
  const w = { cool_curve_base_outdoor: 20, cool_curve_base_flow: 17, cool_curve_norm_outdoor: 32, cool_curve_norm_flow: 15 };
  assert.equal(C.constrain("cool_norm", { x: 10, y: 15 }, w, B).cool_curve_norm_outdoor, 21);
  assert.equal(C.constrain("cool_base", { x: 33, y: 15 }, w, B).cool_curve_base_outdoor, 31);
});

test("keyboard nudge: one step, Shift ×5, lines take any arrow", () => {
  assert.deepEqual(C.nudge("heat_norm", "ArrowUp", HC1, B), { heat_curve_norm_flow: 36.1 });
  assert.deepEqual(C.nudge("heat_norm", "ArrowLeft", HC1, B, 5), { heat_curve_norm_outdoor: -15.5 });
  assert.deepEqual(C.nudge("heat_max", "ArrowRight", HC1, B), { heat_curve_max_flow: 41 });
  assert.deepEqual(C.nudge("heat_limit", "ArrowDown", HC1, B), { heat_limit: 16.6 });
  assert.deepEqual(C.nudge("heat_norm", "Enter", HC1, B), {});
});

test("pending diff, feed confirmation and effect at the current outdoor temp", () => {
  const pending = { heat_curve_norm_flow: 38, heat_curve_norm_outdoor: -15.04, heat_limit: 17.5 };
  const d = C.pendingDiff(HC1, pending, B);
  assert.deepEqual(d, [
    { key: "heat_curve_norm_flow", from: 36, to: 38 },
    { key: "heat_limit", from: 16.7, to: 17.5 },
  ]);
  assert.ok(C.reflects(38.04, 38, 0.1));
  assert.ok(!C.reflects(37.8, 38, 0.1));
  const after = { ...HC1, ...pending };
  const e = C.effectAt(HC1, after, 12.2, 26.8);
  near(e.from, 26.8);
  near(e.to, 26.8 + (C.heatFlowAt(after, 12.2) - C.heatFlowAt(HC1, 12.2)));
  assert.equal(e.aboveLimit, false);
  assert.equal(C.effectAt(HC1, after, 18, 20).aboveLimit, true);
});

test("curveProblems: heating gap and cooling order", () => {
  const { curveProblems } = C;
  assert.deepEqual(curveProblems({ heat_curve_norm_outdoor: -15, heat_curve_base_outdoor: 20 }), []);
  assert.deepEqual(curveProblems({ heat_curve_norm_outdoor: 19, heat_curve_base_outdoor: 20 }), []);
  assert.deepEqual(curveProblems({ heat_curve_norm_outdoor: 19.5, heat_curve_base_outdoor: 20 }), ["heat_order"]);
  assert.deepEqual(curveProblems({ cool_curve_norm_outdoor: 32, cool_curve_base_outdoor: 20 }, "base_first"), []);
  assert.deepEqual(curveProblems({ cool_curve_norm_outdoor: 18, cool_curve_base_outdoor: 20 }, "base_first"), ["cool_order"], "order flipped");
  assert.deepEqual(curveProblems({ cool_curve_norm_outdoor: 20.5, cool_curve_base_outdoor: 20 }, "base_first"), ["cool_order"], "gap < 1 K");
});

test("valueProblems: bounds, step grid, minimum flow", () => {
  const { valueProblems } = C;
  const bounds = { heat_curve_norm_flow: { min: 20, max: 90, step: 0.1 }, heat_curve_max_flow: { min: 0, max: 70, step: 1 } };
  assert.deepEqual(valueProblems([{ key: "heat_curve_norm_flow", to: 38.5 }], bounds), []);
  assert.equal(valueProblems([{ key: "heat_curve_norm_flow", to: 95 }], bounds)[0].code, "bounds");
  assert.equal(valueProblems([{ key: "heat_curve_max_flow", to: 41.5 }], bounds)[0].code, "step");
  assert.equal(valueProblems([{ key: "heat_curve_max_flow", to: 8 }], bounds)[0].code, "min_flow");
});

test("writeOrder keeps every intermediate state valid", () => {
  const { writeOrder, curveProblems } = C;
  const current = { heat_curve_norm_outdoor: -15, heat_curve_base_outdoor: -5, heat_curve_norm_flow: 36 };
  const writes = [{ key: "heat_curve_norm_outdoor", to: 0 }, { key: "heat_curve_norm_flow", to: 38 }, { key: "heat_curve_base_outdoor", to: 5 }];
  const order = writeOrder(current, writes);
  let st = { ...current };
  for (const w of order) {
    st = { ...st, [w.key]: w.to };
    assert.deepEqual(curveProblems(st), [], JSON.stringify(order));
  }
  assert.equal(order.findIndex((w) => w.key === "heat_curve_base_outdoor") < order.findIndex((w) => w.key === "heat_curve_norm_outdoor"), true, "base point first");
  // moving both left: norm first
  const left = writeOrder({ heat_curve_norm_outdoor: -5, heat_curve_base_outdoor: 0 }, [{ key: "heat_curve_base_outdoor", to: -12 }, { key: "heat_curve_norm_outdoor", to: -20 }]);
  assert.deepEqual(left.map((w) => w.key), ["heat_curve_norm_outdoor", "heat_curve_base_outdoor"]);
});
