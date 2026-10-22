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
