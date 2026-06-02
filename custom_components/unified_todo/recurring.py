"""Recurring-task scheduler.

Each recurring rule is stored as a config **subentry** of the integration (so
you add/edit/remove them right under the integration in the UI). This module
turns the enabled rules into time listeners that call the coordinator's
``create_task`` when a rule is due — reusing the exact same create path as the
service and the card, including the per-source destination handling.

Re-scheduling is automatic: changing a subentry triggers a config-entry reload,
which tears down the old listeners (via :meth:`async_unload`) and runs
:meth:`async_setup` again with the new rules.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change

from .api import UnifiedTodoError
from .const import (
    ATTR_DESCRIPTION,
    ATTR_DESTINATION,
    ATTR_SOURCE,
    ATTR_SUMMARY,
    CONF_DAY_OF_MONTH,
    CONF_DUE_OFFSET_DAYS,
    CONF_ENABLED,
    CONF_FREQUENCY,
    CONF_TIME,
    CONF_WEEKDAYS,
    FREQ_DAILY,
    FREQ_MONTHLY,
    FREQ_WEEKLY,
    SUBENTRY_RECURRING,
    WEEKDAYS,
)
from .coordinator import UnifiedTodoCoordinator

_LOGGER = logging.getLogger(__name__)


def _parse_hm(value: str | None) -> tuple[int, int]:
    """Parse ``HH:MM`` (or ``HH:MM:SS``) into ``(hour, minute)``."""
    parts = (value or "09:00").split(":")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return 9, 0


def _due_today(data: dict[str, Any], now: datetime) -> bool:
    """Whether a rule should fire on ``now``'s date, given its frequency."""
    freq = data.get(CONF_FREQUENCY, FREQ_DAILY)
    if freq == FREQ_DAILY:
        return True
    if freq == FREQ_WEEKLY:
        return WEEKDAYS[now.weekday()] in (data.get(CONF_WEEKDAYS) or [])
    if freq == FREQ_MONTHLY:
        dom = int(data.get(CONF_DAY_OF_MONTH, 1) or 1)
        last = calendar.monthrange(now.year, now.month)[1]
        # Clamp so e.g. "the 31st" still fires on the last day of short months.
        return now.day == min(dom, last)
    return False


class RecurringScheduler:
    """Owns the time listeners for one config entry's recurring rules."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator: UnifiedTodoCoordinator,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self._unsubs: list[Any] = []

    @callback
    def async_setup(self) -> None:
        """Register a daily time listener for each enabled recurring rule."""
        for subentry in self.entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_RECURRING:
                continue
            data = dict(subentry.data)
            if not data.get(CONF_ENABLED, True):
                continue
            hour, minute = _parse_hm(data.get(CONF_TIME))
            unsub = async_track_time_change(
                self.hass,
                partial(self._async_fire, data, subentry.title or data.get(ATTR_SUMMARY)),
                hour=hour,
                minute=minute,
                second=0,
            )
            self._unsubs.append(unsub)
        if self._unsubs:
            _LOGGER.debug("Scheduled %d recurring task rule(s)", len(self._unsubs))

    @callback
    def async_unload(self) -> None:
        """Cancel every registered listener."""
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_fire(
        self, data: dict[str, Any], title: str, now: datetime
    ) -> None:
        if not _due_today(data, now):
            return
        due_date: str | None = None
        offset = data.get(CONF_DUE_OFFSET_DAYS)
        if offset is not None:
            try:
                due_date = (now.date() + timedelta(days=int(offset))).isoformat()
            except (TypeError, ValueError):
                due_date = None
        try:
            await self.coordinator.async_create_task(
                data[ATTR_SOURCE],
                data[ATTR_SUMMARY],
                description=data.get(ATTR_DESCRIPTION) or None,
                due_date=due_date,
                destination=data.get(ATTR_DESTINATION) or None,
            )
            _LOGGER.info("Created recurring task '%s'", title)
        except UnifiedTodoError as err:
            _LOGGER.warning("Recurring task '%s' failed: %s", title, err)
        except Exception:  # noqa: BLE001 - never let a rule kill the scheduler
            _LOGGER.exception("Unexpected error creating recurring task '%s'", title)
