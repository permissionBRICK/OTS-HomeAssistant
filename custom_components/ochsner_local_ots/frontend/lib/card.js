// <ochsner-heatpump-card> and its thin per-view variants.

import { morph, rafBatch, fire, esc } from "./dom.js";
import { getStore } from "./store.js";
import { makeI18n, langOf, translate } from "./i18n.js";
import { resolveScheme, schemeVars } from "./colors.js";
import { CSS, TINTS } from "./styles.js";
import { capabilities, circuitsForDevice, hpMode, pickPlant, sections } from "./model.js";
import { PlantView } from "../views/plant-view.js";
import { ControlView } from "../views/control-view.js";
import { EfficiencyView } from "../views/efficiency-view.js";

const VIEWS = ["all", "plant", "controls", "efficiency"];
const VIEW_CLASSES = { plant: PlantView, controls: ControlView, efficiency: EfficiencyView };
let UID = 0;

const docLang = () => {
  const l = (globalThis.document && document.documentElement && document.documentElement.lang) || (globalThis.navigator && navigator.language) || "en";
  return String(l).toLowerCase().startsWith("de") ? "de" : "en";
};

export class OchsnerHeatpumpCard extends HTMLElement {
  static fixedView = null;

  static async getStubConfig(hass) {
    const type = `custom:${this.tagName}`;
    const cfg = { type };
    const store = getStore(hass);
    if (!store) return cfg;
    try {
      store.setLanguage(langOf(hass));
      if (!store.plants) await Promise.race([store.refetch(true), new Promise((r) => setTimeout(r, 3000))]);
      const p = store.plants && store.plants[0];
      if (p && p.device_id) cfg.device_id = p.device_id;
    } catch (_e) { /* integration not loaded: stub without device */ }
    return cfg;
  }

  static getConfigForm() {
    const lang = docLang();
    const t = (k) => translate(lang, k);
    const schema = [{ name: "device_id", selector: { device: { filter: { integration: "ochsner_local_ots" } } } }];
    if (!this.fixedView) {
      schema.push({
        name: "view",
        selector: { select: { mode: "dropdown", options: VIEWS.map((v) => ({ value: v, label: t("view_" + v) })) } },
      });
    }
    schema.push({
      name: "color_scheme",
      selector: { select: { mode: "dropdown", options: ["auto", "ha", "ochsner"].map((v) => ({ value: v, label: t("colors_" + v) })) } },
    });
    const labels = { device_id: t("cfg_device"), view: t("cfg_view"), color_scheme: t("cfg_colors") };
    return { schema, computeLabel: (s) => labels[s.name] || s.name };
  }

