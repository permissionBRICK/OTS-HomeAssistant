from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from aiohttp import ClientSession, BasicAuth

from .const import DEFAULT_MAX_IDS_PER_READ_REQUEST

_LOGGER = logging.getLogger(__name__)


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
        # Host base (without path)
        return f"http://{self.host}:{self.port}"

    @property
    def candidate_paths(self) -> Tuple[str, ...]:
        # Some firmwares use different casing for this endpoint.
        return ("/jsongen.html", "/JSONgen.html")


class ClimatixGenericApi:
    def __init__(
        self,
        session: ClientSession,
        conn: ClimatixGenericConnection,
        *,
        max_ids_per_read_request: int = DEFAULT_MAX_IDS_PER_READ_REQUEST,
    ) -> None:
        self._session = session
        self._conn = conn
        try:
            n = int(max_ids_per_read_request)
        except Exception:
            n = DEFAULT_MAX_IDS_PER_READ_REQUEST
        if n < 1:
            n = 1
        if n > 200:
            n = 200
        self._max_ids_per_read_request = n

    async def _get_json(
        self,
        params: List[Tuple[str, str]],
        *,
        on_http_request: Callable[[], None] | None = None,
    ) -> Dict[str, Any]:
        auth = BasicAuth(self._conn.username, self._conn.password)
        last_exc: Optional[BaseException] = None
        last_error_payload: Optional[Dict[str, Any]] = None

        for path in self._conn.candidate_paths:
            url = f"{self._conn.base_url}{path}"
            try:
                if on_http_request is not None:
                    on_http_request()
                async with self._session.get(
                    url,
                    params=params,
                    auth=auth,
                    timeout=self._conn.timeout_sec,
                ) as resp:
                    resp.raise_for_status()

                    # Some controllers return JSON with non-UTF8 encoding (e.g. ISO-8859-1 for umlauts).
                    # aiohttp's resp.json() assumes UTF-8 for application/json and can crash.
                    raw = await resp.read()
                    try:
                        text = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        text = raw.decode("latin-1")
                    payload = json.loads(text)

                # If the controller returns an error without values, try the next candidate path.
                if isinstance(payload, dict) and "Error" in payload and "values" not in payload:
                    err = payload.get("Error")
                    if err not in (0, None):
                        last_error_payload = payload
                        _LOGGER.debug("Controller error via %s: %s", path, payload)
                        continue

                if isinstance(payload, dict):
                    _LOGGER.debug("Controller response via %s (keys=%s)", path, sorted(payload.keys()))
                    return payload

                # Unexpected non-dict JSON; try next candidate path.
                last_exc = RuntimeError(f"Unexpected JSON type from controller via {path}: {type(payload)}")
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue

        # If we got controller error payloads, return the last one so the caller can raise meaningfully.
        if last_error_payload is not None:
            return last_error_payload

        if last_exc is not None:
            raise last_exc
        return {}

    async def read(
        self,
        ids: Iterable[str],
        *,
        on_http_request: Callable[[], None] | None = None,
    ) -> Dict[str, Any]:
        oa_list = [x for x in ids if x]
        if not oa_list:
            return {"values": {}}

        merged_values: Dict[str, Any] = {}
        # Chunk requests to avoid too-long URLs (which can lead to auth/PIN being dropped).
        for i in range(0, len(oa_list), self._max_ids_per_read_request):
            chunk = oa_list[i : i + self._max_ids_per_read_request]
            params: List[Tuple[str, str]] = [("FN", "Read")]
            for one in chunk:
                params.append(("OA", one))
            if self._conn.pin:
                params.append(("PIN", self._conn.pin))
            params.append(("LNG", "-1"))
            params.append(("US", "1"))

            payload = await self._get_json(params, on_http_request=on_http_request)

            values = payload.get("values") if isinstance(payload, dict) else None
            if isinstance(values, dict):
                for k, v in values.items():
                    merged_values[str(k)] = v
            else:
                # Treat controller errors as failures so we don't silently filter everything during probing.
                if isinstance(payload, dict) and payload.get("Error") not in (0, None):
                    raise RuntimeError(f"Controller error during read: {payload}")

        return {"values": merged_values}

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

        payload = await self._get_json(params)
        if isinstance(payload, dict) and payload.get("Error") not in (0, None) and "values" not in payload:
            raise RuntimeError(f"Controller error during write: {payload}")
        return payload


class ClimatixGenericApiWriteHook:
    """Wrapper for ClimatixGenericApi that runs a hook after successful writes."""

    def __init__(
        self,
        inner: ClimatixGenericApi,
        *,
        on_write: Callable[[], Awaitable[None]],
    ) -> None:
        self._inner = inner
        self._on_write = on_write

    async def read(
        self,
        ids: Iterable[str],
        *,
        on_http_request: Callable[[], None] | None = None,
    ) -> Dict[str, Any]:
        return await self._inner.read(ids, on_http_request=on_http_request)

    async def write(self, generic_id: str, value: Any) -> Dict[str, Any]:
        resp = await self._inner.write(generic_id, value)
        await self._on_write()
        return resp


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
