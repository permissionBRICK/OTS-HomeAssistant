from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from .api import ClimatixGenericApi


_LOGGER = logging.getLogger(__name__)


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    if not isinstance(s, str):
        return str(s)
    # Some translation entries are HTML snippets; we only need a short title.
    s = _HTML_TAG_RE.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _get_translation_tables(bundle_root: Any) -> tuple[Dict[str, Any], str, List[str]]:
    """Return (translations, fallback_lang, enabled_langs)."""
    if not isinstance(bundle_root, dict):
        return {}, "EN", []
    t = bundle_root.get("translation")
    if not isinstance(t, dict):
        return {}, "EN", []

    translations = t.get("translations")
    if not isinstance(translations, dict):
        translations = {}

    fallback = t.get("fallback")
    fallback_lang = str(fallback).strip().upper() if isinstance(fallback, str) and fallback.strip() else "EN"

    enabled = t.get("enabled")
    enabled_langs: List[str] = []
    if isinstance(enabled, list):
        for x in enabled:
            if isinstance(x, str) and x.strip():
                enabled_langs.append(x.strip().upper())

    return translations, fallback_lang, enabled_langs


def _make_translator(bundle_root: Any, *, language: Optional[str]) -> Callable[[str], str]:
    translations, fallback_lang, enabled_langs = _get_translation_tables(bundle_root)

    requested_lang = (language or "AUTO").strip().upper()
    # Special-case: if Home Assistant is set to German, prefer the bundle's default
    # (typically German) labels over translated ones. In practice some bundles either
    # lack DE translations or fall back to EN, which makes a German UI look odd.
    prefer_default_labels = requested_lang.startswith("DE")

    if prefer_default_labels:
        # Use bundle default/raw labels as-is. Many bundles already ship German
        # default names and the translation table can change wording.
        #
        # Exception: HTML widget section titles are referenced by keys like "HTML-..."
        # and need a translation lookup to be meaningful.
        lang = requested_lang.split("-")[0] if requested_lang else "DE"

        def tr(s: str) -> str:
            if not isinstance(s, str):
                return s
            if s.startswith("HTML-"):
                entry = translations.get(s)
                if isinstance(entry, str) and entry.strip():
                    return entry.strip()
                if isinstance(entry, dict):
                    v = entry.get(lang) or entry.get(fallback_lang)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                    for vv in entry.values():
                        if isinstance(vv, str) and vv.strip():
                            return vv.strip()
            return s.lstrip("#")

        return tr

    lang = requested_lang
    if not lang or lang == "AUTO":
        lang = fallback_lang
    # If the bundle constrains enabled languages, clamp to fallback.
    if enabled_langs and lang not in enabled_langs:
        lang = fallback_lang

    def tr(s: str) -> str:
        if not isinstance(s, str):
            return s
        key = s

        def _is_bad(out: str) -> bool:
            o = (out or "").strip()
            return (not o) or o in {"-", "—"}

        # Some bundles use keys with leading '#', some without.
        candidates = [key]
        if key.startswith("#"):
            candidates.append(key.lstrip("#"))
        else:
            candidates.append(f"#{key}")

        for cand in candidates:
            entry = translations.get(cand)
            if entry is None:
                continue
            if isinstance(entry, str):
                out = entry.strip()
                if not _is_bad(out):
                    return out
            if isinstance(entry, dict):
                # Prefer selected language, then fallback, then any non-empty.
                v = entry.get(lang) or entry.get(fallback_lang)
                if isinstance(v, str) and not _is_bad(v):
                    return v.strip()
                for vv in entry.values():
                    if isinstance(vv, str) and not _is_bad(vv):
                        return vv.strip()
        return s

    return tr


_INTERNAL_HASH_KEYS = {
    # Observed UI/layout placeholders that should not become entities.
    "empty",
    "left",
    "right",
    "line",
    "group",
    "weather",
    "shower",
    "50:50",
}


def _looks_like_internal_hash_name(name: str) -> bool:
    n = (name or "").strip()
    if not n.startswith("#"):
        return False
    core = n.lstrip("#").strip().lower()
    if core in _INTERNAL_HASH_KEYS:
        return True
    if re.fullmatch(r"\d+\s*:\s*\d+", core):
        return True
    return False


def _looks_like_json_text(s: str) -> bool:
    if not isinstance(s, str):
        return False
    t = s.strip()
    if not t:
        return False
    if (t.startswith("{") and t.endswith("}")) or (t.startswith("[") and t.endswith("]")):
        return True
    return False


