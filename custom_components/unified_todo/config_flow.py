"""Config and options flow for the Unified To-Do Aggregator."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import api
from .const import (
    ALL_SOURCES,
    ATTR_DESCRIPTION,
    ATTR_DESTINATION,
    ATTR_SOURCE,
    ATTR_SUMMARY,
    CONF_CLICKUP_ASSIGNED_ONLY,
    CONF_CLICKUP_DEFAULT_LIST_ID,
    CONF_CLICKUP_TEAM_ID,
    CONF_CLICKUP_TOKEN,
    CONF_DAY_OF_MONTH,
    CONF_DUE_OFFSET_DAYS,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_GITHUB_DEFAULT_REPO,
    CONF_GITHUB_FILTER,
    CONF_GITHUB_TOKEN,
    CONF_GOOGLE_CLIENT_ID,
    CONF_GOOGLE_CLIENT_SECRET,
    CONF_GOOGLE_DEFAULT_LIST,
    CONF_GOOGLE_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_TIME,
    CONF_WEEKDAYS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    FREQ_WEEKLY,
    FREQUENCIES,
    SOURCE_GITHUB,
    SOURCE_LABELS,
    SUBENTRY_RECURRING,
    WEEKDAYS,
)

_LOGGER = logging.getLogger(__name__)

# A GitHub create-target must look like ``owner/repo``.
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _credentials_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the credentials form schema, pre-filled with ``defaults``."""

    def default(key: str, fallback: Any = "") -> Any:
        return defaults.get(key, fallback)

    return vol.Schema(
        {
            # GitHub
            vol.Optional(CONF_GITHUB_TOKEN, default=default(CONF_GITHUB_TOKEN)): str,
            vol.Optional(CONF_GITHUB_FILTER, default=default(CONF_GITHUB_FILTER)): str,
            vol.Optional(
                CONF_GITHUB_DEFAULT_REPO, default=default(CONF_GITHUB_DEFAULT_REPO)
            ): str,
            # ClickUp
            vol.Optional(CONF_CLICKUP_TOKEN, default=default(CONF_CLICKUP_TOKEN)): str,
            vol.Optional(
                CONF_CLICKUP_TEAM_ID, default=default(CONF_CLICKUP_TEAM_ID)
            ): str,
            vol.Optional(
                CONF_CLICKUP_ASSIGNED_ONLY,
                default=default(CONF_CLICKUP_ASSIGNED_ONLY, True),
            ): bool,
            vol.Optional(
                CONF_CLICKUP_DEFAULT_LIST_ID,
                default=default(CONF_CLICKUP_DEFAULT_LIST_ID),
            ): str,
            # Google Tasks
            vol.Optional(
                CONF_GOOGLE_CLIENT_ID, default=default(CONF_GOOGLE_CLIENT_ID)
            ): str,
            vol.Optional(
                CONF_GOOGLE_CLIENT_SECRET, default=default(CONF_GOOGLE_CLIENT_SECRET)
            ): str,
            vol.Optional(
                CONF_GOOGLE_REFRESH_TOKEN,
                default=default(CONF_GOOGLE_REFRESH_TOKEN),
            ): str,
            vol.Optional(
                CONF_GOOGLE_DEFAULT_LIST,
                default=default(CONF_GOOGLE_DEFAULT_LIST),
            ): str,
            # Polling
            vol.Optional(
                CONF_SCAN_INTERVAL_MINUTES,
                default=default(
                    CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                ),
            ): vol.All(int, vol.Range(min=1, max=1440)),
        }
    )


