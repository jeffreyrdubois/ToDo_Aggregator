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
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import api
from .const import (
    CONF_CLICKUP_ASSIGNED_ONLY,
    CONF_CLICKUP_DEFAULT_LIST_ID,
    CONF_CLICKUP_TEAM_ID,
    CONF_CLICKUP_TOKEN,
    CONF_GITHUB_DEFAULT_REPO,
    CONF_GITHUB_FILTER,
    CONF_GITHUB_TOKEN,
    CONF_GOOGLE_CLIENT_ID,
    CONF_GOOGLE_CLIENT_SECRET,
    CONF_GOOGLE_DEFAULT_LIST,
    CONF_GOOGLE_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
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