def _looks_like_internal_name_key(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    low = n.lower()
    if low.startswith("value_key_"):
        return True
    # Placeholder/template keys used in some bundles.
    if "[[" in n and "]]" in n:
        return True
    return False


def _normalize_bundle_root(bundle: Any) -> Any:
    """Normalize bundle root across different OTS formats.

    Some OTS responses store the actual UI config JSON as a string in:
    bundle['cloudConfig']['data']

    The CLI unwrapped this via load_bundle_config(); do the same here.
    """

    if not isinstance(bundle, dict):
        return bundle

    # Home Assistant Store files wrap the actual payload:
    # {"version": 1, "minor_version": 1, "key": "...", "data": {...}}
    # If someone passes that wrapper into us, unwrap to the single stored bundle.
    if isinstance(bundle.get("data"), dict) and "version" in bundle and "key" in bundle:
        data = bundle.get("data")
        if isinstance(data, dict) and len(data) == 1:
            only_val = next(iter(data.values()))
            if isinstance(only_val, dict):
                bundle = only_val

    cloud_config = bundle.get("cloudConfig")
    if isinstance(cloud_config, dict):
        data = cloud_config.get("data")
        if isinstance(data, str) and _looks_like_json_text(data):
            try:
                return json.loads(data)
            except Exception:  # noqa: BLE001
                # Fall back to returning the original bundle.
                return bundle
    return bundle


def _is_context_node(d: Dict[str, Any]) -> bool:
    if "class" in d and "uid" in d:
        return True
    if "controllerId" in d and "name" in d:
        return True
    return False


def _context_from_node(d: Dict[str, Any], *, path: str) -> Dict[str, Any]:
    return {
        "name": d.get("name"),
        "class": d.get("class"),
        "controllerId": d.get("controllerId"),
        "uid": d.get("uid"),
        "path": path,
    }


_SKIP_NAME_WORDS = {
    # English
    "day",
    "month",
    "year",
    "hour",
    "minute",
    "second",
    # German
    "tag",
    "monat",
    "jahr",
    "stunde",
    "minute",
    "sekunde",
}


def _looks_like_datetime_part(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    # Match whole words only, so we don't exclude unrelated names.
    return re.fullmatch(r".*\b(" + "|".join(sorted(_SKIP_NAME_WORDS)) + r")\b.*", n) is not None


def _try_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        if isinstance(v, str):
            vv = v.strip().replace(",", ".")
            if vv == "":
                return None
            return float(vv)
        return float(v)
    except Exception:
        return None


def _extract_min_max(e: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    # Bundles vary; try a handful of common keys.
    candidates_min = (
        "min",
        "minimum",
        "minValue",
        "minVal",
        "minLimit",
        "lowerLimit",
        "lower",
        "rangeMin",
        "min_range",
    )
    candidates_max = (
        "max",
        "maximum",
        "maxValue",
        "maxVal",
        "maxLimit",
        "upperLimit",
        "upper",
        "rangeMax",
        "max_range",
    )

    def pick(scope: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
        mn: Optional[float] = None
        mx: Optional[float] = None

        def read_key(key: str) -> Optional[float]:
            if key not in scope:
                return None
            v = scope.get(key)
            if isinstance(v, dict):
                for inner_key in ("value", "val", "current", "default"):
                    if inner_key in v:
                        vv = _try_float(v.get(inner_key))
                        if vv is not None:
                            return vv
            return _try_float(v)

        for k in candidates_min:
            mn = read_key(k)
            if mn is not None:
                break
        for k in candidates_max:
            mx = read_key(k)
            if mx is not None:
                break

        # Some bundles nest ranges under 'range'/'limits'.
        if mn is None or mx is None:
            for nested_key in ("range", "limits", "limit", "valueRange"):
                nested = scope.get(nested_key)
                if isinstance(nested, dict):
                    if mn is None:
                        for k in candidates_min:
                            vv = _try_float(nested.get(k))
                            if vv is not None:
                                mn = vv
                                break
                    if mx is None:
                        for k in candidates_max:
                            vv = _try_float(nested.get(k))
                            if vv is not None:
                                mx = vv
                                break
        return mn, mx

    for scope in (
        e,
        e.get("readBinding") if isinstance(e.get("readBinding"), dict) else None,
        e.get("writeBinding") if isinstance(e.get("writeBinding"), dict) else None,
    ):
        if not isinstance(scope, dict):
            continue
        mn, mx = pick(scope)
        if mn is not None or mx is not None:
            return mn, mx

    return None, None


def _looks_like_entity_node(d: Dict[str, Any]) -> bool:
    rb = d.get("readBinding")
    wb = d.get("writeBinding")
    return isinstance(rb, dict) or isinstance(wb, dict)


def _walk_bundle_for_entities(
    obj: Any,
    *,
    path: str,
    context_stack: List[Dict[str, Any]],
    active_section_source: str = "",
    out: List[Dict[str, Any]],
) -> None:
    if isinstance(obj, dict):
        pushed = False
        if _is_context_node(obj):
            context_stack.append(_context_from_node(obj, path=path))
            pushed = True

        if _looks_like_entity_node(obj):
            rb = obj.get("readBinding")
            wb = obj.get("writeBinding")
            read_generic = rb.get("genericJsonId") if isinstance(rb, dict) else None
            write_generic = wb.get("genericJsonId") if isinstance(wb, dict) else None
            if isinstance(read_generic, str) and read_generic:
                ctx = context_stack[-1] if context_stack else {}
                out.append(
                    {
                        "uid": obj.get("uid"),
                        "name": obj.get("name"),
                        "class": obj.get("class") or ctx.get("class"),
                        "controllerId": obj.get("controllerId") or ctx.get("controllerId"),
                        "deviceUid": ctx.get("uid"),
                        "deviceName": ctx.get("name"),
                        "path": path,
                        "context": list(context_stack),
                        "sectionSource": active_section_source or None,
                        "unit": obj.get("unit"),
                        "guiType": obj.get("guiType"),
                        "dynamicVisibilityRules": obj.get("dynamicVisibilityRules"),
                        "visibilityReferenceInverted": obj.get("visibilityReferenceInverted"),
                        "permissions": obj.get("permissions"),
                        "isOrderBased": obj.get("isOrderBased"),
                        "showName": obj.get("showName"),
                        "translateStates": obj.get("translateStates"),
                        "translateName": obj.get("translateName"),
                        "readPrecision": obj.get("readPrecision"),
                        "writePrecision": obj.get("writePrecision"),
                        "isReadonly": obj.get("isReadonly")
                        if obj.get("isReadonly") is not None
                        else (rb.get("isReadonly") if isinstance(rb, dict) else None),
                        "readBinding": rb if isinstance(rb, dict) else None,
                        "writeBinding": wb if isinstance(wb, dict) else None,
                        # Optional limit bindings (common for analog values)
                        "minLimitBinding": obj.get("minLimitBinding") if isinstance(obj.get("minLimitBinding"), dict) else None,
                        "maxLimitBinding": obj.get("maxLimitBinding") if isinstance(obj.get("maxLimitBinding"), dict) else None,
                        "readId": read_generic,
                        "writeId": write_generic,
                        # Optional range hints (keys vary across bundle versions)
                        "min": obj.get("min"),
                        "max": obj.get("max"),
                        "minValue": obj.get("minValue"),
                        "maxValue": obj.get("maxValue"),
                        "lowerLimit": obj.get("lowerLimit"),
                        "upperLimit": obj.get("upperLimit"),
                        "states": obj.get("states") if isinstance(obj.get("states"), list) else None,
                    }
                )

        for k, v in obj.items():
            if isinstance(v, str) and len(v) > 200000:
                continue
            child_path = f"{path}/{k}" if path else str(k)
            _walk_bundle_for_entities(
                v,
                path=child_path,
                context_stack=context_stack,
                active_section_source=active_section_source,
                out=out,
            )

        if pushed:
            context_stack.pop()
        return

    if isinstance(obj, list):
        current_section_source = active_section_source
        for i, v in enumerate(obj):
            # Within sibling lists, bundles often use HTML widgets as section headers.
            # Track the active header source and attach it to following entities so
            # we can prefix generic names like Istwert/Sollwert/Anforderung.
            if isinstance(v, dict) and str(v.get("class") or "") == "HTMLWidgetInfo":
                src = v.get("source")
                if isinstance(src, str) and src.strip():
                    current_section_source = src.strip()

            child_path = f"{path}[{i}]" if path else f"[{i}]"
            _walk_bundle_for_entities(
                v,
                path=child_path,
                context_stack=context_stack,
                active_section_source=current_section_source,
                out=out,
            )
        return


def list_bundle_entities(bundle: Any) -> List[Dict[str, Any]]:
    bundle = _normalize_bundle_root(bundle)
    out: List[Dict[str, Any]] = []
    _walk_bundle_for_entities(bundle, path="", context_stack=[], out=out)

    # Deduplicate aggressively. UID is best if present; otherwise fall back to readId+writeId+name.
    seen: set[Tuple[str, str, str, str]] = set()
    uniq: List[Dict[str, Any]] = []
    for r in out:
        uid = str(r.get("uid") or "")
        key = (
            uid,
            str(r.get("readId") or ""),
            str(r.get("writeId") or ""),
            str(r.get("name") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _walk_bundle_for_bindings(
    obj: Any,
    *,
    path: str,
    context_stack: List[Dict[str, Any]],
    out: List[Dict[str, Any]],
) -> None:
    if isinstance(obj, dict):
        pushed = False
        if _is_context_node(obj):
            context_stack.append(_context_from_node(obj, path=path))
            pushed = True

        ot = obj.get("objectType")
        oid = obj.get("objectId")
        mid = obj.get("memberId")
        if isinstance(ot, int) and isinstance(oid, int) and isinstance(mid, int):
            gen_id = obj.get("genericJsonId")
            if isinstance(gen_id, str) and gen_id:
                ctx = context_stack[-1] if context_stack else {}
                binding_type: Optional[str] = None
                if path:
                    segs = [s.lower() for s in path.replace("[", "/").replace("]", "").split("/") if s]
                    if "readbinding" in segs:
                        binding_type = "read"
                    if "writebinding" in segs:
                        if binding_type is None or segs[::-1].index("writebinding") < segs[::-1].index("readbinding"):
                            binding_type = "write"

                out.append(
                    {
                        "genericJsonId": gen_id,
                        "isReadonly": obj.get("isReadonly"),
                        "bindingKey": path.rsplit("/", 1)[-1] if "/" in path else path,
                        "bindingType": binding_type,
                        "path": path,
                        "deviceName": ctx.get("name"),
                        "deviceClass": ctx.get("class"),
                        "controllerId": ctx.get("controllerId"),
                        "deviceUid": ctx.get("uid"),
                        "context": list(context_stack),
                    }
                )

        for k, v in obj.items():
            if isinstance(v, str) and len(v) > 200000:
                continue
            child_path = f"{path}/{k}" if path else str(k)
            _walk_bundle_for_bindings(v, path=child_path, context_stack=context_stack, out=out)

        if pushed:
            context_stack.pop()
        return

    if isinstance(obj, list):
        for i, v in enumerate(obj):
            child_path = f"{path}[{i}]" if path else f"[{i}]"
            _walk_bundle_for_bindings(v, path=child_path, context_stack=context_stack, out=out)
        return


def list_bundle_bindings(bundle: Any) -> List[Dict[str, Any]]:
    bundle = _normalize_bundle_root(bundle)
    out: List[Dict[str, Any]] = []
    _walk_bundle_for_bindings(bundle, path="", context_stack=[], out=out)

    seen: set[Tuple[str, str, Optional[str]]] = set()
    uniq: List[Dict[str, Any]] = []
    for r in out:
        key = (str(r.get("genericJsonId")), str(r.get("bindingKey")), r.get("deviceUid"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    return uniq


def _heizkreis_from_context(ctx: Any) -> str:
    if not isinstance(ctx, list):
        return ""

    # The bundle walk pushes the entity node itself onto the context stack.
    # Never treat the leaf entity as a "context" for grouping.
    candidates = ctx[:-1] if ctx else []

    for one in candidates:
        if not isinstance(one, dict):
            continue
        name = one.get("name")
        if not isinstance(name, str):
            continue
        n = name.strip()
        # Only accept a pure Heizkreis container label, not e.g. "Heizkreis 1: Alarm...".
        if re.fullmatch(r"(?i)heizkreis\s*\d+\s*", n):
            return n
    return ""


def _heating_circuit_from_context(ctx: Any) -> Tuple[str, str]:
    """Return (heating_circuit_uid, default_label) if entity is under a Heizkreis context."""
    if not isinstance(ctx, list):
        return "", ""

    # See note in _heizkreis_from_context(): exclude the leaf entity node.
    candidates = ctx[:-1] if ctx else []

    for one in candidates:
        if not isinstance(one, dict):
            continue
        name = one.get("name")
        if not isinstance(name, str):
            continue
        n = name.strip()
        if not re.fullmatch(r"(?i)heizkreis\s*\d+\s*", n):
            continue
        uid = one.get("uid")
        uid_s = str(uid).strip() if uid is not None else ""
        # Fall back to the context name if uid is missing.
        return (uid_s or n), n
    return "", ""


_GENERIC_ENTITY_NAMES = {
    # German (very common in Climatix)
    "istwert",
    "sollwert",
    "anforderung",
    "status",
    # English (in case translations are enabled)
    "actual value",
    "target value",
    "request",
}


_SKIP_GROUP_PREFIXES = {
    # Very generic containers that don't help disambiguate.
    "floating screens",
    "wärmemanagement",
    "betriebsdaten",
    "einstellungen",
    "alarme",
    "alarms",
}


def _best_group_prefix_from_context(ctx: Any) -> str:
    """Return the nearest meaningful DeviceGroupInfo name for prefixing."""
    if not isinstance(ctx, list) or not ctx:
        return ""

    # Exclude the leaf entity node itself.
    candidates = ctx[:-1]
    for one in reversed(candidates):
        if not isinstance(one, dict):
            continue
        cls = str(one.get("class") or "")
        if "DeviceGroupInfo" not in cls:
            continue
        name = one.get("name")
        if not isinstance(name, str):
            continue
        n = name.strip().lstrip("#")
        if not n:
            continue
        if n.lower() in _SKIP_GROUP_PREFIXES:
            continue
        return n
    return ""


def _looks_like_heating_circuit_name_value(name: str) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    # Common German labels used for heating circuit naming.
    if n in {"name", "bezeichnung", "heizkreisname", "heizkreis name", "hk name", "heizkreis-bezeichnung"}:
        return True
    if "heizkreis" in n and ("name" in n or "bezeich" in n):
        return True
    return False


def _score_heating_circuit_name_candidate(e: Dict[str, Any]) -> int:
    s = 0
    name = e.get("name")
    if isinstance(name, str) and _looks_like_heating_circuit_name_value(name):
        s += 100
        if name.strip().lower() in {"name", "bezeichnung"}:
            s += 20
    if not isinstance(name, str) or not name.strip() or name.strip().startswith("#"):
        s -= 50
    unit = e.get("unit")
    if unit is None or (isinstance(unit, str) and not unit.strip()):
        s += 10
    cls = str(e.get("class") or "")
    if "string" in cls.lower() or "text" in cls.lower():
        s += 10
    return s


def _precision_to_step(p: Any) -> Optional[float]:
    if p is None:
        return None
    try:
        pi = int(p)
    except Exception:
        return None
    if pi < 0 or pi > 6:
        return None
    return float(10 ** (-pi))


def _is_temperature_unit(unit: Optional[str]) -> bool:
    if not unit:
        return False
    u = str(unit)
    return "°" in u or ("c" in u.lower() and "m³" not in u)


def _enum_label_from_state(st: Dict[str, Any], fallback: str, *, translate: Callable[[str], str]) -> str:
    label = st.get("label")
    label_s = str(label).strip() if isinstance(label, str) else ""
    if label_s:
        label_s = translate(label_s)
        # If translation failed and label looks like an internal key, drop it.
        if label_s.startswith("#") or label_s.strip() in {"-", "—"}:
            label_s = ""
    return label_s or fallback


def _looks_indexed_enum(states: Any) -> bool:
    if not isinstance(states, list) or not states:
        return False

    read_ids: List[str] = []
    write_ids: List[str] = []
    for st in states:
        if not isinstance(st, dict):
            continue
        rid = st.get("readId")
        wid = st.get("writeId")
        if isinstance(rid, (str, int, float)):
            read_ids.append(str(rid))
        if isinstance(wid, (str, int, float)):
            write_ids.append(str(wid))

    if not read_ids:
        return False

    if len(set(read_ids)) != len(read_ids):
        return True
    if write_ids and all(w == "id" for w in write_ids):
        return True
    return False


def _score_option_label(label: str) -> int:
    s = 0
    l = label.strip()
    if not l:
        return -10
    if l.startswith("#"):
        return -10
    low = l.lower()
    if "aller" in low or "alle" in low:
        s += 20
    if "heizkreis" in low:
        s += 2
    if re.search(r"\d", l):
        s -= 5
    s += min(len(l), 60) // 10
    return s


def _collapse_duplicate_option_values(options: Dict[str, Any]) -> Dict[str, Any]:
    if not options:
        return {}

    value_to_labels: Dict[str, List[str]] = {}
    value_to_value: Dict[str, Any] = {}
    for label, val in options.items():
        vkey = str(val)
        value_to_labels.setdefault(vkey, []).append(str(label))
        value_to_value[vkey] = val

    if all(len(ls) == 1 for ls in value_to_labels.values()):
        return options

    collapsed: Dict[str, Any] = {}
    for vkey, labels in value_to_labels.items():
        best = max(labels, key=_score_option_label)
        collapsed[best] = value_to_value[vkey]
    return collapsed


def _build_enum_options_and_value_map(states: Any, *, first: Any) -> Tuple[Dict[str, Any], Dict[str, str]]:
    if not isinstance(states, list) or not states:
        return {}, {}

    prefer_index = False
    try:
        if first is not None:
            f = float(first)
            if abs(f - round(f)) < 1e-6:
                fi = int(round(f))
                if 0 <= fi < len(states):
                    prefer_index = True
    except Exception:
        pass

    indexed = prefer_index or _looks_indexed_enum(states)

    options: Dict[str, Any] = {}
    value_map: Dict[str, str] = {}

    # Placeholder until a translator is injected by the caller.
    translate: Callable[[str], str] = lambda s: s

    for idx, st in enumerate(states):
        if not isinstance(st, dict):
            continue

        if indexed:
            label = _enum_label_from_state(st, fallback=f"{idx}", translate=translate)
            if label.startswith("#"):
                continue
            options[label] = idx
            value_map[str(idx)] = label
            continue

        code = st.get("readId")
        if not isinstance(code, (str, int, float)):
            continue
        code_s = str(code)
        label = _enum_label_from_state(st, fallback=code_s, translate=translate)
        options[label] = code
        value_map[code_s] = label

    if not indexed:
        options = _collapse_duplicate_option_values(options)
    return options, value_map


def _build_enum_options_and_value_map_localized(
    states: Any,
    *,
    first: Any,
    translate: Callable[[str], str],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Build enum options/value_map with a translation function for labels."""
    if not isinstance(states, list) or not states:
        return {}, {}

    prefer_index = False
    try:
        if first is not None:
            f = float(first)
            if abs(f - round(f)) < 1e-6:
                fi = int(round(f))
                if 0 <= fi < len(states):
                    prefer_index = True
    except Exception:
        pass

    indexed = prefer_index or _looks_indexed_enum(states)

    options: Dict[str, Any] = {}
    value_map: Dict[str, str] = {}

    for idx, st in enumerate(states):
        if not isinstance(st, dict):
            continue

        if indexed:
            label = _enum_label_from_state(st, fallback=f"{idx}", translate=translate)
            if label.startswith("#"):
                continue
            options[label] = idx
            value_map[str(idx)] = label
            continue

        code = st.get("readId")
        if not isinstance(code, (str, int, float)):
            continue
        code_s = str(code)
        label = _enum_label_from_state(st, fallback=code_s, translate=translate)
        options[label] = code
        value_map[code_s] = label

    if not indexed:
        options = _collapse_duplicate_option_values(options)
    return options, value_map


def _looks_like_boolean_enum(options: Dict[str, Any]) -> bool:
    if not options or len(options) != 2:
        return False
    labels = {str(k).strip().lower() for k in options.keys()}
    if labels <= {"aus", "ein"}:
        return True
    if labels <= {"off", "on"}:
        return True
    if labels <= {"false", "true"}:
        return True
    if labels <= {"0", "1"}:
        return True
    return False


def _enum_state_label_quality(states: Any) -> int:
    if not isinstance(states, list) or not states:
        return 0
    score = 0
    for st in states:
        if not isinstance(st, dict):
            continue
        code = st.get("readId")
        label = st.get("label")
        if not isinstance(code, (str, int, float)):
            continue
        code_s = str(code)
        label_s = str(label).strip() if isinstance(label, str) else ""
        if not label_s:
            continue
        if label_s.startswith("#"):
            continue
        score += 3 if label_s != code_s else 1
    return score


def _is_internal_enum_widget(states: Any) -> bool:
    if not isinstance(states, list) or not states:
        return False
    read_ids: List[str] = []
    labels: List[str] = []
    for st in states:
        if not isinstance(st, dict):
            continue
        rid = st.get("readId")
        lab = st.get("label")
        if isinstance(rid, (str, int, float)):
            read_ids.append(str(rid))
        if isinstance(lab, str):
            labels.append(lab.strip())
    if read_ids and all(r == "id" for r in read_ids):
        if labels and all((not l) or l.startswith("#") for l in labels):
            return True
    return False


async def _read_values_by_id(api: ClimatixGenericApi, ids: List[str], *, batch_size: int = 40) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        data = await api.read(chunk)
        values = data.get("values") if isinstance(data, dict) else None
        if isinstance(values, dict):
            for k, v in values.items():
                out[str(k)] = v
    return out


def _extract_first_value(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        return raw[0]
    return raw


async def generate_entities_from_bundle(
    *,
    bundle: Any,
    api: ClimatixGenericApi,
    language: Optional[str] = None,
    probe: bool = True,
    batch_size: int = 40,
    exclude_internal_names: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Generate entity configs from a decoded bundle.

    Returns dict with keys: sensors, binary_sensors, numbers, selects.

    This mirrors the CLI generator logic but runs inside HA.
    """

    bundle_root = _normalize_bundle_root(bundle)
    translate = _make_translator(bundle_root, language=language)

    entities = list_bundle_entities(bundle_root)

    if exclude_internal_names:
        # Drop obvious UI/layout placeholders, but keep user-facing '#...' labels
        # even if the translation table entry is empty.
        entities = [
            e
            for e in entities
            if not (isinstance(e.get("name"), str) and _looks_like_internal_hash_name(str(e.get("name"))))
        ]

    readable_ids: Optional[set[str]] = None
    first_values: Dict[str, Any] = {}

    if probe:
        ids: List[str] = []
        for e in entities:
            rid = e.get("readId")
            if isinstance(rid, str) and rid:
                ids.append(str(rid))
            # Also read dynamic min/max limits when available so we can create proper sliders.
            for bind_key in ("minLimitBinding", "maxLimitBinding"):
                b = e.get(bind_key)
                if isinstance(b, dict):
                    gid = b.get("genericJsonId")
                    if isinstance(gid, str) and gid:
                        ids.append(gid)

        # De-duplicate while preserving order
        ids = list(dict.fromkeys(ids))
        _LOGGER.debug("Probing %d candidate IDs from bundle", len(ids))
        id_to_raw = await _read_values_by_id(api, ids, batch_size=batch_size)
        readable_ids = {k for k, v in id_to_raw.items() if v is not None}
        for k, raw in id_to_raw.items():
            first_values[str(k)] = _extract_first_value(raw)
        _LOGGER.debug("Probe results: %d/%d IDs returned values", len(readable_ids), len(ids))

    # Determine heating circuit display names by looking for a "Name"/"Bezeichnung" value
    # under each Heizkreis context and then using its probed value.
    hc_name_read_id_by_uid: Dict[str, str] = {}
    for e in entities:
        hc_uid, _hc_label = _heating_circuit_from_context(e.get("context"))
        if not hc_uid:
            continue
        ename_raw = e.get("name")
        if not isinstance(ename_raw, str):
            continue
        # Heating circuit name keys can be translated too; check raw and translated.
        ename = translate(ename_raw)
        if not (_looks_like_heating_circuit_name_value(ename_raw) or _looks_like_heating_circuit_name_value(ename)):
            continue
        rid = e.get("readId")
        if not isinstance(rid, str) or not rid:
            continue

        existing = hc_name_read_id_by_uid.get(hc_uid)
        if existing is None:
            hc_name_read_id_by_uid[hc_uid] = rid
        else:
            # Prefer the better-scoring candidate.
            cur_best = next((x for x in entities if x.get("readId") == existing and _heating_circuit_from_context(x.get("context"))[0] == hc_uid), None)
            if cur_best is None or _score_heating_circuit_name_candidate(e) > _score_heating_circuit_name_candidate(cur_best):
                hc_name_read_id_by_uid[hc_uid] = rid

    hc_display_name_by_uid: Dict[str, str] = {}
    if probe and hc_name_read_id_by_uid:
        for hc_uid, rid in hc_name_read_id_by_uid.items():
            v = first_values.get(rid)
            if v is None:
                continue
            vs = str(v).strip()
            # Ignore empty placeholders / translation keys.
            if not vs or vs.startswith("#"):
                continue
            hc_display_name_by_uid[hc_uid] = vs

    if hc_name_read_id_by_uid:
        _LOGGER.debug(
            "Heating circuit name candidates: %s",
            {k: v for k, v in hc_name_read_id_by_uid.items()},
        )
    if hc_display_name_by_uid:
        _LOGGER.debug("Resolved heating circuit names: %s", {k: v for k, v in hc_display_name_by_uid.items()})

    binding_rows = list_bundle_bindings(bundle)

    write_by_key: Dict[Tuple[str, str], str] = {}
    write_readonly_by_key: Dict[Tuple[str, str], bool] = {}
    for r in binding_rows:
        dev = str(r.get("deviceUid") or "")
        key = str(r.get("bindingKey") or "")
        if not dev or not key:
            continue
        k = (dev, key)
        if r.get("bindingType") == "write":
            if r.get("isReadonly") is True:
                write_readonly_by_key[k] = True
            wid = r.get("genericJsonId")
            if isinstance(wid, str) and wid:
                write_by_key[k] = wid

    by_read: Dict[str, List[Dict[str, Any]]] = {}
    for e in entities:
        rid = e.get("readId")
        if isinstance(rid, str) and rid:
            by_read.setdefault(rid, []).append(e)

    def score_entity(e: Dict[str, Any]) -> int:
        s = 0
        name = e.get("name")
        if isinstance(name, str) and name and not name.startswith("#"):
            s += 100

        # Tie-break duplicates using only visibility rules.
        # Some bundles expose multiple UI widgets for the same OA readId; prefer the
        # widget that is visible by default / has explicit visibility conditions.
        dvr = e.get("dynamicVisibilityRules")
        if isinstance(dvr, dict):
            if dvr.get("defaultHidden") is False:
                s += 50
            rules = dvr.get("rules")
            if isinstance(rules, list):
                s += min(len(rules), 5)
                for r in rules:
                    if isinstance(r, dict) and "visibilityCondition" in r:
                        s += 2

        if e.get("showName") is True:
            s += 30
        if isinstance(e.get("unit"), str) and str(e.get("unit") or "").strip():
            s += 15
        if isinstance(e.get("writeId"), str) and e.get("writeId"):
            s += 20
        if str(e.get("class") or "").endswith("BinaryDeviceInfo"):
            s += 25
        states = e.get("states")
        s += _enum_state_label_quality(states)
        if _is_internal_enum_widget(states):
            s -= 100
        return s

    entities = [max(group, key=score_entity) for group in by_read.values()]

    sensors: List[Dict[str, Any]] = []
    binary_sensors: List[Dict[str, Any]] = []
    numbers: List[Dict[str, Any]] = []
    selects: List[Dict[str, Any]] = []
    texts: List[Dict[str, Any]] = []

    for e in entities:
        read_id = e.get("readId")
        if not isinstance(read_id, str) or not read_id:
            continue
        if readable_ids is not None and str(read_id) not in readable_ids:
            continue

        first = first_values.get(read_id)

        name = e.get("name")
        raw_name_s = str(name) if isinstance(name, str) else ""
        name_s = raw_name_s

        # Apply bundle translation when available.
        # In this bundle, many user-facing names are German strings that have EN/FR/... translations
        # under translation.translations, so we translate unconditionally.
        if isinstance(name, str):
            name_s = translate(raw_name_s)

        # Fall back to stripping the leading '#', but keep the ability to drop empty internal keys.
        name_s = name_s.lstrip("#").strip() or "Value"
        if _looks_like_internal_name_key(name_s):
            name_s = "Value"

        hc_uid, hc_label = _heating_circuit_from_context(e.get("context"))
        hc_name = ""
        if hc_uid:
            hc_name = hc_display_name_by_uid.get(hc_uid) or hc_label

        # If we group by heating circuit device, don't prefix the entity name with Heizkreis.
        display_name = name_s
        if not hc_uid:
            hk = _heizkreis_from_context(e.get("context"))
            display_name = f"{hk} {name_s}" if hk and hk.lower() not in name_s.lower() else name_s

        # Many Climatix values are named generically (e.g., Istwert/Sollwert/Anforderung).
        # Prefer a nearby HTML section header (common in this bundle) to disambiguate.
        if display_name.strip().lower() in _GENERIC_ENTITY_NAMES:
            section_source = e.get("sectionSource")
            section_title = ""
            if isinstance(section_source, str) and section_source.strip():
                section_title = _strip_html(translate(section_source)).lstrip("#").strip()

            if section_title and section_title.lower() not in display_name.lower():
                display_name = f"{section_title} - {display_name}"
            else:
                gp = _best_group_prefix_from_context(e.get("context"))
                if gp and gp.lower() not in display_name.lower():
                    display_name = f"{gp} {display_name}"

        # Remove noisy timestamp fragments.
        if _looks_like_datetime_part(display_name):
            continue

        uid = e.get("uid")
        uuid_val = str(uid) if uid else f"{e.get('class') or 'value'}:{read_id}"

        cls = str(e.get("class") or "")
        states = e.get("states")
        has_states = isinstance(states, list) and bool(states)
        is_enum = has_states or cls.endswith("EnumDeviceInfo")

        options: Dict[str, Any] = {}
        enum_value_map: Dict[str, str] = {}
        if has_states:
            # Translate enum labels when requested or when labels are translation keys.
            if e.get("translateStates") is True:
                options, enum_value_map = _build_enum_options_and_value_map_localized(states, first=first, translate=translate)
            else:
                # Still translate labels that are explicit translation keys.
                options, enum_value_map = _build_enum_options_and_value_map_localized(states, first=first, translate=translate)

        write_id = e.get("writeId")

        rb = e.get("readBinding") if isinstance(e.get("readBinding"), dict) else None
        dev_uid = str(e.get("deviceUid") or "")
        binding_key = str((rb or {}).get("bindingKey") or "")
        if (not isinstance(write_id, str) or not write_id) and dev_uid and binding_key:
            write_id = write_by_key.get((dev_uid, binding_key))

        is_write_readonly = False
        if dev_uid and binding_key and write_readonly_by_key.get((dev_uid, binding_key)) is True:
            is_write_readonly = True

        if is_enum and _is_internal_enum_widget(states):
            continue

        if cls.endswith("BinaryDeviceInfo"):
            cfg: Dict[str, Any] = {"name": display_name, "uuid": uuid_val, "id": read_id}
            if hc_uid:
                cfg["heating_circuit_uid"] = hc_uid
                cfg["heating_circuit_name"] = hc_name
            binary_sensors.append(cfg)
            continue

        if is_enum and options and _looks_like_boolean_enum(options):
            cfg = {"name": display_name, "uuid": uuid_val, "id": read_id}
            if hc_uid:
                cfg["heating_circuit_uid"] = hc_uid
                cfg["heating_circuit_name"] = hc_name
            binary_sensors.append(cfg)
            continue

        if is_enum and options and isinstance(write_id, str) and write_id and not is_write_readonly:
            cfg: Dict[str, Any] = {
                "name": display_name,
                "uuid": uuid_val,
                "read_id": read_id,
                "write_id": write_id,
                "options": options,
            }
            if hc_uid:
                cfg["heating_circuit_uid"] = hc_uid
                cfg["heating_circuit_name"] = hc_name
            selects.append(cfg)
            continue

        if is_enum and options:
            cfg = {
                "name": display_name,
                "uuid": uuid_val,
                "id": read_id,
                "value_map": enum_value_map,
            }
            if hc_uid:
                cfg["heating_circuit_uid"] = hc_uid
                cfg["heating_circuit_name"] = hc_name
            sensors.append(cfg)
            continue

        unit = e.get("unit")
        unit_s = str(unit) if isinstance(unit, str) and unit.strip() else None

        can_be_number = True
        if probe:
            try:
                float(first)
            except Exception:
                can_be_number = False

        if isinstance(write_id, str) and write_id and (not is_write_readonly) and can_be_number:
            step = _precision_to_step(e.get("writePrecision"))
            if step is None:
                step = 1.0
            if _is_temperature_unit(unit_s) and step >= 1.0:
                step = 0.1

            n_cfg: Dict[str, Any] = {"name": display_name, "uuid": uuid_val, "read_id": read_id, "write_id": write_id}
            if hc_uid:
                n_cfg["heating_circuit_uid"] = hc_uid
                n_cfg["heating_circuit_name"] = hc_name
            if unit_s:
                n_cfg["unit"] = unit_s
            if step is not None:
                n_cfg["step"] = step

            mn, mx = _extract_min_max(e)

            # If direct min/max hints are missing, try dynamic min/max bindings (very common).
            if probe and (mn is None or mx is None):
                def _binding_value(key: str) -> Optional[float]:
                    b = e.get(key)
                    if not isinstance(b, dict):
                        return None
                    gid = b.get("genericJsonId")
                    if not isinstance(gid, str) or not gid:
                        return None
                    return _try_float(first_values.get(gid))

                if mn is None:
                    mn = _binding_value("minLimitBinding")
                if mx is None:
                    mx = _binding_value("maxLimitBinding")

            # Only apply min/max when both are present and sensible.
            if mn is not None and mx is not None and mx > mn:
                n_cfg["min"] = mn
                n_cfg["max"] = mx
            numbers.append(n_cfg)
            continue

        # Writable non-numeric values (typically strings like IP address) -> expose as HA Text.
        # We keep enums handled above (select/binary), and only use Text when a writeId exists.
        if isinstance(write_id, str) and write_id and (not is_write_readonly) and ((not probe) or first is None or isinstance(first, str)):
            t_cfg: Dict[str, Any] = {
                "name": display_name,
                "uuid": uuid_val,
                "read_id": read_id,
                "write_id": write_id,
            }
            if hc_uid:
                t_cfg["heating_circuit_uid"] = hc_uid
                t_cfg["heating_circuit_name"] = hc_name
            texts.append(t_cfg)
            continue

        s_cfg: Dict[str, Any] = {"name": display_name, "uuid": uuid_val, "id": read_id}
        if hc_uid:
            s_cfg["heating_circuit_uid"] = hc_uid
            s_cfg["heating_circuit_name"] = hc_name
        if unit_s:
            s_cfg["unit"] = unit_s
        sensors.append(s_cfg)

    out: Dict[str, Any] = {
        # Avoid duplicates: if a value is controllable (number/select), don't also add it as a sensor.
        "sensors": [
            s
            for s in sensors
            if str(s.get("id")) not in {str(n.get("read_id")) for n in numbers}
            and str(s.get("id")) not in {str(sel.get("read_id")) for sel in selects}
            and str(s.get("id")) not in {str(t.get("read_id")) for t in texts}
        ],
        "binary_sensors": binary_sensors,
        "numbers": numbers,
        "selects": selects,
        "texts": texts,
    }

    return out
