// Colour schemes → CSS custom properties set on each card host.
// "ha": derived from the active HA theme (state colours, text, card bg).
// "ochsner": the Ochsner Smart app palette (dark/light variant).

const CHART = {
  light: { "--ohp-el": "#2a78d6", "--ohp-amb": "#1baf7a" },
  dark: { "--ohp-el": "#3987e5", "--ohp-amb": "#199e70" },
};

// Neutrals shared by "ha" and light "ochsner": everything from the theme.
const THEME_NEUTRALS = {
  "--ohp-ink": "var(--primary-text-color, #212121)",
  "--ohp-ink2": "var(--secondary-text-color, #727272)",
  "--ohp-card": "var(--ha-card-background, var(--card-background-color, #fff))",
  "--ohp-card2": "color-mix(in srgb, var(--primary-text-color, #212121) 5%, var(--ohp-card))",
  "--ohp-line": "var(--divider-color, rgba(127, 127, 127, 0.25))",
  "--ohp-pipe": "color-mix(in srgb, var(--primary-text-color, #212121) 12%, var(--ohp-card))",
  "--ohp-pipe-off": "color-mix(in srgb, var(--primary-text-color, #212121) 24%, var(--ohp-card))",
  "--ohp-grid": "color-mix(in srgb, var(--primary-text-color, #212121) 9%, transparent)",
  "--ohp-tip-bg": "var(--ohp-card)",
  "--ohp-card-border": "var(--ha-card-border-color, var(--divider-color, rgba(127, 127, 127, 0.25)))",
};

export const SCHEMES = {
  ha: {
    common: {
      ...THEME_NEUTRALS,
      "--ohp-heat": "var(--state-climate-heat-color, var(--deep-orange-color, #ff6f22))",
      "--ohp-cool": "var(--state-climate-cool-color, var(--blue-color, #2196f3))",
      "--ohp-heatcool": "var(--state-climate-heat_cool-color, var(--amber-color, #ffc107))",
      "--ohp-dhw": "var(--amber-color, #ffc107)",
      "--ohp-eco": "var(--state-water_heater-eco-color, var(--green-color, #4caf50))",
      "--ohp-warn": "var(--warning-color, #ffa600)",
      "--ohp-err": "var(--error-color, #db4437)",
      "--ohp-ok": "var(--success-color, #43a047)",
      "--ohp-idle": "var(--state-inactive-color, var(--disabled-color, #9e9e9e))",
      "--ohp-defrost": "var(--cyan-color, #00bcd4)",
      "--ohp-aux": "var(--purple-color, #926bc7)",
      "--ohp-pool": "var(--teal-color, #009688)",
      "--ohp-on-heat": "#111",
      "--ohp-on-cool": "#111",
      "--ohp-on-dhw": "#111",
      "--ohp-on-eco": "#111",
    },
    light: {},
    dark: {},
  },
  ochsner: {
    common: {
      "--ohp-dhw": "#FFD953",
      "--ohp-eco": "#23EA97",
      "--ohp-warn": "#F9A42C",
      "--ohp-err": "#F9A42C",
      "--ohp-ok": "#4caf50",
      "--ohp-defrost": "#6fd6e8",
      "--ohp-aux": "#c49bff",
      "--ohp-pool": "#4dd0e1",
      "--ohp-on-heat": "#111",
      "--ohp-on-cool": "#fff",
      "--ohp-on-dhw": "#111",
      "--ohp-on-eco": "#111",
    },
    dark: {
      "--ohp-heat": "#FE7675",
      "--ohp-cool": "#7877F2",
      "--ohp-heatcool": "#FFD953",
      "--ohp-idle": "#7d858d",
      "--ohp-ink": "#e8eaed",
      "--ohp-ink2": "#9aa1a9",
      "--ohp-card": "#1c1f23",
      "--ohp-card2": "#24282d",
      "--ohp-line": "#343a41",
      "--ohp-pipe": "#15181b",
      "--ohp-pipe-off": "#3a4047",
      "--ohp-grid": "#2b3036",
      "--ohp-tip-bg": "#0b0d0f",
      "--ohp-card-border": "#262a2f",
    },
    light: {
      ...THEME_NEUTRALS,
      "--ohp-heat": "#B11512",
      "--ohp-cool": "#3C3ACF",
      "--ohp-heatcool": "#c79a00",
      "--ohp-idle": "#8a9097",
      "--ohp-eco": "#0f9e62",
      "--ohp-dhw": "#e0b100",
      "--ohp-aux": "#7b4fd0",
      "--ohp-pool": "#0097a7",
      "--ohp-defrost": "#0097a7",
      "--ohp-on-heat": "#fff",
      "--ohp-on-cool": "#fff",
    },
  },
};

export function resolveScheme(configScheme, settingsScheme) {
  const s = configScheme && configScheme !== "auto" ? configScheme : settingsScheme;
  return s === "ochsner" ? "ochsner" : "ha";
}

/** CSS custom properties for a scheme and light/dark mode. */
export function schemeVars(scheme, dark) {
  const def = SCHEMES[scheme] || SCHEMES.ha;
  const mode = dark ? "dark" : "light";
  return { ...def.common, ...def[mode], ...CHART[mode] };
}

export function styleText(vars) {
  return Object.entries(vars).map(([k, v]) => `${k}:${v}`).join(";");
}

/** Colour variable for a heat-pump mode. */
export function modeColor(mode) {
  switch (mode) {
    case "heat": return "var(--ohp-heat)";
    case "cool": return "var(--ohp-cool)";
    case "dhw": return "var(--ohp-dhw)";
    case "defrost": return "var(--ohp-defrost)";
    case "fault": return "var(--ohp-err)";
    default: return "var(--ohp-idle)";
  }
}

/** Readable text colour for a hue on the card: mixed towards the text colour. */
export function textTint(col) {
  return `color-mix(in srgb, ${col} 72%, var(--ohp-ink))`;
}