async def _validate(hass, user_input: dict[str, Any]) -> dict[str, str]:
    """Validate the submitted credentials.

    Returns a mapping of ``field -> error code`` (empty when everything is OK).
    Only sources the user actually filled in are checked, and at least one
    source must be configured.
    """
    errors: dict[str, str] = {}
    session = async_get_clientsession(hass)

    github = bool(user_input.get(CONF_GITHUB_TOKEN))
    clickup = bool(user_input.get(CONF_CLICKUP_TOKEN))
    google = bool(
        user_input.get(CONF_GOOGLE_CLIENT_ID)
        or user_input.get(CONF_GOOGLE_CLIENT_SECRET)
        or user_input.get(CONF_GOOGLE_REFRESH_TOKEN)
    )

    if not (github or clickup or google):
        errors["base"] = "no_sources"
        return errors

    if github:
        try:
            await api.async_validate_github(session, user_input[CONF_GITHUB_TOKEN])
        except api.AuthError:
            errors[CONF_GITHUB_TOKEN] = "github_auth"
        except api.UnifiedTodoError:
            errors[CONF_GITHUB_TOKEN] = "cannot_connect"
        repo = user_input.get(CONF_GITHUB_DEFAULT_REPO)
        if repo and not _REPO_RE.match(repo):
            errors[CONF_GITHUB_DEFAULT_REPO] = "github_repo_invalid"

    if clickup:
        if not user_input.get(CONF_CLICKUP_TEAM_ID):
            errors[CONF_CLICKUP_TEAM_ID] = "clickup_team_required"
        else:
            try:
                await api.async_validate_clickup(
                    session,
                    user_input[CONF_CLICKUP_TOKEN],
                    str(user_input[CONF_CLICKUP_TEAM_ID]),
                )
            except api.AuthError:
                errors[CONF_CLICKUP_TOKEN] = "clickup_auth"
            except api.UnifiedTodoError:
                errors[CONF_CLICKUP_TOKEN] = "cannot_connect"

    if google:
        missing = [
            key
            for key in (
                CONF_GOOGLE_CLIENT_ID,
                CONF_GOOGLE_CLIENT_SECRET,
                CONF_GOOGLE_REFRESH_TOKEN,
            )
            if not user_input.get(key)
        ]
        if missing:
            for key in missing:
                errors[key] = "google_incomplete"
        else:
            try:
                await api.async_validate_google(
                    session,
                    user_input[CONF_GOOGLE_CLIENT_ID],
                    user_input[CONF_GOOGLE_CLIENT_SECRET],
                    user_input[CONF_GOOGLE_REFRESH_TOKEN],
                )
            except api.AuthError:
                errors[CONF_GOOGLE_REFRESH_TOKEN] = "google_auth"
            except api.UnifiedTodoError:
                errors[CONF_GOOGLE_REFRESH_TOKEN] = "cannot_connect"

    return errors


def _split_data_options(user_input: dict[str, Any]) -> tuple[dict, dict]:
    """Separate credential data from polling options, dropping blanks."""
    data = {
        key: value
        for key, value in user_input.items()
        if key != CONF_SCAN_INTERVAL_MINUTES and value not in ("", None)
    }
    options = {
        CONF_SCAN_INTERVAL_MINUTES: user_input.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
    }
    return data, options


class UnifiedTodoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial setup."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                data, options = _split_data_options(user_input)
                return self.async_create_entry(
                    title="Unified To-Do Aggregator",
                    data=data,
                    options=options,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input or {}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> UnifiedTodoOptionsFlow:
        return UnifiedTodoOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_RECURRING: RecurringTaskSubentryFlowHandler}


