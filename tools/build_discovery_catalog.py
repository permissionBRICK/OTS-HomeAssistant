#!/usr/bin/env python3
"""Build the packaged local-discovery catalog for ochsner_local_ots (spec v4).

Inputs (research artifacts, not shipped):
  apk_files/reports/catalog_model.json   validated APK OA catalog + instance tags
  apk_files/reports/catalog_names.json   APK-derived human labels per OA
  apk_files/ha_config/core.config_entries  reference-plant seed (names, units,
                                           enum options, write ids, bounds)
  apk_files/live_ids.json                live reference-plant reads incl. the
                                         member-4353 token list per enum id
  apk_files/jadx_out/resources/res/values/strings.xml
                                         APK string resources (the
                                         <Type>IFType_<state> English labels)

Output (shipped with the integration):
  custom_components/ochsner_local_ots/data/discovery_catalog.json

The output is deterministic: same inputs -> byte-identical file (the version
field is a content hash, not a timestamp).

MEMBERSHIP (owner spec v4):
  * A point is included iff it has at least one name source: an APK English
    label, a reference-bundle name, or an APK technical symbol. Nameless
    points are excluded.
  * Schedules/time programs are excluded entirely: object_type 8717 and any
    member_id in 514..525. The integration does not expose them.
  * Enum descriptor members (member_id 4353) are excluded as points: the
    descriptor of an enum point is derived at scan time (same object address,
    member 4353) and read live from the controller.

NAMING (pump-first overall; the packaged name is the offline part):
  Names that can be read from the controller itself (circuit names, plant
  model/serial/software version) are applied at scan time and take priority.
  The packaged per-point name is language-aware. The English-priority choice
  (shipped as name/name_source) is:
    1. APK English label                        name_source=apk_label
       (several distinct labels: the most descriptive wins — longest, then
       lexicographic, a deterministic tie-break; labels that are printf
       templates like "Name of heating circuit %1$s" are template strings,
       not names, and are ignored)
    2. reference-bundle name                    name_source=bundle
    3. APK technical symbol                     name_source=apk_symbol
  The German-priority choice swaps 1 and 2 (bundle name first).

  BILINGUAL PAIR: every point additionally ships an explicit pair
  name_en/name_de with per-side provenance (name_en_source/name_de_source in
  apk_label|bundle|apk_symbol|translated). Each language keeps its native
  evidence verbatim; a missing side is filled from the reviewed translation
  data (tools/catalog_translations.json, source "translated"). A technical
  machine name from bundle/APK-label evidence needs a reviewed pair in
  names_symbol_bilingual (identity entries record that an acronym IS the
  label); only apk_symbol evidence ships verbatim on both sides — the
  single permitted exception (29 points, 25 unique). The runtime picks per
  the user's language (catalog.resolve_point_name); a missing translation
  is recorded in stats.name_translation_gaps and fails the bilingual gate.

ENUM LABELS (the controller cannot localize: jsongen ignores LNG, so the
member-4353 tokens are symbolic keys and the display labels ship here):
  * German, per point (enum_labels_de): index join on the reference plant —
    the bundle's options/value_map gives {german_label: numeric_value}, the
    live token list gives the token at each list index, and list index IS
    the numeric value. Keys are normalized tokens
    (catalog.normalize_enum_token); the literal placeholder "label" (an
    untranslated bundle slot) is not a usable label.
  * English, shared (enum_token_labels.en): from the APK string resources
    <Type>IFType_<state>. A token matches a resource suffix iff their
    normalized spellings are equal (lowercase, strip whitespace/_/-, e.g.
    "TiMinOff" == "ti_min_off"); a suffix whose types disagree on the label
    is ambiguous and is not mapped — a match is never forced.
  * German, shared (enum_token_labels.de): per-token translations of the
    shared English map (translations.enum_shared_en_to_de) — justified per
    token exactly like the EN side, by the token-keyed APK resources, NOT by
    bundle evidence. A point-specific German label (bundle join) is still
    never applied to a point it was not proven on and always outranks the
    shared translation at runtime.
  * English, per point (enum_labels_en): translations of the point's own
    bundle-joined German labels (translations.enum_labels_de_to_en) for
    tokens the shared English map does not cover.
  * Tokens with no label evidence on either side but an unambiguous token
    meaning get shared {en, de} fills (translations.enum_token_labels_both).
  * Reviewed per-point overrides (translations.enum_point_overrides) outrank
    everything: they correct wrong joins and raw symbols the bundle stored
    in the German slot, and give evidence-less numeric states an explicit
    faithful label. The shipped asset must have NO enum_label_gaps left —
    a gap means a raw-token fallback and fails the bilingual gate.
  * A normalized key claimed by two different raw tokens of one state list
    (signs preserved, so "-12" vs "12" stay distinct) is never mapped —
    a join must never shift a label between numeric values.
  * Tokens without a label in a language fall back to the raw token at
    runtime (an option is never empty); per point they are recorded in
    enum_label_gaps.{de,en} so unmapped options are visible, not silent.

TECHNICAL-NAME TEST (catalog.is_technical_name): a name with no whitespace
that has a lowercase-to-uppercase transition or a run of two or more
consecutive uppercase letters (e.g. "CprOprHrs1", "HPMEmgyModConf",
"Th-EngySumAct", "DHCP") is a machine symbol, not a user-facing label. Such
points are still included but entity_registry_enabled_default=False (and
diagnostic).

HEATING-CIRCUIT PROPAGATION: the circuit module is one template instantiated
per circuit, so a triple (object_type, point_index, member_id) CONFIRMED on
>=2 circuit instance tags is generated for all four circuit tags; an
APK-known instance of a confirmed triple that lacks reference metadata is
enriched with the donor circuit's shaping (platform, write binding, bounds)
— same triple, no guessed addresses. A triple seen on only one circuit is
never propagated: its addresses on the other circuits cannot be derived
(e.g. "Sollwert Handbetrieb" uses a different point_index per circuit), and
guessing addresses is forbidden.

Catalog point record keys:
  id            read OA (canonical Base64)
  platform      sensor|binary_sensor|number|select|text|switch
  name          display name (English-priority raw-evidence choice; overlay
                merging and the v4 flag rules key off it)
  name_source   apk_label|bundle|apk_symbol (chosen source, for debugging)
  name_en, name_de           the explicit bilingual pair (always present)
  name_en_source, name_de_source  apk_label|bundle|apk_symbol|translated
  enum_labels_de  normalized token -> German label (reference index join)
  enum_labels_en  normalized token -> English label (translated from the
                  point's own German labels where the shared map has none)
  enum_label_gaps {de: [tokens], en: [tokens]} tokens without a label
  sources       subset of [apk, reference, hc_template]
  write_source  apk_settings for explicitly reviewed Smart app write bindings
  write_id, unit, options, value_map, min/max/bundle_min/bundle_max/step,
  on_value/off_value, enabled_default, diagnostic, hc_tag

Catalog root additionally carries enum_token_labels: {en: {...}, de: {...}},
the shared normalized-token label maps described above (provenance in
enum_token_label_sources).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "custom_components" / "ochsner_local_ots"
sys.path.insert(0, str(PKG_DIR))

import catalog as oa_catalog  # noqa: E402  (loaded as a plain module, no HA needed)

HC_TAGS = list(oa_catalog.HC_INSTANCE_TAGS)

# Confirmed Smart app settings, including circuits absent from the reference
# plant. These are explicit write bindings, not inferred from an OA type or
# a setpoint-like name. Evidence: docs/agent-notes/dhw-setpoints.md.
APK_NUMBER_BINDINGS = {
    "ASMv1XXZAAE=": (5.0, 75.0),   # Comfort
    "ASOp1XXZAAE=": (5.0, 75.0),   # Eco
    "ASOXCHXZAAE=": (5.0, 75.0),   # Reduced
    "ASPuhXXZAAE=": (40.0, 75.0),  # Boost
}


def apply_apk_number_binding(rec: Dict[str, Any]) -> None:
    """Supply reviewed APK write metadata; plant-specific metadata wins later."""
    bounds = APK_NUMBER_BINDINGS.get(rec["id"])
    if bounds is None:
        return
    rec.update(
        platform="number",
        write_id=rec["id"],
        write_source="apk_settings",
        unit="°C",
        min=bounds[0],
        max=bounds[1],
        step=0.5,
    )


# The v4 classification rules (privacy, service/one-shot, technical-name
# test) live in the shipped catalog module: the runtime bundle-overlay path
# applies the same rules, so a stored bundle can never undo them.
is_technical_name = oa_catalog.is_technical_name

# APK labels that are printf templates ("Name of heating circuit %1$s") are
# template strings, not usable names.
_PLACEHOLDER_LABEL = re.compile(r"%\d+\$[sd]|%[sd]\b")

# Circuit ordinal fragments that must be rewritten when a heating-circuit
# point is propagated from one circuit instance to another.
_HC_ORDINAL_RES = (
    re.compile(r"(?i)(heizkreis\s*)(\d+)"),
    re.compile(r"(HC)(\d+)"),
    re.compile(r"(HK)(\d+)"),
)

PLATFORM_PRIORITY = ["switch", "select", "number", "text", "binary_sensor", "sensor"]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


_TRANSLATION_SECTIONS = (
    "names_en_to_de",
    "names_de_to_en",
    "names_symbol_bilingual",
    "enum_shared_en_to_de",
    "enum_labels_de_to_en",
    "enum_token_labels_both",
    "enum_point_overrides",
)

# Sections whose leaf values are reviewed {en, de} pairs rather than strings.
_PAIR_SECTIONS = {"names_symbol_bilingual", "enum_token_labels_both"}


def _load_translations(path: Path) -> Dict[str, Dict[str, Any]]:
    """The reviewed bilingual label data (tools/catalog_translations.json).

    Light validation only: sections present, every leaf a non-empty string
    (string entries and {en, de} pairs; enum_point_overrides nests pairs per
    point id), and printf placeholders preserved — a translation that drops
    "%1$s" would break the template it belongs to.
    """
    data = _load_json(path)

    def check(section: str, key: str, value: Any, *, pair: bool) -> None:
        values = [value.get("en"), value.get("de")] if pair else [value]
        for v in values:
            if not (isinstance(v, str) and v.strip()):
                raise SystemExit(f"{path}: {section}[{key!r}] has an empty value")
            if sorted(_PLACEHOLDER_LABEL.findall(key)) != sorted(_PLACEHOLDER_LABEL.findall(v)):
                raise SystemExit(f"{path}: {section}[{key!r}] changes placeholders")

    out: Dict[str, Dict[str, Any]] = {}
    for section in _TRANSLATION_SECTIONS:
        entries = data.get(section)
        if not isinstance(entries, dict):
            raise SystemExit(f"{path}: missing section {section}")
        for key, value in entries.items():
            if section == "enum_point_overrides":
                for token, pair in value.items():
                    check(section, token, pair, pair=True)
            else:
                check(section, key, value, pair=section in _PAIR_SECTIONS)
        out[section] = entries
    return out


def _reference_controller(config_entries: Any) -> Dict[str, Any]:
    entries = config_entries["data"]["entries"]
    ours = [e for e in entries if e.get("domain") == "ochsner_local_ots"]
    if not ours:
        raise SystemExit("reference config_entries has no ochsner_local_ots entry")
    controllers = ours[0]["data"]["controllers"]
    return controllers[0]


def _apk_name_sources(
    names_rec: Optional[Dict[str, Any]], model_rec: Optional[Dict[str, Any]]
) -> Tuple[List[str], List[str]]:
    """Return (labels, symbols): the APK's name evidence for one address."""

    labels: List[str] = []
    if isinstance(names_rec, dict):
        for n in names_rec.get("apk_names") or []:
            lab = n.get("label_en")
            if isinstance(lab, str) and lab.strip():
                labels.append(lab.strip())
    for rl in (model_rec or {}).get("resource_labels") or []:
        lab = rl.get("label")
        if isinstance(lab, str) and lab.strip():
            labels.append(lab.strip())

    symbols: List[str] = []
    if isinstance(names_rec, dict):
        symbols += [s for s in names_rec.get("symbols") or [] if isinstance(s, str) and s.strip()]
    symbols += [s for s in (model_rec or {}).get("names") or [] if isinstance(s, str) and s.strip()]

    return sorted(set(labels)), sorted(set(symbols))


