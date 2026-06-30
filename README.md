# Unified To-Do Aggregator for Home Assistant

A Home Assistant custom integration that aggregates open tasks from **Google
Tasks**, **GitHub Issues**, and **ClickUp** into a single, unified set of
sensors — and native **To-do list** entities — you can surface on one
dashboard.

Reading is the core job: data stays in its native system and each task keeps a
link to its source so you can jump straight into the full context (GitHub's
collaboration, ClickUp's workflow state, Google Tasks' simplicity). On top of
that, you can optionally **create new tasks** (choosing which service they land
in) and **mark tasks complete** right from Home Assistant — see
[Creating & completing tasks](#creating--completing-tasks). Everything else
(comments, code references, rich editing) still happens in the source app via
the task's link.

> Set up entirely from the Home Assistant UI — no YAML editing required.

## Problem Statement

Managing tasks across three systems creates context switching and reduced
visibility:

- **Google Tasks:** Personal and routine household items
- **GitHub Issues:** SR2 Industries coding projects (comments, code references, linking)
- **ClickUp:** SR2 Industries operational tasks (embroidery orders, heat press jobs, inventory, workflows)

This integration provides a centralized read-only aggregator that displays all
open tasks in one place while preserving the native strengths of each system.

## Design Principles

- **Source System Fidelity:** Data lives in its native system. Writes are limited to creating tasks and marking them complete; nothing is ever destructively synced.
- **Native Context Preservation:** Links and metadata are preserved so you can drill into the original system.
- **Minimal Overhead:** A single polling coordinator fetches all sources concurrently; one source failing never blanks out the others.
- **Simple Setup:** Paste tokens into the UI config flow — nothing to edit in `configuration.yaml`.

## How It Works

```
Google Tasks ──┐
               │
GitHub Issues ─┼──>  Update Coordinator  ──>  sensor.unified_todos      (total + full task list)
               │     (polls every N min)      sensor.github_issues      (per-source counts)
ClickUp ───────┘                              sensor.clickup
                                              sensor.google_tasks
```

The coordinator normalizes every task into one consistent schema (see
[Data Schema](#data-schema)), sorts by due date then priority, and exposes the
full list as the `tasks` attribute of `sensor.unified_todos`.

### Entities created

| Entity | State | Key attributes |
| --- | --- | --- |
| `sensor.unified_todos` | total open task count | `tasks` (full normalized list), `counts`, `errors` |
| `sensor.github_issues` *(if configured)* | open GitHub issues assigned to you | `tasks` |
| `sensor.clickup` *(if configured)* | open ClickUp tasks | `tasks` |
| `sensor.google_tasks` *(if configured)* | open Google Tasks | `tasks` |
| `todo.unified_todos` | **every** source's open tasks in one list | check off → completed in the right service automatically |
| `todo.github_issues` *(if configured)* | open GitHub issues, as a To-do list | add item → new issue; check off → close issue |
| `todo.clickup` *(if configured)* | open ClickUp tasks, as a To-do list | add item → new task; check off → done status |
| `todo.google_tasks` *(if configured)* | open Google Tasks, as a To-do list | add item → new task; check off → completed |

The `todo.*` entities work with Home Assistant's built-in **To-do list** card
and the mobile app. `todo.unified_todos` is the **combined** list — tick
anything off and the integration routes the completion to the correct service,
so you never have to think about which list a task came from (it doesn't offer
"add item", since the destination would be ambiguous — use a per-source list,
the [custom card](#unified-to-do-card), or the `create_task` service). Per-source
"Add item" is only offered once that source has a destination configured (see
[Creating & completing tasks](#creating--completing-tasks)).

## Installation

### Option A — HACS (custom repository)

1. In Home Assistant, go to **HACS → ⋮ (top right) → Custom repositories**.
2. Add `https://github.com/jeffreyrdubois/ToDo_Aggregator` with category **Integration**.
3. Find **Unified To-Do Aggregator** in HACS and click **Download**.
4. **Restart Home Assistant.**

### Option B — Manual

1. Copy the `custom_components/unified_todo` folder into your Home Assistant
   `config/custom_components/` directory.
2. **Restart Home Assistant.**

### Configure

After restarting, go to **Settings → Devices & Services → Add Integration**,
search for **Unified To-Do Aggregator**, and fill in credentials for the
sources you want. Leave a source blank to skip it — at least one is required.
Credentials are validated before the entry is created.

You can change tokens, the repo filter, or the refresh interval any time via
the integration's **Configure** button.

## Getting Your Tokens

### GitHub (personal access token)

1. GitHub → **Settings → Developer settings → Personal access tokens**.
2. Create a token with the **`repo`** scope (needed to read private/org issues).
3. Paste it into the **GitHub personal access token** field.
4. *(Optional)* Set a **repo filter** — a case-insensitive regex matched against
   each issue's `owner/repo` name, e.g. `sr2` to only include SR2 repos.
5. *(Optional)* Set **GitHub repo for new issues** (`owner/repo`) if you want to
   create issues from Home Assistant. The `repo` scope already covers this.

Fetches open **issues assigned to you** (pull requests are excluded). Marking a
GitHub item complete **closes** the issue.

### ClickUp (API token)

1. ClickUp → **Settings → Apps → API Token** → generate a personal token
   (starts with `pk_`).
2. Find your **Team / Workspace ID**: it's the number in your ClickUp URL,
   `https://app.clickup.com/<TEAM_ID>/...`.
3. Paste both into the ClickUp fields. Leave **"Only my assigned tasks"** on to
   limit results to tasks assigned to you.

### Google Tasks (OAuth client + refresh token)

Google Tasks has no simple API key, so this uses a one-time OAuth refresh
token:

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a
   project and **enable the Tasks API**.
2. Configure the **OAuth consent screen** (User Type *External* for a personal
   Gmail), then:
   - Add your own Google account under **Test users**, **and**
   - **Publish the app** (set publishing status to *In production*).

   Both steps matter: if the app is left in *Testing* you'll either be blocked
   with `Error 403: access_denied`, or — even after adding yourself as a test
   user — Google will **expire the refresh token after 7 days**. Publishing to
   production keeps the token durable. For personal use this does *not* require
   Google verification; you'll just click through an "unverified app" warning
   once (**Advanced → Go to … (unsafe)**).
3. Under **APIs & Services → Credentials**, create an **OAuth client ID** of
   type *Web application*. Add `https://developers.google.com/oauthplayground`
   as an authorized redirect URI. Note the **client ID** and **client secret**.
4. Go to the [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/):
   - Click the ⚙️ gear → check **"Use your own OAuth credentials"** and paste
     your client ID/secret.
   - In **Step 1**, authorize the Google Tasks scope (see below).
   - In **Step 2**, click **Exchange authorization code for tokens** and copy
     the **refresh token**.
5. Paste the **client ID**, **client secret**, and **refresh token** into the
   Google Tasks fields.

**Which scope?** The integration only reads tasks today, so either works:

| Scope | Use it if |
| --- | --- |
| `https://www.googleapis.com/auth/tasks` | You'll want create/edit/delete later — it's a superset, so reading works too. Avoids re-doing OAuth when write features land. *(recommended)* |
| `https://www.googleapis.com/auth/tasks.readonly` | You want the tightest, read-only token and don't mind re-authorizing later. |

Don't request both — `tasks` already includes everything `tasks.readonly` can
do. Changing scope later means re-running this OAuth step for a fresh token, so
if write access is on your roadmap, grant `tasks` now.

## Dashboard Card

Add a **Markdown card** to render the unified list with clickable links:

```yaml
type: markdown
content: |
  ## 📋 Unified To-Dos — {{ states('sensor.unified_todos') }} open
  {% for task in state_attr('sensor.unified_todos', 'tasks') %}
  - {% if task.url %}[{{ task.title }}]({{ task.url }}){% else %}{{ task.title }}{% endif %} — *{{ task.source }}*{% if task.due_date %} · 📅 {{ task.due_date }}{% endif %}{% if task.priority %} · ⚡ {{ task.priority }}{% endif %}
  {% endfor %}
```

To group by source, iterate over each per-source sensor's `tasks` attribute
instead, or filter the unified list with `selectattr('source', 'eq', 'github')`.

## Unified To-Do Card

The integration ships a **custom Lovelace card** that's registered
automatically — no resource setup needed. After updating and restarting, add a
card and search for **"Unified To-Do Card"**, or in YAML:

```yaml
type: custom:unified-todo-card
entity: sensor.unified_todos   # optional (default)
title: My To-Dos               # optional
list: Home Tasks               # optional — show only this list/repo
```

### Showing a single list

By default the card aggregates every task across all your services and lists.
Use the `list:` option to scope a card to just **one** list — for example a
single Google Tasks list when you keep several:

```yaml
type: custom:unified-todo-card
title: Home Tasks
list: Home Tasks               # the exact list name (case-insensitive)
```

`list:` matches each task's `list_name` — a **Google Tasks list** title, a
**ClickUp list** name, or a **GitHub `owner/repo`**. Pass several names as a
YAML list to combine a few lists into one card:

```yaml
type: custom:unified-todo-card
title: Personal
list:
  - Home Tasks
  - Errands
```

When no `list:` is set, each task shows a 🗂 chip with the list/repo it lives in.

#### New tasks go to the filtered list

When a card is scoped to a **single** list, the **＋ New task** form drops the
Service / Destination pickers and creates straight into that list — so a "Home
Tasks" card only ever adds to Home Tasks. This works even when the list is
currently empty. (Scoping to several lists at once leaves the normal pickers in
place, since there's no single destination to choose.)

Completing tasks keeps working exactly as on the unaggregated card.

It gives you, in one card:

- The full task list with **one-click complete** (checkbox → completes in the
  right service).
- A **＋ New task** form: pick the **service**, then pick a **destination** from
  a live list of your repos / lists (leave it on **Default** to use the
  configured default), add a title (and optional description / due date), and
  **Create**.

> If the card type isn't found right after updating, hard-refresh your browser
> (Ctrl/Cmd-Shift-R) — the frontend caches modules. It's served at
> `/unified_todo/unified-todo-card.js`.

## Creating & completing tasks

Two ways to write back:

### 1. To-do list card (no YAML)

Add a **To-do list** card and point it at `todo.github_issues`,
`todo.clickup`, or `todo.google_tasks`. Type into the box to create a task in
that service, and tick the checkbox to complete it. You pick the destination
service simply by choosing which list you add to.

### 2. Services (for buttons, scripts & automations)

| Service | What it does |
| --- | --- |
| `unified_todo.create_task` | Create a task. Fields: `source`, `summary`, optional `description`, `due_date`, `destination`. |
| `unified_todo.complete_task` | Complete a task. Fields: `source`, `task_id` (the task's `source_id`). |
| `unified_todo.list_destinations` | Returns the repos / lists you could create in for a `source`, plus the default. Response service (used by the card). |

```yaml
action: unified_todo.create_task
data:
  source: clickup
  summary: Order more navy thread
  description: 5 cones
  due_date: "2026-06-15"
  destination: "901234567"   # optional ClickUp list id; omit to use the default
```

`destination` is a GitHub `owner/repo`, a ClickUp list id, or a Google task
list id. Omit it to fall back to the configured default for that service.

### Where new tasks go

Creating needs a destination per service (completing does not). Set these in
the integration's **Configure** dialog:

| Service | Setting | Notes |
| --- | --- | --- |
| GitHub | **GitHub repo for new issues** (`owner/repo`) | New tasks open as issues here. Completing **closes** the issue. Token needs the `repo` scope (write). |
| ClickUp | **ClickUp list ID for new tasks** | The numeric list id from the list's URL. Completing moves the task to that list's *done* status. |
| Google Tasks | **Google task list ID** *(optional)* | Defaults to your primary list (`@default`). Needs the `tasks` scope — `tasks.readonly` can read but **cannot** create/complete. |

Until a service has its destination set, its To-do list still shows tasks and
lets you check them off — it just won't offer "add item".

## Recurring tasks

Create tasks automatically on a schedule — no automations or YAML required.
Each recurring rule is a **subentry** of the integration. You can create one
two ways:

**From the card (quickest):** in the **＋ New task** form, set **Repeat** to
Daily / Weekly / Monthly, pick the time (and weekdays / day-of-month), and the
"Create" button becomes **Schedule**.

**From Settings:** **Devices & Services → Unified To-Do Aggregator → ＋ Add
recurring task** — same fields, plus the **Due in N days** offset and an
explicit **Enabled** toggle.

Either way the rule captures the **service**, **title** (+ optional description /
destination), how it **repeats**, and the **time of day**. Leave **Destination**
blank to use that service's configured default.

The **card manages the whole lifecycle**: under the task list it shows a
**🔁 Recurring** section listing every rule with its schedule. From there you
can **enable/disable** (the checkbox), **✎ Edit** (loads it back into the form —
the button becomes *Save*), or **🗑 Delete** (click twice to confirm). Rules
also appear as rows under the integration in Settings if you prefer to manage
them there; both stay in sync.

Notes:
- Uses the same create path as the card/service, including per-source
  destinations. GitHub issues ignore the due date.
- A rule fires once at its scheduled time. If Home Assistant is offline at that
  moment, that occurrence is skipped (it does not back-fill).
- Monthly rules clamp to the last day on short months (e.g. "31st" → Feb 28/29).

## Data Schema

Every task is normalized to this shape (the `tasks` attribute is a list of
these):

```json
{
  "title": "Task or issue title",
  "source": "google_tasks | github | clickup",
  "source_id": "task_id or issue_number",
  "due_date": "2026-06-15 or null",
  "priority": "high | medium | low | null",
  "url": "https://...",
  "assignee": "user@example.com or username",
  "description": "snippet or null",
  "updated_at": "2026-06-01T12:34:56Z or null",
  "repo": "owner/repo or null",
  "list_id": "google task list id or null"
}
```

`repo` (GitHub) and `list_id` (Google Tasks) are carried so a completion knows
where to write back; they're `null` for sources that don't use them.

GitHub priority is inferred from labels (e.g. `priority: high`, `P1`, `urgent`);
ClickUp priority maps urgent/high → high, normal → medium, low → low; Google
Tasks has no priority.

## Roadmap

- [x] Aggregate open tasks from Google Tasks, GitHub Issues, and ClickUp
- [x] One unified sensor with the full task list, plus per-source count sensors
- [x] UI config flow with credential validation; editable options
- [x] Configurable refresh interval; resilient to a single source failing
- [x] Create tasks and mark them complete (To-do list entities + services)
- [x] Combined To-do list entity that completes across every source
- [x] Custom Lovelace card with complete + create (provider & destination picker)
- [x] Recurring tasks managed in-integration (daily/weekly/monthly schedules)
- [ ] Optional in-HA Google OAuth flow (no manual refresh-token step)
- [ ] Morning digest / due-date reminder automations (blueprint)
- [ ] Count badges and color-coded priority indicators

## Troubleshooting

**Setup fails validation.** The error points at the offending field — re-check
the token, the ClickUp team ID, or the Google refresh token. GitHub tokens need
the `repo` scope; Google tokens need the `tasks` (or `tasks.readonly`) scope.

**Google OAuth Playground shows `Error 403: access_denied`** ("app has not
completed the Google verification process"). Your OAuth consent screen is in
*Testing* mode and your account isn't an approved tester. Add your Google
account under **OAuth consent screen → Test users**, then retry. To stop the
refresh token from expiring after 7 days, also **publish the app** to *In
production* (no verification needed for personal use — just click through the
"unverified app" warning once). See [Google Tasks setup](#google-tasks-oauth-client--refresh-token).

**Google tasks stop loading after about a week.** The refresh token expired
because the consent screen is still in *Testing*. Publish the app to *In
production* and generate a fresh refresh token via the OAuth Playground.

**A source shows stale data or an `error` attribute.** When one source fails,
its last good results are kept and the failure is recorded in the `errors`
attribute of `sensor.unified_todos` (and `error` on the per-source sensor).
Check **Settings → System → Logs** for details.

**Nothing updates.** Confirm the refresh interval in the integration's
**Configure** dialog, and mind each provider's rate limits:

| Source | Rate limit |
| --- | --- |
| Google Tasks | ~1,000,000 requests/day per project |
| GitHub | 5,000 requests/hour (authenticated) |
| ClickUp | 100 requests/minute per token |

A 15–30 minute interval stays comfortably within all of these.

## Contributing

This is a personal side project, but improvements and bug fixes are welcome.
Testing across Home Assistant versions is appreciated.

## License

[MIT](LICENSE)
