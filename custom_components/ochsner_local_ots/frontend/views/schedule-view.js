// Card D: the circuits' time programs. When a circuit in Comfort runs at its
// Normal setpoint; the rest of the day it runs at Night. One tab per circuit,
// a week grid, a day editor (drag the edges or type the times) and, like the
// heating curve, nothing is written before "Apply".

import { esc } from "../lib/dom.js";
import { clockSvg, moonSvg } from "../lib/icons.js";
import { circuitName, hcKey, num, ofDomain, setpointRows, targetIsCool } from "../lib/model.js";
import {
  DAY, NORMAL, PRESETS, SLOTS, STEP, dayNames, haNow, hasNightSetback, hm, normalizePeriods, parseHm,
  pointsFor, samePeriods, storedPeriods, suggestPeriod, writeCount,
} from "../lib/schedule.js";
import { presetState } from "./control-view.js";
import { programText } from "./plant-view.js";

const NOTE_MS = 5000;
const LABEL_PX = 74; // a block needs this much room for "06:30–23:00"

const errText = (err) => (err && (err.message || err.code)) || String(err);
const pct = (m) => ((m / DAY) * 100).toFixed(3);
const clampTo = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

export class ScheduleView {
  constructor(card) {
    this.card = card;
    this.tab = null;
    this.sel = new Set(); // days being edited (0 = Monday)
    this.pending = new Map(); // circuit → Map(day → Normal periods)
    this.applying = false;
    this.note = null; // { cls, text, until }
    this.inputError = null;
    this.copyOpen = false;
    this.copyTo = new Set();
    this.drag = null;
    this._timers = new Set();
  }

  dispose() {
    this.disposed = true;
    this.pause();
  }

  /** Card left the page: stop a drag and the note timer (pending edits stay). */
  pause() {
    this.drag = null;
    for (const t of this._timers) clearTimeout(t);
    this._timers.clear();
  }

  /** Show circuit n (from a circuit tile). */
  select(n) {
    if (this.tab === n) return;
    this.tab = n;
    this.sel.clear();
    this.copyOpen = false;
    this.inputError = null;
  }

  hasPending(n) {
    return this.pending.has(n);
  }

  _later(ms, fn) {
    if (this.disposed) return;
    const id = setTimeout(() => {
      this._timers.delete(id);
      fn();
    }, ms);
    this._timers.add(id);
  }

  /** Last render's context, only while it belongs to this view's plant. */
  _ctx() {
    const ctx = this.card.ctx;
    return !this.disposed && ctx && ctx.plant.plant_id === this.plantId ? ctx : null;
  }

  _programs(ctx) {
    const s = ctx.schedules;
    return s && s.circuits ? s.circuits : null;
  }

  _program(ctx, n) {
    const p = this._programs(ctx);
    return (p && p.get(n)) || null;
  }

  /** Circuits shown with a time program on the controller. */
  _circuits(ctx) {
    const progs = this._programs(ctx);
    if (!progs) return [];
    return ctx.sec.circuits.filter((c) => {
      const p = progs.get(c.index);
      return p && p.supported;
    });
  }

  /** A day's Normal periods: pending, else stored (null = could not be read). */
  _periods(ctx, n, d) {
    const pend = this.pending.get(n);
    if (pend && pend.has(d)) return pend.get(d);
    return storedPeriods(this._program(ctx, n), d);
  }

  _editable(ctx, n) {
    const p = this._program(ctx, n);
    return !!p && p.supported && p.complete && p.writable !== false && !ctx.disabled && !this.applying;
  }

  /** Stage periods for a day; equal to what is stored = not pending. */
  _stage(ctx, n, d, periods) {
    const norm = normalizePeriods(periods);
    const stored = storedPeriods(this._program(ctx, n), d);
    let m = this.pending.get(n);
    if (stored && samePeriods(stored, norm)) {
      if (m) {
        m.delete(d);
        if (!m.size) this.pending.delete(n);
      }
      return;
    }
    if (!m) {
      m = new Map();
      this.pending.set(n, m);
    }
    m.set(d, norm);
  }

  _days() {
    return [...this.sel].sort((a, b) => a - b);
  }

