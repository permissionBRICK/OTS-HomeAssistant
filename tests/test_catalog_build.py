"""Catalog builder: determinism, provenance, ground-truth invariants.

These tests need the research inputs under apk_files/ (not shipped in git);
they are skipped when absent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = REPO_ROOT / "apk_files" / "reports" / "catalog_model.json"
NAMES = REPO_ROOT / "apk_files" / "reports" / "catalog_names.json"
REFERENCE = REPO_ROOT / "apk_files" / "ha_config" / "core.config_entries"

pytestmark = pytest.mark.skipif(
    not (MODEL.exists() and NAMES.exists() and REFERENCE.exists()),
    reason="APK research inputs not present",
)


@pytest.fixture(scope="module")
def builder(lib):
    spec = importlib.util.spec_from_file_location(
        "build_discovery_catalog", REPO_ROOT / "tools" / "build_discovery_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_discovery_catalog"] = mod
    spec.loader.exec_module(mod)
    return mod


def build(builder):
    return builder.build_catalog(model_path=MODEL, names_path=NAMES, reference_path=REFERENCE)


def test_builder_is_deterministic(builder):
    a = build(builder)
    b = build(builder)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_builder_counts_and_provenance(builder):
    cat = build(builder)
    stats = cat["stats"]
    # All 1,217 validated APK OAs, no false positives.
    assert stats["apk_points"] == 1217
    ids = {p["id"] for p in cat["points"]}
    for fp in ("intEncoding=", "getExitAnim=", "surfaceTint=", "stopTimeout=", "onSecondary="):
        assert fp not in ids
    # 672 schedule points are marked (and excluded from the default sweep).
    assert stats["schedule_points"] >= 672
    # Reference seed: 387 primary/read ids.
    assert stats["reference_points"] == 387
    # Writability only ever comes from reference/bundle evidence, never APK-only.
    for p in cat["points"]:
        if p.get("write_id"):
            assert "reference" in p["sources"] or "hc_template" in p["sources"]


def test_shipped_catalog_matches_builder_output(builder, catalog_mod):
    built = build(builder)
    shipped = json.loads(catalog_mod.catalog_path().read_text(encoding="utf-8"))
    assert shipped["catalog_version"] == built["catalog_version"], (
        "packaged discovery_catalog.json is stale; re-run tools/build_discovery_catalog.py"
    )


def test_destructive_points_created_writable_but_disabled(builder):
    """Owner decision: destructive/one-shot/service points are STILL writable
    entities, just disabled by default — never dropped, never read-only."""

    cat = build(builder)
    hits = [p for p in cat["points"] if "relaistest" in p["name"].lower() or p["name"] == "Geräte-Reset"]
    assert hits
    for p in hits:
        if p.get("write_id"):
            assert p["enabled_default"] is False, p["name"]
            assert p["platform"] in {"number", "select", "switch"}, p["name"]


def test_privacy_points_are_disabled_diagnostics(builder):
    cat = build(builder)
    by_name = {}
    for p in cat["points"]:
        by_name.setdefault(p["name"], p)
    for name in ("Kunde", "IP-Adresse", "Gateway", "MAC"):
        p = by_name.get(name)
        assert p is not None, f"expected privacy point {name} in catalog"
        assert p.get("diagnostic") is True
        assert p.get("enabled_default") is False
