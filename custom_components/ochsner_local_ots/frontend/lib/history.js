// 24 h history: fetch via the recorder websocket API and aggregate into
// time-weighted hourly means (kW → kWh per hour) and status segments.

const HOUR = 3600 * 1000;

export function windowFor(now = Date.now(), hours = 24) {
  const end = now;
  const start = Math.floor(now / HOUR) * HOUR - (hours - 1) * HOUR;
  return { start, end, hours };
}

export async function fetchHistory(hass, entityIds, win) {
  const ids = entityIds.filter(Boolean);
  if (!ids.length) return {};
  const res = await hass.callWS({
    type: "history/history_during_period",
    start_time: new Date(win.start).toISOString(),
    end_time: new Date(win.end).toISOString(),
    entity_ids: ids,
    minimal_response: true,
    no_attributes: true,
    significant_changes_only: false,
  });
  return res || {};
}

/** Normalise compressed ({s, lu}) or full ({state, last_updated}) rows to [{t, s}]. */
export function toSeries(rows) {
  if (!Array.isArray(rows)) return [];
  const out = [];
  for (const r of rows) {
    if (!r) continue;
    const s = r.s !== undefined ? r.s : r.state;
    let t = r.lu !== undefined ? r.lu * 1000 : r.lc !== undefined ? r.lc * 1000 : Date.parse(r.last_updated || r.last_changed);
    if (!Number.isFinite(t)) continue;
    out.push({ t, s });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

const numeric = (s) => {
  if (s === null || s === undefined || s === "" || s === "unavailable" || s === "unknown") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
};

/**
 * Energy per hour bucket (kWh) from a piecewise-constant kW series:
 * integral over the bucket (∩ [.., end]) / 1 h. Unknown/unavailable = gap.
 */
export function hourlyEnergy(series, win) {
  const out = new Array(win.hours).fill(0);
  const known = new Array(win.hours).fill(false);
  for (let i = 0; i < series.length; i++) {
    const v = numeric(series[i].s);
    if (v === undefined) continue;
    const a = Math.max(series[i].t, win.start);
    const b = Math.min(i + 1 < series.length ? series[i + 1].t : win.end, win.end);
    if (b <= a) continue;
    let t = a;
    while (t < b) {
      const k = Math.floor((t - win.start) / HOUR);
      const bucketEnd = win.start + (k + 1) * HOUR;
      const seg = Math.min(b, bucketEnd) - t;
      if (k >= 0 && k < win.hours) {
        out[k] += (Math.max(0, v) * seg) / HOUR;
        known[k] = true;
      }
      t += seg;
    }
  }
  return { kwh: out, known };
}

/** Status segments [{a, b, s}] clipped to the window (a/b in ms). */
export function segments(series, win) {
  const out = [];
  for (let i = 0; i < series.length; i++) {
    const a = Math.max(series[i].t, win.start);
    const b = Math.min(i + 1 < series.length ? series[i + 1].t : win.end, win.end);
    if (b <= a) continue;
    const last = out[out.length - 1];
    if (last && last.s === series[i].s && last.b === a) last.b = b;
    else out.push({ a, b, s: series[i].s });
  }
  return out;
}

/** Map a status history state (raw number or label in any language) to a mode. */
export function statusResolver(labels, hpModeFn) {
  const byLabel = new Map();
  for (const [raw, label] of Object.entries(labels || {})) byLabel.set(String(label).toLowerCase(), Number(raw));
  return (s) => {
    if (s === null || s === undefined || s === "unavailable" || s === "unknown") return "unknown";
    const n = Number(s);
    if (s !== "" && Number.isFinite(n)) return hpModeFn(n);
    const lower = String(s).toLowerCase();
    if (byLabel.has(lower)) return hpModeFn(byLabel.get(lower));
    if (/abtau|defrost/.test(lower)) return "defrost";
    if (/warmwasser|ww |dhw|hot water/.test(lower)) return "dhw";
    if (/k(ü|ue)hl|cool/.test(lower)) return "cool";
    if (/heiz|heat/.test(lower)) return "heat";
    if (/st(ö|oe)rung|fault|error/.test(lower)) return "fault";
    if (/aus|off/.test(lower)) return "off";
    return "standby";
  };
}

/** Compressor runs from mode segments: count and mean duration (minutes). */
export function runStats(modeSegs) {
  const producing = (m) => m === "heat" || m === "dhw" || m === "cool";
  let runs = 0, total = 0, cur = null;
  for (const seg of modeSegs) {
    if (producing(seg.mode)) {
      if (cur && cur.b === seg.a) cur.b = seg.b;
      else {
        if (cur) { runs++; total += cur.b - cur.a; }
        cur = { a: seg.a, b: seg.b };
      }
    } else if (cur && seg.mode === "defrost" && cur.b === seg.a) {
      cur.b = seg.b; // a defrost interrupts but doesn't end a run
    } else if (cur) {
      runs++; total += cur.b - cur.a; cur = null;
    }
  }
  if (cur) { runs++; total += cur.b - cur.a; }
  return { runs, avgMinutes: runs ? total / runs / 60000 : undefined };
}

export function hasData(series) {
  return series.some((p) => numeric(p.s) !== undefined);
}
