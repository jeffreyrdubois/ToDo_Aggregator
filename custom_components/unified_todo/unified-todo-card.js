/**
 * Unified To-Do Card
 *
 * A Lovelace card for the Unified To-Do Aggregator custom integration.
 * Shows every open task across GitHub / ClickUp / Google Tasks with one-click
 * completion, and a create form that lets you pick a provider and then a
 * destination (repo / list) — falling back to the configured default if you
 * leave the destination on "Default".
 *
 * Dependency-free: plain custom element, no build step.
 *
 * Usage:
 *   type: custom:unified-todo-card
 *   entity: sensor.unified_todos   # optional, this is the default
 *   title: My To-Dos               # optional
 */

const SOURCE_LABELS = {
  github: "GitHub",
  clickup: "ClickUp",
  google_tasks: "Google Tasks",
};

const PRIORITY_COLOR = {
  high: "#e45649",
  medium: "#d19a66",
  low: "#56a64b",
};

class UnifiedTodoCard extends HTMLElement {
  constructor() {
    super();
    this._built = false;
    this._destCache = {}; // source -> { destinations, default }
    this._pending = new Set(); // "source:id" currently completing
    this._busyCreate = false;
  }

  setConfig(config) {
    this._config = Object.assign(
      { entity: "sensor.unified_todos", title: "Unified To-Dos" },
      config || {}
    );
    this._built = false;
    if (this.isConnected) this._build();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._renderTasks();
    this._syncProviders();
  }

  getCardSize() {
    const tasks = this._tasks();
    return 2 + Math.min(tasks.length, 10);
  }

  // ----- data helpers -------------------------------------------------------

  _stateObj() {
    if (!this._hass || !this._config) return null;
    return this._hass.states[this._config.entity] || null;
  }

  _tasks() {
    const s = this._stateObj();
    const tasks = s && s.attributes ? s.attributes.tasks : null;
    return Array.isArray(tasks) ? tasks : [];
  }

  _sources() {
    const s = this._stateObj();
    const counts = (s && s.attributes && s.attributes.counts) || {};
    const fromCounts = Object.keys(counts);
    if (fromCounts.length) return fromCounts;
    // Fall back to whatever sources actually appear in the task list.
    return [...new Set(this._tasks().map((t) => t.source))];
  }

  // ----- build (once) -------------------------------------------------------

  _build() {
    if (!this._config) return;
    this.innerHTML = "";
    const card = document.createElement("ha-card");
    card.header = this._config.title;
    card.appendChild(this._styleEl());

    const body = document.createElement("div");
    body.className = "utc-body";

    this._taskList = document.createElement("div");
    this._taskList.className = "utc-tasks";
    body.appendChild(this._taskList);

    body.appendChild(this._buildCreateForm());
    card.appendChild(body);
    this.appendChild(card);
    this._built = true;
  }

  _styleEl() {
    const style = document.createElement("style");
    style.textContent = `
      .utc-body { padding: 0 16px 16px; }
      .utc-tasks { display: flex; flex-direction: column; }
      .utc-row {
        display: flex; align-items: flex-start; gap: 10px;
        padding: 8px 0; border-bottom: 1px solid var(--divider-color, #e0e0e0);
      }
      .utc-row:last-child { border-bottom: none; }
      .utc-row.pending { opacity: 0.45; pointer-events: none; }
      .utc-check {
        width: 20px; height: 20px; margin-top: 2px; cursor: pointer; flex: none;
        accent-color: var(--primary-color);
      }
      .utc-main { flex: 1; min-width: 0; }
      .utc-title { color: var(--primary-text-color); text-decoration: none; word-break: break-word; }
      .utc-title:hover { text-decoration: underline; }
      .utc-meta { margin-top: 2px; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
      .utc-chip {
        font-size: 11px; line-height: 1; padding: 3px 7px; border-radius: 10px;
        background: var(--secondary-background-color, #f0f0f0);
        color: var(--secondary-text-color);
      }
      .utc-chip.src { background: var(--primary-color); color: var(--text-primary-color, #fff); }
      .utc-empty { padding: 16px 0; color: var(--secondary-text-color); text-align: center; }
      .utc-create { margin-top: 14px; border-top: 1px solid var(--divider-color, #e0e0e0); padding-top: 12px; }
      .utc-create summary { cursor: pointer; font-weight: 500; color: var(--primary-text-color); }
      .utc-fields { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; }
      .utc-fields label { font-size: 12px; color: var(--secondary-text-color); display: block; margin-bottom: 2px; }
      .utc-fields input, .utc-fields select, .utc-fields textarea {
        width: 100%; box-sizing: border-box; padding: 8px;
        border: 1px solid var(--divider-color, #ccc); border-radius: 6px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        font: inherit;
      }
      .utc-actions { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
      .utc-btn {
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        border: none; border-radius: 6px; padding: 8px 16px; cursor: pointer; font: inherit;
      }
      .utc-btn[disabled] { opacity: 0.5; cursor: default; }
      .utc-note { font-size: 12px; color: var(--secondary-text-color); }
      .utc-note.err { color: var(--error-color, #e45649); }
    `;
    return style;
  }

