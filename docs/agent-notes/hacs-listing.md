# HACS default-listing readiness

Rechecked 2026-09-06 for `permissionBRICK/OTS-HomeAssistant`, after publishing
v2.1.0 with PRs #17 and #18 merged.

Inclusion requested in [hacs/default #10695](https://github.com/hacs/default/pull/10695),
merged 2026-10-18 after the v2.1.2 brand-folder and v2.1.3 credential fixes.
The integration is now listed in the default HACS repository.

Sources:
- [Default-list inclusion requirements](https://www.hacs.xyz/docs/publish/include/)
- [Integration requirements](https://www.hacs.xyz/docs/publish/integration/)
- [Validation action](https://www.hacs.xyz/docs/publish/action/)
- [Submission template](https://github.com/hacs/default/blob/master/.github/PULL_REQUEST_TEMPLATE.md)

## Repository audit

| Requirement | Status |
| --- | --- |
| Public, active GitHub repository | Already satisfied |
| Description and enabled issues | Already satisfied |
| Repository topics | Added `climatix`, `hacs`, `heat-pump`, `home-assistant`, `local-control`, `ochsner` |
| One integration; runtime files within its component directory | Already satisfied |
| Root `hacs.json` with a name | Already satisfied |
| Required integration manifest fields | Already present; key order corrected for Hassfest |
| Brand icons | Already registered in [home-assistant/brands](https://github.com/home-assistant/brands/tree/master/custom_integrations/ochsner_local_ots) (`icon.png`, `icon@2x.png`) |
| HACS validation without ignored checks | Passed on release commit `d687e32`; no checks ignored |
| Hassfest validation | Passed on release commit `d687e32` |
| New release after successful validation | Full, non-prerelease v2.1.0 published after both jobs passed |
| Owner submission from a personal fork | `permissionBRICK/hacs-default`, branch `add-ochsner-local-ots` from upstream `master` |
| Editable PR and complete template | Verified `maintainer_can_modify: true`; all checklist and evidence links supplied |
| Alphabetical JSON entry | One-line addition only; upstream sorted check, JSON and repository-name schema passed locally |
| Entry in `hacs/default` | Merged in #10695 on 2026-10-18 |

Since v2.1.2 the integration also bundles `custom_components/ochsner_local_ots/brand/`
(`icon.png`, `icon@2x.png`, copied from the brands entry). The HACS validator
falls back to the central brands entry, but the #10695 reviewer required the
local folder. The local protocol has no country-specific
service restriction; a country filter is not applicable.

`.github/workflows/validate.yaml` runs both required validators for pushes,
pull requests, daily schedules and manual dispatches. There are no ignored
checks. Permissions are read-only; the HACS action does not post PR comments.
Action references are pinned, with Dependabot configured to update them.

## Submission evidence

- [Hassfest job](https://github.com/permissionBRICK/OTS-HomeAssistant/actions/runs/34045932062/job/101520852144)
  passed at 2026-09-06 16:35:47 UTC.
- [HACS job](https://github.com/permissionBRICK/OTS-HomeAssistant/actions/runs/34045932062/job/101520852307)
  passed at 2026-09-06 16:36:12 UTC.
- [Full v2.1.0 release](https://github.com/permissionBRICK/OTS-HomeAssistant/releases/tag/v2.1.0)
  was published at 2026-09-06 16:36:32 UTC, after both successful checks.
- The release manifest version is `2.1.0`, with one runtime integration in
  `custom_components/ochsner_local_ots`. This is a standalone custom integration,
  not a replacement or alpha/beta test of a Home Assistant Core integration.
- The current upstream integration, blacklist and removed lists contained no
  existing OTS-HomeAssistant entry; no duplicate open inclusion PR was found.
- Submission checkout: `/root/repos/hacs-default-submission`; `origin` is the
  personal fork and `upstream` is `hacs/default`. No reviewers were requested.

The integration need not become a Home Assistant Core integration. HACS default
listing is a separate submission/review process. Keep the validators enabled
so every release still passes the HACS and Hassfest checks.
