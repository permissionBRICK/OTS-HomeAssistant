import test from "node:test";
import assert from "node:assert/strict";
import { SCHEMES, modeColor, resolveScheme, schemeVars } from "../../custom_components/ochsner_local_ots/frontend/lib/colors.js";
import { STRINGS, makeI18n, translate } from "../../custom_components/ochsner_local_ots/frontend/lib/i18n.js";

test("colour schemes define the same variables in light and dark", () => {
  const ref = Object.keys(schemeVars("ha", false)).sort();
  for (const s of Object.keys(SCHEMES)) for (const dark of [false, true]) assert.deepEqual(Object.keys(schemeVars(s, dark)).sort(), ref, `${s} dark=${dark}`);
  for (const k of ["--ohp-heat", "--ohp-cool", "--ohp-dhw", "--ohp-eco", "--ohp-err", "--ohp-idle", "--ohp-ink", "--ohp-card", "--ohp-el", "--ohp-amb"]) assert.ok(ref.includes(k), k);
});

test("ha scheme maps to HA theme variables with fallbacks", () => {
  const v = schemeVars("ha", true);
  assert.match(v["--ohp-heat"], /^var\(--state-climate-heat-color, .+\)$/);
  assert.match(v["--ohp-cool"], /^var\(--state-climate-cool-color, /);
  assert.match(v["--ohp-heatcool"], /--state-climate-heat-cool-color/, "HA's variable name (hyphen, not heat_cool)");
  assert.equal(v["--ohp-auto-bg"], "var(--ohp-heatcool)", "Auto uses the theme's heat/cool colour");
  assert.match(schemeVars("ochsner", true)["--ohp-auto-bg"], /linear-gradient\(90deg, var\(--ohp-heat\), var\(--ohp-cool\)\)/);
  assert.match(v["--ohp-err"], /--error-color/);
  assert.match(v["--ohp-warn"], /--warning-color/);
  assert.match(v["--ohp-ok"], /--success-color/);
  assert.match(v["--ohp-idle"], /--state-inactive-color.*--disabled-color/);
  assert.match(v["--ohp-ink"], /--primary-text-color/);
  assert.match(v["--ohp-ink2"], /--secondary-text-color/);
  assert.match(v["--ohp-card"], /--ha-card-background.*--card-background-color/);
  assert.match(v["--ohp-line"], /--divider-color/);
  assert.equal(schemeVars("ha", false)["--ohp-el"], "#2a78d6");
  assert.equal(schemeVars("ha", true)["--ohp-el"], "#3987e5");
  assert.equal(schemeVars("ha", false)["--ohp-amb"], "#1baf7a");
  assert.equal(schemeVars("ha", true)["--ohp-amb"], "#199e70");
});

test("ochsner scheme uses the app palette", () => {
  const d = schemeVars("ochsner", true), l = schemeVars("ochsner", false);
  assert.equal(d["--ohp-heat"], "#FE7675");
  assert.equal(d["--ohp-cool"], "#7877F2");
  assert.equal(l["--ohp-heat"], "#B11512");
  assert.equal(l["--ohp-cool"], "#3C3ACF");
  assert.equal(d["--ohp-dhw"], "#FFD953");
  assert.equal(d["--ohp-eco"], "#23EA97");
  assert.equal(d["--ohp-err"], "#F9A42C");
  assert.equal(d["--ohp-card"], "#1c1f23");
  assert.match(l["--ohp-card"], /--card-background-color/, "light uses theme neutrals");
});

test("scheme resolution: card config overrides integration setting", () => {
  assert.equal(resolveScheme("auto", "ochsner"), "ochsner");
  assert.equal(resolveScheme(undefined, undefined), "ha");
  assert.equal(resolveScheme("ha", "ochsner"), "ha");
  assert.equal(resolveScheme("ochsner", "ha"), "ochsner");
  assert.equal(modeColor("heat"), "var(--ohp-heat)");
  assert.equal(modeColor("standby"), "var(--ohp-idle)");
});

test("i18n: DE and EN have the same keys and placeholders", () => {
  const de = Object.keys(STRINGS.de).sort(), en = Object.keys(STRINGS.en).sort();
  assert.deepEqual(de, en);
  const ph = (s) => (s.match(/\{\w+\}/g) || []).sort().join();
  for (const k of de) assert.equal(ph(STRINGS.de[k]), ph(STRINGS.en[k]), k);
  for (const k of de) assert.ok(STRINGS.de[k].length && STRINGS.en[k].length, k);
});

test("i18n: language pick, placeholders, number formatting", () => {
  const de = makeI18n({ locale: { language: "de-AT", number_format: "language" } });
  const en = makeI18n({ locale: { language: "en-GB" } });
  const fr = makeI18n({ locale: { language: "fr" } });
  assert.equal(de.lang, "de");
  assert.equal(en.lang, "en");
  assert.equal(fr.lang, "en");
  assert.equal(de.t("now_label", { flow: "26,8°", out: "12,2°" }), "jetzt 26,8° bei 12,2°");
  assert.equal(en.t("apply"), "Apply");
  assert.equal(translate("de", "nonexistent"), "nonexistent");
  assert.equal(de.num(21.456), "21,5");
  assert.equal(en.num(21.456), "21.5");
  assert.equal(makeI18n({ locale: { language: "de" } }).num(1284, 0), "1.284");
  assert.match(de.num(1284, 0), /^1\s284$/, "de-AT groups with a (narrow) space");
  assert.equal(de.temp(-15), "−15,0°");
  assert.equal(de.num(-0.01), "0,0");
  assert.equal(de.signed(0), "±0,0");
  assert.equal(de.signed(0.5), "+0,5");
  assert.equal(de.signed(-1.5), "−1,5");
  assert.equal(de.auto(36), "36");
  assert.equal(de.auto(16.7), "16,7");
  assert.equal(de.num(undefined), "–");
  const dc = makeI18n({ locale: { language: "en", number_format: "decimal_comma" } });
  assert.equal(dc.num(1.5), "1,5");
  assert.equal(dc.lang, "en");
});
