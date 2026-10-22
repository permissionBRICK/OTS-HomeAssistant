"""Plant roles, capabilities and the app's write sequences (HA-free).

Expected write sequences are the OCHSNER Smart app's actions from the
decompiled client: circuits p045d8/v.java, DHW p045d8/z.java.
"""

from __future__ import annotations

import pytest

HC1, HC2, HC3, HC4 = 31886, 19693, 23756, 11307


def values_of(snapshot):
    return dict(snapshot["values"])


# --- ids --------------------------------------------------------------------


def test_make_id_and_retag(plant_mod):
    P = plant_mod
    assert P.make_id(8971, HC1, 12262, 256) == "CyPmL458AAE="
    assert P.retag("CyPmL458AAE=", HC2) == "CyPmL+1MAAE="
    assert P.retag("CyPmL458AAE=", HC3) == "CyPmL8xcAAE="
    assert P.retag("CyPmL458AAE=", HC4) == "CyPmLyssAAE="
    assert P.HC_TAGS == (HC1, HC2, HC3, HC4)


@pytest.mark.parametrize(
    "n,role,expected",
    [
        # Re-tagged template points (the app's own per-circuit ids).
        (2, "op_program", "AiIQve1MIgE="),
        (3, "op_program", "AiIQvcxcIgE="),
        (4, "op_program", "AiIQvSssIgE="),
        (4, "comfort_eco", "AiPKUCssAAE="),
        (4, "season", "AiMN7CssAAE="),
        (2, "room_offset", "ACNZw+1MAAE="),
        (4, "room_offset", "ACNZwyssAAE="),
        (4, "badge", "CyPmLyssAAE="),
        (4, "name", "BCNL7issAAE="),
        (3, "heat_curve_norm_flow", "ACO38MxcAAE="),
        # Per-circuit points that are NOT a re-tag (app circuit screens).
        (1, "room_target_current", "CiMeXI58AAE="),
        (2, "room_target_current", "CiPkJO1MAAE="),
        (3, "room_target_current", "CiOt/MxcAAE="),
        (4, "room_target_current", "CiMQ1SssAAE="),
        (1, "room_temp", "AyI4FSiJAAE="),
        (2, "room_temp", "AyJN3SiJAAE="),
        (3, "room_temp", "AyKemiiJAAE="),
        (4, "room_temp", "AyKGXSiJAAE="),
        (1, "humidity", "AyJLCiiJAAE="),
        (4, "humidity", "AyI7yCiJAAE="),
        (2, "dew_point", "CiP+/+1MAAE="),
        (3, "dew_point", "CiNeusxcAAE="),
        (2, "setpoint_manual", "ASPYye1MAAE="),
        (4, "flow_actual", "AyJjGCiJAAE="),
        (4, "pump", "ByKxWCiJAAE="),
        (4, "mixer", "BiKI5SiJSjA="),
        (3, "availability", "ESMvhAAAAAE="),
    ],
)
def test_circuit_role_ids(plant_mod, n, role, expected):
    assert plant_mod.circuit_role_ids(n)[f"hc{n}.{role}"] == expected


def test_circuit_role_ids_hc4_has_no_manual_setpoint(plant_mod):
    assert "hc4.setpoint_manual" not in plant_mod.circuit_role_ids(4)
    with pytest.raises(ValueError):
        plant_mod.circuit_role_ids(5)


def test_circuit_roles_answer_on_the_reference_plant(plant_mod, snapshot):
    """HC1-3 role ids are real points on the AirHawk 518 (read-only snapshot)."""
    values = values_of(snapshot)
    # Not readable on this plant (cooling disabled, no room sensors, app-only).
    absent_ok = {"cool_limit_status", "room_temp", "humidity", "party_on", "party_hours", "party_temp",
                 "dew_point", "setpoint_manual"}
    for n in (1, 2, 3):
        for role, oid in plant_mod.circuit_role_ids(n).items():
            key = role.split(".", 1)[1]
            if key in absent_ok:
                continue
            assert oid in values, f"{role} {oid} missing from the snapshot"
    # HC4 does not exist on this plant.
    assert plant_mod.circuit_role_ids(4)["hc4.badge"] not in values


# --- capabilities -----------------------------------------------------------


