from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from aiohttp import ClientSession, BasicAuth


@dataclass(frozen=True)
class ClimatixGenericConnection:
    host: str
    port: int
    username: str
    password: str
    pin: str
    timeout_sec: float = 10.0

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/jsongen.html"


class ClimatixGenericApi:
    def __init__(self, session: ClientSession, conn: ClimatixGenericConnection) -> None:
        self._session = session
        self._conn = conn

    async def read(self, ids: Iterable[str]) -> Dict[str, Any]:
        oa_list = [x for x in ids if x]
        if not oa_list:
            return {"values": {}}

        params: List[Tuple[str, str]] = [("FN", "Read")]
        for one in oa_list:
            params.append(("OA", one))
        if self._conn.pin:
            params.append(("PIN", self._conn.pin))
        params.append(("LNG", "-1"))
        params.append(("US", "1"))

        auth = BasicAuth(self._conn.username, self._conn.password)
        async with self._session.get(self._conn.base_url, params=params, auth=auth, timeout=self._conn.timeout_sec) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def write(self, generic_id: str, value: Any) -> Dict[str, Any]:
        if not generic_id:
            raise ValueError("generic_id is required")

        # Keep formatting stable for enums: 1.0 -> "1".
        if isinstance(value, float) and value.is_integer():
            value_str = str(int(value))
        else:
            value_str = str(value)

        params: List[Tuple[str, str]] = [("FN", "Write"), ("OA", f"{generic_id};{value_str}")]
        if self._conn.pin:
            params.append(("PIN", self._conn.pin))
        params.append(("LNG", "-1"))
        params.append(("US", "1"))

        auth = BasicAuth(self._conn.username, self._conn.password)
        async with self._session.get(self._conn.base_url, params=params, auth=auth, timeout=self._conn.timeout_sec) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)


def extract_first_value(payload: Dict[str, Any], generic_id: str) -> Optional[Any]:
    """Return the raw value for one OA id.

    Controllers can return either:
    - {"values": {"<id>": [x, x]}}  (common)
    - {"values": {"<id>": x}}       (also seen)
    """
    values = payload.get("values")
    if not isinstance(values, dict):
        return None
    raw = values.get(generic_id)
    if raw is None:
        return None

    if isinstance(raw, list):
        if not raw:
            return None
        return raw[0]

    # Some firmwares return a scalar directly.
    return raw


def extract_first_numeric_value(payload: Dict[str, Any], generic_id: str) -> Optional[float]:
    """From {"values": {"<id>": [x,x]}} return x as float if possible."""
    v = extract_first_value(payload, generic_id)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
