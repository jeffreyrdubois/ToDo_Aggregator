"""Sensor platform for the Unified To-Do Aggregator.

Creates one aggregate sensor (``sensor.unified_todos``) plus one count sensor
per configured source. The full task list is exposed as the ``tasks``
attribute of the aggregate sensor, ready to be rendered by a Markdown card.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    ENTITY_ID_FORMAT,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    SOURCE_LABELS,
    SOURCE_OBJECT_IDS,
)
from .coordinator import UnifiedTodoCoordinator

# The task list can be large; keep it out of the recorder database.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the unified to-do sensors from a config entry."""
    coordinator: UnifiedTodoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [UnifiedTodoAggregateSensor(coordinator, entry)]
    entities.extend(
        UnifiedTodoSourceSensor(coordinator, entry, source)
        for source in coordinator.enabled_sources
    )
    async_add_entities(entities)


class _BaseSensor(CoordinatorEntity[UnifiedTodoCoordinator], SensorEntity):
    """Shared device + attribution wiring."""

    _attr_attribution = "Aggregated from Google Tasks, GitHub and ClickUp"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "tasks"

    def __init__(
        self, coordinator: UnifiedTodoCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Unified To-Do Aggregator",
            manufacturer="Unified To-Do",
            entry_type=DeviceEntryType.SERVICE,
        )


class UnifiedTodoAggregateSensor(_BaseSensor):
    """Total open task count across every source, with the full list attached."""

    _attr_icon = "mdi:format-list-checks"
    _attr_name = "Unified To-Dos"
    # Don't store the (potentially large) task list in the recorder DB.
    _unrecorded_attributes = frozenset({"tasks", "errors"})

    def __init__(
        self, coordinator: UnifiedTodoCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_unified_todos"
        # Force the documented entity_id: sensor.unified_todos.
        self.entity_id = generate_entity_id(
            ENTITY_ID_FORMAT, "unified_todos", hass=coordinator.hass
        )

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data["counts"].get("total", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        counts = data.get("counts", {})
        return {
            "tasks": data.get("tasks", []),
            "counts": {k: v for k, v in counts.items() if k != "total"},
            "errors": data.get("errors", {}),
        }


class UnifiedTodoSourceSensor(_BaseSensor):
    """Open task count for a single source."""

    _attr_icon = "mdi:format-list-bulleted"
    _unrecorded_attributes = frozenset({"tasks"})

    def __init__(
        self,
        coordinator: UnifiedTodoCoordinator,
        entry: ConfigEntry,
        source: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._source = source
        self._attr_unique_id = f"{entry.entry_id}_{source}"
        self._attr_name = SOURCE_LABELS.get(source, source)
        self.entity_id = generate_entity_id(
            ENTITY_ID_FORMAT,
            SOURCE_OBJECT_IDS.get(source, source),
            hass=coordinator.hass,
        )

    @property
    def native_value(self) -> int | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data["counts"].get(self._source, 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        tasks = data.get("by_source", {}).get(self._source, [])
        attrs: dict[str, Any] = {"tasks": tasks}
        error = data.get("errors", {}).get(self._source)
        if error:
            attrs["error"] = error
        return attrs
