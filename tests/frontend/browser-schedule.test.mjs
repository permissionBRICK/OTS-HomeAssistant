// Time program in a real browser: the circuit tiles' rules (both setpoints and
// today's bar only in Komfort with a night setback), when the full dashboard
// shows the card, the week grid, editing by keyboard, mouse and typing,
// presets, copying, Apply/Discard.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { CHROME, browser, on, sleep, writes } from "./harness/browser-helpers.mjs";

const skip = !CHROME && "no chromium";
let b;
before(async () => { if (CHROME) b = await browser(); });
after(async () => { if (b) await b.close(); });

const tile = (n) => `.hc[data-key="hc${n}"]`;
const calls = (page, type = "ochsner_local_ots/schedule/set") => page.eval(`(harness.fake.conn.scheduleCalls || []).filter((m) => m.type === ${JSON.stringify(type)})`);
const count = (page, c, sel) => page.eval(`${c.root}.querySelectorAll(${JSON.stringify(sel)}).length`);
const hidden = (page, c) => page.eval(`${c.root}.querySelector(".card-schedule").hidden`);
const tabOn = (page, c) => page.eval(`${c.root}.querySelector(".card-schedule .tabs button.on").dataset.c`);
const setValues = (page, vals) => page.eval(`(() => { Object.assign(harness.plant.values, ${JSON.stringify(vals)}); harness.plant.push(); return true; })()`);

test("Komfort with a night setback: both setpoints and today's bar; Reduziert: one setpoint, no bar", { skip }, async () => {
  const page = await b.open("variant=house&view=all&latency=30", { width: 1280, height: 1600 });
  const c = on(page);
  for (const n of [1, 2]) {
    assert.ok(await c.exists(`${tile(n)} .sp.alt .stepper`), `hc${n} night setpoint`);
    assert.match(await c.text(`${tile(n)} .tstrip`), /^\s*(Normal|Nacht) bis /, `hc${n} strip`);
    assert.equal(await count(page, c, `${tile(n)} .tstrip .mini > span`), 1, "one Normal block today (04:00-20:00)");
  }
  assert.ok(!(await c.exists(`${tile(3)} .sp.alt`)) && !(await c.exists(`${tile(3)} .tstrip`)));
  // the time program card: one tab per circuit, seven days, a block per day
  assert.equal(await count(page, c, ".card-schedule .tabs button"), 3);
  assert.equal(await count(page, c, ".card-schedule .drow"), 7);
  assert.equal(await count(page, c, ".card-schedule .blk"), 7);
  assert.ok(!(await c.exists(".card-schedule [data-act='tphide']")), "nothing to close");
  assert.match(await c.text(".card-schedule .drow[data-d='0'] .blk"), /04:00–20:00/);
  // Wintergarten runs Reduziert: its tab explains that the program is paused
  await c.click(".card-schedule .tabs button[data-c='3']");
  assert.match(await c.text(".card-schedule .tpbanner"), /Reduziert ist aktiv: Wintergarten bleibt ganztägig auf Nacht 10,0°/);
  // the strip opens the program of its circuit
  await c.click(`${tile(2)} .tpgo`);
  assert.equal(await page.eval(`${c.root}.querySelector(".card-schedule .tabs button.on").dataset.c`), "2");
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(await calls(page), []);
  assert.deepEqual(page.errors, []);
});

