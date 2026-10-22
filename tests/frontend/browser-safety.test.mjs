// Write-safety tests in a real browser: queued writes never outlive the
// card, the plant or the mode they were made for; curve Apply revalidates
// and orders its writes; unit conversion at the service-call boundary.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { CHROME, browser, on, sleep, writes } from "./harness/browser-helpers.mjs";

const skip = !CHROME && "no chromium";
let b;
before(async () => { if (CHROME) b = await browser(); });
after(async () => { if (b) await b.close(); });

test("removing the card cancels a queued −/+ write", { skip }, async () => {
  const page = await b.open("variant=house&view=controls&latency=30");
  const c = on(page);
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  assert.match(await c.text('.hc[data-key="hc1"] .stepper .v'), /22,0°/);
  await page.eval("harness.el.remove()");
  await sleep(2200);
  assert.deepEqual(await writes(page), []);
  assert.deepEqual(page.errors, []);
});

const CARD = 'harness.el.shadowRoot.querySelector("ochsner-heatpump-card").shadowRoot';

async function panelTab(page, host) {
  const p = on(page, "harness.el.shadowRoot");
  await p.click(`.tabs button[data-p$="${host}"]`);
  await sleep(150);
}

test("control state is per plant: switching plants cancels A's queued edits and never writes to B", { skip }, async () => {
  const page = await b.open("variant=multi&view=panel&theme=light&latency=30", { width: 1280, height: 1600 });
  const c = on(page, CARD);
  // A: stepper burst + a pending curve edit
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  await c.focusKey('[data-h="heat_limit"]', "ArrowRight", "ArrowRight");
  assert.ok(await c.exists(".pendbar"));
  await panelTab(page, "192.0.2.81");
  assert.match(await c.text(".card-plant .ttl"), /Ochsner Wärmepumpe/);
  // B shows nothing pending, Enter on a B handle applies nothing
  assert.ok(!(await c.exists(".pendbar")), "no pending bar on B");
  assert.ok(!(await c.exists(".stepper .v.pend")), "no pending stepper on B");
  await c.focusKey('[data-h="heat_limit"]', "Enter");
  await sleep(1800);
  assert.deepEqual(await writes(page), [], "A's queued write was cancelled, nothing went to B");
  // B's own stepper writes to B's entity only
  await c.click('.hc[data-key="hc1"] [data-d="1"]');
  await page.waitFor("harness.writes().length === 1", 3000);
  assert.deepEqual(await writes(page), ["climate.set_temperature climate.wp2_erdgeschoss 24.5"]);
  // back on A: the cancelled edits stay gone
  await panelTab(page, "192.0.2.80");
  assert.ok(!(await c.exists(".pendbar")));
  assert.ok(!(await c.exists(".stepper .v.pend")));
  assert.deepEqual(page.errors, []);
});

test("an Apply that is running when the plant changes only writes A's entities", { skip }, async () => {
  const page = await b.open("variant=multi&view=panel&theme=light&latency=300", { width: 1280, height: 1600 });
  const c = on(page, CARD);
  await c.focusKey('[data-h="heat_norm"]', "ArrowUp", "ArrowLeft");
  await c.focusKey('[data-h="heat_max"]', "ArrowUp");
  await c.click('[data-cact="apply"]');
  await panelTab(page, "192.0.2.81");
  await page.waitFor("harness.writes().length === 3", 4000);
  await sleep(400);
  const w = await writes(page);
  assert.equal(w.length, 3);
  for (const x of w) assert.match(x, /^number\.set_value number\.(?!wp2_)fussboden_/, x);
  assert.ok(!(await c.exists(".pendbar")), "B shows nothing of A's apply");
});
