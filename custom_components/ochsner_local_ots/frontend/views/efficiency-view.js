// Card C: heating curve (per circuit tab) + 24 h energy and efficiency tiles.

import { esc } from "../lib/dom.js";
import { fetchHistory, hasData, hourlyEnergy, runStats, segments, statusResolver, toSeries, windowFor } from "../lib/history.js";
import { circuitName, displayEntity, hpMode, num, role } from "../lib/model.js";
import { CurveEditor } from "./curve-editor.js";

const HISTORY_TTL = 10 * 60 * 1000;
const HOUR = 3600 * 1000;

const MODE_FILL = {
  heat: "var(--ohp-heat)",
  cool: "var(--ohp-cool)",
  dhw: "var(--ohp-dhw)",
  defrost: "var(--ohp-defrost)",
  fault: "var(--ohp-err)",
  standby: "var(--ohp-pipe-off)",
};

function niceMax(v) {
  if (!(v > 0)) return 1;
  for (const m of [0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50]) if (v <= m) return m;
  return Math.ceil(v / 10) * 10;
}

export class EfficiencyView {
  constructor(card) {
    this.card = card;
    this.curve = new CurveEditor(card);
    this.tab = null;
    this.hist = { key: null, at: 0, loading: false, data: null, error: null };
  }

  set plantId(id) {
    this._plantId = id;
    this.curve.plantId = id;
  }

  get plantId() {
    return this._plantId;
  }

  dispose() {
    this.curve.dispose();
  }

