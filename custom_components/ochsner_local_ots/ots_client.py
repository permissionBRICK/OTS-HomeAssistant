from __future__ import annotations

import logging
import base64
import gzip
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

OTS_BASE_URL = "https://ots-services.ochsner.com/app"


@dataclass(frozen=True)
class OtsPlantInfo:
    """Best-effort normalized plant info returned by OTS /login."""

    config_id: str
    site_id: str
    name: str
    raw: Dict[str, Any]


def _plant_display_name(p: Dict[str, Any]) -> str:
    # OTS responses vary a bit across accounts/regions. Prefer the most human field.
    for k in (
        "name",
        "plantName",
        "siteName",
        "displayName",
        "systemName",
        "title",
    ):
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    cfg = p.get("configID") or p.get("configId")
    site = p.get("siteID") or p.get("siteId")
    if cfg and site:
        return f"{cfg} / {site}"
    if cfg:
        return str(cfg)
    if site:
        return str(site)
    return "Plant"


def _get_plant_ids(p: Dict[str, Any]) -> Optional[tuple[str, str]]:
    cfg = p.get("configID") or p.get("configId")
    site = p.get("siteID") or p.get("siteId")
    if not isinstance(cfg, str) or not cfg.strip():
        return None
    if not isinstance(site, str) or not site.strip():
        return None
    return cfg.strip(), site.strip()


async def ots_login(hass: HomeAssistant, *, username: str, password: str) -> List[OtsPlantInfo]:
    """Login to OTS and return available plants.

    NOTE: Credentials are not persisted anywhere; caller must keep them only in-memory.
    """

    session = async_get_clientsession(hass)
    url = f"{OTS_BASE_URL}/login"

    async with session.get(url, headers={"userName": username, "userPass": password}) as resp:
        text = await resp.text()
        if resp.status != 200:
            _LOGGER.debug("OTS /login failed: %s %s", resp.status, text)
            raise RuntimeError(f"OTS login failed (HTTP {resp.status})")
        data = await resp.json(content_type=None)

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected OTS login response")

    plant_infos = data.get("plantInfos")
    if not isinstance(plant_infos, list) or not plant_infos:
        raise RuntimeError("No plantInfos returned")

    out: List[OtsPlantInfo] = []
    for p in plant_infos:
        if not isinstance(p, dict):
            continue
        ids = _get_plant_ids(p)
        if not ids:
            continue
        cfg, site = ids
        out.append(
            OtsPlantInfo(
                config_id=cfg,
                site_id=site,
                name=_plant_display_name(p),
                raw=p,
            )
        )

    if not out:
        raise RuntimeError("No usable plants returned")
    return out


def _format_config_id_for_server(config_id: str) -> str:
    # Accept either "C-ABCD-EF01" or raw; normalize and format into "C-XXXX-YYYY-...".
    s = (config_id or "").strip()
    if not s:
        raise ValueError("config id is empty")
    if s.upper().startswith("C-"):
        s = s[2:].replace("-", "")
    raw = s.lower().replace("-", "")
    raw_u = raw.upper()
    if len(raw_u) % 4 != 0:
        return "C-" + raw_u
    parts = [raw_u[i : i + 4] for i in range(0, len(raw_u), 4)]
    return "C-" + "-".join(parts)


async def ots_getconfig(
    hass: HomeAssistant,
    *,
    config_id: str,
    site_id: str,
    stamp: int = 0,
) -> Dict[str, Any]:
    """Fetch plant config (bundle wrapper) from OTS.

    Returns the decoded JSON response from /getconfig, which contains `success` and `content`.
    """

    session = async_get_clientsession(hass)
    cfg_fmt = _format_config_id_for_server(config_id)
    url = f"{OTS_BASE_URL}/getconfig"
    params = {"configID": cfg_fmt, "siteID": site_id, "stamp": str(int(stamp))}

    async with session.get(url, params=params) as resp:
        text = await resp.text()
        if resp.status != 200:
            _LOGGER.debug("OTS /getconfig failed: %s %s", resp.status, text)
            raise RuntimeError(f"OTS getconfig failed (HTTP {resp.status})")
        data = await resp.json(content_type=None)

    if not isinstance(data, dict):
        raise RuntimeError("Unexpected OTS getconfig response")
    return data


def decode_ots_bundle_content(content_b64_gzip: str) -> Dict[str, Any]:
    """Decode the bundle `content` field from /getconfig (base64+gzip JSON)."""

    raw = base64.b64decode(content_b64_gzip.encode("ascii"))
    decoded = gzip.decompress(raw)
    text = decoded.decode("utf-8", errors="strict")
    obj = json.loads(text)
    if not isinstance(obj, dict):
        raise ValueError("Decoded bundle is not a JSON object")
    return obj
