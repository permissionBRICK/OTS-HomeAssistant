// Card B: controls, mirroring the Ochsner app (heat-pump program, circuit
// setpoints and modes, hot water).

import { esc } from "../lib/dom.js";
import { modeColor } from "../lib/colors.js";
import { Coalescer } from "../lib/coalesce.js";
import { boltSvg, heroSvg, iconSvg, leafSvg, showerSvg } from "../lib/icons.js";
import { badgeFor, circuitKind, circuitName, convertDelta, convertTemp, displayEntity, enumLabel, haTempUnit, hcKey, num, ofDomain, val, writableEntity } from "../lib/model.js";
import { poolEntity, poolText, programText, statusText } from "./plant-view.js";

const OPT_TIMEOUT = 12000; // optimistic chip state if HA never confirms
const ARM_TIME = 2500;

const errText = (err) => (err && (err.message || err.code)) || String(err);

/** Temperature in `unit` (HA's unit for climate/water_heater values; "°F" spelled out). */
const tempIn = (I, v, unit, d) => I.temp(v, d, unit);

/**
 * Current circuit mode for the Komfort | Eco | Aus chips; "program" while a
 * legacy operating program (Reduziert, Normalbetrieb, …) runs instead.
 */
export function presetState(ctx, c) {
  const st = c.climate ? ctx.state(c.climate) : null;
  const prog = val(ctx.live, hcKey(c.index, "op_program"));
  if ((st && st.state === "off") || (!st && Number(prog) === 1)) return "off";
  if (programText(ctx, c)) return "program";
  const pm = st && st.attributes && st.attributes.preset_mode;
  if (pm === "comfort" || pm === "eco") return pm;
  const ce = val(ctx.live, hcKey(c.index, "comfort_eco"));
  if (ce !== undefined) return Number(ce) === 1 ? "eco" : "comfort";
  return null;
}

/** Circuit target and bounds: from the climate entity, else read-only from the feed. */
export function circuitTarget(ctx, c) {
  const st = c.climate ? ctx.state(c.climate) : null;
  if (st && st.attributes && typeof st.attributes.temperature === "number") {
    const a = st.attributes;
    return {
      value: a.temperature,
      min: typeof a.min_temp === "number" ? a.min_temp : a.temperature - 3,
      max: typeof a.max_temp === "number" ? a.max_temp : a.temperature + 3,
      step: a.target_temp_step || 0.5,
      writable: st.state !== "unavailable",
      climate: true,
    };
  }
  const n = c.index;
  const sel = num(ctx.live, hcKey(n, "target_selector"));
  const key = sel === 1 || sel === 3 ? "room_target_cool" : "room_target_heat";
  const v = num(ctx.live, hcKey(n, key)) ?? num(ctx.live, hcKey(n, "room_target_current"));
  return { value: v, min: v, max: v, step: 0.5, writable: false };
}

export class ControlView {
  constructor(card) {
    this.card = card;
    this.coal = new Map();
    this.busy = new Map();
    this.notes = new Map();
    this.armed = null;
    this._timers = new Set();
  }

  dispose() {
    this.disposed = true;
    this.pause();
  }

  /** Card left the page: drop queued writes and every timer with its transient state. */
  pause() {
    this.cancelWrites();
    for (const t of this._timers) clearTimeout(t);
    this._timers.clear();
    this.busy.clear();
    this.armed = null;
  }

  /** Drop every queued (not yet sent) stepper write. */
  cancelWrites() {
    for (const c of this.coal.values()) c.dispose();
    this.coal.clear();
  }

  /** Timer owned by the view; none is started once it is disposed or off the page. */
  _later(ms, fn) {
    if (this.disposed || !this.card.isConnected) return;
    const id = setTimeout(() => {
      this._timers.delete(id);
      fn();
    }, ms);
    this._timers.add(id);
  }

