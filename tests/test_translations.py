"""Spec v4 F: German and English UI strings, kept in sync with strings.json."""

from __future__ import annotations

import json
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "custom_components" / "ochsner_local_ots"


def _keys(d, prefix=""):
    out = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _keys(v, path)
        else:
            out.add(path)
    return out


def test_translation_files_are_in_sync():
    strings = json.loads((PKG / "strings.json").read_text(encoding="utf-8"))
    en = json.loads((PKG / "translations" / "en.json").read_text(encoding="utf-8"))
    de = json.loads((PKG / "translations" / "de.json").read_text(encoding="utf-8"))
    assert _keys(strings) == _keys(en) == _keys(de)


def test_entity_translations_cover_integration_provided_names():
    for lang in ("en", "de"):
        data = json.loads((PKG / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        sensors = data["entity"]["sensor"]
        for key in ("flash_writes", "read_requests", "read_rate_5m"):
            assert sensors[key]["name"].strip(), f"{lang}: missing entity name for {key}"
