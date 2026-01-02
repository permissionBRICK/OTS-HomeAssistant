#!/usr/bin/env python3
"""Minimal client for the Siemens Climatix local JSON endpoint used by this app.

Reverse-engineered from:
- sources/p076h3/C2175f.java (direct/local HTTP)
- sources/p086i3/C2238c.java (ReadAction)
- sources/p086i3/C2239d.java (WriteAction)
- sources/p246y2/C4106a.java (Climatix jsonId encoding)

Endpoint:
  GET http://{host}:{port}/JSON.HTML?FN=Read&ID=<id>&LNG=-1&US=1[&PIN=<pin>]
  GET http://{host}:{port}/JSON.HTML?FN=Write&ID=<id>;<value>&LNG=-1&US=1[&PIN=<pin>]

Auth:
  HTTP Basic auth, default user/pass seen in ClimatixJSONControllerInfo:
    user = JSON
    pass = SBTAdmin!

This script intentionally stays dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import re
import sys
import textwrap
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


DEFAULT_USER = "JSON"
DEFAULT_PASS = "SBTAdmin!"
DEFAULT_PORT = 80

OTS_BASE_URL = "https://ots-services.ochsner.com/app"


@dataclass(frozen=True)
class Connection:
    host: str
    port: int = DEFAULT_PORT
    username: str = DEFAULT_USER
    password: str = DEFAULT_PASS
    pin: Optional[str] = None
    timeout_sec: float = 10.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/JSON.HTML"

    @property
    def generic_base_url(self) -> str:
        return f"http://{self.host}:{self.port}/jsongen.html"


def _basic_auth_header(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _build_query(fn: str, ids: Sequence[str], *, pin: Optional[str]) -> str:
    # The app sends: ...?FN=Read&ID=<id1>&ID=<id2>&LNG=-1&US=1[&PIN=<pin>]
    # For write it sends ID=<id>;<value>
    params: List[Tuple[str, str]] = [("FN", fn)]
    for one in ids:
        params.append(("ID", one))
    if pin:
        params.append(("PIN", pin))
    params.append(("LNG", "-1"))
    params.append(("US", "1"))
    return urllib.parse.urlencode(params, doseq=True)


def _build_generic_query(fn: str, oas: Sequence[str], *, pin: Optional[str]) -> str:
    # Generic endpoint uses OA=<genericJsonId> (or OA=<genericJsonId>;<value>)
    params: List[Tuple[str, str]] = [("FN", fn)]
    for one in oas:
        params.append(("OA", one))
    # App always includes PIN on generic endpoint.
    if pin:
        params.append(("PIN", pin))
    params.append(("LNG", "-1"))
    params.append(("US", "1"))
    return urllib.parse.urlencode(params, doseq=True)


def climatix_read(conn: Connection, ids: Sequence[str]) -> Any:
    if not ids:
        raise ValueError("read requires at least one --id")
    query = _build_query("Read", ids, pin=conn.pin)
    url = f"{conn.base_url}?{query}"

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", _basic_auth_header(conn.username, conn.password))

    with urllib.request.urlopen(req, timeout=conn.timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        return json.loads(body)


def climatix_write(conn: Connection, json_id: str, value: str) -> Any:
    if not json_id:
        raise ValueError("write requires --id")
    if value is None or value == "":
        raise ValueError("write requires --value")

    # App format: ID=<jsonId>;<value>
    query = _build_query("Write", [f"{json_id};{value}"], pin=conn.pin)
    url = f"{conn.base_url}?{query}"

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", _basic_auth_header(conn.username, conn.password))

    with urllib.request.urlopen(req, timeout=conn.timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        return json.loads(body)


def climatix_generic_read(conn: Connection, generic_ids: Sequence[str]) -> Any:
    if not generic_ids:
        raise ValueError("read-generic requires at least one --id")
    query = _build_generic_query("Read", generic_ids, pin=conn.pin)
    url = f"{conn.generic_base_url}?{query}"

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", _basic_auth_header(conn.username, conn.password))

    with urllib.request.urlopen(req, timeout=conn.timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        return json.loads(body)


def climatix_generic_write(conn: Connection, generic_json_id: str, value: str) -> Any:
    if not generic_json_id:
        raise ValueError("write-generic requires --id")
    if value is None or value == "":
        raise ValueError("write-generic requires --value")

    query = _build_generic_query("Write", [f"{generic_json_id};{value}"], pin=conn.pin)
    url = f"{conn.generic_base_url}?{query}"

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", _basic_auth_header(conn.username, conn.password))

    with urllib.request.urlopen(req, timeout=conn.timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        return json.loads(body)


# --- Climatix jsonId encoding/decoding (matches y2/C4106a.m7462o) ---

def _climatix_safe_b64(b: bytes) -> str:
    # App does regular Base64 then replaces + / = with . _ -
    s = base64.b64encode(b).decode("ascii")
    return s.replace("+", ".").replace("/", "_").replace("=", "-")


def _unsafe_climatix_b64(s: str) -> str:
    return s.replace(".", "+").replace("_", "/").replace("-", "=")


def encode_json_id(object_type: int, object_id: int, member_id: int) -> str:
    # Big endian: putShort(objectType), putInt(objectId), putShort(memberId)
    b = (
        int(object_type).to_bytes(2, byteorder="big", signed=False)
        + int(object_id).to_bytes(4, byteorder="big", signed=False)
        + int(member_id).to_bytes(2, byteorder="big", signed=False)
    )
    return _climatix_safe_b64(b)


def decode_json_id(json_id: str) -> Tuple[int, int, int]:
    raw = base64.b64decode(_unsafe_climatix_b64(json_id).encode("ascii"))
    if len(raw) != 8:
        raise ValueError(f"Expected 8 bytes after base64 decode, got {len(raw)}")
    object_type = int.from_bytes(raw[0:2], byteorder="big", signed=False)
    object_id = int.from_bytes(raw[2:6], byteorder="big", signed=False)
    member_id = int.from_bytes(raw[6:8], byteorder="big", signed=False)
    return object_type, object_id, member_id


def encode_generic_json_id(object_type: int, object_id: int, member_id: int) -> str:
    # Little endian: putShort(objectType), putInt(objectId), putShort(memberId)
    b = (
        int(object_type).to_bytes(2, byteorder="little", signed=False)
        + int(object_id).to_bytes(4, byteorder="little", signed=False)
        + int(member_id).to_bytes(2, byteorder="little", signed=False)
    )
    return base64.b64encode(b).decode("ascii")


def decode_generic_json_id(generic_json_id: str) -> Tuple[int, int, int]:
    raw = base64.b64decode(generic_json_id.encode("ascii"))
    if len(raw) != 8:
        raise ValueError(f"Expected 8 bytes after base64 decode, got {len(raw)}")
    object_type = int.from_bytes(raw[0:2], byteorder="little", signed=False)
    object_id = int.from_bytes(raw[2:6], byteorder="little", signed=False)
    member_id = int.from_bytes(raw[6:8], byteorder="little", signed=False)
    return object_type, object_id, member_id


def _iter_files(root: str) -> Iterator[str]:
    if os.path.isfile(root):
        yield root
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield os.path.join(dirpath, name)


def _read_maybe_gzip_bytes(path: str, *, max_bytes: int) -> Optional[bytes]:
    try:
        size = os.path.getsize(path)
        if size > max_bytes:
            return None
        with open(path, "rb") as f:
            b = f.read(max_bytes + 1)
        if len(b) > max_bytes:
            return None
        if len(b) >= 2 and b[0] == 0x1F and b[1] == 0x8B:
            return gzip.decompress(b)
        return b
    except Exception:
        return None


def _looks_like_json_text(s: str) -> bool:
    for ch in s.lstrip():
        return ch in "{["
    return False


def _scan_for_ids_in_text(text: str) -> Iterator[Tuple[str, str]]:
    # Climatix JSON ID: 8 bytes base64 => 12 chars, last two are padding ('==')
    # App replaces '=' with '-' so it ends with '--'. Also replaces '+'->'.', '/'->'_'.
    for m in re.finditer(r"\b([A-Za-z0-9._]{10}--)\b", text):
        yield ("jsonId", m.group(1))
    # Generic JSON ID: standard base64 for 8 bytes ends with '=='
    for m in re.finditer(r"\b([A-Za-z0-9+/]{10}==)\b", text):
        yield ("genericJsonId", m.group(1))


def _extract_bindings_from_json(obj: Any, out: Dict[Tuple[int, int, int], Dict[str, Any]], *, source: str) -> None:
    if isinstance(obj, dict):
        # BindingInfo.java fields: objectType, objectId, memberId, jsonId, genericJsonId, label
        ot = obj.get("objectType")
        oid = obj.get("objectId")
        mid = obj.get("memberId")
        if isinstance(ot, int) and isinstance(oid, int) and isinstance(mid, int):
            key = (ot, oid, mid)
            if key not in out:
                out[key] = {
                    "objectType": ot,
                    "objectId": oid,
                    "memberId": mid,
                    "jsonId": obj.get("jsonId") or encode_json_id(ot, oid, mid),
                    "genericJsonId": obj.get("genericJsonId") or encode_generic_json_id(ot, oid, mid),
                    "label": obj.get("label"),
                    "source": source,
                }
        for v in obj.values():
            _extract_bindings_from_json(v, out, source=source)
        return
    if isinstance(obj, list):
        for v in obj:
            _extract_bindings_from_json(v, out, source=source)


def extract_ids(path: str, *, max_file_kb: int = 4096) -> List[Dict[str, Any]]:
    results: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    max_bytes = max_file_kb * 1024

    for file_path in _iter_files(path):
        blob = _read_maybe_gzip_bytes(file_path, max_bytes=max_bytes)
        if not blob:
            continue
        text = blob.decode("utf-8", errors="ignore")

        # 1) If it's JSON, parse and walk it.
        if _looks_like_json_text(text):
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = None
            if parsed is not None:
                # If this is a Bundle-like JSON, cloudConfig.data is itself a JSON string.
                if isinstance(parsed, dict):
                    cc = parsed.get("cloudConfig")
                    if isinstance(cc, dict):
                        data = cc.get("data")
                        if isinstance(data, str) and _looks_like_json_text(data):
                            try:
                                inner = json.loads(data)
                                _extract_bindings_from_json(inner, results, source=f"{file_path}::cloudConfig.data")
                            except Exception:
                                pass
                _extract_bindings_from_json(parsed, results, source=file_path)

        # 2) Always do a lightweight regex scan for ID-looking tokens.
        for kind, token in _scan_for_ids_in_text(text):
            try:
                if kind == "jsonId":
                    ot, oid, mid = decode_json_id(token)
                    key = (ot, oid, mid)
                    results.setdefault(
                        key,
                        {
                            "objectType": ot,
                            "objectId": oid,
                            "memberId": mid,
                            "jsonId": token,
                            "genericJsonId": encode_generic_json_id(ot, oid, mid),
                            "label": None,
                            "source": file_path,
                        },
                    )
                else:
                    ot, oid, mid = decode_generic_json_id(token)
                    key = (ot, oid, mid)
                    results.setdefault(
                        key,
                        {
                            "objectType": ot,
                            "objectId": oid,
                            "memberId": mid,
                            "jsonId": encode_json_id(ot, oid, mid),
                            "genericJsonId": token,
                            "label": None,
                            "source": file_path,
                        },
                    )
            except Exception:
                continue

    return sorted(results.values(), key=lambda r: (r["objectType"], r["objectId"], r["memberId"]))


# --- Bundle-aware helpers (user-friendly listing/picking) ---


def load_bundle_config(bundle_path: str) -> Any:
    with open(bundle_path, "r", encoding="utf-8") as f:
        outer = json.load(f)
    if not isinstance(outer, dict):
        return outer

    cloud_config = outer.get("cloudConfig")
    if isinstance(cloud_config, dict):
        data = cloud_config.get("data")
        if isinstance(data, str) and _looks_like_json_text(data):
            try:
                return json.loads(data)
            except Exception:
                # Fall back to returning the outer bundle.
                return outer
    return outer


def _shorten(s: Optional[str], n: int, *, truncate: bool) -> str:
    if not s:
        return ""
    s = str(s)
    if not truncate:
        return s
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def _is_context_node(d: Dict[str, Any]) -> bool:
    # Heuristic: many widget/device infos have class+uid and often name/controllerId.
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


def _walk_bundle_for_bindings(
    obj: Any,
    *,
    path: str,
    context_stack: List[Dict[str, Any]],
    out: List[Dict[str, Any]],
) -> None:
    if isinstance(obj, dict):
        # Push context if this looks like a device/widget node.
        pushed = False
        if _is_context_node(obj):
            context_stack.append(_context_from_node(obj, path=path))
            pushed = True

        # Detect binding dicts by presence of (objectType, objectId, memberId).
        ot = obj.get("objectType")
        oid = obj.get("objectId")
        mid = obj.get("memberId")
        if isinstance(ot, int) and isinstance(oid, int) and isinstance(mid, int):
            ctx = context_stack[-1] if context_stack else {}
            json_id = obj.get("jsonId") or encode_json_id(ot, oid, mid)
            gen_id = obj.get("genericJsonId") or encode_generic_json_id(ot, oid, mid)

            # Best-effort classify whether this binding came from a readBinding/writeBinding node.
            binding_type: Optional[str] = None
            if path:
                segs = [s.lower() for s in path.replace("[", "/").replace("]", "").split("/") if s]
                if "readbinding" in segs:
                    binding_type = "read"
                if "writebinding" in segs:
                    # If both appear in the path (rare), prefer the last occurrence.
                    if binding_type is None or segs[::-1].index("writebinding") < segs[::-1].index("readbinding"):
                        binding_type = "write"
            out.append(
                {
                    "objectType": ot,
                    "objectId": oid,
                    "memberId": mid,
                    "jsonId": json_id,
                    "genericJsonId": gen_id,
                    "label": obj.get("label"),
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
            # Avoid dumping huge string blobs into recursion.
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


def list_bundle_bindings(bundle_path: str) -> List[Dict[str, Any]]:
    cfg = load_bundle_config(bundle_path)
    out: List[Dict[str, Any]] = []
    _walk_bundle_for_bindings(cfg, path="", context_stack=[], out=out)

    # Deduplicate by jsonId+bindingKey+deviceUid to reduce noise.
    seen: set[Tuple[str, str, Optional[str]]] = set()
    uniq: List[Dict[str, Any]] = []
    for r in out:
        key = (str(r.get("jsonId")), str(r.get("bindingKey")), r.get("deviceUid"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    return uniq


def _match_binding(r: Dict[str, Any], needle: str) -> bool:
    n = needle.lower()
    for k in ("deviceName", "deviceClass", "controllerId", "bindingKey", "label", "jsonId", "path"):
        v = r.get(k)
        if v is not None and n in str(v).lower():
            return True
    return False


def _match_context(r: Dict[str, Any], needle: str) -> bool:
    n = (needle or "").lower().strip()
    if not n:
        return True
    ctx = r.get("context")
    if not isinstance(ctx, list):
        return False
    for one in ctx:
        if not isinstance(one, dict):
            continue
        for k in ("name", "class", "controllerId", "uid", "path"):
            v = one.get(k)
            if v is not None and n in str(v).lower():
                return True
    return False


def _print_bundle_table(
    rows: List[Dict[str, Any]],
    *,
    limit: int,
    hk_width: int = 12,
    device_width: int = 44,
    key_width: int = 18,
    label_width: int = 12,
    truncate: bool = True,
    id_mode: str = "json",
) -> None:
    id_mode = (id_mode or "json").lower()
    if id_mode not in {"json", "generic", "both"}:
        raise ValueError("id_mode must be one of: json, generic, both")

    # Always show genericJsonId to make it easy to use the jsongen.html (OA=...) endpoint.
    id_label = "genericId" if id_mode == "generic" else "jsonId  genericId"

    def _heizkreis_name(r: Dict[str, Any]) -> str:
        ctx = r.get("context")
        if not isinstance(ctx, list):
            return ""
        for one in ctx:
            if not isinstance(one, dict):
                continue
            name = one.get("name")
            if not isinstance(name, str):
                continue
            if name.lower().startswith("heizkreis"):
                return name
        return ""

    header = (
        f"{'#':>4}  {'ctrl':<10}  {('hk'):<{hk_width}}  {('device'):<{device_width}}  {('key'):<{key_width}}  "
        f"{('label'):<{label_width}}  {'ro':<2}  {id_label}"
    )
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:limit]):
        hk = _shorten(_heizkreis_name(r), hk_width, truncate=truncate)
        json_text = r.get("jsonId") or ""
        gen_text = r.get("genericJsonId") or ""
        if id_mode == "generic":
            id_text = gen_text
        else:
            id_text = f"{json_text}  {gen_text}".rstrip()
        print(
            f"{i:>4}  {str(r.get('controllerId') or ''):<10}  {hk:<{hk_width}}  "
            f"{_shorten(r.get('deviceName'), device_width, truncate=truncate):<{device_width}}  "
            f"{_shorten(r.get('bindingKey'), key_width, truncate=truncate):<{key_width}}  "
            f"{_shorten(r.get('label'), label_width, truncate=truncate):<{label_width}}  "
            f"{('Y' if r.get('isReadonly') else 'N' if r.get('isReadonly') is not None else ''):<2}  "
            f"{id_text}"
        )
    if len(rows) > limit:
        print(f"... and {len(rows) - limit} more (use --limit or --filter)")


def _extract_first_value(raw: Any) -> Optional[Any]:
    """Return the first scalar value from common Climatix response shapes.

    Generic jsongen.html often returns arrays like [x, x].
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        if not raw:
            return None
        return _extract_first_value(raw[0])
    if isinstance(raw, dict):
        # Some endpoints can nest a single scalar.
        # Prefer common keys; otherwise don't guess.
        for k in ("value", "val"):
            if k in raw:
                return _extract_first_value(raw.get(k))
        return None
    return raw


