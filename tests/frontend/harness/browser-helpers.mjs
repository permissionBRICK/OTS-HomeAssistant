// Shared helpers for the headless-Chromium tests (harness page + CDP driver).
import assert from "node:assert/strict";
import { findChrome, launch } from "./cdp.mjs";
import { startServer } from "./serve.mjs";

export const CHROME = findChrome();
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Start server + browser; returns { open, close }. */
export async function browser() {
  const server = await startServer(0);
  const base = `http://127.0.0.1:${server.address().port}/tests/frontend/harness/index.html?`;
  const chrome = await launch();
  return {
    async open(query, { width = 760, height = 1000, mobile = false } = {}) {
      const page = await chrome.newPage();
      await page.init();
      await page.viewport(width, height, mobile);
      await page.goto(base + query);
      await sleep(150);
      return page;
    },
    async close() {
      await chrome.close();
      server.close();
    },
  };
}

/** Helpers bound to a shadow root expression (card or the panel's inner card). */
export function on(page, root = "harness.el.shadowRoot") {
  const center = (sel) => page.eval(`(() => { const e = ${root}.querySelector(${JSON.stringify(sel)}); if (!e) return null; e.scrollIntoView({ block: "nearest" }); const r = e.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; })()`);
  const h = {
    root,
    center,
    exists: (sel) => page.eval(`!!${root}.querySelector(${JSON.stringify(sel)})`),
    text: (sel) => page.eval(`(${root}.querySelector(${JSON.stringify(sel)}) || {}).textContent || ""`),
    async click(sel) {
      const c = await center(sel);
      assert.ok(c, `element ${sel}`);
      await page.mouse("mouseMoved", c[0], c[1]);
      await page.mouse("mousePressed", c[0], c[1], 1);
      await page.mouse("mouseReleased", c[0], c[1]);
      await sleep(60);
    },
    async drag(sel, dx, dy, steps = 8) {
      const c = await center(sel);
      assert.ok(c, `element ${sel}`);
      const [x, y] = c;
      await page.mouse("mouseMoved", x, y);
      await page.mouse("mousePressed", x, y, 1);
      for (let i = 1; i <= steps; i++) {
        await page.mouse("mouseMoved", x + (dx * i) / steps, y + (dy * i) / steps, 1);
        await sleep(16);
      }
      await page.mouse("mouseReleased", x + dx, y + dy);
      await sleep(80);
    },
    async focusKey(sel, ...keys) {
      await page.eval(`${root}.querySelector(${JSON.stringify(sel)}).focus()`);
      for (const k of keys) await page.key(k.replace(/^\+/, ""), { shift: k.startsWith("+") });
      await sleep(60);
    },
  };
  return h;
}

export const writes = (page) => page.eval("harness.writes()");
