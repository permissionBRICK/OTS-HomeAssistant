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