def test_capabilities_live_snapshot(plant_mod, snapshot):
    caps = plant_mod.compute_capabilities(values_of(snapshot))
    assert plant_mod.public_capabilities(caps) == {
        "bms": False, "dhw": False, "buffer": True, "buffer_temp": True, "aux": True,
        "aux_temp": True, "buffer_aux_heater": False, "pool": False, "silent": True,
        "emergency": True, "power": True, "cooling": False,
    }
    # Live availability 27 27 27 0 -> three heat-only circuits, no cooling UI.
    assert caps["circuits"] == [
        {"index": n, "heat": True, "cool": False, "room_sensor": False, "humidity": False}
        for n in (1, 2, 3)
    ]


def _avail(plant_mod, hc=(0, 0, 0, 0), **extra):
    values = {plant_mod.hc_availability_id(i + 1): [v, v] for i, v in enumerate(hc)}
    for role, v in extra.items():
        values[plant_mod.AVAIL_ROLES[f"avail.{role}"]] = [v, v]
    return values


def test_capability_bitfields(plant_mod):
    P = plant_mod
    caps = P.compute_capabilities(_avail(P, (27, 27, 27, 0)))
    assert [c["index"] for c in caps["circuits"]] == [1, 2, 3]
    assert not any(c["cool"] for c in caps["circuits"])
    assert caps["cooling"] is False

    # 1 on + 2 heat + 4 cool + 32 room sensor + 64 humidity.
    caps = P.compute_capabilities(_avail(P, (1 | 2 | 4 | 32 | 64, 1 | 4, 2, 0)))
    c1, c2 = caps["circuits"]
    assert c1 == {"index": 1, "heat": True, "cool": True, "room_sensor": True, "humidity": True}
    assert c2 == {"index": 2, "heat": False, "cool": True, "room_sensor": False, "humidity": False}
    assert caps["cooling"] is True  # circuit 3 lacks bit 1: not present

    caps = P.compute_capabilities(_avail(P, (27,), dhw=256 | 2, pool=1, buffer=0, buffer_aux=4, aux=32, silent=2, emergency=0))
    assert caps["dhw"] and caps["pool"] and caps["buffer_aux_heater"] and caps["aux"]
    assert not caps["buffer"] and not caps["buffer_temp"] and not caps["aux_temp"]
    assert not caps["silent"] and not caps["emergency"]
    assert caps["power"] is False  # no power readings

    caps = P.compute_capabilities(_avail(P, (27,), dhw=2))
    assert caps["dhw"] is False  # bit 256 decides, not the circulation pump bit

    # BMS hides circuits, DHW and pool.
    caps = P.compute_capabilities(_avail(P, (27, 27, 0, 0), bms=1, dhw=256, pool=1, buffer=1))
    assert caps["bms"] and caps["circuits"] == [] and not caps["dhw"] and not caps["pool"]
    assert caps["buffer"]

    # Missing / absent points count as 0.
    caps = P.compute_capabilities({})
    assert caps["circuits"] == [] and not any(v for k, v in caps.items() if k != "circuits")


def test_role_ids_only_for_present_sections(plant_mod, snapshot):
    P = plant_mod
    caps = P.compute_capabilities(values_of(snapshot))
    roles = P.role_ids(caps)
    assert roles["hp.outdoor"] == "AyKWuiiJAAE="
    assert roles["hc3.badge"] == "CyPmL8xcAAE="
    assert "hc4.badge" not in roles and roles["hc4.availability"] == "ESPI9AAAAAE="
    assert not any(r.startswith(("dhw.", "pool.")) for r in roles)
    caps = dict(caps, dhw=True, pool=True)
    roles = P.role_ids(caps)
    assert roles["dhw.boost"] == "AiOzyHXZAAE=" and roles["pool.mode"] == "AiIQvULcIgE="
    for oid in P.capability_ids():
        assert oid in roles.values()


def test_role_values_unwrap_and_clean(plant_mod, snapshot):
    P = plant_mod
    values = values_of(snapshot)
    out = P.role_values(values, P.role_ids(P.compute_capabilities(values)))
    assert out["hp.outdoor"] == 12.1851
    assert out["hp.status"] == 1
    assert out["hc1.badge"] == 3
    assert out["hc1.season"] == 4
    assert out["hc1.name"] == "Fußboden"
    assert out["hc1.op_program_present"] == 3  # scalar answer
    assert out["avail.dhw"] == 0
    assert out["hp.dhw_valve"] is False
    assert "hc1.room_temp" not in out  # absent -> omitted
    # Mojibake from a latin-1 decoded response is repaired.
    assert P.clean_text("FuÃ\x9fboden") == "Fußboden"
    assert P.clean_text("#---") is None


