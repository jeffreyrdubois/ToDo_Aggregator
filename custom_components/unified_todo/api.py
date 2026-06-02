"""Client logic for fetching and normalising tasks from each source.

Every source is reduced to the same unified task dictionary so the rest of the
integration never has to care where a task came from::

    {
        "title": str,
        "source": "github" | "clickup" | "google_tasks",
        "source_id": str,
        "due_date": "YYYY-MM-DD" | None,
        "priority": "high" | "medium" | "low" | None,
        "url": str | None,
        "assignee": str | None,
        "description": str | None,
        "updated_at": str | None,   # ISO 8601
    }

The integration is strictly read-only: nothing here ever writes back to a
source system.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .const import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_MEDIUM,
    SOURCE_CLICKUP,
    SOURCE_GITHUB,
    SOURCE_GOOGLE,
)

_LOGGER = logging.getLogger(__name__)

# How long any single HTTP request is allowed to take.
REQUEST_TIMEOUT = ClientTimeout(total=30)

GITHUB_API = "https://api.github.com"
CLICKUP_API = "https://api.clickup.com/api/v2"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_TASKS_API = "https://www.googleapis.com/tasks/v1"

# Description snippets are truncated so sensor attributes stay reasonable.
DESCRIPTION_MAX_LEN = 280


class UnifiedTodoError(Exception):
    """Base error for source clients."""


class AuthError(UnifiedTodoError):
    """Raised when a credential is rejected by a source (HTTP 401/403)."""


class SourceConnectionError(UnifiedTodoError):
    """Raised when a source cannot be reached or returns an unexpected error."""


def _truncate(text: str | None) -> str | None:
    """Trim a description to a sane length for sensor attributes."""
    if not text:
        return None
    text = text.strip()
    if len(text) <= DESCRIPTION_MAX_LEN:
        return text
    return text[: DESCRIPTION_MAX_LEN - 1].rstrip() + "…"


def _epoch_ms_to_date(value: Any) -> str | None:
    """Convert a ClickUp millisecond epoch string to ``YYYY-MM-DD``."""
    if not value:
        return None
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
    return dt.date().isoformat()


def _epoch_ms_to_iso(value: Any) -> str | None:
    """Convert a ClickUp millisecond epoch string to an ISO 8601 timestamp."""
    if not value:
        return None
    try:
        dt = datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None
    return dt.isoformat()


async def _raise_for_auth(resp) -> None:
    """Convert auth-related HTTP statuses into :class:`AuthError`."""
    if resp.status in (401, 403):
        raise AuthError(f"Authentication failed (HTTP {resp.status})")


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------

_GH_PRIORITY_LABELS = {
    PRIORITY_HIGH: re.compile(r"(priority[:/ -]*high|p0|p1|urgent|critical)", re.I),
    PRIORITY_MEDIUM: re.compile(r"(priority[:/ -]*med|p2)", re.I),
    PRIORITY_LOW: re.compile(r"(priority[:/ -]*low|p3|p4)", re.I),
}


def _github_priority(labels: list[dict[str, Any]]) -> str | None:
    """Derive a normalised priority from issue label names, if any."""
    names = " ".join(label.get("name", "") for label in labels)
    for level, pattern in _GH_PRIORITY_LABELS.items():
        if pattern.search(names):
            return level
    return None


def _normalise_github(issue: dict[str, Any]) -> dict[str, Any]:
    milestone = issue.get("milestone") or {}
    assignee = (issue.get("assignee") or {}).get("login")
    return {
        "title": issue.get("title"),
        "source": SOURCE_GITHUB,
        "source_id": str(issue.get("number")),
        "due_date": (milestone.get("due_on") or "")[:10] or None,
        "priority": _github_priority(issue.get("labels") or []),
        "url": issue.get("html_url"),
        "assignee": assignee,
        "description": _truncate(issue.get("body")),
        "updated_at": issue.get("updated_at"),
    }


async def async_fetch_github(
    session: ClientSession, token: str, repo_filter: str | None = None
) -> list[dict[str, Any]]:
    """Fetch open issues assigned to the authenticated user.

    ``repo_filter`` is an optional case-insensitive regex applied to each
    issue's ``owner/repo`` full name (e.g. ``sr2`` to only keep SR2 repos).
    Pull requests are excluded.
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"filter": "assigned", "state": "open", "per_page": "100"}

    pattern = re.compile(repo_filter, re.I) if repo_filter else None
    tasks: list[dict[str, Any]] = []
    try:
        async with session.get(
            f"{GITHUB_API}/issues",
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            await _raise_for_auth(resp)
            resp.raise_for_status()
            issues = await resp.json()
    except ClientResponseError as err:
        raise SourceConnectionError(f"GitHub returned HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach GitHub: {err}") from err

    for issue in issues:
        # The /issues endpoint mixes in pull requests; skip them.
        if "pull_request" in issue:
            continue
        if pattern:
            repo = (issue.get("repository") or {}).get("full_name", "")
            # Fall back to parsing the API URL when ``repository`` is absent.
            if not repo:
                repo = "/".join(issue.get("repository_url", "").split("/")[-2:])
            if not pattern.search(repo):
                continue
        tasks.append(_normalise_github(issue))
    return tasks


async def async_validate_github(session: ClientSession, token: str) -> str:
    """Validate a GitHub token and return the authenticated user's login."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    try:
        async with session.get(
            f"{GITHUB_API}/user", headers=headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            await _raise_for_auth(resp)
            resp.raise_for_status()
            data = await resp.json()
    except ClientResponseError as err:
        raise SourceConnectionError(f"GitHub returned HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach GitHub: {err}") from err
    return data.get("login", "")


# ---------------------------------------------------------------------------
# ClickUp
# ---------------------------------------------------------------------------

_CLICKUP_PRIORITY = {
    "urgent": PRIORITY_HIGH,
    "high": PRIORITY_HIGH,
    "normal": PRIORITY_MEDIUM,
    "low": PRIORITY_LOW,
}


def _normalise_clickup(task: dict[str, Any]) -> dict[str, Any]:
    priority_obj = task.get("priority") or {}
    priority = _CLICKUP_PRIORITY.get((priority_obj.get("priority") or "").lower())
    assignees = task.get("assignees") or []
    assignee = None
    if assignees:
        first = assignees[0]
        assignee = first.get("username") or first.get("email")
    return {
        "title": task.get("name"),
        "source": SOURCE_CLICKUP,
        "source_id": str(task.get("id")),
        "due_date": _epoch_ms_to_date(task.get("due_date")),
        "priority": priority,
        "url": task.get("url"),
        "assignee": assignee,
        "description": _truncate(task.get("text_content") or task.get("description")),
        "updated_at": _epoch_ms_to_iso(task.get("date_updated")),
    }


async def _clickup_user_id(session: ClientSession, token: str) -> int | None:
    """Return the id of the user the ClickUp token belongs to."""
    headers = {"Authorization": token}
    try:
        async with session.get(
            f"{CLICKUP_API}/user", headers=headers, timeout=REQUEST_TIMEOUT
        ) as resp:
            await _raise_for_auth(resp)
            resp.raise_for_status()
            data = await resp.json()
    except ClientResponseError as err:
        raise SourceConnectionError(f"ClickUp returned HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach ClickUp: {err}") from err
    return (data.get("user") or {}).get("id")


async def async_fetch_clickup(
    session: ClientSession,
    token: str,
    team_id: str,
    assigned_only: bool = True,
) -> list[dict[str, Any]]:
    """Fetch open, not-yet-closed tasks from a ClickUp workspace (team)."""
    headers = {"Authorization": token}
    params: dict[str, Any] = {
        "include_closed": "false",
        "subtasks": "true",
        "page": "0",
    }
    if assigned_only:
        user_id = await _clickup_user_id(session, token)
        if user_id is not None:
            params["assignees[]"] = str(user_id)

    tasks: list[dict[str, Any]] = []
    page = 0
    # ClickUp paginates 100 tasks per page; ``last_page`` signals the end.
    while True:
        params["page"] = str(page)
        try:
            async with session.get(
                f"{CLICKUP_API}/team/{team_id}/task",
                headers=headers,
                params=params,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                await _raise_for_auth(resp)
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as err:
            raise SourceConnectionError(
                f"ClickUp returned HTTP {err.status}"
            ) from err
        except (ClientError, TimeoutError) as err:
            raise SourceConnectionError(f"Could not reach ClickUp: {err}") from err

        page_tasks = data.get("tasks") or []
        for task in page_tasks:
            # Skip tasks in a "closed"/"done" type status if any slipped through.
            status_type = (task.get("status") or {}).get("type")
            if status_type in ("closed", "done"):
                continue
            tasks.append(_normalise_clickup(task))

        if data.get("last_page") or not page_tasks:
            break
        page += 1
        if page > 50:  # Hard safety cap (~5000 tasks).
            break
    return tasks


async def async_validate_clickup(
    session: ClientSession, token: str, team_id: str
) -> None:
    """Validate a ClickUp token and that the team id is reachable."""
    headers = {"Authorization": token}
    try:
        async with session.get(
            f"{CLICKUP_API}/team/{team_id}/space",
            headers=headers,
            params={"archived": "false"},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            await _raise_for_auth(resp)
            if resp.status == 404:
                raise SourceConnectionError("ClickUp team id not found")
            resp.raise_for_status()
    except ClientResponseError as err:
        raise SourceConnectionError(f"ClickUp returned HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach ClickUp: {err}") from err


# ---------------------------------------------------------------------------
# Google Tasks
# ---------------------------------------------------------------------------


def _normalise_google(task: dict[str, Any]) -> dict[str, Any]:
    # Google Tasks ``due`` is an RFC 3339 timestamp; only the date is meaningful.
    due = task.get("due")
    return {
        "title": task.get("title"),
        "source": SOURCE_GOOGLE,
        "source_id": str(task.get("id")),
        "due_date": due[:10] if due else None,
        "priority": None,  # Google Tasks has no priority concept.
        "url": task.get("webViewLink") or "https://tasks.google.com/",
        "assignee": None,
        "description": _truncate(task.get("notes")),
        "updated_at": task.get("updated"),
    }


async def async_google_access_token(
    session: ClientSession,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """Exchange a long-lived refresh token for a short-lived access token."""
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    try:
        async with session.post(
            GOOGLE_TOKEN_URL, data=payload, timeout=REQUEST_TIMEOUT
        ) as resp:
            if resp.status in (400, 401):
                raise AuthError("Google rejected the refresh token / client credentials")
            resp.raise_for_status()
            data = await resp.json()
    except ClientResponseError as err:
        raise SourceConnectionError(f"Google token endpoint HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach Google: {err}") from err
    token = data.get("access_token")
    if not token:
        raise AuthError("Google did not return an access token")
    return token


async def async_fetch_google(
    session: ClientSession,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> list[dict[str, Any]]:
    """Fetch all open (not completed/hidden) tasks across every task list."""
    access_token = await async_google_access_token(
        session, client_id, client_secret, refresh_token
    )
    headers = {"Authorization": f"Bearer {access_token}"}

    # 1. Enumerate the user's task lists.
    try:
        async with session.get(
            f"{GOOGLE_TASKS_API}/users/@me/lists",
            headers=headers,
            params={"maxResults": "100"},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            await _raise_for_auth(resp)
            resp.raise_for_status()
            lists_data = await resp.json()
    except ClientResponseError as err:
        raise SourceConnectionError(f"Google Tasks HTTP {err.status}") from err
    except (ClientError, TimeoutError) as err:
        raise SourceConnectionError(f"Could not reach Google Tasks: {err}") from err

    tasks: list[dict[str, Any]] = []
    for task_list in lists_data.get("items", []):
        list_id = task_list.get("id")
        if not list_id:
            continue
        page_token: str | None = None
        while True:
            params = {
                "showCompleted": "false",
                "showHidden": "false",
                "maxResults": "100",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                async with session.get(
                    f"{GOOGLE_TASKS_API}/lists/{list_id}/tasks",
                    headers=headers,
                    params=params,
                    timeout=REQUEST_TIMEOUT,
                ) as resp:
                    await _raise_for_auth(resp)
                    resp.raise_for_status()
                    data = await resp.json()
            except ClientResponseError as err:
                raise SourceConnectionError(
                    f"Google Tasks HTTP {err.status}"
                ) from err
            except (ClientError, TimeoutError) as err:
                raise SourceConnectionError(
                    f"Could not reach Google Tasks: {err}"
                ) from err

            for task in data.get("items", []):
                # Skip completed and deleted tasks defensively.
                if task.get("status") == "completed" or task.get("deleted"):
                    continue
                tasks.append(_normalise_google(task))

            page_token = data.get("nextPageToken")
            if not page_token:
                break
    return tasks


async def async_validate_google(
    session: ClientSession,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> None:
    """Validate Google credentials by acquiring an access token."""
    await async_google_access_token(session, client_id, client_secret, refresh_token)
