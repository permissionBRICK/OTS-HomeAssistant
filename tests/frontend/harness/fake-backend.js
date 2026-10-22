// Fake Ochsner backend for the frontend harness and tests: builds the exact
// B6 websocket payloads (ochsner_local_ots/plants + subscribe events) and a
// fake `hass` (states, callService, callWS history) from the sanitized
// AirHawk 518 snapshot and the discovery catalog. Service calls are recorded
// and applied to the fake controller (B4/B5 semantics) so the UI reacts.
// Runs in Node and in the browser (no DOM use).

// ---- datapoint ids ---------------------------------------------------------

const HC1 = {
  name: "BCNL7o58AAE=", op_program: "AiIQvY58IgE=", op_program_present: "AiIQvY58AAE=",
  comfort_eco: "AiPKUI58AAE=", season: "AiMN7I58AAE=", room_offset: "ACNZw458AAE=",
  target_selector: "CyP/dY58AAE=", badge: "CyPmL458AAE=", status: "CyMogo58AAE=",
  heat_limit_status: "CyO9/o58AAE=", cool_limit_status: "CyNm0I58AAE=",
  room_target_current: "CiMeXI58AAE=", room_target_heat: "CiPcW458AAE=", room_target_cool: "CiO36o58AAE=",
  flow_target: "ACJz7Y58AAE=", dew_point: "CiMeMY58AAE=",
  setpoint_normal_heat: "ASMhEo58AAE=", setpoint_reduced_heat: "ASOkTo58AAE=",
  setpoint_normal_cool: "ASNKo458AAE=", setpoint_reduced_cool: "ASPP/458AAE=",
  setpoint_frost_room: "ASOcQo58AAE=", setpoint_manual: "ASMXeI58AAE=",
  heat_curve_base_outdoor: "ACPl8458AAE=", heat_curve_base_flow: "ACPUwI58AAE=",
  heat_curve_norm_outdoor: "ACOGw458AAE=", heat_curve_norm_flow: "ACO38I58AAE=",
  heat_curve_max_flow: "ACM5mY58AAE=", heat_limit: "ACM8vI58AAE=", heat_curve_ref_room: "ACNx1I58AAE=",
  cool_curve_base_outdoor: "ACN5nI58AAE=", cool_curve_base_flow: "ACNIr458AAE=",
  cool_curve_norm_outdoor: "ACMarI58AAE=", cool_curve_norm_flow: "ACMrn458AAE=",
  cool_limit: "ACPdII58AAE=", cool_curve_ref_room: "ACNxpY58AAE=",
};
const HC_TAGS = { 1: 31886, 2: 19693, 3: 23756, 4: 11307 };
const HC_IO = {
  availability: { 1: "ESNtpAAAAAE=", 2: "ESMOlAAAAAE=", 3: "ESMvhAAAAAE=", 4: "ESPI9AAAAAE=" },
  flow_actual: { 1: "AyKvbyiJAAE=", 2: "AyLrQiiJAAE=", 3: "AyLIqSiJAAE=" },
  mixer: { 1: "BiJEkiiJSjA=", 2: "BiIAvyiJSjA=", 3: "BiIjVCiJSjA=" },
  pump: { 1: "ByIPECiJAAE=", 2: "ByJ62CiJAAE=", 3: "ByKpnyiJAAE=" },
};
const HP = {
  "hp.outdoor": "AyKWuiiJAAE=", "hp.outdoor_avg": "ACMZwiiJAAE=",
  "hp.flow": "AyJZqiiJAAE=", "hp.return": "AyILsCiJAAE=", "hp.flow_rate": "AyIZciiJAAE=",
  "hp.buffer_top": "AyJn4iiJAAE=", "hp.status": "CyNmeMdZAAE=", "hp.system_status": "CyMOIMdZAAE=",
  "hp.program": "AiMu5MdZAAE=", "hp.aux_program": "AiNiMcdZAAE=", "hp.aux_status": "CyM4OMdZAAE=",
  "hp.aux_flow": "AyIkniiJAAE=", "hp.silent_program": "AiP5J8dZAAE=",
  "hp.heat_output": "CiO6JyiJAAE=", "hp.el_power": "CiPAhCiJAAE=", "hp.pressure": "AyKaCCiJAAE=",
  "hp.cycles_today": "AyMePiiJAAE=", "hp.defrosts_today": "AyNkR4r8AAE=", "hp.defrosts_total": "AyOVHIr8AAE=",
  "hp.hours_total": "AyOvsSiJAAE=", "hp.energy_heat_total": "AyMXFSiJAAE=",
  "hp.energy_heat_year": "DCNBAiiJAAE=", "hp.energy_el_year": "DCP7vSiJAAE=",
  "hp.energy_heat_prev_year": "DCPOmiiJAAE=", "hp.energy_el_prev_year": "DCM2oyiJAAE=",
  "hp.jaz_year": "CiMUVyiJAAE=", "hp.jaz_prev_year": "CiM6siiJAAE=",
  "hp.sum_fault": "BSLZ1IwCAAE=", "hp.dhw_valve": "ByIDxCiJAAE=", "hp.capability": "ESO6g8dZAAE=",
};
const AVAIL = {
  "avail.bms": "ESNHDAAAAAE=", "avail.dhw": "ESMSIwAAAAE=", "avail.buffer": "AiPN8gAAAAE=",
  "avail.buffer_aux": "ESNbHQAAAAE=", "avail.aux": "ESM/0gAAAAE=", "avail.pool": "AiM3dQAAAAE=",
  "avail.silent": "AiPMsAAAAAE=", "avail.emergency": "AiMR5AAAAAE=",
  "avail.ext_dhw": "AiP/JgAAAAE=", "avail.ext_buffer": "AiNnNAAAAAE=",
};
const DHW = {
  "dhw.temp": "AyKOLyiJAAE=", "dhw.target": "CiMzqnXZAAE=", "dhw.status": "CyMognXZAAE=",
  "dhw.program": "AiIQvXXZIgE=", "dhw.comfort_eco": "AiPKUHXZAAE=", "dhw.boost": "AiOzyHXZAAE=",
  "dhw.offset": "ACPOwnXZAAE=", "dhw.setpoint_comfort": "ASMv1XXZAAE=", "dhw.setpoint_eco": "ASOp1XXZAAE=",
  "dhw.setpoint_reduced": "ASOXCHXZAAE=", "dhw.setpoint_boost": "ASPuhXXZAAE=",
};
// Pool roles are not in the B1 table yet; keys assumed by the frontend.
const POOL = { "pool.temp": "ASPmx0LcAAE=", "pool.program": "AiIQvULcIgE=", "pool.status": "CyMogkLcAAE=" };