  /** The periods the editor shows: the first selected day's. */
  _template(ctx) {
    const days = this._days();
    return days.length ? (this._periods(ctx, this.tab, days[0]) || []).map((p) => [...p]) : [];
  }

  _setTemplate(ctx, periods) {
    for (const d of this.sel) this._stage(ctx, this.tab, d, periods);
    this.inputError = null;
    this.card.requestRender();
  }

  _tooMany(ctx, n, periods) {
    const p = this._program(ctx, n);
    return pointsFor(periods, p ? p.default : 0).length > SLOTS;
  }

  // --- events -------------------------------------------------------------

  bind(el) {
    el.addEventListener("click", (e) => {
      const t = e.target.closest("[data-act]");
      if (!t || t.disabled || t.tagName === "SELECT" || t.tagName === "INPUT") return;
      this._act(t.getAttribute("data-act"), t.dataset, e);
    });
    el.addEventListener("change", (e) => {
      const t = e.target;
      if (t.matches && t.matches("input[data-act=tptime]")) this._time(t);
      else if (t.matches && t.matches("select[data-act=tpcopyfrom]")) this._copyFrom(t);
    });
    el.addEventListener("keydown", (e) => this._key(e));
    el.addEventListener("pointerdown", (e) => this._down(e));
    el.addEventListener("pointermove", (e) => this._move(e));
    el.addEventListener("pointerup", () => this._up());
    el.addEventListener("pointercancel", () => this._up());
  }

  /** After a render: show the template in inputs the user is not typing in. */
  attach(el) {
    for (const input of el.querySelectorAll("input[data-act=tptime]")) {
      if (input !== input.getRootNode().activeElement && input.value !== input.getAttribute("value")) input.value = input.getAttribute("value");
    }
    for (const sel of el.querySelectorAll("select[data-act=tpcopyfrom]")) sel.value = "";
  }

  _act(act, d, e) {
    const ctx = this._ctx();
    if (!ctx) return;
    const n = this.tab;
    const I = ctx.I;
    switch (act) {
      case "tptab":
        this.select(Number(d.c));
        break;
      case "tprow": {
        const day = Number(d.d);
        if (e && (e.shiftKey || e.ctrlKey || e.metaKey)) {
          if (this.sel.has(day)) this.sel.delete(day);
          else this.sel.add(day);
        } else if (this.sel.size === 1 && this.sel.has(day)) {
          this.sel.clear();
        } else {
          this.sel = new Set([day]);
        }
        this.inputError = null;
        break;
      }
      case "tpday": {
        if (!this._editable(ctx, n)) return;
        const day = Number(d.d);
        if (this.sel.has(day)) {
          if (this.sel.size > 1) this.sel.delete(day);
        } else {
          // A day joining the selection gets the times shown.
          const tpl = this._template(ctx);
          this.sel.add(day);
          this._stage(ctx, n, day, tpl);
        }
        break;
      }
      case "tpdel": {
        const tpl = this._template(ctx);
        tpl.splice(Number(d.i), 1);
        this._setTemplate(ctx, tpl);
        return;
      }
      case "tpadd": {
        const tpl = this._template(ctx);
        const p = suggestPeriod(tpl);
        if (!p || this._tooMany(ctx, n, [...tpl, p])) return;
        this._setTemplate(ctx, [...tpl, p]);
        return;
      }
      case "tpall":
        this._setTemplate(ctx, [[0, DAY]]);
        return;
      case "tpdone":
        this.sel.clear();
        this.inputError = null;
        break;
      case "tpown":
        this.sel = new Set([0, 1, 2, 3, 4, 5, 6]);
        break;
      case "tppreset": {
        const week = (PRESETS[d.p] || (() => null))();
        if (!week || !this._editable(ctx, n)) return;
        week.forEach((periods, day) => this._stage(ctx, n, day, periods));
        this.sel.clear();
        break;
      }
      case "tpcomfort": {
        const c = ctx.sec.circuits.find((x) => x.index === Number(d.c));
        const climate = c && ofDomain(c.climate, "climate");
        if (!climate || ctx.disabled) return;
        this.card.call("climate", "set_preset_mode", { entity_id: climate, preset_mode: "comfort" }).catch((err) => {
          this._setNote("err", `${I.t("error")}: ${errText(err)}`);
        });
        return;
      }
      case "tpcopy":
        this.copyOpen = !this.copyOpen;
        this.copyTo.clear();
        break;
      case "tpcopytarget": {
        const t = Number(d.c);
        if (this.copyTo.has(t)) this.copyTo.delete(t);
        else this.copyTo.add(t);
        break;
      }
      case "tpcopygo": {
        for (const t of this.copyTo) {
          if (!this._editable(ctx, t)) continue;
          for (let day = 0; day < 7; day++) {
            const ps = this._periods(ctx, n, day);
            if (ps) this._stage(ctx, t, day, ps);
          }
        }
        this.copyOpen = false;
        this.copyTo.clear();
        break;
      }
      case "tpdiscard":
        this.pending.clear();
        this.inputError = null;
        this.note = null;
        break;
      case "tpapply":
        this._apply();
        return;
      default:
        return;
    }
    this.card.requestRender();
  }