  _buildCreateForm() {
    const details = document.createElement("details");
    details.className = "utc-create";
    const summary = document.createElement("summary");
    summary.textContent = "＋ New task";
    details.appendChild(summary);

    const fields = document.createElement("div");
    fields.className = "utc-fields";

    // Provider
    this._provider = document.createElement("select");
    this._provider.addEventListener("change", () => this._onProviderChange());
    fields.appendChild(this._field("Service", this._provider));

    // Destination
    this._destination = document.createElement("select");
    fields.appendChild(this._field("Destination", this._destination));

    // Summary
    this._summary = document.createElement("input");
    this._summary.type = "text";
    this._summary.placeholder = "What needs doing?";
    fields.appendChild(this._field("Title", this._summary));

    // Description
    this._description = document.createElement("textarea");
    this._description.rows = 2;
    this._description.placeholder = "Optional notes / issue body";
    fields.appendChild(this._field("Description", this._description));

    // Due date
    this._due = document.createElement("input");
    this._due.type = "date";
    this._dueField = this._field("Due date", this._due);
    fields.appendChild(this._dueField);

    // Actions
    const actions = document.createElement("div");
    actions.className = "utc-actions";
    this._createBtn = document.createElement("button");
    this._createBtn.className = "utc-btn";
    this._createBtn.textContent = "Create";
    this._createBtn.addEventListener("click", () => this._onCreate());
    this._note = document.createElement("span");
    this._note.className = "utc-note";
    actions.appendChild(this._createBtn);
    actions.appendChild(this._note);
    fields.appendChild(actions);

    details.appendChild(fields);
    return details;
  }

  _field(labelText, input) {
    const wrap = document.createElement("div");
    const label = document.createElement("label");
    label.textContent = labelText;
    wrap.appendChild(label);
    wrap.appendChild(input);
    return wrap;
  }

  // ----- task list rendering ------------------------------------------------

  _renderTasks() {
    if (!this._taskList) return;
    const tasks = this._tasks();
    this._taskList.innerHTML = "";

    if (!this._stateObj()) {
      this._taskList.appendChild(
        this._empty(`Entity ${this._config.entity} not found.`)
      );
      return;
    }
    if (!tasks.length) {
      this._taskList.appendChild(this._empty("🎉 Nothing open — all caught up!"));
      return;
    }

    for (const task of tasks) {
      this._taskList.appendChild(this._taskRow(task));
    }
  }

  _empty(text) {
    const d = document.createElement("div");
    d.className = "utc-empty";
    d.textContent = text;
    return d;
  }

  _taskRow(task) {
    const key = `${task.source}:${task.source_id}`;
    const row = document.createElement("div");
    row.className = "utc-row" + (this._pending.has(key) ? " pending" : "");

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "utc-check";
    check.title = "Mark complete";
    check.checked = this._pending.has(key);
    check.addEventListener("change", () => this._onComplete(task, check));
    row.appendChild(check);

    const main = document.createElement("div");
    main.className = "utc-main";

    let title;
    if (task.url) {
      title = document.createElement("a");
      title.href = task.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    } else {
      title = document.createElement("span");
    }
    title.className = "utc-title";
    title.textContent = task.title || "(untitled)";
    main.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "utc-meta";
    meta.appendChild(this._chip(SOURCE_LABELS[task.source] || task.source, "src"));
    if (task.due_date) meta.appendChild(this._chip("📅 " + task.due_date));
    if (task.priority) {
      const c = this._chip("⚡ " + task.priority);
      c.style.background = PRIORITY_COLOR[task.priority] || "";
      if (PRIORITY_COLOR[task.priority]) c.style.color = "#fff";
      meta.appendChild(c);
    }
    main.appendChild(meta);
    row.appendChild(main);
    return row;
  }

  _chip(text, extra) {
    const s = document.createElement("span");
    s.className = "utc-chip" + (extra ? " " + extra : "");
    s.textContent = text;
    return s;
  }