def _value_to_display(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        # Try to normalize numeric strings.
        try:
            n = float(v.strip())
            # Avoid showing trailing .0 for integers.
            if n.is_integer():
                return str(int(n))
            return str(n)
        except Exception:
            return v
    return str(v)


def _read_values_by_id(
    conn: "Connection",
    ids: Sequence[str],
    *,
    use_generic: bool,
    batch_size: int,
) -> Dict[str, Any]:
    """Read many IDs and return mapping id->raw value entry from response."""
    out: Dict[str, Any] = {}
    ids = [i for i in ids if isinstance(i, str) and i]
    if not ids:
        return out
    bs = max(1, int(batch_size))

    for start in range(0, len(ids), bs):
        chunk = ids[start : start + bs]
        resp = climatix_generic_read(conn, chunk) if use_generic else climatix_read(conn, chunk)
        if not isinstance(resp, dict):
            continue
        values = resp.get("values")
        if not isinstance(values, dict):
            continue
        for k, v in values.items():
            out[str(k)] = v
    return out


def _print_bundle_table_with_values(
    rows: List[Dict[str, Any]],
    *,
    id_to_value: Dict[str, Any],
    limit: int,
    use_generic: bool,
    value_width: int = 10,
    hk_width: int = 12,
    device_width: int = 44,
    key_width: int = 18,
    label_width: int = 12,
    truncate: bool = True,
    id_mode: str = "json",
) -> None:
    id_mode = (id_mode or "json").lower()
    if id_mode not in {"json", "generic", "both"}:
        raise ValueError("id_mode must be one of: json, generic, both")

    # Always show genericJsonId to make it easy to use the jsongen.html (OA=...) endpoint.
    id_label = "genericId" if id_mode == "generic" else "jsonId  genericId"

    def _heizkreis_name(r: Dict[str, Any]) -> str:
        ctx = r.get("context")
        if not isinstance(ctx, list):
            return ""
        for one in ctx:
            if not isinstance(one, dict):
                continue
            name = one.get("name")
            if not isinstance(name, str):
                continue
            if name.lower().startswith("heizkreis"):
                return name
        return ""

    header = (
        f"{'#':>4}  {'ctrl':<10}  {('hk'):<{hk_width}}  {('device'):<{device_width}}  {('key'):<{key_width}}  "
        f"{('label'):<{label_width}}  {'ro':<2}  {('val'):<{value_width}}  {id_label}"
    )
    print(header)
    print("-" * len(header))

    for i, r in enumerate(rows[:limit]):
        hk = _shorten(_heizkreis_name(r), hk_width, truncate=truncate)

        primary_id = (r.get("genericJsonId") if use_generic else r.get("jsonId")) or ""
        raw_val = id_to_value.get(str(primary_id)) if primary_id else None
        first_val = _extract_first_value(raw_val)
        val_text = _shorten(_value_to_display(first_val), value_width, truncate=truncate)

        if id_mode == "json":
            json_text = r.get("jsonId") or ""
            gen_text = r.get("genericJsonId") or ""
            id_text = f"{json_text}  {gen_text}".rstrip()
        elif id_mode == "generic":
            id_text = r.get("genericJsonId") or ""
        else:
            json_text = r.get("jsonId") or ""
            gen_text = r.get("genericJsonId") or ""
            id_text = f"{json_text}  {gen_text}".rstrip()

        print(
            f"{i:>4}  {str(r.get('controllerId') or ''):<10}  {hk:<{hk_width}}  "
            f"{_shorten(r.get('deviceName'), device_width, truncate=truncate):<{device_width}}  "
            f"{_shorten(r.get('bindingKey'), key_width, truncate=truncate):<{key_width}}  "
            f"{_shorten(r.get('label'), label_width, truncate=truncate):<{label_width}}  "
            f"{('Y' if r.get('isReadonly') else 'N' if r.get('isReadonly') is not None else ''):<2}  "
            f"{val_text:<{value_width}}  "
            f"{id_text}"
        )
    if len(rows) > limit:
        print(f"... and {len(rows) - limit} more (use --limit or --filter)")


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))


