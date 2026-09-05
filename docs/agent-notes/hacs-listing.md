# HACS default-listing readiness

Checked 2026-09-05 for `permissionBRICK/OTS-HomeAssistant`, alongside PR #18.

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
| HACS validation without ignored checks | Workflow added in PR #18; inspect its run before release |
| Hassfest validation | Workflow added in PR #18; official container passes locally |
| New release after successful validation | Pending merge, green Actions runs, and release |
| Entry in `hacs/default` | Not present; submit after the release prerequisite is met |

Current HACS also accepts bundled `custom_components/ochsner_local_ots/brand/icon.png`.
Its validator falls back to the existing central brands entry, so new artwork
or a new brands PR is unnecessary. The local protocol has no country-specific
service restriction; a country filter is not applicable.

`.github/workflows/validate.yaml` runs both required validators for pushes,
pull requests, daily schedules and manual dispatches. There are no ignored
checks. Permissions are read-only; the HACS action does not post PR comments.
Action references are pinned, with Dependabot configured to update them.

## Steps before submitting

1. Merge the reviewed changes, then confirm **both** HACS and Hassfest pass on
   the release commit on `main`. Retain links to the successful runs.
2. Bump the integration's manifest version for the intended release, validate
   that commit, and publish a full GitHub release from it. An old release or
   a tag alone does not satisfy the new-release-after-validation requirement.
3. From a personal fork of `hacs/default`, create a feature branch from its
   current `master`. Add `permissionBRICK/OTS-HomeAssistant` alphabetically to
   the JSON array in `integration`.
4. Open the inclusion PR as the owner/major contributor with maintainer edits
   enabled. Complete the current template accurately, including the release
   URL and successful HACS/Hassfest run links. Do not request reviewers.

The integration need not become a Home Assistant Core integration. HACS default
listing is a separate submission/review process. No release or default-list
submission is made as part of this preparation PR.