# --- badge ------------------------------------------------------------------


def test_badges(plant_mod):
    P = plant_mod
    assert len(P.BADGES) == 23
    assert P.badge_name(3) == "auto_comfort_red"
    assert P.badge_name(22) == "vacation_blue"
    assert P.badge_name(23) == "unknown" and P.badge_name(None) == "unknown"
    expect = {0: "off", 1: "idle", 2: "idle", 3: "heating", 4: "cooling", 11: "heating",
              13: "cooling", 16: "heating", 17: "idle", 21: "cooling", 22: "cooling", 99: None}
    for v, action in expect.items():
        assert P.hvac_action_from_badge(v) == action, v


# --- climate mapping --------------------------------------------------------


@pytest.mark.parametrize(
    "heat,cool,modes",
    [
        (True, False, ["off", "heat"]),
        (False, True, ["off", "cool"]),
        (True, True, ["off", "heat", "cool", "heat_cool"]),
    ],
)
def test_hvac_modes(plant_mod, heat, cool, modes):
    assert plant_mod.hvac_modes(heat, cool) == modes


@pytest.mark.parametrize(
    "op,season,heat,cool,expected",
    [
        (1, 4, True, True, "off"),
        (0, 3, True, True, "heat"),
        (0, 2, True, True, "cool"),
        (0, 4, True, True, "heat_cool"),
        (0, 0, True, True, "heat_cool"),
        (0, 1, True, True, "heat_cool"),
        (3, 4, True, True, "heat_cool"),
        (0, 4, True, False, "heat"),  # heat-only: season ignored
        (0, 2, True, False, "heat"),
        (4, 4, True, True, "heat"),  # legacy manual heating
        (5, 4, True, True, "cool"),  # legacy manual cooling
        (0, 4, False, True, "cool"),  # cool-only circuit never reports heat_cool
        (None, 4, True, True, None),
    ],
)
def test_hvac_mode(plant_mod, op, season, heat, cool, expected):
    assert plant_mod.hvac_mode(op, season, heat, cool) == expected


def _circuit(P, n=1, **vals):
    ids = {k.split(".", 1)[1]: v for k, v in P.circuit_role_ids(n).items()}
    values = {ids[k]: [v, v] for k, v in vals.items() if v is not None}
    return values, ids


def test_plan_hvac_mode_matches_app_actions(plant_mod):
    P = plant_mod
    op, season = "AiIQvY58IgE=", "AiMN7I58AAE="
    # ToggleOff: op <- 1.
    values, ids = _circuit(P, op_program=0, season=4)
    assert P.plan_hvac_mode("off", values, ids, cool=True) == [(op, 1)]
    values, ids = _circuit(P, op_program=1, season=4)
    assert P.plan_hvac_mode("off", values, ids, cool=True) == []
    # SetWinter / SetSummer / SetAuto from off: op <- 0, season <- 3/2/4.
    assert P.plan_hvac_mode("heat", values, ids, cool=True) == [(op, 0), (season, 3)]
    assert P.plan_hvac_mode("cool", values, ids, cool=True) == [(op, 0), (season, 2)]
    assert P.plan_hvac_mode("heat_cool", values, ids, cool=True) == [(op, 0)]
    # Only changed values are written.
    values, ids = _circuit(P, op_program=0, season=3)
    assert P.plan_hvac_mode("heat", values, ids, cool=True) == []
    assert P.plan_hvac_mode("heat_cool", values, ids, cool=True) == [(season, 4)]
    # Heat-only circuit: never touches the season changeover.
    values, ids = _circuit(P, op_program=1, season=4)
    assert P.plan_hvac_mode("heat", values, ids, cool=False) == [(op, 0)]
    # Unknown readings are written.
    values, ids = _circuit(P)
    assert P.plan_hvac_mode("cool", values, ids, cool=True) == [(op, 0), (season, 2)]
    assert P.plan_turn_on(values, ids) == [(op, 0)]
    with pytest.raises(ValueError):
        P.plan_hvac_mode("dry", values, ids, cool=True)


def test_plan_hvac_mode_uses_the_circuits_own_ids(plant_mod):
    P = plant_mod
    values, ids = _circuit(P, 3, op_program=1, season=3)
    assert P.plan_hvac_mode("cool", values, ids, cool=True) == [("AiIQvcxcIgE=", 0), ("AiMN7MxcAAE=", 2)]