export const BADGES = [
  "off_grey", "fault_orange", "auto_comfort_grey", "auto_comfort_red", "auto_comfort_blue",
  "auto_eco_grey", "auto_eco_red", "auto_eco_blue", "heat_comfort_grey", "heat_comfort_red",
  "heat_eco_grey", "heat_eco_red", "cool_comfort_grey", "cool_comfort_blue", "cool_eco_grey",
  "cool_eco_blue", "off_frost_red", "vacation_grey", "vacation_red", "party_grey", "party_red",
  "party_blue", "vacation_blue",
];

const b64decode = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const b64encode = (bytes) => btoa(String.fromCharCode(...bytes));

export function retag(id, tag) {
  const b = b64decode(id);
  b[4] = tag & 0xff;
  b[5] = (tag >> 8) & 0xff;
  return b64encode(b);
}

/** Enum descriptor ("A*B*C") lives on member 4353 of the same object. */
export function descriptorId(id) {
  const b = b64decode(id);
  b[6] = 4353 & 0xff;
  b[7] = 4353 >> 8;
  return b64encode(b);
}

/** role key → datapoint id for every role of the plant model. */
export function roleIds() {
  const out = { ...HP, ...AVAIL, ...DHW, ...POOL };
  for (let n = 1; n <= 4; n++) {
    for (const [k, id] of Object.entries(HC1)) out[`hc${n}.${k}`] = retag(id, HC_TAGS[n]);
    for (const [k, ids] of Object.entries(HC_IO)) if (ids[n]) out[`hc${n}.${k}`] = ids[n];
  }
  return out;
}

// Writable datapoints missing from the catalog (B2 supplement entities).
const SUPPLEMENT = {
  comfort_eco: { platform: "select", de: "Komfort/Eco", en: "Comfort/Eco", options: { 0: "Komfort", 1: "Eco" } },
  room_offset: { platform: "number", de: "Raumsollwert-Korrektur", en: "Room setpoint correction", min: -3, max: 3, step: 0.5, unit: "K" },
  "dhw.offset": { platform: "number", de: "Warmwasser-Korrektur", en: "DHW correction", min: -9, max: 9, step: 3, unit: "K" },
  "dhw.comfort_eco": { platform: "select", de: "Warmwasser Komfort/Eco", en: "DHW comfort/eco", options: { 1: "Eco", 2: "Komfort" } },
  "dhw.program": { platform: "select", de: "Betriebswahl Warmwasser", en: "DHW operating program", options: { 0: "Ein", 1: "Aus" } },
  "dhw.boost": { platform: "sensor", de: "Boost-Ladung", en: "Boost charging" },
};
const NO_ENTITY = new Set(["badge", "target_selector", "op_program_present", "availability"]);

const slug = (s) =>
  String(s)
    .toLowerCase()
    .replace(/ä/g, "a").replace(/ö/g, "o").replace(/ü/g, "u").replace(/ß/g, "ss")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");

const first = (v) => (Array.isArray(v) ? v[0] : v);
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// ---- variants --------------------------------------------------------------

