"""Minimal client for the Home Assistant WebSocket API.

Inside the app it talks to ws://supervisor/core/websocket and authenticates
with SUPERVISOR_TOKEN (needs ``homeassistant_api: true`` in config.yaml).
Long-term statistics are only available over the WebSocket API, not REST.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

# Units we ask HA to convert into, per HA unit class.
STATISTIC_UNITS = {"energy": "kWh", "volume": "m³"}

# HA picks the converter from the statistic's unit when its unit_class is
# missing; these are the units of the two converters we use.
_UNIT_CLASS_BY_UNIT = {
    **dict.fromkeys(("Wh", "kWh", "MWh", "GWh", "TWh", "J", "kJ", "MJ", "GJ", "cal", "kcal", "Mcal", "Gcal"), "energy"),
    **dict.fromkeys(("L", "mL", "m³", "ft³", "CCF", "MCF", "gal", "fl. oz."), "volume"),
}

# How far before a period HA must already have data for the change over that
# period to be complete (HA counts from 0 if the period starts before its data).
COVERAGE_WINDOW = timedelta(hours=24)


class HAError(Exception):
    """Home Assistant could not be reached or rejected a command."""


@dataclass(frozen=True)
class PeriodQuery:
    statistic_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class PeriodChange:
    statistic_id: str
    change: float | None
    unit: str | None
    # False if HA's data starts inside the period, so `change` is too small.
    covered: bool


class HomeAssistant:
    def __init__(self, ws_url: str, token: str | None, timeout: float = 20.0) -> None:
        self.ws_url = ws_url
        self.token = token
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token)

    async def _call(self, commands: list[dict[str, Any]]) -> list[Any]:
        """Send commands over one authenticated connection and return their results in order."""
        if not self.token:
            raise HAError("Keine Verbindung zu Home Assistant konfiguriert.")
        try:
            async with asyncio.timeout(self.timeout):
                return await self._exchange(commands)
        except TimeoutError as exc:
            raise HAError("Home Assistant antwortet nicht (Zeitüberschreitung).") from exc
        except (OSError, WebSocketException, ValueError, KeyError) as exc:
            raise HAError(f"Home Assistant nicht erreichbar: {exc}") from exc

    async def _exchange(self, commands: list[dict[str, Any]]) -> list[Any]:
        async with connect(self.ws_url, open_timeout=10, max_size=16 * 1024 * 1024) as ws:
            greeting = json.loads(await ws.recv())
            if greeting.get("type") != "auth_required":
                raise HAError(f"Unerwartete Antwort von Home Assistant: {greeting.get('type')}")
            await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
            auth = json.loads(await ws.recv())
            if auth.get("type") != "auth_ok":
                raise HAError("Anmeldung bei Home Assistant fehlgeschlagen.")

            results = []
            for message_id, command in enumerate(commands, start=1):
                await ws.send(json.dumps({"id": message_id, **command}))
                while True:
                    reply = json.loads(await ws.recv())
                    if reply.get("id") == message_id and reply.get("type") == "result":
                        break
                if not reply.get("success"):
                    error = reply.get("error") or {}
                    raise HAError(error.get("message", "Home Assistant meldet einen Fehler."))
                results.append(reply["result"])
            return results

    async def list_sum_statistics(self) -> list[dict[str, Any]]:
        """Statistics with a running sum, e.g. energy, gas and water meters."""
        [result] = await self._call([{"type": "recorder/list_statistic_ids", "statistic_type": "sum"}])
        return sorted(result, key=lambda item: item["statistic_id"])

    async def period_changes(self, queries: list[PeriodQuery]) -> list[PeriodChange]:
        """How much each statistic grew between start and end (one connection for all)."""
        if not queries:
            return []
        statistic_ids = sorted({query.statistic_id for query in queries})
        commands: list[dict[str, Any]] = [
            {"type": "recorder/get_statistics_metadata", "statistic_ids": statistic_ids}
        ]
        for query in queries:
            commands.append(
                {
                    "type": "recorder/statistic_during_period",
                    "statistic_id": query.statistic_id,
                    "fixed_period": {
                        "start_time": query.start.isoformat(),
                        "end_time": query.end.isoformat(),
                    },
                    "types": ["change"],
                    "units": STATISTIC_UNITS,
                }
            )
            commands.append(
                {
                    "type": "recorder/statistics_during_period",
                    "statistic_ids": [query.statistic_id],
                    "period": "hour",
                    "start_time": (query.start - COVERAGE_WINDOW).isoformat(),
                    "end_time": query.start.isoformat(),
                    "types": ["sum"],
                }
            )
        metadata, *results = await self._call(commands)
        units = {item["statistic_id"]: _display_unit(item) for item in metadata}
        return [
            PeriodChange(
                statistic_id=query.statistic_id,
                change=change.get("change"),
                unit=units.get(query.statistic_id),
                covered=bool(coverage.get(query.statistic_id)),
            )
            for query, change, coverage in zip(queries, results[0::2], results[1::2])
        ]


def _display_unit(metadata: dict[str, Any]) -> str | None:
    """The unit HA reports after applying STATISTIC_UNITS (mirrors HA's converter choice)."""
    unit = metadata.get("statistics_unit_of_measurement")
    unit_class = metadata.get("unit_class")
    if unit_class not in STATISTIC_UNITS or _UNIT_CLASS_BY_UNIT.get(unit) != unit_class:
        unit_class = _UNIT_CLASS_BY_UNIT.get(unit)
    return STATISTIC_UNITS.get(unit_class, unit)
