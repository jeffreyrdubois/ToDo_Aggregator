"""Constants for the Unified To-Do Aggregator integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "unified_todo"

# Source identifiers (also used as the ``source`` field in the unified schema).
SOURCE_GITHUB: Final = "github"
SOURCE_CLICKUP: Final = "clickup"
SOURCE_GOOGLE: Final = "google_tasks"

ALL_SOURCES: Final = (SOURCE_GITHUB, SOURCE_CLICKUP, SOURCE_GOOGLE)

# Human friendly labels for each source, keyed by source id.
SOURCE_LABELS: Final = {
    SOURCE_GITHUB: "GitHub Issues",
    SOURCE_CLICKUP: "ClickUp",
    SOURCE_GOOGLE: "Google Tasks",
}

# Preferred entity_id object ids per source, shared by the sensor and todo
# platforms so entity ids stay stable and predictable (kept in sync with the
# README).
SOURCE_OBJECT_IDS: Final = {
    SOURCE_GITHUB: "github_issues",
    SOURCE_CLICKUP: "clickup",
    SOURCE_GOOGLE: "google_tasks",
}

# Config / option keys.
CONF_GITHUB_TOKEN: Final = "github_token"
CONF_GITHUB_FILTER: Final = "github_repo_filter"
# Target repo (``owner/repo``) new issues are created in.
CONF_GITHUB_DEFAULT_REPO: Final = "github_default_repo"

CONF_CLICKUP_TOKEN: Final = "clickup_token"
CONF_CLICKUP_TEAM_ID: Final = "clickup_team_id"
CONF_CLICKUP_ASSIGNED_ONLY: Final = "clickup_assigned_only"
# Target list id new tasks are created in.
CONF_CLICKUP_DEFAULT_LIST_ID: Final = "clickup_default_list_id"

CONF_GOOGLE_CLIENT_ID: Final = "google_client_id"
CONF_GOOGLE_CLIENT_SECRET: Final = "google_client_secret"
CONF_GOOGLE_REFRESH_TOKEN: Final = "google_refresh_token"
# Target task list id new tasks are created in (``@default`` if unset).
CONF_GOOGLE_DEFAULT_LIST: Final = "google_default_list"
GOOGLE_DEFAULT_LIST: Final = "@default"

# ``scan_interval`` is stored in options, expressed in minutes.
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 15
MIN_SCAN_INTERVAL: Final = timedelta(minutes=1)

# Normalised priority levels.
PRIORITY_HIGH: Final = "high"
PRIORITY_MEDIUM: Final = "medium"
PRIORITY_LOW: Final = "low"

# Services for creating / completing tasks.
SERVICE_CREATE_TASK: Final = "create_task"
SERVICE_COMPLETE_TASK: Final = "complete_task"
SERVICE_LIST_DESTINATIONS: Final = "list_destinations"

ATTR_SOURCE: Final = "source"
ATTR_SUMMARY: Final = "summary"
ATTR_DESCRIPTION: Final = "description"
ATTR_DUE_DATE: Final = "due_date"
ATTR_TASK_ID: Final = "task_id"
ATTR_DESTINATION: Final = "destination"

# Object id / uid for the combined (all-sources) To-do list entity.
COMBINED_OBJECT_ID: Final = "unified_todos"
# Separator used to pack ``source`` + ``source_id`` into a combined item uid.
COMBINED_UID_SEP: Final = ":"
