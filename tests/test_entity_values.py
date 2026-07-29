"""Value-adapter tests: discovery-shaped entity configs drive the REAL HA
entity classes, proving scan values become the exposed entity state (sensor
value_map, binary bool, number float, select label, text string, switch
on/off) and that the fallback unique_id rule is applied.

Needs Home Assistant importable (project venv); skipped otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ha = pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.ochsner_local_ots.binary_sensor import ClimatixGenericBinarySensor  # noqa: E402
from custom_components.ochsner_local_ots.number import ClimatixGenericNumber  # noqa: E402
from custom_components.ochsner_local_ots.select import ClimatixGenericSelect  # noqa: E402
from custom_components.ochsner_local_ots.sensor import ClimatixGenericSensor  # noqa: E402
from custom_components.ochsner_local_ots.switch import ClimatixGenericSwitch  # noqa: E402
from custom_components.ochsner_local_ots.text import ClimatixGenericText  # noqa: E402

HOST = "192.0.2.1"


class FakeCoordinator:
    """Just enough of DataUpdateCoordinator for value-property testing."""

    def __init__(self, values):
        self.data = {"values": values}

    def async_add_listener(self, *_a, **_k):
        return lambda: None


def kwargs(values):
    return {"host": HOST, "base_url": f"http://{HOST}"}


def test_sensor_value_map_and_unique_id():
    cfg = {"name": "Status Zusatzheizung", "id": "CyM4OMdZAAE=", "value_map": {"0": "Standby", "1": "Heizbetrieb"}}
    coord = FakeCoordinator({"CyM4OMdZAAE=": [1.0, 1.0]})
    ent = ClimatixGenericSensor(coord, host=HOST, base_url=f"http://{HOST}", cfg=cfg)
    assert ent.native_value == "Heizbetrieb"
    assert ent.unique_id == f"{HOST}:sensor:CyM4OMdZAAE"


def test_sensor_diagnostic_disabled_flags():
    from homeassistant.helpers.entity import EntityCategory

    cfg = {"name": "n1_Weak", "id": "CyM4OMdZAAE=", "enabled_default": False, "diagnostic": True}
    ent = ClimatixGenericSensor(FakeCoordinator({}), host=HOST, base_url="u", cfg=cfg)
    assert ent.entity_registry_enabled_default is False
    assert ent.entity_category is EntityCategory.DIAGNOSTIC


def test_binary_sensor_bool():
    cfg = {"name": "Handabtauung", "id": "AiN95Ir8AAE="}
    ent = ClimatixGenericBinarySensor(
        FakeCoordinator({"AiN95Ir8AAE=": [1.0, 1.0]}), host=HOST, base_url="u", cfg=cfg
    )
    assert ent.is_on is True


def test_number_float_value():
    cfg = {"name": "Setpoint", "read_id": "ACJz7Y58AAE=", "write_id": "ACJz7Y58AAE=", "min": 10.0, "max": 30.0, "step": 0.5, "unit": "°C"}
    ent = ClimatixGenericNumber(
        FakeCoordinator({"ACJz7Y58AAE=": [21.5, 21.5]}), api=None, host=HOST, base_url="u", cfg=cfg
    )
    assert ent.native_value == 21.5
    assert ent.unique_id == f"{HOST}:number:ACJz7Y58AAE"


def test_select_label_from_value():
    cfg = {
        "name": "Betriebswahl",
        "read_id": "AiIQvY58IgE=",
        "write_id": "AiIQvY58IgE=",
        "options": {"Aus": 1, "Komfort": 0, "Normalbetrieb": 3},
    }
    ent = ClimatixGenericSelect(
        FakeCoordinator({"AiIQvY58IgE=": [3.0, 3.0]}), api=None, host=HOST, base_url="u", cfg=cfg
    )
    assert ent.current_option == "Normalbetrieb"


def test_text_string_value():
    cfg = {"name": "Name Heizkreis 1", "read_id": "BCNL7o58AAE=", "write_id": "BCNL7o58AAE="}
    ent = ClimatixGenericText(
        FakeCoordinator({"BCNL7o58AAE=": ["Fußboden", "Fußboden"]}), api=None, host=HOST, base_url="u", cfg=cfg
    )
    assert ent.native_value == "Fußboden"


def test_switch_on_off_state():
    cfg = {"name": "Boost", "read_id": "AiOzyHXZAAE=", "write_id": "AiOzyHXZAAE=", "on_value": 1, "off_value": 0}
    on = ClimatixGenericSwitch(FakeCoordinator({"AiOzyHXZAAE=": [1.0, 1.0]}), api=None, host=HOST, base_url="u", cfg=cfg)
    off = ClimatixGenericSwitch(FakeCoordinator({"AiOzyHXZAAE=": [0.0, 0.0]}), api=None, host=HOST, base_url="u", cfg=cfg)
    assert on.is_on is True
    assert off.is_on is False
