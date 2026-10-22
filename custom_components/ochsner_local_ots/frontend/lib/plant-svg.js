// Plant schematic SVG (card A): outdoor unit, pipes, buffer, DHW tank and one
// row per circuit/pool. Two layouts: wide (mockup, circuits right of the
// buffer) and narrow (sources on top, circuit rows below). Pure string
// builder; `vm` is prepared by the plant view.

import { esc } from "./dom.js";
import { kindIcon } from "./icons.js";

const C = {
  heat: "var(--ohp-heat)",
  cool: "var(--ohp-cool)",
  dhw: "var(--ohp-dhw)",
  defrost: "var(--ohp-defrost)",
  standby: "var(--ohp-idle)",
  pool: "var(--ohp-pool)",
};

export const textWidth = (s, size) => String(s).length * size * 0.56;

export function fitText(s, maxW, size) {
  const str = String(s || "");
  if (textWidth(str, size) <= maxW) return str;
  const n = Math.max(1, Math.floor(maxW / (size * 0.56)) - 1);
  return str.slice(0, n) + "…";
}

const label = (x, y, t, anchor = "middle", cls = "lbl") =>
  `<text x="${r(x)}" y="${r(y)}" text-anchor="${anchor}" class="${cls}">${esc(t)}</text>`;

const r = (v) => Math.round(v * 10) / 10;

function pipe(d, col, active, w = 5, extra = "") {
  return (
    `<path d="${d}" fill="none" stroke="var(--ohp-pipe)" stroke-width="${w + 4}" stroke-linecap="round"/>` +
    `<path d="${d}" fill="none" stroke="${active ? col : "var(--ohp-pipe-off)"}" stroke-width="${w}" stroke-linecap="round"${active ? ` class="flow${extra}"` : ""}/>`
  );
}

function fan(cx, cy, rad, col, spin) {
  const blade = (a) => `<path d="M${cx} ${cy} q ${r(rad * 0.3)} ${r(-rad * 0.75)} 0 ${r(-rad * 0.85)} q ${r(-rad * 0.35)} ${r(rad * 0.15)} 0 ${r(rad * 0.85)}" fill="${col}" fill-opacity=".8" transform="rotate(${a} ${cx} ${cy})"/>`;
  return (
    `<circle cx="${cx}" cy="${cy}" r="${rad}" fill="none" stroke="var(--ohp-line)" stroke-width="2"/>` +
    `<g class="fan${spin ? " spin" : ""}" style="transform-origin:${cx}px ${cy}px">${[0, 120, 240].map(blade).join("")}` +
    `<circle cx="${cx}" cy="${cy}" r="${r(rad * 0.15)}" fill="var(--ohp-ink2)"/></g>`
  );
}

function zigzag(x, y, on) {
  return `<path d="M${x} ${y} l6 -8 l6 8 l6 -8 l6 8 l6 -8 l6 8" fill="none" stroke="${on ? "var(--ohp-aux)" : "var(--ohp-pipe-off)"}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"><title>${on ? "on" : "off"}</title></path>`;
}

function clickable(entity, aria, body) {
  if (!entity) return `<g>${body}</g>`;
  return `<g class="hit" data-more="${esc(entity)}" tabindex="0" role="button" aria-label="${esc(aria)}">${body}</g>`;
}

function row(rw, vm, x, y, w, h, narrow) {
  const col = rw.type === "pool" ? C.pool : rw.cooling ? C.cool : C.heat;
  const chipSize = 11.5;
  const tw = textWidth(rw.chip || "", chipSize) + 18;
  let s = `<rect x="${r(x)}" y="${r(y - h / 2)}" width="${r(w)}" height="${h}" rx="12" class="rowbg"/>`;
  s += `<circle cx="${r(x + 26)}" cy="${r(y)}" r="17" fill="${rw.active ? col : "var(--ohp-pipe-off)"}" fill-opacity=".18"/>`;
  s += kindIcon(rw.kind, x + 26, y, rw.active ? col : "var(--ohp-ink2)");
  const chipX = x + w - 10 - tw;
  const chipY = narrow ? y - 20 : y - 12;
  const chipH = narrow ? 20 : 24;
  const nameMax = chipX - (x + 52) - 8;
  s += label(x + 52, y - 4, fitText(rw.name, nameMax, 14), "start", "name");
  const subMax = narrow ? w - 60 : chipX - (x + 52) - 8;
  s += label(x + 52, y + 15, fitText(rw.sub, subMax, 12), "start", "lbl dim");
  if (rw.chip) {
    s += `<rect x="${r(chipX)}" y="${r(chipY)}" width="${r(tw)}" height="${chipH}" rx="${chipH / 2}" fill="${rw.chipColor || (rw.active ? col : "var(--ohp-pipe-off)")}" fill-opacity=".2"/>`;
    s += label(chipX + tw / 2, chipY + chipH / 2 + 4, rw.chip, "middle", "chip");
  }
  return clickable(rw.entity, rw.name, s);
}