/** Raw values per role for a variant ("house" = the real snapshot). */
export function variantValues(snapshot, variant) {
  const ids = roleIds();
  const vals = {};
  for (const [key, id] of Object.entries(ids)) {
    if (snapshot.states && snapshot.states[id] === 5) continue;
    if (!(id in snapshot.values)) continue;
    let v = first(snapshot.values[id]);
    if (v === null || v === undefined) continue;
    vals[key] = v;
  }
  if (variant === "full") {
    Object.assign(vals, {
      "avail.dhw": 256, "avail.pool": 1, "hp.status": 2, "hp.system_status": 3,
      "hp.outdoor": 29.4, "hp.outdoor_avg": 28.1, "hp.flow": 17.8, "hp.return": 21.3,
      "hp.buffer_top": 18.4, "hp.heat_output": 4.2, "hp.el_power": 1.1, "hp.flow_rate": 1.12,
      "hp.cycles_today": 4, "hp.defrosts_today": 0, "hp.dhw_valve": false, "hp.capability": 7,
      "hc1.availability": 31, "hc2.availability": 31, "hc3.availability": 0, "hc4.availability": 0,
      "hc1.name": "Erdgeschoss", "hc2.name": "Obergeschoss",
      "hc1.badge": 4, "hc2.badge": 7, "hc1.season": 4, "hc2.season": 2, "hc1.comfort_eco": 0, "hc2.comfort_eco": 1,
      "hc1.target_selector": 3, "hc2.target_selector": 3, "hc1.op_program": 0, "hc2.op_program": 0,
      "hc1.room_target_cool": 24, "hc2.room_target_cool": 26, "hc1.room_offset": 0, "hc2.room_offset": 0.5,
      "hc1.flow_actual": 18.6, "hc2.flow_actual": 19.1, "hc1.flow_target": 18.5, "hc2.flow_target": 19.0,
      "hc1.dew_point": 15.2, "hc2.dew_point": 15.6, "hc1.pump": true, "hc2.pump": true,
      "hc1.cool_curve_base_outdoor": 20, "hc1.cool_curve_base_flow": 20, "hc1.cool_curve_norm_outdoor": 32, "hc1.cool_curve_norm_flow": 18,
      "hc2.cool_curve_base_outdoor": 35, "hc2.cool_curve_base_flow": 16, "hc2.cool_curve_norm_outdoor": 25, "hc2.cool_curve_norm_flow": 20,
      "hc1.cool_limit": 22, "hc2.cool_limit": 23.5,
      "hc1.heat_curve_norm_flow": 35, "hc1.heat_curve_max_flow": 40, "hc2.heat_curve_norm_flow": 36, "hc2.heat_curve_base_flow": 24,
      "hc2.heat_curve_max_flow": 40, "hc2.heat_limit": 16,
      "dhw.temp": 52.3, "dhw.target": 52, "dhw.status": 0, "dhw.program": 0, "dhw.comfort_eco": 2, "dhw.boost": 0, "dhw.offset": 0,
      "dhw.setpoint_comfort": 52, "dhw.setpoint_eco": 46, "dhw.setpoint_reduced": 40, "dhw.setpoint_boost": 60,
      "pool.temp": 26.8, "pool.program": 1, "pool.status": 0,
    });
    for (const k of Object.keys(vals)) if (k.startsWith("hc3.") || k.startsWith("hc4.")) if (!k.endsWith("availability")) delete vals[k];
  } else if (variant === "compact") {
    Object.assign(vals, {
      "avail.dhw": 256, "avail.buffer": 0, "avail.buffer_aux": 0, "avail.aux": 0, "avail.silent": 0, "avail.emergency": 0,
      "hp.status": 3, "hp.system_status": 2, "hp.outdoor": 3.2, "hp.outdoor_avg": 2.4, "hp.flow": 51.8, "hp.return": 45.0,
      "hp.heat_output": 6.1, "hp.flow_rate": 1.05, "hp.dhw_valve": true, "hp.cycles_today": 5, "hp.defrosts_today": 4,
      "hc1.availability": 27, "hc2.availability": 0, "hc3.availability": 0, "hc4.availability": 0,
      "hc1.name": "Heizkreis 1", "hc1.badge": 2, "hc1.pump": false, "hc1.flow_actual": 31.0, "hc1.flow_target": 33.5,
      "hc1.heat_curve_base_outdoor": 20, "hc1.heat_curve_base_flow": 24, "hc1.heat_curve_norm_outdoor": -12, "hc1.heat_curve_norm_flow": 38,
      "hc1.heat_curve_max_flow": 45, "hc1.heat_limit": 16, "hc1.room_target_heat": 21, "hc1.room_offset": 0,
      "dhw.temp": 46.1, "dhw.target": 50, "dhw.status": 1, "dhw.program": 0, "dhw.comfort_eco": 2, "dhw.boost": 0, "dhw.offset": 0,
      "dhw.setpoint_comfort": 50, "dhw.setpoint_eco": 45, "dhw.setpoint_reduced": 40, "dhw.setpoint_boost": 60,
    });
    delete vals["hp.el_power"];
    delete vals["hp.buffer_top"];
    for (const k of Object.keys(vals)) if (/^hc[234]\./.test(k) && !k.endsWith("availability")) delete vals[k];
  }
  return vals;
}

