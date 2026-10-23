// Tiny Chrome DevTools Protocol driver (Node built-in WebSocket/fetch; no deps).
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const CANDIDATES = [
  process.env.CHROME,
  "/usr/bin/chromium",
  "/usr/bin/google-chrome",
];

export function findChrome() {
  for (const c of CANDIDATES) if (c && fs.existsSync(c)) return c;
  const pw = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(os.homedir(), ".cache", "ms-playwright");
  if (fs.existsSync(pw)) {
    for (const d of fs.readdirSync(pw).sort().reverse()) {
      for (const sub of ["chrome-linux64/chrome", "chrome-headless-shell-linux64/chrome-headless-shell"]) {
        const p = path.join(pw, d, sub);
        if (fs.existsSync(p)) return p;
      }
    }
  }
  return null;
}

export async function launch({ width = 1200, height = 900 } = {}) {
  const bin = findChrome();
  if (!bin) throw new Error("no chrome binary found");
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ohp-chrome-"));
  const proc = spawn(bin, ["--headless=new", "--no-sandbox", "--hide-scrollbars", "--remote-debugging-port=0", `--user-data-dir=${dir}`, `--window-size=${width},${height}`, "--force-color-profile=srgb", "about:blank"], { stdio: ["ignore", "ignore", "pipe"] });
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = "";
    const to = setTimeout(() => reject(new Error("chrome did not start")), 15000);
    proc.stderr.on("data", (d) => {
      buf += d;
      const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf);
      if (m) { clearTimeout(to); resolve(m[1]); }
    });
    proc.on("exit", (c) => reject(new Error("chrome exited " + c)));
  });
  const browser = await connect(wsUrl);
  return {
    browser,
    async newPage() {
      const { targetId } = await browser.send("Target.createTarget", { url: "about:blank" });
      const { sessionId } = await browser.send("Target.attachToTarget", { targetId, flatten: true });
      return new Page(browser, sessionId);
    },
    async close() {
      try { await browser.send("Browser.close"); } catch (_e) { /* ignore */ }
      browser.ws.close();
      proc.kill("SIGKILL");
      fs.rmSync(dir, { recursive: true, force: true });
    },
  };
}

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const listeners = new Set();
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result);
    } else for (const l of listeners) l(msg);
  };
  return {
    ws,
    on: (fn) => listeners.add(fn),
    send(method, params = {}, sessionId) {
      const mid = ++id;
      ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
      return new Promise((resolve, reject) => pending.set(mid, { resolve, reject }));
    },
  };
}

export class Page {
  constructor(browser, sessionId) {
    this.b = browser;
    this.s = sessionId;
    this.errors = [];
    browser.on((msg) => {
      if (msg.sessionId !== this.s) return;
      if (msg.method === "Runtime.exceptionThrown") this.errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === "Runtime.consoleAPICalled" && msg.params.type === "error") this.errors.push(msg.params.args.map((a) => a.value ?? a.description).join(" "));
    });
  }
  send(method, params) { return this.b.send(method, params, this.s); }
  async init() { await this.send("Runtime.enable"); await this.send("Page.enable"); }
  async viewport(width, height, mobile = false, scale = 1) {
    await this.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: scale, mobile });
    if (mobile) await this.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
  }
  async goto(url) {
    await this.send("Page.navigate", { url });
    await this.waitFor("window.__ready === true", 15000);
  }
  async eval(expr) {
    const r = await this.send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    return r.result.value;
  }
  async waitFor(expr, timeout = 5000) {
    const t0 = Date.now();
    for (;;) {
      try { if (await this.eval(expr)) return; } catch (_e) { /* not yet */ }
      if (Date.now() - t0 > timeout) throw new Error("timeout waiting for " + expr);
      await new Promise((r) => setTimeout(r, 50));
    }
  }
  async screenshot(file, { fullPage = true } = {}) {
    let clip;
    if (fullPage) {
      const m = await this.send("Page.getLayoutMetrics");
      const s = m.cssContentSize || m.contentSize;
      clip = { x: 0, y: 0, width: Math.ceil(s.width), height: Math.ceil(s.height), scale: 1 };
    }
    const r = await this.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, ...(clip ? { clip } : {}) });
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, Buffer.from(r.data, "base64"));
  }
  mouse(type, x, y, buttons = 0) {
    return this.send("Input.dispatchMouseEvent", { type, x, y, button: type === "mouseMoved" ? (buttons ? "left" : "none") : "left", buttons, clickCount: type === "mouseMoved" ? 0 : 1 });
  }
  touch(type, points) {
    return this.send("Input.dispatchTouchEvent", { type, touchPoints: points.map(([x, y]) => ({ x, y })) });
  }
  key(key, { shift = false } = {}) {
    const codes = { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Enter: 13, Escape: 27, Tab: 9 };
    const base = { key, code: key, windowsVirtualKeyCode: codes[key] || 0, modifiers: shift ? 8 : 0 };
    return this.send("Input.dispatchKeyEvent", { type: "keyDown", ...base }).then(() => this.send("Input.dispatchKeyEvent", { type: "keyUp", ...base }));
  }
}
