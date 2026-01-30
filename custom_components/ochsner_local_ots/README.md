# Ochsner Local OTS - Home Assistant custom integration

This custom integration polls a Siemens Climatix controller via the **generic** LAN endpoint:

- `http://<host>:<port>/jsongen.html?FN=Read&OA=<genericJsonId>&PIN=...&LNG=-1&US=1`

It supports:
- Read-only values as Home Assistant `sensor` entities
- Writable values as Home Assistant `number` entities

Values returned by Climatix often look like:

```json
{"values": {"AyJn4iiJAAE=": [36.5375, 36.5375]}}
```

This integration automatically takes the **first element** (`36.5375`) so Home Assistant gets a plain numeric state.

## Install

Copy the folder:

- `custom_components/ochsner_local_ots`

into your Home Assistant config directory.

## Configuration (configuration.yaml)

```yaml
ochsner_local_ots:
  host: 192.168.178.80
  port: 80
  username: JSON
  password: SBTAdmin!
  pin: "7659"
  scan_interval: 10  # seconds

  sensors:
    - name: Current Temp
      id: AyJn4iiJAAE=
      unit: "°C"

  numbers:
    - name: Buffer Temp Setpoint
      id: ACPxdC60AAE=
      unit: "°C"
      min: 0
      max: 80
      step: 0.5

    # If your controller uses different OA IDs for reading vs writing, you can split them:
    - name: Room Setpoint
      read_id: AAAAAAAABBB=
      write_id: CCCCCCCCDDD=
      unit: "°C"
      min: 10
      max: 30
      step: 0.5

  selects:
    - name: Betriebswahl Heizkreis 4
      read_id: EEEEEEEEFFF=
      write_id: GGGGGGGGHHH=
      options:
        Aus: 0
        Auto: 1
        Komfort: 2
        Absenk: 3
```

Restart Home Assistant after changing the config.

## Configuration (UI)

If you set up the integration via the UI, you can adjust **per-entity** metadata in:

- Settings → Devices & services → Ochsner Local OTS → Options

Enable `configure_entities` to:

- Override `device_class` (e.g. `temperature`, `humidity`, `pressure`)
- Override `unit` (e.g. `°C`, `%`)
- Override `min`/`max`/`step` for `number` entities (clamped to `bundle_min`/`bundle_max`)

These overrides are stored in the config entry **options** and applied after a reload.

## Notes

- `id`/`read_id`/`write_id` must be the **genericJsonId** (the `OA` value), e.g. something that ends in `=`.
- If you don’t want to send a PIN, set `pin` to an empty string.
- For `selects`, `options` maps the dropdown text (key) to the value written to the register.
