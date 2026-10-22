"""Frontend serving, the sidebar panel and the upgrade notification.

The card/panel JavaScript ships inside the integration (``frontend/``). It
is served under a versioned URL (manifest version + content hash) so every
module, including relative imports, is re-fetched after an update, and is
added to every dashboard so the cards are always available. The sidebar
panel is registered while at least one loaded entry has it enabled.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_COLOR_SCHEME,
    CONF_SIDEBAR_PANEL,
    COLOR_SCHEME_HA,
    DEFAULT_SIDEBAR_PANEL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PKG_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = PKG_DIR / "frontend"
FRONTEND_ENTRY = "ochsner-heatpump.js"
URL_BASE = f"/{DOMAIN}/frontend"

PANEL_URL_PATH = "ochsner-heatpump"
PANEL_COMPONENT = "ochsner-heatpump-panel"
PANEL_ICON = "mdi:heat-pump"

NOTIFICATION_ID = f"{DOMAIN}_dashboard_added"

_DATA_FRONTEND = "_frontend"
_DATA_PANEL = "_panel_registered"


def manifest_version() -> str:
    try:
        return str(json.loads((PKG_DIR / "manifest.json").read_text(encoding="utf-8")).get("version") or "0")
    except (OSError, ValueError):
        return "0"


def frontend_version(frontend_dir: Path | None = None) -> str:
    """Manifest version + short hash over every shipped frontend file.

    Blocking (file reads): run in the executor.
    """
    root = frontend_dir or FRONTEND_DIR
    digest = hashlib.sha256()
    if root.is_dir():
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return f"{manifest_version()}-{digest.hexdigest()[:8]}"


def frontend_base_url(hass: HomeAssistant) -> str | None:
    info = hass.data.get(DOMAIN, {}).get(_DATA_FRONTEND)
    return info["base"] if info else None


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve the frontend and load it on every dashboard (once per run)."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_DATA_FRONTEND):
        return
    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig

    directory = FRONTEND_DIR
    version = await hass.async_add_executor_job(frontend_version, directory)
    base = f"{URL_BASE}/{version}"
    if not (directory / FRONTEND_ENTRY).is_file():
        _LOGGER.warning("Heat pump dashboard files are missing (%s)", directory)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(base, str(directory), cache_headers=True)]
    )
    frontend.add_extra_js_url(hass, f"{base}/{FRONTEND_ENTRY}")
    data[_DATA_FRONTEND] = {"base": base, "version": version}


def panel_title(hass: HomeAssistant) -> str:
    language = str(getattr(hass.config, "language", "") or "").lower()
    return "Wärmepumpe" if language.startswith("de") else "Heat pump"


def entry_wants_panel(entry: Any) -> bool:
    return bool((entry.options or {}).get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL))


def _loaded_entries(hass: HomeAssistant) -> list:
    data = hass.data.get(DOMAIN, {})
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(data.get(entry.entry_id), dict) and "controllers" in data[entry.entry_id]
    ]


def color_scheme(hass: HomeAssistant) -> str:
    entries = _loaded_entries(hass) or list(hass.config_entries.async_entries(DOMAIN))
    if entries:
        return str((entries[0].options or {}).get(CONF_COLOR_SCHEME) or COLOR_SCHEME_HA)
    return COLOR_SCHEME_HA


async def async_sync_panel(hass: HomeAssistant) -> None:
    """Register the sidebar panel iff any loaded entry wants it (idempotent)."""
    data = hass.data.setdefault(DOMAIN, {})
    base = frontend_base_url(hass)
    want = base is not None and any(entry_wants_panel(e) for e in _loaded_entries(hass))
    registered = bool(data.get(_DATA_PANEL))
    if want == registered:
        return
    from homeassistant.components import frontend

    if want:
        from homeassistant.components import panel_custom

        data[_DATA_PANEL] = True
        try:
            await panel_custom.async_register_panel(
                hass,
                frontend_url_path=PANEL_URL_PATH,
                webcomponent_name=PANEL_COMPONENT,
                module_url=f"{base}/{FRONTEND_ENTRY}",
                sidebar_title=panel_title(hass),
                sidebar_icon=PANEL_ICON,
                require_admin=False,
                config={},
            )
        except Exception:
            data[_DATA_PANEL] = False
            raise
    else:
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
        data[_DATA_PANEL] = False


_NOTIFICATION_TEXT = {
    "de": (
        "Neue Wärmepumpen-Ansicht",
        "Ochsner Local OTS hat in der Seitenleiste die neue Ansicht **Wärmepumpe** "
        "hinzugefügt (Anlagenschema, Bedienung und Heizkurve).\n\n"
        "Ausschalten: **Einstellungen → Geräte & Dienste → Ochsner Local OTS → "
        "Konfigurieren** und „Wärmepumpen-Ansicht in der Seitenleiste“ deaktivieren.\n\n"
        "Dieselben Ansichten gibt es auch als Karten: Dashboard bearbeiten → "
        "Karte hinzufügen → nach „Ochsner“ suchen.",
    ),
    "en": (
        "New heat pump view",
        "Ochsner Local OTS added the new **Heat pump** view to the sidebar "
        "(plant schematic, controls and heating curve).\n\n"
        "To switch it off: **Settings → Devices & services → Ochsner Local OTS → "
        "Configure** and disable \"Show the heat pump view in the sidebar\".\n\n"
        "The same views are available as cards: edit a dashboard → Add card → "
        "search for \"Ochsner\".",
    ),
}


def async_notify_dashboard_added(hass: HomeAssistant) -> None:
    """One persistent notification (fixed id: repeated calls replace it)."""
    from homeassistant.components import persistent_notification

    language = str(getattr(hass.config, "language", "") or "").lower()
    title, message = _NOTIFICATION_TEXT["de" if language.startswith("de") else "en"]
    persistent_notification.async_create(
        hass, message, title=title, notification_id=NOTIFICATION_ID
    )
