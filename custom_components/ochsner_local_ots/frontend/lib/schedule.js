// Heating-circuit time programs (pure helpers, no DOM; unit-tested in tests/frontend).
//
// The controller stores per weekday six switch points [minute, value]
// (value 1 = Normal setpoint, 0 = Eco / Night) and per circuit a default
// value that applies before a day's first switch point. The UI edits
// "Normal periods" on a Night background and turns them back into the
// fewest switch points, always explicit (never relying on the default
// being Night).

export const NORMAL = 1;
export const ECO = 0;
export const SLOTS = 6;
export const DAY = 1440;
export const STEP = 15; // drag snap, minutes

/** Used switch points of a day's six slots ([minute|null, value]), sorted by time. */
export function dayPoints(slots) {
  if (!Array.isArray(slots)) return [];
  return slots
    .filter((s) => Array.isArray(s) && Number.isInteger(s[0]) && s[0] >= 0 && s[0] < DAY)
    .map((s) => [s[0], Number(s[1]) === NORMAL ? NORMAL : ECO])
    .sort((a, b) => a[0] - b[0]);
}

/** The day as consecutive segments {s, e, v} covering 0..1440. */
export function segments(points, def) {
  const out = [];
  let cur = Number(def) === NORMAL ? NORMAL : ECO;
  let at = 0;
  for (const [m, v] of points) {
    if (m > at) {
      out.push({ s: at, e: m, v: cur });
      at = m;
    }
    cur = v;
  }
  out.push({ s: at, e: DAY, v: cur });
  const merged = [];
  for (const seg of out) {
    const last = merged[merged.length - 1];
    if (last && last.v === seg.v) last.e = seg.e;
    else merged.push({ ...seg });
  }
  return merged;
}

/** Normal periods [[start, end]] of a day's points (end may be 1440). */
export function normalPeriods(points, def) {
  return segments(points, def).filter((s) => s.v === NORMAL).map((s) => [s.s, s.e]);
}

/** Sorted, clipped, non-empty periods; overlapping or touching ones merged. */
export function normalizePeriods(periods) {
  const list = (periods || [])
    .map(([s, e]) => [Math.max(0, Math.min(DAY, Math.round(s))), Math.max(0, Math.min(DAY, Math.round(e)))])
    .filter(([s, e]) => e > s)
    .sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const p of list) {
    const last = out[out.length - 1];
    if (last && p[0] <= last[1]) last[1] = Math.max(last[1], p[1]);
    else out.push([...p]);
  }
  return out;
}

/** The fewest switch points that give `periods` (Normal) on a Night background. */
export function pointsFor(periods, def) {
  const segs = [];
  let at = 0;
  for (const [s, e] of normalizePeriods(periods)) {
    if (s > at) segs.push([at, ECO]);
    segs.push([s, NORMAL]);
    at = e;
  }
  if (at < DAY) segs.push([at, ECO]);
  const out = [];
  let cur = Number(def) === NORMAL ? NORMAL : ECO;
  for (const [m, v] of segs) {
    if (v !== cur) out.push([m, v]);
    cur = v;
  }
  return out;
}

/** Controller members a write of `points` changes in a day's stored slots. */
export function writeCount(slots, points) {
  let n = 0;
  for (let i = 0; i < SLOTS; i++) {
    const cur = (slots && slots[i]) || [null, null];
    const p = points[i];
    if ((p ? p[0] : null) !== cur[0]) n++;
    if (p && p[1] !== cur[1]) n++;
  }
  return n;
}

export const samePeriods = (a, b) => JSON.stringify(normalizePeriods(a)) === JSON.stringify(normalizePeriods(b));

/** Normal periods of a stored day (null when the day could not be read). */
export function storedPeriods(program, day) {
  const slots = program && program.days ? program.days[day] : null;
  return slots ? normalPeriods(dayPoints(slots), program.default) : null;
}

/** Does any readable day of the week run at Night for a while? */
export function hasNightSetback(program) {
  if (!program || !program.supported || !Array.isArray(program.days)) return false;
  return program.days.some((slots) => slots && segments(dayPoints(slots), program.default).some((s) => s.v === ECO));
}

