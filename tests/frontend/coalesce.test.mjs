import test from "node:test";
import assert from "node:assert/strict";
import { Coalescer } from "../../custom_components/ochsner_local_ots/frontend/lib/coalesce.js";

const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

function setup(t, { current = 21.5, fail = null } = {}) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const sent = [];
  const state = { current };
  const c = new Coalescer({
    delay: 1500,
    current: () => state.current,
    send: async (v) => {
      sent.push(v);
      if (fail) throw new Error(fail);
      state.current = v;
    },
  });
  return { c, sent, state };
}

test("a −/+ burst is shown immediately and written once after 1.5 s idle", async (t) => {
  const { c, sent } = setup(t);
  c.push(22);
  t.mock.timers.tick(700);
  c.push(22.5);
  t.mock.timers.tick(700);
  c.push(23);
  assert.equal(c.value, 23);
  assert.equal(c.state, "waiting");
  t.mock.timers.tick(1499);
  await flush();
  assert.deepEqual(sent, []);
  t.mock.timers.tick(1);
  await flush();
  assert.deepEqual(sent, [23]);
  assert.equal(c.state, "idle");
  assert.equal(c.value, undefined, "HA reflects the value → optimistic value dropped");
});

test("no write when the final value equals the current one", async (t) => {
  const { c, sent } = setup(t);
  c.push(22);
  c.push(21.5);
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, []);
  assert.equal(c.value, undefined);
  assert.equal(c.state, "idle");
});

test("error reverts the optimistic value and reports it", async (t) => {
  const { c, sent } = setup(t, { fail: "controller timeout" });
  c.push(24);
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, [24]);
  assert.equal(c.state, "error");
  assert.equal(c.error, "controller timeout");
  assert.equal(c.value, undefined);
  assert.equal(c.display(), 21.5);
});

test("optimistic value is kept until HA confirms (sync) or the timeout", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = { current: 50 };
  const sent = [];
  const c = new Coalescer({ delay: 1500, confirmTimeout: 15000, current: () => state.current, send: async (v) => { sent.push(v); } });
  c.push(52);
  t.mock.timers.tick(1500);
  await flush();
  assert.equal(c.state, "sent");
  assert.equal(c.display(), 52);
  assert.equal(c.sync(), false);
  state.current = 52;
  assert.equal(c.sync(), true);
  assert.equal(c.value, undefined);
  c.push(53);
  t.mock.timers.tick(1500);
  await flush();
  t.mock.timers.tick(15000);
  assert.equal(c.value, undefined, "dropped after confirm timeout");
  assert.deepEqual(sent, [52, 53]);
});

test("input while a write is in flight leads to exactly one follow-up write", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = { current: 20 };
  const sent = [];
  let release;
  const c = new Coalescer({
    delay: 1500,
    current: () => state.current,
    send: (v) => new Promise((res) => { sent.push(v); release = () => { state.current = v; res(); }; }),
  });
  c.push(21);
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, [21]);
  c.push(21.5);
  c.push(22);
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, [21], "waits for the running write");
  release();
  await flush();
  await flush();
  assert.deepEqual(sent, [21, 22]);
  release();
  await flush();
  assert.equal(state.current, 22);
});

test("flush sends immediately, cancel drops the pending value", async (t) => {
  const { c, sent } = setup(t);
  c.push(23);
  await c.flush();
  await flush();
  assert.deepEqual(sent, [23]);
  c.push(19);
  c.cancel();
  t.mock.timers.tick(5000);
  await flush();
  assert.deepEqual(sent, [23]);
});

test("dispose cancels a queued write for good", async (t) => {
  const { c, sent } = setup(t);
  c.push(23);
  c.dispose();
  t.mock.timers.tick(5000);
  await flush();
  assert.deepEqual(sent, []);
  c.push(24); // a stale click handler after removal
  t.mock.timers.tick(5000);
  await flush();
  assert.deepEqual(sent, [], "a disposed coalescer never writes again");
  assert.equal(c.value, undefined);
});

test("dispose while an earlier write is in flight: the queued follow-up is never sent", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = { current: 20 };
  const sent = [];
  let release;
  const c = new Coalescer({
    delay: 1500,
    current: () => state.current,
    send: (v) => new Promise((res) => { sent.push(v); release = () => { state.current = v; res(); }; }),
  });
  c.push(21);
  t.mock.timers.tick(1500);
  await flush();
  c.push(22);
  t.mock.timers.tick(1500); // _fire() now awaits the running write
  await flush();
  c.dispose();
  release();
  await flush();
  await flush();
  assert.deepEqual(sent, [21], "the follow-up was dropped");
});

test("cancel between the timer and the deferred send stops the write", async (t) => {
  const { c, sent } = setup(t);
  c.push(23);
  t.mock.timers.tick(1500); // _fire() runs, the send is deferred by a microtask
  c.cancel();
  await flush();
  assert.deepEqual(sent, []);
});

function ctxSetup(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = { mode: "performance", target: 52 };
  const sent = [];
  const c = new Coalescer({
    delay: 1500,
    current: () => state.target,
    context: () => state.mode,
    send: async (v) => { sent.push(v); state.target = v; },
  });
  return { c, sent, state };
}

test("a queued edit is dropped when the mode changed before it fires", async (t) => {
  const { c, sent, state } = ctxSetup(t);
  c.push(52.5);
  state.mode = "eco";
  state.target = 46; // eco setpoint now shown
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, [], "52.5 must not land on the eco setpoint");
  assert.equal(c.state, "dropped");
  assert.equal(c.value, undefined);
});

test("a queued edit is dropped when the target changed externally", async (t) => {
  const { c, sent, state } = ctxSetup(t);
  c.push(52.5);
  state.target = 55; // someone else changed it
  t.mock.timers.tick(1500);
  await flush();
  assert.deepEqual(sent, []);
  assert.equal(c.state, "dropped");
});

test("our own previous write landing during a new burst does not drop it", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const state = { mode: "heat|comfort", target: 21 };
  const sent = [];
  let release;
  const c = new Coalescer({
    delay: 1500,
    current: () => state.target,
    context: () => state.mode,
    send: (v) => new Promise((res) => { sent.push(v); release = () => { state.target = v; res(); }; }),
  });
  c.push(21.5);
  t.mock.timers.tick(1500);
  await flush();
  c.push(22); // new burst while 21.5 is on the wire (baseline still 21)
  release(); // HA now reports 21.5 — our own write
  t.mock.timers.tick(1500);
  await flush();
  await flush();
  assert.deepEqual(sent, [21.5, 22]);
});