  _time(input) {
    const ctx = this._ctx();
    if (!ctx || !this._editable(ctx, this.tab)) return;
    const i = Number(input.dataset.i);
    const edge = input.dataset.edge;
    const v = parseHm(input.value, edge === "e");
    const tpl = this._template(ctx);
    if (v === null || !tpl[i]) return;
    tpl[i][edge === "s" ? 0 : 1] = v;
    if (tpl[i][0] >= tpl[i][1]) {
      this.inputError = ctx.I.t("tp_invalid_time");
      this.card.requestRender();
      return;
    }
    if (this._tooMany(ctx, this.tab, tpl)) {
      this.inputError = ctx.I.t("tp_too_many");
      this.card.requestRender();
      return;
    }
    this._setTemplate(ctx, tpl);
  }

  _copyFrom(select) {
    const ctx = this._ctx();
    if (!ctx || select.value === "") return;
    const ps = this._periods(ctx, this.tab, Number(select.value));
    if (ps) this._setTemplate(ctx, ps);
  }

  /** New position of a period edge, kept inside its neighbours (never touching them). */
  _moved(tpl, i, edge, minute) {
    const p = tpl[i];
    if (!p) return null;
    if (edge === "s") p[0] = clampTo(minute, i > 0 ? tpl[i - 1][1] + STEP : 0, p[1] - STEP);
    else p[1] = clampTo(minute, p[0] + STEP, i < tpl.length - 1 ? tpl[i + 1][0] - STEP : DAY);
    return tpl;
  }

  _down(e) {
    const h = e.target.closest && e.target.closest(".hdl");
    const ctx = this._ctx();
    if (!h || !ctx || !this._editable(ctx, this.tab)) return;
    e.preventDefault();
    this.drag = { i: Number(h.dataset.i), edge: h.dataset.edge, bar: h.closest(".bar"), id: e.pointerId };
    try {
      h.setPointerCapture(e.pointerId);
    } catch (_e) { /* synthetic events */ }
  }

  _move(e) {
    const dr = this.drag;
    const ctx = this._ctx();
    if (!dr || !ctx || (dr.id !== undefined && e.pointerId !== dr.id)) return;
    const rect = dr.bar.getBoundingClientRect();
    if (!rect.width) return;
    const minute = Math.round(((e.clientX - rect.left) / rect.width) * DAY / STEP) * STEP;
    const tpl = this._moved(this._template(ctx), dr.i, dr.edge, minute);
    if (tpl) this._setTemplate(ctx, tpl);
  }

  _up() {
    if (!this.drag) return;
    this.drag = null;
    this.card.requestRender();
  }

  _key(e) {
    const t = e.target;
    const ctx = this._ctx();
    if (!ctx) return;
    if (t.classList && t.classList.contains("hdl") && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
      if (!this._editable(ctx, this.tab)) return;
      e.preventDefault();
      const i = Number(t.dataset.i), edge = t.dataset.edge;
      const tpl = this._template(ctx);
      const cur = tpl[i] ? tpl[i][edge === "s" ? 0 : 1] : null;
      if (cur === null) return;
      const step = (e.shiftKey ? 60 : STEP) * (e.key === "ArrowLeft" ? -1 : 1);
      const moved = this._moved(tpl, i, edge, cur + step);
      if (moved) this._setTemplate(ctx, moved);
      return;
    }
    if (t.getAttribute && t.getAttribute("data-act") === "tprow" && (e.key === "Enter" || e.key === " ")) {
      e.preventDefault();
      this._act("tprow", t.dataset, e);
    }
  }

