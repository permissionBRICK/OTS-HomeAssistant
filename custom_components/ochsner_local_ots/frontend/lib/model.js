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