/** B3 capabilities from raw values. */
export function computeCapabilities(v) {
  const i = (k) => (Number.isFinite(Number(v[k])) ? Number(v[k]) : 0);
  const bms = i("avail.bms") !== 0;
  const circuits = [];
  for (let n = 1; n <= 4; n++) {
    const a = i(`hc${n}.availability`);
    if (!bms && a & 1) circuits.push({ index: n, heat: !!(a & 2), cool: !!(a & 4), room_sensor: !!(a & 32), humidity: !!(a & 64) });
  }
  const caps = {
    bms,
    dhw: !bms && !!(i("avail.dhw") & 256),
    buffer: i("avail.buffer") > 0,
    buffer_temp: !!(i("avail.buffer_aux") & 1024),
    aux: !!(i("avail.aux") & (16 | 32)),
    aux_temp: !!(i("avail.aux") & 2048),
    buffer_aux_heater: !!(i("avail.buffer_aux") & 4),
    pool: !bms && i("avail.pool") > 0,
    silent: i("avail.silent") === 1,
    emergency: i("avail.emergency") === 1,
    power: v["hp.el_power"] !== undefined && v["hp.heat_output"] !== undefined,
    cooling: circuits.some((c) => c.cool),
  };
  return { caps, circuits };
}

// ---- fake plant --------------------------------------------------------------

export class FakePlant {
  constructor({ catalog, snapshot, variant = "house", host = "192.0.2.80", entryId = "01JFAKEENTRY0000000000000", lang = "de", latency = 250, timers } = {}) {
    this.catalog = new Map(catalog.points.map((p) => [p.id, p]));
    this.descriptors = snapshot.values;
    this.variant = variant;
    this.host = host;
    this.entryId = entryId;
    this.plantId = `${entryId}:${host}`;
    this.lang = lang;
    this.latency = latency;
    this.timers = timers || globalThis;
    this.ids = roleIds();
    this.values = variantValues(snapshot, variant);
    this.calls = [];
    this.failNext = null; // string → next service call rejects with this message
    this.flashWrites = 1284;
    this.available = true;
    this._subs = new Set();
    this._listeners = new Set();
    this._entities = null;
    this.title = variant === "house" ? "AirHawk Wärmepumpe" : variant === "full" ? "Ochsner Wärmepumpe" : "Kompakt-Wärmepumpe";
    this.model = variant === "house" ? "AIRHAWK518C11A" : "AIRHAWK212C";
    this._initTargets();
  }

  _initTargets() {
    // The fake controller derives room targets from base setpoint + offset.
    this.baseTarget = {};
    for (let n = 1; n <= 4; n++) {
      const sel = Number(this.values[`hc${n}.target_selector`]);
      const key = sel === 1 || sel === 3 ? "room_target_cool" : "room_target_heat";
      const t = this.values[`hc${n}.${key}`];
      if (t !== undefined) this.baseTarget[n] = t - (Number(this.values[`hc${n}.room_offset`]) || 0);
    }
  }

  get capabilities() {
    return computeCapabilities(this.values).caps;
  }

  get circuits() {
    return computeCapabilities(this.values).circuits;
  }

  // ---- entities & roles ----------------------------------------------------

  _circuitName(n) {
    return this.values[`hc${n}.name`] || `Heizkreis ${n}`;
  }

  /** Present sections' role keys. */
  roleKeys() {
    const caps = this.capabilities;
    const keys = Object.keys(this.ids).filter((k) => {
      if (k.startsWith("hp.") || k.startsWith("avail.")) return true;
      if (k.startsWith("dhw.")) return caps.dhw;
      if (k.startsWith("pool.")) return caps.pool;
      const m = /^hc(\d)\./.exec(k);
      if (m) return k.endsWith(".availability") || this.circuits.some((c) => c.index === Number(m[1]));
      return false;
    });
    return keys;
  }

  entities() {
    if (this._entities) return this._entities;
    const used = new Set();
    const ent = {};
    const mk = (platform, base) => {
      let id = `${platform}.${slug(base)}`;
      let i = 2;
      while (used.has(id)) id = `${platform}.${slug(base)}_${i++}`;
      used.add(id);
      return id;
    };
    for (const key of this.roleKeys()) {
      const short = key.replace(/^hc\d\./, "");
      const n = /^hc(\d)\./.exec(key)?.[1];
      const prefix = n ? this._circuitName(n) + " " : "";
      if (NO_ENTITY.has(short) || key.startsWith("avail.")) {
        ent[key] = null;
        continue;
      }
      const sup = SUPPLEMENT[key] || SUPPLEMENT[short];
      const p = this.catalog.get(this.ids[key]);
      if (sup && (!p || sup.platform !== "sensor")) {
        ent[key] = { platform: sup.platform, entity_id: mk(sup.platform, prefix + sup.de), name: prefix + sup[this.lang] || sup.de, sup };
        continue;
      }
      if (!p) {
        ent[key] = null;
        continue;
      }
      const platform = p.platform === "switch" ? "switch" : p.platform;
      ent[key] = {
        platform,
        entity_id: mk(platform, prefix + (p.name_de || p.name)),
        name: prefix + ((this.lang === "en" ? p.name_en : p.name_de) || p.name),
        point: p,
        enabled: p.enabled_default !== false,
      };
    }
    this._entities = ent;
    return ent;
  }