/** Value (NORMAL/ECO) at `minute` of `day` (0 = Monday), from `periodsOf(day)`. */
export function valueAt(periodsOf, day, minute) {
  const ps = periodsOf(day) || [];
  return ps.some(([s, e]) => minute >= s && minute < e) ? NORMAL : ECO;
}

/**
 * What runs now and until when: { value, day, minute } where day/minute is
 * the next change (day relative: 0 = today, 1 = tomorrow, …), or
 * { value, day: null } when the value never changes within a week.
 */
export function nextChange(periodsOf, day, minute) {
  const value = valueAt(periodsOf, day, minute);
  for (let ahead = 0; ahead < 8; ahead++) {
    const d = (day + ahead) % 7;
    const segs = segments(pointsFor(periodsOf(d) || [], ECO), ECO);
    for (const seg of segs) {
      if (ahead === 0 && seg.e <= minute) continue;
      if (seg.v !== value) return { value, day: ahead, minute: seg.s };
    }
  }
  return { value, day: null, minute: null };
}

/** Day (0 = Monday) and minute now in Home Assistant's time zone (the controller's clock). */
export function haNow(hass, date = new Date()) {
  const tz = hass && hass.config && hass.config.time_zone;
  try {
    const parts = new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23" }).formatToParts(date);
    const get = (t) => (parts.find((p) => p.type === t) || {}).value;
    const day = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].indexOf(get("weekday"));
    const minute = (Number(get("hour")) % 24) * 60 + Number(get("minute"));
    if (day >= 0 && Number.isFinite(minute)) return { day, minute };
  } catch (_e) { /* unknown time zone: browser time */ }
  return { day: (date.getDay() + 6) % 7, minute: date.getHours() * 60 + date.getMinutes() };
}

/** "06:30"; 1440 → "24:00". */
export function hm(minute) {
  const m = Math.max(0, Math.min(DAY, Math.round(minute)));
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** "HH:MM" → minutes, null if malformed. An end of "00:00" means midnight (1440) when `end`. */
export function parseHm(text, end = false) {
  const m = /^(\d{1,2}):(\d{2})/.exec(String(text || ""));
  if (!m) return null;
  const h = Number(m[1]), mi = Number(m[2]);
  if (h > 24 || mi > 59 || (h === 24 && mi > 0)) return null;
  const v = h * 60 + mi;
  return end && v === 0 ? DAY : v;
}

/** A new Normal period in the largest Night gap (at least an hour), or null. */
export function suggestPeriod(periods) {
  const ps = normalizePeriods(periods);
  if (!ps.length) return [360, 1320]; // Night all day: a usual day
  let best = null, at = 0;
  for (const [s, e] of [...ps, [DAY, DAY]]) {
    // the later gap wins a tie (an evening rather than a night period)
    if (s - at >= 60 && (!best || s - at >= best[1] - best[0])) best = [at, s];
    at = e;
  }
  if (!best) return null;
  const len = best[1] - best[0];
  const pad = len >= 240 ? 60 : 0;
  const s = best[0] + pad, e = Math.min(best[1] - pad, s + Math.max(60, Math.min(180, len - 2 * pad)));
  return [Math.round(s / STEP) * STEP, Math.round(e / STEP) * STEP];
}

/** Starting points offered when no night setback exists. */
export const PRESETS = {
  night: () => Array.from({ length: 7 }, () => [[360, 1320]]),
  workdays: () => Array.from({ length: 7 }, (_, d) => (d < 5 ? [[360, 480], [960, 1320]] : [[420, 1320]])),
};

/** Weekday names Monday..Sunday ("short": "Mo"/"Mon", "long"). */
export function dayNames(lang, style = "short") {
  const out = [];
  for (let d = 0; d < 7; d++) {
    const date = new Date(Date.UTC(2024, 0, 1 + d)); // 2024-01-01 was a Monday
    let name;
    try {
      name = new Intl.DateTimeFormat(lang, { weekday: style, timeZone: "UTC" }).format(date);
    } catch (_e) {
      name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d];
    }
    out.push(name.replace(/\.$/, ""));
  }
  return out;
}