  static get tagName() {
    return "ochsner-heatpump-card";
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._uid = `o${++UID}`;
    this._config = { view: this.constructor.fixedView || "all", color_scheme: "auto" };
    this._views = {};
    this._containers = {};
    this._widths = {};
    this._watch = new Map();
    this._skeleton = null;
    this._schemeKey = null;
    this.writeDelay = 1500;
    this.requestRender = rafBatch(() => this._render());
    this._onStore = () => this.requestRender();
    this._ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver((entries) => this._resized(entries)) : null;
    this.shadowRoot.addEventListener("click", (e) => {
      const t = e.target.closest && e.target.closest("[data-more]");
      if (t) this.moreInfo(t.getAttribute("data-more"));
    });
    this.shadowRoot.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const t = e.target.closest && e.target.closest("svg.plant [data-more]");
      if (t) {
        e.preventDefault();
        this.moreInfo(t.getAttribute("data-more"));
      }
    });
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    const view = this.constructor.fixedView || config.view || "all";
    if (!VIEWS.includes(view)) throw new Error(`view must be one of ${VIEWS.join(", ")}`);
    const cs = config.color_scheme || "auto";
    if (!["auto", "ha", "ochsner"].includes(cs)) throw new Error("color_scheme must be auto, ha or ochsner");
    if (config.circuits !== undefined && !Array.isArray(config.circuits)) throw new Error("circuits must be a list of circuit numbers");
    this._config = { ...config, view, color_scheme: cs };
    this.writeDelay = Number.isFinite(Number(config.write_delay)) ? Math.max(300, Number(config.write_delay)) : 1500;
    this.requestRender();
  }

  get config() {
    return this._config;
  }

  get store() {
    return this._store;
  }

  set hass(h) {
    const prev = this._hass;
    this._hass = h;
    if (!h) return;
    const store = getStore(h);
    // language first: the first plants request must already carry it
    if (store) store.setLanguage(langOf(h));
    if (store !== this._store) {
      if (this._unlisten) this._unlisten();
      this._unlisten = null;
      // New connection: nothing queued on the old one may still be sent.
      if (this._store) this._resetViews();
      this._store = store;
      if (this.isConnected && store) this._unlisten = store.addListener(this._onStore);
    }
    if (this._relevantChange(prev, h)) this.requestRender();
  }

  get hass() {
    return this._hass;
  }

  _relevantChange(prev, h) {
    if (!prev || !this._ctxBuilt) return true;
    if (prev.locale !== h.locale || prev.language !== h.language) return true;
    if ((prev.themes && prev.themes.darkMode) !== (h.themes && h.themes.darkMode)) return true;
    for (const [eid, st] of this._watch) if (h.states[eid] !== st) return true;
    return false;
  }

  connectedCallback() {
    if (this._store && !this._unlisten) this._unlisten = this._store.addListener(this._onStore);
    for (const el of Object.values(this._containers)) if (this._ro) this._ro.observe(el);
    this.requestRender();
  }

  disconnectedCallback() {
    if (this._unlisten) this._unlisten();
    this._unlisten = null;
    if (this._ro) this._ro.disconnect();
    // Removed from the page: queued −/+ writes the user can no longer see
    // are dropped (an explicitly started curve Apply still completes).
    for (const v of Object.values(this._views)) if (v.cancelWrites) v.cancelWrites();
  }

  /** Throw away all view state (queued writes, pending edits); rebuilt on the next render. */
  _resetViews() {
    for (const v of Object.values(this._views)) if (v.dispose) v.dispose();
    this._views = {};
    this._skeleton = null;
  }

  _resized(entries) {
    let changed = false;
    for (const e of entries) {
      const key = e.target.getAttribute("data-v");
      const w = Math.round(e.contentRect.width);
      if (key && w > 0 && Math.abs((this._widths[key] || 0) - w) >= 2) {
        this._widths[key] = w;
        changed = true;
      }
    }
    if (changed) this.requestRender();
  }

  stateOf(eid) {
    return eid && this._hass ? this._hass.states[eid] : undefined;
  }

  get i18n() {
    return makeI18n(this._hass);
  }

  call(domain, service, data) {
    if (!this._hass) return Promise.reject(new Error("not connected"));
    return this._hass.callService(domain, service, data);
  }

  moreInfo(entityId) {
    if (entityId) fire(this, "hass-more-info", { entityId });
  }

  getCardSize() {
    switch (this._config.view) {
      case "plant": return 5;
      case "controls": return 3 + 2 * ((this.ctx && this.ctx.sec.circuits.length) || 2);
      case "efficiency": return 9;
      default: return 16;
    }
  }

  getGridOptions() {
    const v = this._config.view;
    return { columns: 12, min_columns: v === "all" || v === "plant" ? 6 : 4 };
  }

  _viewList() {
    const v = this._config.view;
    return v === "all" ? ["plant", "controls", "efficiency"] : [v];
  }

  _ensureSkeleton() {
    const views = this._viewList();
    const key = views.join(",");
    if (this._skeleton === key) return;
    this._skeleton = key;
    if (this._ro) this._ro.disconnect();
    for (const v of Object.values(this._views)) if (v.dispose) v.dispose();
    this._views = {};
    this._containers = {};
    this._widths = {};
    const box = (v) => `<ha-card class="card-${v}"><div class="v" data-v="${v}"></div></ha-card>`;
    const body = views.length === 1 ? box(views[0]) : `<div class="all">${box("plant")}<div class="bc">${box("controls")}${box("efficiency")}</div></div>`;
    this.shadowRoot.innerHTML = `<style>${CSS}${TINTS}</style><style class="vars"></style><div class="msg" hidden></div>${body}`;
    this._schemeKey = null; // the new <style class="vars"> is empty
    for (const v of views) {
      const el = this.shadowRoot.querySelector(`.v[data-v="${v}"]`);
      this._containers[v] = el;
      const view = new VIEW_CLASSES[v](this);
      view.bind(el);
      this._views[v] = view;
      if (this._ro) this._ro.observe(el);
    }
  }

  _applyScheme(settings) {
    const scheme = resolveScheme(this._config.color_scheme, settings && settings.color_scheme);
    const dark = !!(this._hass && this._hass.themes && this._hass.themes.darkMode);
    const key = `${scheme}:${dark}`;
    if (key === this._schemeKey) return;
    this._schemeKey = key;
    const vars = schemeVars(scheme, dark);
    const style = this.shadowRoot.querySelector("style.vars");
    if (style) style.textContent = `:host{${Object.entries(vars).map(([k, v]) => `${k}:${v}`).join(";")}}`;
    this.toggleAttribute("dark", dark);
    this.setAttribute("scheme", scheme);
  }

  _message(text) {
    this._ensureSkeleton();
    const msg = this.shadowRoot.querySelector(".msg");
    for (const el of Object.values(this._containers)) el.closest("ha-card").hidden = true;
    const all = this.shadowRoot.querySelector(".all");
    if (all) all.hidden = true;
    msg.hidden = false;
    morph(msg, `<ha-card><div class="empty">${esc(text)}</div></ha-card>`);
  }

  _render() {
    if (!this._hass || !this.isConnected) return;
    const store = this._store;
    const plant = store && store.plants ? pickPlant(store.plants, this._config) : null;
    // Control state (queued writes, pending curve edits, busy chips) belongs
    // to one plant: another plant gets fresh views, nothing carries over.
    if (plant && this._plantId !== undefined && plant.plant_id !== this._plantId) this._resetViews();
    if (plant) this._plantId = plant.plant_id;
    this._ensureSkeleton();
    this._applyScheme(store && store.settings);
    const I = makeI18n(this._hass);
    if (!store) return this._message(I.t("not_loaded"));
    if (!store.plants) return this._message(store.error ? I.t("not_loaded") : I.t("loading"));
    if (!plant) return this._message(I.t("no_plant"));
    const msg = this.shadowRoot.querySelector(".msg");
    if (!msg.hidden) {
      msg.hidden = true;
      for (const el of Object.values(this._containers)) el.closest("ha-card").hidden = false;
      const all = this.shadowRoot.querySelector(".all");
      if (all) all.hidden = false;
    }
    const ev = store.liveFor(plant.plant_id);
    const live = ev || { values: {}, available: plant.available };
    const circuitsFilter = this._config.circuits || circuitsForDevice(plant, this._config.device_id);
    const sec = sections(plant, live, { circuits: circuitsFilter });
    // The feed is current; the one-shot payload only counts until it arrives
    // (a controller that recovered must not stay disabled until a refetch).
    const available = ev ? ev.available !== false : plant.available !== false;
    const watch = new Map();
    const hass = this._hass;
    const ctx = {
      hass,
      I,
      plant,
      live,
      sec,
      caps: capabilities(plant, live),
      mode: hpMode(live.values && live.values["hp.status"]),
      config: this._config,
      uid: this._uid,
      disabled: !available,
      banner: available ? "" : `<div class="banner" role="status">${esc(I.t("unavailable"))}</div>`,
      state: (eid) => {
        if (!eid) return undefined;
        const st = hass.states[eid];
        watch.set(eid, st);
        return st;
      },
    };
    if (plant.flash_writes) ctx.state(plant.flash_writes);
    this.ctx = ctx;
    for (const [name, view] of Object.entries(this._views)) {
      if (view.plantId === undefined) view.plantId = plant.plant_id;
      const el = this._containers[name];
      const width = this._widths[name] || el.clientWidth || 400;
      const vctx = { ...ctx, width, narrow: width < 520 };
      this.ctx = vctx;
      try {
        morph(el, view.render(vctx));
        if (view.attach) view.attach(el);
      } catch (err) {
        // eslint-disable-next-line no-console
        console.error("ochsner-heatpump: render failed", err);
        el.textContent = String(err && err.message ? err.message : err);
      }
    }
    this.ctx = ctx;
    this._watch = watch;
    this._ctxBuilt = true;
  }
}

