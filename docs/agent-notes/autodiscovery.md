# Autodiscovery and dynamic addresses

Issue: https://github.com/permissionBRICK/OTS-HomeAssistant/issues/16
Branch: `feat/autodiscovery-dynamic-ip` (based independently on main).
The DHW setpoint fix is separate: `feat/dhw-setpoint-controls`, PR #17.

## Discovery contract and evidence

- Home Assistant loads custom integration DHCP matchers after installation and
  restart. The manifest uses `pol*`, `ochsner*`, `00A003*`, and registered device
  MACs as hints. A discovery flow reads Ochsner model + serial registers before
  offering a confirm-only card; a Siemens web server/banner alone is insufficient.
- `pol*` and `00A003*` are not Ochsner-specific or universal. Siemens's
  [Climatix IC getting-started guide](https://www.climatixic.com/documentation-html/pol/GettingStarted_EN/en-US/resources/012_ClimatixIC_GettingStarted_A6V101065428_en.pdf)
  shows POL648_EB4A89 with 00-A0-03-EB-4A-89. The live AIRHAWK controller uses
  POL688 and the same prefix. This corroborates a Climatix convention, not
  coverage of all Ochsner controllers. `ochsner*` is a hostname heuristic.
- Home Assistant cannot execute this integration's arbitrary subnet scan merely
  because HACS downloaded it, before HA loads a flow/entry. For unfamiliar
  DHCP hints, Add integration → Scan network starts the scan without IP input.
  See [HA network discovery](https://developers.home-assistant.io/docs/network_discovery/)
  and [DHCP manifests](https://developers.home-assistant.io/docs/creating_integration_manifest/#dhcp).
- Identification reads model `BCP0c9VVAAE=`, serial `BCOOVNVVAAE=`, and optional
  MAC `IgABAAAAAAA=`. Model and serial must contain actual non-placeholder
  values. Successful HTTP status or protocol state codes do not qualify.
- Automatic discovery uses default credentials/port; rediscovery uses stored
  credentials when the address or registered MAC matches. Recovery always uses
  the saved credentials/port. Manual setup remains available for overrides.

## Persistence and recovery

`host` is mutable connection data. `identity_key` is immutable:

- New controllers: `serial:<serial>`.
- Existing controllers: freeze their original host namespace so all existing
  unique IDs, device associations, option keys and write-counter storage survive.
  The parent device also gets the serial identifier and MAC connection.
- Existing saved serials are never replaced by a different device's identity.
  Entries without a serial learn it from their current working address; until
  then they retain their previous fixed-address behavior.

Full subnet scans are explicitly restricted (Christoph, 2026-09-05): only the
user's **Scan network** menu action, or recovery after a configured pump becomes
unavailable and quick discovery cannot find it. No periodic/background sweep
runs merely because the integration is installed or polling a healthy pump.

`SerialVerifiedApi` verifies identity before every read/write. After a failed
datapoint read it rechecks the current controller; if that identity still
responds, it retries the read without discovery. A failed identity check first
tries HA's cached DHCP address matching the controller's saved MAC, independent
of hostname/vendor prefix. This probes only that candidate, without enumerating
adapters/subnets. Missing MAC/cache, an unreachable candidate or a wrong serial
falls back to the bounded full scan. Quick recovery remains available during
the full-scan cooldown. Recovery requires a single matching
serial among the results, followed by another identity read at that address,
then persists the host without reloading entities. A shared lock serializes
scans across entries. Per-controller scan cooldown survives setup retries; it
runs for five minutes after scan completion/cancellation. The wrapper never
replays a failed write (the existing API's alternate endpoint casing behavior
is unchanged). A read/write lock prevents concurrent recovery from changing a
write's destination. This is device association checking, not cryptographic
authentication of the unencrypted local controller API.

Network scans use enabled RFC1918 IPv4 interfaces with prefixes /20 or smaller
subnets. Budget: 4096 hosts, eight workers, two-second wall timeout per probe.
An off-interface saved private IP gets a /24 fallback, prioritized over other
subnets. This helps a NAT VM reach a known routed LAN but cannot infer an
unknown LAN before initial setup. /24 takes at most roughly 64 seconds plus
scheduling; a full /20 can take roughly 17 minutes. Cancellation stops workers.
IPv6 discovery and unbounded scans of large/private routed networks are excluded.

Device registry configuration URLs are updated when the address changes.
DHCP rediscovery can update an existing entry and trigger reload immediately;
polling recovery swaps its connection in place. Internal address persistence
counts pending listener notifications so it cannot swallow the next options
change or create a reload loop. Local catalog rescans reject a changed serial.

## Verification (2026-09-05)

- Python 3.14.7, Home Assistant 2026.9.1: **103 passed, 21 skipped**, including real HA config
  entries/device registry, discovery confirmation/progress, and HTTP simulation.
- The HTTP simulation starts an old entry without a serial, learns identity,
  then puts another pump at its old IP. Coordinator polling finds the original
  serial at another IP; no value read goes to the replacement pump. Entity
  factories retain all unique IDs after unload/setup; the device is not duplicated.
- Negative tests cover missing/wrong/ambiguous serials, address reassignment
  between scan and adoption, no write replay, scan cancellation/concurrency,
  recovery throttling across setup retries, and options reload after recovery.
- Quick recovery tests cover exact saved-MAC matching with a non-Siemens prefix,
  no subnet enumeration, stale/missing/wrong-serial DHCP candidates, recovery
  during the full-scan cooldown, and no discovery for healthy controllers or
  isolated datapoint errors. The real HTTP/coordinator test covers both quick
  recovery and the full-scan fallback while preserving entities.
- Live read-only scan of Christoph's 192.168.178.0/24 found one AIRHAWK518C11A at
  192.168.178.80 in 60.2 seconds. A deliberately unreachable starting connection
  recovered to that verified serial and read heating/cooling setpoints of
  22.5, 17, 24 and 26 °C. No hardware settings or DHCP lease were changed.
  Hardware has no DHW circuit. Serial/MAC values are omitted from these notes.
- Live HA 2026.9.0 / HACS 2.0.5: installed the branch, restarted HA, and used
  the actual configuration-flow API behind the UI. Add integration → Scan
  network found the existing pump in 65.1 seconds; selecting it returned
  `already_configured`. All 441 original entity IDs, unique IDs and device
  associations survived. No entry was deleted/recreated. This validates the
  live flow/backend; it is not a browser-rendering test or an actual DHCP move.
- Actual router DHCP reassignment and the first-install automatic discovery
  card on an empty HA instance still need user acceptance testing.

Reproduce HA tests with Python supported by that HA release:

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python homeassistant==2026.9.1 pytest pytest-asyncio aiodhcpwatcher==1.2.7 aiodiscover==3.3.2 cached-ipaddress==1.1.2
.venv/bin/python -m pytest -q
```

The baseline test suite also contains tests skipped when old APK/bundle research
inputs are absent; those inputs are not required by the integration.

## HACS branch testing

HACS's ordinary version dropdown lists releases/default branch, not arbitrary
feature branches. HACS 2.0.5's update entity accepts an explicit `version` via
`update.install`, and passes it to its repository downloader as a branch ref.
Sources: [update entity](https://github.com/hacs/integration/blob/2.0.5/custom_components/hacs/update.py),
[repository downloader](https://github.com/hacs/integration/blob/2.0.5/custom_components/hacs/repositories/base.py).

1. Install this repository in HACS normally if it is not installed.
2. In Developer Tools → Actions, select **Update: Install** (`update.install`).
3. Select HACS's **Ochsner Local OTS** update entity as the target.
4. Enable the optional **Version** field and enter `feat/autodiscovery-dynamic-ip`.
5. Perform the action, then restart Home Assistant.
6. Check discovery cards, or Add integration → Ochsner Local OTS → Scan network.
   Existing configurations should retain their entities; no deletion/re-add is needed.
7. To test a DHCP move, record a few existing entity IDs, change the controller's
   reservation using the router's normal procedure, and let it obtain the new
   address. Allow the recovery scan to finish. Verify the same device/entities
   become available and the device's configuration link uses the new address.

For the independent DHW fix, use `feat/dhw-setpoint-controls` in step 4 and run
**Configure → Local catalog scan now** after restarting. Testing one branch
replaces the other branch's code; the two changes are independent PRs.

Return to a stable release with HACS → integration → Redownload, select the
release and restart HA. Older code does not understand `identity_key`: reverting
after creating serial-based entities or changing the address can create new
entity/device IDs. Take an HA backup before those tests and restore it for a
complete rollback. A code-only rollback retains the latest stored address but
does not retain this branch's dynamic recovery or identity handling.

## Testing while preserving the existing installation

Install the discovery branch and restart HA while retaining the existing
Ochsner entry. Let it finish loading so legacy identity metadata can be learned.

### Read-only discovery test

1. Open **Settings → Devices & services → Add integration → Ochsner Local OTS**.
2. Choose **Scan network**. The results deliberately include configured pumps.
3. Check that the model, serial and IP correspond to your existing pump.
4. Select it. **Already configured** is the expected successful outcome: the
   scanner identified it and the serial check prevented a duplicate entry.
5. Check your existing device/entity IDs. They should be unchanged. When the
   stored address/identity metadata already matches, this test does not reload
   the entry or change its options. You can also stop at the results list.

An automatic DHCP discovery card is deliberately suppressed for a configured
serial, including disabled entries. Disabling the entry does not turn it into
an unconfigured device. To test the exact first-install card, use a temporary
Home Assistant instance with its own empty configuration on the same LAN,
install this branch there, and restart that instance. Leave the production
configuration untouched. Stop at the discovered card; adoption is unnecessary
to verify that the automatic advertisement appears. A NAT-only test VM might
not receive the pump LAN's DHCP/discovery information.

### Address recovery test

Keep the existing entry. Record a few entity IDs and the parent device's URL.
Let the controller obtain a different address through the router's normal DHCP
procedure; do not change its address in the HA integration. Check that the same
entities recover and the parent device URL changes. DHCP events can update the
address immediately; otherwise polling failure tries the cached DHCP address
for the saved MAC first. Only a failed quick lookup triggers the subnet sweep.
A /24 sweep can take about a minute, followed by up to five minutes between
failed attempts. Restoring the original DHCP reservation can exercise recovery
in the opposite direction. This changes network availability temporarily, but
requires no deletion/recreation of entries or entities.
