"""Test helpers: load the integration's HA-free modules without importing the
package __init__ (which requires Home Assistant)."""

from __future__ import annotations

import gc
import importlib.util
import logging
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
PKG_DIR = REPO_ROOT / "custom_components" / "ochsner_local_ots"

_PKG_NAME = "ots_local_lib"


def _load_lib() -> types.ModuleType:
    pkg = sys.modules.get(_PKG_NAME)
    if pkg is None:
        pkg = types.ModuleType(_PKG_NAME)
        pkg.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules[_PKG_NAME] = pkg

    for name in ("const", "catalog", "api", "network_discovery", "connection", "discovery", "discovery_entities", "plant"):
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


@pytest.fixture(scope="session")
def plant_mod(lib):
    return sys.modules[f"{_PKG_NAME}.plant"]


SNAPSHOT_PATH = Path(__file__).resolve().parent / "fixtures" / "airhawk518_snapshot.json"


def load_snapshot() -> dict:
    """Sanitized read-only snapshot of a real AirHawk 518 (values + states)."""
    import json

    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


class FakeController:
    """In-memory controller answering like jsongen from a snapshot.

    ``values[id]`` is what the controller returns; ids that are absent answer
    ``states: 5``. Writes are recorded in order and change the stored value,
    so a re-read after a write sees the new value. Never touches a network.
    """

    def __init__(self, snapshot: dict | None = None) -> None:
        snap = snapshot if snapshot is not None else load_snapshot()
        self.values = {k: v for k, v in snap["values"].items()}
        self.writes: list = []
        self.reads: list = []

    def set(self, oid: str, value) -> None:
        self.values[oid] = [value, value]

    def drop(self, oid: str) -> None:
        self.values.pop(oid, None)

    async def read(self, ids, *, on_http_request=None):
        ids = [i for i in ids if i]
        self.reads.append(list(ids))
        return {
            "values": {i: self.values[i] for i in ids if i in self.values},
            "states": {i: 5 for i in ids if i not in self.values},
        }

    async def read_raw(self, ids, *, on_http_request=None):
        ids = [i for i in ids if i]
        return {
            "values": {i: self.values[i] for i in ids if i in self.values},
            "states": {i: 5 for i in ids if i not in self.values},
        }

    async def write(self, oid, value):
        self.writes.append((oid, value))
        self.set(oid, value)
        return {"values": {}}


@pytest.fixture
def snapshot() -> dict:
    return load_snapshot()


@pytest.fixture
def fake_controller():
    return FakeController


class _AsyncioErrors(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.messages: list = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.fixture(autouse=True)
def no_unretrieved_task_exceptions(request):
    """Fail a test whose background tasks die with an exception nobody sees
    (asyncio logs "Task exception was never retrieved" when they are freed)."""
    handler = _AsyncioErrors()
    logger = logging.getLogger("asyncio")
    logger.addHandler(handler)
    try:
        yield
        if "hass" in request.fixturenames:
            gc.collect()
    finally:
        logger.removeHandler(handler)
    leaked = [m for m in handler.messages if "never retrieved" in m]
    assert not leaked, leaked