test("no night setback anywhere: the full dashboard shows the card only once a tile's set-up link asks for it", { skip }, async () => {
  const page = await b.open("variant=house&view=all&latency=30&schedule=none&lang=en", { width: 1280, height: 1600 });
  const c = on(page);
  assert.equal(await hidden(page, c), true, "no card while nothing is set up");
  assert.ok(!(await c.exists(`${tile(1)} .sp.alt .stepper`)), "no night setpoint");
  assert.match(await c.text(`${tile(1)} .tplink`), /Set up time program/);
  assert.ok(!(await c.exists(`${tile(1)} .tstrip`)), "no bar");
  await c.click(`${tile(1)} .tplink`);
  await sleep(100);
  assert.equal(await hidden(page, c), false, "the link shows the card");
  assert.ok(await c.exists(`${tile(1)} .sp.alt .stepper`), "night setpoint shown once asked");
  assert.match(await c.text(`${tile(1)} .tstrip`), /No night setback yet/);
  assert.ok(!(await c.exists(`${tile(2)} .sp.alt .stepper`)), "only the circuit asked for");
  assert.equal(await tabOn(page, c), "1");
  assert.match(await c.text(".card-schedule .tpempty"), /Fußboden runs at Normal 21\.5° around the clock/);
  // a preset stages the whole week; nothing is written yet
  await c.click(".card-schedule [data-act='tppreset'][data-p='night']");
  assert.match(await c.text(".card-schedule .pendbar"), /Fußboden · Mon, Tue, Wed, Thu, Fri, Sat, Sun: 06:00–22:00/);
  assert.match(await c.text(".card-schedule .pendbar .cost"), /28 controller writes/);
  // feed updates, a fresh read of the programs and the clock tick leave the card and the edits alone
  await setValues(page, {});
  await page.eval("harness.el.store.fetchSchedules(harness.plant.plantId, true).then(() => true)");
  await page.eval("harness.el.requestRender(); true");
  await sleep(80);
  assert.equal(await hidden(page, c), false);
  assert.ok(await c.exists(".card-schedule .pendbar"), "still pending");
  // Discard drops the edits but keeps the card
  await c.click(".card-schedule [data-act='tpdiscard']");
  assert.ok(!(await c.exists(".card-schedule .pendbar")));
  assert.equal(await hidden(page, c), false, "Discard keeps the card");
  // ✕ puts it away with what was staged, and folds the tile back to its link
  await c.click(".card-schedule [data-act='tppreset'][data-p='workdays']");
  assert.ok(await c.exists(".card-schedule .pendbar"));
  assert.equal(await page.eval(`${c.root}.querySelector("[data-act='tphide']").getAttribute("aria-label")`), "Hide time program");
  await c.click(".card-schedule [data-act='tphide']");
  assert.equal(await hidden(page, c), true, "closed");
  assert.ok(!(await c.exists(`${tile(1)} .sp.alt .stepper`)) && (await c.exists(`${tile(1)} .tplink`)), "tile back to the link");
  // asked again, from the other circuit: its tab, nothing pending
  await c.click(`${tile(2)} .tplink`);
  await sleep(100);
  assert.equal(await hidden(page, c), false);
  assert.equal(await tabOn(page, c), "2");
  assert.ok(!(await c.exists(".card-schedule .pendbar")), "the closed edits are gone");
  assert.deepEqual(await calls(page), []);
  assert.deepEqual(page.errors, []);
});

test("full dashboard: a saved night setback keeps the card; removing the last one leaves it open until closed", { skip }, async () => {
  const page = await b.open("variant=house&view=all&latency=30&schedule=none&lang=en", { width: 1280, height: 1600 });
  const c = on(page);
  await c.click(`${tile(1)} .tplink`);
  await sleep(100);
  await c.click(".card-schedule [data-act='tppreset'][data-p='night']");
  await c.click(".card-schedule [data-act='tpapply']");
  await sleep(250);
  assert.equal((await calls(page)).length, 1);
  assert.match(await c.text(".card-schedule .note.ok"), /Time program saved/);
  assert.ok(!(await c.exists(".card-schedule [data-act='tphide']")), "a night setback exists: no ✕");
  assert.match(await c.text(`${tile(1)} .tstrip`), /(Normal|Night) until/);
  // back to Normal all day: the card stays on screen, now with its ✕
  await c.click(".card-schedule .drow[data-d='0']");
  await c.click(".card-schedule [data-act='tpclear']");
  await c.click(".card-schedule [data-act='tpapply']");
  await sleep(250);
  assert.equal((await calls(page)).length, 2);
  assert.equal(await hidden(page, c), false);
  assert.ok(await c.exists(".card-schedule .tpempty"));
  await c.click(".card-schedule [data-act='tphide']");
  assert.equal(await hidden(page, c), true);
  assert.deepEqual(page.errors, []);
});

test("the separate time program card shows without a night setback, with no ✕", { skip }, async () => {
  const page = await b.open("variant=house&view=schedule&latency=30&schedule=none&lang=en", { width: 760, height: 1200 });
  const c = on(page);
  assert.equal(await hidden(page, c), false);
  assert.ok(await c.exists(".tpempty"));
  assert.ok(!(await c.exists("[data-act='tphide']")));
  assert.deepEqual(page.errors, []);
});

