# Climatix (OTS bundle → local control → Home Assistant) — step-by-step

This repo contains:
- A Python CLI tool: `tools/climatix_local_api.py`
- A Home Assistant custom integration: `custom_components/climatix_generic/`

The overall flow is:
1) Use OTS cloud credentials to fetch the **bundle JSON** (contains the bindings/IDs)
2) Use the bundle to **search for points** (names like “Puffertemp”, “Heizkreis 1”, …)
3) Use the **generic local endpoint** (`jsongen.html`, `OA=<genericJsonId>`) to read/write values
4) Add the IDs to Home Assistant (`sensors`, `numbers`, `selects`)

> Note on filenames: your examples use `climatix_control.py`, but this repo’s script is `tools/climatix_local_api.py`.
> If you want to keep your command lines identical, you can copy/rename it:
> `copy .\tools\climatix_local_api.py .\climatix_control.py`

---

## 0) Prerequisites

- Windows + Python 3.10+ installed
- LAN access to the controller IP (example below uses `192.168.X.X`)

If you don't know your Heatpump IP Address - check your router. It should list all clients somewhere. Make sure to assign it a static IP address in your local network.

If you find a device in your router and connect to the ip in your browser, you'll receive a login screen. Abort the login prompt, and if you receive a 401 error from Siemens Building Technologies Climatix WEB Server V1.00, 2008 (that's the actual manufacturer of the Heatpump, not Ochsner), then you'll know you have the right one.

---

## 1) Retrieve `configID` + `siteID` from OTS cloud

Run:

```powershell
python .\tools\climatix_local_api.py ots-login --ots-user <OTS_USER> --ots-pass <OTS_PASS>
```

In the output, look at `plantInfos[...]` and copy:
- `configID`
- `siteID`

(You can also just use `--plant-index 0` in the next step if you only have one plant.)

---

## 2) Download + decode the bundle.json

Option A (simplest): choose a plant by index:

```powershell
python .\tools\climatix_local_api.py ots-download-bundle --ots-user <OTS_USER> --ots-pass <OTS_PASS> --plant-index 0 --out .\bundle.json
```

Option B: specify the IDs you copied from step 1:

```powershell
python .\tools\climatix_local_api.py ots-download-bundle --ots-user <OTS_USER> --ots-pass <OTS_PASS> --config-id <configID> --site-id <siteID> --out .\bundle.json
```

After this step you should have:
- `bundle.json` in the repo root

---

## 3) Find interesting registers (search the bundle)

Example: find “Puffertemp”:

```powershell
python .\tools\climatix_local_api.py bundle-list --bundle .\bundle.json --filter "Puffertemp"
```

Tips:
- Use `--context-filter "Heizkreis 1"` to narrow by heating circuit
- The table shows `genericId` (this is the id you need for the home assistant config)

---

## 4) Read values for many matches (and copy the `genericId` you need)

`bundle-list-read` reads the **readBinding** rows only and prints a `val` column.

Heizkreis example (read many matches inside Heizkreis 1):

```powershell
python .\tools\climatix_local_api.py --host 192.168.X.X  bundle-list-read --bundle .\bundle.json --filter "raum" --context-filter "Heizkreis 1" --wide --limit 20 --generic
```

Buffer example:

```powershell
python .\tools\climatix_local_api.py --host 192.168.X.X bundle-list-read --bundle .\bundle.json --filter "puffertem" --wide --limit 20 --generic
```

From the output, copy the `genericId` (base64 ending in `=`) for the point you want.

---

## 5) Test writing a value (generic endpoint)

Once you copied a `genericId` (OA), try a write:

```powershell
python .\tools\climatix_local_api.py --host 192.168.X.X write-generic --id ASMhEo58AAE= --value 23
```

If your “read” ID differs from your “write” ID:
- use the `writeBinding` row’s `genericId` for writing
- use the `readBinding` row’s `genericId` for reading

---

## 6) Home Assistant: install the custom integration

1) Copy this folder into your HA config directory:

- `custom_components/climatix_generic/`

2) Add configuration to `configuration.yaml`.

Sample config (covers sensor + number + select):

```yaml
climatix_generic:
  host: 192.168.X.X
  scan_interval: 10

  sensors:
    - name: Puffertemp Ist
      id: <PASTE_READ_genericId_FROM_bundle-list-read>
      unit: "°C"

  numbers:
    # If read/write use the same ID:
    - name: Puffertemp Soll
      id: <PASTE_genericId>
      unit: "°C"
      min: 0
      max: 80
      step: 0.5

    # If read/write use different IDs:
    - name: Raum Soll Heizkreis 1
      read_id: <PASTE_READ_genericId>
      write_id: <PASTE_WRITE_genericId>
      unit: "°C"
      min: 10
      max: 30
      step: 0.5

  selects:
    - name: Betriebswahl Heizkreis Radiatoren
      read_id: <PASTE_READ_genericId>
      write_id: <PASTE_WRITE_genericId>
      options:
        Komfort: 0
        Aus: 1
        Reduziert: 2
        Standard: 3
        Manuell: 4
```
Usually read and write ids will be identical for each parameter, but it is technically possible for them to differ for the same parameter, therefore they are configured separately.


3) Restart Home Assistant.

After restart:
- all entities appear under one device: **Climatix (<host>)**
- entities have stable unique IDs (so you can rename/customize them in HA)

---

## Quick troubleshooting

- If reads work but writes don’t: verify you’re using the `writeBinding` ID for writing.