"""Test helpers: load the integration's HA-free modules without importing the
package __init__ (which requires Home Assistant)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "custom_components" / "ochsner_local_ots"

_PKG_NAME = "ots_local_lib"


def _load_lib() -> types.ModuleType:
    pkg = sys.modules.get(_PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules[_PKG_NAME] = pkg

    for name in ("const", "catalog", "api", "discovery", "discovery_entities"):
        full = f"{_PKG_NAME}.{name}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, PKG_DIR / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return pkg


@pytest.fixture(scope="session")
def lib() -> types.ModuleType:
    return _load_lib()


@pytest.fixture(scope="session")
def catalog_mod(lib):
    return sys.modules[f"{_PKG_NAME}.catalog"]


@pytest.fixture(scope="session")
def discovery_mod(lib):
    return sys.modules[f"{_PKG_NAME}.discovery"]


@pytest.fixture(scope="session")
def entities_mod(lib):
    return sys.modules[f"{_PKG_NAME}.discovery_entities"]