  _labels(key, lang) {
    const p = this.catalog.get(this.ids[key]);
    const e = this.entities()[key];
    if (e && e.sup && e.sup.options) return { ...e.sup.options };
    if (!p) return null;
    if (p.value_map) {
      if (lang !== "en" || !p.enum_labels_en) return { ...p.value_map };
      if (Object.keys(p.enum_labels_en).every((k) => /^\d+$/.test(k))) return { ...p.enum_labels_en };
      // token-keyed English labels: map raw → token via the enum descriptor
      const desc = this.descriptors[descriptorId(this.ids[key])];
      const tokens = typeof desc === "string" ? desc.split("*") : [];
      const out = {};
      for (const raw of Object.keys(p.value_map)) out[raw] = p.enum_labels_en[(tokens[raw] || "").toLowerCase()] || p.value_map[raw];
      return out;
    }
    if (p.options) {
      const desc = this.descriptors[descriptorId(this.ids[key])];
      const tokens = typeof desc === "string" ? desc.split("*") : null;
      const out = {};
      const table = lang === "en" ? p.enum_labels_en : p.enum_labels_de;
      for (const [label, raw] of Object.entries(p.options)) {
        const tok = tokens && tokens[raw] ? tokens[raw].toLowerCase() : null;
        out[raw] = (table && tok && table[tok]) || (p.enum_labels_de && tok && p.enum_labels_de[tok]) || label;
      }
      return out;
    }
    return null;
  }

  roles(lang = this.lang) {
    const out = {};
    for (const key of this.roleKeys()) {
      const e = this.entities()[key];
      if (!e) {
        out[key] = { entity_id: null };
        continue;
      }
      const r = { entity_id: e.entity_id };
      if (e.enabled === false) r.enabled = false;
      const p = e.point;
      const unit = (e.sup && e.sup.unit) || (p && p.unit);
      if (unit) r.unit = unit;
      if (e.platform === "number") {
        r.writable = true;
        const src = e.sup || p;
        r.min = src.min;
        r.max = src.max;
        r.step = src.step;
      }
      if (e.platform === "select") {
        r.writable = true;
        r.options = e.sup ? { ...e.sup.options } : Object.fromEntries(Object.entries(p.options).map(([label, raw]) => [String(raw), label]));
      }
      if (e.platform === "switch" || e.platform === "text") r.writable = true;
      const labels = this._labels(key, lang);
      if (labels) r.labels = labels;
      out[key] = r;
    }
    return out;
  }

  climateId(n) {
    return `climate.${slug(this._circuitName(n))}`;
  }

  plantPayload(lang = this.lang) {
    const caps = this.capabilities;
    return {
      plant_id: this.plantId,
      entry_id: this.entryId,
      host: this.host,
      device_id: "dev_main_" + this.variant,
      title: this.title,
      model: this.model,
      available: this.available,
      capabilities: caps,
      circuits: this.circuits.map((c) => ({ ...c, name: this._circuitName(c.index), device_id: `dev_hc${c.index}_${this.variant}`, climate: this.climateId(c.index) })),
      dhw: caps.dhw ? { water_heater: "water_heater.warmwasser", boost_button: "button.warmwasser_boost" } : null,
      badges: BADGES,
      roles: this.roles(lang),
      flash_writes: "sensor.ochsner_flash_writes",
    };
  }

  event() {
    const values = {};
    for (const key of this.roleKeys()) if (this.values[key] !== undefined) values[key] = this.values[key];
    return { plant_id: this.plantId, available: this.available, updated: new Date().toISOString(), capabilities: this.capabilities, values };
  }

  // ---- hass states ---------------------------------------------------------

  _hvac(n) {
    const c = this.circuits.find((x) => x.index === n);
    const prog = Number(this.values[`hc${n}.op_program`]);
    if (prog === 1) return "off";
    if (c && c.cool) {
      const s = Number(this.values[`hc${n}.season`]);
      return s === 3 ? "heat" : s === 2 ? "cool" : "heat_cool";
    }
    return "heat";
  }

  _target(n) {
    const sel = Number(this.values[`hc${n}.target_selector`]);
    const key = sel === 1 || sel === 3 ? "room_target_cool" : "room_target_heat";
    return this.values[`hc${n}.${key}`] ?? this.values[`hc${n}.room_target_current`];
  }

