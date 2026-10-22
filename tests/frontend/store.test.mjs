import test from "node:test";
import assert from "node:assert/strict";
import { PlantStore } from "../../custom_components/ochsner_local_ots/frontend/lib/store.js";
import { FakePlant, fakeConnection } from "./harness/fake-backend.js";
import { fixtures } from "./helpers.mjs";

const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function setup(t, variant = "house") {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { catalog, snapshot, backend } = fixtures();
  const plant = new FakePlant({ catalog, snapshot, backend, variant, latency: 0 });
  const conn = fakeConnection([plant]);
  const store = new PlantStore(conn);
  return { plant, conn, store };
}

test("one plants call (with language) + one subscription shared by all listeners", async (t) => {
  const { conn, store, plant } = setup(t);
  store.setLanguage("de");
  let calls = 0;
  const un1 = store.addListener(() => calls++);
  const un2 = store.addListener(() => {});
  await flush();
  assert.deepEqual(conn.sent.map((m) => m.type), ["ochsner_local_ots/plants", "ochsner_local_ots/subscribe"]);
  assert.equal(conn.sent[0].language, "de");
  assert.equal(conn.subscriptions, 1);
  assert.equal(store.plants.length, 1);
  assert.equal(store.liveFor(plant.plantId).values["hp.status"], 1);
  assert.ok(calls >= 2);
  un1();
  un2();
  t.mock.timers.tick(10000);
  await flush();
  assert.equal(conn.subscriptions, 0, "unsubscribed after the last listener left");
});

test("re-mount within the grace period keeps the subscription", async (t) => {
  const { conn, store } = setup(t);
  const un = store.addListener(() => {});
  await flush();
  un();
  t.mock.timers.tick(3000);
  const un2 = store.addListener(() => {});
  t.mock.timers.tick(20000);
  await flush();
  assert.equal(conn.subscriptions, 1);
  assert.equal(conn.sent.filter((m) => m.type === "ochsner_local_ots/subscribe").length, 1);
  un2();
});

test("reconnect ('ready') re-fetches plants; language change re-fetches", async (t) => {
  const { conn, store } = setup(t);
  store.setLanguage("de");
  store.addListener(() => {});
  await flush();
  conn.fire("ready");
  await flush();
  assert.equal(conn.sent.filter((m) => m.type === "ochsner_local_ots/plants").length, 2);
  store.setLanguage("en");
  await flush();
  const last = conn.sent.filter((m) => m.type === "ochsner_local_ots/plants").pop();
  assert.equal(last.language, "en");
});

test("circuit/DHW presence change in the feed re-fetches plants exactly once", async (t) => {
  const { conn, store, plant } = setup(t);
  store.addListener(() => {});
  await flush();
  const count = () => conn.sent.filter((m) => m.type === "ochsner_local_ots/plants").length;
  assert.equal(count(), 1);
  plant.push(); // ordinary update
  await flush();
  assert.equal(count(), 1);
  plant.values["hc4.availability"] = 27; // a 4th circuit appears
  plant.push();
  await flush();
  t.mock.timers.tick(6000);
  await flush();
  assert.equal(count(), 2);
  assert.equal(store.plants[0].circuits.length, 4);
  plant.push();
  plant.push();
  await flush();
  t.mock.timers.tick(6000);
  await flush();
  assert.equal(count(), 2, "no refetch loop");
});

test("missing integration (unknown command) is reported, not thrown", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const { catalog, snapshot, backend } = fixtures();
  const conn = fakeConnection([new FakePlant({ catalog, snapshot, backend })], { failPlants: true });
  const store = new PlantStore(conn);
  store.addListener(() => {});
  await flush();
  assert.equal(store.plants, null);
  assert.equal(store.error.code, "unknown_command");
});

const plantsCalls = (conn) => conn.sent.filter((m) => m.type === "ochsner_local_ots/plants").length;

for (const [what, change] of [
  ["a circuit gains cooling", (p) => { p.values["hc2.availability"] = 27 | 4; }],
  ["a room sensor appears", (p) => { p.values["hc1.availability"] = 27 | 32; }],
  ["humidity appears", (p) => { p.values["hc3.availability"] = 27 | 64; }],
  ["a pool appears", (p) => { p.values["avail.pool"] = 1; }],
  ["the buffer disappears", (p) => { p.values["avail.buffer"] = 0; }],
  ["DHW appears", (p) => { p.values["avail.dhw"] = 256; }],
]) {
  test(`capability change re-fetches plants: ${what}`, async (t) => {
    const { conn, store, plant } = setup(t);
    store.addListener(() => {});
    await flush();
    assert.equal(plantsCalls(conn), 1);
    change(plant);
    plant.push();
    plant.push();
    plant.push();
    await flush();
    assert.equal(plantsCalls(conn), 1, "debounced");
    t.mock.timers.tick(1000);
    await flush();
    assert.equal(plantsCalls(conn), 2, "one refetch for the burst");
    plant.push();
    t.mock.timers.tick(70000);
    await flush();
    assert.equal(plantsCalls(conn), 2, "payload matches again → no more refetches");
  });
}

test("a mismatch the backend has not caught up with is retried a bounded number of times", async (t) => {
  const { conn, store, plant } = setup(t);
  const stale = plant.plantPayload("de");
  plant.plantPayload = () => JSON.parse(JSON.stringify(stale)); // backend keeps the old structure
  store.addListener(() => {});
  await flush();
  plant.values["hc2.availability"] = 27 | 4;
  for (let i = 0; i < 12; i++) {
    plant.push();
    await flush();
    t.mock.timers.tick(30000);
    await flush();
  }
  assert.equal(plantsCalls(conn), 1 + 3, "1 s, 10 s, 60 s, then stop");
  plant.values["hc3.availability"] = 27 | 4; // a different change starts over
  plant.push();
  await flush();
  t.mock.timers.tick(1000);
  await flush();
  assert.equal(plantsCalls(conn), 5);
});
