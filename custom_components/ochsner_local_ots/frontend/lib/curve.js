// Heating/cooling curve maths: evaluation, axes, snapping, drag constraints,
// pending diffs. Pure (unit-tested). Curve objects use the short role keys
// without the "hcN." prefix, e.g. { heat_curve_norm_outdoor: -15, … }.

export const HEAT_KEYS = [
  "heat_curve_norm_outdoor", "heat_curve_norm_flow",
  "heat_curve_base_outdoor", "heat_curve_base_flow",
  "heat_curve_max_flow", "heat_limit",
];
export const COOL_KEYS = [
  "cool_curve_norm_outdoor", "cool_curve_norm_flow",
  "cool_curve_base_outdoor", "cool_curve_base_flow",
  "cool_limit",
];
export const CURVE_KEYS = [...HEAT_KEYS, ...COOL_KEYS];

export const MIN_FLOW = 10;
export const MIN_GAP = 1; // K between the two outdoor points

/** Draggable handles; x/y are the role keys each axis writes. */
export const HANDLES = {
  heat_norm: { kind: "point", curve: "heat", x: "heat_curve_norm_outdoor", y: "heat_curve_norm_flow" },
  heat_base: { kind: "point", curve: "heat", x: "heat_curve_base_outdoor", y: "heat_curve_base_flow" },
  heat_limit: { kind: "vline", curve: "heat", x: "heat_limit" },
  heat_max: { kind: "hline", curve: "heat", y: "heat_curve_max_flow" },
  cool_norm: { kind: "point", curve: "cool", x: "cool_curve_norm_outdoor", y: "cool_curve_norm_flow" },
  cool_base: { kind: "point", curve: "cool", x: "cool_curve_base_outdoor", y: "cool_curve_base_flow" },
  cool_limit: { kind: "vline", curve: "cool", x: "cool_limit" },
};

const fin = (v) => typeof v === "number" && Number.isFinite(v);

export function lineAt(x1, y1, x2, y2, x) {
  if (x1 === x2) return (y1 + y2) / 2;
  return y1 + ((x - x1) * (y2 - y1)) / (x2 - x1);
}

export function hasHeatCurve(c) {
  return fin(c.heat_curve_norm_outdoor) && fin(c.heat_curve_norm_flow) && fin(c.heat_curve_base_outdoor) && fin(c.heat_curve_base_flow);
}

export function hasCoolCurve(c) {
  return fin(c.cool_curve_norm_outdoor) && fin(c.cool_curve_norm_flow) && fin(c.cool_curve_base_outdoor) && fin(c.cool_curve_base_flow);
}

/** Unclamped straight line through norm and base point. */
export function heatLineAt(c, x) {
  if (!hasHeatCurve(c)) return undefined;
  return lineAt(c.heat_curve_norm_outdoor, c.heat_curve_norm_flow, c.heat_curve_base_outdoor, c.heat_curve_base_flow, x);
}

/** Configured heating flow target at outdoor x (line, clamped at max flow). */
export function heatFlowAt(c, x, shift = 0) {
  const v = heatLineAt(c, x);
  if (v === undefined) return undefined;
  const y = v + shift;
  return fin(c.heat_curve_max_flow) ? Math.min(c.heat_curve_max_flow, y) : y;
}

export function aboveHeatLimit(c, x) {
  return fin(c.heat_limit) && x > c.heat_limit;
}

/** Cooling flow target: interpolated between the two points, held flat outside. */
export function coolFlowAt(c, x) {
  if (!hasCoolCurve(c)) return undefined;
  const [a, b] = coolPointsOrdered(c);
  if (x <= a[0]) return a[1];
  if (x >= b[0]) return b[1];
  return lineAt(a[0], a[1], b[0], b[1], x);
}

export function coolPointsOrdered(c) {
  const n = [c.cool_curve_norm_outdoor, c.cool_curve_norm_flow];
  const b = [c.cool_curve_base_outdoor, c.cool_curve_base_flow];
  return n[0] <= b[0] ? [n, b] : [b, n];
}

/**
 * Deviation of the controller's real flow target from the configured line at
 * the current outdoor temperature (room-setpoint shift vs. reference room).
 */
export function curveShift(c, nowX, nowFlow) {
  if (!fin(nowX) || !fin(nowFlow)) return 0;
  const v = heatLineAt(c, nowX);
  if (v === undefined) return 0;
  // When the configured line is clamped at max flow the shift can't be read.
  if (fin(c.heat_curve_max_flow) && v >= c.heat_curve_max_flow && nowFlow >= c.heat_curve_max_flow - 0.05) return 0;
  return nowFlow - v;
}

