import asyncio
from datetime import datetime, timezone

import pytest

from app.ha import HAError, HomeAssistant, PeriodQuery, _display_unit
from tests.helpers import NO_REPLY, fake_home_assistant

QUERY = PeriodQuery(
    "sensor.strom",
    datetime(2025, 1, 1, tzinfo=timezone.utc),
    datetime(2026, 1, 1, tzinfo=timezone.utc),
)


def test_a_silent_home_assistant_times_out():
    with fake_home_assistant(lambda command: NO_REPLY) as (url, _):
        ha = HomeAssistant(url, "secret", timeout=0.5)
        with pytest.raises(HAError, match="Zeitüberschreitung"):
            asyncio.run(ha.list_sum_statistics())


def test_unreachable_home_assistant_raises_ha_error():
    ha = HomeAssistant("ws://127.0.0.1:9/", "secret", timeout=2)
    with pytest.raises(HAError):
        asyncio.run(ha.list_sum_statistics())


@pytest.mark.parametrize(("history", "covered"), [([{"sum": 1.0}], True), ([], False)])
def test_period_change_reports_whether_ha_covers_the_period(history, covered):
    def respond(command):
        match command["type"]:
            case "recorder/get_statistics_metadata":
                return [{"statistic_id": "sensor.strom", "unit_class": "energy", "statistics_unit_of_measurement": "kWh"}]
            case "recorder/statistic_during_period":
                return {"change": 1500.0}
            case "recorder/statistics_during_period":
                return {"sensor.strom": history} if history else {}

    with fake_home_assistant(respond) as (url, _):
        [change] = asyncio.run(HomeAssistant(url, "secret").period_changes([QUERY]))
    assert (change.change, change.unit, change.covered) == (1500.0, "kWh", covered)


@pytest.mark.parametrize(
    ("metadata", "unit"),
    [
        ({"unit_class": "energy", "statistics_unit_of_measurement": "Wh"}, "kWh"),
        ({"unit_class": None, "statistics_unit_of_measurement": "Wh"}, "kWh"),
        ({"unit_class": "volume", "statistics_unit_of_measurement": "L"}, "m³"),
        ({"unit_class": None, "statistics_unit_of_measurement": "°C"}, "°C"),
    ],
)
def test_display_unit_follows_home_assistants_conversion(metadata, unit):
    assert _display_unit(metadata) == unit
