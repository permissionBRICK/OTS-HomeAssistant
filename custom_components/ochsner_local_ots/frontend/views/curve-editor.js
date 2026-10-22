// Heating-curve editor (card C, top): draw the configured heating/cooling
// curves and let the user drag their points/limits with mouse, touch or
// keyboard. Changes stay pending until "Apply"; then each changed number
// entity is written once (sequentially).

import { esc } from "../lib/dom.js";
import {
  COOL_KEYS, CURVE_KEYS, HANDLES, HEAT_KEYS, SHIFT_THRESHOLD,
  aboveHeatLimit, axes, constrain, coolFlowAt, coolOrder, coolPointsOrdered, curveShift, dragStep,
  effectAt, hasCoolCurve, hasHeatCurve, heatFlowAt, nudge, pendingDiff, reflects, ticks,
} from "../lib/curve.js";
import { hcKey, num, writableEntity, role } from "../lib/model.js";

const AWAIT_TIMEOUT = 90000;
const P = { l: 34, r: 12, t: 14, b: 24 };
const HIT_R = 22;

const errText = (err) => (err && (err.message || err.code)) || String(err);

export class CurveEditor {
  constructor(card) {
    this.card = card;
    this.st = new Map(); // circuit → { pending, awaiting, applying, error }
    this.drag = null;
    this.geom = null; // last render geometry (for pointer maths)
    this.focused = null; // handle id with keyboard focus
    this._hoverTimer = null;
  }

  _s(n) {
    let s = this.st.get(n);
    if (!s) {
      s = { pending: {}, awaiting: {}, applying: null, error: null };
      this.st.set(n, s);
    }
    return s;
  }

  hasPending(n) {
    const s = this.st.get(n);
    return !!s && Object.keys(s.pending).length > 0;
  }

  /** Feed values (+ written-but-unconfirmed values), without pending. */
  _base(ctx, n) {
    const s = this._s(n);
    const v = {};
    for (const k of CURVE_KEYS) {
      const x = num(ctx.live, hcKey(n, k));
      if (x !== undefined) v[k] = x;
    }
    const now = Date.now();
    for (const [k, a] of Object.entries(s.awaiting)) {
      const step = this._bounds(ctx, n)[k]?.step;
      if (reflects(v[k], a.v, step) || now - a.at > AWAIT_TIMEOUT) delete s.awaiting[k];
      else v[k] = a.v;
    }
    return v;
  }

  _bounds(ctx, n) {
    const b = {};
    for (const k of CURVE_KEYS) {
      const r = role(ctx.plant, hcKey(n, k));
      if (!r) continue;
      b[k] = { min: typeof r.min === "number" ? r.min : undefined, max: typeof r.max === "number" ? r.max : undefined, step: typeof r.step === "number" && r.step > 0 ? r.step : 0.5, unit: r.unit || "°C" };
    }
    return b;
  }

  _writable(ctx, n) {
    const w = new Set();
    for (const k of CURVE_KEYS) if (writableEntity(ctx.plant, hcKey(n, k))) w.add(k);
    return w;
  }

  _values(ctx, n) {
    return { ...this._base(ctx, n), ...this._s(n).pending };
  }