  _dhwMode() {
    const v = this.values;
    if (Number(v["dhw.program"]) === 1) return "off";
    if (Number(v["dhw.boost"]) === 1) return "high_demand";
    if (Number(v["dhw.comfort_eco"]) === 1) return "eco";
    return "performance";
  }

  states() {
    const st = {};
    const now = new Date().toISOString();
    const put = (entity_id, state, attributes = {}) => {
      st[entity_id] = { entity_id, state: String(state), attributes, last_changed: now, last_updated: now, context: { id: "x" } };
    };
    const roles = this.roles();
    const ents = this.entities();
    for (const [key, e] of Object.entries(ents)) {
      if (!e || e.enabled === false) continue;
      const raw = this.values[key];
      const r = roles[key];
      const attrs = { friendly_name: e.name };
      if (r.unit) attrs.unit_of_measurement = r.unit;
      let state = raw === undefined ? "unavailable" : raw;
      if (e.platform === "select") {
        attrs.options = Object.values(r.options);
        state = raw === undefined ? "unknown" : r.options[String(raw)] ?? "unknown";
      } else if (e.platform === "switch") state = raw === undefined ? "unavailable" : raw === true || Number(raw) === 1 ? "on" : "off";
      else if (e.platform === "number") Object.assign(attrs, { min: r.min, max: r.max, step: r.step });
      else if (r.labels && raw !== undefined && e.platform === "sensor") state = r.labels[String(raw)] ?? raw;
      put(e.entity_id, state, attrs);
    }
    for (const c of this.circuits) {
      const n = c.index;
      const t = this._target(n);
      const off = Number(this.values[`hc${n}.room_offset`]) || 0;
      const badge = BADGES[Number(this.values[`hc${n}.badge`])] || "";
      const hvac_modes = ["off", ...(c.heat ? ["heat"] : []), ...(c.cool ? ["cool"] : []), ...(c.heat && c.cool ? ["heat_cool"] : [])];
      put(this.climateId(n), this._hvac(n), {
        friendly_name: this._circuitName(n),
        hvac_modes,
        preset_modes: ["comfort", "eco"],
        preset_mode: Number(this.values[`hc${n}.comfort_eco`]) === 1 ? "eco" : "comfort",
        temperature: t,
        min_temp: t !== undefined ? t - off - 3 : 7,
        max_temp: t !== undefined ? t - off + 3 : 35,
        target_temp_step: 0.5,
        current_temperature: null,
        hvac_action: badge === "off_grey" ? "off" : badge.endsWith("_red") ? "heating" : badge.endsWith("_blue") ? "cooling" : "idle",
        badge,
        room_offset: off,
        flow_temperature: this.values[`hc${n}.flow_actual`],
        flow_target: this.values[`hc${n}.flow_target`],
        supported_features: 17,
      });
    }
    if (this.capabilities.dhw) {
      const m = this._dhwMode();
      const sp = m === "eco" ? "dhw.setpoint_eco" : m === "high_demand" ? "dhw.setpoint_boost" : "dhw.setpoint_comfort";
      put("water_heater.warmwasser", m, {
        friendly_name: "Warmwasser",
        operation_list: ["off", "eco", "performance", "high_demand"],
        operation_mode: m,
        temperature: this.values[sp],
        current_temperature: this.values["dhw.temp"],
        min_temp: m === "high_demand" ? 40 : 5,
        max_temp: 75,
        target_temp_step: 0.5,
        offset: this.values["dhw.offset"],
      });
      put("button.warmwasser_boost", "unknown", { friendly_name: "Warmwasser Boost" });
    }
    put("sensor.ochsner_flash_writes", this.flashWrites, { friendly_name: "Flash-Schreibzugriffe" });
    return st;
  }

  // ---- service calls -------------------------------------------------------

  _keyFor(entityId) {
    for (const [key, e] of Object.entries(this.entities())) if (e && e.entity_id === entityId) return key;
    return null;
  }

  _write(key, v) {
    if (this.values[key] === v) return;
    this.values[key] = v;
    this.flashWrites++;
  }

  _badgeFor(n) {
    const prog = Number(this.values[`hc${n}.op_program`]);
    if (prog === 1) return 0;
    const eco = Number(this.values[`hc${n}.comfort_eco`]) === 1;
    const cur = BADGES[Number(this.values[`hc${n}.badge`])] || "auto_comfort_grey";
    const colour = cur.split("_").pop();
    const c = colour === "red" || colour === "blue" || colour === "grey" ? colour : "grey";
    const name = `auto_${eco ? "eco" : "comfort"}_${c}`;
    const idx = BADGES.indexOf(name);
    return idx >= 0 ? idx : 2;
  }

