// Small inline SVG icons (stroke-based, drawn around a centre point).

export function kindIcon(kind, x, y, col, sw = 2) {
  const g = (d) => `<path d="${d}" fill="none" stroke="${col}" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/>`;
  switch (kind) {
    case "floor":
      return g(`M${x - 9} ${y + 7} H${x + 9} M${x - 9} ${y - 3} q3 -5 6 0 t6 0 t6 0 M${x - 9} ${y + 2} q3 -5 6 0 t6 0 t6 0`);
    case "radiator":
      return g(`M${x - 9} ${y - 7} V${y + 7} M${x - 3} ${y - 7} V${y + 7} M${x + 3} ${y - 7} V${y + 7} M${x + 9} ${y - 7} V${y + 7} M${x - 11} ${y + 9} H${x + 11}`);
    case "pool":
      return g(`M${x - 10} ${y + 2} q2.5 -4 5 0 t5 0 t5 0 t5 0 M${x - 10} ${y + 7} q2.5 -4 5 0 t5 0 t5 0 t5 0 M${x - 4} ${y - 3} V${y - 9} M${x + 4} ${y - 3} V${y - 9}`);
    case "snow":
      return g(`M${x} ${y - 9} V${y + 9} M${x - 8} ${y - 4.5} L${x + 8} ${y + 4.5} M${x + 8} ${y - 4.5} L${x - 8} ${y + 4.5}`);
    case "off":
      return g(`M${x} ${y - 9} V${y - 1} M${x - 6} ${y - 6} a8.5 8.5 0 1 0 12 0`);
    case "party":
      return g(`M${x - 8} ${y + 9} L${x - 3} ${y - 5} L${x + 6} ${y + 4} Z M${x + 2} ${y - 7} l2 -2 M${x + 6} ${y - 3} l3 -1 M${x + 5} ${y - 9} l1 -1`);
    case "vacation":
      return g(`M${x - 9} ${y + 8} H${x + 9} M${x} ${y + 8} V${y - 4} M${x - 8} ${y - 3} q8 -10 16 0 z`);
    case "fault":
      return g(`M${x} ${y - 9} L${x + 9} ${y + 7} H${x - 9} Z M${x} ${y - 3} V${y + 2} M${x} ${y + 4.5} v0.5`);
    case "frost":
      return g(`M${x} ${y - 9} V${y + 9} M${x - 8} ${y - 4.5} L${x + 8} ${y + 4.5} M${x + 8} ${y - 4.5} L${x - 8} ${y + 4.5} M${x - 3} ${y - 9} l3 3 l3 -3`);
    default:
      return g(`M${x - 9} ${y + 8} V${y - 2} L${x} ${y - 9} L${x + 9} ${y - 2} V${y + 8} Z`);
  }
}

/** Icon in its own 24px svg, for HTML contexts. */
export function iconSvg(kind, col, size = 16, sw = 2) {
  return `<svg width="${size}" height="${size}" viewBox="-12 -12 24 24" aria-hidden="true">${kindIcon(kind, 0, 0, col, sw)}</svg>`;
}

export const leafSvg = (size = 12) =>
  `<svg class="leaf" width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19C5 9 11 4 20 4c0 9-5 15-15 15z" fill="var(--ohp-eco)" stroke="var(--ohp-card2)" stroke-width="1.5"/></svg>`;

export const showerSvg = (col, size = 16) =>
  `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 11a6 6 0 0 1 12 0zM8 14v2M12 14v2M16 14v2M8 19v1M12 19v1M16 19v1M12 5V3h6v18" fill="none" stroke="${col}" stroke-width="2" stroke-linecap="round"/></svg>`;

const HERO = {
  heat: (c) => `<path d="M12 2c1 4 6 6 6 12a6 6 0 0 1-12 0c0-3 1.5-5 3-6 0 2 1 3 2 3 0-3-1-6 1-9z" fill="${c}"/>`,
  cool: (c) => `<path d="M12 2v20M3.3 7l17.4 10M20.7 7L3.3 17" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`,
  dhw: (c) => `<path d="M12 3c3 4 6 7.5 6 11a6 6 0 0 1-12 0c0-3.5 3-7 6-11z" fill="${c}"/>`,
  defrost: (c) => `<path d="M12 2v20M3.3 7l17.4 10M20.7 7L3.3 17M9 3.5l3 2.5 3-2.5M9 20.5l3-2.5 3 2.5" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`,
  fault: (c) => `<path d="M12 3L22 20H2z M12 9v5 M12 17v.5" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`,
  off: (c) => `<path d="M12 3v8 M6.3 6.3a8 8 0 1 0 11.4 0" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`,
  standby: (c) => `<path d="M12 4a8 8 0 1 0 0.01 0 M12 8v4l3 2" fill="none" stroke="${c}" stroke-width="2" stroke-linecap="round"/>`,
};

export function heroSvg(mode, col, size = 24) {
  const f = HERO[mode] || HERO.standby;
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" aria-hidden="true">${f(col)}</svg>`;
}

export const menuSvg = `<svg width="24" height="24" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
export const boltSvg = `<svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L4 14h7l-1 8 9-12h-7z" fill="currentColor"/></svg>`;
export const chevronSvg = `<svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