  _setNote(cls, text, ms = 0) {
    this.note = { cls, text, until: ms ? Date.now() + ms : 0 };
    if (ms) this._later(ms + 20, () => this.card.requestRender());
    this.card.requestRender();
  }

  /** Write every circuit's pending days (one request per circuit). */
  async _apply() {
    const ctx = this._ctx();
    const store = this.card.store;
    if (!ctx || !store || this.applying || !this.pending.size) return;
    const I = ctx.I;
    for (const [n, days] of this.pending) {
      for (const periods of days.values()) {
        if (this._tooMany(ctx, n, periods)) return this._setNote("err", I.t("tp_too_many"));
      }
    }
    this.applying = true;
    this.note = null;
    this.card.requestRender();
    let writes = 0;
    try {
      for (const [n, days] of [...this.pending]) {
        const prog = this._program(ctx, n);
        const payload = {};
        for (const [d, periods] of days) payload[d] = pointsFor(periods, prog ? prog.default : 0);
        const res = await store.saveSchedule(this.plantId, n, payload);
        if (this.disposed) return;
        this.pending.delete(n);
        writes += Number(res && res.writes) || 0;
      }
      this.sel.clear();
      this.applying = false;
      this._setNote("ok", `✓ ${I.t(writes === 1 ? "tp_saved_one" : "tp_saved", { n: writes })}`, NOTE_MS);
    } catch (err) {
      if (this.disposed) return;
      this.applying = false;
      this._setNote("err", I.t("apply_error", { msg: errText(err) }));
    }
  }

  // --- rendering ----------------------------------------------------------

  /** Normal and Night setpoints of the circuit's season (for the legend and texts). */
  _setpoints(ctx, c) {
    const rows = setpointRows(ctx.plant, c);
    const cool = rows.some((r) => r.cool) && (!rows.some((r) => !r.cool) || targetIsCool(ctx.live, c.index));
    const pick = (reduced) => {
      const r = rows.find((x) => x.cool === cool && x.reduced === reduced);
      return r ? num(ctx.live, hcKey(c.index, r.key)) : undefined;
    };
    return { cool, normal: pick(false), night: pick(true) };
  }

  _banner(ctx, c, sp) {
    const { I } = ctx;
    const ps = presetState(ctx, c);
    const name = circuitName(c, ctx.live);
    const raw = num(ctx.live, hcKey(c.index, "op_program"));
    let text = null;
    if (ps === "eco") text = I.t("tp_banner_night", { mode: I.t("eco"), name, t: I.temp(sp.night) });
    else if (ps === "off") text = I.t("tp_banner_off", { name });
    else if (ps === "program") {
      const label = programText(ctx, c);
      if (raw === 2 || raw === 6) text = I.t("tp_banner_night", { mode: label, name, t: I.temp(sp.night) });
      else if (raw === 3) text = I.t("tp_banner_normal", { mode: label, name, t: I.temp(sp.normal) });
      else text = I.t("tp_banner_other", { mode: label });
    }
    if (!text) return "";
    const btn = ps !== "off" && ofDomain(c.climate, "climate") && !ctx.disabled
      ? `<button class="btn sm" data-act="tpcomfort" data-c="${c.index}">${esc(I.t("tp_to_comfort"))}</button>`
      : "";
    return `<div class="tpbanner" role="status"><span>${moonSvg("var(--ohp-night-ink)")} ${esc(text)}</span>${btn}</div>`;
  }

  _empty(ctx, c, sp, editable) {
    const { I } = ctx;
    const btns = editable
      ? `<div class="quick center"><button class="btn sm" data-act="tppreset" data-p="night">${esc(I.t("tp_preset_night"))}</button>
          <button class="btn sm" data-act="tppreset" data-p="workdays">${esc(I.t("tp_preset_workdays"))}</button>
          <button class="btn sm" data-act="tpown">${esc(I.t("tp_preset_own"))}</button></div>`
      : "";
    return `<div class="tpempty">${clockSvg("var(--ohp-ink2)", 26)}<div class="big">${esc(I.t("tp_no_setback"))}</div>
      <div class="sub">${esc(I.t("tp_empty_sub", { name: circuitName(c, ctx.live), t: I.temp(sp.normal) }))}</div>${btns}</div>`;
  }