def test_plan_preset_matches_app_actions(plant_mod):
    P = plant_mod
    op, ce = "AiIQvY58IgE=", "AiPKUI58AAE="
    values, ids = _circuit(P, op_program=1, comfort_eco=0)
    assert P.plan_preset("eco", values, ids) == [(op, 0), (ce, 1)]  # SetEco
    assert P.plan_preset("comfort", values, ids) == [(op, 0)]
    values, ids = _circuit(P, op_program=0, comfort_eco=1)
    assert P.plan_preset("comfort", values, ids) == [(ce, 0)]  # SetComfort
    assert P.plan_preset("eco", values, ids) == []
    assert P.preset_mode(0) == "comfort" and P.preset_mode(1) == "eco" and P.preset_mode(None) is None
    with pytest.raises(ValueError):
        P.plan_preset("away", values, ids)


@pytest.mark.parametrize(
    "selector,expected",
    [(0, 21.0), (2, 21.0), (1, 24.0), (3, 24.0), (None, 20.5), (7, 20.5)],
)
def test_circuit_target_follows_target_selector(plant_mod, selector, expected):
    values, ids = _circuit(
        plant_mod, target_selector=selector, room_target_heat=21.0, room_target_cool=24.0, room_target_current=20.5
    )
    assert plant_mod.circuit_target(values, ids) == expected


def test_offset_math(plant_mod):
    P = plant_mod
    off = "ACNZw458AAE="
    # Target 21.5 incl. offset 0.5 -> base 21.0, reachable 18.0..24.0.
    values, ids = _circuit(P, target_selector=0, room_target_heat=21.5, room_offset=0.5)
    assert P.offset_bounds(21.5, 0.5) == (18.0, 24.0)
    assert P.plan_temperature(22.0, values, ids) == [(off, 1.0)]
    assert P.plan_temperature(21.0, values, ids) == [(off, 0.0)]
    assert P.plan_temperature(21.5, values, ids) == []  # unchanged -> no write
    assert P.plan_temperature(30.0, values, ids) == [(off, 3.0)]  # clamped
    assert P.plan_temperature(10.0, values, ids) == [(off, -3.0)]
    assert P.plan_temperature(21.7, values, ids) == []  # rounds to the current 0.5 step
    assert P.plan_temperature(21.2, values, ids) == [(off, 0.0)]
    assert P.plan_temperature(21.8, values, ids) == [(off, 1.0)]
    assert P.plan_temperature(20.24, values, ids) == [(off, -1.0)]
    assert P.new_offset(21.0, 21.5, -0.5) == -1.0
    assert P.new_offset(21.5, 21.5, None) == 0.0
    values, ids = _circuit(P)
    with pytest.raises(ValueError):
        P.plan_temperature(21.0, values, ids)


# --- DHW --------------------------------------------------------------------

PROG, CE, BOOST = "AiIQvXXZIgE=", "AiPKUHXZAAE=", "AiOzyHXZAAE="


def _dhw(**vals):
    m = {"program": PROG, "comfort_eco": CE, "boost": BOOST}
    return {m[k]: [v, v] for k, v in vals.items() if v is not None}


@pytest.mark.parametrize(
    "vals,expected",
    [
        ({"program": 1, "comfort_eco": 2, "boost": 1}, "off"),
        ({"program": 0, "comfort_eco": 2, "boost": 1}, "high_demand"),
        ({"program": 0, "comfort_eco": 1, "boost": 0}, "eco"),
        ({"program": 0, "comfort_eco": 2, "boost": 0}, "performance"),
        ({"program": 0}, "performance"),
        ({}, None),
    ],
)
def test_dhw_operation(plant_mod, vals, expected):
    assert plant_mod.dhw_operation(_dhw(**vals)) == expected


