"""To-do list platform for the Unified To-Do Aggregator.

Exposes one native Home Assistant To-do list entity per configured source
(``todo.github_issues``, ``todo.clickup``, ``todo.google_tasks``). The built-in
To-do card and the mobile app can then add items and check them off:

* **Adding** an item creates a task in that source (when a destination is
  configured) — i.e. you pick the service simply by choosing its list.
* **Checking off** an item marks it complete in its source (for GitHub this
  closes the issue).

Editing an item's text is intentionally not supported; open the source app via
the task's link for richer edits.
"""

from __future__ import annotations

from datetime import date, datetime

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UnifiedTodoError
from .const import (
    COMBINED_OBJECT_ID,
    COMBINED_UID_SEP,
    DOMAIN,
    SOURCE_LABELS,
    SOURCE_OBJECT_IDS,
)
from .coordinator import UnifiedTodoCoordinator

TODO_ENTITY_ID_FORMAT = "todo.{}"

# Each create/complete hits a remote API; don't let HA fan them out in parallel.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the combined To-do list plus one per configured source."""
    coordinator: UnifiedTodoCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[TodoListEntity] = [
        UnifiedTodoListEntity(coordinator, entry, source)
        for source in coordinator.enabled_sources
    ]
    if coordinator.enabled_sources:
        entities.insert(0, UnifiedCombinedTodoListEntity(coordinator, entry))
    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Unified To-Do Aggregator",
        manufacturer="Unified To-Do",
        entry_type=DeviceEntryType.SERVICE,
    )


def _parse_due(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


class UnifiedTodoListEntity(
    CoordinatorEntity[UnifiedTodoCoordinator], TodoListEntity
):
    """A To-do list backed by one source's open tasks."""

    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: UnifiedTodoCoordinator,
        entry: ConfigEntry,
        source: str,
    ) -> None:
        super().__init__(coordinator)
        self._source = source
        self._attr_name = SOURCE_LABELS.get(source, source)
        self._attr_unique_id = f"{entry.entry_id}_{source}_todo"
        self._attr_device_info = _device_info(entry)
        self.entity_id = generate_entity_id(
            TODO_ENTITY_ID_FORMAT,
            SOURCE_OBJECT_IDS.get(source, source),
            hass=coordinator.hass,
        )

        # Completing is offered whenever the source supports it; creating is
        # only offered when a destination (repo / list) is configured.
        features = TodoListEntityFeature(0)
        if coordinator.can_complete(source):
            features |= TodoListEntityFeature.UPDATE_TODO_ITEM
        if coordinator.can_create(source):
            features |= (
                TodoListEntityFeature.CREATE_TODO_ITEM
                | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
                | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
            )
        self._attr_supported_features = features

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Open tasks for this source. Completed tasks fall off after refresh."""
        if not self.coordinator.data:
            return None
        tasks = self.coordinator.data.get("by_source", {}).get(self._source, [])
        return [
            TodoItem(
                uid=str(task.get("source_id")),
                summary=task.get("title") or "(untitled)",
                status=TodoItemStatus.NEEDS_ACTION,
                due=_parse_due(task.get("due_date")),
                description=task.get("description"),
            )
            for task in tasks
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        due_date = item.due.isoformat()[:10] if item.due else None
        try:
            await self.coordinator.async_create_task(
                self._source,
                item.summary or "",
                description=item.description,
                due_date=due_date,
            )
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err

    async def async_update_todo_item(self, item: TodoItem) -> None:
        # The only edit we support is marking an item complete.
        if item.status != TodoItemStatus.COMPLETED:
            raise HomeAssistantError(
                "Editing items isn't supported — open the task's link to edit "
                "it in the source app."
            )
        try:
            await self.coordinator.async_complete_task(self._source, item.uid)
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err


class UnifiedCombinedTodoListEntity(
    CoordinatorEntity[UnifiedTodoCoordinator], TodoListEntity
):
    """One list showing every source's open tasks.

    Check an item off here and the integration routes the completion to the
    right service automatically — no need to know which list it came from. The
    item ``uid`` packs the source and the source's own id (``source:source_id``)
    so completion knows where to go.

    Creating is intentionally not offered on the combined list (the
    destination would be ambiguous) — use a per-source list, the custom card, or
    the ``create_task`` service for that.
    """

    _attr_icon = "mdi:format-list-checks"
    _attr_name = "Unified To-Dos"
    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM

    def __init__(
        self, coordinator: UnifiedTodoCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_combined_todo"
        self._attr_device_info = _device_info(entry)
        self.entity_id = generate_entity_id(
            TODO_ENTITY_ID_FORMAT, COMBINED_OBJECT_ID, hass=coordinator.hass
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:
        if not self.coordinator.data:
            return None
        items: list[TodoItem] = []
        for task in self.coordinator.data.get("tasks", []):
            source = task.get("source")
            source_id = task.get("source_id")
            if not source or source_id is None:
                continue
            label = SOURCE_LABELS.get(source, source)
            items.append(
                TodoItem(
                    uid=f"{source}{COMBINED_UID_SEP}{source_id}",
                    summary=f"{task.get('title') or '(untitled)'} · {label}",
                    status=TodoItemStatus.NEEDS_ACTION,
                    due=_parse_due(task.get("due_date")),
                    description=task.get("description"),
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status != TodoItemStatus.COMPLETED:
            raise HomeAssistantError(
                "Editing items isn't supported — open the task's link to edit "
                "it in the source app."
            )
        source, _, source_id = (item.uid or "").partition(COMBINED_UID_SEP)
        if not source or not source_id:
            raise HomeAssistantError(f"Unrecognised task id: {item.uid}")
        try:
            await self.coordinator.async_complete_task(source, source_id)
        except UnifiedTodoError as err:
            raise HomeAssistantError(str(err)) from err