# The bundle's untranslated placeholder: a slot the cloud translation table
# had no entry for is exported as the literal string "label" — never a name.
_BUNDLE_PLACEHOLDER = "label"

normalize_enum_token = oa_catalog.normalize_enum_token


def _live_token_lists(live: Any) -> Dict[str, List[str]]:
    """read id -> member-4353 token list from the live reference reads."""
    out: Dict[str, List[str]] = {}
    for rid, rec in (live.get("live") or {}).items():
        states = (rec or {}).get("states_text") if isinstance(rec, dict) else None
        if isinstance(states, str) and "*" in states:
            out[str(rid)] = [t.strip() for t in states.split("*")]
    return out


def _apk_state_labels(strings_xml: Path) -> Tuple[Dict[str, str], int, int]:
    """Shared English label map from the APK <Type>IFType_<state> resources.

    Returns (normalized_suffix -> label, matched_suffixes, ambiguous). A
    suffix used by several types with DIFFERENT labels is ambiguous and not
    mapped (a match is never forced); identical labels across types agree.
    """
    by_suffix: Dict[str, set] = {}
    for el in ET.parse(strings_xml).getroot():
        name = el.get("name") or ""
        if "IFType_" not in name:
            continue
        label = (el.text or "").strip()
        if not label or label == "#" or _PLACEHOLDER_LABEL.search(label):
            continue
        suffix = normalize_enum_token(name.split("IFType_", 1)[1])
        by_suffix.setdefault(suffix, set()).add(label)
    en_map = {s: next(iter(labels)) for s, labels in by_suffix.items() if len(labels) == 1}
    return en_map, len(en_map), sum(1 for labels in by_suffix.values() if len(labels) > 1)


