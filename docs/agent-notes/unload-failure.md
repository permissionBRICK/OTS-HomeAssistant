# Failed to unload after branch testing

Source: [forum post 60](https://community.home-assistant.io/t/integration-of-heatpump-ochsner-tronic-smart-services-ots-via-modbus/672974/60),
2026-09-05. The screenshot shows `Failed to unload`, not a HACS download error.
The screenshot does not include the underlying traceback.

## Reproduction and fix

A real Home Assistant `ConfigEntry.async_unload` forwarding test reproduces
failure for an entry that only loaded sensors (or sensors and numbers), while
other integrations loaded the remaining platform domains globally.

The integration previously forwarded unloads to all six platforms. A platform
never loaded for this entry raises `ValueError("Config entry was never loaded!")`.
HA catches that error inside its forwarding wrapper and returns `False`, so the
integration's outer `except ValueError` is ineffective. The overall entry then
reports `Failed to unload`.

Both PR #17 and PR #18 now record which platforms were forwarded at setup and
unload exactly that list with HA's standard helper. The list reflects the
running entry, so a saved catalog rescan cannot change which platforms should
be unloaded. A fallback derives the list from an older runtime. Session cleanup
still requires a successful unload; real unload errors are not suppressed.

`tests/test_unload.py` uses HA's real forwarding wrapper rather than mocking it
as always successful. It fails on the old implementation and passes after the
fix for both recorded-platform and older-runtime cases.

## Live verification

On the maintainer's HA 2026.9.0 / HACS 2.0.5 installation, downloading the original
DHW branch via `update.install`, restarting HA, and submitting the local catalog
rescan succeeded. All 441 existing entity IDs, unique IDs and device associations
were retained. That older bundle-based entry loads all six platforms, so it does
not exhibit the missing-platform failure. No heat-pump settings were written.
A configuration backup was created before testing.

The forum user's traceback is still needed to confirm this is their exact cause;
the screenshot alone only establishes the failed-unload state.

## Recovery instructions

1. Keep the existing Ochsner entry and its entities.
2. Download the latest version of the intended feature branch.
3. Perform a **full Home Assistant restart** before requesting another catalog
   rescan. Reloading an entry already in `Failed to unload` is insufficient to
   replace the old Python code in memory.
4. Once the entry is loaded, run **Configure → Local catalog scan now** if
   testing the DHW controls. The discovery branch instead offers **Add
   integration → Scan network**; selecting the existing pump should report
   **Already configured**.

HACS 2.0.5's `update.install` rejects an explicit version string equal to the
installed one, even when new commits exist on that branch. To refresh the same
branch through this action, download `v2.0.0`, then the feature branch again,
**without restarting between downloads**. Restart only after the desired branch
has finished downloading. HACS's `hacs/repository/download` WebSocket command,
used by its download dialog, also accepts a branch and does not impose that
same-version check. Do not delete the integration or recreate its entities.
