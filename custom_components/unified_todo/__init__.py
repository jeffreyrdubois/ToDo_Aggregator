"""The Unified To-Do Aggregator integration.

Aggregates open tasks from Google Tasks, GitHub Issues and ClickUp into a
single set of Home Assistant sensors and To-do lists. Reading is the core job;
creating tasks and marking them complete are offered on top via native To-do
list entities and two services.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import MappingProxyType

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api import UnifiedTodoError
from .const import (
    ALL_SOURCES,
    ATTR_DESCRIPTION,
    ATTR_DESTINATION,
    ATTR_DUE_DATE,
    ATTR_SOURCE,
    ATTR_SUMMARY,
    ATTR_TASK_ID,
    CONF_DAY_OF_MONTH,
    CONF_DUE_OFFSET_DAYS,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_TIME,
    CONF_WEEKDAYS,
    DOMAIN,
    FREQ_WEEKLY,
    FREQUENCIES,
    SERVICE_ADD_RECURRING,
    SERVICE_COMPLETE_TASK,
    SERVICE_CREATE_TASK,
    SERVICE_LIST_DESTINATIONS,
    SUBENTRY_RECURRING,
    WEEKDAYS,
)
from .coordinator import UnifiedTodoCoordinator
from .recurring import RecurringScheduler

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

# Custom Lovelace card served straight from the integration folder.
CARD_FILENAME = "unified-todo-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"

CREATE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
        vol.Required(ATTR_SUMMARY): cv.string,
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Optional(ATTR_DUE_DATE): cv.date,
        vol.Optional(ATTR_DESTINATION): cv.string,
    }
)

COMPLETE_TASK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
        vol.Required(ATTR_TASK_ID): cv.string,
    }
)

LIST_DESTINATIONS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
    }
)

ADD_RECURRING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_SOURCE): vol.In(ALL_SOURCES),
        vol.Required(ATTR_SUMMARY): cv.string,
        vol.Optional(ATTR_DESCRIPTION): cv.string,
        vol.Optional(ATTR_DESTINATION): cv.string,
        vol.Optional(CONF_FREQUENCY, default=FREQ_WEEKLY): vol.In(FREQUENCIES),
        vol.Optional(CONF_TIME, default="09:00"): cv.string,
        vol.Optional(CONF_WEEKDAYS, default=list): vol.All(
            cv.ensure_list, [vol.In(WEEKDAYS)]
        ),
        vol.Optional(CONF_DAY_OF_MONTH, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=31)
        ),
        vol.Optional(CONF_DUE_OFFSET_DAYS, default=0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=365)
        ),
        vol.Optional(CONF_ENABLED, default=True): cv.boolean,
    }
)


def _build_recurring_data(call: ServiceCall) -> dict:
    """Turn an add_recurring_task call into stored subentry data."""
    data: dict = {
        ATTR_SOURCE: call.data[ATTR_SOURCE],
        ATTR_SUMMARY: call.data[ATTR_SUMMARY].strip(),
        CONF_FREQUENCY: call.data[CONF_FREQUENCY],
        CONF_TIME: str(call.data[CONF_TIME])[:5],
        CONF_DAY_OF_MONTH: call.data[CONF_DAY_OF_MONTH],
        CONF_DUE_OFFSET_DAYS: call.data[CONF_DUE_OFFSET_DAYS],
        CONF_ENABLED: call.data[CONF_ENABLED],
    }
    if (desc := (call.data.get(ATTR_DESCRIPTION) or "").strip()):
        data[ATTR_DESCRIPTION] = desc
    if (dest := (call.data.get(ATTR_DESTINATION) or "").strip()):
        data[ATTR_DESTINATION] = dest
    if call.data.get(CONF_WEEKDAYS):
        data[CONF_WEEKDAYS] = call.data[CONF_WEEKDAYS]
    return data


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
                destination=call.data.get(ATTR_DESTINATION),
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

    async def _handle_list_destinations(call: ServiceCall) -> ServiceResponse:
        coordinator = _only_coordinator(hass)
        try:
            return await coordinator.async_list_destinations(call.data[ATTR_SOURCE])
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err

    async def _handle_add_recurring(call: ServiceCall) -> None:
        coordinator = _only_coordinator(hass)
        data = _build_recurring_data(call)
        if data[CONF_FREQUENCY] == FREQ_WEEKLY and not data.get(CONF_WEEKDAYS):
            raise HomeAssistantError(
                "Pick at least one weekday for a weekly schedule."
            )
        subentry = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=SUBENTRY_RECURRING,
            title=data[ATTR_SUMMARY],
            unique_id=None,
        )
        # Adding the subentry fires the entry's update listeners, which reloads
        # the entry and (re)schedules the new rule.
        hass.config_entries.async_add_subentry(coordinator.entry, subentry)

    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_TASK, _handle_create, schema=CREATE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_COMPLETE_TASK, _handle_complete, schema=COMPLETE_TASK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_DESTINATIONS,
        _handle_list_destinations,
        schema=LIST_DESTINATIONS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ADD_RECURRING, _handle_add_recurring, schema=ADD_RECURRING_SCHEMA
    )


_CARD_REGISTERED = "_card_registered"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the custom Lovelace card and load it on the frontend (once)."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_CARD_REGISTERED):
        return
    card_path = Path(__file__).parent / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), False)]
    )
    # Append a cache-busting query so the frontend picks up new versions.
    add_extra_js_url(hass, f"{CARD_URL}?v={_card_version()}")
    domain_data[_CARD_REGISTERED] = True


def _card_version() -> str:
    """Use the card file's mtime as a cheap cache-buster."""
    try:
        return str(int((Path(__file__).parent / CARD_FILENAME).stat().st_mtime))
    except OSError:
        return "0"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Unified To-Do Aggregator from a config entry."""
    coordinator = UnifiedTodoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_register_services(hass)
    await _async_register_card(hass)

    # Schedule any recurring-task rules (stored as subentries).
    scheduler = RecurringScheduler(hass, entry, coordinator)
    scheduler.async_setup()
    entry.async_on_unload(scheduler.async_unload)

    # Reload when the user edits options or adds/edits a recurring-task
    # subentry, so the new scan interval / sources / schedules take effect.
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
            hass.services.async_remove(DOMAIN, SERVICE_LIST_DESTINATIONS)
            hass.services.async_remove(DOMAIN, SERVICE_ADD_RECURRING)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