export const SHIFT_THRESHOLD = 0.2;

const floor5 = (v) => Math.floor(v / 5) * 5;
const ceil5 = (v) => Math.ceil(v / 5) * 5;

/**
 * Chart axes: outdoor −20…+35 °C and flow 10…max(60, max_flow+5), extended
 * so every point (plus a small margin) fits.
 */
export function axes(c, extra = {}) {
  let xmin = -20, xmax = 35, ymin = MIN_FLOW, ymax = Math.max(60, fin(c.heat_curve_max_flow) ? c.heat_curve_max_flow + 5 : 0);
  const xs = [c.heat_curve_norm_outdoor, c.heat_curve_base_outdoor, c.heat_limit, extra.nowX];
  const ys = [c.heat_curve_norm_flow, c.heat_curve_base_flow, extra.nowFlow, extra.actualFlow];
  if (extra.cool) {
    xs.push(c.cool_curve_norm_outdoor, c.cool_curve_base_outdoor, c.cool_limit);
    ys.push(c.cool_curve_norm_flow, c.cool_curve_base_flow);
  }
  for (const x of xs) if (fin(x)) { xmin = Math.min(xmin, floor5(x - 3)); xmax = Math.max(xmax, ceil5(x + 3)); }
  for (const y of ys) if (fin(y)) { ymin = Math.min(ymin, floor5(y - 2)); ymax = Math.max(ymax, ceil5(y + 3)); }
  return { xmin, xmax, ymin, ymax };
}

export function ticks(min, max, step) {
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
  return out;
}

export function decimals(step) {
  if (!fin(step) || step <= 0) return 1;
  const s = String(step);
  const i = s.indexOf(".");
  return i < 0 ? 0 : Math.min(4, s.length - i - 1);
}

const tidy = (v, step) => Number(v.toFixed(Math.max(decimals(step), 0) + 0));

/** Snap to the step grid (multiples of step) and clamp to [min, max]. */
export function snap(v, step, min, max) {
  let out = v;
  if (fin(step) && step > 0) out = tidy(Math.round(out / step) * step, step);
  if (fin(min) && out < min) out = min;
  if (fin(max) && out > max) out = max;
  return out;
}

function snapDown(v, step) {
  return fin(step) && step > 0 ? tidy(Math.floor(v / step + 1e-9) * step, step) : v;
}
function snapUp(v, step) {
  return fin(step) && step > 0 ? tidy(Math.ceil(v / step - 1e-9) * step, step) : v;
}

/**
 * Coarser snapping for pointer drags so values don't jitter at sub-pixel
 * resolution: the smallest multiple of the entity step among [step, .5, 1, 2, 5]
 * that is at least ~2.5 px on screen.
 */
export function dragStep(step, unitsPerPx) {
  const s = fin(step) && step > 0 ? step : 0.1;
  const want = 2.5 * (fin(unitsPerPx) ? unitsPerPx : 0);
  for (const cand of [s, 0.5, 1, 2, 5]) {
    if (cand < s - 1e-9) continue;
    const ratio = cand / s;
    if (Math.abs(ratio - Math.round(ratio)) > 1e-6) continue;
    if (cand >= want - 1e-9) return cand;
  }
  return tidy(Math.ceil(want / s - 1e-9) * s, s);
}

/**
 * Apply a proposed handle position. `values` holds the current (pending-
 * overlaid) curve values, `bounds[key] = {min, max, step}` per role,
 * `opts.steps[key]` optional snapping override (drag resolution),
 * `opts.axes` optional visible range clamp, `opts.order` the cooling point
 * order to keep ("norm_first" | "base_first"; computed from values if absent),
 * `opts.writable` set of keys that may change.
 * Returns { key: newValue } for the axes the handle controls.
 */