  _week(ctx, c, editable) {
    const { I } = ctx;
    const n = c.index;
    const compact = ctx.width < 520;
    const names = dayNames(I.lang, "short");
    const lw = compact ? 30 : 40;
    const barPx = Math.max(100, ctx.width - lw - 8);
    const now = haNow(ctx.hass);
    const tpl = this.sel.size ? this._template(ctx) : null;
    const ticks = compact ? [0, 6, 12, 18, 24] : [0, 3, 6, 9, 12, 15, 18, 21, 24];
    let h = `<div class="week" style="--lw:${lw}px"><div class="axis" aria-hidden="true">${ticks.map((t) => `<span style="left:${(t / 24) * 100}%">${String(t).padStart(2, "0")}</span>`).join("")}</div><div class="rows">`;
    for (let d = 0; d < 7; d++) {
      const periods = this._periods(ctx, n, d);
      const pend = !!(this.pending.get(n) && this.pending.get(n).has(d));
      const sel = this.sel.has(d);
      // handles only where the row shows exactly what the editor edits
      const handles = sel && editable && tpl && periods && samePeriods(periods, tpl);
      let blocks = "";
      if (periods === null) {
        blocks = `<span class="unread">${esc(I.t("tp_unreadable"))}</span>`;
      } else {
        periods.forEach(([s, e], i) => {
          const label = ((e - s) / DAY) * barPx >= LABEL_PX ? `${hm(s)}–${hm(e)}` : "";
          blocks += `<span class="blk${pend ? " pend" : ""}" style="left:${pct(s)}%;width:${pct(e - s)}%" title="${esc(`${hm(s)}–${hm(e)}`)}">${esc(label)}</span>`;
          if (handles) {
            for (const [edge, m] of [["s", s], ["e", e]]) {
              blocks += `<span class="hdl" data-key="h${d}-${i}${edge}" data-i="${i}" data-edge="${edge}" tabindex="0" role="slider" aria-label="${esc(`${I.t("tp_period", { n: i + 1 })} ${I.t(edge === "s" ? "tp_start" : "tp_end")}`)}" aria-valuetext="${hm(m)}" style="left:${pct(m)}%"></span>`;
            }
          }
        });
      }
      h += `<div class="drow${d === now.day ? " today" : ""}${sel ? " sel" : ""}" data-key="d${d}" data-act="tprow" data-d="${d}" role="button" tabindex="0" aria-pressed="${sel}">
        <span class="dl">${esc(names[d])}</span><div class="bar">${blocks}</div></div>`;
    }
    h += `<span class="nowline" data-t="${hm(now.minute)}" style="left:calc(var(--lw) + (100% - var(--lw)) * ${(now.minute / DAY).toFixed(4)} - 1px)"></span></div></div>`;
    return h;
  }

