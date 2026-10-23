// Pure plant model helpers: capabilities → sections, heat-pump mode, badges,
// role lookups. No DOM, no hass calls (unit-tested in tests/frontend).

export const HP_MODE = {
  1: "heat", 6: "heat",
  2: "cool", 7: "cool",
  3: "dhw", 16: "dhw",
  4: "defrost",
  14: "fault",
  0: "off", 5: "off", 8: "off", 17: "off",
};

/** Heat-pump mode from hp.status: heat | cool | dhw | defrost | fault | off | standby. */
export function hpMode(status) {
  if (status === null || status === undefined || status === "") return "standby";
  const n = Number(status);
  if (!Number.isFinite(n)) return "standby";
  return HP_MODE[n] || "standby";
}

/** Modes in which the compressor runs (fan spins, water heated/cooled). */
export function isProducing(mode) {
  return mode === "heat" || mode === "cool" || mode === "dhw";
}

/**
 * Parse a badge name from the plant payload's `badges` table
 * ("auto_comfort_red", "off_frost_red", "vacation_blue", …).
 * activity: heating (red) | cooling (blue) | idle (grey) | fault (orange) | off.
 */
export function parseBadge(name) {
  const out = { name: name || "unknown", kind: "unknown", preset: null, activity: "idle" };
  if (!name || typeof name !== "string") return out;
  const parts = name.split("_");
  const colour = parts[parts.length - 1];
  out.activity = colour === "red" ? "heating" : colour === "blue" ? "cooling" : colour === "orange" ? "fault" : "idle";
  const head = parts[0];
  if (name === "off_grey") return { ...out, kind: "off", activity: "off" };
  if (head === "off" && parts[1] === "frost") return { ...out, kind: "frost" };
  if (head === "fault") return { ...out, kind: "fault", activity: "fault" };
  if (head === "auto" || head === "heat" || head === "cool") {
    return { ...out, kind: head, preset: parts[1] === "eco" ? "eco" : parts[1] === "comfort" ? "comfort" : null };
  }
  if (head === "vacation" || head === "party") return { ...out, kind: head };
  return { ...out, kind: "unknown" };
}

export function badgeFor(plant, raw) {
  if (raw === null || raw === undefined) return parseBadge(null);
  const table = (plant && plant.badges) || [];
  const name = table[Number(raw)];
  return parseBadge(name || null);
}

/** Circuit icon from its name (user cannot configure it in v1). */
export function circuitKind(name) {
  const n = String(name || "").toLowerCase();
  if (/(fu(ß|ss)boden|fussbod|boden|fbh|floor|underfloor)/.test(n)) return "floor";
  if (/(radiator|heizk(ö|oe)rper|hk-?rad)/.test(n)) return "radiator";
  return "house";
}

export const hcKey = (n, key) => `hc${n}.${key}`;

/** Raw live value for a role key (undefined if absent). */
export function val(live, key) {
  if (!live || !live.values) return undefined;
  const v = live.values[key];
  return v === null ? undefined : v;
}