test("the panel opened from a tile on another dashboard shows the card for that circuit", { skip }, async () => {
  const page = await b.open("variant=house&view=panel&latency=30&schedule=none&lang=en#time-program-2", { width: 1280, height: 1600 });
  const c = on(page, 'harness.el.shadowRoot.querySelector("ochsner-heatpump-card").shadowRoot');
  await sleep(100);
  assert.equal(await hidden(page, c), false);
  assert.equal(await tabOn(page, c), "2");
  assert.ok(await c.exists(".card-schedule [data-act='tphide']"));
  assert.deepEqual(page.errors, []);
});

test("editing Saturday by keyboard and mouse; Apply writes explicit switch points once", { skip }, async () => {
  const page = await b.open("variant=house&view=schedule&latency=30", { width: 760, height: 1200 });
  const c = on(page);
  await c.click(".drow[data-d='5']");
  assert.ok(await c.exists(".tpeditor"), "editor open");
  assert.equal(await count(page, c, ".hdl"), 2, "handles on the selected day");
  // end 20:00 → 21:00 with the arrow keys (4 × 15 min)
  await c.focusKey(".hdl[data-edge='e']", "ArrowRight", "ArrowRight", "ArrowRight", "ArrowRight");
  assert.match(await c.text(".drow[data-d='5'] .blk.pend"), /04:00–21:00/);
  // start 04:00 → 06:00 by dragging (2 h of the bar)
  const w = await page.eval(`${c.root}.querySelector(".drow[data-d='5'] .bar").getBoundingClientRect().width`);
  await c.drag(".hdl[data-edge='s']", (w * 120) / 1440, 0, 10);
  assert.match(await c.text(".drow[data-d='5'] .blk.pend"), /06:00–21:00/);
  assert.match(await c.text(".pendbar"), /Fußboden · Sa: 06:00–21:00/);
  assert.match(await c.text(".pendbar .cost"), /2 Schreibvorgänge/);
  assert.ok(await c.exists(".tabs button[data-c='1'] .pdot"), "pending dot on the tab");
  assert.deepEqual(await calls(page), [], "nothing before Apply");
  await c.click("[data-act='tpapply']");
  await sleep(250);
  const sent = await calls(page);
  assert.equal(sent.length, 1);
  assert.deepEqual(sent[0].days, { 5: [[0, 0], [360, 1], [1260, 0]] });
  assert.equal(sent[0].circuit, 1);
  assert.ok(!(await c.exists(".pendbar")), "saved");
  assert.match(await c.text(".note.ok"), /Zeitprogramm gespeichert \(2 Schreibvorgänge\)/);
  assert.match(await c.text(".drow[data-d='5'] .blk"), /06:00–21:00/, "shows what was read back");
  assert.deepEqual(page.errors, []);
});

test("typing times, adding and removing periods, the six switch point limit", { skip }, async () => {
  const page = await b.open("variant=house&view=schedule&latency=30&lang=en", { width: 760, height: 1300 });
  const c = on(page);
  await c.click(".drow[data-d='0']");
  const type = (i, edge, v) => page.eval(`(() => { const e = ${c.root}.querySelector("input[data-i='${i}'][data-edge='${edge}']"); e.value = ${JSON.stringify(v)}; e.dispatchEvent(new Event("change", { bubbles: true })); return true; })()`);
  await type(0, "e", "03:00");
  await sleep(60);
  assert.match(await c.text(".tpeditor .note.err"), /start must be before the end/);
  assert.ok(!(await c.exists(".pendbar")), "nothing staged");
  await type(0, "e", "08:00");
  await sleep(60);
  assert.match(await c.text(".drow[data-d='0'] .blk.pend"), /04:00–08:00/);
  // add a second period (largest gap: the evening)
  await c.click("[data-act='tpadd']");
  assert.equal(await count(page, c, ".tpeditor .period"), 2);
  assert.match(await c.text(".tpeditor .addrow .hint"), /5 of 6 switch points used/);
  // under the Normal default a third period with Night around it would need 7 points
  assert.equal(await page.eval(`${c.root}.querySelector("[data-act='tpadd']").disabled`), true);
  await c.click(".tpeditor [data-act='tpdel'][data-i='1']");
  assert.equal(await count(page, c, ".tpeditor .period"), 1);
  assert.match(await c.text(".pendbar"), /Fußboden · Mon: 04:00–08:00/);
  // Tuesday joins Monday with the same times
  await c.click(".tpeditor .days button[data-d='1']");
  assert.match(await c.text(".pendbar"), /Fußboden · Mon, Tue: 04:00–08:00/);
  // "Remove schedule": every day Normal all day, the editor closes
  assert.match(await c.text("[data-act='tpclear']"), /Remove schedule/);
  await c.click("[data-act='tpclear']");
  assert.ok(!(await c.exists(".tpeditor")));
  assert.match(await c.text(".pendbar"), /Fußboden · Mon, Tue, Wed, Thu, Fri, Sat, Sun: Normal all day/);
  await c.click("[data-act='tpapply']");
  await sleep(250);
  const sent = await calls(page);
  assert.deepEqual(sent[0].days, { 0: [], 1: [], 2: [], 3: [], 4: [], 5: [], 6: [] }, "no switch point left on any day");
  // without a night setback the card offers the starting points again
  assert.ok(await c.exists(".tpempty"));
  assert.deepEqual(page.errors, []);
});