def _value_labels_de(ref: Dict[str, Any]) -> Dict[int, str]:
    """numeric value -> German label from a reference record's enum shape."""
    out: Dict[int, str] = {}
    options = ref.get("options")
    value_map = ref.get("value_map")
    if isinstance(options, dict):
        for label, value in options.items():
            try:
                out[int(value)] = str(label)
            except (TypeError, ValueError):
                continue
    elif isinstance(value_map, dict):
        for value, label in value_map.items():
            try:
                out[int(float(value))] = str(label)
            except (TypeError, ValueError):
                continue
    return {v: lab for v, lab in out.items() if lab and lab != _BUNDLE_PLACEHOLDER}


def _attach_enum_labels(
    ref: Dict[str, Any],
    tokens: List[str],
    de_to_en: Dict[str, str],
) -> None:
    """Index-join one reference enum point: token at list index N <-> value N.

    Adds enum_labels_de (normalized token -> German label, bundle join) and
    enum_labels_en (the reviewed English translation of EVERY own German
    label — point/index evidence always outranks a generic shared token
    meaning, in both languages) to the reference record, plus the raw token
    list under ``_enum_tokens`` so per-point overrides and gap computation
    run after heating-circuit propagation (same tokens, same template).
    """
    v2de = _value_labels_de(ref)
    # A normalized key claimed by two different raw tokens in ONE list would
    # shift labels between numeric values: such keys are never mapped (the
    # tokens stay raw and are recorded as gaps).
    key_tokens: Dict[str, set] = {}
    for token in tokens:
        key = normalize_enum_token(token)
        if key:
            key_tokens.setdefault(key, set()).add(token.strip())
    collisions = {k for k, toks in key_tokens.items() if len(toks) > 1}
    de_labels: Dict[str, str] = {}
    en_labels: Dict[str, str] = {}
    for idx, token in enumerate(tokens):
        token = token.strip()
        key = normalize_enum_token(token)
        if not token or not key or key in collisions:
            # Placeholder slots ("-") and colliding keys are never mapped.
            continue
        own_de = v2de.get(idx)
        if own_de:
            de_labels.setdefault(key, own_de)
            own_en = de_to_en.get(own_de)
            if own_en:
                en_labels.setdefault(key, own_en)
    if de_labels:
        ref["enum_labels_de"] = de_labels
    if en_labels:
        ref["enum_labels_en"] = en_labels
    ref["_enum_tokens"] = [t.strip() for t in tokens]


