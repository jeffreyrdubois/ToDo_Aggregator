"""The Unified To-Do Aggregator integration.

Aggregates open tasks from Google Tasks, GitHub Issues and ClickUp into a
single set of Home Assistant sensors and To-do lists. Reading is the core job;
creating tasks and marking them complete are offered on top via native To-do
list entities and two services.
"""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import UnifiedTodoError
from .const import (
    ALL_SOURCES,
    ATTR_DESCRIPTION,
    ATTR_DUE_DATE,
    ATTR_SOURCE,
    ATTR_SUMMARY,
    ATTR_TASK_ID,
    DOMAIN,
    SERVICE_COMPLETE_TASK,
    SERVICE_CREATE_TASK,
)
from .coordinator import UnifiedTodoCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

CREATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
        vol.Required(ATTR_SUMMARY): cv.string,
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Optional(ATTR_DUE_DATE): cv.date,
    }
)

COMPLETE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
        vol.Required(ATTR_TASK_ID): cv.string,
    }
)


def _only_coordinator(hass: HomeAssistant) -> UnifiedTodoCoordinator:
    """Return the single config entry's coordinator (single_config_entry)."""
    coordinators = [
        obj
        for obj in hass.data.get(DOMAIN, {}).values()
        if isinstance(obj, UnifiedTodoCoordinator)
    ]
    if not coordinators:
        raise HomeAssistantError("Unified To-Do Aggregator is not set up")
    return coordinators[0]


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register the create/complete services once for the integration."""
    if hass.services.has_service(DOMAIN, SERVICE_CREATE_TASK):
        return

    async def _handle_create(call: ServiceCall) -> None:
        coordinator = _only_coordinator(hass)
        due = call.data.get(ATTR_DUE_DATE)
        try:
            await coordinator.async_create_task(
                call.data[ATTR_SOURCE],
                call.data[ATTR_SUMMARY],
                description=call.data.get(ATTR_DESCRIPTION),
                due_date=due.isoformat() if due else None,
            )
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_complete(call: ServiceCall) -> None:
        coordinator = _only_coordinator(hass)
        try:
            await coordinator.async_complete_task(
                call.data[ATTR_SOURCE], call.data[ATTR_TASK_ID]
            )
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_TASK, _handle_create, schema=CREATE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_TASK, _handle_complete, schema=COMPLETE_TASK_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Unified To-Do Aggregator from a config entry."""
    coordinator = UnifiedTodoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_services(hass)

    # Reload when the user edits options so the new scan interval / sources
    # take effect immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        # Remove the integration-wide services once the last entry is gone.
        if not any(
            isinstance(obj, UnifiedTodoCoordinator)
            for obj in hass.data.get(DOMAIN, {}).values()
        ):
            hass.services.async_remove(DOMAIN, SERVICE_CREATE_TASK)
            hass.services.async_remove(DOMAIN, SERVICE_COMPLETE_TASK)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