  _editor(ctx, c, sp, editable) {
    const { I } = ctx;
    const n = c.index;
    const days = this._days();
    const long = dayNames(I.lang, "long");
    const short = dayNames(I.lang, "short");
    const tpl = this._template(ctx);
    const differ = days.some((d) => !samePeriods(this._periods(ctx, n, d) || [], tpl));
    const prog = this._program(ctx, n);
    const used = pointsFor(tpl, prog ? prog.default : 0).length;
    const dis = editable ? "" : " disabled";
    const label = days.length === 5 && days.join() === "0,1,2,3,4" ? `${long[0]} – ${long[4]}` : days.length === 7 ? I.t("tp_every_day") : days.map((d) => long[d]).join(", ");
    let h = `<div class="tpeditor"><div class="eh"><div class="ttl">${esc(label)}</div><button class="btn sm" data-act="tpdone">${esc(I.t("tp_done"))}</button></div>
      <div class="days" role="group" aria-label="${esc(I.t("tp_edit_together"))}">${short.map((x, i) => `<button class="${this.sel.has(i) ? "on" : ""}" data-act="tpday" data-d="${i}" aria-pressed="${this.sel.has(i)}"${dis}>${esc(x)}</button>`).join("")}</div>`;
    if (differ) h += `<div class="hint">${esc(I.t("tp_days_differ"))}</div>`;
    tpl.forEach(([s, e], i) => {
      h += `<div class="period"><span class="pn"><i></i>${esc(I.t("tp_period", { n: i + 1 }))}</span>
        <input type="time" step="60" data-act="tptime" data-i="${i}" data-edge="s" value="${hm(s)}" aria-label="${esc(`${I.t("tp_period", { n: i + 1 })} ${I.t("tp_start")}`)}"${dis}>
        <span class="sub">${esc(I.t("tp_to"))}</span>
        <input type="time" step="60" data-act="tptime" data-i="${i}" data-edge="e" value="${e >= DAY ? "00:00" : hm(e)}" aria-label="${esc(`${I.t("tp_period", { n: i + 1 })} ${I.t("tp_end")}`)}"${dis}>
        <button class="x" data-act="tpdel" data-i="${i}" aria-label="${esc(I.t("tp_remove"))}" title="${esc(I.t("tp_remove"))}"${dis}>✕</button></div>`;
    });
    if (!tpl.length) h += `<div class="hint">${esc(I.t("tp_night_all_day_hint", { t: I.temp(sp.night) }))}</div>`;
    const next = suggestPeriod(tpl);
    const canAdd = editable && next && !this._tooMany(ctx, n, [...tpl, next]);
    h += `<div class="addrow"><button class="addp" data-act="tpadd"${canAdd ? "" : " disabled"}>+ ${esc(I.t("tp_add"))}</button><span class="hint">${esc(I.t("tp_points", { used, max: SLOTS }))}</span></div>`;
    if (this.inputError) h += `<div class="note err" role="alert">${esc(this.inputError)}</div>`;
    h += `<div class="hint">${esc(I.t("tp_outside", { t: I.temp(sp.night) }))} ${esc(I.t("tp_midnight"))}</div>`;
    const others = [0, 1, 2, 3, 4, 5, 6].filter((d) => !this.sel.has(d));
    h += `<div class="quick"><button class="btn sm" data-act="tpall"${dis}>${esc(I.t("tp_all_normal"))}</button>
      <select data-act="tpcopyfrom" aria-label="${esc(I.t("tp_copy_from"))}"${dis}><option value="">${esc(I.t("tp_copy_from"))}</option>${others.map((d) => `<option value="${d}">${esc(long[d])}</option>`).join("")}</select></div></div>`;
    return h;
  }

  _pendbar(ctx, circuits) {
    const { I } = ctx;
    if (!this.pending.size) return "";
    const short = dayNames(I.lang, "short");
    let writes = 0, bad = false;
    const lines = [];
    for (const c of circuits) {
      const days = this.pending.get(c.index);
      if (!days) continue;
      const prog = this._program(ctx, c.index);
      const groups = new Map(); // periods text → days
      for (const [d, periods] of [...days].sort((a, b) => a[0] - b[0])) {
        const points = pointsFor(periods, prog ? prog.default : 0);
        if (points.length > SLOTS) bad = true;
        writes += writeCount(prog && prog.days ? prog.days[d] : null, points);
        const text = periods.length ? periods.map(([s, e]) => `${hm(s)}–${hm(e)}`).join(", ") : I.t("tp_night_all_day");
        if (!groups.has(text)) groups.set(text, []);
        groups.get(text).push(short[d]);
      }
      for (const [text, ds] of groups) lines.push(`<li><b>${esc(circuitName(c, ctx.live))}</b> · ${esc(ds.join(", "))}: ${esc(text)}</li>`);
    }
    const busy = this.applying;
    return `<div class="pendbar" role="status"><div class="t">${esc(I.t("tp_pending"))}</div><ul>${lines.join("")}</ul>
      ${bad ? `<div class="note err">${esc(I.t("tp_too_many"))}</div>` : ""}
      <div class="row"><span class="cost">${esc(busy ? I.t("tp_saving") : I.t(writes === 1 ? "tp_writes_one" : "tp_writes", { n: writes }))}</span>
      <div class="btns"><button class="btn" data-act="tpdiscard"${busy ? " disabled" : ""}>${esc(I.t("discard"))}</button>
      <button class="btn primary" data-act="tpapply"${busy || bad || ctx.disabled ? " disabled" : ""}>${esc(I.t("apply"))}</button></div></div></div>`;
  }