def test_dhw_write_sequences_match_app_actions(plant_mod):
    P = plant_mod
    unknown = {}
    # Exact app sequences when nothing is known (everything is written).
    assert P.plan_dhw_operation("off", unknown) == [(PROG, 1)]
    assert P.plan_dhw_operation("eco", unknown) == [(PROG, 0), (CE, 1), (BOOST, 0)]
    assert P.plan_dhw_operation("performance", unknown) == [(PROG, 0), (CE, 2), (BOOST, 0)]
    assert P.plan_dhw_operation("high_demand", unknown) == [(PROG, 0), (BOOST, 1)]
    assert P.plan_dhw_boost(unknown) == [(PROG, 0), (BOOST, 1)]
    assert P.plan_dhw_on(unknown) == [(PROG, 0)]
    # Only differing values are written.
    cur = _dhw(program=0, comfort_eco=2, boost=0)
    assert P.plan_dhw_operation("eco", cur) == [(CE, 1)]
    assert P.plan_dhw_operation("performance", cur) == []
    assert P.plan_dhw_operation("high_demand", cur) == [(BOOST, 1)]
    assert P.plan_dhw_boost(cur) == [(BOOST, 1)]
    assert P.plan_dhw_on(cur) == []
    cur = _dhw(program=1, comfort_eco=1, boost=0)
    assert P.plan_dhw_operation("performance", cur) == [(PROG, 0), (CE, 2)]
    with pytest.raises(ValueError):
        P.plan_dhw_operation("electric", cur)
    assert P.dhw_setpoint_role("eco") == "dhw.setpoint_eco"
    assert P.dhw_setpoint_role("high_demand") == "dhw.setpoint_boost"
    assert P.dhw_setpoint_role("performance") == "dhw.setpoint_comfort"
    assert P.dhw_setpoint_role("off") == "dhw.setpoint_comfort"


# --- supplements ------------------------------------------------------------


def test_supplements_from_snapshot(plant_mod, snapshot):
    P = plant_mod
    values = values_of(snapshot)
    caps = P.compute_capabilities(values)
    sup = P.supplement_entities(
        values=values, caps=caps, existing={}, circuit_devices={1: ("tag:31886", "Fußboden")}, language="de"
    )
    selects, numbers = sup["selects"], sup["numbers"]
    assert [s["read_id"] for s in selects] == ["AiPKUI58AAE=", "AiPKUO1MAAE=", "AiPKUMxcAAE="]
    assert [n["read_id"] for n in numbers] == ["ACNZw458AAE=", "ACNZw+1MAAE=", "ACNZw8xcAAE="]
    s1, n1 = selects[0], numbers[0]
    assert s1["name"] == "Komfort/Eco" and s1["options"] == {"Komfort": 0, "Eco": 1}
    assert s1["write_id"] == s1["read_id"]
    assert (s1["heating_circuit_uid"], s1["heating_circuit_name"]) == ("tag:31886", "Fußboden")
    assert selects[1]["heating_circuit_uid"] == "tag:19693"  # default discovery uid
    assert selects[1]["heating_circuit_name"] == "Heizkreis 2"
    assert n1["name"] == "Raumsollwert-Korrektur"
    assert (n1["min"], n1["max"], n1["step"], n1["unit"]) == (-3.0, 3.0, 0.5, "K")
    # No uuid: the platforms derive the standard host:platform:read_id unique_id.
    assert "uuid" not in s1 and "uuid" not in n1
    en = P.supplement_entities(values=values, caps=caps, existing={}, circuit_devices={}, language="en")
    assert en["selects"][0]["name"] == "Comfort/Eco"
    assert en["selects"][0]["options"] == {"Comfort": 0, "Eco": 1}
    assert en["numbers"][0]["name"] == "Room setpoint correction"


def test_supplements_dedupe_and_presence(plant_mod, snapshot):
    P = plant_mod
    values = values_of(snapshot)
    caps = P.compute_capabilities(values)
    existing = {
        # A stored writable entity on the same point (unpadded spelling) wins.
        "selects": [{"read_id": "AiPKUI58AAE", "write_id": "AiPKUI58AAE", "options": {"a": 0}}],
        # A read-only sensor does not block the writable control.
        "sensors": [{"id": "ACNZw458AAE="}],
    }
    sup = P.supplement_entities(values=values, caps=caps, existing=existing, circuit_devices={}, language="en")
    assert "AiPKUI58AAE=" not in [s["read_id"] for s in sup["selects"]]
    assert "ACNZw458AAE=" in [n["read_id"] for n in sup["numbers"]]
    # Points that do not answer get no entity.
    values.pop("ACNZw+1MAAE=")
    sup = P.supplement_entities(values=values, caps=caps, existing={}, circuit_devices={}, language="en")
    assert "ACNZw+1MAAE=" not in [n["read_id"] for n in sup["numbers"]]