/**
 * vm = { id, mode, spin, moving, hpLabel, flowT, retT, flowShort, retShort,
 *        buffer: {label, t, aux, auxOn, entity} | null,
 *        aux: {on} | null  (aux heater without buffer),
 *        dhw: {label, t, sub, active, entity} | null,
 *        hpEntity, rows: [{type, name, kind, sub, chip, active, cooling, entity}] }
 */
export function plantSVG(vm, width) {
  const narrow = width < 520;
  return narrow ? narrowLayout(vm, Math.max(300, Math.round(width))) : wideLayout(vm, Math.min(1600, Math.max(600, Math.round(width))));
}

function hpColor(vm) {
  return C[vm.mode] || C.standby;
}

function defs(vm, toBuf, col) {
  const id = vm.id;
  return `<defs><linearGradient id="buf${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="${toBuf ? col : "var(--ohp-idle)"}" stop-opacity=".85"/>
      <stop offset="1" stop-color="${toBuf ? col : "var(--ohp-idle)"}" stop-opacity=".18"/></linearGradient>
    <linearGradient id="dhw${id}" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="var(--ohp-dhw)" stop-opacity=".9"/><stop offset="1" stop-color="var(--ohp-dhw)" stop-opacity=".2"/></linearGradient></defs>`;
}

function circuitColor(vm) {
  return vm.mode === "cool" || vm.rows.some((x) => x.cooling && x.active) ? C.cool : C.heat;
}

