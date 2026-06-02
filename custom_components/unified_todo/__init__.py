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
from homeassistant.helpers.typing import ConfigType

from .api import UnifiedTodoError
from .const import (
    ALL_SOURCES,
    ATTR_DESCRIPTION,
    ATTR_DESTINATION,
    ATTR_DUE_DATE,
    ATTR_RULE_ID,
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
    SERVICE_DELETE_RECURRING,
    SERVICE_LIST_DESTINATIONS,
    SERVICE_LIST_RECURRING,
    SERVICE_UPDATE_RECURRING,
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


UPDATE_RECURRING_SCHEMA = ADD_RECURRING_SCHEMA.extend(
    {vol.Required(ATTR_RULE_ID): cv.string}
)

DELETE_RECURRING_SCHEMA = vol.Schema({vol.Required(ATTR_RULE_ID): cv.string})

LIST_RECURRING_SCHEMA = vol.Schema({})


def _build_recurring_data(call: ServiceCall) -> dict:
    """Turn an add/update_recurring_task call into stored subentry data."""
    data: dict = {
        ATTR_SOURCE: call.data[ATTR_SOURCE],
        ATTR_SUMMARY: call.data[ATTR_SUMMARY].strip(),
        CONF_FREQUENCY: call.data[CONF_FREQUENCY],
        CONF_TIME: str(call.data[CONF_TIME])[:5],
        CONF_DAY_OF_MONTH: call.data[CONF_DAY_OF_MONTH],
        CONF_DUE_OFFSET_DAYS: call.data[CONF_DUE_OFFSET_DAYS],
        CONF_ENABLED: call.data[CONF_ENABLED],
    }
    if desc := (call.data.get(ATTR_DESCRIPTION) or "").strip():
        data[ATTR_DESCRIPTION] = desc
    if dest := (call.data.get(ATTR_DESTINATION) or "").strip():
        data[ATTR_DESTINATION] = dest
    if call.data.get(CONF_WEEKDAYS):
        data[CONF_WEEKDAYS] = call.data[CONF_WEEKDAYS]
    return data


def _rule_to_dict(subentry_id: str, data) -> dict:
    """Serialise a recurring subentry into a frontend-friendly rule dict."""
    return {
        "id": subentry_id,
        ATTR_SOURCE: data.get(ATTR_SOURCE),
        ATTR_SUMMARY: data.get(ATTR_SUMMARY),
        ATTR_DESCRIPTION: data.get(ATTR_DESCRIPTION),
        ATTR_DESTINATION: data.get(ATTR_DESTINATION),
        CONF_FREQUENCY: data.get(CONF_FREQUENCY),
        CONF_TIME: data.get(CONF_TIME),
        CONF_WEEKDAYS: list(data.get(CONF_WEEKDAYS) or []),
        CONF_DAY_OF_MONTH: data.get(CONF_DAY_OF_MONTH),
        CONF_DUE_OFFSET_DAYS: data.get(CONF_DUE_OFFSET_DAYS),
        CONF_ENABLED: data.get(CONF_ENABLED, True),
    }


def _only_entry(hass: HomeAssistant) -> ConfigEntry:
    """Return the single config entry (works even mid-reload)."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise HomeAssistantError("Unified To-Do Aggregator is not set up")
    return entries[0]


def _recurring_subentries(entry: ConfigEntry) -> dict:
    """Map of subentry_id -> ConfigSubentry for recurring rules only."""
    return {
        sid: sub
        for sid, sub in entry.subentries.items()
        if sub.subentry_type == SUBENTRY_RECURRING
    }


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

    def _check_weekly(data: dict) -> None:
        if data[CONF_FREQUENCY] == FREQ_WEEKLY and not data.get(CONF_WEEKDAYS):
            raise HomeAssistantError(
                "Pick at least one weekday for a weekly schedule."
            )

    async def _handle_add_recurring(call: ServiceCall) -> None:
        entry = _only_entry(hass)
        data = _build_recurring_data(call)
        _check_weekly(data)
        subentry = ConfigSubentry(
            data=MappingProxyType(data),
            subentry_type=SUBENTRY_RECURRING,
            title=data[ATTR_SUMMARY],
            unique_id=None,
        )
        # Adding the subentry fires the entry's update listeners, which reloads
        # the entry and (re)schedules the new rule.
        hass.config_entries.async_add_subentry(entry, subentry)

    async def _handle_list_recurring(call: ServiceCall) -> ServiceResponse:
        entry = _only_entry(hass)
        rules = [
            _rule_to_dict(sid, sub.data)
            for sid, sub in _recurring_subentries(entry).items()
        ]
        return {"rules": rules}

    async def _handle_update_recurring(call: ServiceCall) -> None:
        entry = _only_entry(hass)
        rule_id = call.data[ATTR_RULE_ID]
        subentry = _recurring_subentries(entry).get(rule_id)
        if subentry is None:
            raise HomeAssistantError(f"Unknown recurring rule '{rule_id}'")
        data = _build_recurring_data(call)
        _check_weekly(data)
        hass.config_entries.async_update_subentry(
            entry, subentry, data=MappingProxyType(data), title=data[ATTR_SUMMARY]
        )

    async def _handle_delete_recurring(call: ServiceCall) -> None:
        entry = _only_entry(hass)
        rule_id = call.data[ATTR_RULE_ID]
        if rule_id not in _recurring_subentries(entry):
            raise HomeAssistantError(f"Unknown recurring rule '{rule_id}'")
        hass.config_entries.async_remove_subentry(entry, rule_id)

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
    hass.services.async_register(
        DOMAIN,
        SERVICE_LIST_RECURRING,
        _handle_list_recurring,
        schema=LIST_RECURRING_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_RECURRING,
        _handle_update_recurring,
        schema=UPDATE_RECURRING_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_DELETE_RECURRING,
        _handle_delete_recurring,
        schema=DELETE_RECURRING_SCHEMA,
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


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services once, before any entry is set up.

    Registering here (rather than per entry) keeps the services available across
    config-entry reloads — which happen every time a recurring-task subentry
    changes — so the card never hits a momentarily-missing service.
    """
    await _async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Unified To-Do Aggregator from a config entry."""
    coordinator = UnifiedTodoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
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
    """Unload a config entry.

    Services are registered in ``async_setup`` (integration lifetime), so they
    are intentionally left in place across entry reloads.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
