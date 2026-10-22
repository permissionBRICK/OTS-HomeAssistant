// Render the harness in headless Chromium and save screenshots.
// Usage: node tests/frontend/harness/shoot.mjs [outdir] [filter]
import { startServer } from "./serve.mjs";
import { launch } from "./cdp.mjs";
import os from "node:os";
import path from "node:path";

const out = process.argv[2] || path.join(os.tmpdir(), "ochsner-dashboard-shots");
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
  ["house-en-narrow-dark", "variant=house&theme=dark&scheme=ha&lang=en", 360],
  ["house-en-wide-light", "variant=house&theme=light&scheme=ha&lang=en", 1280],
  ["full-emergency-dark-ochsner", "variant=full&theme=dark&scheme=ochsner&emergency=hp.emergency_status:1,dhw.emergency_status:2&view=plant", 760],
  ["full-fahrenheit-light-ha", "variant=full&theme=light&scheme=ha&fahrenheit=1&lang=en&view=controls", 420],
  ["house-fahrenheit-light-ha", "variant=house&theme=light&scheme=ha&fahrenheit=1&lang=en", 760],
  ["full-fahrenheit-narrow-dark", "variant=full&theme=dark&scheme=ha&fahrenheit=1&lang=en", 360],
  ["unavailable-dark", "variant=house&theme=dark&unavailable=1&nohistory=1&view=controls", 420],
  ["drag-live-dark-ochsner", "variant=house&theme=dark&scheme=ochsner&view=efficiency", 760, "dragLive"],
  ["drag-pending-light-ha", "variant=house&theme=light&scheme=ha&view=efficiency", 760, "dragPending"],
  ["drag-pending-narrow-dark", "variant=full&theme=dark&scheme=ha&view=efficiency", 360, "dragPending"],
  ["hover-crosshair-light", "variant=house&theme=light&scheme=ochsner&view=efficiency&lang=en", 760, "hover"],
  ["controls-burst-dark", "variant=house&theme=dark&scheme=ha&view=controls", 420, "burst"],
  ["controls-confirm-off-light", "variant=house&theme=light&scheme=ochsner&view=controls", 420, "armOff"],
  ["setpoints-house-dark-ha-wide", "variant=house&theme=dark&scheme=ha", 1280, "fold:3"],
  ["setpoints-house-narrow-dark-ha", "variant=house&theme=dark&scheme=ha", 360, "fold:3"],
  ["setpoints-house-en-narrow-light-ochsner", "variant=house&theme=light&scheme=ochsner&lang=en&view=controls", 360, "fold:3"],
  ["setpoints-full-cooling-dark-ochsner", "variant=full&theme=dark&scheme=ochsner", 760, "fold:2"],
  ["setpoints-full-cooling-narrow-light-ha", "variant=full&theme=light&scheme=ha&view=controls", 360, "fold:2"],
  ["setpoints-room-sensors-wide-dark-ha", "variant=full&theme=dark&scheme=ha", 1280],
  ["setpoints-room-sensors-narrow-light-ha", "variant=full&theme=light&scheme=ha", 360],
  ["setpoints-room-sensors-fahrenheit-narrow-dark-ochsner", "variant=full&theme=dark&scheme=ochsner&fahrenheit=1&lang=en", 360],
];

const sr = "harness.el.shadowRoot";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const center = (page, sel) => page.eval(`(() => { const r = ${sr}.querySelector(${JSON.stringify(sel)}).getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
async function drag(page, sel, dx, dy, release = true) {
  const [x, y] = await center(page, sel);
  await page.mouse("mouseMoved", x, y);
  await page.mouse("mousePressed", x, y, 1);
  for (let i = 1; i <= 6; i++) { await page.mouse("mouseMoved", x + (dx * i) / 6, y + (dy * i) / 6, 1); await sleep(20); }
  if (release) await page.mouse("mouseReleased", x + dx, y + dy);
  await sleep(120);
}
async function click(page, sel) {
  const [x, y] = await center(page, sel);
  await page.mouse("mousePressed", x, y, 1);
  await page.mouse("mouseReleased", x, y);
  await sleep(60);
}
const ACTIONS = {
  dragLive: (page) => drag(page, '[data-h="heat_norm"] .dotv', 0, -36, false),
  dragPending: async (page) => {
    await drag(page, '[data-h="heat_norm"] .dotv', 0, -30);
    await drag(page, '[data-h="heat_limit"] .hit', 18, 0);
  },
  burst: async (page) => { for (let i = 0; i < 2; i++) await click(page, '.hc[data-key="hc1"] [data-d="1"]'); },
  hover: async (page) => {
    const r = await page.eval(`(() => { const b = ${sr}.querySelector("svg.ch .plotbg").getBoundingClientRect(); return [b.left + b.width * 0.3, b.top + b.height * 0.5]; })()`);
    await page.mouse("mouseMoved", r[0], r[1]);
    await sleep(60);
    const bar = await center(page, 'svg.bars .bh[data-i="16"]');
    await page.mouse("mouseMoved", bar[0], bar[1]);
    await sleep(60);
    await page.mouse("mouseMoved", r[0], r[1]);
    await sleep(60);
  },
  armOff: (page) => click(page, '[data-act="hp"][data-v="3"]'),
  fold: async (page, n) => {
    await page.eval(`${sr}.querySelector('.hc[data-key="hc${n}"] .sptoggle').scrollIntoView({ block: "center" })`);
    await click(page, `.hc[data-key="hc${n}"] .sptoggle`);
  },
};

const server = await startServer(0);
const base = `http://127.0.0.1:${server.address().port}/tests/frontend/harness/index.html?`;
const chrome = await launch();
try {
  for (const [name, query, width, action] of SHOTS) {
    if (filter && !name.includes(filter)) continue;
    const page = await chrome.newPage();
    await page.init();
    await page.viewport(width, 900, width < 500);
    await page.goto(base + query);
    await new Promise((r) => setTimeout(r, 400));
    if (action) {
      const [act, arg] = action.split(":");
      await ACTIONS[act](page, arg);
    }
    await page.screenshot(`${out}/${name}.png`);
    if (page.errors.length) console.log(name, "ERRORS:", page.errors);
    console.log("saved", `${out}/${name}.png`);
  }
} finally {
  await chrome.close();
  server.close();
}