function wideLayout(vm, W) {
  const col = hpColor(vm);
  const moving = vm.moving;
  const toBuf = moving && vm.mode !== "dhw";
  const rows = vm.rows;
  const rowH = 54, gap = 8;
  const block = rows.length * (rowH + gap) - gap;
  let H = Math.max(vm.dhw ? 250 : 230, block + 24);
  const cy = vm.dhw ? Math.max(H / 2, 162) : H / 2;
  if (vm.dhw) H = Math.max(H, cy + 92);
  const fy = cy - 30, ry = cy + 30;
  const bufX = 240, bufW = 64, trunkX = 336, rowX = 372, vx = 176;
  const flowEnd = vm.buffer ? bufX : trunkX;
  let s = `<svg viewBox="0 0 ${W} ${r(H)}" class="plant" role="img" aria-label="${esc(vm.aria || "")}">` + defs(vm, toBuf, col);

  if (vm.dhw && vm.dhw.active) {
    s += pipe(`M${vx} ${fy} H${flowEnd}`, col, false);
    s += pipe(`M140 ${fy} H${vx}`, col, moving);
  } else s += pipe(`M140 ${fy} H${flowEnd}`, col, moving && (vm.mode !== "dhw" || !vm.dhw));
  s += pipe(`M140 ${ry} H${flowEnd}`, col, toBuf, 4, " rev");
  const lx = ((vm.dhw ? vx : 140) + flowEnd) / 2;
  if (vm.flowT) s += label(lx, fy - 12, `${vm.flowShort} ${vm.flowT}`);
  if (vm.retT) s += label((140 + flowEnd) / 2, ry + 22, `${vm.retShort} ${vm.retT}`);

  if (vm.dhw) {
    const top = cy - 152;
    let d = pipe(`M${vx} ${fy} V${top + 72}`, C.dhw, vm.dhw.active && moving, 4);
    d += `<path d="M${vx - 8} ${fy - 7} L${vx + 8} ${fy + 7} M${vx + 8} ${fy - 7} L${vx - 8} ${fy + 7}" stroke="var(--ohp-ink2)" stroke-width="2"/>`;
    let t = `<rect x="${vx - 26}" y="${r(top)}" width="52" height="72" rx="20" fill="url(#dhw${vm.id})" stroke="var(--ohp-line)"/>`;
    t += label(vx + 36, top + 22, vm.dhw.label, "start", "lbl dim");
    t += label(vx + 36, top + 42, vm.dhw.t, "start", "big");
    if (vm.dhw.sub) t += label(vx + 36, top + 58, vm.dhw.sub, "start", "lbl dim");
    s += d + clickable(vm.dhw.entity, vm.dhw.label, t);
  }

  if (vm.buffer) {
    let b = `<rect x="${bufX}" y="${r(cy - 82)}" width="${bufW}" height="164" rx="30" fill="url(#buf${vm.id})" stroke="var(--ohp-line)"/>`;
    b += label(bufX + bufW / 2, cy - 50, vm.buffer.label, "middle", "lbl ink");
    if (vm.buffer.t) b += label(bufX + bufW / 2, cy - 28, vm.buffer.t, "middle", "big");
    if (vm.buffer.aux) b += zigzag(bufX + 14, cy + 52, vm.buffer.auxOn);
    s += clickable(vm.buffer.entity, vm.buffer.label, b);
  }

  const ys = rows.map((_, i) => (H - block) / 2 + i * (rowH + gap) + rowH / 2);
  const anyActive = rows.some((x) => x.active);
  const ccol = circuitColor(vm);
  if (rows.length) {
    const tTop = Math.min(fy, ...ys), tBot = Math.max(fy, ...ys);
    if (vm.buffer) s += pipe(`M${bufX + bufW} ${fy} H${trunkX}`, ccol, anyActive);
    s += pipe(`M${trunkX} ${r(tTop)} V${r(tBot)}`, ccol, anyActive);
    rows.forEach((rw, i) => {
      const y = ys[i];
      const pc = rw.type === "pool" ? C.pool : rw.cooling ? C.cool : C.heat;
      s += pipe(`M${trunkX} ${r(y)} H${rowX}`, pc, rw.active);
      s += row(rw, vm, rowX, y, W - rowX - 6, rowH, false);
    });
  }

  let hp = `<rect x="12" y="${r(cy - 62)}" width="128" height="124" rx="16" class="rowbg"/>`;
  hp += fan(76, r(cy - 8), 40, vm.spin ? col : "var(--ohp-ink2)", vm.spin);
  hp += label(76, cy + 50, vm.hpLabel, "middle", "lbl dim");
  if (vm.aux && !vm.buffer) hp += zigzag(100, cy - 44, vm.aux.on);
  s += clickable(vm.hpEntity, vm.hpLabel, hp);
  return s + `</svg>`;
}

