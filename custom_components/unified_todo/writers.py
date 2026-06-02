"""Write-back handlers for each task source.

The aggregator is read-mostly. This module adds the *optional* ability to
**create** new tasks and **mark them complete**, without touching the polling
or normalisation logic.

Every source implements the same small :class:`SourceWriter` interface and
registers itself in :data:`WRITERS`. Adding write support for a future source
therefore means writing one class and adding one line to the registry — nothing
else in the integration needs to change.
"""

from __future__ import annotations

from typing import Any

from aiohttp import ClientSession

from . import api
from .const import (
    CONF_CLICKUP_DEFAULT_LIST_ID,
    CONF_CLICKUP_TOKEN,
    CONF_GITHUB_DEFAULT_REPO,
    CONF_GITHUB_TOKEN,
    CONF_GOOGLE_CLIENT_ID,
    CONF_GOOGLE_CLIENT_SECRET,
    CONF_GOOGLE_DEFAULT_LIST,
    CONF_GOOGLE_REFRESH_TOKEN,
    GOOGLE_DEFAULT_LIST,
    SOURCE_CLICKUP,
    SOURCE_GITHUB,
    SOURCE_GOOGLE,
)


class SourceWriter:
    """Common interface for creating and completing tasks in one source.

    A handler is given the effective config dict (entry data overlaid with
    options) on every call, so it can pull whatever credentials / destinations
    it needs. ``can_create`` / ``can_complete`` report what the *current* config
    supports, which the UI uses to advertise the right capabilities.
    """

    source: str

    def can_create(self, config: dict[str, Any]) -> bool:
        """Whether the current config has everything needed to create a task."""
        return False

    def can_complete(self, config: dict[str, Any]) -> bool:
        """Whether the current config has everything needed to complete a task."""
        return False

    async def create(
        self,
        session: ClientSession,
        config: dict[str, Any],
        *,
        summary: str,
        description: str | None = None,
        due_date: str | None = None,
    ) -> dict[str, Any]:
        """Create a task and return it in the unified schema."""
        raise NotImplementedError

    async def complete(
        self,
        session: ClientSession,
        config: dict[str, Any],
        task: dict[str, Any],
    ) -> None:
        """Mark an existing unified ``task`` complete in its source."""
        raise NotImplementedError


class GithubWriter(SourceWriter):
    """Creates issues in a configured repo; completing closes the issue."""

    source = SOURCE_GITHUB

    def can_create(self, config: dict[str, Any]) -> bool:
        return bool(config.get(CONF_GITHUB_TOKEN) and config.get(CONF_GITHUB_DEFAULT_REPO))

    def can_complete(self, config: dict[str, Any]) -> bool:
        return bool(config.get(CONF_GITHUB_TOKEN))

    async def create(self, session, config, *, summary, description=None, due_date=None):
        return await api.async_github_create_issue(
            session,
            config[CONF_GITHUB_TOKEN],
            config[CONF_GITHUB_DEFAULT_REPO],
            summary,
            description,
        )

    async def complete(self, session, config, task):
        repo = task.get("repo")
        if not repo:
            raise api.UnifiedTodoError(
                "Cannot determine the GitHub repository for this issue"
            )
        await api.async_github_close_issue(
            session, config[CONF_GITHUB_TOKEN], repo, task["source_id"]
        )


class ClickupWriter(SourceWriter):
    """Creates tasks in a configured list; completing sets the done status."""

    source = SOURCE_CLICKUP

    def can_create(self, config: dict[str, Any]) -> bool:
        return bool(
            config.get(CONF_CLICKUP_TOKEN) and config.get(CONF_CLICKUP_DEFAULT_LIST_ID)
        )

    def can_complete(self, config: dict[str, Any]) -> bool:
        return bool(config.get(CONF_CLICKUP_TOKEN))

    async def create(self, session, config, *, summary, description=None, due_date=None):
        return await api.async_clickup_create_task(
            session,
            config[CONF_CLICKUP_TOKEN],
            str(config[CONF_CLICKUP_DEFAULT_LIST_ID]),
            summary,
            description,
            due_date,
        )

    async def complete(self, session, config, task):
        await api.async_clickup_complete_task(
            session, config[CONF_CLICKUP_TOKEN], task["source_id"]
        )


class GoogleWriter(SourceWriter):
    """Creates tasks in a task list (``@default`` unless configured)."""

    source = SOURCE_GOOGLE

    @staticmethod
    def _has_creds(config: dict[str, Any]) -> bool:
        return bool(
            config.get(CONF_GOOGLE_CLIENT_ID)
            and config.get(CONF_GOOGLE_CLIENT_SECRET)
            and config.get(CONF_GOOGLE_REFRESH_TOKEN)
        )

    @staticmethod
    def _creds(config: dict[str, Any]) -> tuple[str, str, str]:
        return (
            config[CONF_GOOGLE_CLIENT_ID],
            config[CONF_GOOGLE_CLIENT_SECRET],
            config[CONF_GOOGLE_REFRESH_TOKEN],
        )

    def can_create(self, config: dict[str, Any]) -> bool:
        return self._has_creds(config)

    def can_complete(self, config: dict[str, Any]) -> bool:
        return self._has_creds(config)

    async def create(self, session, config, *, summary, description=None, due_date=None):
        list_id = config.get(CONF_GOOGLE_DEFAULT_LIST) or GOOGLE_DEFAULT_LIST
        return await api.async_google_create_task(
            session, *self._creds(config), list_id, summary, description, due_date
        )

    async def complete(self, session, config, task):
        list_id = task.get("list_id") or config.get(
            CONF_GOOGLE_DEFAULT_LIST
        ) or GOOGLE_DEFAULT_LIST
        await api.async_google_complete_task(
            session, *self._creds(config), list_id, task["source_id"]
        )


# Registry of write handlers keyed by source id. Add a new source here.
WRITERS: dict[str, SourceWriter] = {
    SOURCE_GITHUB: GithubWriter(),
    SOURCE_CLICKUP: ClickupWriter(),
    SOURCE_GOOGLE: GoogleWriter(),
}