# --- OTS cloud access (Ochsner services) ---


def _normalize_config_id(config_id: str) -> str:
    # Mirrors C2143u.m4467g / m4462b: accept either "C-ABCD-EF01" form or raw.
    s = (config_id or "").strip()
    if not s:
        raise ValueError("config id is empty")
    if s.upper().startswith("C-"):
        s = s[2:].replace("-", "")
    s = s.lower()
    return s


def _format_config_id(config_id: str) -> str:
    # Creates "C-XXXX-YYYY-..." for server request.
    raw = _normalize_config_id(config_id).upper()
    if len(raw) % 4 != 0:
        # Still let the server validate; this just matches the app's formatting.
        return "C-" + raw
    parts = [raw[i : i + 4] for i in range(0, len(raw), 4)]
    return "C-" + "-".join(parts)


def ots_login(username: str, password: str, *, timeout_sec: float = 20.0) -> Dict[str, Any]:
    # Matches OtsServices.m3851c: GET /login with userName/userPass headers.
    url = f"{OTS_BASE_URL}/login"
    req = urllib.request.Request(url, method="GET")
    req.add_header("userName", username)
    req.add_header("userPass", password)
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected login response")
        return data


def ots_getconfig(
    *,
    config_id: str,
    site_id: str,
    stamp: int,
    timeout_sec: float = 60.0,
) -> Dict[str, Any]:
    # Matches OtsServices.m3850b: GET /getconfig?configID=...&siteID=...&stamp=...
    config_id_fmt = _format_config_id(config_id)
    qs = urllib.parse.urlencode({"configID": config_id_fmt, "siteID": site_id, "stamp": str(int(stamp))})
    url = f"{OTS_BASE_URL}/getconfig?{qs}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {body}")
        data = json.loads(body)
        if not isinstance(data, dict):
            raise RuntimeError("Unexpected getconfig response")
        return data


