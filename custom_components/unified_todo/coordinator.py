"""Data update coordinator for the Unified To-Do Aggregator."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import api
from .const import (
    CONF_CLICKUP_ASSIGNED_ONLY,
    CONF_CLICKUP_TEAM_ID,
    CONF_CLICKUP_TOKEN,
    CONF_GITHUB_FILTER,
    CONF_GITHUB_TOKEN,
    CONF_GOOGLE_CLIENT_ID,
    CONF_GOOGLE_CLIENT_SECRET,
    CONF_GOOGLE_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    SOURCE_CLICKUP,
    SOURCE_GITHUB,
    SOURCE_GOOGLE,
)
from .writers import WRITERS

_LOGGER = logging.getLogger(__name__)

# Sort order helpers: tasks with a due date first (soonest first), then by
# priority, then alphabetically by title.
_PRIORITY_RANK = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 1, PRIORITY_LOW: 2, None: 3}


def _sort_key(task: dict[str, Any]) -> tuple:
    due = task.get("due_date")
    return (
        due is None,  # tasks with a due date come first
        due or "",
        _PRIORITY_RANK.get(task.get("priority"), 3),
        (task.get("title") or "").lower(),
    )


class UnifiedTodoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches every configured source and merges them into one task list."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        minutes = entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.entry = entry
        self._session = async_get_clientsession(hass)

    @property
    def _config(self) -> dict[str, Any]:
        """Effective config: entry data overlaid with any edited options."""
        return {**self.entry.data, **self.entry.options}

    @property
    def enabled_sources(self) -> list[str]:
        """Sources that have credentials configured."""
        config = self._config
        sources: list[str] = []
        if config.get(CONF_GITHUB_TOKEN):
            sources.append(SOURCE_GITHUB)
        if config.get(CONF_CLICKUP_TOKEN) and config.get(CONF_CLICKUP_TEAM_ID):
            sources.append(SOURCE_CLICKUP)
        if (
            config.get(CONF_GOOGLE_CLIENT_ID)
            and config.get(CONF_GOOGLE_CLIENT_SECRET)
            and config.get(CONF_GOOGLE_REFRESH_TOKEN)
        ):
            sources.append(SOURCE_GOOGLE)
        return sources

    def can_create(self, source: str) -> bool:
        """Whether new tasks can be created in ``source`` with the current config."""
        writer = WRITERS.get(source)
        return (
            source in self.enabled_sources
            and writer is not None
            and writer.can_create(self._config)
        )

    def can_complete(self, source: str) -> bool:
        """Whether tasks in ``source`` can be marked complete from Home Assistant."""
        writer = WRITERS.get(source)
        return (
            source in self.enabled_sources
            and writer is not None
            and writer.can_complete(self._config)
        )

    def get_task(self, source: str, source_id: str) -> dict[str, Any] | None:
        """Return the cached unified task for ``source``/``source_id``, if known."""
        if not self.data:
            return None
        for task in self.data.get("by_source", {}).get(source, []):
            if str(task.get("source_id")) == str(source_id):
                return task
        return None

    async def async_list_destinations(self, source: str) -> dict[str, Any]:
        """Return the destinations a task can be created in for ``source``.

        ``{"source", "destinations": [{"id", "name"}], "default": id|None}``.
        """
        writer = WRITERS.get(source)
        if writer is None or source not in self.enabled_sources:
            raise UpdateFailed(f"Source '{source}' is not configured")
        destinations = await writer.list_destinations(self._session, self._config)
        return {
            "source": source,
            "destinations": destinations,
            "default": writer.default_destination(self._config),
        }

    async def async_create_task(
        self,
        source: str,
        summary: str,
        description: str | None = None,
        due_date: str | None = None,
        destination: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in ``source`` and refresh so it shows up immediately.

        ``destination`` (repo / list id) overrides the configured default. When
        it is omitted a default must be configured.
        """
        writer = WRITERS.get(source)
        if writer is None or source not in self.enabled_sources:
            raise UpdateFailed(f"Source '{source}' is not configured")
        if destination is None and not writer.can_create(self._config):
            raise UpdateFailed(
                f"Creating tasks in '{source}' needs a destination — pick one or "
                "set a default in the integration's Configure dialog"
            )
        task = await writer.create(
            self._session,
            self._config,
            summary=summary,
            description=description,
            due_date=due_date,
            destination=destination,
        )
        await self.async_request_refresh()
        return task

    async def async_complete_task(self, source: str, source_id: str) -> None:
        """Mark a task complete in its source and refresh."""
        writer = WRITERS.get(source)
        if writer is None or source not in self.enabled_sources:
            raise UpdateFailed(f"Source '{source}' is not configured")
        if not writer.can_complete(self._config):
            raise UpdateFailed(f"Completing tasks in '{source}' is not supported")
        task = self.get_task(source, source_id) or {
            "source": source,
            "source_id": str(source_id),
        }
        await writer.complete(self._session, self._config, task)
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Any]:
        config = self._config
        sources = self.enabled_sources
        if not sources:
            raise UpdateFailed("No task sources are configured")

        async def _github() -> list[dict[str, Any]]:
            return await api.async_fetch_github(
                self._session,
                config[CONF_GITHUB_TOKEN],
                config.get(CONF_GITHUB_FILTER) or None,
            )

        async def _clickup() -> list[dict[str, Any]]:
            return await api.async_fetch_clickup(
                self._session,
                config[CONF_CLICKUP_TOKEN],
                str(config[CONF_CLICKUP_TEAM_ID]),
                config.get(CONF_CLICKUP_ASSIGNED_ONLY, True),
            )

        async def _google() -> list[dict[str, Any]]:
            return await api.async_fetch_google(
                self._session,
                config[CONF_GOOGLE_CLIENT_ID],
                config[CONF_GOOGLE_CLIENT_SECRET],
                config[CONF_GOOGLE_REFRESH_TOKEN],
            )

        fetchers = {
            SOURCE_GITHUB: _github,
            SOURCE_CLICKUP: _clickup,
            SOURCE_GOOGLE: _google,
        }

        active = [s for s in sources if s in fetchers]
        results = await asyncio.gather(
            *(fetchers[s]() for s in active), return_exceptions=True
        )

        all_tasks: list[dict[str, Any]] = []
        by_source: dict[str, list[dict[str, Any]]] = {}
        errors: dict[str, str] = {}
        for source, result in zip(active, results):
            if isinstance(result, Exception):
                errors[source] = str(result)
                _LOGGER.warning("Failed to fetch %s tasks: %s", source, result)
                # Preserve the previous data for this source so a transient
                # failure of one source doesn't blank out the dashboard.
                if self.data:
                    previous = self.data.get("by_source", {}).get(source, [])
                    by_source[source] = previous
                    all_tasks.extend(previous)
                else:
                    by_source[source] = []
                continue
            by_source[source] = result
            all_tasks.extend(result)

        # If every source failed and we have nothing cached, surface the failure.
        if errors and len(errors) == len(active) and not self.data:
            raise UpdateFailed("; ".join(f"{s}: {e}" for s, e in errors.items()))

        all_tasks.sort(key=_sort_key)
        counts = {source: len(tasks) for source, tasks in by_source.items()}
        counts["total"] = len(all_tasks)

        return {
            "tasks": all_tasks,
            "by_source": by_source,
            "counts": counts,
            "errors": errors,
        }
