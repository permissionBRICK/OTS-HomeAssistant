// Time program model: switch points ↔ Normal periods, next change, editing helpers.
import test from "node:test";
import assert from "node:assert/strict";
import {
  DAY, ECO, NORMAL, dayNames, dayPoints, haNow, hasNightSetback, hm, nextChange, normalPeriods, normalizePeriods,
  parseHm, pointsFor, samePeriods, segments, storedPeriods, suggestPeriod, writeCount,
} from "../../custom_components/ochsner_local_ots/frontend/lib/schedule.js";

const HOUSE = [[0, 0], [240, 1], [1200, 0], [null, 0], [null, 1], [null, 0]];
const program = (days, def = 1) => ({ supported: true, complete: true, default: def, days });

test("the house program reads as Normal 04:00-20:00", () => {
  const pts = dayPoints(HOUSE);
  assert.deepEqual(pts, [[0, 0], [240, 1], [1200, 0]]);
  assert.deepEqual(segments(pts, 1), [{ s: 0, e: 240, v: ECO }, { s: 240, e: 1200, v: NORMAL }, { s: 1200, e: DAY, v: ECO }]);
  assert.deepEqual(normalPeriods(pts, 1), [[240, 1200]]);
});

test("before the first switch point the default applies", () => {
  // No 00:00 point: the Normal default runs until 06:00.
  assert.deepEqual(normalPeriods([[360, 0], [960, 1]], 1), [[0, 360], [960, DAY]]);
  assert.deepEqual(normalPeriods([], 1), [[0, DAY]]);
  assert.deepEqual(normalPeriods([], 0), []);
});

test("periods become the fewest explicit switch points", () => {
  // Default Normal: a day that starts at Night needs the 00:00 point.
  assert.deepEqual(pointsFor([[240, 1200]], 1), [[0, 0], [240, 1], [1200, 0]]);
  assert.deepEqual(pointsFor([[240, 1200]], 0), [[240, 1], [1200, 0]]);
  // Starting at midnight / running to midnight needs no extra point.
  assert.deepEqual(pointsFor([[0, 480], [960, DAY]], 1), [[480, 0], [960, 1]]);
  assert.deepEqual(pointsFor([], 1), [[0, 0]]);
  assert.deepEqual(pointsFor([[0, DAY]], 1), []);
  assert.deepEqual(pointsFor([[0, DAY]], 0), [[0, 1]]);
  // Three periods with Night around them: 7 points under a Normal default.
  assert.equal(pointsFor([[300, 480], [720, 780], [960, 1320]], 1).length, 7);
  assert.equal(pointsFor([[300, 480], [720, 780], [960, 1320]], 0).length, 6);
  // Round trip
  for (const def of [0, 1]) {
    for (const ps of [[], [[0, DAY]], [[240, 1200]], [[0, 300], [600, 900], [1200, DAY]]]) {
      assert.deepEqual(normalPeriods(pointsFor(ps, def), def), ps);
    }
  }
});

test("normalize merges, sorts and clips", () => {
  assert.deepEqual(normalizePeriods([[900, 1000], [100, 300], [250, 400], [400, 500], [-5, 0], [1400, 2000]]), [[100, 500], [900, 1000], [1400, DAY]]);
  assert.ok(samePeriods([[1, 2], [2, 3]], [[1, 3]]));
});

test("write count only counts changed members", () => {
  assert.equal(writeCount(HOUSE, [[0, 0], [240, 1], [1200, 0]]), 0);
  assert.equal(writeCount(HOUSE, [[0, 0], [240, 1], [1320, 0]]), 1);
  // new slot 4 with a value that differs from the stored unused value
  assert.equal(writeCount(HOUSE, [[0, 0], [240, 1], [480, 0], [960, 1], [1200, 0]]), 5);
  assert.equal(writeCount(HOUSE, []), 3);
});

test("night setback detection", () => {
  assert.ok(hasNightSetback(program(Array.from({ length: 7 }, () => HOUSE))));
  const none = Array.from({ length: 7 }, () => [[null, 0], [null, 0], [null, 0], [null, 0], [null, 0], [null, 0]]);
  assert.ok(!hasNightSetback(program(none, 1)));
  assert.ok(hasNightSetback(program(none, 0)));
  assert.ok(!hasNightSetback({ supported: false }));
  assert.ok(!hasNightSetback(null));
  assert.deepEqual(storedPeriods(program([HOUSE, null]), 0), [[240, 1200]]);
  assert.equal(storedPeriods(program([HOUSE, null]), 1), null);
});

test("next change looks ahead across days", () => {
  const week = (fn) => (d) => fn(d);
  const house = week(() => [[240, 1200]]);
  assert.deepEqual(nextChange(house, 6, 627), { value: NORMAL, day: 0, minute: 1200 });
  assert.deepEqual(nextChange(house, 6, 1260), { value: ECO, day: 1, minute: 240 });
  assert.deepEqual(nextChange(house, 6, 100), { value: ECO, day: 0, minute: 240 });
  // Normal all week
  assert.deepEqual(nextChange(week(() => [[0, DAY]]), 2, 600), { value: NORMAL, day: null, minute: null });
  // Normal Friday until midnight, Saturday from 00:00 until 02:00 → until Saturday 02:00
  const fri = (d) => (d === 4 ? [[1200, DAY]] : d === 5 ? [[0, 120]] : []);
  assert.deepEqual(nextChange(fri, 4, 1300), { value: NORMAL, day: 1, minute: 120 });
  // Night all weekend from Friday 22:00 until Monday 06:00
  const wk = (d) => (d < 5 ? [[360, 1320]] : []);
  assert.deepEqual(nextChange(wk, 4, 1380), { value: ECO, day: 3, minute: 360 });
});

test("time helpers", () => {
  assert.equal(hm(0), "00:00");
  assert.equal(hm(390), "06:30");
  assert.equal(hm(DAY), "24:00");
  assert.equal(parseHm("06:30"), 390);
  assert.equal(parseHm("00:00", true), DAY);
  assert.equal(parseHm("00:00"), 0);
  assert.equal(parseHm("24:00"), DAY);
  assert.equal(parseHm("25:00"), null);
  assert.equal(parseHm(""), null);
  assert.deepEqual(suggestPeriod([[240, 1200]]), [1260, 1380]);
  assert.deepEqual(suggestPeriod([]), [360, 1320]);
  assert.deepEqual(suggestPeriod([[360, 480], [960, 1320]]), [540, 720]);
  assert.equal(suggestPeriod([[0, DAY]]), null);
});

test("now in Home Assistant's time zone", () => {
  // 2026-10-25 09:27 UTC is Sunday 10:27 in Vienna (CET).
  const d = new Date(Date.UTC(2026, 9, 25, 9, 27));
  assert.deepEqual(haNow({ config: { time_zone: "Europe/Vienna" } }, d), { day: 6, minute: 627 });
  assert.deepEqual(haNow({ config: { time_zone: "America/New_York" } }, d), { day: 6, minute: 5 * 60 + 27 });
  assert.deepEqual(dayNames("de", "short").slice(0, 2), ["Mo", "Di"]);
  assert.equal(dayNames("en", "long")[6], "Sunday");
});
