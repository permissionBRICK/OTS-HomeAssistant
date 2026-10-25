// Card: heating curve per circuit tab (drag the points and limits, apply).

import { esc } from "../lib/dom.js";
import { circuitName } from "../lib/model.js";
import { CurveEditor } from "./curve-editor.js";

export class CurveView {
  constructor(card) {
    this.card = card;
    this.curve = new CurveEditor(card);
    this.tab = null;
  }

  set plantId(id) {
    this._plantId = id;
    this.curve.plantId = id;
  }

  get plantId() {
    return this._plantId;
  }

  dispose() {
    this.curve.dispose();
  }

  pause() {
    this.curve.pause();
  }

  bind(el) {
    this.curve.bind(el);
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-tab]");
      if (b) {
        this.tab = Number(b.getAttribute("data-tab"));
        this.card.requestRender();
      }
    });
  }

  attach(root) {
    this.curve.attach(root);
  }

  /** `inline`: on top of the combined curve + energy card (no unavailable banner of its own). */
  render(ctx, { inline = false } = {}) {
    const { I, sec, live } = ctx;
    const circuits = sec.circuits;
    if (!circuits.length) return "";
    if (!circuits.some((c) => c.index === this.tab)) this.tab = circuits[0].index;
    const c = circuits.find((x) => x.index === this.tab);
    let h = `<div class="hdr"><div class="ttl">${esc(I.t("heating_curve"))}</div><div class="sub">${esc(I.t("drag_hint"))}</div></div>`;
    if (!inline && ctx.config.view !== "all") h += ctx.banner; // the panel shows it on the controls
    if (circuits.length > 1) {
      h += `<div class="tabs" role="tablist">${circuits
        .map((x) => `<button role="tab" data-tab="${x.index}" class="${x.index === this.tab ? "on" : ""}" aria-selected="${x.index === this.tab}">${esc(circuitName(x, live))}${this.curve.hasPending(x.index) ? '<span class="pdot" aria-hidden="true"></span>' : ""}</button>`)
        .join("")}</div>`;
    }
    h += `<div data-key="curve-${c.index}">${this.curve.render(ctx, c, ctx.width)}</div>`;
    return h;
  }
}
