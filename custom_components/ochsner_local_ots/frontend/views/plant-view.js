// Card A: live plant schematic.

import { esc } from "../lib/dom.js";
import { modeColor, textTint } from "../lib/colors.js";
import { plantSVG } from "../lib/plant-svg.js";
import { badgeFor, circuitActive, circuitCooling, circuitKind, circuitName, convertTemp, displayEntity, enumLabel, haTempUnit, hcKey, isProducing, legacyProgram, liveCop, num, val } from "../lib/model.js";

// Aux heater status enum Off*Htg*Stby*Dhw*BIP*Error*Frost*…: these mean "heating now".
const AUX_ON = new Set([1, 3, 6]);

export function statusText(ctx) {
  const raw = val(ctx.live, "hp.status");
  return enumLabel(ctx.plant, "hp.status", raw) || ctx.I.t(ctx.mode === "off" ? "off" : ctx.mode === "fault" ? "fault" : ctx.mode === "standby" ? "standby" : ctx.mode === "heat" ? "heating" : ctx.mode === "cool" ? "cooling" : ctx.mode === "dhw" ? "dhw" : "defrost");
}

// pool.mode: 0 auto, 1 off, 2 on, 3 holiday.
const POOL_OFF = 1;

/** Pool status text: status label, else the mode label. */
export function poolText(ctx) {
  const { I, plant, live } = ctx;
  const mode = val(live, "pool.mode");
  return enumLabel(plant, "pool.status", val(live, "pool.status"))
    || enumLabel(plant, "pool.mode", mode)
    || (mode === undefined ? "" : Number(mode) === POOL_OFF ? I.t("off") : I.t("auto"));
}

export function poolActive(live) {
  const st = num(live, "pool.status");
  return st !== undefined && st > 0 && num(live, "pool.mode") !== POOL_OFF;
}

export function poolEntity(plant) {
  return displayEntity(plant, "pool.temp") || displayEntity(plant, "pool.status") || displayEntity(plant, "pool.setpoint") || displayEntity(plant, "pool.mode");
}

/** Label of a legacy operating program (Reduziert, Normalbetrieb, …), or null. */
export function programText(ctx, c) {
  const lp = legacyProgram(ctx.plant, ctx.live, c.index);
  return lp ? lp.label || ctx.I.t("program_n", { n: lp.raw }) : null;
}

/** Chip text for a circuit row / the B tile status. */
export function circuitPresetText(ctx, c) {
  const { I, plant, live } = ctx;
  const n = c.index;
  const b = badgeFor(plant, val(live, hcKey(n, "badge")));
  const st = c.climate ? ctx.state(c.climate) : null;
  const prog = val(live, hcKey(n, "op_program"));
  if (b.kind === "off" || (b.kind === "unknown" && (Number(prog) === 1 || (st && st.state === "off")))) return I.t("off");
  if (b.kind === "frost") return I.t("frost");
  if (b.kind === "vacation") return I.t("vacation");
  if (b.kind === "party") return I.t("party");
  if (b.kind === "fault") return I.t("fault");
  // A legacy program runs instead of the app's Komfort/Eco control.
  const legacy = programText(ctx, c);
  if (legacy) return legacy;
  let preset = b.preset;
  if (!preset) {
    const ce = val(live, hcKey(n, "comfort_eco"));
    if (ce !== undefined) preset = Number(ce) === 1 ? "eco" : "comfort";
    else if (st && st.attributes) preset = st.attributes.preset_mode;
  }
  return preset === "eco" ? I.t("eco") : preset === "comfort" ? I.t("comfort") : "";
}

export class PlantView {
  constructor(card) {
    this.card = card;
  }

  bind() {}

