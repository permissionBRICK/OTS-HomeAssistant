// Ochsner Local OTS – heat-pump dashboard (Lovelace cards + sidebar panel).
// Vanilla ES modules, no build step. Loaded on every dashboard via
// frontend.add_extra_js_url and as the panel_custom module.

import { registerCards } from "./lib/card.js";
import { OchsnerHeatpumpPanel } from "./lib/panel.js";

registerCards();
if (!customElements.get("ochsner-heatpump-panel")) customElements.define("ochsner-heatpump-panel", OchsnerHeatpumpPanel);
