// Render the harness in headless Chromium and save screenshots.
// Usage: node tests/frontend/harness/shoot.mjs [outdir] [filter]
import { startServer } from "./serve.mjs";
import { launch } from "./cdp.mjs";

const out = process.argv[2] || "/root/work/shots/frontend";
const filter = process.argv[3] || "";

export const SHOTS = [
  ["house-dark-ha-wide", "variant=house&theme=dark&scheme=ha", 1280],
  ["house-light-ha-wide", "variant=house&theme=light&scheme=ha", 1280],
  ["house-dark-ochsner", "variant=house&theme=dark&scheme=ochsner", 760],
  ["house-light-ochsner", "variant=house&theme=light&scheme=ochsner", 760],
  ["house-narrow-dark", "variant=house&theme=dark&scheme=ha", 360],
  ["house-narrow-light-ochsner", "variant=house&theme=light&scheme=ochsner", 360],
  ["full-dark-ochsner", "variant=full&theme=dark&scheme=ochsner", 760],
  ["full-light-ha", "variant=full&theme=light&scheme=ha", 760],
  ["full-narrow-dark-ha", "variant=full&theme=dark&scheme=ha", 360],
  ["compact-light-ha", "variant=compact&theme=light&scheme=ha", 760],
  ["compact-narrow-dark-ochsner", "variant=compact&theme=dark&scheme=ochsner", 360],
  ["panel-wide-dark", "variant=house&theme=dark&scheme=ha&view=panel", 1400],
  ["panel-narrow-light-multi", "variant=multi&theme=light&scheme=ha&view=panel", 390],
  ["house-en-light", "variant=house&theme=light&scheme=ha&lang=en", 760],
  ["unavailable-dark", "variant=house&theme=dark&unavailable=1&nohistory=1&view=controls", 420],
];

const server = await startServer(0);
const base = `http://127.0.0.1:${server.address().port}/tests/frontend/harness/index.html?`;
const chrome = await launch();
try {
  for (const [name, query, width] of SHOTS) {
    if (filter && !name.includes(filter)) continue;
    const page = await chrome.newPage();
    await page.init();
    await page.viewport(width, 900, width < 500);
    await page.goto(base + query);
    await new Promise((r) => setTimeout(r, 400));
    await page.screenshot(`${out}/${name}.png`);
    if (page.errors.length) console.log(name, "ERRORS:", page.errors);
    console.log("saved", `${out}/${name}.png`);
  }
} finally {
  await chrome.close();
  server.close();
}