  vm(ctx) {
    const { I, plant, live, sec, mode } = ctx;
    const t = I.t;
    const flowRate = num(live, "hp.flow_rate");
    const moving = flowRate !== undefined ? flowRate > 0.05 : isProducing(mode) || mode === "defrost";
    const auxRaw = num(live, "hp.aux_status");
    const auxOn = auxRaw !== undefined && AUX_ON.has(auxRaw);
    const rows = sec.circuits.map((c) => {
      const n = c.index;
      const active = circuitActive(plant, live, n, mode);
      const fa = num(live, hcKey(n, "flow_actual"));
      const ft = num(live, hcKey(n, "flow_target"));
      const name = circuitName(c, live);
      const parts = [];
      if (fa !== undefined) parts.push(`${ctx.narrow ? t("flow_short") : t("flow")} ${I.temp(fa)}`);
      if (ft !== undefined) parts.push(`${t("target")} ${I.temp(ft)}`);
      return {
        type: "hc",
        name,
        kind: circuitKind(name),
        sub: parts.join(" · "),
        chip: circuitPresetText(ctx, c),
        active,
        cooling: circuitCooling(plant, live, c, mode),
        entity: c.climate || displayEntity(plant, hcKey(n, "status")),
      };
    });
    if (sec.pool) {
      const pt = num(live, "pool.temp"); // optional: not every pool module reports it
      const sp = num(live, "pool.setpoint");
      const parts = [];
      if (pt !== undefined) parts.push(`${t("water")} ${I.temp(pt)}`);
      if (sp !== undefined) parts.push(`${t("target")} ${I.temp(sp)}`);
      rows.push({
        type: "pool",
        name: t("pool"),
        kind: "pool",
        sub: parts.join(" · "),
        chip: poolText(ctx),
        active: mode !== "dhw" && poolActive(live),
        cooling: false,
        entity: poolEntity(plant),
      });
    }
    let dhw = null;
    if (sec.dhw) {
      const wh = plant.dhw && plant.dhw.water_heater;
      const st = wh ? ctx.state(wh) : null;
      const a = (st && st.attributes) || {};
      // The schematic shows native °C like every other value in it; the
      // water heater's attributes are in HA's unit.
      const tu = haTempUnit(ctx.hass);
      const temp = convertTemp(a.current_temperature, tu, "°C") ?? num(live, "dhw.temp");
      const target = convertTemp(a.temperature, tu, "°C") ?? num(live, "dhw.target");
      const boost = Number(val(live, "dhw.boost")) === 1;
      const valve = val(live, "hp.dhw_valve");
      dhw = {
        label: t("dhw"),
        t: I.temp(temp, 0),
        sub: target !== undefined ? `${t("target")} ${I.temp(target, 0)}${boost ? " · " + t("boost") : ""}` : "",
        active: valve !== undefined ? valve === true || Number(valve) === 1 : mode === "dhw",
        entity: wh || displayEntity(plant, "dhw.temp"),
      };
    }
    return {
      id: ctx.uid,
      aria: `${plant.title || t("heat_pump")}: ${statusText(ctx)}`,
      mode: mode === "fault" ? "standby" : mode,
      spin: isProducing(mode),
      moving,
      hpLabel: t("heat_pump"),
      hpEntity: displayEntity(plant, "hp.status"),
      flowT: num(live, "hp.flow") !== undefined ? I.temp(num(live, "hp.flow")) : null,
      retT: num(live, "hp.return") !== undefined ? I.temp(num(live, "hp.return")) : null,
      flowShort: t("flow_short"),
      retShort: t("return_short"),
      buffer: sec.buffer
        ? {
            label: t("buffer"),
            t: sec.bufferTemp && num(live, "hp.buffer_top") !== undefined ? I.temp(num(live, "hp.buffer_top")) : null,
            aux: sec.aux,
            auxOn,
            entity: displayEntity(plant, "hp.buffer_top"),
          }
        : null,
      aux: !sec.buffer && sec.aux ? { on: auxOn } : null,
      dhw,
      rows,
    };
  }

