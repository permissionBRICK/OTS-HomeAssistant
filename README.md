# Ochsner local OTS - Climatix - (HACS)

**What the hell is this repo?**

It's a way to locally read and control Heatpump settings! All without Modbus or Cloud services required!

**How does it work?**

Instead of interacting with the Heatpump over the Interface that is offered via ModbusTCP which is very undocumented and allows you to only read some values and control almost nothing, this one uses the JSON Interface that the OTS App itself uses to communicate with the heatpump (which is even less documented since it is entirely reverse engineered), except it runs entirely locally!

The issue is that in order to find the IDs that the local api needs in order to be able to know which values you want to write/read, you need to download and parse the configuration from the Ochsner cloud.

Luckily, the newest version does all of this automatically, so the python tool is no longer needed!

This has been tested and confirmed working so far with:
 - AirHawk 518C
 - AirHawk 208C

However, it is plausible that it could work with any Ochsner Heat Pump that uses the OTS app.

---


## 1) Home Assistant: install the integration
### 1a) Install via HACS - recommended

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

### 2) Add the integration into Home Assistant (New Automatic process via UI)

Steps:
1) Add a new integration via the HA UI
2) Search for Ochsner Local OTS
3) Enter your Ochsner OTS Credentials (they are not stored, they are just needed to retrieve the configuration from the cloud once)
4) Select which Heatpump you want to add (in case you have multiple)
5) Enter your local Heatpump IP Address (this is displayed inside your Heatpump settings. Make sure to assign it a fixed IP address in your internet router.)
6) Enter a Name for your Heatpump
7) Click finish - This could take a few seconds while all the available values are automatically scanned and then imported.
8) Done!




---

## Disclaimer & Warning

Tread with caution when writing random values, make sure you know the exact value you are trying to change and have correctly identified it. While there seem to be some limits and safeguards to writing certain implausible values, I have no idea how much of those are implemented in the UI of the app vs actually being verified by the backend.

Also of note: The settings are saved inside the Siemens Climatix Controller inside the heatpump and that uses Flash storage - which means it has a limited number of erase cycles before the chip fails. Siemens documentation seems to claim this is at 100k (100,000) write cycles. Therefore, if you plan not to just make controls available to the UI but also automate some settings, calculate a rough estimate of how many write cycles this would cause in the worst case and ensure that over the lifetime of your heatpump you stay below that total number of writes. For example: an average of 10 writes per day would last you 30 years for the rated lifetime of the flash chip. But, of course if you only control the heatpump for the most expensive 4 months of the year, you get up to 30 writes for each of those days.

This was entirely reverse engineered from the app using AI. Technically it should be possible to properly redevelop this as an integration that just accepts ochsner credentials and automatically imports all available values into Home assistant, but I do have a full time job. If anyone wants to make a PR, feel free!
