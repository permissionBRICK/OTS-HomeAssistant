# Climatix local API notes (reverse engineered)

## Where this comes from in the app

- Local/direct Climatix HTTP client: `sources/p076h3/C2175f.java`
- Read/Write query building:
  - `sources/p086i3/C2238c.java` (`FN=Read`, adds `ID=<id>`)
  - `sources/p086i3/C2239d.java` (`FN=Write`, adds `ID=<id>;<value>`)
- Default credentials in controller info model: `sources/eu/inthouse/info/controllerinfo/ClimatixJSONControllerInfo.java`
  - username default: `JSON`
  - password default: `SBTAdmin!`
  - port default: `80`
  - PIN default: `7659` (optional: it’s only appended if present)

## The actual local endpoint

The app talks to the controller’s *built-in* Climatix web interface:

- `GET http://{host}:{port}/JSON.HTML?...`
- Adds `LNG=-1&US=1` on every request
- Uses HTTP Basic authentication header

Read:

- `FN=Read`
- One or more repeated `ID` query params:
  - `.../JSON.HTML?FN=Read&ID=<id1>&ID=<id2>&LNG=-1&US=1`

Write:

- `FN=Write`
- `ID=<id>;<value>` (value is typically just a number string for analog setpoints)
  - `.../JSON.HTML?FN=Write&ID=<id>;<value>&LNG=-1&US=1`

Optional PIN:

- If the controller has a PIN configured, the app appends `PIN=...` as an additional query param.

## Generic local endpoint (jsongen.html / OA=...)

Some controllers/firmware expose (or behave better with) the *generic* Climatix endpoint:

- `GET http://{host}:{port}/jsongen.html?...`
- Uses query param `OA` (not `ID`)
- `OA` values are **`genericJsonId`** from the bundle (normal Base64, typically ends with `=`)

Read:

- `.../jsongen.html?FN=Read&OA=<genericJsonId>&PIN=<pin>&LNG=-1&US=1`

Write:

- `.../jsongen.html?FN=Write&OA=<genericJsonId>;<value>&PIN=<pin>&LNG=-1&US=1`

## Script

A small standalone script is in:

- `tools/climatix_local_api.py`

Examples:

```bash
python tools/climatix_local_api.py --host 192.168.1.50 read --id ACIAAAABAAA-

python tools/climatix_local_api.py --host 192.168.1.50 --pin 7659 read --id <YOUR_SETPOINT_ID>

python tools/climatix_local_api.py --host 192.168.1.50 --pin 7659 write --id <YOUR_SETPOINT_ID> --value 21.5

# Generic endpoint examples
python tools/climatix_local_api.py --host 192.168.1.50 --pin 7659 read-generic --id <YOUR_GENERIC_ID>

python tools/climatix_local_api.py --host 192.168.1.50 --pin 7659 write-generic --id <YOUR_GENERIC_ID> --value 21.5
```

If you know the numeric triple, you can compute the ID the same way the app does:

```bash
python tools/climatix_local_api.py encode-id --object-type 34 --object-id 1 --member-id 40
python tools/climatix_local_api.py decode-id --id ACIAAAABAAA-
```

If you have an extracted config/bundle/resources dump from the app, you can scan it for bindings:

```bash
python tools/climatix_local_api.py extract-ids --path <FILE_OR_DIR>
```

## Download the config bundle from the cloud (no phone/root required)

This app can fetch the same bundle it uses at runtime via Ochsner's OTS service endpoints.

1) Login and list plants (shows `configID` + `siteID`):

```bash
python tools/climatix_local_api.py ots-login --ots-user <USER> --ots-pass <PASS>
```

2) Download + decode the bundle JSON, and optionally extract bindings:

```bash
python tools/climatix_local_api.py ots-download-bundle --ots-user <USER> --ots-pass <PASS> --plant-index 0 --out bundle.json --also-extract-ids

# Or specify the IDs explicitly (from the login output)
python tools/climatix_local_api.py ots-download-bundle --ots-user <USER> --ots-pass <PASS> --config-id <CONFIGID> --site-id <SITEID> --out bundle.json
```

## Use a downloaded bundle to read/write more easily

List bindings (table):

```bash
python tools/climatix_local_api.py bundle-list --bundle bundle.json --filter temperatur

# If your controller requires the generic endpoint, list the OA IDs instead
python tools/climatix_local_api.py bundle-list --bundle bundle.json --filter temperatur --id-mode generic

# Or show both jsonId and genericJsonId
python tools/climatix_local_api.py bundle-list --bundle bundle.json --filter temperatur --id-mode both

# Narrow to a controller and writable points
python tools/climatix_local_api.py bundle-list --bundle bundle.json --controller climatix --writable --filter set
```

List bindings and also query current values (adds a `val` column):

```bash
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-list-read --bundle bundle.json --filter temperatur --limit 50

# Generic endpoint version (reads via jsongen.html / OA=...)
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-list-read --bundle bundle.json --filter temperatur --generic --wide
```

Read a matched binding (prints candidates if multiple):

```bash
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-read --bundle bundle.json --filter "#Wohnzimmer" --pick 0

# Generic endpoint version (uses genericJsonId / OA=...)
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-read --bundle bundle.json --filter "#Wohnzimmer" --pick 0 --generic
```

Write a matched binding:

```bash
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-write --bundle bundle.json --filter "comfort" --pick 0 --value 21.5

# Generic endpoint version
python tools/climatix_local_api.py --host <CONTROLLER_IP> --pin <PIN> bundle-write --bundle bundle.json --filter "comfort" --pick 0 --value 21.5 --generic
```

## How to find the right `ID` for temperature setpoints

This repo is *code only*; the actual configuration (which contains the bindings between UI widgets and Climatix objectType/objectId/memberId) is downloaded at runtime.

Practical ways to obtain the setpoint ID:

1. Observe requests made by the UI while changing a setpoint.
   - When you tap +/- in the thermostat UI, the app issues a `FN=Write` request containing `ID=<something>;<value>`.
2. Extract the app’s downloaded config from the device (phone/local hub) and search for `jsonId` strings.
   - The app stores configs per `configId` and uses them to populate `C4106a` bindings.
  - The script supports this directly via `extract-ids`.
3. If you already know `objectType/objectId/memberId` for the setpoint, compute the Climatix JSON ID via `encode-id`.

Once you have the correct ID, reading and writing it via `JSON.HTML` is straightforward.