  // ----- providers / destinations ------------------------------------------

  _syncProviders() {
    if (!this._provider) return;
    const sources = this._sources();
    const current = this._provider.value;
    const existing = Array.from(this._provider.options).map((o) => o.value);
    const same =
      existing.length === sources.length &&
      existing.every((v, i) => v === sources[i]);
    if (same) return;

    this._provider.innerHTML = "";
    for (const src of sources) {
      const opt = document.createElement("option");
      opt.value = src;
      opt.textContent = SOURCE_LABELS[src] || src;
      this._provider.appendChild(opt);
    }
    if (sources.includes(current)) this._provider.value = current;
    this._onProviderChange();
  }

  async _onProviderChange() {
    const source = this._provider.value;
    if (!source) return;
    // GitHub issues have no due date.
    this._dueField.style.display = source === "github" ? "none" : "";
    this._destination.innerHTML = "";
    const loading = document.createElement("option");
    loading.textContent = "Loading…";
    loading.value = "";
    this._destination.appendChild(loading);

    let data = this._destCache[source];
    if (!data) {
      try {
        data = await this._callList(source);
        this._destCache[source] = data;
      } catch (err) {
        data = { destinations: [], default: null, error: String(err) };
      }
    }
    this._fillDestinations(data);
  }

  async _callList(source) {
    const res = await this._hass.callService(
      "unified_todo",
      "list_destinations",
      { source },
      undefined,
      false,
      true
    );
    const payload = res && res.response ? res.response : res || {};
    return {
      destinations: payload.destinations || [],
      default: payload.default || null,
    };
  }

  _fillDestinations(data) {
    this._destination.innerHTML = "";
    const def = document.createElement("option");
    def.value = "";
    def.textContent = data.default
      ? `Default (${data.default})`
      : "Default";
    this._destination.appendChild(def);
    for (const d of data.destinations) {
      const opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.name;
      this._destination.appendChild(opt);
    }
    if (data.error) {
      this._setNote("Couldn't load destinations: " + data.error, true);
    }
  }

  // ----- actions ------------------------------------------------------------

  async _onComplete(task, check) {
    const key = `${task.source}:${task.source_id}`;
    if (this._pending.has(key)) return;
    this._pending.add(key);
    check.closest(".utc-row").classList.add("pending");
    try {
      await this._hass.callService("unified_todo", "complete_task", {
        source: task.source,
        task_id: String(task.source_id),
      });
      // The coordinator refresh will drop the task from the list shortly.
    } catch (err) {
      this._pending.delete(key);
      check.checked = false;
      check.closest(".utc-row").classList.remove("pending");
      this._setNote("Complete failed: " + this._errMsg(err), true);
    }
  }

  async _onCreate() {
    if (this._busyCreate) return;
    const source = this._provider.value;
    const summary = this._summary.value.trim();
    if (!source) return this._setNote("Pick a service first.", true);
    if (!summary) return this._setNote("A title is required.", true);

    const data = { source, summary };
    if (this._description.value.trim()) data.description = this._description.value.trim();
    if (source !== "github" && this._due.value) data.due_date = this._due.value;
    if (this._destination.value) data.destination = this._destination.value;

    this._busyCreate = true;
    this._createBtn.disabled = true;
    this._setNote("Creating…");
    try {
      await this._hass.callService("unified_todo", "create_task", data);
      this._summary.value = "";
      this._description.value = "";
      this._due.value = "";
      this._setNote("Created ✓");
    } catch (err) {
      this._setNote("Create failed: " + this._errMsg(err), true);
    } finally {
      this._busyCreate = false;
      this._createBtn.disabled = false;
    }
  }

  _errMsg(err) {
    if (!err) return "unknown error";
    if (err.message) return err.message;
    if (err.body && err.body.message) return err.body.message;
    return String(err);
  }

  _setNote(text, isError) {
    if (!this._note) return;
    this._note.textContent = text;
    this._note.className = "utc-note" + (isError ? " err" : "");
  }

  static getStubConfig() {
    return { entity: "sensor.unified_todos", title: "Unified To-Dos" };
  }
}

customElements.define("unified-todo-card", UnifiedTodoCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "unified-todo-card",
  name: "Unified To-Do Card",
  description:
    "Aggregated tasks from GitHub, ClickUp and Google Tasks with complete & create.",
  preview: false,
});

console.info("%c UNIFIED-TODO-CARD ", "background:#03a9f4;color:#fff;border-radius:3px");