function narrowLayout(vm, W) {
  const col = hpColor(vm);
  const moving = vm.moving;
  const toBuf = moving && vm.mode !== "dhw";
  const rows = vm.rows;
  const rowH = 54, gap = 8;
  const cyTop = vm.dhw ? 152 : 82;
  const fy = cyTop - 28, ry = cyTop + 28;
  const hpX = 8, hpW = 104, hpR = hpX + hpW;
  const trunkX = W - 16;
  const bufW = 52;
  const bufX = Math.round((hpR + trunkX) / 2 + 4 - bufW / 2);
  const flowEnd = vm.buffer ? bufX : trunkX;
  const vx = Math.round(hpR + (bufX - hpR) * 0.42);
  const rowsTop = cyTop + 70 + 16;
  const rowX = 8, rowW = W - 42;
  const H = rows.length ? rowsTop + rows.length * (rowH + gap) - gap + 6 : cyTop + 76;
  let s = `<svg viewBox="0 0 ${W} ${r(H)}" class="plant narrow" role="img" aria-label="${esc(vm.aria || "")}">` + defs(vm, toBuf, col);

  if (vm.dhw && vm.dhw.active) {
    s += pipe(`M${vx} ${fy} H${flowEnd}`, col, false);
    s += pipe(`M${hpR} ${fy} H${vx}`, col, moving);
  } else s += pipe(`M${hpR} ${fy} H${flowEnd}`, col, moving && (vm.mode !== "dhw" || !vm.dhw));
  s += pipe(`M${hpR} ${ry} H${vm.buffer ? bufX : trunkX - 30}`, col, toBuf, 4, " rev");
  const lx = (hpR + (vm.buffer ? bufX : Math.min(trunkX, hpR + 120))) / 2 + 2;
  if (vm.flowT) s += label(lx, fy + 18, `${vm.flowShort} ${vm.flowT}`, "middle", "lbl sm");
  if (vm.retT) s += label(lx, ry - 8, `${vm.retShort} ${vm.retT}`, "middle", "lbl sm");

  if (vm.dhw) {
    const top = cyTop - 144;
    let d = pipe(`M${vx} ${fy} V${top + 70}`, C.dhw, vm.dhw.active && moving, 4);
    d += `<path d="M${vx - 7} ${fy - 7} L${vx + 7} ${fy + 7} M${vx + 7} ${fy - 7} L${vx - 7} ${fy + 7}" stroke="var(--ohp-ink2)" stroke-width="2"/>`;
    let t = `<rect x="${vx - 24}" y="${top}" width="48" height="70" rx="19" fill="url(#dhw${vm.id})" stroke="var(--ohp-line)"/>`;
    t += label(vx + 32, top + 20, vm.dhw.label, "start", "lbl dim");
    t += label(vx + 32, top + 40, vm.dhw.t, "start", "big");
    if (vm.dhw.sub) t += label(vx + 32, top + 56, fitText(vm.dhw.sub, W - vx - 40, 12), "start", "lbl dim");
    s += d + clickable(vm.dhw.entity, vm.dhw.label, t);
  }

  if (vm.buffer) {
    let b = `<rect x="${bufX}" y="${cyTop - 70}" width="${bufW}" height="140" rx="25" fill="url(#buf${vm.id})" stroke="var(--ohp-line)"/>`;
    b += label(bufX + bufW / 2, cyTop - 40, vm.buffer.label, "middle", "lbl ink sm");
    if (vm.buffer.t) b += label(bufX + bufW / 2, cyTop - 20, vm.buffer.t, "middle", "big");
    if (vm.buffer.aux) b += zigzag(bufX + 8, cyTop + 46, vm.buffer.auxOn);
    s += clickable(vm.buffer.entity, vm.buffer.label, b);
  }

  const ys = rows.map((_, i) => rowsTop + i * (rowH + gap) + rowH / 2);
  const anyActive = rows.some((x) => x.active);
  const ccol = circuitColor(vm);
  if (rows.length) {
    if (vm.buffer) s += pipe(`M${bufX + bufW} ${fy} H${trunkX}`, ccol, anyActive);
    s += pipe(`M${trunkX} ${fy} V${r(ys[ys.length - 1])}`, ccol, anyActive);
    rows.forEach((rw, i) => {
      const y = ys[i];
      const pc = rw.type === "pool" ? C.pool : rw.cooling ? C.cool : C.heat;
      s += pipe(`M${trunkX} ${r(y)} H${rowX + rowW}`, pc, rw.active);
      s += row(rw, vm, rowX, y, rowW, rowH, true);
    });
  }

  let hp = `<rect x="${hpX}" y="${cyTop - 58}" width="${hpW}" height="116" rx="16" class="rowbg"/>`;
  hp += fan(hpX + hpW / 2, cyTop - 10, 34, vm.spin ? col : "var(--ohp-ink2)", vm.spin);
  hp += label(hpX + hpW / 2, cyTop + 44, fitText(vm.hpLabel, hpW - 10, 12), "middle", "lbl dim");
  if (vm.aux && !vm.buffer) hp += zigzag(hpX + hpW - 44, cyTop - 44, vm.aux.on);
  s += clickable(vm.hpEntity, vm.hpLabel, hp);
  return s + `</svg>`;
}
