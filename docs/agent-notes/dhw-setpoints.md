# DHW setpoints: Smart app write bindings

Investigated 2026-09-05 following [Home Assistant forum post 58](https://community.home-assistant.io/t/integration-of-heatpump-ochsner-tronic-smart-services-ots-via-modbus/672974/58).

## Cause and correction

The accountless catalog defaulted APK-only datapoints to `sensor`. It obtained
write bindings and numeric limits from a reference installation without a
domestic hot-water circuit, so that reference could not supply DHW metadata.
The four reported controls are present in the Smart app's settings and write
to their read addresses. The catalog and builder now include those explicit
bindings. A name or an object type alone is not evidence of writability.

| Setting | Read and write OA | App range (°C) | Step (°C) |
| --- | --- | --- | --- |
| Comfort | `ASMv1XXZAAE=` | 5–75 | 0.5 |
| Eco | `ASOp1XXZAAE=` | 5–75 | 0.5 |
| Reduced | `ASOXCHXZAAE=` | 5–75 | 0.5 |
| Boost | `ASPuhXXZAAE=` | 40–75 | 0.5 |

These are app UI ranges, not a measurement of a particular controller's
accepted limits. There is no scaling: writes carry a decimal Celsius value.
The calculated temperatures `CiOhSHXZAAE=`, `CiPvuXXZAAE=`, and
`CiMzqnXZAAE=` remain sensors. Other APK-only settings need their own evidence.

After installing this change and restarting Home Assistant, existing users
can select **Local catalog scan now (accountless; add missing entities)** in
the integration options. The existing additive merge adds four `number`
entities where the controller answers with values, preserving previous
sensor entities and their identities. New installations get the number
entities directly. Controllers without DHW do not acquire these controls.

## APK evidence and reproduction

Downloaded from APKPure's `https://d.apkpure.net/b/APK/com.ochsner.app?version=latest`.
Despite its filename, the download is an XAPK ZIP containing a base APK and
configuration APKs. Its manifest identifies **V1.2.0, version code 1785**,
the same version referenced by the original catalog.

- Archive SHA-256: `ffc257e2cc8172f5057e7e6dc9c7e6b10ab2964962e3d5342f92f9d1b15b301c`
- Base `com.ochsner.app.apk` SHA-256: `f4077876b7de6b938ca1b1331d93c3d6b45cb7c228e6060f99e0985d60331a43`
- Decompiler: JADX 1.5.6. Default decompilation fails for important enums and
  a large settings conversion method; use `--decompilation-mode simple` for
  `--single-class H7.Q0` and `--single-class c7.z`. Do not trust the default
  enum output: it loses the address constructor argument.

Evidence chain in that APK (obfuscated names may change between releases):

1. `H7/P0.java`: reads the four OA strings and deserializes each as `J7.X3`
   (Celsius). `H7/O0.java` identifies the four corresponding DTO fields.
2. `H7/Q0.java`, simple mode: maps `baseSetPointComfort`, `baseSetPointEco`,
   `baseSetPointReduced`, and `baseSetPointBoost` to the four OA strings.
   Its `i` field stores the address, not the enum name.
3. `p032c7/z.java` (`c7.z`), simple mode, `H7.O0` branch: constructs
   `X7.x0` settings with `X7.Y` temperature ranges of 5–75 / 40–75 and
   precision 0.5. `Z7.a` is the Celsius temperature type. Settings are gated
   by hot-water availability and the corresponding value being present.
4. `p109j8/b.java`: the `X7.x0` write branch passes the setting value and
   `Q0.i` to its generic write builder. The `X7.Y` branch serializes the raw
   temperature through `Z7.d.c()` without multiplying by ten.
5. `M7/w.java`: formats the address and numeric value as `address;value`.

The extracted artifacts are temporary under `/tmp/ots-investigation/` on
the investigation VM. They are not required by the integration. The original
`apk_files/` reference bundles and catalog research reports were not present
on this replacement VM, so a complete catalog rebuild could not be run.
The shipped catalog was updated using the same new builder helper, with its
content hash and writable-point statistic recomputed.

## Local hardware and validation

Christoph supplied controller address **192.168.178.80**. It is reachable
directly from this VM; the old VM's proxy/tunnel is unnecessary. Controller:
**AIRHAWK518C11A, firmware v3.3.20**. Christoph confirmed that it has **no DHW
circuit**, so it cannot validate actual DHW writes.

Read-only queries returned heating-circuit base temperatures of 22.5 °C
(standard heating), 17 °C (reduced heating), 24 °C (standard cooling), and
26 °C (reduced cooling). The four DHW addresses returned only state code 5,
with no values. No controller writes were sent.

Regression coverage checks packaged metadata against the transcribed app
bindings and the builder, number creation, additive/idempotent rescanning
beside existing sensors, and omission on a controller without DHW.

Validation result: **58 tests passed, 23 skipped** (two Home Assistant test
modules need Home Assistant installed; the remaining skips need the old
reference installation/research inputs). A full live read-only sweep covered
581 catalog addresses and returned 412 readable values. Building entities
from that scan with the old and corrected catalogs produced identical
configurations for this non-DHW plant (251 sensors, 106 numbers, 39 selects,
9 texts, 7 switches). Actual DHW writes remain untested on hardware.