  _recomputeTarget(n) {
    const sel = Number(this.values[`hc${n}.target_selector`]);
    const key = sel === 1 || sel === 3 ? "room_target_cool" : "room_target_heat";
    if (this.baseTarget[n] !== undefined) this.values[`hc${n}.${key}`] = this.baseTarget[n] + (Number(this.values[`hc${n}.room_offset`]) || 0);
  }

  async callService(domain, service, data = {}) {
    const call = { domain, service, data: { ...data }, at: Date.now() };
    this.calls.push(call);
    await new Promise((r) => this.timers.setTimeout(r, this.latency));
    if (this.failNext) {
      const msg = this.failNext;
      this.failNext = null;
      call.error = msg;
      const err = new Error(msg);
      err.code = "home_assistant_error";
      throw err;
    }
    const eid = data.entity_id;
    const v = this.values;
    if (domain === "number" && service === "set_value") {
      const key = this._keyFor(eid);
      if (!key) throw new Error(`unknown entity ${eid}`);
      this._write(key, Number(data.value));
      const m = /^hc(\d)\.room_offset$/.exec(key);
      if (m) this._recomputeTarget(Number(m[1]));
    } else if (domain === "select" && service === "select_option") {
      const key = this._keyFor(eid);
      const opts = this.roles()[key].options;
      const raw = Object.keys(opts).find((k) => opts[k] === data.option);
      if (raw === undefined) throw new Error(`invalid option ${data.option}`);
      this._write(key, Number(raw));
      if (key === "hp.program") v["hp.status"] = Number(raw) === 3 ? 17 : 1;
    } else if (domain === "climate") {
      const n = this.circuits.find((c) => this.climateId(c.index) === eid)?.index;
      if (!n) throw new Error(`unknown climate ${eid}`);
      const c = this.circuits.find((x) => x.index === n);
      if (service === "set_temperature") {
        const t = this._target(n);
        const off = Number(v[`hc${n}.room_offset`]) || 0;
        const nw = clamp(Math.round((off + Number(data.temperature) - t) * 2) / 2, -3, 3);
        if (nw !== off) this._write(`hc${n}.room_offset`, nw);
        this._recomputeTarget(n);
      } else if (service === "set_hvac_mode") {
        if (data.hvac_mode === "off") this._write(`hc${n}.op_program`, 1);
        else {
          if (Number(v[`hc${n}.op_program`]) !== 0) this._write(`hc${n}.op_program`, 0);
          if (c.cool) this._write(`hc${n}.season`, { heat: 3, cool: 2, heat_cool: 4 }[data.hvac_mode]);
        }
        v[`hc${n}.badge`] = this._badgeFor(n);
      } else if (service === "set_preset_mode") {
        if (Number(v[`hc${n}.op_program`]) !== 0) this._write(`hc${n}.op_program`, 0);
        this._write(`hc${n}.comfort_eco`, data.preset_mode === "eco" ? 1 : 0);
        v[`hc${n}.badge`] = this._badgeFor(n);
      }
    } else if (domain === "water_heater") {
      if (service === "set_operation_mode") {
        const m = data.operation_mode;
        if (m === "off") this._write("dhw.program", 1);
        else {
          if (Number(v["dhw.program"]) !== 0) this._write("dhw.program", 0);
          if (m === "eco") { this._write("dhw.comfort_eco", 1); this._write("dhw.boost", 0); }
          if (m === "performance") { this._write("dhw.comfort_eco", 2); this._write("dhw.boost", 0); }
          if (m === "high_demand") this._write("dhw.boost", 1);
        }
      } else if (service === "set_temperature") {
        const m = this._dhwMode();
        const sp = m === "eco" ? "dhw.setpoint_eco" : m === "high_demand" ? "dhw.setpoint_boost" : "dhw.setpoint_comfort";
        this._write(sp, Number(data.temperature));
        v["dhw.target"] = Number(data.temperature);
      }
    } else if (domain === "button" && service === "press") {
      if (Number(v["dhw.program"]) !== 0) this._write("dhw.program", 0);
      this._write("dhw.boost", 1);
    } else {
      throw new Error(`service ${domain}.${service} not faked`);
    }
    this.push();
    return { context: { id: "fake" } };
  }

  /** Emit a coordinator update: subscription event + new hass states. */
  push() {
    const ev = this.event();
    for (const cb of this._subs) cb(ev);
    for (const fn of this._listeners) fn();
  }

  onChange(fn) {
    this._listeners.add(fn);
    return () => this._listeners.delete(fn);
  }

  /** Writes recorded as compact strings, e.g. "number.set_value number.x 38". */
  writes() {
    return this.calls.map((c) => {
      const d = { ...c.data };
      const eid = d.entity_id;
      delete d.entity_id;
      return `${c.domain}.${c.service} ${eid} ${Object.values(d).join(" ")}`.trim();
    });
  }
}

// ---- fake connection + hass ------------------------------------------------

