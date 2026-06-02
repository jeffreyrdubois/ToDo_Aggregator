# Home Assistant Unified To-Do Aggregator

A Home Assistant custom integration that aggregates tasks from multiple sources—Google Tasks, GitHub Issues, and ClickUp—into a single unified dashboard view.

## Problem Statement

Managing tasks across three different systems creates context switching and reduced visibility:

- **Google Tasks:** Personal and routine household items
- **GitHub Issues:** SR2 Industries coding projects (with rich collaborative features: comments, code references, linking)
- **ClickUp:** SR2 Industries operational tasks (embroidery orders, heat press jobs, inventory management, workflows)

This project provides a centralized read-only aggregator that displays all open tasks in one place while preserving the native strengths of each system (GitHub's collaboration, ClickUp's workflow state, Tasks' simplicity).

## Design Principles

- **Source System Fidelity:** Data lives in its native system; this integration is read-only and does not modify or sync data back.
- **Native Context Preservation:** Links and metadata are preserved so users can drill into the full context in the original system.
- **Infrastructure Separation:** Personal data stays on personal infrastructure (Home Assistant); SR2 data remains isolated on SR2 infrastructure.
- **Minimal Overhead:** Leverages existing Home Assistant REST sensor capabilities with template sensors for aggregation.

## Architecture

### Data Flow

```
Google Tasks ──┐
               │
GitHub Issues ─┼──> REST Sensors ──> Template Sensor ──> Dashboard Card
               │   (one per source)  (sensor.unified_todos)
ClickUp ───────┘
```

### Components

**REST Sensors (one per source)**

- `sensor.google_tasks_all`: Pulls all open tasks from Google Tasks
- `sensor.github_issues_all`: Pulls all open issues assigned to user from SR2 repos
- `sensor.clickup_tasks_all`: Pulls all open tasks assigned to user from SR2 workspace

**Template Sensor**

- `sensor.unified_todos`: Aggregates and normalizes data from all three REST sensors
- Formats as structured list with consistent schema (title, source, due_date, priority, url, etc.)

**Dashboard Card**

- Custom Markdown card or Entities card displaying the unified task list
- Grouped by source or due date (configurable)
- Each task is clickable and links to the native app

## Features

### MVP (Phase 1)

- [ ] Aggregate open tasks from Google Tasks, GitHub Issues, and ClickUp
- [ ] Display in unified Home Assistant dashboard card
- [ ] Show task title, source, due date, priority
- [ ] Clickable links to open tasks in native app
- [ ] Simple filtering by source or due date range

### Phase 2 (Future)

- [ ] Search/quick-filter by keyword
- [ ] Priority/urgency indicator (color-coded or icon)
- [ ] Count badges (e.g., "3 due today", "12 open total")
- [ ] Configurable grouping (by source, by due date, by priority)
- [ ] Morning digest automation (send task summary notification)
- [ ] Due-date reminder notifications

### Phase 3 (Optional)

- [ ] Custom Home Assistant card (instead of Markdown) for better UI
- [ ] Sorting options (alphabetical, due date, priority)
- [ ] Exclude completed tasks
- [ ] Refresh interval tuning

## Configuration

### Prerequisites

- Home Assistant (version 2023.12 or later)
- API keys/tokens for:
  - Google Tasks API (OAuth2 or service account)
  - GitHub API (personal access token)
  - ClickUp API (API token)

### Setup Steps

1. **Enable APIs**
   - **Google Tasks:** Enable in Google Cloud Console, generate OAuth2 credentials
   - **GitHub:** Generate personal access token with `repo:read` scope
   - **ClickUp:** Generate API token from workspace settings
2. **Configure Home Assistant**
   - Add REST sensors to `configuration.yaml` (or split into separate YAML files)
   - Configure template sensor to parse and aggregate
   - Add dashboard card(s) to display

### Sample Configuration

_(to be populated during implementation)_

```yaml
# REST sensors for each API
# Template sensor for aggregation
# Dashboard YAML
```

## API Endpoints & Authentication

### Google Tasks

- **Endpoint:** `https://www.googleapis.com/tasks/v1/lists/{tasklist}/tasks`
- **Auth:** OAuth2 bearer token
- **Query params:** `showCompleted=false`

### GitHub

- **Endpoint:** `https://api.github.com/issues?assigned:me&state=open`
- **Auth:** Bearer token (personal access token)
- **Filters:** Repos matching `sr2*` pattern

### ClickUp

- **Endpoint:** `https://api.clickup.com/api/v2/team/{team_id}/task?assignees=[{user_id}]&status=open`
- **Auth:** Bearer token (ClickUp API token)
- **Filters:** Custom statuses (configurable)

## Data Schema

Unified task object (post-aggregation):

```json
{
  "title": "Task or issue title",
  "source": "google_tasks|github|clickup",
  "source_id": "task_id or issue_number",
  "due_date": "2026-06-15 or null",
  "priority": "high|medium|low|null",
  "url": "https://...",
  "assignee": "user@example.com or username",
  "description": "snippet or full text",
  "updated_at": "2026-06-01T12:34:56Z"
}
```

## Usage

Once configured:

1. Navigate to your Home Assistant dashboard
2. View the "Unified To-Dos" card
3. Click any task to open it in its native app
4. Filter or sort as needed

## Development Roadmap

- **Week 1:** REST sensors + template sensor for Google Tasks and ClickUp
- **Week 2:** Add GitHub Issues integration
- **Week 3:** Dashboard card and UI refinement
- **Week 4:** Automations and notifications
- **Future:** Custom card component if Markdown card is too limiting

## Troubleshooting

### API Rate Limits

- **Google Tasks:** 1 million requests/day per project
- **GitHub:** 60 req/hr (unauthenticated), 5,000 req/hr (authenticated)
- **ClickUp:** 180 req/minute per API token
- **Mitigation:** Set reasonable REST sensor update intervals (15–30 min recommended)

### Missing Tasks

- Verify API token/credentials are correct
- Check query parameters (assigned-to filters, status flags)
- Review Home Assistant logs for API errors

### Dashboard Not Updating

- Check REST sensor `last_updated` timestamp
- Verify template sensor is re-evaluating on REST sensor changes
- Consider manual refresh or interval adjustment

## Contributing

This is a personal project, but improvements and bug fixes are welcome. Testing across different Home Assistant versions is appreciated.

## License

MIT

## Notes

- This integration does not modify tasks in any source system; it is read-only.
- API credentials should be stored securely in Home Assistant secrets.
- Refresh intervals are configurable to balance freshness vs. API rate limit consumption.
