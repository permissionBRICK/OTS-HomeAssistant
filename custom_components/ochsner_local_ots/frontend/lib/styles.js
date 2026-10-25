// Shared shadow-DOM styles for the cards and the panel.

export const CSS = `
:host {
  display: block;
  font-family: var(--ha-font-family-body, Roboto, system-ui, sans-serif);
  color: var(--ohp-ink);
  -webkit-tap-highlight-color: transparent;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
button.chip { cursor: pointer; font-size: 12px; }
button { font: inherit; color: inherit; }
button:focus-visible, [tabindex]:focus-visible { outline: 2px solid var(--primary-color, #03a9f4); outline-offset: 2px; }
ha-card, .card {
  display: block;
  position: relative;
  background: var(--ohp-card);
  color: var(--ohp-ink);
  border-radius: var(--ha-card-border-radius, 12px);
  border-width: var(--ha-card-border-width, 1px);
  border-style: solid;
  border-color: var(--ohp-card-border);
  padding: 14px 16px;
  box-shadow: var(--ha-card-box-shadow, none);
  overflow: hidden;
}
.all { display: flex; flex-direction: column; gap: var(--ohp-gap, 12px); container-type: inline-size; }
.bc { display: grid; grid-template-columns: 1fr; gap: var(--ohp-gap, 12px); align-items: start; }
.bc > .col { display: flex; flex-direction: column; gap: var(--ohp-gap, 12px); min-width: 0; }
@container (min-width: 880px) { .bc { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } }
.hdr { display: flex; justify-content: space-between; align-items: center; gap: 8px 12px; margin-bottom: 6px; flex-wrap: wrap; }
.hdr > .l { display: flex; align-items: center; gap: 10px; min-width: 0; flex-wrap: wrap; }
.ttl { font-size: 16px; font-weight: 500; line-height: 1.3; }
.sub { font-size: 12.5px; color: var(--ohp-ink2); line-height: 1.35; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: 12.5px; font-weight: 500; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: currentColor; flex: none; }
.kpis { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; }
.kpi { white-space: nowrap; }
.kpi[title] { cursor: help; }
.kpi b { font-size: 18px; font-weight: 500; }
.kpi small { color: var(--ohp-ink2); font-size: 11.5px; margin-left: 3px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip { background: var(--ohp-card2); border: 1px solid var(--ohp-line); border-radius: 999px; padding: 3px 10px; font-size: 12px; color: var(--ohp-ink2); white-space: nowrap; }
.chip b { color: var(--ohp-ink); font-weight: 500; }
.chip.ok { color: var(--ohp-ok-text); border-color: color-mix(in srgb, var(--ohp-ok) 45%, transparent); }
.chip.ok b { color: var(--ohp-ok-text); }
.chip.warn { color: var(--ohp-warn-text); border-color: color-mix(in srgb, var(--ohp-warn) 60%, transparent); background: color-mix(in srgb, var(--ohp-warn) 12%, var(--ohp-card2)); }
.chip.warn b { color: var(--ohp-warn-text); }
.banner { font-size: 12.5px; padding: 8px 10px; border-radius: 10px; margin: 6px 0; background: color-mix(in srgb, var(--ohp-warn) 14%, transparent); color: var(--ohp-ink); border: 1px solid color-mix(in srgb, var(--ohp-warn) 45%, transparent); }
.empty { padding: 18px 4px; color: var(--ohp-ink2); font-size: 13px; }

@media (max-width: 420px) {
  ha-card, .card { padding: 12px; }
  .hc { padding: 10px; }
}

/* plant schematic */
svg.plant { width: 100%; height: auto; display: block; overflow: visible; }
svg.plant text { font-family: inherit; }
svg.plant .lbl { font-size: 12px; fill: var(--ohp-ink); }
svg.plant .lbl.sm { font-size: 11px; }
svg.plant .lbl.dim { fill: var(--ohp-ink2); }
svg.plant .lbl.ink { fill: var(--ohp-ink); opacity: .9; }
svg.plant .big { font-size: 17px; font-weight: 500; fill: var(--ohp-ink); }
svg.plant .name { font-size: 14px; font-weight: 500; fill: var(--ohp-ink); }
svg.plant .chip { font-size: 11.5px; fill: var(--ohp-ink); font-weight: 500; }
svg.plant .rowbg { fill: var(--ohp-card2); stroke: var(--ohp-line); }
svg.plant .hit { cursor: pointer; outline: none; }
svg.plant .hit:hover .rowbg, svg.plant .hit:focus-visible .rowbg { stroke: var(--ohp-ink2); }
svg.plant .hit:focus-visible rect:first-child { stroke: var(--primary-color, #03a9f4); stroke-width: 2; }
.flow { stroke-dasharray: 7 9; animation: ohp-flow 1.1s linear infinite; }
.flow.rev { animation-direction: reverse; opacity: .55; }
@keyframes ohp-flow { to { stroke-dashoffset: -32; } }
.spin { animation: ohp-spin 1.6s linear infinite; }
@keyframes ohp-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .flow, .spin { animation: none !important; } }

/* controls */
.hero { display: flex; gap: 12px; align-items: center; min-width: 0; flex: 1 1 0; }
.heroicon { width: 46px; height: 46px; border-radius: 50%; display: grid; place-items: center; flex: none; }
.seg { display: flex; background: var(--ohp-card2); border-radius: 12px; padding: 3px; gap: 3px; }
.seg button { flex: 1; font-size: 13px; color: var(--ohp-ink2); background: transparent; border: 0; border-radius: 9px; padding: 8px 4px; cursor: pointer; min-height: 36px; transition: background .15s; }
.seg button.on { color: var(--ohp-on-auto); background: var(--ohp-auto-bg); }
:host([scheme="ochsner"]) .seg button.on { text-shadow: 0 0 3px rgba(0,0,0,.35); }
.seg button.on.off { background: var(--ohp-idle); color: var(--ohp-on-dhw); text-shadow: none; }
.seg button.on.manual { background: var(--ohp-heat); color: var(--ohp-on-heat); text-shadow: none; }
.seg button.on.manual.coolm { background: var(--ohp-cool); color: var(--ohp-on-cool); }
.seg button.armed { background: var(--ohp-warn); color: #111; text-shadow: none; }
.seg button:disabled { cursor: default; }
.sect { margin-top: 12px; }
.sect > .lab { font-size: 12px; color: var(--ohp-ink2); margin: 0 0 6px 2px; letter-spacing: .3px; text-transform: uppercase; }
.hc { background: var(--ohp-card2); border-radius: 12px; padding: 10px 12px; margin-bottom: 8px; }
.hcrow { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.hcname { font-size: 14px; font-weight: 500; display: flex; align-items: center; gap: 9px; min-width: 0; flex: 1 1 auto; cursor: pointer; background: none; border: 0; padding: 0; text-align: left; }
.hcname .sub { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.hcname > div { min-width: 0; }
.hcname .nm { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
.stepper { display: flex; align-items: center; gap: 6px; flex: none; }
.stepper button { width: 34px; height: 34px; border-radius: 50%; border: 1px solid var(--ohp-line); background: var(--ohp-card); color: var(--ohp-ink); font-size: 18px; line-height: 1; cursor: pointer; display: grid; place-items: center; padding: 0; touch-action: manipulation; }
.stepper button:disabled { opacity: .4; cursor: default; }
.stepper .v { min-width: 58px; text-align: center; font-size: 17px; font-weight: 500; font-variant-numeric: tabular-nums; }
/* the setpoint not in effect right now */
.stepper .v.idle { color: var(--ohp-ink2); }
.stepper .v.pend { color: var(--ohp-aux-text); }
.hc .room { margin-top: 6px; font-size: 13px; color: var(--ohp-ink2); font-variant-numeric: tabular-nums; }
.hc .room b { font-size: 14px; font-weight: 500; color: var(--ohp-ink); }
.sp { display: flex; align-items: center; justify-content: flex-end; gap: 6px; flex: none; }
.sp.alt { margin-top: 4px; }
/* small stepper = the main one (34 / 6 / 58 px) scaled to ~82 % as a whole,
   centred in a box as wide as the main stepper so the values line up */
.spbox { width: 138px; display: flex; justify-content: center; flex: none; }
.stepper.sm { gap: 5px; }
.stepper.sm button { width: 28px; height: 28px; font-size: 15px; }
.stepper.sm .v { min-width: 48px; font-size: 14px; font-weight: 400; }
.spbadge { font-size: 11px; line-height: 1.6; padding: 0 6px; border-radius: 999px; border: 1px solid var(--ohp-line); color: var(--ohp-ink2); white-space: nowrap; }
/* circuit tile: one grid, its own width picks the arrangement */
.hc.circuit { container: hc / inline-size; }
.hcg { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: 6px; row-gap: 4px; align-items: center;
  grid-template-areas: "name main" "info alt" "chips chips" "season season"; }
.hcg > .hcrow, .hcg > .hcrow2 { display: contents; }
.hcg .hcname { grid-area: name; }
.hcg .sp.main { grid-area: main; }
.hcg .hcinfo { grid-area: info; }
.hcg .sp.alt { grid-area: alt; margin-top: 0; }
.hcg .modechips.preset { grid-area: chips; margin-top: 4px; }
.hcg .modechips.season { grid-area: season; margin-top: 2px; }
.hcg .note { grid-column: 1 / -1; margin-top: 2px; }
/* No small setpoint (one setpoint mode) and no link in its place: the flow
   moves over to its spot instead of hanging in the middle. */
.hcg.noalt { grid-template-areas: "name main" "info info" "chips chips" "season season"; }
/* The Nacht/Normal badge only fits on wide tiles; elsewhere the stepper's
   tooltip and label name the setpoint. */
.hcg .sp.alt .spbadge { display: none; }
/* tiny tiles: three rows, both steppers a size smaller so the name keeps its room */
@container hc (width < 285px) {
  .hcg .sp.main .stepper { gap: 4px; }
  .hcg .sp.main .stepper button { width: 32px; height: 32px; }
  .hcg .sp.main .stepper .v { min-width: 50px; font-size: 16px; }
  .hcg .sp.alt .spbox { width: 122px; }
}
/* phones: two rows. Flow sits under the status (the main stepper makes that row
   tall anyway); the buttons share the second row with the small stepper, which
   may give up a few px of its centring box but never pushes a button onto a new line. */
@container hc (min-width: 285px) {
  .hcg { grid-template-areas: "name main" "info main" "row row" "season season"; row-gap: 2px; }
  .hcg.noalt { grid-template-areas: "name main" "info main" "row row" "season season"; }
  .hcg .hcinfo { align-self: start; padding-left: 39px; }
  .hcg > .hcrow2 { grid-area: row; display: flex; align-items: center; justify-content: space-between; gap: 6px; margin-top: 6px; }
  .hcg .modechips.preset { margin-top: 0; gap: 5px; flex: none; }
  .hcg .modechips.preset .mc { padding-left: 8px; padding-right: 8px; }
  .hcg .sp.alt { flex: 0 1 auto; min-width: 0; }
  .hcg .sp.alt .spbox { flex: 0 1 auto; min-width: min-content; }
}
/* wide tiles: buttons move up next to the small stepper, flow at the bottom right */
@container hc (min-width: 500px) {
  .hcg { grid-template-columns: auto minmax(0, 1fr) auto; column-gap: 10px;
    grid-template-areas: "name name main" "chips info alt" "season season season"; }
  .hcg.noalt { grid-template-areas: "name name main" "chips info info" "season season season"; }
  .hcg > .hcrow2 { display: contents; }
  .hcg .hcname, .hcg .sp.main { margin-bottom: 6px; }
  .hcg .hcinfo { justify-self: end; align-items: flex-end; text-align: right; align-self: center; padding-left: 0; }
  .hcg .sp.alt { flex: none; }
  .hcg .sp.alt .spbadge { display: inline-block; }
  .hcg .sp.alt .spbox { flex: none; }
  .hcg .modechips.preset .mc { padding-left: 11px; padding-right: 11px; }
  .hcg .hcinfo .room { justify-content: flex-end; }
}
.hcinfo { min-width: 0; flex: 1 1 auto; display: flex; flex-direction: column; align-items: flex-start; gap: 3px; }
.hcinfo .sub { white-space: nowrap; font-size: 12px; max-width: 100%; overflow: hidden; text-overflow: ellipsis; }
.hcinfo .room { margin: 0; font-size: 12.5px; }
.hc .room { display: flex; flex-wrap: wrap; column-gap: 8px; }
.hc .room > span { white-space: nowrap; }
.hcinfo .corr { font-size: 11.5px; padding: 1px 8px; }
.hcinfo button.corr { min-height: 24px; }
.subrow { display: flex; align-items: center; justify-content: space-between; gap: 4px 8px; flex-wrap: wrap; margin-top: 4px; }
.corr { font-size: 12px; line-height: 1.3; padding: 3px 9px; border-radius: 999px; white-space: nowrap; color: var(--ohp-aux-text); border: 1px solid color-mix(in srgb, var(--ohp-aux) 50%, transparent); background: color-mix(in srgb, var(--ohp-aux) 12%, transparent); }
button.corr { cursor: pointer; min-height: 28px; }
.modechips { display: flex; gap: 6px; margin-top: 8px; flex-wrap: wrap; }
.mc { font-size: 12px; padding: 5px 11px; border-radius: 999px; border: 1px solid var(--ohp-line); color: var(--ohp-ink2); cursor: pointer; background: transparent; min-height: 30px; }
.mc.on { border-color: transparent; color: var(--ohp-on-heat); background: var(--ohp-heat); }
.mc.on.eco { color: var(--ohp-on-eco); background: var(--ohp-eco); }
.mc.on.offc { color: #111; background: var(--ohp-idle); }
.mc.on.cool { color: var(--ohp-on-cool); background: var(--ohp-cool); }
.mc.on.auto { color: var(--ohp-on-auto); background: var(--ohp-auto-bg); }
:host([scheme="ochsner"]) .mc.on.auto { text-shadow: 0 0 3px rgba(0,0,0,.35); }
.mc.on.dhwc { color: var(--ohp-on-dhw); background: var(--ohp-dhw); }
.mc.busy { opacity: .6; }
.mc:disabled { cursor: default; opacity: .55; }
.seg button:disabled:not(.manual) { opacity: .55; }
.note { font-size: 11.5px; margin-top: 6px; color: var(--ohp-ink2); }
.note.ok { color: var(--ohp-ok-text); }
.note.err { color: var(--ohp-err-text); }
.note.wait { color: var(--ohp-aux-text); }
.boost { font-size: 13px; font-weight: 500; padding: 7px 12px; border-radius: 10px; border: 0; background: var(--ohp-dhw); color: var(--ohp-on-dhw); cursor: pointer; display: inline-flex; align-items: center; gap: 5px; min-height: 34px; flex: none; }
.boost.running { opacity: .8; box-shadow: inset 0 0 0 2px color-mix(in srgb, #000 25%, transparent); }
.boost:disabled { cursor: default; }
.badge { position: relative; width: 30px; height: 30px; border-radius: 50%; display: inline-grid; place-items: center; flex: none; }
.badge .leaf { position: absolute; right: -4px; bottom: -4px; }
.gauge { position: relative; height: 22px; margin-top: 10px; }
.gauge > div { position: absolute; top: 8px; height: 6px; border-radius: 3px; }
.gauge .mark { top: 3px; width: 2px; height: 16px; border-radius: 1px; background: var(--ohp-ink); }
.gscale { display: flex; justify-content: space-between; font-size: 11px; color: var(--ohp-ink2); }

/* efficiency / curve */
.tabs { display: flex; gap: 5px; margin: 4px 0 6px; flex-wrap: wrap; }
.tabs button { font-size: 12.5px; padding: 5px 11px; border-radius: 999px; border: 1px solid var(--ohp-line); background: transparent; color: var(--ohp-ink2); cursor: pointer; min-height: 30px; }
.tabs button.on { background: var(--ohp-ink); color: var(--ohp-card); border-color: transparent; }
.tabs button .pdot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--ohp-aux); margin-left: 6px; vertical-align: 2px; }
.legend { display: flex; gap: 4px 12px; font-size: 11.5px; color: var(--ohp-ink2); margin: 2px 0 4px; flex-wrap: wrap; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }
.legend i.ln { height: 3px; border-radius: 2px; vertical-align: 2px; width: 14px; }
.legend i.dash { height: 0; border-top: 2px dashed; background: none !important; vertical-align: 3px; width: 14px; border-radius: 0; }
.legend i.ring { border: 2px solid var(--ohp-ink); background: none !important; border-radius: 50%; }
.chartwrap { position: relative; }
svg.ch { width: 100%; display: block; overflow: visible; user-select: none; -webkit-user-select: none; }
svg.ch text { font-family: inherit; }
svg.ch .ax { font-size: 10.5px; fill: var(--ohp-ink2); }
svg.ch .axl { font-size: 10.5px; fill: var(--ohp-ink2); }
svg.ch .grid { stroke: var(--ohp-grid); }
svg.ch .base { stroke: var(--ohp-line); }
svg.ch .plotbg { fill: transparent; }
svg.ch .h { cursor: grab; touch-action: none; outline: none; }
svg.ch .h.ro { cursor: not-allowed; }
svg.ch .h.frozen { cursor: progress; }
svg.ch .h .hit { fill: transparent; stroke: none; }
svg.ch .h .dotv { transition: r .12s; }
svg.ch .h:hover .dotv, svg.ch .h:focus-visible .dotv, svg.ch .h.act .dotv { r: 9px; }
svg.ch .h:focus-visible .ring { stroke: var(--primary-color, #03a9f4); stroke-width: 2; }
svg.ch circle.ring { fill: none; }
svg.ch.pf .h:focus-visible circle.ring { stroke: none; }
svg.ch .h .lineh { transition: stroke-width .12s; }
svg.ch .h:hover .lineh, svg.ch .h:focus-visible .lineh, svg.ch .h.act .lineh { stroke-width: 3; }
svg.ch.dragging, svg.ch.dragging .h { cursor: grabbing; }
svg.ch .flab rect { fill: var(--ohp-tip-bg); stroke: var(--ohp-line); }
svg.ch .flab text { font-size: 12px; fill: var(--ohp-ink); font-weight: 500; }
@media (prefers-reduced-motion: reduce) { svg.ch .h .dotv, svg.ch .h .lineh { transition: none; } }
.tip { position: absolute; pointer-events: none; background: var(--ohp-tip-bg); border: 1px solid var(--ohp-line); color: var(--ohp-ink); font-size: 12px; padding: 5px 8px; border-radius: 8px; white-space: nowrap; z-index: 3; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
.readout { font-size: 12.5px; margin-top: 4px; min-height: 18px; color: var(--ohp-ink2); font-variant-numeric: tabular-nums; }
.readout b { color: var(--ohp-ink); font-weight: 500; }
.pendbar { margin-top: 8px; border-radius: 12px; padding: 10px 12px; background: color-mix(in srgb, var(--ohp-aux) 12%, var(--ohp-card2)); border: 1px solid color-mix(in srgb, var(--ohp-aux) 40%, transparent); }
.pendbar .t { font-size: 12.5px; font-weight: 500; margin-bottom: 4px; }
.pendbar ul { margin: 0 0 8px; padding: 0 0 0 16px; font-size: 12.5px; font-variant-numeric: tabular-nums; }
.pendbar li { margin: 1px 0; }
.pendbar .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.pendbar .cost { font-size: 11.5px; color: var(--ohp-ink2); margin-right: auto; }
.pendbar .btns { display: flex; gap: 8px; margin-left: auto; }
.btn { font-size: 13px; font-weight: 500; padding: 7px 14px; border-radius: 10px; border: 1px solid var(--ohp-line); background: transparent; color: var(--ohp-ink); cursor: pointer; min-height: 34px; }
.btn.primary { background: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); border-color: transparent; }
.btn:disabled { opacity: .55; cursor: default; }
.tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(84px, 1fr)); gap: 6px; margin-top: 10px; }
.tile { background: var(--ohp-card2); border-radius: 10px; padding: 8px 10px; min-width: 0; }
.tile b { display: block; font-size: 16px; font-weight: 500; white-space: nowrap; }
/* labels wrap to (at most) two lines instead of being cut off */
.tile span { font-size: 11px; line-height: 1.25; color: var(--ohp-ink2); display: block; overflow-wrap: break-word; hyphens: auto; }
.foot { font-size: 11px; color: var(--ohp-ink2); margin-top: 8px; line-height: 1.4; }
.sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

/* time program: today strip in a circuit tile */
.tstrip { display: flex; align-items: center; gap: 10px; margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--ohp-line); }
.tstrip .txt { font-size: 12.5px; color: var(--ohp-ink2); display: flex; align-items: center; gap: 6px; white-space: nowrap; min-width: 0; }
.tstrip .txt > span { overflow: hidden; text-overflow: ellipsis; }
.tstrip .txt b { color: var(--ohp-ink); font-weight: 500; }
.tstrip .mini { flex: 1 1 auto; min-width: 70px; position: relative; height: 10px; border-radius: 5px; background: var(--ohp-night); overflow: hidden; }
.tstrip .mini.normal { background: var(--ohp-heat); opacity: .55; }
.tstrip .mini > span { position: absolute; top: 0; bottom: 0; background: var(--ohp-heat); }
.tstrip .mini > em { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--ohp-ink); border-radius: 1px; }
.tpgo, .tplink { font-size: 12px; color: var(--primary-color, #03a9f4); background: none; border: 0; padding: 4px 2px; cursor: pointer; white-space: nowrap; display: inline-flex; align-items: center; gap: 5px; min-height: 30px; }
.tpgo:hover, .tplink:hover { text-decoration: underline; }
@container hc (width < 420px) {
  .tstrip { flex-wrap: wrap; row-gap: 6px; }
  .tstrip .mini { order: 3; flex-basis: 100%; }
  .tstrip .tpgo { margin-left: auto; }
}

/* time program card */
.week { position: relative; user-select: none; -webkit-user-select: none; margin-top: 2px; }
.week .axis { position: relative; height: 16px; margin-left: var(--lw); font-size: 10.5px; color: var(--ohp-ink2); }
.week .axis span { position: absolute; transform: translateX(-50%); }
.week .axis span:first-child { transform: none; }
.week .axis span:last-child { transform: translateX(-100%); }
.week .rows { position: relative; }
.drow { display: flex; align-items: center; height: 30px; margin-bottom: 4px; cursor: pointer; border-radius: 8px; outline: none; }
.drow .dl { width: var(--lw); flex: none; font-size: 12.5px; color: var(--ohp-ink2); padding-left: 2px; }
.drow.today .dl { color: var(--ohp-ink); font-weight: 600; }
.drow .bar { position: relative; flex: 1; height: 26px; border-radius: 7px; background: var(--ohp-night);
  background-image: repeating-linear-gradient(90deg, transparent 0 calc(12.5% - 1px), var(--ohp-grid) calc(12.5% - 1px) 12.5%); }
.drow:hover .bar { box-shadow: 0 0 0 1px var(--ohp-ink2); }
.drow:focus-visible .bar { box-shadow: 0 0 0 2px var(--primary-color, #03a9f4); }
.drow.sel .bar { box-shadow: 0 0 0 2px var(--primary-color, #03a9f4); }
.blk { position: absolute; top: 3px; bottom: 3px; border-radius: 5px; background: var(--tp-normal, var(--ohp-heat)); color: #111; font-size: 11px; font-weight: 500; display: flex; align-items: center; justify-content: center; overflow: hidden; white-space: nowrap; font-variant-numeric: tabular-nums; pointer-events: none; }
.blk.pend { background: repeating-linear-gradient(135deg, var(--tp-normal, var(--ohp-heat)) 0 6px, color-mix(in srgb, var(--tp-normal, var(--ohp-heat)) 72%, var(--ohp-card)) 6px 12px); outline: 2px solid var(--ohp-aux); outline-offset: 1px; }
.drow .unread { position: absolute; inset: 0; display: grid; place-items: center; font-size: 11px; color: var(--ohp-ink2); }
.hdl { position: absolute; top: -4px; bottom: -4px; width: 22px; margin-left: -11px; cursor: ew-resize; touch-action: none; z-index: 2; outline: none; }
.hdl::after { content: ""; position: absolute; left: 9px; top: 6px; bottom: 6px; width: 4px; border-radius: 2px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.45); }
.hdl:hover::after, .hdl:focus-visible::after { box-shadow: 0 0 0 2px var(--primary-color, #03a9f4); }
.nowline { position: absolute; top: -3px; bottom: 2px; width: 2px; background: var(--ohp-ink); border-radius: 1px; pointer-events: none; z-index: 1; }
.nowline::before { content: attr(data-t); position: absolute; top: -15px; left: 50%; transform: translateX(-50%); font-size: 10.5px; font-weight: 600; background: var(--ohp-card); padding: 0 3px; border-radius: 3px; }
.legend .dh { margin-left: auto; }
.tpeditor { margin-top: 10px; border-radius: 12px; background: var(--ohp-card2); padding: 10px 12px; }
.tpeditor .eh { display: flex; justify-content: space-between; gap: 8px; align-items: center; flex-wrap: wrap; }
.tpeditor .eh .ttl { font-size: 14px; }
.tpeditor .days { display: flex; gap: 4px; flex-wrap: wrap; margin: 8px 0 4px; }
.tpeditor .days button { min-width: 36px; height: 30px; padding: 0 6px; border-radius: 999px; border: 1px solid var(--ohp-line); background: transparent; color: var(--ohp-ink2); font-size: 12px; cursor: pointer; }
.tpeditor .days button.on { background: var(--ohp-ink); color: var(--ohp-card); border-color: transparent; }
.tpeditor .days button:disabled { cursor: default; opacity: .55; }
.tpeditor .period { display: flex; align-items: center; gap: 8px; margin: 6px 0; flex-wrap: wrap; }
.tpeditor .pn { font-size: 12.5px; color: var(--ohp-ink2); min-width: 74px; display: flex; align-items: center; gap: 6px; }
.tpeditor .pn i { width: 10px; height: 10px; border-radius: 3px; background: var(--tp-normal, var(--ohp-heat)); }
.tpeditor input[type=time], .tpeditor select { font: inherit; font-size: 14px; font-variant-numeric: tabular-nums; padding: 5px 8px; border-radius: 8px; border: 1px solid var(--ohp-line); background: var(--ohp-card); color: var(--ohp-ink); min-height: 34px; color-scheme: inherit; }
.tpeditor select { font-size: 12.5px; }
.tpeditor .x { width: 32px; height: 32px; border-radius: 50%; border: 0; background: transparent; color: var(--ohp-ink2); cursor: pointer; }
.tpeditor .addrow { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.addp { font-size: 12.5px; color: var(--primary-color, #03a9f4); background: none; border: 0; padding: 6px 0; cursor: pointer; min-height: 30px; }
.addp:disabled { color: var(--ohp-ink2); cursor: default; opacity: .6; }
.hint { font-size: 11.5px; color: var(--ohp-ink2); margin-top: 4px; }
.quick { display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px; align-items: center; }
.quick.center { justify-content: center; margin-top: 10px; }
.btn.sm { font-size: 12.5px; padding: 4px 10px; min-height: 30px; font-weight: 400; }
.tpbanner { font-size: 12.5px; padding: 8px 10px; border-radius: 10px; margin: 2px 0 8px; display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap;
  background: color-mix(in srgb, var(--ohp-night-ink) 10%, transparent); border: 1px solid color-mix(in srgb, var(--ohp-night-ink) 35%, transparent); }
.tpbanner > span { display: flex; gap: 6px; align-items: center; }
.tpempty { text-align: center; padding: 12px 6px 4px; }
.tpempty .big { font-size: 14px; font-weight: 500; margin: 6px 0 2px; }
.copyrow { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; margin: 4px 0 8px; }
.copyrow .mc.on.sel { color: var(--ohp-card); background: var(--ohp-ink); }
`;

/** Text colours derived for contrast in light and dark (mixed toward the ink colour). */
export const TINTS = `
:host {
  --ohp-ok-text: color-mix(in srgb, var(--ohp-ok) 75%, var(--ohp-ink));
  --ohp-warn-text: color-mix(in srgb, var(--ohp-warn) 65%, var(--ohp-ink));
  --ohp-err-text: color-mix(in srgb, var(--ohp-err) 70%, var(--ohp-ink));
  --ohp-aux-text: color-mix(in srgb, var(--ohp-aux) 70%, var(--ohp-ink));
  --ohp-night: color-mix(in srgb, #5c6bc0 20%, var(--ohp-card2));
  --ohp-night-ink: color-mix(in srgb, #5c6bc0 65%, var(--ohp-ink));
}
`;