test("Eco: the card explains the paused program and switches to Komfort on request", { skip }, async () => {
  const page = await b.open("variant=house&view=all&latency=30", { width: 1280, height: 1600 });
  const c = on(page);
  await setValues(page, { "hc1.comfort_eco": 1 });
  await sleep(80);
  assert.ok(!(await c.exists(`${tile(1)} .sp.alt`)) && !(await c.exists(`${tile(1)} .tstrip`)), "Eco: night only, no bar");
  assert.match(await c.text(".card-schedule .tpbanner"), /Eco ist aktiv: Fußboden bleibt ganztägig auf Nacht 19,0°/);
  await c.click(".card-schedule [data-act='tpcomfort']");
  await sleep(100);
  assert.deepEqual(await writes(page), [`climate.set_preset_mode ${await page.eval("harness.plant.climateId(1)")} comfort`]);
  assert.deepEqual(page.errors, []);
});

test("copying a week to another circuit and applying both", { skip }, async () => {
  const page = await b.open("variant=house&view=schedule&latency=30&schedule=mixed&lang=en", { width: 760, height: 1300 });
  const c = on(page);
  assert.match(await c.text(".drow[data-d='0']"), /05:00–08:00.*16:00–22:00/);
  await c.click("[data-act='tpcopy']");
  await c.click("[data-act='tpcopytarget'][data-c='3']");
  await c.click("[data-act='tpcopygo']");
  assert.ok(await c.exists(".tabs button[data-c='3'] .pdot"));
  assert.match(await c.text(".pendbar"), /Wintergarten · Mon, Tue, Wed, Thu, Fri: 05:00–08:00, 16:00–22:00/);
  assert.match(await c.text(".pendbar"), /Wintergarten · Sat, Sun: 07:00–22:30/);
  await c.click("[data-act='tpapply']");
  await sleep(300);
  const sent = await calls(page);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].circuit, 3);
  assert.deepEqual(sent[0].days[0], [[0, 0], [300, 1], [480, 0], [960, 1], [1320, 0]]);
  assert.deepEqual(sent[0].days[6], [[0, 0], [420, 1], [1350, 0]]);
  assert.deepEqual(page.errors, []);
});

test("no time program on the controller: no card, no strip, both setpoints as before", { skip }, async () => {
  const page = await b.open("variant=house&view=all&latency=30&schedule=unsupported", { width: 1280, height: 1600 });
  const c = on(page);
  assert.equal(await page.eval(`${c.root}.querySelector(".card-schedule").hidden`), true);
  assert.ok(await c.exists(`${tile(1)} .sp.alt`) && !(await c.exists(".tstrip")) && !(await c.exists(".tplink")));
  assert.deepEqual(page.errors, []);
});

test("a failed write keeps the edits and says why", { skip }, async () => {
  const page = await b.open("variant=house&view=schedule&latency=30&lang=en", { width: 760, height: 1200 });
  const c = on(page);
  await page.eval("harness.fake.conn.failScheduleSet = true");
  await c.click(".drow[data-d='2']");
  await c.click("[data-act='tpclear']");
  await c.click("[data-act='tpapply']");
  await sleep(250);
  assert.match(await c.text(".note.err"), /Writing to the heat pump failed/);
  assert.ok(await c.exists(".pendbar"), "still pending");
  assert.deepEqual(page.errors, []);
});