def _finalize_enum_labels(
    rec: Dict[str, Any],
    tokens: List[str],
    overrides: Dict[str, Any],
    en_map: Dict[str, str],
    shared_de: Dict[str, str],
) -> None:
    """Apply reviewed per-point overrides and recompute gaps + provenance.

    Runs on the FINAL point record (after heating-circuit propagation copied
    the join to derived circuits). Overrides outrank the bundle join and the
    shared maps in both languages — they are the reviewed correction channel
    for wrong joins (the cloud select's inverted 'Verbindung aktiv') and for
    raw symbols the bundle stored in the German slot. enum_label_sources
    records the per-token per-side provenance of the point's own maps
    (de: bundle|override, en: translated|override); tokens carried only by
    the shared maps have their provenance in the catalog root.
    ``enum_label_gaps`` afterwards lists tokens that still resolve to a raw
    fallback in a language — the bilingual gate requires none.
    """
    de_labels = dict(rec.get("enum_labels_de") or {})
    en_labels = dict(rec.get("enum_labels_en") or {})
    sources: Dict[str, Dict[str, str]] = {}
    for key in de_labels:
        sources.setdefault(key, {})["de"] = "bundle"
    for key in en_labels:
        sources.setdefault(key, {})["en"] = "translated"
    for key, pair in overrides.items():
        de_labels[key] = str(pair["de"])
        en_labels[key] = str(pair["en"])
        sources.setdefault(key, {})["de"] = "override"
        sources[key]["en"] = "override"

    key_tokens: Dict[str, set] = {}
    for token in tokens:
        key = normalize_enum_token(token)
        if key:
            key_tokens.setdefault(key, set()).add(token.strip())
    collisions = {k for k, toks in key_tokens.items() if len(toks) > 1}
    gaps: Dict[str, List[str]] = {"de": [], "en": []}
    for token in tokens:
        token = token.strip()
        key = normalize_enum_token(token)
        if not token or not key:
            continue
        if key in collisions and key not in overrides:
            gaps["de"].append(token)
            gaps["en"].append(token)
            continue
        if key not in de_labels and key not in shared_de:
            gaps["de"].append(token)
        if key not in en_labels and key not in en_map:
            gaps["en"].append(token)

    for field, mapping in (("enum_labels_de", de_labels), ("enum_labels_en", en_labels)):
        if mapping:
            rec[field] = mapping
        else:
            rec.pop(field, None)
    if sources:
        rec["enum_label_sources"] = {k: sources[k] for k in sorted(sources)}
    gaps = {lang: toks for lang, toks in gaps.items() if toks}
    if gaps:
        rec["enum_label_gaps"] = gaps
    else:
        rec.pop("enum_label_gaps", None)