export function num(live, key) {
  const v = val(live, key);
  if (v === undefined || v === "" || typeof v === "boolean") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

/** Label for an enum role value (labels/options from the payload), else undefined. */
export function enumLabel(plant, key, raw) {
  if (raw === undefined || raw === null) return undefined;
  const role = plant && plant.roles && plant.roles[key];
  if (!role) return undefined;
  const k = String(typeof raw === "boolean" ? Number(raw) : raw);
  if (role.labels && role.labels[k] !== undefined) return role.labels[k];
  if (role.options && role.options[k] !== undefined) return role.options[k];
  return undefined;
}

/**
 * Circuit operating program (hcN.op_program: Comfort*Off*Red*Norm*HeatMan*
 * CoolMan*Eco*Party*Holiday = 0..8) other than the app's Komfort/Eco (0) and
 * Aus (1): a legacy program set by the old app or the generic select entity.
 * Returns { raw, label } (label from the payload, may be undefined) or null.
 */
export function legacyProgram(plant, live, n) {
  const raw = num(live, hcKey(n, "op_program"));
  if (raw === undefined || raw === 0 || raw === 1) return null;
  return { raw, label: enumLabel(plant, hcKey(n, "op_program"), raw) };
}

export function role(plant, key) {
  return (plant && plant.roles && plant.roles[key]) || null;
}

/**
 * Entity id usable for writing: the role says writable, the entity exists
 * and is enabled, and it is of the `domain` whose service will be called.
 */
export function writableEntity(plant, key, domain) {
  const r = role(plant, key);
  if (!r || r.writable !== true || !r.entity_id || r.enabled === false) return null;
  if (!domain || !String(r.entity_id).startsWith(domain + ".")) return null;
  return r.entity_id;
}

/** `id` if it is an entity of `domain`, else null. */
export function ofDomain(id, domain) {
  return id && String(id).startsWith(domain + ".") ? id : null;
}

/** Entity id for display/more-info (may be disabled → null). */
export function displayEntity(plant, key) {
  const r = role(plant, key);
  if (!r || !r.entity_id || r.enabled === false) return null;
  return r.entity_id;
}

/** Live capabilities (per event) override the one-shot plant payload. */
export function capabilities(plant, live) {
  return { ...((plant && plant.capabilities) || {}), ...((live && live.capabilities) || {}) };
}

/**
 * Which UI sections to show, exactly following the plant's capabilities
 * (the app's rules are evaluated by the backend).
 */
export function sections(plant, live, opts = {}) {
  const caps = capabilities(plant, live);
  const bms = !!caps.bms;
  let circuits = bms ? [] : ((plant && plant.circuits) || []).slice().sort((a, b) => a.index - b.index);
  if (Array.isArray(opts.circuits) && opts.circuits.length) {
    const want = new Set(opts.circuits.map(Number));
    circuits = circuits.filter((c) => want.has(Number(c.index)));
  }
  return {
    bms,
    circuits,
    dhw: !bms && !!caps.dhw,
    buffer: !!caps.buffer,
    bufferTemp: !!caps.buffer && !!caps.buffer_temp,
    aux: !!caps.aux,
    auxTemp: !!caps.aux_temp,
    bufferAuxHeater: !!caps.buffer_aux_heater,
    pool: !bms && !!caps.pool,
    silent: !!caps.silent,
    emergency: !!caps.emergency,
    power: !!caps.power,
    cooling: !!caps.cooling || circuits.some((c) => c.cool),
  };
}

/** Structural signature; a change means re-layout (and maybe re-fetch). */
export function layoutSignature(plant, live) {
  const s = sections(plant, live);
  return JSON.stringify([s.circuits.map((c) => [c.index, !!c.heat, !!c.cool]), s.dhw, s.buffer, s.bufferTemp, s.aux, s.pool, s.silent, s.emergency, s.power, s.bms]);
}

/** Circuit name: live text value wins over the one-shot payload. */
export function circuitName(circuit, live) {
  const v = val(live, hcKey(circuit.index, "name"));
  return (typeof v === "string" && v.trim()) || circuit.name || `HC ${circuit.index}`;
}

/** Live COP (heat_output / el_power), only when both > 0.1 kW. */
export function liveCop(live, caps) {
  if (caps && caps.power === false) return undefined;
  const q = num(live, "hp.heat_output");
  const p = num(live, "hp.el_power");
  if (q === undefined || p === undefined || q <= 0.1 || p <= 0.1) return undefined;
  return q / p;
}

/** Is water flowing through the circuit? pump role, else badge activity. */
export function circuitActive(plant, live, n, mode) {
  if (mode === "dhw") return false;
  const pump = val(live, hcKey(n, "pump"));
  if (pump !== undefined) return pump === true || Number(pump) > 0;
  const b = badgeFor(plant, val(live, hcKey(n, "badge")));
  return b.activity === "heating" || b.activity === "cooling";
}

/** Does the circuit currently cool (badge blue, or season summer on a cool-capable circuit)? */
export function circuitCooling(plant, live, circuit, mode) {
  if (!circuit.cool) return false;
  const b = badgeFor(plant, val(live, hcKey(circuit.index, "badge")));
  if (b.activity === "cooling") return true;
  if (b.activity === "heating") return false;
  return mode === "cool";
}

// ---- room setpoints ----------------------------------------------------------
// The app's setpoint screen: normal (Komfort by day) and reduced (Eco, and
// Komfort at night) room setpoints for heating, plus both for cooling on
// cool-capable circuits. The current target is the active one + room_offset.

export const SETPOINTS = [
  { key: "setpoint_normal_heat", cool: false, reduced: false },
  { key: "setpoint_reduced_heat", cool: false, reduced: true },
  { key: "setpoint_normal_cool", cool: true, reduced: false },
  { key: "setpoint_reduced_cool", cool: true, reduced: true },
];

/** Setpoint rows a circuit offers: heating unless heat is off, cooling only with the cooling bit; roles the payload lacks are left out. */
export function setpointRows(plant, circuit) {
  return SETPOINTS.filter((s) => (s.cool ? !!circuit.cool : circuit.heat !== false) && !!role(plant, hcKey(circuit.index, s.key)));
}

/** Does the circuit's current target follow the cooling setpoints (target_selector 1/3)? */
export function targetIsCool(live, n) {
  const sel = num(live, hcKey(n, "target_selector"));
  return sel === 1 || sel === 3;
}

/**
 * Does the selected mode run on the reduced setpoint? Eco (comfort_eco 1 under
 * the app's control, or the legacy Eco program 6) and the legacy "Reduziert"
 * program (2). Komfort and "Normal" (3) follow the time program, whose day
 * value is the normal setpoint; every other state (off, manual, party,
 * holiday) counts as normal too.
 */
export function mainIsReduced(live, n) {
  const prog = num(live, hcKey(n, "op_program"));
  if (prog === 2 || prog === 6) return true;
  if (prog !== undefined && prog !== 0) return false;
  return num(live, hcKey(n, "comfort_eco")) === 1;
}

/**
 * The circuit's setpoint pair for the controls card: heating or cooling per
 * target_selector (cooling only on cool-capable circuits), `main` chosen by
 * the selected mode (never by day/night), `alt` the other one of the pair.
 * { cool, main, alt } with SETPOINTS entries (alt may be null), or null when
 * the payload has no setpoint role for the circuit.
 */
export function setpointPair(plant, live, circuit) {
  const rows = setpointRows(plant, circuit);
  const hasCool = rows.some((s) => s.cool), hasHeat = rows.some((s) => !s.cool);
  if (!hasCool && !hasHeat) return null;
  const cool = hasCool && (!hasHeat || targetIsCool(live, circuit.index));
  const pair = rows.filter((s) => s.cool === cool);
  const reduced = mainIsReduced(live, circuit.index);
  const main = pair.find((s) => s.reduced === reduced) || pair[0];
  return { cool, main, alt: pair.find((s) => s !== main) || null };
}

export const ACTIVE_TOLERANCE = 0.05;

/**
 * Setpoint key ("setpoint_normal_heat", …) the circuit's current target is
 * derived from right now: value + room_offset equals room_target_heat/_cool
 * (per target_selector) within 0.05 K. null when none matches (frost
 * protection, party, holiday, values missing). If normal and reduced are
 * equal, the reduced one wins under Eco or the legacy "Reduziert" program.
 */
export function activeSetpoint(plant, live, circuit) {
  const n = circuit.index;
  const cool = targetIsCool(live, n);
  const target = num(live, hcKey(n, cool ? "room_target_cool" : "room_target_heat"));
  if (target === undefined) return null;
  const off = num(live, hcKey(n, "room_offset")) ?? 0;
  const hits = setpointRows(plant, circuit).filter((s) => {
    const v = num(live, hcKey(n, s.key));
    return s.cool === cool && v !== undefined && Math.abs(v + off - target) <= ACTIVE_TOLERANCE + 1e-9;
  });
  if (!hits.length) return null;
  if (hits.length === 1) return hits[0].key;
  const prog = num(live, hcKey(n, "op_program"));
  const reduced = num(live, hcKey(n, "comfort_eco")) === 1 || prog === 2 || prog === 6;
  return (hits.find((s) => s.reduced === reduced) || hits[0]).key;
}

/** Pick the plant for a card config ({plant} | {device_id}), first plant otherwise. */
export function pickPlant(plants, config = {}) {
  if (!plants || !plants.length) return null;
  if (config.plant) {
    const p = plants.find((x) => x.plant_id === config.plant || x.entry_id === config.plant);
    if (p) return p;
  }
  if (config.device_id) {
    const p = plants.find((x) => x.device_id === config.device_id || (x.circuits || []).some((c) => c.device_id === config.device_id));
    if (p) return p;
  }
  return plants[0];
}

/** Circuit indices for a device_id that is a circuit device (card shows only that circuit). */
export function circuitsForDevice(plant, deviceId) {
  if (!plant || !deviceId || plant.device_id === deviceId) return null;
  const c = (plant.circuits || []).find((x) => x.device_id === deviceId);
  return c ? [c.index] : null;
}

// ---- units -----------------------------------------------------------------
// Feed values and number-role metadata (unit/min/max/step) are in the
// controller's native unit. Entities may be shown by HA in another unit
// (`display_unit`, e.g. °F) and number.set_value expects that unit, so a
// value is converted exactly once, at the service call. Deltas (unit K, e.g.
// offsets) are never converted. Climate/water_heater attributes are in HA's
// temperature unit (hass.config.unit_system.temperature).

const TEMP_UNITS = { "°C": "C", "℃": "C", "°F": "F", "℉": "F" };

/** Absolute temperature °C ↔ °F; undefined when not convertible. */
export function convertTemp(v, from, to) {
  const a = TEMP_UNITS[from], b = TEMP_UNITS[to];
  if (typeof v !== "number" || !a || !b) return undefined;
  if (a === b) return v;
  return a === "C" ? (v * 9) / 5 + 32 : ((v - 32) * 5) / 9;
}

/** A temperature difference (K) in display units (°F degrees are 5/9 K). */
export function convertDelta(v, from, to) {
  if (typeof v !== "number") return undefined;
  const a = TEMP_UNITS[from] || (from === "K" ? "C" : null), b = TEMP_UNITS[to] || (to === "K" ? "C" : null);
  if (!a || !b || a === b) return v;
  return a === "C" ? (v * 9) / 5 : (v * 5) / 9;
}

const round4 = (v) => Math.round(v * 1e4) / 1e4;

/**
 * Native value → the value number.set_value expects for this role's entity.
 * null = units differ in a way we cannot convert (refuse to write).
 */
export function toServiceValue(r, v) {
  const du = r && r.display_unit;
  if (!du || du === r.unit || r.unit === "K") return v;
  const out = convertTemp(v, r.unit, du);
  return out === undefined ? null : round4(out);
}

/**
 * toServiceValue, checked against the unit the entity is in right now (its
 * state's unit_of_measurement). `stale`: the role's display_unit no longer
 * matches the entity (HA's units changed after the payload was loaded), so
 * the conversion would be wrong: reload the payload before writing.
 */
export function serviceValue(r, v, liveUnit) {
  if (!r || !TEMP_UNITS[r.unit]) return { value: v, stale: false };
  if (liveUnit !== (r.display_unit || r.unit)) return { value: null, stale: true };
  return { value: toServiceValue(r, v), stale: false };
}

/**
 * Room sensor readings of a circuit (availability bit 32 room sensor, 64
 * humidity), or null without a room sensor. `temp` is in HA's unit `unit`:
 * the climate entity's current_temperature, else the feed's native value
 * converted. `hum` in %. `dew` (native °C, like the flow values) only for
 * cool-capable circuits with a humidity sensor.
 */
export function roomReading(circuit, live, climateState, unit = "°C") {
  if (!circuit || !circuit.room_sensor) return null;
  const n = circuit.index;
  const a = (climateState && climateState.state !== "unavailable" && climateState.attributes) || {};
  const temp = typeof a.current_temperature === "number" ? a.current_temperature : convertTemp(num(live, hcKey(n, "room_temp")), "°C", unit);
  let hum, dew;
  if (circuit.humidity) {
    hum = typeof a.current_humidity === "number" ? a.current_humidity : num(live, hcKey(n, "humidity"));
    if (circuit.cool) dew = num(live, hcKey(n, "dew_point"));
  }
  if (temp === undefined && hum === undefined) return null;
  return { temp, unit, hum, dew };
}

/** HA's temperature unit (climate / water_heater attributes are in it). */
export function haTempUnit(hass) {
  const u = hass && hass.config && hass.config.unit_system && hass.config.unit_system.temperature;
  return TEMP_UNITS[u] ? u : "°C";
}