def decode_ots_bundle_content(content_b64_gzip: str) -> Dict[str, Any]:
    # Matches C2140r.m4449a + Bundle.m117b: base64 decode, gzip decompress, UTF-8 JSON.
    raw = base64.b64decode(content_b64_gzip.encode("ascii"))
    decoded = gzip.decompress(raw)
    text = decoded.decode("utf-8", errors="strict")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Decoded bundle is not a JSON object")
    return obj


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="climatix_local_api",
        description="Read/write Siemens Climatix local JSON endpoint (/JSON.HTML).",
    )

    p.add_argument("--host", default=None, help="Controller hostname/IP (required for read/write)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="Controller HTTP port (default: 80)")
    p.add_argument("--user", default=DEFAULT_USER, help=f"Basic auth username (default: {DEFAULT_USER})")
    p.add_argument("--password", default=DEFAULT_PASS, help="Basic auth password")
    p.add_argument(
        "--pin",
        default="7659",
        help="PIN (adds PIN=... to query). Default: 7659. Use --pin '' to omit PIN.",
    )
    p.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")

    sub = p.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser("read", help="Read one or more JSON IDs")
    p_read.add_argument("--id", dest="ids", action="append", required=True, help="Climatix JSON ID (repeatable)")

    p_read_g = sub.add_parser("read-generic", help="Read one or more Generic JSON IDs (jsongen.html / OA=...)")
    p_read_g.add_argument(
        "--id",
        dest="ids",
        action="append",
        required=True,
        help="GenericJsonId (base64, usually ends with '=') (repeatable)",
    )

    p_write = sub.add_parser("write", help="Write a value to a JSON ID")
    p_write.add_argument("--id", required=True, help="Climatix JSON ID")
    p_write.add_argument("--value", required=True, help="Value string, e.g. 21.5")

    p_write_g = sub.add_parser("write-generic", help="Write a value to a Generic JSON ID (jsongen.html / OA=...)")
    p_write_g.add_argument("--id", required=True, help="GenericJsonId (base64, usually ends with '=')")
    p_write_g.add_argument("--value", required=True, help="Value string, e.g. 21.5")

    p_enc = sub.add_parser("encode-id", help="Compute Climatix JSON ID from objectType/objectId/memberId")
    p_enc.add_argument("--object-type", type=int, required=True)
    p_enc.add_argument("--object-id", type=int, required=True)
    p_enc.add_argument("--member-id", type=int, required=True)

    p_dec = sub.add_parser("decode-id", help="Decode Climatix JSON ID into objectType/objectId/memberId")
    p_dec.add_argument("--id", required=True)

    p_ext = sub.add_parser(
        "extract-ids",
        help="Extract candidate Climatix bindings from downloaded config/resources (JSON or cache dump)",
    )
    p_ext.add_argument(
        "--path",
        required=True,
        help="File or directory to scan (bundle JSON, config JSON, or cached resource folder)",
    )
    p_ext.add_argument(
        "--max-file-kb",
        type=int,
        default=4096,
        help="Skip files larger than this (default: 4096 KB)",
    )

    p_ots_login = sub.add_parser("ots-login", help="Login to OTS cloud and list available plants/config IDs")
    p_ots_login.add_argument("--ots-user", required=True, help="OTS cloud username")
    p_ots_login.add_argument("--ots-pass", required=True, help="OTS cloud password")

    p_ots_dl = sub.add_parser(
        "ots-download-bundle",
        help="Download and decode the same bundle the app uses (no phone access required)",
    )
    p_ots_dl.add_argument("--ots-user", required=True, help="OTS cloud username")
    p_ots_dl.add_argument("--ots-pass", required=True, help="OTS cloud password")
    p_ots_dl.add_argument(
        "--plant-index",
        type=int,
        default=None,
        help="Which plant from ots-login to use (0-based)",
    )
    p_ots_dl.add_argument("--config-id", default=None, help="Override configID (from ots-login)")
    p_ots_dl.add_argument("--site-id", default=None, help="Override siteID (from ots-login)")
    p_ots_dl.add_argument(
        "--stamp",
        type=int,
        default=0,
        help="Stamp/last-known timestamp (use 0 to request full config; default: 0)",
    )
    p_ots_dl.add_argument(
        "--out",
        required=True,
        help="Write decoded bundle JSON to this path",
    )
    p_ots_dl.add_argument(
        "--also-extract-ids",
        action="store_true",
        help="After download, also print extracted bindings to stdout",
    )

    p_bl = sub.add_parser("bundle-list", help="List bindings found in a downloaded bundle.json")
    p_bl.add_argument("--bundle", required=True, help="Path to bundle.json (decoded)")
    p_bl.add_argument("--filter", default=None, help="Case-insensitive substring filter")
    p_bl.add_argument(
        "--context-filter",
        default=None,
        help="Also require a match in the ancestor context stack (e.g. 'Heizkreis 4', 'Warmwasser', 'floatingScreens')",
    )
    p_bl.add_argument("--controller", default=None, help="Filter by controllerId (e.g. climatix)")
    p_bl.add_argument("--writable", action="store_true", help="Only show bindings with isReadonly=false")
    p_bl.add_argument("--limit", type=int, default=100, help="Max rows to print (default: 100)")
    p_bl.add_argument("--json", action="store_true", help="Output JSON instead of a table")
    p_bl.add_argument("--no-truncate", action="store_true", help="Do not truncate columns (may wrap)")
    p_bl.add_argument("--device-width", type=int, default=24, help="Device column width (default: 24)")
    p_bl.add_argument("--key-width", type=int, default=18, help="Key column width (default: 18)")
    p_bl.add_argument("--label-width", type=int, default=12, help="Label column width (default: 12)")
    p_bl.add_argument(
        "--wide",
        action="store_true",
        help="Shortcut for wider columns (device=48,key=28,label=48)",
    )
    p_bl.add_argument(
        "--id-mode",
        choices=["json", "generic", "both"],
        default="json",
        help="Which ID to show. Note: genericJsonId is always shown; --id-mode generic shows only genericJsonId",
    )

    p_blr = sub.add_parser(
        "bundle-list-read",
        help="List bundle bindings and also read current values (adds a 'val' column)",
    )
    p_blr.add_argument("--bundle", required=True, help="Path to bundle.json (decoded)")
    p_blr.add_argument("--filter", default=None, help="Case-insensitive substring filter")
    p_blr.add_argument(
        "--context-filter",
        default=None,
        help="Also require a match in the ancestor context stack (e.g. 'Heizkreis 4', 'Warmwasser')",
    )
    p_blr.add_argument("--controller", default=None, help="Filter by controllerId (e.g. climatix)")
    p_blr.add_argument("--writable", action="store_true", help="Only show bindings with isReadonly=false")
    p_blr.add_argument("--limit", type=int, default=50, help="Max rows to read+print (default: 50)")
    p_blr.add_argument("--no-truncate", action="store_true", help="Do not truncate columns (may wrap)")
    p_blr.add_argument("--device-width", type=int, default=24, help="Device column width (default: 24)")
    p_blr.add_argument("--key-width", type=int, default=18, help="Key column width (default: 18)")
    p_blr.add_argument("--label-width", type=int, default=12, help="Label column width (default: 12)")
    p_blr.add_argument(
        "--value-width",
        type=int,
        default=10,
        help="Value column width (default: 10)",
    )
    p_blr.add_argument(
        "--wide",
        action="store_true",
        help="Shortcut for wider columns (device=48,key=28,label=48,value=14)",
    )
    p_blr.add_argument(
        "--id-mode",
        choices=["json", "generic", "both"],
        default="json",
        help="Which ID to show. Note: genericJsonId is always shown; --id-mode generic shows only genericJsonId",
    )
    p_blr.add_argument(
        "--generic",
        action="store_true",
        help="Read values via generic endpoint (jsongen.html / OA=...) using genericJsonId",
    )
    p_blr.add_argument(
        "--batch-size",
        type=int,
        default=25,
        help="How many IDs to include per HTTP request (default: 25)",
    )

    p_br = sub.add_parser("bundle-read", help="Read a binding selected from bundle.json")
    p_br.add_argument("--bundle", required=True, help="Path to bundle.json")
    p_br.add_argument("--filter", required=True, help="Match against deviceName/bindingKey/label/jsonId")
    p_br.add_argument("--pick", type=int, default=None, help="Which match to use (0-based). If omitted, lists matches.")
    p_br.add_argument("--wide", action="store_true", help="Use wider columns when listing matches")
    p_br.add_argument(
        "--generic",
        action="store_true",
        help="Use generic endpoint (jsongen.html) and genericJsonId (OA=...) instead of JSON.HTML/jsonId",
    )

    p_bw = sub.add_parser("bundle-write", help="Write a binding selected from bundle.json")
    p_bw.add_argument("--bundle", required=True, help="Path to bundle.json")
    p_bw.add_argument("--filter", required=True, help="Match against deviceName/bindingKey/label/jsonId")
    p_bw.add_argument("--value", required=True, help="Value string, e.g. 21.5")
    p_bw.add_argument("--pick", type=int, default=None, help="Which match to use (0-based). If omitted, lists matches.")
    p_bw.add_argument("--allow-readonly", action="store_true", help="Allow writing even if binding says isReadonly=true")
    p_bw.add_argument("--wide", action="store_true", help="Use wider columns when listing matches")
    p_bw.add_argument(
        "--generic",
        action="store_true",
        help="Use generic endpoint (jsongen.html) and genericJsonId (OA=...) instead of JSON.HTML/jsonId",
    )

    args = p.parse_args(argv)

    conn: Optional[Connection] = None
    if args.cmd in {"read", "read-generic", "write", "write-generic", "bundle-read", "bundle-write", "bundle-list-read"}:
        if not args.host:
            raise SystemExit("--host is required for read/write")
        conn = Connection(
            host=args.host,
            port=args.port,
            username=args.user,
            password=args.password,
            pin=args.pin,
            timeout_sec=args.timeout,
        )

    try:
        if args.cmd == "read":
            assert conn is not None
            data = climatix_read(conn, args.ids)
            _print_json(data)
            return 0

        if args.cmd == "read-generic":
            assert conn is not None
            data = climatix_generic_read(conn, args.ids)
            _print_json(data)
            return 0

        if args.cmd == "write":
            assert conn is not None
            data = climatix_write(conn, args.id, args.value)
            _print_json(data)
            return 0

        if args.cmd == "write-generic":
            assert conn is not None
            data = climatix_generic_write(conn, args.id, args.value)
            _print_json(data)
            return 0

        if args.cmd == "encode-id":
            print(encode_json_id(args.object_type, args.object_id, args.member_id))
            return 0

        if args.cmd == "decode-id":
            ot, oid, mid = decode_json_id(args.id)
            print(json.dumps({"objectType": ot, "objectId": oid, "memberId": mid}, indent=2))
            return 0

        if args.cmd == "extract-ids":
            ids = extract_ids(args.path, max_file_kb=args.max_file_kb)
            _print_json(ids)
            return 0

        if args.cmd == "ots-login":
            data = ots_login(args.ots_user, args.ots_pass, timeout_sec=args.timeout)
            _print_json(data)
            return 0

        if args.cmd == "ots-download-bundle":
            login = ots_login(args.ots_user, args.ots_pass, timeout_sec=args.timeout)
            plant_infos = login.get("plantInfos")
            if not isinstance(plant_infos, list) or not plant_infos:
                raise RuntimeError("No plantInfos returned; check credentials")

            config_id = args.config_id
            site_id = args.site_id

            if config_id is None or site_id is None:
                if args.plant_index is None:
                    # Default: first plant
                    idx = 0
                else:
                    idx = int(args.plant_index)
                if idx < 0 or idx >= len(plant_infos):
                    raise ValueError(f"plant-index out of range: {idx}")
                chosen = plant_infos[idx]
                if not isinstance(chosen, dict):
                    raise RuntimeError("Unexpected plantInfos element")
                config_id = config_id or chosen.get("configID")
                site_id = site_id or chosen.get("siteID")

            if not config_id or not site_id:
                raise RuntimeError("Need configID and siteID (use --plant-index or --config-id/--site-id)")

            cfg = ots_getconfig(config_id=config_id, site_id=site_id, stamp=args.stamp, timeout_sec=max(args.timeout, 30.0))
            if not cfg.get("success"):
                raise RuntimeError(f"GetConfig failed: {cfg.get('message')}")
            content = cfg.get("content")
            if not isinstance(content, str) or not content:
                raise RuntimeError("GetConfig returned no content")

            bundle = decode_ots_bundle_content(content)
            out_path = args.out
            os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(bundle, f, indent=2, sort_keys=True, ensure_ascii=False)

            if args.also_extract_ids:
                ids = extract_ids(out_path, max_file_kb=1024 * 1024)
                _print_json(ids)
            else:
                print(out_path)
            return 0

        if args.cmd == "bundle-list":
            rows = list_bundle_bindings(args.bundle)
            if args.controller:
                rows = [r for r in rows if str(r.get("controllerId") or "").lower() == args.controller.lower()]
            if args.writable:
                rows = [r for r in rows if r.get("isReadonly") is False]
            if args.filter:
                rows = [r for r in rows if _match_binding(r, args.filter)]
            if getattr(args, "context_filter", None):
                rows = [r for r in rows if _match_context(r, str(args.context_filter))]
            if args.json:
                _print_json(rows)
            else:
                device_w = int(args.device_width)
                key_w = int(args.key_width)
                label_w = int(args.label_width)
                if args.wide:
                    device_w, key_w, label_w = 48, 28, 48
                _print_bundle_table(
                    rows,
                    limit=max(1, int(args.limit)),
                    device_width=device_w,
                    key_width=key_w,
                    label_width=label_w,
                    truncate=not bool(args.no_truncate),
                    id_mode=str(args.id_mode),
                )
            return 0

        if args.cmd == "bundle-list-read":
            assert conn is not None
            rows = list_bundle_bindings(args.bundle)
            if args.controller:
                rows = [r for r in rows if str(r.get("controllerId") or "").lower() == args.controller.lower()]
            if args.writable:
                rows = [r for r in rows if r.get("isReadonly") is False]
            if args.filter:
                rows = [r for r in rows if _match_binding(r, args.filter)]
            if getattr(args, "context_filter", None):
                rows = [r for r in rows if _match_context(r, str(args.context_filter))]

            # Only query values for read bindings (avoid writeBinding rows).
            rows = [r for r in rows if r.get("bindingType") == "read"]

            # Only read the rows we will print.
            limit = max(1, int(args.limit))
            to_show = rows[:limit]

            use_generic = bool(getattr(args, "generic", False))
            ids: List[str] = []
            for r in to_show:
                v = r.get("genericJsonId" if use_generic else "jsonId")
                if isinstance(v, str) and v:
                    ids.append(v)

            id_to_value = _read_values_by_id(conn, ids, use_generic=use_generic, batch_size=int(args.batch_size))

            device_w = int(args.device_width)
            key_w = int(args.key_width)
            label_w = int(args.label_width)
            value_w = int(args.value_width)
            if args.wide:
                device_w, key_w, label_w, value_w = 48, 28, 48, 14

            id_mode = str(args.id_mode)
            # Convenience: if user chose generic reads and didn't override id-mode, show generic IDs.
            if use_generic and id_mode == "json":
                id_mode = "generic"

            _print_bundle_table_with_values(
                rows,
                id_to_value=id_to_value,
                limit=limit,
                use_generic=use_generic,
                device_width=device_w,
                key_width=key_w,
                label_width=label_w,
                value_width=value_w,
                truncate=not bool(args.no_truncate),
                id_mode=id_mode,
            )
            return 0

        if args.cmd in {"bundle-read", "bundle-write"}:
            assert conn is not None
            rows = list_bundle_bindings(args.bundle)
            rows = [r for r in rows if _match_binding(r, args.filter)]
            if not rows:
                raise RuntimeError("No bindings matched. Try: bundle-list --filter <text>")
            if args.pick is None:
                if getattr(args, "wide", False):
                    _print_bundle_table(
                        rows,
                        limit=min(100, len(rows)),
                        device_width=48,
                        key_width=28,
                        label_width=16,
                        id_mode="generic" if bool(getattr(args, "generic", False)) else "json",
                    )
                else:
                    _print_bundle_table(
                        rows,
                        limit=min(100, len(rows)),
                        id_mode="generic" if bool(getattr(args, "generic", False)) else "json",
                    )
                raise RuntimeError("Multiple matches. Re-run with --pick N")
            idx = int(args.pick)
            if idx < 0 or idx >= len(rows):
                raise ValueError(f"pick out of range: {idx}")
            chosen = rows[idx]
            use_generic = bool(getattr(args, "generic", False))
            if use_generic:
                generic_id = chosen.get("genericJsonId")
                if not isinstance(generic_id, str) or not generic_id:
                    raise RuntimeError("Selected binding has no genericJsonId")
            else:
                json_id = chosen.get("jsonId")
                if not isinstance(json_id, str) or not json_id:
                    raise RuntimeError("Selected binding has no jsonId")

            if args.cmd == "bundle-read":
                data = climatix_generic_read(conn, [generic_id]) if use_generic else climatix_read(conn, [json_id])
                _print_json({"selected": chosen, "response": data})
                return 0

            # bundle-write
            if chosen.get("isReadonly") is True and not args.allow_readonly:
                raise RuntimeError("Selected binding isReadonly=true; pass --allow-readonly to force")
            data = climatix_generic_write(conn, generic_id, args.value) if use_generic else climatix_write(conn, json_id, args.value)
            _print_json({"selected": chosen, "response": data})
            return 0

        raise AssertionError("unhandled cmd")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        print(f"HTTP error: {e.code} {e.reason}\n{body}", file=sys.stderr)
        return 2
    except urllib.error.URLError as e:
        print(f"Network error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