def _choose_name(
    labels: List[str], bundle_name: Optional[str], symbols: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """Apply the documented naming priority; (None, None) = no name source.

    Every usable APK label outranks the bundle name (spec v4 B2). With
    several distinct labels the most descriptive wins: longest first, then
    lexicographic — a deterministic tie-break. Printf-template labels are
    not usable names and fall through to the next source.
    """

    usable = sorted(
        (lab for lab in labels if not _PLACEHOLDER_LABEL.search(lab)),
        key=lambda lab: (-len(lab), lab),
    )
    if usable:
        return usable[0], "apk_label"
    if bundle_name:
        return bundle_name, "bundle"
    if symbols:
        return symbols[0], "apk_symbol"
    return None, None


def _choose_name_de(
    labels: List[str], bundle_name: Optional[str], symbols: List[str]
) -> Tuple[Optional[str], Optional[str]]:
    """The German-priority choice: the reference-bundle name (German) first,
    then the same fallbacks as the English choice."""
    if bundle_name:
        return bundle_name, "bundle"
    return _choose_name(labels, bundle_name, symbols)


def _bilingual_names(
    labels: List[str],
    bundle_name: Optional[str],
    symbols: List[str],
    tr: Dict[str, Dict[str, Any]],
    name_gaps: Dict[str, set],
) -> Optional[Dict[str, str]]:
    """The explicit bilingual pair for one point, with per-side provenance.

    Each language prefers its native evidence (EN: APK label, DE: bundle
    name); a missing side is filled from the reviewed translation data
    (source "translated"). A technical machine name from bundle or APK-label
    evidence is NOT exempt: it must have a reviewed {en, de} pair in
    names_symbol_bilingual (an identity entry like DHCP records the decision
    that the acronym IS the label). Only apk_symbol evidence — a point whose
    sole name is the APK code identifier — ships the symbol verbatim on both
    sides, the single permitted exception. A prose name whose translation is
    missing keeps the other language's string (recorded in ``name_gaps`` so
    the bilingual audit sees it) rather than dropping the point.
    """
    usable = sorted(
        (lab for lab in labels if not _PLACEHOLDER_LABEL.search(lab)),
        key=lambda lab: (-len(lab), lab),
    )
    label = usable[0] if usable else None
    symbol_pairs = tr["names_symbol_bilingual"]

    def _native_side(name: str, source: str, side: str) -> Tuple[str, str]:
        """The side the evidence is written in — verbatim, except that a
        technical machine name needs a reviewed pair (identity entries keep
        the native provenance: the string IS the label)."""
        pair = symbol_pairs.get(name)
        if pair and str(pair[side]) != name:
            return str(pair[side]), "translated"
        if pair is None and is_technical_name(name):
            # An unreviewed machine name is a gap, never silently accepted.
            name_gaps[side].add(name)
        return name, source

    def _translated_side(name: str, source: str, side: str) -> Tuple[str, str]:
        """The opposite side: the reviewed pair for machine names, the
        reviewed prose translation otherwise; a missing entry keeps the
        evidence string and is recorded as a gap."""
        pair = symbol_pairs.get(name)
        if pair:
            translated = str(pair[side])
            return translated, source if translated == name else "translated"
        if not is_technical_name(name):
            section = "names_de_to_en" if side == "en" else "names_en_to_de"
            translated = tr[section].get(name)
            if translated:
                return translated, "translated"
        name_gaps[side].add(name)
        return name, source

    bundle_pair = symbol_pairs.get(bundle_name) if bundle_name else None
    if label:
        pair_en = str(bundle_pair["en"]) if bundle_pair else None
        if pair_en and pair_en not in (bundle_name, label):
            # A reviewed technical bundle pair carries proven specificity a
            # generic APK label lacks ("Electrical energy consumption" vs
            # E-EngySum2's per-year bucket): the reviewed English side wins
            # so the four points of a family stay distinguishable.
            en, en_src = pair_en, "translated"
        else:
            en, en_src = _native_side(label, "apk_label", "en")
    elif bundle_name:
        en, en_src = _translated_side(bundle_name, "bundle", "en")
    elif symbols:
        en, en_src = symbols[0], "apk_symbol"
    else:
        return None

    if bundle_name:
        de, de_src = _native_side(bundle_name, "bundle", "de")
    elif label:
        de, de_src = _translated_side(label, "apk_label", "de")
    else:
        de, de_src = symbols[0], "apk_symbol"

    return {
        "name_en": en,
        "name_en_source": en_src,
        "name_de": de,
        "name_de_source": de_src,
    }


def _rewrite_hc_ordinal(name: str, target_ordinal: int) -> str:
    out = name
    for rx in _HC_ORDINAL_RES:
        out = rx.sub(lambda m: f"{m.group(1)}{target_ordinal}", out)
    return out


def _hc_uid_to_tag(ctrl: Dict[str, Any]) -> Dict[str, int]:
    """Map the reference bundle's heating-circuit uids to circuit instance tags.

    A circuit's own points (e.g. its name text) are addressed on the circuit
    instance tag, which identifies the circuit; shared-tag points (pump,
    mixer, flow temperature on tag 35112) inherit the circuit via this map.
    """

    out: Dict[str, int] = {}
    for key in ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches"):
        for ent in ctrl.get(key, []) or []:
            if not isinstance(ent, dict):
                continue
            uid = str(ent.get("heating_circuit_uid") or "").strip()
            if not uid or uid in out:
                continue
            oa = oa_catalog.try_decode_oa(ent.get("read_id") or ent.get("id"))
            if oa is not None and oa.instance_tag in HC_TAGS:
                out[uid] = oa.instance_tag
    return out


def _reference_records(ctrl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """One metadata record per read OA from the reference plant entity lists.

    When a read id backs several entity kinds (the reference entry predates
    the generator's dedupe), the writable/most specific platform wins — the
    same outcome today's bundle generator produces.
    """

    by_id: Dict[str, Dict[str, Any]] = {}
    hc_uid_to_tag = _hc_uid_to_tag(ctrl)

    def visit(kind: str, ent: Dict[str, Any]) -> None:
        read_id = str(ent.get("id") or ent.get("read_id") or "").strip()
        if not read_id:
            return
        oa = oa_catalog.try_decode_oa(read_id)
        if oa is None or oa_catalog.is_excluded_point(oa):
            return

        rec: Dict[str, Any] = {
            "id": read_id,
            "platform": kind,
            "name": str(ent.get("name") or read_id),
        }
        write_id = ent.get("write_id")
        if isinstance(write_id, str) and write_id and kind in {"number", "select", "text", "switch"}:
            rec["write_id"] = write_id
        for k in ("unit", "options", "value_map", "min", "max", "bundle_min", "bundle_max", "step", "on_value", "off_value"):
            if ent.get(k) is not None:
                rec[k] = ent[k]
        if ent.get("enabled_default") is False:
            rec["enabled_default"] = False

        # Circuit membership: from the bundle's circuit uid (works for
        # shared-tag points like pump/mixer/flow temperature whose OA cannot
        # reveal the circuit), falling back to the read OA's own circuit tag.
        hc_uid = str(ent.get("heating_circuit_uid") or "").strip()
        if hc_uid:
            hc_tag = hc_uid_to_tag.get(hc_uid)
            if hc_tag is None and oa.instance_tag in HC_TAGS:
                hc_tag = oa.instance_tag
            if hc_tag is not None:
                rec["hc_tag"] = hc_tag

        existing = by_id.get(read_id)
        if existing is None or PLATFORM_PRIORITY.index(kind) < PLATFORM_PRIORITY.index(existing["platform"]):
            if existing is not None:
                # Keep any metadata the lower-priority record contributed
                # (e.g. a sensor's unit alongside a number).
                for k, v in existing.items():
                    rec.setdefault(k, v)
                rec["platform"] = kind
                rec["id"] = read_id
            by_id[read_id] = rec

    for kind, key in (
        ("sensor", "sensors"),
        ("binary_sensor", "binary_sensors"),
        ("number", "numbers"),
        ("select", "selects"),
        ("text", "texts"),
        ("switch", "switches"),
    ):
        for ent in ctrl.get(key, []) or []:
            if isinstance(ent, dict):
                visit(kind, ent)

    return by_id


def _propagate_hc_confirmed(
    universe: Dict[str, Dict[str, Any]],
) -> Tuple[int, int, int]:
    """Instantiate CONFIRMED heating-circuit triples on all four circuit tags.

    A triple (object_type, point_index, member_id) observed on >=2 circuit
    instance tags (via APK evidence and/or the reference bundle) is proven to
    be part of the per-circuit template and is generated for every circuit
    tag; an already-known instance (usually APK-only) that lacks reference
    metadata is enriched with the donor circuit's shaping — same triple, no
    guessed addresses. Single-tag triples are left untouched (addresses on
    the other circuits are not derivable).
    Returns (confirmed_triples, added_points, enriched_points).
    """

    hc_ordinal = {tag: i + 1 for i, tag in enumerate(HC_TAGS)}

    by_triple: Dict[Tuple[int, int, int], Dict[int, Dict[str, Any]]] = {}
    for rec in universe.values():
        oa = rec["oa"]
        if oa.instance_tag in hc_ordinal:
            by_triple.setdefault(
                (oa.object_type, oa.point_index, oa.member_id), {}
            )[oa.instance_tag] = rec

    confirmed = {t: recs for t, recs in by_triple.items() if len(recs) >= 2}

    added = 0
    enriched = 0
    for (object_type, point_index, member_id), recs in confirmed.items():
        # Metadata donor: prefer an instance the reference plant proves
        # (platform shaping, write binding, options, bounds), deterministic
        # tie-break by tag order.
        donor_tag = min(
            recs,
            key=lambda t: (0 if recs[t].get("ref") else 1, HC_TAGS.index(t)),
        )
        donor = recs[donor_tag]
        donor_ref = donor.get("ref")

        # Write-binding evidence across ALL proven instances of the triple.
        ref_writes: Dict[int, str] = {}
        for t, r in recs.items():
            t_ref = r.get("ref")
            if t_ref and t_ref.get("write_id"):
                ref_writes[t] = str(t_ref["write_id"])

        def _derived_write(target_tag: int, target_id: str) -> Optional[str]:
            """A write binding for another circuit only when the evidence
            proves it — a write OA is never invented (spec v4 A6):

            - all proven instances (>=2) share ONE identical binding -> that
              common binding is preserved verbatim;
            - every instance writes through its own read address -> the
              target's read address;
            - every instance writes its own per-circuit OA of one common
              write triple, confirmed on >=2 circuits -> that triple retagged;
            - anything else (single-instance non-self binding, mixed or
              cross-module patterns) -> None.
            """
            distinct = set(ref_writes.values())
            if len(distinct) == 1 and len(ref_writes) >= 2:
                return next(iter(distinct))
            if all(
                w == oa_catalog.encode_oa(object_type, t, point_index, member_id)
                for t, w in ref_writes.items()
            ):
                return target_id
            write_triples = set()
            for t, w in ref_writes.items():
                wo = oa_catalog.try_decode_oa(w)
                if wo is None or wo.instance_tag != t:
                    return None
                write_triples.add((wo.object_type, wo.point_index, wo.member_id))
            if len(write_triples) == 1 and len(ref_writes) >= 2:
                wot, wpi, wmid = next(iter(write_triples))
                return oa_catalog.encode_oa(wot, target_tag, wpi, wmid)
            return None

        def _derived_ref(target_tag: int, target_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
            """The donor's reference metadata re-addressed to another circuit."""
            if donor_ref is None:
                return None, None
            ref = {k: v for k, v in donor_ref.items() if k not in {"id", "write_id", "name", "hc_tag"}}
            ref["id"] = target_id
            bundle_name = _rewrite_hc_ordinal(
                str(donor_ref.get("name") or ""), hc_ordinal[target_tag]
            ) or None
            if donor_ref.get("hc_tag") is not None:
                ref["hc_tag"] = target_tag
            if ref_writes:
                write_id = _derived_write(target_tag, target_id)
                if write_id is not None:
                    ref["write_id"] = write_id
                else:
                    # Write evidence exists but cannot be transferred without
                    # guessing an address: the derived point is read-only.
                    ref["platform"] = "sensor"
            return ref, bundle_name

        for target_tag in HC_TAGS:
            target_id = oa_catalog.encode_oa(object_type, target_tag, point_index, member_id)
            existing = universe.get(target_id)
            if existing is not None:
                if existing.get("ref") is None and donor_ref is not None:
                    ref, bundle_name = _derived_ref(target_tag, target_id)
                    existing["ref"] = ref
                    if not existing.get("bundle_name"):
                        existing["bundle_name"] = bundle_name
                    existing["sources"] = sorted(set(existing["sources"]) | {"hc_template"})
                    enriched += 1
                continue

            ref, bundle_name = _derived_ref(target_tag, target_id)
            universe[target_id] = {
                "oa": oa_catalog.decode_oa(target_id),
                # Labels need the ordinal rewrite ("Heizkreis 1" -> "Heizkreis 2").
                "labels": [
                    _rewrite_hc_ordinal(lab, hc_ordinal[target_tag])
                    for lab in donor.get("labels") or []
                ],
                "symbols": list(donor.get("symbols") or []),
                "sources": sorted(set(donor.get("sources") or []) | {"hc_template"}),
                "ref": ref,
                "bundle_name": bundle_name,
            }
            added += 1

    return len(confirmed), added, enriched


def build_catalog(
    *,
    model_path: Path,
    names_path: Path,
    reference_path: Path,
    live_path: Path,
    strings_path: Path,
    translations_path: Optional[Path] = None,
) -> Dict[str, Any]:
    model = _load_json(model_path)
    names = _load_json(names_path)
    ctrl = _reference_controller(_load_json(reference_path))
    token_lists = _live_token_lists(_load_json(live_path))
    en_map, en_suffixes, en_ambiguous = _apk_state_labels(strings_path)
    tr = _load_translations(
        translations_path or REPO_ROOT / "tools" / "catalog_translations.json"
    )

    # Shared German token labels: per-token translations of the shared APK
    # English map (same token-keyed justification as the EN side), plus the
    # {en, de} fills for tokens with no label evidence at all. The fills
    # extend BOTH shared maps. Per-token provenance is recorded and shipped.
    shared_de: Dict[str, str] = {
        key: tr["enum_shared_en_to_de"][key]
        for key in en_map
        if key in tr["enum_shared_en_to_de"]
    }
    token_sources = {
        "en": {key: "apk" for key in en_map},
        "de": {key: "translated" for key in shared_de},
    }
    for key, both in tr["enum_token_labels_both"].items():
        if key not in en_map:
            en_map[key] = str(both["en"])
            token_sources["en"][key] = "fill"
        if key not in shared_de:
            shared_de[key] = str(both["de"])
            token_sources["de"][key] = "fill"
    shared_untranslated = sorted(k for k in en_map if k not in shared_de)

    apk_records = [r for r in model["catalog"] if r.get("classification") == "ochsner_object_address"]
    names_by_id = names.get("apk_id_catalog") or {}
    ref_records = _reference_records(ctrl)

    # Language-aware enum labels: index-join every enum-shaped reference
    # point that has a live token list (list index = numeric value).
    for read_id, ref in ref_records.items():
        tokens = token_lists.get(read_id)
        if not tokens:
            continue
        if ref.get("platform") == "select" or ref.get("options") or ref.get("value_map"):
            _attach_enum_labels(ref, tokens, tr["enum_labels_de_to_en"])

    # No shared German map: bundle evidence is point-specific (review round
    # 1) — consistency on one reference plant does not justify relabelling
    # another plant's point. German is per-point join or raw token.

    # The explicitly requested reference-plant result, on distinct reference
    # SELECT records: how many have live descriptors / German maps / map
    # fully (spec: "33 of 37 ... report your actual number").
    ref_selects = [r for r in ref_records.values() if r.get("platform") == "select"]
    ref_selects_de = [r for r in ref_selects if r.get("enum_labels_de")]
    ref_select_stats = {
        "total": len(ref_selects),
        "with_descriptor": sum(1 for r in ref_selects if token_lists.get(r["id"])),
        "de_mapped": len(ref_selects_de),
        "de_fully_mapped": sum(
            1 for r in ref_selects_de if "de" not in (r.get("enum_label_gaps") or {})
        ),
    }

    excluded_schedule = 0
    excluded_descriptor = 0

    # Universe: every APK address plus every reference read id, with all name
    # evidence attached. Membership/naming decisions happen after HC
    # propagation so a propagated point sees the full evidence too.
    universe: Dict[str, Dict[str, Any]] = {}

    for r in apk_records:
        oa_id = str(r["id"])
        # Trust our codec, not the report: re-encode and require equality.
        oa = oa_catalog.decode_oa(oa_id)
        assert oa_catalog.encode_oa(*oa) == oa_id
        if oa.object_type == oa_catalog.SCHEDULE_OBJECT_TYPE or oa_catalog.is_schedule_member(oa.member_id):
            excluded_schedule += 1
            continue
        if oa.member_id == oa_catalog.DESCRIPTOR_MEMBER_ID:
            excluded_descriptor += 1
            continue
        labels, symbols = _apk_name_sources(names_by_id.get(oa_id), r)
        universe[oa_id] = {
            "oa": oa,
            "labels": labels,
            "symbols": symbols,
            "sources": ["apk"],
            "ref": None,
            "bundle_name": None,
        }

    for read_id, ref in ref_records.items():
        entry = universe.get(read_id)
        if entry is None:
            entry = {
                "oa": oa_catalog.decode_oa(read_id),
                "labels": [],
                "symbols": [],
                "sources": [],
                "ref": None,
                "bundle_name": None,
            }
            universe[read_id] = entry
        entry["sources"] = sorted(set(entry["sources"]) | {"reference"})
        entry["ref"] = ref
        entry["bundle_name"] = str(ref.get("name") or "") or None

    confirmed_triples, propagated, enriched = _propagate_hc_confirmed(universe)

    # Final membership + naming + flags.
    points: Dict[str, Dict[str, Any]] = {}
    excluded_unnamed = 0
    name_gaps: Dict[str, set] = {"en": set(), "de": set()}
    for oa_id in sorted(universe):
        entry = universe[oa_id]
        name, name_source = _choose_name(
            entry["labels"], entry.get("bundle_name"), entry["symbols"]
        )
        if name is None:
            excluded_unnamed += 1
            continue

        rec: Dict[str, Any] = {
            "id": oa_id,
            "platform": "sensor",
            "name": name,
            "name_source": name_source,
            "sources": entry["sources"],
        }
        # The explicit bilingual pair (native evidence per language, the
        # missing side filled from the reviewed translation data). ``name``
        # stays the raw-evidence English-priority choice: overlay merging and
        # the v4 flag rules key off it, and it is never a translation.
        rec.update(
            _bilingual_names(
                entry["labels"],
                entry.get("bundle_name"),
                entry["symbols"],
                tr,
                name_gaps,
            )
            or {}
        )
        apply_apk_number_binding(rec)
        ref = entry.get("ref")
        if ref is not None:
            for k in (
                "platform",
                "write_id",
                "unit",
                "options",
                "value_map",
                "min",
                "max",
                "bundle_min",
                "bundle_max",
                "step",
                "on_value",
                "off_value",
                "enabled_default",
                "hc_tag",
                "enum_labels_de",
                "enum_labels_en",
            ):
                if ref.get(k) is not None:
                    rec[k] = ref[k]
            _finalize_enum_labels(
                rec,
                (ref.get("_enum_tokens") or []),
                tr["enum_point_overrides"].get(oa_id) or {},
                en_map,
                shared_de,
            )
        elif entry["oa"].instance_tag in HC_TAGS:
            # A circuit's own point groups under that circuit's device even
            # without reference metadata: the OA tag identifies the circuit.
            rec["hc_tag"] = entry["oa"].instance_tag

        # Privacy/service classification sees all HUMAN name evidence, not
        # only the chosen display name (an APK-first "Program selection" must
        # not mask the service evidence in its bundle name "Modus
        # Austrocknungsprogramm"). Technical symbols are code identifiers and
        # are excluded: substring-matching prose patterns against camel case
        # produces false positives ("n8bDryingTemperatureStart" ~ "restart").
        evidence = list(entry["labels"])
        if entry.get("bundle_name"):
            evidence.append(str(entry["bundle_name"]))
        points[oa_id] = oa_catalog.apply_v4_point_flags(rec, extra_names=evidence)

    # Instance tag table (drives the phase-A module probe).
    module_by_tag: Dict[int, str] = {}
    for t in model.get("instance_tags") or []:
        if t.get("classification") == "ochsner_instance_tag":
            module_by_tag[int(t["instance_tag"])] = str(t.get("module") or "")
    all_tags = sorted({universe[oid]["oa"].instance_tag for oid in points})
    tags = [
        {"tag": tag, "module": module_by_tag.get(tag, "reference_only")}
        for tag in all_tags
    ]

    point_list = [points[k] for k in sorted(points)]
    body = {
        "enum_token_labels": {
            "en": dict(sorted(en_map.items())),
            "de": dict(sorted(shared_de.items())),
        },
        # Per-token provenance of the shared maps (en: apk resource | fill,
        # de: reviewed translation | fill); per-point maps carry theirs in
        # each point's enum_label_sources.
        "enum_token_label_sources": {
            "en": dict(sorted(token_sources["en"].items())),
            "de": dict(sorted(token_sources["de"].items())),
        },
        "hc_tags": HC_TAGS,
        "points": point_list,
        "tags": tags,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "schema_version": 1,
        "catalog_version": digest,
        "app_source": "com.ochsner.app v1.2.0/1785 + reference plant seed",
        **body,
        "stats": {
            "points_total": len(point_list),
            "enabled": sum(1 for p in point_list if p.get("enabled_default") is not False),
            "disabled": sum(1 for p in point_list if p.get("enabled_default") is False),
            "disabled_technical": sum(1 for p in point_list if p.get("technical")),
            "writable_points": sum(1 for p in point_list if p.get("write_id")),
            "by_name_source": {
                src: sum(1 for p in point_list if p["name_source"] == src)
                for src in ("apk_label", "bundle", "apk_symbol")
            },
            "apk_points": sum(1 for p in point_list if "apk" in p["sources"]),
            "reference_points": sum(1 for p in point_list if "reference" in p["sources"]),
            "hc_template_points": sum(1 for p in point_list if "hc_template" in p["sources"]),
            "hc_confirmed_triples": confirmed_triples,
            "hc_template_propagated": propagated,
            "hc_template_enriched": enriched,
            "excluded_schedule": excluded_schedule,
            "excluded_descriptor": excluded_descriptor,
            "excluded_unnamed": excluded_unnamed,
            "name_de_points": sum(1 for p in point_list if p.get("name_de")),
            "by_name_en_source": {
                src: sum(1 for p in point_list if p.get("name_en_source") == src)
                for src in ("apk_label", "bundle", "apk_symbol", "translated")
            },
            "by_name_de_source": {
                src: sum(1 for p in point_list if p.get("name_de_source") == src)
                for src in ("apk_label", "bundle", "apk_symbol", "translated")
            },
            # Prose names still missing a translation (the bilingual audit
            # target is for both lists to be empty).
            "name_translation_gaps": {
                "en": sorted(name_gaps["en"]),
                "de": sorted(name_gaps["de"]),
            },
            "enum_points_with_de_labels": sum(1 for p in point_list if p.get("enum_labels_de")),
            "enum_points_with_en_labels": sum(1 for p in point_list if p.get("enum_labels_en")),
            "enum_points_fully_mapped_de": sum(
                1
                for p in point_list
                if p.get("enum_labels_de") and "de" not in (p.get("enum_label_gaps") or {})
            ),
            "enum_points_with_de_gaps": sum(
                1 for p in point_list if "de" in (p.get("enum_label_gaps") or {})
            ),
            "enum_points_with_en_gaps": sum(
                1 for p in point_list if "en" in (p.get("enum_label_gaps") or {})
            ),
            "reference_selects": ref_select_stats,
            "shared_token_labels_en": len(en_map),
            "shared_token_labels_de": len(shared_de),
            "shared_tokens_untranslated_de": shared_untranslated,
            "en_suffixes_ambiguous": en_ambiguous,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, default=REPO_ROOT / "apk_files/reports/catalog_model.json")
    ap.add_argument("--names", type=Path, default=REPO_ROOT / "apk_files/reports/catalog_names.json")
    ap.add_argument("--reference", type=Path, default=REPO_ROOT / "apk_files/ha_config/core.config_entries")
    ap.add_argument("--live", type=Path, default=REPO_ROOT / "apk_files/live_ids.json")
    ap.add_argument("--strings", type=Path, default=REPO_ROOT / "apk_files/jadx_out/resources/res/values/strings.xml")
    ap.add_argument("--translations", type=Path, default=REPO_ROOT / "tools/catalog_translations.json")
    ap.add_argument("--out", type=Path, default=PKG_DIR / "data" / "discovery_catalog.json")
    args = ap.parse_args()

    catalog = build_catalog(
        model_path=args.model,
        names_path=args.names,
        reference_path=args.reference,
        live_path=args.live,
        strings_path=args.strings,
        translations_path=args.translations,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, sort_keys=True, indent=1)
        fh.write("\n")

    print(f"Wrote {args.out} (catalog_version={catalog['catalog_version']})")
    print(json.dumps(catalog["stats"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