  chips(ctx) {
    const { I, plant, live, sec } = ctx;
    const t = I.t;
    const out = [];
    const chip = (cls, key, content, entity) => {
      const ent = entity ? ` data-more="${esc(entity)}"` : "";
      const tag = entity ? "button" : "span";
      out.push(`<${tag} class="chip ${cls}" data-key="${key}"${ent}>${content}</${tag}>`);
    };
    const fault = val(live, "hp.sum_fault");
    if (fault !== undefined) {
      if (Number(fault) === 1) chip("ok", "health", `● <b>${esc(t("healthy"))}</b>`, displayEntity(plant, "hp.sum_fault"));
      else chip("warn", "health", `⚠ <b>${esc(t("fault"))}</b> ${esc(enumLabel(plant, "hp.sum_fault", fault) || "")}`, displayEntity(plant, "hp.sum_fault"));
    }
    if (sec.aux) {
      const a = val(live, "hp.aux_status");
      const l = enumLabel(plant, "hp.aux_status", a);
      if (l) chip("", "aux", `${esc(t("aux_heater"))} <b>${esc(l)}</b>`, displayEntity(plant, "hp.aux_status"));
    }
    if (sec.silent) {
      const s = val(live, "hp.silent_program");
      const l = enumLabel(plant, "hp.silent_program", s);
      if (l) chip("", "silent", `${esc(t("silent"))} <b>${esc(l)}</b>`, displayEntity(plant, "hp.silent_program"));
    }
    // Emergency operation (enum 0 passive, 1 reduced unconfirmed, 2 reduced
    // confirmed, 3 standard confirmed, 4 plant fault): only when not passive.
    for (const [key, label, show] of [["hp.emergency_status", "emergency_hp", sec.emergency], ["dhw.emergency_status", "emergency_dhw", sec.emergency && sec.dhw]]) {
      const e = num(live, key);
      if (!show || e === undefined || e === 0) continue;
      chip("warn", key, `⚠ <b>${esc(t(label))}</b> ${esc(enumLabel(plant, key, e) || "")}`, displayEntity(plant, key));
    }
    const dewCirc = sec.circuits.find((c) => c.cool && circuitCooling(plant, live, c, ctx.mode) && num(live, hcKey(c.index, "dew_point")) !== undefined);
    if (dewCirc) chip("", "dew", `${esc(t("dew_point"))} <b>${I.temp(num(live, hcKey(dewCirc.index, "dew_point")))}</b>`, displayEntity(plant, hcKey(dewCirc.index, "dew_point")));
    const n = (key, label, d, unit) => {
      const v = num(live, key);
      if (v !== undefined) chip("", key, `${esc(t(label))} <b>${I.num(v, d)}${unit ? " " + unit : ""}</b>`, displayEntity(plant, key));
    };
    n("hp.cycles_today", "cycles_today", 0);
    n("hp.defrosts_today", "defrosts_today", 0);
    n("hp.flow_rate", "flow_rate", 2, "m³/h");
    n("hp.pressure", "pressure", 2, "bar");
    return out.join("");
  }

  render(ctx) {
    const { I, plant, live, mode } = ctx;
    const t = I.t;
    const col = modeColor(mode);
    const out = num(live, "hp.outdoor");
    const q = num(live, "hp.heat_output");
    const cop = liveCop(live, ctx.caps);
    const status = statusText(ctx);
    const kpis = [];
    if (out !== undefined) kpis.push(`<div class="kpi"><b>${I.num(out)}</b><small>°C ${esc(t("outdoor"))}</small></div>`);
    if (q !== undefined) kpis.push(`<div class="kpi"><b>${I.num(q)}</b><small>${esc(t(mode === "cool" ? "kw_cool" : "kw_heat"))}</small></div>`);
    // Same basis as the 24 h COP tile: compressor power only, hence the "*".
    if (cop !== undefined) kpis.push(`<div class="kpi" title="${esc(t("cop_live_title"))}"><b>${I.num(cop)}*</b><small>${esc(t("cop"))}</small></div>`);
    return `
      <div class="hdr"><div class="l"><div class="ttl">${esc(plant.title || plant.model || t("heat_pump"))}</div>
        <span class="pill" style="color:${textTint(col)};background:color-mix(in srgb, ${col} 16%, transparent)"><span class="dot" style="background:${col}"></span>${esc(status)}</span></div>
        <div class="kpis">${kpis.join("")}</div></div>
      ${ctx.banner}
      <div data-key="svg">${plantSVG(this.vm(ctx), ctx.width)}</div>
      <div class="chips">${this.chips(ctx)}</div>`;
  }
}