def test_dhw_supplements(plant_mod):
    P = plant_mod
    values = {
        "ESMSIwAAAAE=": [256, 256],
        "ACPOwnXZAAE=": [0, 0],
        "AiPKUHXZAAE=": [2, 2],
    }
    caps = P.compute_capabilities(values)
    existing = {
        # The catalog exposes both as read-only sensors: still supplemented.
        "sensors": [{"id": "ACPOwnXZAAE="}, {"id": "AiPKUHXZAAE="}],
    }
    sup = P.supplement_entities(values=values, caps=caps, existing=existing, circuit_devices={}, language="de")
    (num,) = sup["numbers"]
    (sel,) = sup["selects"]
    assert num["read_id"] == "ACPOwnXZAAE=" and num["name"] == "Warmwasser-Korrektur"
    assert (num["min"], num["max"], num["step"]) == (-9.0, 9.0, 3.0)
    assert "heating_circuit_uid" not in num  # main device
    assert sel["read_id"] == "AiPKUHXZAAE=" and sel["options"] == {"Eco": 1, "Komfort": 2}
    # Without DHW nothing is generated.
    values["ESMSIwAAAAE="] = [0, 0]
    sup = P.supplement_entities(values=values, caps=P.compute_capabilities(values), existing={}, circuit_devices={}, language="de")
    assert sup == {"numbers": [], "selects": []}


def test_circuit_device_map_reuses_existing_devices(plant_mod):
    P = plant_mod
    ents = {
        "sensors": [
            # Shared IO tag: says nothing about the circuit's own device.
            {"id": "AyKvbyiJAAE=", "heating_circuit_uid": "io", "heating_circuit_name": "x"},
            {"id": "CyMogo58AAE=", "heating_circuit_uid": "bundle-uuid-1", "heating_circuit_name": "Fußboden"},
        ],
        "numbers": [{"read_id": "ACO38O1MAAE", "heating_circuit_uid": "tag:19693", "heating_circuit_name": "Radiatoren"}],
    }
    assert P.circuit_device_map(ents) == {1: ("bundle-uuid-1", "Fußboden"), 2: ("tag:19693", "Radiatoren")}


# --- labels -----------------------------------------------------------------


def test_builtin_labels(plant_mod):
    P = plant_mod
    assert P.builtin_labels("hc2.op_program", "de")["1"] == "Aus"
    assert P.builtin_labels("hc2.op_program_present", "en")["8"] == "Holiday"
    assert P.builtin_labels("hc1.season", "en")["4"] == "Automatic (heating/cooling)"
    assert P.builtin_labels("dhw.comfort_eco", "de") == {"1": "Eco", "2": "Komfort"}
    assert P.builtin_labels("hp.program", "de")["3"] == "Abgeschaltet"
    assert P.builtin_labels("pool.mode", "en") == {"0": "Automatic", "1": "Off", "2": "On", "3": "Holiday"}
    assert P.builtin_labels("hp.outdoor", "de") is None


def test_catalog_labels(plant_mod, catalog_mod):
    P = plant_mod
    cat = catalog_mod.load_catalog()
    rec = cat.points_by_id["CyNmeMdZAAE="]  # heat pump status, numeric-keyed labels
    de = P.catalog_labels(rec, "de", cat.enum_token_labels)
    en = P.catalog_labels(rec, "en", cat.enum_token_labels)
    assert de["1"] == "Heizen" and de["14"] == "Störung"
    assert en["1"] != "Heizen" and en["1"]
    # Token-keyed labels resolve from the pump's descriptor tokens.
    rec = cat.points_by_id["AiMu5MdZAAE="]
    tokens = ["Auto", "HeatSpd", "CoolSpd", "Off"]
    assert P.catalog_labels(rec, "de", cat.enum_token_labels, tokens) == {
        "0": "Automatik", "1": "Handbetrieb Heizen", "2": "Handbetrieb Kühlen", "3": "Abgeschaltet",
    }
    assert P.catalog_labels(None, "de") is None


def test_role_units(plant_mod):
    P = plant_mod
    assert P.role_unit("hp.outdoor") == "°C"
    assert P.role_unit("hp.energy_el_year") == "kWh"
    assert P.role_unit("hc2.room_offset") == "K"
    assert P.role_unit("hc1.heat_curve_norm_flow") == "°C"
    assert P.role_unit("hc1.heat_limit_status") is None
    assert P.role_unit("hp.status") is None