export function fakeConnection(plants, { failPlants = false } = {}) {
  const listeners = {};
  const conn = {
    sent: [],
    subscriptions: 0,
    sendMessagePromise(msg) {
      conn.sent.push(msg);
      if (msg.type === "ochsner_local_ots/plants") {
        if (failPlants) return Promise.reject({ code: "unknown_command", message: "Unknown command." });
        return Promise.resolve({
          version: 1,
          settings: { color_scheme: conn.colorScheme || "ha" },
          plants: plants.map((p) => p.plantPayload(msg.language && msg.language.startsWith("en") ? "en" : "de")),
        });
      }
      return Promise.reject({ code: "unknown_command", message: `Unknown command ${msg.type}` });
    },
    subscribeMessage(cb, msg) {
      conn.sent.push(msg);
      if (msg.type !== "ochsner_local_ots/subscribe") return Promise.reject({ code: "unknown_command" });
      conn.subscriptions++;
      const wrapped = (ev) => cb(ev);
      for (const p of plants) p._subs.add(wrapped);
      Promise.resolve().then(() => {
        for (const p of plants) wrapped(p.event());
      });
      return Promise.resolve(async () => {
        conn.subscriptions--;
        for (const p of plants) p._subs.delete(wrapped);
      });
    },
    addEventListener(type, fn) {
      (listeners[type] = listeners[type] || new Set()).add(fn);
    },
    removeEventListener(type, fn) {
      if (listeners[type]) listeners[type].delete(fn);
    },
    fire(type) {
      for (const fn of listeners[type] || []) fn(conn, type);
    },
  };
  return conn;
}

/** 24 h synthetic history shaped like the real plant (hourly kW means + status). */
export const HIST = {
  heat: [0.23, 2.79, 0.07, 0.23, 3.03, 0.0, 0.21, 2.96, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.11, 3.71, 2.04, 0.21, 3.68, 0.11, 1.63, 1.54, 0.21],
  elec: [0.04, 0.41, 0.02, 0.05, 0.43, 0.01, 0.04, 0.45, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.03, 0.56, 0.32, 0.04, 0.54, 0.03, 0.23, 0.22, 0.04],
  status: [[0, 0], [0.15, 12], [1.283, 1], [1.883, 0], [2.717, 12], [4.417, 1], [5.033, 0], [6.1, 12], [7.2, 1], [7.817, 9], [7.833, 13], [7.85, 0], [15.45, 12], [16.283, 1], [17.483, 0], [18.167, 12], [19.0, 1], [19.817, 4], [19.9, 1], [20.0, 0], [20.617, 12], [21.783, 3], [22.4, 0], [23.233, 12]],
};

export function makeHistory(plant, msg, now = Date.now()) {
  const roles = plant.plantPayload().roles;
  const start = Date.parse(msg.start_time);
  const out = {};
  const ids = msg.entity_ids || [];
  const H = 3600 * 1000;
  for (const eid of ids) {
    if (eid === roles["hp.heat_output"]?.entity_id) out[eid] = HIST.heat.map((v, i) => ({ s: String(v), lu: (start + i * H) / 1000 }));
    else if (eid === roles["hp.el_power"]?.entity_id) out[eid] = HIST.elec.map((v, i) => ({ s: String(v), lu: (start + i * H) / 1000 }));
    else if (eid === roles["hp.status"]?.entity_id) {
      const labels = roles["hp.status"].labels || {};
      out[eid] = HIST.status.map(([h, raw]) => ({ s: labels[String(raw)] ?? String(raw), lu: (start + h * H) / 1000 })).filter((x) => x.lu * 1000 <= now);
    }
  }
  return out;
}

/**
 * Build a fake hass around one or more FakePlants. `onUpdate(hass)` is
 * called with a fresh hass object whenever states change (like HA does).
 */
export function fakeHass(plants, { lang = "de", dark = false, onUpdate, noHistory = false, failPlants = false } = {}) {
  const conn = fakeConnection(plants, { failPlants });
  const build = () => {
    const states = {};
    for (const p of plants) Object.assign(states, p.states());
    const hass = {
      connection: conn,
      states,
      language: lang,
      locale: { language: lang, number_format: "language", time_format: "language" },
      themes: { darkMode: dark },
      dockedSidebar: "docked",
      config: { language: lang },
      user: { is_admin: true, name: "Harness" },
      callService: (domain, service, data) => {
        const p = plants.find((x) => Object.keys(x.states()).includes(data.entity_id)) || plants[0];
        return p.callService(domain, service, data);
      },
      callWS: (msg) => {
        if (msg.type === "history/history_during_period") {
          if (noHistory) return Promise.resolve({});
          return Promise.resolve(makeHistory(plants[0], msg));
        }
        return conn.sendMessagePromise(msg);
      },
    };
    return hass;
  };
  let hass = build();
  for (const p of plants) {
    p.onChange(() => {
      hass = build();
      if (onUpdate) onUpdate(hass);
    });
  }
  return { get hass() { return hass; }, conn, rebuild: () => { hass = build(); if (onUpdate) onUpdate(hass); return hass; } };
}