class UnifiedTodoOptionsFlow(OptionsFlow):
    """Allow editing credentials, filters and the poll interval after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(self.hass, user_input)
            if not errors:
                data, options = _split_data_options(user_input)
                # Update the credential data on the entry as well as options.
                self.hass.config_entries.async_update_entry(
                    self.config_entry, data=data
                )
                return self.async_create_entry(title="", data=options)

        # Pre-fill with the current effective configuration.
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=_credentials_schema(current),
            errors=errors,
        )


# ---------------------------------------------------------------------------
# Recurring task subentries
# ---------------------------------------------------------------------------


def _recurring_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build the add/edit form for a recurring task rule."""

    def d(key: str, fallback: Any) -> Any:
        return defaults.get(key, fallback)

    source_options = [
        selector.SelectOptionDict(value=s, label=SOURCE_LABELS.get(s, s))
        for s in ALL_SOURCES
    ]
    freq_options = [selector.SelectOptionDict(value=f, label=f) for f in FREQUENCIES]
    weekday_options = [selector.SelectOptionDict(value=w, label=w) for w in WEEKDAYS]

    return vol.Schema(
        {
            vol.Required(ATTR_SOURCE, default=d(ATTR_SOURCE, SOURCE_GITHUB)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(options=source_options)
                )
            ),
            vol.Required(ATTR_SUMMARY, default=d(ATTR_SUMMARY, "")): (
                selector.TextSelector()
            ),
            vol.Optional(ATTR_DESCRIPTION, default=d(ATTR_DESCRIPTION, "")): (
                selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                )
            ),
            vol.Optional(ATTR_DESTINATION, default=d(ATTR_DESTINATION, "")): (
                selector.TextSelector()
            ),
            vol.Required(CONF_FREQUENCY, default=d(CONF_FREQUENCY, FREQ_WEEKLY)): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=freq_options, translation_key="frequency"
                    )
                )
            ),
            vol.Required(CONF_TIME, default=d(CONF_TIME, "09:00")): (
                selector.TimeSelector()
            ),
            vol.Optional(CONF_WEEKDAYS, default=d(CONF_WEEKDAYS, ["mon"])): (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=weekday_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="weekdays",
                    )
                )
            ),
            vol.Optional(CONF_DAY_OF_MONTH, default=d(CONF_DAY_OF_MONTH, 1)): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=31, mode=selector.NumberSelectorMode.BOX
                    )
                )
            ),
            vol.Optional(CONF_DUE_OFFSET_DAYS, default=d(CONF_DUE_OFFSET_DAYS, 0)): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=365, mode=selector.NumberSelectorMode.BOX
                    )
                )
            ),
            vol.Required(CONF_ENABLED, default=d(CONF_ENABLED, True)): (
                selector.BooleanSelector()
            ),
        }
    )


def _clean_recurring(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalise a submitted recurring form into stored subentry data."""
    data = dict(user_input)
    for key in (ATTR_DESCRIPTION, ATTR_DESTINATION):
        if not (data.get(key) or "").strip():
            data.pop(key, None)
        elif isinstance(data.get(key), str):
            data[key] = data[key].strip()
    for key in (CONF_DAY_OF_MONTH, CONF_DUE_OFFSET_DAYS):
        if data.get(key) is not None:
            data[key] = int(data[key])
    if data.get(CONF_TIME):
        # TimeSelector yields HH:MM:SS; keep HH:MM.
        data[CONF_TIME] = str(data[CONF_TIME])[:5]
    if data.get(ATTR_SUMMARY):
        data[ATTR_SUMMARY] = data[ATTR_SUMMARY].strip()
    return data


def _validate_recurring(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not (data.get(ATTR_SUMMARY) or "").strip():
        errors[ATTR_SUMMARY] = "summary_required"
    if data.get(CONF_FREQUENCY) == FREQ_WEEKLY and not data.get(CONF_WEEKDAYS):
        errors[CONF_WEEKDAYS] = "weekdays_required"
    dest = data.get(ATTR_DESTINATION)
    if data.get(ATTR_SOURCE) == SOURCE_GITHUB and dest and not _REPO_RE.match(dest):
        errors[ATTR_DESTINATION] = "github_repo_invalid"
    return errors


class RecurringTaskSubentryFlowHandler(ConfigSubentryFlow):
    """Add or edit a single recurring task rule."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=False)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        return await self._async_form(user_input, reconfigure=True)

    async def _async_form(
        self, user_input: dict[str, Any] | None, reconfigure: bool
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if reconfigure and user_input is None:
            defaults = dict(self._get_reconfigure_subentry().data)
        else:
            defaults = user_input or {}

        if user_input is not None:
            data = _clean_recurring(user_input)
            errors = _validate_recurring(data)
            if not errors:
                title = data[ATTR_SUMMARY]
                if reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=title,
                        data=data,
                    )
                return self.async_create_entry(title=title, data=data)
            defaults = user_input

        return self.async_show_form(
            step_id="reconfigure" if reconfigure else "user",
            data_schema=_recurring_schema(defaults),
            errors=errors,
        )
