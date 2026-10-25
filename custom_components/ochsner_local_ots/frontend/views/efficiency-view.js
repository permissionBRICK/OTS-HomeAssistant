// The combined heating curve + 24 h energy card (view "efficiency", kept
// for dashboards that use it; the panel shows the two as separate cards).

import { CurveView } from "./curve-view.js";
import { EnergyView } from "./energy-view.js";

export class EfficiencyView {
  constructor(card) {
    this.card = card;
    this.curveView = new CurveView(card);
    this.energy = new EnergyView(card);
  }

  /** The heating-curve editor (tests and the card reach it here). */
  get curve() {
    return this.curveView.curve;
  }

  set plantId(id) {
    this._plantId = id;
    this.curveView.plantId = id;
  }

  get plantId() {
    return this._plantId;
  }

  dispose() {
    this.curveView.dispose();
  }

  pause() {
    this.curveView.pause();
  }

  bind(el) {
    this.curveView.bind(el);
    this.energy.bind(el);
  }

  attach(root) {
    this.curveView.attach(root);
  }

  render(ctx) {
    return this.curveView.render(ctx, { inline: true }) + this.energy.render(ctx, { inline: true });
  }
}