  render(ctx) {
    const { I } = ctx;
    const head = (extra = "") => `<div class="hdr"><div><div class="ttl">${esc(I.t("tp_title"))}</div><div class="sub">${esc(I.t("tp_sub"))}</div></div>${extra}</div>`;
    const s = ctx.schedules;
    if (!s || !s.circuits) {
      if (s && s.error) return head() + `<div class="empty">${esc(I.t("tp_error"))}</div>`;
      return head() + `<div class="empty">${esc(I.t("tp_loading"))}</div>`;
    }
    const circuits = this._circuits(ctx);
    if (!circuits.length) return "";
    for (const n of [...this.pending.keys()]) if (!circuits.some((c) => c.index === n)) this.pending.delete(n);
    if (!circuits.some((c) => c.index === this.tab)) this.select(circuits[0].index);
    const c = circuits.find((x) => x.index === this.tab);
    const n = c.index;
    const prog = this._program(ctx, n);
    const editable = this._editable(ctx, n);
    const sp = this._setpoints(ctx, c);
    const targets = circuits.filter((x) => x.index !== n && this._editable(ctx, x.index));
    const copyBtn = targets.length && editable ? `<button class="btn sm" data-act="tpcopy" aria-expanded="${this.copyOpen}">${esc(I.t("tp_copy_to"))}</button>` : "";
    let h = head(copyBtn) + ctx.banner;
    if (this.copyOpen && targets.length) {
      h += `<div class="copyrow"><span class="sub">${esc(I.t("tp_copy_week"))}</span>${targets.map((x) => `<button class="mc${this.copyTo.has(x.index) ? " on sel" : ""}" data-act="tpcopytarget" data-c="${x.index}" aria-pressed="${this.copyTo.has(x.index)}">${esc(circuitName(x, ctx.live))}</button>`).join("")}
        <button class="btn sm primary" data-act="tpcopygo"${this.copyTo.size ? "" : " disabled"}>${esc(I.t("tp_copy"))}</button></div>`;
    }
    if (circuits.length > 1) {
      h += `<div class="tabs" role="tablist">${circuits.map((x) => `<button role="tab" data-act="tptab" data-c="${x.index}" class="${x.index === n ? "on" : ""}" aria-selected="${x.index === n}">${esc(circuitName(x, ctx.live))}${this.pending.has(x.index) ? '<span class="pdot" aria-hidden="true"></span>' : ""}</button>`).join("")}</div>`;
    }
    h += this._banner(ctx, c, sp);
    if (!prog.complete) h += `<div class="banner">${esc(I.t("tp_incomplete"))}</div>`;
    else if (prog.writable === false) h += `<div class="note">${esc(I.t("tp_readonly"))}</div>`;
    const nothing = !hasNightSetback(prog) && !this.pending.has(n) && !this.sel.size;
    if (nothing) {
      h += this._empty(ctx, c, sp, editable);
    } else {
      const col = sp.cool ? "var(--ohp-cool)" : "var(--ohp-heat)";
      h += `<div class="legend" style="--tp-normal:${col}"><span><i style="background:${col}"></i>${esc(I.t("tp_normal"))} <b>${I.temp(sp.normal)}</b></span>
        <span><i style="background:var(--ohp-night)"></i>${esc(I.t("tp_night"))} <b>${I.temp(sp.night)}</b></span>
        <span><i style="background:var(--ohp-ink);width:2px;height:11px;border-radius:1px;vertical-align:-1px"></i>${esc(I.t("tp_now"))}</span>
        ${this.sel.size && editable ? `<span class="dh">${esc(I.t("tp_drag_hint"))}</span>` : !this.sel.size && editable ? `<span class="dh">${esc(I.t("tp_tap_hint"))}</span>` : ""}</div>`;
      h += `<div style="--tp-normal:${col}">${this._week(ctx, c, editable)}</div>`;
      if (this.sel.size) h += this._editor(ctx, c, sp, editable);
    }
    h += this._pendbar(ctx, circuits);
    const note = this.note;
    if (note && (!note.until || note.until > Date.now())) h += `<div class="note ${note.cls}" role="status">${esc(note.text)}</div>`;
    else this.note = null;
    return h;
  }
}