export class OchsnerPlantCard extends OchsnerHeatpumpCard {
  static fixedView = "plant";
  static get tagName() { return "ochsner-plant-card"; }
}
export class OchsnerControlCard extends OchsnerHeatpumpCard {
  static fixedView = "controls";
  static get tagName() { return "ochsner-control-card"; }
}
export class OchsnerEfficiencyCard extends OchsnerHeatpumpCard {
  static fixedView = "efficiency";
  static get tagName() { return "ochsner-efficiency-card"; }
}

export function registerCards() {
  const defs = [
    [OchsnerHeatpumpCard, "card_all"],
    [OchsnerPlantCard, "card_plant"],
    [OchsnerControlCard, "card_control"],
    [OchsnerEfficiencyCard, "card_eff"],
  ];
  const lang = docLang();
  window.customCards = window.customCards || [];
  for (const [cls, key] of defs) {
    if (!customElements.get(cls.tagName)) customElements.define(cls.tagName, cls);
    if (!window.customCards.some((c) => c.type === cls.tagName)) {
      window.customCards.push({
        type: cls.tagName,
        name: translate(lang, key + "_name"),
        description: translate(lang, key + "_desc"),
        preview: true,
        documentationURL: "https://github.com/permissionBRICK/OTS-HomeAssistant",
      });
    }
  }
}
