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

# Config / option keys.
CONF_GITHUB_TOKEN: Final = "github_token"
CONF_GITHUB_FILTER: Final = "github_repo_filter"

CONF_CLICKUP_TOKEN: Final = "clickup_token"
CONF_CLICKUP_TEAM_ID: Final = "clickup_team_id"
CONF_CLICKUP_ASSIGNED_ONLY: Final = "clickup_assigned_only"

CONF_GOOGLE_CLIENT_ID: Final = "google_client_id"
CONF_GOOGLE_CLIENT_SECRET: Final = "google_client_secret"
CONF_GOOGLE_REFRESH_TOKEN: Final = "google_refresh_token"

# ``scan_interval`` is stored in options, expressed in minutes.
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 15
MIN_SCAN_INTERVAL: Final = timedelta(minutes=1)

# Normalised priority levels.
PRIORITY_HIGH: Final = "high"
PRIORITY_MEDIUM: Final = "medium"
PRIORITY_LOW: Final = "low"
