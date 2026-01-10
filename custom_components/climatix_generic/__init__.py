from __future__ import annotations

from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

DOMAIN = "climatix_generic"
NEW_DOMAIN = "ochsner_local_ots"


async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    # This legacy shim exists so that upgrades from the old domain can show a Repairs warning
    # even before the user adds the new integration.
    if isinstance(config, dict) and DOMAIN in config:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "legacy_yaml_domain",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="legacy_yaml_domain",
            translation_placeholders={
                "old_domain": DOMAIN,
                "new_domain": NEW_DOMAIN,
            },
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:  # type: ignore[no-untyped-def]
    # If a config entry from the legacy domain still exists, show a repair so users can migrate.
    ir.async_create_issue(
        hass,
        DOMAIN,
        "legacy_config_entry",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="legacy_config_entry",
        translation_placeholders={
            "old_domain": DOMAIN,
            "new_domain": NEW_DOMAIN,
        },
    )
    return True
