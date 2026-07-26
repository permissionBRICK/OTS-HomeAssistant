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
### 1a) Install via HACS - recommended
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=permissionBRICK&repository=OTS-HomeAssistant&category=integration)

Prereqs:
- HACS is installed in Home Assistant.

Steps:
1) In Home Assistant, go to **HACS** -> **Integrations**.
2) Open the menu (top right) **Custom repositories**.
3) Add this GitHub repo URL and set category to **Integration**.
4) Search for **Ochsner Local OTS - Climatix Generic** in HACS and install.
5) Restart Home Assistant.

### 1b) Alternative - install the custom integration manually

Copy this folder into your HA config directory:

- `custom_components/ochsner_local_ots/`

After that restart Home Assistant.

### 2) Add the integration into Home Assistant (accountless, IP-only)

Steps:
1) Add a new integration via the HA UI
2) Search for Ochsner Local OTS
3) Enter your local Heatpump IP Address - that's it. No cloud account, no OTS login. (The IP is displayed inside your Heatpump settings. Make sure to assign it a fixed IP address in your internet router.)
4) The integration scans the controller with its built-in datapoint catalog and creates all readable entities automatically. This takes a few seconds.
5) Done!

The standard Climatix credentials and PIN are used automatically. In the rare case your controller was reconfigured, the flow offers advanced settings (port / username / password / PIN) after a failed connection attempt.

#### What the local catalog scan can and cannot find (honest coverage)

The accountless scan is a **best-effort local catalog scan**, not a promise of full parity with a cloud bundle:

- The catalog ships the addresses extracted from the OCHSNER app **plus** everything known from a real reference plant (names, units, enum options, safe ranges, write bindings). On that reference plant the scan reproduces the bundle-based setup — **by construction**, because its datapoints seed the catalog: all 387 readable datapoint ids are found (392 distinct ids counting the five write-only ones), with identical units, platforms and device grouping. Display text depends on the chosen language: in **German** the names match the bundle exactly and only 10 enum option sets intentionally differ (the controller advertises states the bundle never knew — those show as raw controller tokens rather than invented labels); in **English** names and enum labels intentionally differ from the German bundle because they come from the app's English resources. Other plants benefit from every seeded datapoint they share with it, but this is not a universal 100% guarantee.
- From the app alone (i.e. for datapoints the reference plant does not have), the measured ceiling is **53.3% of addresses** and **40.8% with a good human label**. Unknown or weakly-named points are still created, but as **disabled-by-default diagnostic entities** so they never clutter your setup.
- Writable datapoints (setpoints, mode selectors, curve parameters, DHW boost, ...) are exposed as writable number/select/switch/text entities with exactly the same rules the bundle path uses. Destructive one-shot points (reset/factory/...) are created disabled-by-default.
- Owner/customer name and network configuration datapoints are only created as disabled-by-default diagnostic entities.
- Heating circuits are discovered generically (any number of circuits) and named with the names configured on your controller.

Existing installations are fully preserved: entity identity — unique IDs, entity IDs, devices — never changes and upgrading never duplicates anything; only friendly display names may update to your language. Existing bundle-based entries can additionally run **"Local catalog scan now"** from the integration options to add any datapoints the local catalog knows on top of their bundle (additions only). The cloud-bundle re-download remains available for entries that were created with a bundle.

#### Entity names and labels in your language (Deutsch / English)

The controller itself cannot localize: its enum states are internal tokens (`Comfort*Off*Red*Norm*...`), so the integration ships a label catalog and resolves display text itself:

- **German** names and enum labels come from the reference cloud bundle (the exact texts the OTS app shows German users), **English** ones from the OCHSNER app's own string resources. Names configured on your controller (heating-circuit names, plant model) always win regardless of language.
- **New (local-scan) entries follow the Home Assistant language.** A legacy entry created from a cloud bundle keeps the language stored with that bundle until you choose otherwise. The integration's **options** offer Deutsch / English / follow Home Assistant; a saved choice applies on reload — offline, no rescan — and identity never changes.
- Names re-resolve for all catalog datapoints, including bundle-created entities. Enum **labels** re-resolve where the raw controller tokens are known (all local-discovery enums); legacy bundle enums keep their original bundle labels — those are already proper display text, and guessing would be worse.
- A state the label catalog does not know keeps its raw controller token (never an empty or invented label), and every selectable numeric value stays selectable.

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