export function constrain(handleId, proposed, values, bounds = {}, opts = {}) {
  const h = HANDLES[handleId];
  if (!h) return {};
  const out = {};
  const writable = opts.writable;
  const ax = opts.axes;
  const step = (k) => (opts.steps && fin(opts.steps[k]) ? opts.steps[k] : bounds[k] && bounds[k].step);
  const lo = (k) => bounds[k] && bounds[k].min;
  const hi = (k) => bounds[k] && bounds[k].max;
  const place = (key, v, axisMin, axisMax, extraMin, extraMax) => {
    if (!fin(v)) return;
    if (writable && !writable.has(key)) return;
    let x = v;
    if (fin(axisMin)) x = Math.max(axisMin, x);
    if (fin(axisMax)) x = Math.min(axisMax, x);
    x = snap(x, step(key), lo(key), hi(key));
    const es = bounds[key] && bounds[key].step;
    if (fin(extraMin) && x < extraMin) x = snapUp(extraMin, es);
    if (fin(extraMax) && x > extraMax) x = snapDown(extraMax, es);
    if (fin(lo(key)) && x < lo(key)) x = lo(key);
    if (fin(hi(key)) && x > hi(key)) x = hi(key);
    out[key] = x;
  };

  if (h.x && "x" in proposed) {
    let emin, emax;
    if (handleId === "heat_norm" && fin(values.heat_curve_base_outdoor)) emax = values.heat_curve_base_outdoor - MIN_GAP;
    if (handleId === "heat_base" && fin(values.heat_curve_norm_outdoor)) emin = values.heat_curve_norm_outdoor + MIN_GAP;
    if (handleId === "cool_norm" || handleId === "cool_base") {
      const other = handleId === "cool_norm" ? values.cool_curve_base_outdoor : values.cool_curve_norm_outdoor;
      const order = opts.order || coolOrder(values);
      const iAmFirst = (handleId === "cool_norm") === (order === "norm_first");
      if (fin(other)) {
        if (iAmFirst) emax = other - MIN_GAP;
        else emin = other + MIN_GAP;
      }
    }
    place(h.x, proposed.x, ax && ax.xmin, ax && ax.xmax, emin, emax);
  }
  if (h.y && "y" in proposed) {
    place(h.y, proposed.y, ax && ax.ymin, ax && ax.ymax, MIN_FLOW, undefined);
  }
  return out;
}

export function coolOrder(values) {
  return fin(values.cool_curve_norm_outdoor) && fin(values.cool_curve_base_outdoor) && values.cool_curve_norm_outdoor > values.cool_curve_base_outdoor
    ? "base_first" : "norm_first";
}

/** Keyboard nudge: arrows move one entity step (× mult). */
export function nudge(handleId, key, values, bounds, mult = 1, opts = {}) {
  const h = HANDLES[handleId];
  if (!h) return {};
  const dir = { ArrowLeft: ["x", -1], ArrowRight: ["x", 1], ArrowDown: ["y", -1], ArrowUp: ["y", 1] }[key];
  if (!dir) return {};
  let [axis, sign] = dir;
  // Lines move along their only axis with any arrow key.
  if (h.kind === "vline") axis = "x";
  if (h.kind === "hline") axis = "y";
  const roleKey = h[axis];
  if (!roleKey || !fin(values[roleKey])) return {};
  const st = (bounds[roleKey] && bounds[roleKey].step) || 0.5;
  const s = key === "ArrowLeft" || key === "ArrowDown" ? -1 : 1;
  return constrain(handleId, { [axis]: values[roleKey] + s * st * mult }, values, bounds, { ...opts, axes: undefined });
}

/** Changed values: [{key, from, to}] in a stable order. */
export function pendingDiff(original, pending, bounds = {}) {
  const out = [];
  for (const key of CURVE_KEYS) {
    if (!(key in pending)) continue;
    const from = original[key];
    const to = pending[key];
    const st = bounds[key] && bounds[key].step;
    const eps = fin(st) && st > 0 ? st / 2 : 1e-6;
    if (!fin(from) || Math.abs(to - from) >= eps - 1e-9) out.push({ key, from, to });
  }
  return out;
}

/** Feed has caught up with a written value (within half a step). */
export function reflects(feedValue, written, step) {
  if (!fin(feedValue) || !fin(written)) return false;
  const eps = fin(step) && step > 0 ? step / 2 : 0.05;
  return Math.abs(feedValue - written) < eps + 1e-9;
}

/**
 * Effect of a pending change at outdoor temperature x: the controller's
 * current target moves by the difference of the two configured curves.
 */
export function effectAt(before, after, x, nowFlow) {
  const b = heatFlowAt(before, x);
  const a = heatFlowAt(after, x);
  if (b === undefined || a === undefined) return null;
  const from = fin(nowFlow) ? nowFlow : b;
  let to = from + (a - b);
  if (fin(after.heat_curve_max_flow)) to = Math.min(after.heat_curve_max_flow, to);
  return { from, to, aboveLimit: aboveHeatLimit(after, x) };
}