  bind(el) {
    this.curve.bind(el);
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) {
        this.tab = Number(b.getAttribute("data-tab"));
        this.card.requestRender();
      }
    });
    el.addEventListener("pointermove", (e) => this._barTip(e));
    el.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse") this._barTip(e, true);
    });
    el.addEventListener("pointerleave", () => this._hideBarTip());
  }

  attach(root) {
    this.curve.attach(root);
  }

  // ---- history -------------------------------------------------------------

  _histIds(ctx) {
    return {
      heat: displayEntity(ctx.plant, "hp.heat_output"),
      el: ctx.sec.power ? displayEntity(ctx.plant, "hp.el_power") : null,
      status: displayEntity(ctx.plant, "hp.status"),
    };
  }

  _maybeFetch(ctx) {
    const ids = this._histIds(ctx);
    const key = JSON.stringify(ids);
    const h = this.hist;
    if (!ids.heat) {
      h.key = key;
      h.data = null;
      return;
    }
    if (h.loading) return;
    if (h.key === key && Date.now() - h.at < HISTORY_TTL) return;
    h.loading = true;
    h.key = key;
    h.at = Date.now();
    const win = windowFor(Date.now(), 24);
    fetchHistory(this.card.hass, [ids.heat, ids.el, ids.status], win)
      .then((res) => {
        h.data = this._aggregate(ctx, res, ids, win);
        h.error = null;
      })
      .catch((err) => {
        h.data = null;
        h.error = (err && err.message) || String(err);
      })
      .finally(() => {
        h.loading = false;
        this.card.requestRender();
      });
  }

  _aggregate(ctx, res, ids, win) {
    const heatS = toSeries(res[ids.heat]);
    if (!hasData(heatS)) return { empty: true };
    const heat = hourlyEnergy(heatS, win);
    const elS = ids.el ? toSeries(res[ids.el]) : [];
    const el = ids.el && hasData(elS) ? hourlyEnergy(elS, win) : null;
    const resolve = statusResolver((role(ctx.plant, "hp.status") || {}).labels, hpMode);
    const segs = ids.status ? segments(toSeries(res[ids.status]), win).map((s) => ({ ...s, mode: resolve(s.s) })) : [];
    return { win, heat: heat.kwh, el: el ? el.kwh : null, segs, runs: segs.length ? runStats(segs) : null };
  }

  // ---- bars ----------------------------------------------------------------

  _bars(ctx, data, width) {
    const { I } = ctx;
    const W = Math.max(260, Math.round(width));
    const H = 136, P = { l: 30, r: 8, t: 8, b: 34 };
    const n = data.heat.length;
    const power = !!data.el;
    const max = niceMax(Math.max(...data.heat, ...(data.el || [0])));
    const bw = (W - P.l - P.r) / n;
    const Y = (v) => H - P.b - (v / max) * (H - P.t - P.b);
    const r1 = (v) => Math.round(v * 10) / 10;
    let s = `<svg class="ch bars" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="${esc(I.t("last_24h"))}">`;
    // (no "0" tick label: the status strip label sits right below the baseline)
    for (const v of [0, max / 2, max]) s += `<line class="${v ? "grid" : "base"}" x1="${P.l}" x2="${W - P.r}" y1="${r1(Y(v))}" y2="${r1(Y(v))}"/>${v ? `<text class="ax" x="${P.l - 6}" y="${r1(Y(v) + 3.5)}" text-anchor="end">${I.auto(v, 1)}</text>` : ""}`;
    const gapPx = bw > 8 ? 1.5 : 0.75;
    for (let i = 0; i < n; i++) {
      const x = P.l + i * bw + gapPx, w = Math.max(1, bw - 2 * gapPx);
      const heat = data.heat[i];
      const top = (y0, y1, col) => {
        const rr = Math.min(2, (y0 - y1) / 2, w / 2);
        return `<path d="M${r1(x)} ${r1(y0)} V${r1(y1 + rr)} q0 ${-rr} ${rr} ${-rr} h${r1(w - 2 * rr)} q${rr} 0 ${rr} ${rr} V${r1(y0)} Z" fill="${col}"/>`;
      };
      if (heat > 0.02) {
        if (power) {
          const e = Math.min(data.el[i], heat);
          if (e > 0.01) s += `<rect x="${r1(x)}" y="${r1(Y(e))}" width="${r1(w)}" height="${r1(Y(0) - Y(e))}" fill="var(--ohp-el)"/>`;
          if (heat - e > 0.02) s += top(Y(e) - (e > 0.01 ? 1.5 : 0), Y(heat), "var(--ohp-amb)");
        } else s += top(Y(0), Y(heat), "var(--ohp-heat)");
      }
      s += `<rect class="bh" data-i="${i}" x="${r1(P.l + i * bw)}" y="${P.t}" width="${r1(bw)}" height="${H - P.t - P.b + 12}" fill="transparent"/>`;
    }
    // Buckets are rolling hours (ending now); label the full hours 00/06/12/18 where they fall.
    const span0 = data.win.hours * HOUR;
    for (let t = Math.ceil(data.win.start / HOUR) * HOUR; t <= data.win.end; t += HOUR) {
      const h = new Date(t).getHours();
      if (h % 6) continue;
      const x = P.l + ((t - data.win.start) / span0) * (W - P.l - P.r);
      if (x > W - P.r - 28) continue;
      s += `<line class="grid" x1="${r1(x)}" x2="${r1(x)}" y1="${H - P.b}" y2="${H - P.b + 3}"/><text class="ax" x="${r1(x)}" y="${H - 4}" text-anchor="middle">${String(h).padStart(2, "0")}:00</text>`;
    }
    // status strip (mode is also given as text in each segment's tooltip)
    const sy = Y(0) + 5;
    const span = data.win.hours * HOUR;
    const pw = W - P.l - P.r;
    for (const seg of data.segs || []) {
      const fill = MODE_FILL[seg.mode];
      if (!fill) continue;
      const a = P.l + ((seg.a - data.win.start) / span) * pw;
      const b = P.l + ((seg.b - data.win.start) / span) * pw;
      s += `<rect x="${r1(a)}" y="${r1(sy)}" width="${r1(Math.max(1, b - a - 0.5))}" height="6" rx="2" fill="${fill}" pointer-events="none"><title>${esc(String(seg.s))}</title></rect>`;
    }
    s += `<text class="ax" x="${P.l - 6}" y="${r1(sy + 6)}" text-anchor="end">${esc(I.t("hp_strip"))}</text></svg>`;
    return s;
  }

  _barTip(e, sticky) {
    const r = e.target.closest && e.target.closest("svg.bars .bh");
    if (!r) {
      if (!sticky) this._hideBarTip();
      return;
    }
    const ctx = this.card.ctx;
    const d = this.hist.data;
    if (!ctx || !d || d.empty) return;
    const I = ctx.I;
    const i = Number(r.getAttribute("data-i"));
    const from = new Date(d.win.start + i * HOUR), to = new Date(d.win.start + (i + 1) * HOUR);
    const hh = (t) => `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
    let txt = I.t("bar_tip", { from: hh(from), to: hh(to), heat: I.num(d.heat[i], 2) });
    if (d.el) {
      txt += I.t("bar_tip_power", { el: I.num(d.el[i], 2), amb: I.num(Math.max(0, d.heat[i] - d.el[i]), 2) });
      if (d.el[i] > 0.05) txt += I.t("bar_tip_cop", { cop: I.num(d.heat[i] / d.el[i], 1) });
    }
    const svg = r.ownerSVGElement;
    const wrap = svg.parentElement;
    const tip = wrap.querySelector(".tip.bar");
    if (!tip) return;
    tip.textContent = txt;
    tip.style.display = "block";
    const rect = wrap.getBoundingClientRect();
    let left = e.clientX - rect.left + 12;
    if (left + tip.offsetWidth > rect.width) left = Math.max(0, e.clientX - rect.left - tip.offsetWidth - 12);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(0, e.clientY - rect.top - 40)}px`;
  }

  _hideBarTip() {
    const tip = this.card.shadowRoot && this.card.shadowRoot.querySelector(".tip.bar");
    if (tip) tip.style.display = "none";
  }

  _tiles(ctx, d) {
    const { I, live, sec } = ctx;
    const t = [];
    let foot = "";
    if (d && !d.empty) {
      const H = d.heat.reduce((a, b) => a + b, 0);
      t.push([I.num(H, 1), I.t("kwh_heat")]);
      if (d.el) {
        const E = d.el.reduce((a, b) => a + b, 0);
        t.push([I.num(E, 1), I.t("kwh_el")]);
        if (H > 0.05) t.push([`${I.num(Math.max(0, (1 - E / H) * 100), 0)} %`, I.t("from_ambient")]);
        if (E > 0.05) {
          t.push([`${I.num(H / E, 1)}*`, I.t("cop_24h")]);
          foot = I.t("cop_footnote");
        }
      }
    }
    const cyc = num(live, "hp.cycles_today");
    if (cyc !== undefined) t.push([I.int(cyc), I.t("cycles_today")]);
    else if (d && d.runs) t.push([I.int(d.runs.runs), I.t("cycles")]);
    if (d && d.runs && d.runs.avgMinutes !== undefined) {
      const m = d.runs.avgMinutes;
      t.push([m >= 90 ? `${I.num(m / 60, 1)} ${I.t("h")}` : `${I.int(m)} ${I.t("min")}`, I.t("avg_runtime")]);
    }
    const jy = num(live, "hp.jaz_year");
    if (jy !== undefined && jy > 0) t.push([I.num(jy, 2), I.t("jaz_year", { y: new Date().getFullYear() })]);
    const jp = num(live, "hp.jaz_prev_year");
    if (jp !== undefined && jp > 0) t.push([I.num(jp, 2), I.t("jaz_prev")]);
    if (!sec.power) foot = "";
    return { html: t.map(([v, l]) => `<div class="tile"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join(""), foot };
  }

  render(ctx) {
    const { I, sec, live } = ctx;
    const circuits = sec.circuits;
    let h = "";
    if (circuits.length) {
      if (!circuits.some((c) => c.index === this.tab)) this.tab = circuits[0].index;
      const c = circuits.find((x) => x.index === this.tab);
      h += `<div class="hdr"><div class="ttl">${esc(I.t("heating_curve"))}</div><div class="sub">${esc(I.t("drag_hint"))}</div></div>`;
      if (circuits.length > 1) {
        h += `<div class="tabs" role="tablist">${circuits
          .map((x) => `<button role="tab" data-tab="${x.index}" class="${x.index === this.tab ? "on" : ""}" aria-selected="${x.index === this.tab}">${esc(circuitName(x, live))}${this.curve.hasPending(x.index) ? '<span class="pdot" aria-hidden="true"></span>' : ""}</button>`)
          .join("")}</div>`;
      }
      h += `<div data-key="curve-${c.index}">${this.curve.render(ctx, c, ctx.width)}</div>`;
    }
    this._maybeFetch(ctx);
    const d = this.hist.data;
    h += `<div class="hdr" style="margin-top:14px"><div class="ttl" style="font-size:14px">${esc(I.t("last_24h"))}</div></div>`;
    if (d && !d.empty) {
      h += `<div class="legend">${d.el
        ? `<span><i style="background:var(--ohp-el)"></i>${esc(I.t("electricity"))}</span><span><i style="background:var(--ohp-amb)"></i>${esc(I.t("ambient"))}</span><span>${esc(I.t("bars_hint"))}</span>`
        : `<span><i style="background:var(--ohp-heat)"></i>${esc(I.t("heat_kwh"))}</span>`}</div>`;
      h += `<div class="chartwrap">${this._bars(ctx, d, ctx.width)}<div class="tip bar" style="display:none"></div></div>`;
    } else if (this.hist.loading && !d) {
      h += `<div class="sub">${esc(I.t("history_loading"))}</div>`;
    } else {
      h += `<div class="sub">${esc(I.t("no_history"))}</div>`;
    }
    const tiles = this._tiles(ctx, d);
    if (tiles.html) h += `<div class="tiles">${tiles.html}</div>`;
    if (tiles.foot) h += `<div class="foot">${esc(tiles.foot)}</div>`;
    const fw = ctx.plant.flash_writes && ctx.state(ctx.plant.flash_writes);
    if (fw && fw.state !== undefined && Number.isFinite(Number(fw.state))) {
      h += `<div class="foot"><button class="chip" data-more="${esc(ctx.plant.flash_writes)}" style="cursor:pointer">${esc(I.t("flash_writes", { n: I.int(Number(fw.state)) }))}</button></div>`;
    }
    return h;
  }
}
