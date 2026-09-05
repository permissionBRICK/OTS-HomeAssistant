# Ochsner local OTS - Climatix - (HACS)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![GitHub Release](https://img.shields.io/github/release/permissionBRICK/OTS-HomeAssistant.svg)](https://github.com/permissionBRICK/OTS-HomeAssistant/releases)
[![License](https://img.shields.io/github/license/permissionBRICK/OTS-HomeAssistant.svg)](LICENSE)
[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-donate-yellow.svg)](https://buymeacoffee.com/permissionbrick)
![Usage counter](https://img.shields.io/badge/dynamic/json?color=41BDF5&logo=home-assistant&label=integration%20usage&suffix=%20installs&cacheSeconds=15600&url=https://analytics.home-assistant.io/custom_integrations.json&query=$.ochsner_local_ots.total)

**What is this repo?**

It's a way to locally read and control Ochsner heat pump settings via Home Assistant, allowing you to access all the same settings and values as the OTS app, all without Modbus or Cloud services required.

---


## 1) Installation
### One-click install via HACS - recommended
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=permissionBRICK&repository=OTS-HomeAssistant&category=integration)

#### Alternative: Manual install path via HACS
1) In Home Assistant, go to **HACS** -> **Integrations**.
2) Open the menu (top right) **Custom repositories**.
3) Add this GitHub repo URL and set category to **Integration**.
4) Search for **Ochsner Local OTS - Climatix Generic** in HACS and install.
5) Restart Home Assistant.

#### Offline alternative: Manual folder install path
Copy this folder into your HA config directory:

- `custom_components/ochsner_local_ots/`

After that restart Home Assistant.

### 2) Add the integration into Home Assistant

After restarting Home Assistant, check **Settings → Devices & services** for a
discovered Ochsner heat pump and click **Add**. The integration verifies the
model and serial number before offering it, then scans its available sensors
and controls when you adopt it.

If no discovery card appears:

1) Choose **Add integration → Ochsner Local OTS → Scan network**.
2) Select the heat pump by its model and serial number, then confirm.
3) Alternatively, choose **Enter address manually**. Advanced credentials/PIN
   settings are offered after a failed connection attempt.

Automatic cards use Home Assistant's DHCP/network discovery. Siemens Climatix
MAC/hostname patterns are candidate hints, **not Ochsner identification and not
guaranteed across all models**. The explicit network scan does not require
these hints. Home Assistant must be able to reach the heat pump's LAN; check
**Settings → System → Network** if the scan finds nothing. Initial discovery
uses the standard local API credentials and port.

Once the serial number is known, a fixed IP is optional. The integration keeps
the last address and searches again if that address becomes unavailable or
answers with a different serial. It switches only after verifying the saved
serial at the new address. Existing entities, history, device associations,
and entity overrides retain their identifiers. Older installations learn the
serial from their working address on the next restart.

Scans cover enabled private IPv4 subnets of up to /20, with at most 4096
candidates, eight concurrent probes and a two-second timeout per candidate.
A /24 scan can take about a minute; larger networks take longer. Recovery also
tries the saved address's /24 when it is outside Home Assistant's selected
subnets, which helps with routed/container setups. Unsuccessful recovery scans
wait five minutes before trying again. Scanning is IPv4-only. For initial
setup on a remote/VLAN network without a selected interface, enter a reachable
controller address manually.

---

## How does this work?

Instead of interacting with the heat pump over the Interface that is offered via ModbusTCP which is very undocumented and allows you to only read some values and control almost nothing, this one uses the JSON Interface that the OTS App itself uses to communicate with the heat pump (which is even less documented since it is entirely reverse engineered), except it runs entirely locally!

The local API offers an interface that allows you to read and write almost any parameter, as long as you know its ID. The datapoint id system was reverse engineered (an id is the Base64 encoding of object type, module instance tag, point index and member id), which makes it possible to ship a datapoint catalog with the integration and probe which of those datapoints your controller actually has - entirely locally, with only the IP address, no cloud account needed.

Setup runs a two-phase scan: first one representative datapoint per known module to see which modules your plant has (heating circuits, DHW, buffer, ...), then all catalog datapoints of the present modules. Only ids that the controller answers with a value become entities. The scan is strictly read-only and takes only a few seconds; afterwards the entity list is stored in Home Assistant, so the scan does not run again unless you ask for a rescan. The integration works entirely locally, no matter what happens to the Ochsner cloud.

This has been tested and confirmed working so far with:
 - Air Hawk 518
 - Air Hawk 208
 - Air Falcon 212

However, it is plausible that it could work with any Ochsner Heat Pump that uses the OTS app.


## Disclaimer & Warning

This project is not affiliated with or endorsed by Ochsner. It is simply a hobby project based entirely on reverse engineerecd information.

Tread with caution when changing random values, make sure you know the exact value you are trying to change and have correctly identified it. For the most part, the integration mostly seems to correctly parse the minimum / maximum for every value and map that to the UI so you can't set any temperature ranges that the heatpump wouldn't allow, but there are also some settings here which appear to be hidden in the app, and I have no idea what their effects might be if you try to use them.

Also of note: The settings are saved inside the Siemens Climatix Controller inside the heatpump and that uses Flash storage - which means it has a limited number of erase cycles before the chip fails. Siemens documentation seems to claim this is at 100k (100,000) write cycles. Therefore, if you plan not to just make controls available to the UI but also automate some settings, calculate a rough estimate of how many write cycles this would cause in the worst case and ensure that over the lifetime of your heatpump you stay below that total number of writes. For example: an average of 10 writes per day would last you 30 years for the rated lifetime of the flash chip. But, of course if you only control the heatpump for the most expensive 4 months of the year, you get up to 30 writes for each of those days.

The integration offers a Flash Counter value that automatically keeps track of the total number of writes you issue against the heat pump controller, so if you automate anything, keep an eye on that number.

This was entirely reverse engineered from the app using AI. There are a lot of redundant code segments and strange hacks that the AI implemented in order to find the correct values / descriptions, but I don't have the time or energy to properly re-develop this by hand. If you find any issues, feel free to make a PR.
