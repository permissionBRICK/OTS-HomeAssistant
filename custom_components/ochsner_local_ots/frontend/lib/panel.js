// <ochsner-heatpump-panel>: sidebar panel with the full dashboard.

import { esc, fire, morph, navigate, rafBatch } from "./dom.js";
import { getStore } from "./store.js";
import { langOf, makeI18n } from "./i18n.js";
import { menuSvg } from "./icons.js";

const PANEL_CSS = `
:host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color); font-family: var(--ha-font-family-body, Roboto, system-ui, sans-serif); }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
.toolbar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px; position: sticky; top: 0; z-index: 4;
  background: var(--app-header-background-color, var(--primary-background-color)); color: var(--app-header-text-color, var(--primary-text-color));
  border-bottom: var(--app-header-border-bottom, 1px solid var(--divider-color, transparent)); }
.toolbar .title { font-size: 20px; font-weight: 400; margin-left: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.menu { width: 40px; height: 40px; border-radius: 50%; border: 0; background: transparent; color: inherit; display: grid; place-items: center; cursor: pointer; padding: 0; }
.menu:hover { background: color-mix(in srgb, currentColor 8%, transparent); }
.menu:focus-visible, a:focus-visible, .tabs button:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
.content { max-width: 1440px; margin: 0 auto; padding: 16px; }
:host([narrow]) .content { padding: 8px; }
.tabs { display: flex; gap: 6px; flex-wrap: wrap; margin: 0 0 12px; }
.tabs button { font: inherit; font-size: 13px; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--secondary-text-color); cursor: pointer; }
.tabs button.on { background: var(--primary-text-color); color: var(--primary-background-color); border-color: transparent; }
.footer { display: flex; gap: 18px; flex-wrap: wrap; margin: 14px 4px 24px; font-size: 13px; }
.footer a { color: var(--primary-color, #03a9f4); text-decoration: none; cursor: pointer; }
.footer a:hover { text-decoration: underline; }
`;

export class OchsnerHeatpumpPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._narrow = false;
    this._sel = null;
    this.requestRender = rafBatch(() => this._render());
    this._onStore = () => this.requestRender();
    this.shadowRoot.innerHTML = `<style>${PANEL_CSS}</style><div class="toolbar"></div><div class="content"><div class="tabs" hidden></div><ochsner-heatpump-card></ochsner-heatpump-card><div class="footer"></div></div>`;
    this._card = this.shadowRoot.querySelector("ochsner-heatpump-card");
    this._cardCfg = null;
    this.shadowRoot.addEventListener("click", (e) => {
      const menu = e.target.closest(".menu");
      if (menu) {
        fire(this, "hass-toggle-menu", { open: true });
        return;
      }
      const tab = e.target.closest(".tabs button[data-p]");
      if (tab) {
        this._sel = tab.getAttribute("data-p");
        this.requestRender();
        return;
      }
      const a = e.target.closest("a[data-nav]");
      if (a && !e.ctrlKey && !e.metaKey && !e.shiftKey && e.button === 0) {
        e.preventDefault();
        navigate(a.getAttribute("data-nav"));
      }
    });
  }

  set hass(h) {
    this._hass = h;
    const store = getStore(h);
    // language first: the first plants request must already carry it
    if (store) store.setLanguage(langOf(h));
    if (store !== this._store) {
      if (this._unlisten) this._unlisten();
      this._store = store;
      this._unlisten = this.isConnected && store ? store.addListener(this._onStore) : null;
    }
    this._card.hass = h;
    if (!this._rendered || this._lastLocale !== h.locale || this._lastSidebar !== h.dockedSidebar) this.requestRender();
  }

  get hass() {
    return this._hass;
  }

  set narrow(v) {
    this._narrow = !!v;
    this.toggleAttribute("narrow", this._narrow);
    this.requestRender();
  }

  get narrow() {
    return this._narrow;
  }

  set panel(p) {
    this._panel = p;
    this.requestRender();
  }

  set route(r) {
    this._route = r;
  }

  connectedCallback() {
    if (this._store && !this._unlisten) this._unlisten = this._store.addListener(this._onStore);
    this.requestRender();
  }

  disconnectedCallback() {
    if (this._unlisten) this._unlisten();
    this._unlisten = null;
  }

  _render() {
    const h = this._hass;
    if (!h) return;
    this._rendered = true;
    this._lastLocale = h.locale;
    this._lastSidebar = h.dockedSidebar;
    const I = makeI18n(h);
    const plants = (this._store && this._store.plants) || [];
    if (!plants.some((p) => p.plant_id === this._sel)) this._sel = plants.length ? plants[0].plant_id : null;
    const plant = plants.find((p) => p.plant_id === this._sel) || null;
    const showMenu = this._narrow || h.dockedSidebar === "always_hidden";
    const title = (this._panel && this._panel.title) || I.t("heat_pump");
    morph(this.shadowRoot.querySelector(".toolbar"), `${showMenu ? `<button class="menu" aria-label="${esc(I.t("menu"))}">${menuSvg}</button>` : ""}<div class="title">${esc(title)}</div>`);
    const tabs = this.shadowRoot.querySelector(".tabs");
    tabs.hidden = plants.length < 2;
    if (plants.length > 1) {
      morph(tabs, plants.map((p) => `<button data-p="${esc(p.plant_id)}" class="${p.plant_id === this._sel ? "on" : ""}" aria-pressed="${p.plant_id === this._sel}">${esc(p.title || p.host)}</button>`).join(""));
    }
    const cfg = { type: "custom:ochsner-heatpump-card", view: "all", color_scheme: "auto" };
    if (plant) cfg.plant = plant.plant_id;
    const key = JSON.stringify(cfg);
    if (key !== this._cardCfg) {
      this._cardCfg = key;
      this._card.setConfig(cfg);
    }
    const links = [];
    if (plant && plant.device_id) links.push(`<a href="/config/devices/device/${esc(plant.device_id)}" data-nav="/config/devices/device/${esc(plant.device_id)}">${esc(I.t("all_values"))}</a>`);
    links.push(`<a href="/config/integrations/integration/ochsner_local_ots" data-nav="/config/integrations/integration/ochsner_local_ots">${esc(I.t("integration_settings"))}</a>`);
    morph(this.shadowRoot.querySelector(".footer"), links.join(""));
  }
}
