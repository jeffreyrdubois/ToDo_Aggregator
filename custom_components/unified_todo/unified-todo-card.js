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
    this._rules = []; // recurring rules from the backend
    this._rulesLoaded = false;
    this._editingRuleId = null; // non-null while editing a recurring rule
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
    this._ensureRulesLoaded();
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

    this._rulesSection = document.createElement("div");
    this._rulesSection.className = "utc-rules";
    body.appendChild(this._rulesSection);

    body.appendChild(this._buildCreateForm());
    card.appendChild(body);
    this.appendChild(card);
    this._built = true;
    this._updateVisibility();
  }

  _updateVisibility() {
    const src = this._provider ? this._provider.value : "";
    const repeat = this._repeat ? this._repeat.value : "";
    const recurring = repeat !== "";
    // Absolute due-date only applies to one-off, non-GitHub tasks.
    if (this._dueField) {
      this._dueField.style.display = !recurring && src !== "github" ? "" : "none";
    }
    if (this._timeField) this._timeField.style.display = recurring ? "" : "none";
    if (this._weekdaysField) {
      this._weekdaysField.style.display = repeat === "weekly" ? "" : "none";
    }
    if (this._domField) {
      this._domField.style.display = repeat === "monthly" ? "" : "none";
    }
    const editing = this._editingRuleId !== null;
    if (this._createBtn) {
      this._createBtn.textContent = editing
        ? "Save"
        : recurring
          ? "Schedule"
          : "Create";
    }
    if (this._cancelBtn) {
      this._cancelBtn.style.display = editing ? "" : "none";
    }
    // While editing a recurring rule, "Once" makes no sense.
    if (editing && this._repeat) {
      const once = this._repeat.querySelector('option[value=""]');
      if (once) once.disabled = true;
    } else if (this._repeat) {
      const once = this._repeat.querySelector('option[value=""]');
      if (once) once.disabled = false;
    }
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
      .utc-days { display: flex; flex-wrap: wrap; gap: 4px; }
      .utc-day {
        width: 34px; height: 32px; border-radius: 6px; cursor: pointer; font: inherit;
        border: 1px solid var(--divider-color, #ccc);
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
      }
      .utc-day.sel { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
      .utc-rules { margin-top: 6px; }
      .utc-rules-head {
        font-weight: 500; color: var(--primary-text-color);
        margin: 10px 0 4px; display: flex; align-items: center; gap: 6px;
      }
      .utc-rule {
        display: flex; align-items: center; gap: 10px; padding: 6px 0;
        border-bottom: 1px solid var(--divider-color, #e0e0e0);
      }
      .utc-rule:last-child { border-bottom: none; }
      .utc-rule.disabled .utc-rule-main { opacity: 0.5; }
      .utc-rule-main { flex: 1; min-width: 0; }
      .utc-rule-title { color: var(--primary-text-color); }
      .utc-rule-sub { font-size: 12px; color: var(--secondary-text-color); }
      .utc-iconbtn {
        border: none; background: none; cursor: pointer; padding: 4px 6px;
        border-radius: 6px; color: var(--secondary-text-color); font: inherit;
      }
      .utc-iconbtn:hover { background: var(--secondary-background-color, #f0f0f0); }
      .utc-iconbtn.danger { color: var(--error-color, #e45649); }
      .utc-cancel {
        background: none; border: none; color: var(--secondary-text-color);
        cursor: pointer; font: inherit; text-decoration: underline;
      }
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

    // Repeat (turns the create into a recurring rule)
    this._repeat = document.createElement("select");
    for (const [val, label] of [
      ["", "Once"],
      ["daily", "Daily"],
      ["weekly", "Weekly"],
      ["monthly", "Monthly"],
    ]) {
      const o = document.createElement("option");
      o.value = val;
      o.textContent = label;
      this._repeat.appendChild(o);
    }
    this._repeat.addEventListener("change", () => this._updateVisibility());
    fields.appendChild(this._field("Repeat", this._repeat));

    // Time of day (recurring only)
    this._rtime = document.createElement("input");
    this._rtime.type = "time";
    this._rtime.value = "09:00";
    this._timeField = this._field("Time of day", this._rtime);
    fields.appendChild(this._timeField);

    // Weekday picker (weekly only)
    this._weekdaySel = new Set();
    this._weekdaysWrap = document.createElement("div");
    this._weekdaysWrap.className = "utc-days";
    for (const [val, label] of [
      ["mon", "Mo"],
      ["tue", "Tu"],
      ["wed", "We"],
      ["thu", "Th"],
      ["fri", "Fr"],
      ["sat", "Sa"],
      ["sun", "Su"],
    ]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "utc-day";
      b.textContent = label;
      b.dataset.day = val;
      b.addEventListener("click", () => {
        if (this._weekdaySel.has(val)) {
          this._weekdaySel.delete(val);
          b.classList.remove("sel");
        } else {
          this._weekdaySel.add(val);
          b.classList.add("sel");
        }
      });
      this._weekdaysWrap.appendChild(b);
    }
    this._weekdaysField = this._field("On these days", this._weekdaysWrap);
    fields.appendChild(this._weekdaysField);

    // Day of month (monthly only)
    this._dom = document.createElement("input");
    this._dom.type = "number";
    this._dom.min = "1";
    this._dom.max = "31";
    this._dom.value = "1";
    this._domField = this._field("Day of month", this._dom);
    fields.appendChild(this._domField);

    // Actions
    const actions = document.createElement("div");
    actions.className = "utc-actions";
    this._createBtn = document.createElement("button");
    this._createBtn.className = "utc-btn";
    this._createBtn.textContent = "Create";
    this._createBtn.addEventListener("click", () => this._onCreate());
    this._cancelBtn = document.createElement("button");
    this._cancelBtn.className = "utc-cancel";
    this._cancelBtn.textContent = "Cancel edit";
    this._cancelBtn.style.display = "none";
    this._cancelBtn.addEventListener("click", () => this._cancelEdit());
    this._note = document.createElement("span");
    this._note.className = "utc-note";
    actions.appendChild(this._createBtn);
    actions.appendChild(this._cancelBtn);
    actions.appendChild(this._note);
    fields.appendChild(actions);

    this._formDetails = details;

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
    this._updateVisibility();
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

    const editing = this._editingRuleId !== null;
    const recurring = this._repeat.value !== "";
    if (editing && !recurring)
      return this._setNote("A recurring rule must repeat.", true);

    let service;
    let data;
    if (recurring) {
      data = {
        source,
        summary,
        frequency: this._repeat.value,
        time: this._rtime.value || "09:00",
        enabled: true,
      };
      if (this._description.value.trim())
        data.description = this._description.value.trim();
      if (this._destination.value) data.destination = this._destination.value;
      if (this._repeat.value === "weekly") {
        if (this._weekdaySel.size === 0)
          return this._setNote("Pick at least one weekday.", true);
        data.weekdays = [...this._weekdaySel];
      }
      if (this._repeat.value === "monthly")
        data.day_of_month = Number(this._dom.value) || 1;
      if (editing) {
        data.rule_id = this._editingRuleId;
        service = "update_recurring_task";
      } else {
        service = "add_recurring_task";
      }
    } else {
      data = { source, summary };
      if (this._description.value.trim())
        data.description = this._description.value.trim();
      if (source !== "github" && this._due.value) data.due_date = this._due.value;
      if (this._destination.value) data.destination = this._destination.value;
      service = "create_task";
    }

    const verb = editing ? "Saving" : recurring ? "Scheduling" : "Creating";
    const done = editing ? "Saved ✓" : recurring ? "Scheduled ✓" : "Created ✓";
    this._busyCreate = true;
    this._createBtn.disabled = true;
    this._setNote(verb + "…");
    try {
      await this._hass.callService("unified_todo", service, data);
      this._summary.value = "";
      this._description.value = "";
      this._due.value = "";
      if (editing) {
        this._editingRuleId = null;
        this._repeat.value = "";
        this._weekdaySel = new Set();
        for (const b of this._weekdaysWrap.querySelectorAll(".utc-day"))
          b.classList.remove("sel");
      }
      this._updateVisibility();
      this._setNote(done);
      if (service !== "create_task") await this._loadRules();
    } catch (err) {
      this._setNote("Failed: " + this._errMsg(err), true);
    } finally {
      this._busyCreate = false;
      this._createBtn.disabled = false;
    }
  }

  // ----- recurring rules (CRUD) --------------------------------------------

  _ensureRulesLoaded() {
    if (this._rulesLoaded || !this._hass) return;
    this._rulesLoaded = true;
    this._loadRules();
  }

  async _loadRules() {
    try {
      const res = await this._hass.callService(
        "unified_todo",
        "list_recurring_tasks",
        {},
        undefined,
        false,
        true
      );
      const payload = res && res.response ? res.response : res || {};
      this._rules = payload.rules || [];
    } catch (err) {
      // Keep the previous list and allow a retry on the next hass update
      // (the entry may be mid-reload).
      this._rulesLoaded = false;
      return;
    }
    this._renderRules();
  }

  _scheduleSummary(rule) {
    const time = rule.time || "09:00";
    const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
    if (rule.frequency === "daily") return `Daily · ${time}`;
    if (rule.frequency === "weekly") {
      const days = (rule.weekdays || []).map((d) => cap(d)).join(", ") || "—";
      return `Weekly · ${days} · ${time}`;
    }
    if (rule.frequency === "monthly")
      return `Monthly · day ${rule.day_of_month || 1} · ${time}`;
    return time;
  }

  _renderRules() {
    if (!this._rulesSection) return;
    this._rulesSection.innerHTML = "";
    if (!this._rules.length) return;

    const head = document.createElement("div");
    head.className = "utc-rules-head";
    head.textContent = `🔁 Recurring (${this._rules.length})`;
    this._rulesSection.appendChild(head);

    for (const rule of this._rules) {
      this._rulesSection.appendChild(this._ruleRow(rule));
    }
  }

  _ruleRow(rule) {
    const row = document.createElement("div");
    row.className = "utc-rule" + (rule.enabled ? "" : " disabled");

    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.className = "utc-check";
    toggle.checked = !!rule.enabled;
    toggle.title = rule.enabled ? "Disable" : "Enable";
    toggle.addEventListener("change", () => this._toggleRule(rule, toggle));
    row.appendChild(toggle);

    const main = document.createElement("div");
    main.className = "utc-rule-main";
    const title = document.createElement("div");
    title.className = "utc-rule-title";
    title.textContent = rule.summary || "(untitled)";
    const sub = document.createElement("div");
    sub.className = "utc-rule-sub";
    sub.textContent = `${this._scheduleSummary(rule)} · ${
      SOURCE_LABELS[rule.source] || rule.source
    }`;
    main.appendChild(title);
    main.appendChild(sub);
    row.appendChild(main);

    const edit = document.createElement("button");
    edit.className = "utc-iconbtn";
    edit.textContent = "✎ Edit";
    edit.addEventListener("click", () => this._editRule(rule));
    row.appendChild(edit);

    const del = document.createElement("button");
    del.className = "utc-iconbtn danger";
    del.textContent = "🗑 Delete";
    del.addEventListener("click", () => this._deleteRule(rule, del));
    row.appendChild(del);

    return row;
  }

  _ruleToServiceData(rule) {
    const data = {
      source: rule.source,
      summary: rule.summary,
      frequency: rule.frequency,
      time: rule.time || "09:00",
      enabled: !!rule.enabled,
    };
    if (rule.description) data.description = rule.description;
    if (rule.destination) data.destination = rule.destination;
    if (rule.frequency === "weekly") data.weekdays = rule.weekdays || [];
    if (rule.frequency === "monthly")
      data.day_of_month = rule.day_of_month || 1;
    if (rule.due_offset_days != null)
      data.due_offset_days = rule.due_offset_days;
    return data;
  }

  async _toggleRule(rule, toggle) {
    const data = this._ruleToServiceData(rule);
    data.enabled = !rule.enabled;
    data.rule_id = rule.id;
    try {
      await this._hass.callService("unified_todo", "update_recurring_task", data);
      await this._loadRules();
    } catch (err) {
      toggle.checked = !!rule.enabled;
      this._setNote("Update failed: " + this._errMsg(err), true);
    }
  }

  async _editRule(rule) {
    if (this._formDetails) this._formDetails.open = true;
    this._editingRuleId = rule.id;
    this._provider.value = rule.source;
    await this._onProviderChange();
    // Select the rule's destination once options are loaded.
    this._destination.value = rule.destination || "";
    this._summary.value = rule.summary || "";
    this._description.value = rule.description || "";
    this._repeat.value = rule.frequency || "weekly";
    this._rtime.value = rule.time || "09:00";
    this._dom.value = rule.day_of_month || 1;
    this._weekdaySel = new Set(rule.weekdays || []);
    for (const btn of this._weekdaysWrap.querySelectorAll(".utc-day")) {
      btn.classList.toggle("sel", this._weekdaySel.has(btn.dataset.day));
    }
    this._updateVisibility();
    this._setNote(`Editing “${rule.summary}”`);
    this.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  _cancelEdit() {
    this._editingRuleId = null;
    this._summary.value = "";
    this._description.value = "";
    this._repeat.value = "";
    this._weekdaySel = new Set();
    for (const btn of this._weekdaysWrap.querySelectorAll(".utc-day"))
      btn.classList.remove("sel");
    this._updateVisibility();
    this._setNote("");
  }

  async _deleteRule(rule, btn) {
    if (btn.dataset.confirm !== "1") {
      btn.dataset.confirm = "1";
      btn.textContent = "Sure?";
      setTimeout(() => {
        btn.dataset.confirm = "";
        btn.textContent = "🗑 Delete";
      }, 3000);
      return;
    }
    try {
      await this._hass.callService("unified_todo", "delete_recurring_task", {
        rule_id: rule.id,
      });
      if (this._editingRuleId === rule.id) this._cancelEdit();
      await this._loadRules();
    } catch (err) {
      this._setNote("Delete failed: " + this._errMsg(err), true);
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