  bind(el) {
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-act]");
      if (!b || b.disabled) return;
      this._act(b.getAttribute("data-act"), b.dataset);
    });
  }

  _note(key, cls, text, ms = 0) {
    const until = ms ? Date.now() + ms : 0;
    this.notes.set(key, { cls, text, until });
    if (ms) this._later(ms + 20, () => this.card.requestRender());
  }

  /**
   * Entity a stepper key ("hc<n>" or "dhw") may write to in `ctx` right now:
   * its section is shown, the controller is available and the entity is of
   * the right domain and writable. null = no write allowed.
   */
  _target(ctx, key) {
    if (!ctx || ctx.disabled) return null;
    if (key === "dhw") {
      if (!ctx.sec.dhw) return null;
      const wh = ofDomain(ctx.plant.dhw && ctx.plant.dhw.water_heater, "water_heater");
      const st = wh && ctx.state(wh);
      return st && st.state !== "unavailable" && st.attributes && typeof st.attributes.temperature === "number" ? wh : null;
    }
    const c = ctx.sec.circuits.find((x) => `hc${x.index}` === key);
    if (!c || !ofDomain(c.climate, "climate")) return null;
    return circuitTarget(ctx, c).writable ? c.climate : null;
  }

  /** The stepper binding still holds as of now (fresh store/hass, not the last render). */
  _bindingHolds(key, entityId, conn) {
    if (this.disposed) return false;
    const snap = this.card.snapshot();
    if (!snap || snap.hass.connection !== conn || snap.plant.plant_id !== this.plantId) return false;
    const ctx = { ...snap, disabled: !snap.available, state: (eid) => snap.hass.states[eid] };
    return this._target(ctx, key) === entityId;
  }

  /**
   * Render/refetch: drop queued values whose section disappeared, whose
   * entity mapping changed or whose control became read-only/unavailable.
   */
  _reconcile(ctx) {
    for (const [key, c] of [...this.coal]) {
      if (c.conn === ctx.hass.connection && this._target(ctx, key) === c.entityId) continue;
      if (c.state === "waiting") this._note(key, "info", ctx.I.t("dropped_gone"), 6000);
      c.dispose();
      this.coal.delete(key);
    }
  }

  /**
   * Coalescer for a stepper (circuit room target or DHW target), bound to the
   * entity the user acted on: a different entity for the same key (payload
   * changed) gets a fresh coalescer and the old queued value is dropped.
   */
  _coal(key, entityId, domain) {
    let c = this.coal.get(key);
    const conn = this.card.hass && this.card.hass.connection;
    if (c && c.entityId === entityId && c.conn === conn) return c;
    if (c) c.dispose();
    let lastState = "idle";
    c = new Coalescer({
      delay: this.card.writeDelay,
      // Re-checked against the newest store/hass right before the write.
      valid: () => this._bindingHolds(key, entityId, conn),
      send: (v) => this.card.call(domain, "set_temperature", { entity_id: entityId, temperature: v }),
      current: () => {
        const st = this.card.stateOf(entityId);
        return st && st.attributes ? st.attributes.temperature : undefined;
      },
      // The target belongs to the active mode (hvac mode + preset, or the
      // water heater's operation mode): another mode has another setpoint.
      context: () => {
        const st = this.card.stateOf(entityId);
        if (!st) return "";
        return domain === "climate" ? `${st.state}|${(st.attributes && st.attributes.preset_mode) || ""}` : st.state;
      },
      onChange: () => {
        const I = this.card.i18n;
        if (c.state === "error") this._note(key, "err", `${I.t("error")}: ${c.error}`, 8000);
        else if (c.state === "dropped") this._note(key, "info", I.t(c.reason === "invalid" ? "dropped_gone" : "dropped_ctx"), 6000);
        else if (lastState === "sending" && (c.state === "sent" || c.state === "idle")) this._note(key, "ok", "✓ " + I.t("saved"), 1800);
        else if (c.state === "waiting" || c.state === "sending") this.notes.delete(key);
        lastState = c.state;
        this.card.requestRender();
      },
    });
    c.entityId = entityId;
    c.conn = conn;
    this.coal.set(key, c);
    return c;
  }

  /**
   * A mode/preset/season/on-off change re-targets the section's setpoint:
   * drop a queued (not yet sent) temperature edit instead of letting it land
   * on the new mode's setpoint.
   */
  _dropQueued(key) {
    const c = this.coal.get(key);
    if (c && c.state === "waiting") c.cancel();
  }

  async _service(key, target, noteKey, domain, service, data) {
    this.busy.set(key, { target, at: Date.now() });
    this.notes.delete(noteKey);
    this.card.requestRender();
    try {
      await this.card.call(domain, service, data);
      if (this.disposed) return;
      const b = this.busy.get(key);
      if (b && b.target === target) {
        b.sent = true;
        this._later(OPT_TIMEOUT, () => {
          const cur = this.busy.get(key);
          if (cur && cur.target === target) {
            this.busy.delete(key);
            this.card.requestRender();
          }
        });
      }
    } catch (err) {
      if (this.disposed) return;
      this.busy.delete(key);
      this._note(noteKey, "err", `${this.card.i18n.t("error")}: ${errText(err)}`, 8000);
    }
    this.card.requestRender();
  }

  _act(act, d) {
    const ctx = this.card.ctx;
    if (!ctx || ctx.disabled || ctx.plant.plant_id !== this.plantId) return;
    const { plant } = ctx;
    const found = d.c ? ctx.sec.circuits.find((x) => String(x.index) === d.c) : null;
    // Only ever call a domain's services on an entity of that domain.
    const circuit = found && ofDomain(found.climate, "climate") ? found : null;
    const wh = ofDomain(plant.dhw && plant.dhw.water_heater, "water_heater");
    switch (act) {
      case "hp": {
        const ent = writableEntity(plant, "hp.program", "select");
        const opt = plant.roles["hp.program"].options || {};
        if (!ent || opt[d.v] === undefined) return;
        if (d.v === "3" && !(this.armed && this.armed > Date.now())) {
          this.armed = Date.now() + ARM_TIME;
          this._later(ARM_TIME + 20, () => this.card.requestRender());
          this.card.requestRender();
          return;
        }
        this.armed = null;
        this._service("hp", Number(d.v), "hp", "select", "select_option", { entity_id: ent, option: opt[d.v] });
        return;
      }
      case "step": {
        if (!circuit || this._target(ctx, `hc${circuit.index}`) !== circuit.climate) return;
        const tg = circuitTarget(ctx, circuit);
        const c = this._coal(`hc${circuit.index}`, circuit.climate, "climate");
        const base = c.display();
        const next = Math.min(tg.max, Math.max(tg.min, Math.round(((base ?? tg.value) + Number(d.d) * tg.step) / tg.step) * tg.step));
        c.push(Number(next.toFixed(2)));
        return;
      }
      case "preset": {
        if (!circuit || !circuit.climate) return;
        this._dropQueued(`hc${circuit.index}`);
        const key = `p${circuit.index}`;
        if (d.v === "off") this._service(key, "off", `hc${circuit.index}`, "climate", "set_hvac_mode", { entity_id: circuit.climate, hvac_mode: "off" });
        else this._service(key, d.v, `hc${circuit.index}`, "climate", "set_preset_mode", { entity_id: circuit.climate, preset_mode: d.v });
        return;
      }
      case "season": {
        if (!circuit || !circuit.climate) return;
        this._dropQueued(`hc${circuit.index}`);
        this._service(`s${circuit.index}`, d.v, `hc${circuit.index}`, "climate", "set_hvac_mode", { entity_id: circuit.climate, hvac_mode: d.v });
        return;
      }
      case "boost": {
        const btn = ofDomain(plant.dhw && plant.dhw.boost_button, "button");
        if (!btn) return;
        this._dropQueued("dhw");
        this._service("boost", 1, "dhw", "button", "press", { entity_id: btn });
        return;
      }
      case "dhwstep": {
        if (!wh || this._target(ctx, "dhw") !== wh) return;
        const a = ctx.state(wh).attributes;
        const step = a.target_temp_step || 0.5;
        const c = this._coal("dhw", wh, "water_heater");
        const base = c.display() ?? a.temperature;
        const tu = haTempUnit(ctx.hass);
        const lo = typeof a.min_temp === "number" ? a.min_temp : convertTemp(5, "°C", tu);
        const hi = typeof a.max_temp === "number" ? a.max_temp : convertTemp(75, "°C", tu);
        c.push(Number(Math.min(hi, Math.max(lo, Math.round((base + Number(d.d) * step) / step) * step)).toFixed(2)));
        return;
      }
      case "dhwmode": {
        if (!wh) return;
        this._dropQueued("dhw");
        this._service("dhwmode", d.v, "dhw", "water_heater", "set_operation_mode", { entity_id: wh, operation_mode: d.v });
        return;
      }
      default:
    }
  }

  _busyTarget(key, current) {
    const b = this.busy.get(key);
    if (!b) return undefined;
    if (b.sent && current === b.target) {
      this.busy.delete(key);
      return undefined;
    }
    return b.target;
  }

  _noteHtml(key) {
    const c = this.coal.get(key);
    const I = this.card.i18n;
    if (c && c.state === "waiting") return `<div class="note wait">⏳ ${esc(I.t("pending_wait"))}</div>`;
    if (c && c.state === "sending") return `<div class="note wait">${esc(I.t("sending"))}</div>`;
    const n = this.notes.get(key);
    if (n && (!n.until || n.until > Date.now())) return `<div class="note ${n.cls}" role="status">${esc(n.text)}</div>`;
    if (n) this.notes.delete(key);
    return "";
  }

  _hero(ctx) {
    const { I, live, sec, mode } = ctx;
    const col = modeColor(mode);
    const parts = [];
    if (sec.buffer && sec.bufferTemp && num(live, "hp.buffer_top") !== undefined) parts.push(`${I.t("buffer")} ${I.temp(num(live, "hp.buffer_top"))}`);
    const f = num(live, "hp.flow"), r = num(live, "hp.return");
    if (f !== undefined || r !== undefined) parts.push(`${I.t("flow_short")} ${I.temp(f)} → ${I.t("return_short")} ${I.temp(r)}`);
    const out = num(live, "hp.outdoor");
    return `<div class="hdr"><div class="hero"><div class="heroicon" style="background:color-mix(in srgb, ${col} 18%, transparent)">${heroSvg(mode, col)}</div>
      <div style="min-width:0"><div class="ttl">${esc(statusText(ctx))}</div><div class="sub">${esc(parts.join(" · "))}</div></div></div>
      ${out !== undefined ? `<div class="kpi"><b>${I.num(out)}</b><small>°C</small></div>` : ""}</div>`;
  }

  _hpSection(ctx) {
    const { I, plant, live } = ctx;
    const r = plant.roles["hp.program"];
    if (!r) return "";
    const ent = writableEntity(plant, "hp.program", "select");
    const cur = num(live, "hp.program");
    const shown = this._busyTarget("hp", cur) ?? cur;
    const armed = this.armed && this.armed > Date.now();
    const dis = !ent || ctx.disabled ? " disabled" : "";
    const title = !ent ? ` title="${esc(I.t("entity_missing"))}"` : "";
    let segs = `<button data-act="hp" data-v="0" class="${shown === 0 ? "on" : ""}" aria-pressed="${shown === 0}"${dis}${title}>${esc(I.t("auto"))}</button>`;
    if (shown === 1 || shown === 2) {
      const lbl = enumLabel(plant, "hp.program", shown) || I.t("manual");
      segs += `<button class="on manual${shown === 2 ? " coolm" : ""}" aria-pressed="true" disabled>${esc(lbl)}</button>`;
    }
    segs += `<button data-act="hp" data-v="3" class="${shown === 3 ? "on off" : ""}${armed ? " armed" : ""}" aria-pressed="${shown === 3}"${dis}${title}>${esc(armed ? I.t("really_off") : I.t("off"))}</button>`;
    return `<div class="sect"><div class="lab">${esc(I.t("section_hp"))}</div><div class="seg" role="group" aria-label="${esc(I.t("section_hp"))}">${segs}</div>${this._noteHtml("hp")}</div>`;
  }

  _badge(ctx, c, name) {
    const b = badgeFor(ctx.plant, val(ctx.live, hcKey(c.index, "badge")));
    const bg = b.activity === "heating" ? "var(--ohp-heat)" : b.activity === "cooling" ? "var(--ohp-cool)" : b.activity === "fault" ? "var(--ohp-warn)" : "var(--ohp-pipe-off)";
    const coloured = b.activity !== "idle" && b.activity !== "off";
    const icon = b.kind === "off" ? "off" : b.kind === "frost" ? "frost" : b.kind === "vacation" ? "vacation" : b.kind === "party" ? "party" : b.kind === "fault" ? "fault" : circuitKind(name);
    const pst = presetState(ctx, c);
    const leaf = b.preset === "eco" || (!b.preset && pst === "eco") ? leafSvg() : "";
    return `<span class="badge" style="background:${bg}" title="${esc(b.name)}">${leaf}${iconSvg(icon, coloured ? "#fff" : "var(--ohp-ink)")}</span>`;
  }

  _statusLine(ctx, c) {
    const { I } = ctx;
    const b = badgeFor(ctx.plant, val(ctx.live, hcKey(c.index, "badge")));
    let first;
    switch (b.kind) {
      case "off": first = I.t("off"); break;
      case "frost": first = I.t("frost"); break;
      case "vacation": first = I.t("vacation"); break;
      case "party": first = I.t("party"); break;
      case "fault": first = I.t("fault"); break;
      default:
        first = b.activity === "heating" ? I.t("heating") : b.activity === "cooling" ? I.t("cooling") : I.t("ready");
    }
    const ps = presetState(ctx, c);
    const second = ps === "comfort" ? I.t("comfort") : ps === "eco" ? I.t("eco") : ps === "program" ? programText(ctx, c) : "";
    return second && ps !== "off" && b.kind !== "off" ? `${first} | ${second}` : first;
  }

  _circuit(ctx, c) {
    const { I, live } = ctx;
    const n = c.index;
    const name = circuitName(c, live);
    const tg = circuitTarget(ctx, c);
    const coal = this.coal.get(`hc${n}`);
    if (coal) coal.sync();
    const shown = coal && coal.value !== undefined ? coal.value : tg.value;
    const pend = coal && coal.value !== undefined;
    const off = num(live, hcKey(n, "room_offset"));
    // The stepper is in HA's unit (climate attributes), the offset in K.
    const tu = tg.climate ? haTempUnit(ctx.hass) : "°C";
    const shownOff = off !== undefined && pend && tg.value !== undefined ? off + convertDelta(shown - tg.value, tu, "K") : off;
    const fa = num(live, hcKey(n, "flow_actual"));
    const ft = num(live, hcKey(n, "flow_target"));
    const subParts = [];
    if (shownOff !== undefined) subParts.push(`${I.t("correction")} ${I.signed(shownOff)} K`);
    if (fa !== undefined || ft !== undefined) subParts.push(`${I.t("flow")} ${I.temp(fa)} / ${I.temp(ft)}`);
    const canStep = tg.writable && !ctx.disabled && !!c.climate;
    const roTitle = !canStep && !ctx.disabled ? ` title="${esc(I.t("entity_missing"))}"` : "";
    const minus = canStep && shown !== undefined && shown > tg.min + 1e-6 ? "" : " disabled";
    const plus = canStep && shown !== undefined && shown < tg.max - 1e-6 ? "" : " disabled";

    const ps = presetState(ctx, c);
    const pShown = this._busyTarget(`p${n}`, ps) ?? ps;
    const chipDis = !c.climate || ctx.disabled ? " disabled" : "";
    // Under a legacy program neither Komfort nor Eco is active; choosing one
    // switches the circuit to the app's Komfort/Eco control.
    const legacy = ps === "program" ? programText(ctx, c) : null;
    const chip = (v, label, cls) => {
      const tip = legacy && v !== "off" ? ` title="${esc(I.t("program_replace", { program: legacy, choice: label }))}"` : "";
      return `<button class="mc${pShown === v ? " on " + cls : ""}${this.busy.has(`p${n}`) && this.busy.get(`p${n}`).target === v ? " busy" : ""}" data-act="preset" data-c="${n}" data-v="${v}" aria-pressed="${pShown === v}"${tip}${chipDis}>${esc(label)}</button>`;
    };
    let chips = `<div class="modechips" role="group">${chip("comfort", I.t("comfort"), "")}${chip("eco", I.t("eco"), "eco")}${chip("off", I.t("off"), "offc")}</div>`;
    if (c.cool) {
      const st = c.climate ? ctx.state(c.climate) : null;
      const hv = st ? st.state : null;
      const sShown = this._busyTarget(`s${n}`, hv) ?? hv;
      const sc = (v, label, cls) =>
        `<button class="mc${sShown === v ? " on " + cls : ""}" data-act="season" data-c="${n}" data-v="${v}" aria-pressed="${sShown === v}"${chipDis}>${esc(label)}</button>`;
      chips += `<div class="modechips" role="group">${sc("heat", I.t("heating"), "")}${sc("heat_cool", I.t("auto"), "auto")}${sc("cool", I.t("cooling"), "cool")}</div>`;
    }
    const more = c.climate || displayEntity(ctx.plant, hcKey(n, "status"));
    return `<div class="hc" data-key="hc${n}"><div class="hcrow">
        <button class="hcname"${more ? ` data-more="${esc(more)}"` : ""}>${this._badge(ctx, c, name)}<div><span class="nm">${esc(name)}</span><div class="sub" style="font-weight:400">${esc(this._statusLine(ctx, c))}</div></div></button>
        <div class="stepper"${roTitle}><button data-act="step" data-c="${n}" data-d="-1" aria-label="${esc(I.t("lower") + " " + name)}"${minus}>−</button>
          <div class="v${pend ? " pend" : ""}" aria-live="polite">${shown !== undefined ? tempIn(I, shown, tu) : "–"}</div>
          <button data-act="step" data-c="${n}" data-d="1" aria-label="${esc(I.t("raise") + " " + name)}"${plus}>+</button></div></div>
      <div class="sub" style="margin-top:4px">${esc(subParts.join(" · "))}</div>
      ${chips}${this._noteHtml(`hc${n}`)}</div>`;
  }

  _pool(ctx) {
    const { I, plant, live } = ctx;
    const pt = num(live, "pool.temp"), sp = num(live, "pool.setpoint");
    const st = poolText(ctx);
    const ent = poolEntity(plant);
    const vals = [];
    if (pt !== undefined) vals.push(I.temp(pt));
    if (sp !== undefined) vals.push(`${I.t("target")} ${I.temp(sp)}`);
    return `<div class="hc" data-key="pool"><div class="hcrow"><button class="hcname"${ent ? ` data-more="${esc(ent)}"` : ""}><span class="badge" style="background:var(--ohp-pipe-off)">${iconSvg("pool", "var(--ohp-ink)")}</span><div><span class="nm">${esc(I.t("pool"))}</span>${st ? `<div class="sub" style="font-weight:400">${esc(st)}</div>` : ""}</div></button>
      <div class="sub">${esc(vals.join(" · "))}</div></div></div>`;
  }

  _dhw(ctx) {
    const { I, plant, live, mode } = ctx;
    const wh = plant.dhw && plant.dhw.water_heater;
    const st = wh ? ctx.state(wh) : null;
    const a = (st && st.attributes) || {};
    // All gauge maths in HA's unit (the water heater's attributes); native
    // feed values (°C, K) are converted into it.
    const tu = haTempUnit(ctx.hass);
    const D = (v) => convertTemp(v, "°C", tu);
    const temp = typeof a.current_temperature === "number" ? a.current_temperature : D(num(live, "dhw.temp"));
    const target = typeof a.temperature === "number" ? a.temperature : D(num(live, "dhw.target"));
    const coal = this.coal.get("dhw");
    if (coal) coal.sync();
    const shown = coal && coal.value !== undefined ? coal.value : target;
    const boostOn = Number(val(live, "dhw.boost")) === 1 || (st && st.state === "high_demand");
    const boostBusy = this._busyTarget("boost", boostOn ? 1 : 0);
    const running = boostOn || boostBusy === 1;
    const heating = mode === "dhw";
    const dis = ctx.disabled ? " disabled" : "";
    const canSet = this._target(ctx, "dhw") === wh;
    const minT = typeof a.min_temp === "number" ? a.min_temp : D(5);
    const maxT = typeof a.max_temp === "number" ? a.max_temp : D(75);
    const margin = convertDelta(5, "K", tu);
    const lo = Math.min(D(30), Math.floor(((temp ?? D(30)) - margin) / 5) * 5);
    const hi = Math.max(D(65), Math.ceil(((shown ?? D(60)) + margin) / 5) * 5);
    const X = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    const hyst = convertDelta(num(live, "dhw.hysteresis"), "K", tu);
    const T = (v, d) => tempIn(I, v, tu, d);
    let gauge = `<div class="gauge" aria-hidden="true"><div style="left:0;right:0;background:var(--ohp-pipe-off)"></div>`;
    if (hyst !== undefined && shown !== undefined) gauge += `<div title="${esc(I.t("charges_below", { t: T(shown - hyst) }))}" style="left:${X(shown - hyst)}%;width:${X(shown) - X(shown - hyst)}%;border-radius:0;background:color-mix(in srgb, var(--ohp-dhw) 35%, transparent)"></div>`;
    if (temp !== undefined) gauge += `<div style="left:0;width:${X(temp)}%;background:var(--ohp-dhw)"></div>`;
    if (shown !== undefined) gauge += `<div class="mark" style="left:calc(${X(shown)}% - 1px)"></div>`;
    gauge += `</div><div class="gscale"><span>${T(Math.round(lo), 0)}</span><span>${hyst !== undefined && shown !== undefined ? esc(I.t("charges_below", { t: T(shown - hyst, 0) })) + " · " : ""}${esc(I.t("target"))} ${T(shown, 0)}</span><span>${T(Math.round(hi), 0)}</span></div>`;
    const curMode = st ? st.state : null;
    const mShown = this._busyTarget("dhwmode", curMode) ?? curMode;
    const chipDis = !st || ctx.disabled ? " disabled" : "";
    const chip = (v, label, cls) => `<button class="mc${mShown === v ? " on " + cls : ""}" data-act="dhwmode" data-v="${v}" aria-pressed="${mShown === v}"${chipDis}>${esc(label)}</button>`;
    const minus = canSet && shown > minT + 1e-6 ? "" : " disabled";
    const plus = canSet && shown < maxT - 1e-6 ? "" : " disabled";
    const boostBtn = plant.dhw && plant.dhw.boost_button
      ? `<button class="boost${running ? " running" : ""}" data-act="boost"${running || ctx.disabled ? " disabled" : ""}>${boltSvg} ${esc(running ? I.t("boost_running") : I.t("boost"))}</button>`
      : "";
    return `<div class="sect"><div class="lab">${esc(I.t("section_dhw"))}</div><div class="hc" data-key="dhw"><div class="hcrow">
        <button class="hcname"${wh ? ` data-more="${esc(wh)}"` : ""}><span class="badge" style="background:${heating ? "var(--ohp-dhw)" : "var(--ohp-pipe-off)"}">${showerSvg(heating ? "#111" : "var(--ohp-ink)")}</span><div><span class="nm">${T(temp, 0)}</span><div class="sub" style="font-weight:400">${esc(I.t("target"))} ${T(target, 0)}</div></div></button>
        <div class="stepper"><button data-act="dhwstep" data-d="-1" aria-label="${esc(I.t("lower") + " " + I.t("dhw"))}"${minus}>−</button>
          <div class="v${coal && coal.value !== undefined ? " pend" : ""}">${T(shown, 0)}</div>
          <button data-act="dhwstep" data-d="1" aria-label="${esc(I.t("raise") + " " + I.t("dhw"))}"${plus}>+</button></div></div>
      ${gauge}
      <div class="hcrow" style="margin-top:8px"><div class="modechips" style="margin-top:0">${chip("performance", I.t("comfort"), "dhwc")}${chip("eco", I.t("eco"), "eco")}${chip("off", I.t("off"), "offc")}</div>${boostBtn}</div>
      ${this._noteHtml("dhw")}${dis ? "" : ""}</div></div>`;
  }

  render(ctx) {
    const { I, sec } = ctx;
    this._reconcile(ctx);
    let h = this._hero(ctx) + ctx.banner + this._hpSection(ctx);
    if (sec.circuits.length || sec.pool) {
      h += `<div class="sect"><div class="lab">${esc(I.t("section_circuits"))}</div>`;
      for (const c of sec.circuits) h += this._circuit(ctx, c);
      if (sec.pool) h += this._pool(ctx);
      h += `</div>`;
    }
    if (sec.dhw) h += this._dhw(ctx);
    return h;
  }
}