  bind(el) {
    el.addEventListener("pointerdown", (e) => this._down(e));
    el.addEventListener("pointermove", (e) => this._move(e));
    el.addEventListener("pointerup", (e) => this._up(e));
    el.addEventListener("pointercancel", (e) => this._up(e, true));
    el.addEventListener("lostpointercapture", (e) => this._up(e));
    el.addEventListener("pointerleave", (e) => {
      if (e.pointerType === "mouse" && !this.drag) this._hideCross();
    });
    el.addEventListener("keydown", (e) => this._key(e));
    // touch-action on SVG children is not honoured everywhere: block the
    // page scroll explicitly for touches that start on a draggable handle.
    const stopScroll = (e) => {
      if (this.drag || (e.target.closest && e.target.closest("svg.ch .h[data-h]:not(.ro)"))) {
        if (e.cancelable) e.preventDefault();
      }
    };
    el.addEventListener("touchstart", stopScroll, { passive: false });
    el.addEventListener("touchmove", stopScroll, { passive: false });
    el.addEventListener("focusin", (e) => {
      const h = e.target.closest && e.target.closest("svg.ch .h[data-h]");
      const id = h ? h.getAttribute("data-h") : null;
      if (id !== this.focused) {
        this.focused = id;
        this.card.requestRender();
      }
    });
    el.addEventListener("focusout", (e) => {
      const h = e.target.closest && e.target.closest("svg.ch .h[data-h]");
      if (h && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest("svg.ch .h[data-h]"))) {
        this.focused = null;
        this.card.requestRender();
      }
    });
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-cact]");
      if (!b || b.disabled) return;
      const n = Number(b.getAttribute("data-c"));
      if (b.getAttribute("data-cact") === "apply") this.apply(n);
      else this.discard(n);
    });
  }

  // ---- pointer -------------------------------------------------------------

  _svgPoint(e) {
    const g = this.geom;
    if (!g || !g.svg || !g.svg.isConnected) return null;
    const rect = g.svg.getBoundingClientRect();
    if (!rect.width) return null;
    return { x: ((e.clientX - rect.left) * g.W) / rect.width, y: ((e.clientY - rect.top) * g.H) / rect.height };
  }

  _down(e) {
    const hEl = e.target.closest && e.target.closest("svg.ch .h[data-h]");
    const svg = e.target.closest && e.target.closest("svg.ch[data-curve]");
    if (!svg || !this.geom) return;
    this.geom.svg = svg;
    if (!hEl) {
      if (e.pointerType !== "mouse") this._showCross(e, true);
      return;
    }
    if (hEl.classList.contains("ro") || e.button > 0) return;
    const ctx = this.card.ctx;
    if (!ctx || ctx.disabled) return;
    const id = hEl.getAttribute("data-h");
    const n = this.geom.n;
    const pt = this._svgPoint(e);
    if (!pt) return;
    e.preventDefault();
    const vals = this._values(ctx, n);
    const h = HANDLES[id];
    const g = this.geom;
    const hx = h.x ? g.X(vals[h.x]) : pt.x;
    const hy = h.y ? g.Y(vals[h.y]) : pt.y;
    this.drag = {
      id, n, pointerId: e.pointerId,
      offX: hx - pt.x, offY: hy - pt.y,
      axes: { ...g.ax },
      startPending: { ...this._s(n).pending },
      order: coolOrder(vals),
      moved: false,
    };
    try {
      svg.setPointerCapture(e.pointerId);
    } catch (_e) { /* synthetic events */ }
    try {
      hEl.focus({ preventScroll: true });
    } catch (_e) { /* ignore */ }
    this.focused = id;
    this._hideCross();
    this.card.requestRender();
  }

  _move(e) {
    if (!this.drag) {
      if (e.pointerType === "mouse") this._showCross(e, false);
      return;
    }
    if (e.pointerId !== this.drag.pointerId) return;
    const ctx = this.card.ctx;
    if (!ctx) return;
    const pt = this._svgPoint(e);
    if (!pt) return;
    e.preventDefault();
    const d = this.drag;
    const g = this.geom;
    const n = d.n;
    const bounds = this._bounds(ctx, n);
    const vals = this._values(ctx, n);
    const h = HANDLES[d.id];
    const prop = {};
    if (h.x) prop.x = g.Xi(pt.x + d.offX);
    if (h.y) prop.y = g.Yi(pt.y + d.offY);
    const steps = {};
    if (h.x) steps[h.x] = dragStep(bounds[h.x]?.step, (d.axes.xmax - d.axes.xmin) / g.pw);
    if (h.y) steps[h.y] = dragStep(bounds[h.y]?.step, (d.axes.ymax - d.axes.ymin) / g.ph);
    const res = constrain(d.id, prop, vals, bounds, { steps, axes: d.axes, order: d.order, writable: this._writable(ctx, n) });
    this._setPending(ctx, n, res);
    d.moved = true;
  }

  _up(e, cancel = false) {
    const d = this.drag;
    if (!d || e.pointerId !== d.pointerId) return;
    this.drag = null;
    if (cancel && !d.moved) this._s(d.n).pending = d.startPending;
    try {
      if (this.geom && this.geom.svg && this.geom.svg.hasPointerCapture && this.geom.svg.hasPointerCapture(e.pointerId)) this.geom.svg.releasePointerCapture(e.pointerId);
    } catch (_e) { /* ignore */ }
    this.card.requestRender();
  }

  /** Merge new handle values into pending; values equal to the base drop out. */
  _setPending(ctx, n, res) {
    const s = this._s(n);
    const base = this._base(ctx, n);
    const bounds = this._bounds(ctx, n);
    let changed = false;
    for (const [k, v] of Object.entries(res)) {
      const eps = (bounds[k]?.step || 0.1) / 2;
      if (typeof base[k] === "number" && Math.abs(base[k] - v) < eps) {
        if (k in s.pending) { delete s.pending[k]; changed = true; }
      } else if (s.pending[k] !== v) {
        s.pending[k] = v;
        changed = true;
      }
    }
    s.error = null;
    if (changed) this.card.requestRender();
  }

  // ---- keyboard ------------------------------------------------------------

  _key(e) {
    const hEl = e.target.closest && e.target.closest("svg.ch .h[data-h]");
    if (!hEl) return;
    const ctx = this.card.ctx;
    if (!ctx || !this.geom) return;
    const n = this.geom.n;
    const id = hEl.getAttribute("data-h");
    if (e.key === "Escape") {
      e.preventDefault();
      if (this.drag) {
        this._s(n).pending = this.drag.startPending;
        this.drag = null;
        this.card.requestRender();
      } else this.discard(n);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      this.apply(n);
      return;
    }
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(e.key)) return;
    e.preventDefault();
    if (hEl.classList.contains("ro") || ctx.disabled) return;
    const vals = this._values(ctx, n);
    const res = nudge(id, e.key, vals, this._bounds(ctx, n), e.shiftKey ? 5 : 1, { writable: this._writable(ctx, n) });
    this._setPending(ctx, n, res);
  }

  // ---- apply / discard -----------------------------------------------------

  discard(n) {
    const s = this._s(n);
    if (s.applying) return;
    s.pending = {};
    s.error = null;
    this.card.requestRender();
  }

  async apply(n) {
    const ctx = this.card.ctx;
    const s = this._s(n);
    if (!ctx || s.applying || ctx.disabled) return;
    const base = this._base(ctx, n);
    const bounds = this._bounds(ctx, n);
    const diff = pendingDiff(base, s.pending, bounds);
    // Drop no-op entries.
    for (const k of Object.keys(s.pending)) if (!diff.some((d) => d.key === k)) delete s.pending[k];
    if (!diff.length) {
      this.card.requestRender();
      return;
    }
    s.error = null;
    s.applying = { i: 0, n: diff.length };
    this.card.requestRender();
    for (const d of diff) {
      s.applying.i++;
      this.card.requestRender();
      const ent = writableEntity(this.card.ctx.plant, hcKey(n, d.key));
      if (!ent) {
        s.error = this.card.i18n.t("handle_readonly", { name: this.card.i18n.t("r_" + d.key) });
        break;
      }
      try {
        await this.card.call("number", "set_value", { entity_id: ent, value: d.to });
        delete s.pending[d.key];
        s.awaiting[d.key] = { v: d.to, at: Date.now() };
      } catch (err) {
        s.error = this.card.i18n.t("apply_error", { msg: errText(err) });
        break;
      }
    }
    s.applying = null;
    this.card.requestRender();
  }

  // ---- crosshair (imperative, no re-render) --------------------------------

  _showCross(e, sticky) {
    const g = this.geom;
    const ctx = this.card.ctx;
    if (!g || !g.svg || !ctx) return;
    const pt = this._svgPoint(e);
    const line = g.svg.querySelector(".cx");
    const tip = g.svg.parentElement && g.svg.parentElement.querySelector(".tip.cur");
    if (!pt || !line || !tip) return;
    if (pt.x < P.l || pt.x > g.W - P.r || pt.y < P.t || pt.y > g.H - P.b) {
      this._hideCross();
      return;
    }
    const I = ctx.I;
    const x = g.Xi(pt.x);
    const vals = this._values(ctx, g.n);
    let text;
    if (hasHeatCurve(vals)) {
      text = aboveHeatLimit(vals, x)
        ? I.t("tip_above_limit", { out: I.temp(x) })
        : I.t("tip_curve", { out: I.temp(x), flow: I.temp(heatFlowAt(vals, x)) });
    } else text = I.temp(x);
    if (g.cool && hasCoolCurve(vals) && (typeof vals.cool_limit !== "number" || x >= vals.cool_limit)) text += " · " + I.t("tip_cool", { flow: I.temp(coolFlowAt(vals, x)) });
    line.setAttribute("x1", pt.x);
    line.setAttribute("x2", pt.x);
    line.style.display = "";
    tip.textContent = text;
    tip.style.display = "block";
    const rect = g.svg.getBoundingClientRect();
    const scale = rect.width / g.W;
    let left = pt.x * scale + 12;
    const tw = tip.offsetWidth;
    if (left + tw > rect.width) left = Math.max(0, pt.x * scale - tw - 12);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(0, pt.y * scale - 38)}px`;
    if (this._hoverTimer) clearTimeout(this._hoverTimer);
    if (sticky) this._hoverTimer = setTimeout(() => this._hideCross(), 2500);
  }

  _hideCross() {
    const g = this.geom;
    if (!g || !g.svg) return;
    const line = g.svg.querySelector(".cx");
    const tip = g.svg.parentElement && g.svg.parentElement.querySelector(".tip.cur");
    if (line) line.style.display = "none";
    if (tip) tip.style.display = "none";
  }

  // ---- render --------------------------------------------------------------

  /** Called after morph so pointer maths use the live <svg>. */
  attach(root) {
    if (this.geom) this.geom.svg = root.querySelector("svg.ch[data-curve]");
  }

  render(ctx, circuit, width) {
    const { I } = ctx;
    const n = circuit.index;
    const s = this._s(n);
    const base = this._base(ctx, n);
    const bounds = this._bounds(ctx, n);
    const writable = this._writable(ctx, n);
    const vals = { ...base, ...s.pending };
    const cool = !!circuit.cool && hasCoolCurve(vals);
    if (!hasHeatCurve(vals) && !cool) return `<div class="empty">–</div>`;

    const nowX = num(ctx.live, "hp.outdoor_avg") ?? num(ctx.live, "hp.outdoor");
    const nowFlow = num(ctx.live, hcKey(n, "flow_target"));
    const actual = num(ctx.live, hcKey(n, "flow_actual"));
    const ax = this.drag && this.drag.n === n ? this.drag.axes : axes(vals, { nowX, nowFlow, actualFlow: actual, cool });

    const W = Math.max(260, Math.round(width));
    const H = Math.round(Math.min(340, Math.max(210, W * 0.56)));
    const pw = W - P.l - P.r, ph = H - P.t - P.b;
    const X = (v) => P.l + ((v - ax.xmin) / (ax.xmax - ax.xmin)) * pw;
    const Y = (v) => H - P.b - ((v - ax.ymin) / (ax.ymax - ax.ymin)) * ph;
    const Xi = (px) => ax.xmin + ((px - P.l) / pw) * (ax.xmax - ax.xmin);
    const Yi = (py) => ax.ymin + ((H - P.b - py) / ph) * (ax.ymax - ax.ymin);
    const prevSvg = this.geom && this.geom.svg;
    this.geom = { n, W, H, pw, ph, X, Y, Xi, Yi, ax, cool, svg: prevSvg };
    const r1 = (v) => Math.round(v * 10) / 10;
    const clampY = (v) => Math.max(P.t - 6, Math.min(H - P.b, v));

    let g = "";
    // grid + axes
    for (const v of ticks(ax.ymin, ax.ymax, 10)) g += `<line class="${v === ax.ymin ? "base" : "grid"}" x1="${P.l}" x2="${W - P.r}" y1="${r1(Y(v))}" y2="${r1(Y(v))}"/><text class="ax" x="${P.l - 6}" y="${r1(Y(v) + 3.5)}" text-anchor="end">${v}°</text>`;
    const xstep = pw / ((ax.xmax - ax.xmin) / 10) < 34 ? 20 : 10;
    for (const v of ticks(ax.xmin, ax.xmax, xstep)) g += `<text class="ax" x="${r1(X(v))}" y="${H - 7}" text-anchor="middle">${I.num(v, 0)}°</text>`;
    g += `<rect class="plotbg" x="${P.l}" y="${P.t}" width="${pw}" height="${ph}"/>`;

    const heatPath = (c, shift = 0) => {
      const brk = [ax.xmin, ax.xmax];
      if (typeof c.heat_limit === "number") brk.push(c.heat_limit);
      if (typeof c.heat_curve_max_flow === "number") {
        // x where the straight line reaches max flow
        const x1 = c.heat_curve_norm_outdoor, y1 = c.heat_curve_norm_flow, x2 = c.heat_curve_base_outdoor, y2 = c.heat_curve_base_flow;
        if (y1 !== y2) brk.push(x1 + ((c.heat_curve_max_flow - shift - y1) * (x2 - x1)) / (y2 - y1));
      }
      const xs = [...new Set(brk.filter((x) => x >= ax.xmin && x <= ax.xmax))].sort((a, b) => a - b);
      const lim = typeof c.heat_limit === "number" ? Math.min(Math.max(c.heat_limit, ax.xmin), ax.xmax) : ax.xmax;
      const seg = (from, to) => {
        const pts = xs.filter((x) => x > from && x < to);
        return [from, ...pts, to].map((x, i) => `${i ? "L" : "M"}${r1(X(x))} ${r1(clampY(Y(heatFlowAt(c, x, shift))))}`).join(" ");
      };
      return { solid: lim > ax.xmin ? seg(ax.xmin, lim) : "", dashed: lim < ax.xmax ? seg(lim, ax.xmax) : "" };
    };

    const coolPath = (c) => {
      const [a, b] = coolPointsOrdered(c);
      const from = typeof c.cool_limit === "number" ? Math.max(ax.xmin, Math.min(c.cool_limit, a[0])) : a[0];
      const xs = [from, a[0], b[0], ax.xmax].filter((x, i, arr) => x >= from && (i === 0 || x !== arr[i - 1]));
      return xs.map((x, i) => `${i ? "L" : "M"}${r1(X(x))} ${r1(Y(coolFlowAt(c, x)))}`).join(" ");
    };

    const diff = pendingDiff(base, s.pending, bounds);
    const hasHeat = hasHeatCurve(vals);
    let curves = "";
    // ghost of the current configuration while changes are pending
    if (diff.length && hasHeat && hasHeatCurve(base)) {
      const gp = heatPath(base);
      curves += `<path d="${gp.solid} ${gp.dashed}" fill="none" stroke="var(--ohp-heat)" stroke-width="1.5" stroke-dasharray="2 4" opacity=".45"/>`;
    }
    if (diff.length && cool && hasCoolCurve(base)) curves += `<path d="${coolPath(base)}" fill="none" stroke="var(--ohp-cool)" stroke-width="1.5" stroke-dasharray="2 4" opacity=".45"/>`;
    // effective (shifted) curve through the controller's real target
    const shift = hasHeat && hasHeatCurve(base) ? curveShift(base, nowX, nowFlow) : 0;
    const showShift = Math.abs(shift) > SHIFT_THRESHOLD && nowX !== undefined && !aboveHeatLimit(base, nowX);
    if (showShift) {
      const ep = heatPath(vals, shift);
      curves += `<path d="${ep.solid}" fill="none" stroke="var(--ohp-heat)" stroke-width="1.5" opacity=".35"/>`;
    }
    if (hasHeat) {
      const hp = heatPath(vals);
      if (hp.solid) curves += `<path d="${hp.solid}" fill="none" stroke="var(--ohp-heat)" stroke-width="2.5" stroke-linejoin="round"/>`;
      if (hp.dashed) curves += `<path d="${hp.dashed}" fill="none" stroke="var(--ohp-heat)" stroke-width="2" stroke-dasharray="4 4" opacity=".6"/>`;
    }
    if (cool) curves += `<path d="${coolPath(vals)}" fill="none" stroke="var(--ohp-cool)" stroke-width="2.5" stroke-linejoin="round"/>`;

    // now point
    let now = "";
    if (nowX !== undefined && nowFlow !== undefined && nowX >= ax.xmin && nowX <= ax.xmax) {
      const nx = X(nowX), ny = Y(nowFlow);
      now += `<line x1="${r1(nx)}" x2="${r1(nx)}" y1="${r1(ny)}" y2="${H - P.b}" stroke="var(--ohp-ink)" stroke-opacity=".35"/>`;
      if (actual !== undefined) now += `<circle cx="${r1(nx)}" cy="${r1(Y(actual))}" r="4.5" fill="none" stroke="var(--ohp-ink)" stroke-width="1.5" opacity=".8"><title>${esc(I.t("flow_actual"))} ${I.temp(actual)}</title></circle>`;
      now += `<circle cx="${r1(nx)}" cy="${r1(ny)}" r="5" fill="var(--ohp-ink)" stroke="var(--ohp-card)" stroke-width="2"/>`;
      const txt = I.t("now_label", { flow: I.temp(nowFlow), out: I.temp(nowX) });
      const tw = txt.length * 5.9, th = 12;
      // Pick the label position that collides least with handles and curves.
      const obstacles = [];
      for (const id of ["heat_norm", "heat_base", "cool_norm", "cool_base"]) {
        const h = HANDLES[id];
        if (typeof vals[h.x] === "number" && typeof vals[h.y] === "number") obstacles.push([X(vals[h.x]), Y(vals[h.y]), 13]);
      }
      for (let px = P.l; px <= W - P.r; px += 6) {
        const xv = Xi(px);
        if (hasHeat) obstacles.push([px, Y(heatFlowAt(vals, xv)), 3]);
        if (cool && (typeof vals.cool_limit !== "number" || xv >= vals.cool_limit)) obstacles.push([px, Y(coolFlowAt(vals, xv)), 3]);
      }
      const cands = [
        [nx - 8 - tw, ny - 10 - th], [nx + 8, ny + 6], [nx - 8 - tw, ny + 6], [nx + 8, ny - 10 - th],
        [nx - tw / 2, ny - 14 - th], [nx - tw / 2, ny + 10],
      ];
      let best = cands[0], bestScore = Infinity;
      cands.forEach(([lx, ly], i) => {
        let score = i * 0.01;
        if (lx < P.l || lx + tw > W - P.r || ly < P.t || ly + th > H - P.b) score += 50;
        for (const [ox, oy, rr] of obstacles) if (ox > lx - rr && ox < lx + tw + rr && oy > ly - rr && oy < ly + th + rr) score += rr > 5 ? 10 : 1;
        if (score < bestScore) { bestScore = score; best = [lx, ly]; }
      });
      now += `<text class="ax" x="${r1(best[0])}" y="${r1(best[1] + th - 2)}" text-anchor="start" style="fill:var(--ohp-ink)">${esc(txt)}</text>`;
    }

    // handles
    const active = this.drag && this.drag.n === n ? this.drag.id : this.focused;
    const roName = (keys) => keys.filter((k) => !writable.has(k)).map((k) => I.t("r_" + k)).join(", ");
    let lines = "";
    let points = "";
    const handleAttrs = (id, keys, label, valuetext) => {
      const ro = keys.every((k) => !writable.has(k)) || ctx.disabled;
      const tip = ro && !ctx.disabled ? I.t("handle_readonly", { name: roName(keys) }) : label;
      return {
        ro,
        open: `<g class="h${ro ? " ro" : ""}${active === id ? " act" : ""}" data-h="${id}" data-key="h-${id}" ${ro ? "" : 'tabindex="0" '}role="slider" aria-label="${esc(label)}" aria-valuetext="${esc(valuetext)}"${ro ? ' aria-disabled="true"' : ""}><title>${esc(tip)}</title>`,
      };
    };
    const vline = (id, key, col, labelKey, labelTop) => {
      const v = vals[key];
      if (typeof v !== "number" || v < ax.xmin || v > ax.xmax) return;
      const x = r1(X(v));
      const txt = I.t(labelKey, { t: I.temp(v) });
      const a = handleAttrs(id, [key], I.t("h_" + id), txt);
      const tw = txt.length * 5.8;
      const flip = x + 6 + tw > W - P.r;
      const ty = labelTop ? P.t + 9 : H - P.b - 6;
      lines += `${a.open}<rect class="hit" x="${x - 11}" y="${P.t}" width="22" height="${ph}"/>`
        + `<line class="lineh" x1="${x}" x2="${x}" y1="${P.t}" y2="${H - P.b}" stroke="${col}" stroke-width="1.5" stroke-dasharray="3 4" opacity=".85"/>`
        + (a.ro ? "" : `<rect class="ring" x="${x - 4}" y="${labelTop ? P.t - 2 : H - P.b - 18}" width="8" height="20" rx="4" fill="var(--ohp-card)" stroke="${col}" stroke-width="1.5"/>`)
        + `<text class="axl" x="${r1(flip ? x - 8 : x + 8)}" y="${ty}" text-anchor="${flip ? "end" : "start"}">${esc(txt)}</text></g>`;
    };
    const hline = (id, key, col) => {
      const v = vals[key];
      if (typeof v !== "number" || v < ax.ymin || v > ax.ymax) return;
      const y = r1(Y(v));
      const txt = I.t("max_flow_label", { t: I.temp(v, 0) });
      const a = handleAttrs(id, [key], I.t("h_" + id), txt);
      lines += `${a.open}<rect class="hit" x="${P.l}" y="${y - 11}" width="${pw}" height="22"/>`
        + `<line class="lineh" x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="${col}" stroke-width="1.2" stroke-dasharray="6 4" opacity=".7"/>`
        + (a.ro ? "" : `<rect class="ring" x="${P.l + 2}" y="${y - 4}" width="20" height="8" rx="4" fill="var(--ohp-card)" stroke="${col}" stroke-width="1.5"/>`)
        + `<text class="axl" x="${P.l + 28}" y="${y - 5}">${esc(txt)}</text></g>`;
    };
    const point = (id, col) => {
      const h = HANDLES[id];
      const x = vals[h.x], y = vals[h.y];
      if (typeof x !== "number" || typeof y !== "number") return;
      const cx = r1(X(x)), cy = r1(Y(y));
      const vt = `${I.temp(x)} → ${I.temp(y)}`;
      const a = handleAttrs(id, [h.x, h.y], I.t("h_" + id), vt);
      points += `${a.open}<circle class="hit" cx="${cx}" cy="${cy}" r="${HIT_R}"/>`
        + `<circle class="ring" cx="${cx}" cy="${cy}" r="12" fill="none" stroke="none"/>`
        + `<circle class="dotv" cx="${cx}" cy="${cy}" r="6.5" fill="${a.ro ? col : "var(--ohp-card)"}" stroke="${col}" stroke-width="2.5"/></g>`;
    };
    if (hasHeat) {
      vline("heat_limit", "heat_limit", "var(--ohp-ink2)", "heat_limit_label", true);
      hline("heat_max", "heat_curve_max_flow", "var(--ohp-heat)");
    }
    if (cool) vline("cool_limit", "cool_limit", "var(--ohp-cool)", "cool_limit_label", false);
    if (hasHeat) {
      point("heat_norm", "var(--ohp-heat)");
      point("heat_base", "var(--ohp-heat)");
    }
    if (cool) {
      point("cool_norm", "var(--ohp-cool)");
      point("cool_base", "var(--ohp-cool)");
    }

    // floating value label next to the active handle
    let flab = "";
    if (active && HANDLES[active] && (this.drag || diff.length || this.focused)) {
      const h = HANDLES[active];
      let txt = "", hx, hy;
      if (h.kind === "point" && typeof vals[h.x] === "number" && typeof vals[h.y] === "number") {
        txt = `${I.temp(vals[h.x])} → ${I.temp(vals[h.y])}`;
        hx = X(vals[h.x]); hy = Y(vals[h.y]);
      } else if (h.kind === "vline" && typeof vals[h.x] === "number") {
        txt = I.t(active === "heat_limit" ? "heat_limit_label" : "cool_limit_label", { t: I.temp(vals[h.x]) });
        hx = X(vals[h.x]); hy = P.t + ph * 0.42;
      } else if (h.kind === "hline" && typeof vals[h.y] === "number") {
        txt = I.t("max_flow_label", { t: I.temp(vals[h.y]) });
        hx = P.l + pw * 0.5; hy = Y(vals[h.y]);
      }
      if (txt) {
        const tw = txt.length * 6.6 + 14, th = 22;
        let lx = hx + 16, ly = hy - th - 12;
        if (lx + tw > W - 2) lx = hx - 16 - tw;
        if (lx < 2) lx = 2;
        if (ly < 2) ly = hy + 14;
        flab = `<g class="flab" pointer-events="none"><rect x="${r1(lx)}" y="${r1(ly)}" width="${r1(tw)}" height="${th}" rx="7"/><text x="${r1(lx + tw / 2)}" y="${r1(ly + 15)}" text-anchor="middle">${esc(txt)}</text></g>`;
      }
    }

    const cross = `<line class="cx" y1="${P.t}" y2="${H - P.b}" stroke="var(--ohp-ink2)" stroke-opacity=".6" style="display:none" pointer-events="none"/>`;
    const svg = `<svg class="ch${this.drag ? " dragging" : ""}" data-curve="${n}" viewBox="0 0 ${W} ${H}" width="100%" role="group" aria-label="${esc(I.t("heating_curve"))}">${g}${curves}${now}${cross}${lines}${points}${flab}</svg>`;

    // legend
    let legend = `<div class="legend">`;
    if (hasHeat) legend += `<span><i class="ln" style="background:var(--ohp-heat)"></i>${esc(I.t("heating_curve"))}</span>`;
    if (cool) legend += `<span><i class="ln" style="background:var(--ohp-cool)"></i>${esc(I.t("cooling_curve"))}</span>`;
    if (showShift) legend += `<span><i class="ln" style="background:var(--ohp-heat);opacity:.4"></i>${esc(I.t("effective"))}</span>`;
    if (nowFlow !== undefined) legend += `<span><i style="background:var(--ohp-ink);border-radius:50%"></i>${esc(I.t("now"))}</span>`;
    if (actual !== undefined && nowFlow !== undefined) legend += `<span><i class="ring"></i>${esc(I.t("flow_actual"))}</span>`;
    legend += `</div>`;

    // readout: effect at the current outdoor temperature
    let readout = "";
    if (diff.length && nowX !== undefined && hasHeat) {
      const eff = effectAt(base, vals, nowX, nowFlow);
      if (eff) readout = eff.aboveLimit ? esc(I.t("tip_above_limit", { out: I.temp(nowX) })) : I.t("effect_at_now", { out: `<b>${I.temp(nowX)}</b>`, from: `<b>${I.temp(eff.from)}</b>`, to: `<b>${I.temp(eff.to)}</b>` });
    } else if (this.focused && !this.drag) readout = esc(I.t("kbd_hint"));

    // pending bar
    let bar = "";
    const awaiting = Object.keys(s.awaiting).length;
    if (diff.length || s.applying || s.error) {
      const items = diff.map((d) => {
        const st = bounds[d.key]?.step || 0.5;
        const dec = st >= 1 ? 0 : st >= 0.1 ? 1 : 2;
        return `<li>${esc(I.t("r_" + d.key))} ${I.auto(d.from, dec)} → <b>${I.auto(d.to, dec)}</b> ${esc(bounds[d.key]?.unit || "°C")}</li>`;
      }).join("");
      const cost = I.t(diff.length === 1 ? "writes_count" : "writes_count_plural", { n: diff.length });
      const busy = !!s.applying;
      bar = `<div class="pendbar" role="region" aria-label="${esc(I.t("pending_title"))}"><div class="t">${esc(I.t("pending_title"))}</div>`
        + (items ? `<ul>${items}</ul>` : "")
        + (s.error ? `<div class="note err" role="alert">${esc(s.error)}</div>` : "")
        + `<div class="row"><span class="cost">${busy ? esc(I.t("applying", { i: s.applying.i, n: s.applying.n })) : esc(cost)}</span>`
        + `<button class="btn" data-cact="discard" data-c="${n}"${busy ? " disabled" : ""}>${esc(I.t("discard"))}</button>`
        + `<button class="btn primary" data-cact="apply" data-c="${n}"${busy || !diff.length || ctx.disabled ? " disabled" : ""}>${esc(I.t("apply"))}</button></div></div>`;
    } else if (awaiting) {
      bar = `<div class="note wait" role="status">${esc(I.t("applied_wait"))}</div>`;
    }

    return `${legend}<div class="chartwrap">${svg}<div class="tip cur" style="display:none"></div></div><div class="readout" aria-live="polite">${readout}</div>${bar}`;
  }
}

export { HEAT_KEYS, COOL_KEYS };
