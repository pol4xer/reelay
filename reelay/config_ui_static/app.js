"use strict";

const apiToken = document.querySelector('meta[name="reelay-token"]')?.content || "";
const sections = {
  telegram: {
    badge: "TELEGRAM BOT",
    title: "Telegram",
    description: "Кто может управлять ботом и каким токеном он подключается к Telegram.",
    icon: "➤",
    link: "https://t.me/BotFather",
  },
  instagram_meta: {
    badge: "INSTAGRAM + META",
    title: "Instagram & Meta",
    description: "Основной Instagram-аккаунт, Graph API и доступ к исходным роликам.",
    icon: "◎",
    link: "https://developers.facebook.com/apps/",
  },
  facebook: {
    badge: "FACEBOOK PAGE",
    title: "Facebook Page",
    description: "Публикация Reels на связанную Facebook Page через официальный Video API.",
    icon: "f",
    link: "https://developers.facebook.com/docs/video-api/guides/reels-publishing/",
  },
  threads: {
    badge: "THREADS API",
    title: "Threads",
    description: "Видео-публикации в Threads через одноразовый Cloudflare Quick Tunnel.",
    icon: "@",
    link: "https://developers.facebook.com/docs/threads/",
  },
  youtube: {
    badge: "YOUTUBE DATA API",
    title: "YouTube Shorts",
    description: "OAuth-канал, видимость и автоматическая загрузка вертикальных Shorts.",
    icon: "▶",
    link: "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
  },
  tiktok: {
    badge: "TIKTOK CONTENT POSTING API",
    title: "TikTok Inbox",
    description: "Загрузка MP4 в Inbox: откройте уведомление TikTok и завершите публикацию вручную.",
    icon: "♪",
    link: "https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content",
  },
  schedule_storage: {
    badge: "AUTOMATION",
    title: "Расписание & файлы",
    description: "Частота публикаций, catch-up после сна, hashtags и очистка MP4.",
    icon: "◷",
    link: "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones",
  },
};

const platformPresentation = {
  instagram: { name: "Instagram", logo: "◎", section: "instagram_meta" },
  facebook: { name: "Facebook Page", logo: "f", section: "facebook" },
  threads: { name: "Threads", logo: "@", section: "threads" },
  youtube: { name: "YouTube Shorts", logo: "▶", section: "youtube" },
  tiktok: { name: "TikTok Inbox", logo: "♪", section: "tiktok" },
};

const state = {
  schema: [],
  values: {},
  configured: {},
  drafts: {},
  controls: new Map(),
  dirty: new Set(),
  errors: {},
  activeSection: "overview",
  status: null,
  saving: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-Reelay-Token", apiToken);
  if (options.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401 && !sessionStorage.getItem("reelay-token-reload")) {
    sessionStorage.setItem("reelay-token-reload", "1");
    window.location.reload();
    return new Promise(() => {});
  }
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.payload = payload;
    throw error;
  }
  sessionStorage.removeItem("reelay-token-reload");
  return payload;
}

async function loadAll() {
  try {
    const [config, status] = await Promise.all([api("/api/config"), api("/api/status")]);
    state.schema = config.schema || [];
    state.values = { ...(config.values || {}) };
    state.configured = { ...(config.configured || {}) };
    state.status = status;
    renderEverything();
  } catch (error) {
    showToast("Не удалось открыть настройки", humanError(error), true);
  } finally {
    setTimeout(() => $("#loading-screen").classList.add("is-hidden"), 120);
  }
}

function renderEverything() {
  renderStatus();
  renderNavigationState();
  showSection(state.activeSection);
  updateSaveBar();
}

function renderStatus() {
  const status = state.status || {};
  const service = status.service || {};
  const queue = status.queue || {};
  const config = status.config || {};
  const byStatus = queue.byStatus || {};
  const running = Boolean(service.running);

  $("#service-pill").classList.toggle("is-running", running);
  $("#service-pill").classList.toggle("is-error", service.installed && !running);
  $("#service-pill-text").textContent = running
    ? `Reelay работает · PID ${service.pid || "—"}`
    : service.installed
      ? "LaunchAgent остановлен"
      : "LaunchAgent не установлен";

  $("#hero-title").textContent = running
    ? "Reelay работает автономно"
    : "Reelay требует внимания";
  $("#hero-copy").textContent = running
    ? "LaunchAgent активен. Изменения можно сохранить и применить одним перезапуском."
    : "Проверьте конфигурацию и запустите LaunchAgent из панели или Makefile.";
  $("#metric-service").textContent = running ? "Online" : "Offline";
  $("#metric-service-detail").textContent = service.installed ? "macOS LaunchAgent" : "не установлен";
  $("#metric-queued").textContent = queue.available ? String(byStatus.queued ?? 0) : "—";
  $("#metric-queue-detail").textContent = queue.paused ? "очередь на паузе" : "видео ожидают";
  $("#metric-published").textContent = queue.available ? String(byStatus.published ?? 0) : "—";
  $("#metric-config").textContent = config.valid ? "Готово" : "Проверить";
  $("#metric-config-detail").textContent = config.valid
    ? "обязательные поля заполнены"
    : `${Object.keys(config.errors || {}).length} полей требуют внимания`;

  const platformList = $("#platform-list");
  platformList.replaceChildren();
  for (const [key, presentation] of Object.entries(platformPresentation)) {
    const enabled = key === "instagram" || Boolean(config.enabledPlatforms?.[key]);
    const row = element("button", "platform-row");
    row.type = "button";
    row.addEventListener("click", () => activateNavigation(presentation.section));
    const logo = element("span", "platform-logo", presentation.logo);
    const copy = document.createElement("span");
    copy.append(element("strong", "", presentation.name));
    copy.append(element("small", "", enabled ? "Включено в общий pipeline" : "Публикация выключена"));
    const badge = element("span", `platform-badge${enabled ? " is-on" : ""}`, enabled ? "Active" : "Off");
    row.append(logo, copy, badge);
    platformList.append(row);
  }
}

function renderNavigationState() {
  for (const button of $$(".nav-item[data-section]")) {
    const section = button.dataset.section;
    const dot = button.querySelector(".nav-state");
    if (!dot) continue;
    const fields = state.schema.filter((field) => field.section === section);
    const required = fields.filter((field) => isFieldRequired(field));
    const ready = required.every((field) => fieldValuePresent(field));
    dot.className = `nav-state ${ready ? "is-ready" : "is-warning"}`;
  }
}

function showSection(section) {
  state.activeSection = section;
  $$(".nav-item").forEach((item) => item.classList.toggle("is-active", item.dataset.section === section));
  const overview = section === "overview";
  $("#overview-panel").hidden = !overview;
  $("#settings-panel").hidden = overview;
  if (overview) {
    setPageHeader("REELAY SETTINGS", "Обзор", "Состояние сервиса, очереди и всех подключённых платформ.");
    return;
  }

  const meta = sections[section];
  setPageHeader(meta.badge, meta.title, meta.description);
  $("#section-icon").textContent = meta.icon;
  $("#section-badge").textContent = meta.badge;
  $("#section-title").textContent = meta.title;
  $("#section-help").textContent = meta.description;
  const link = $("#section-link");
  link.href = meta.link;
  link.hidden = !meta.link;
  renderForm(section);
}

function setPageHeader(eyebrow, title, description) {
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  $("#page-description").textContent = description;
}

function renderForm(section) {
  const grid = $("#fields-grid");
  grid.replaceChildren();
  state.controls.clear();
  const fields = state.schema.filter((field) => field.section === section);
  for (const field of fields) grid.append(buildField(field));
}

function buildField(field) {
  const isToggle = field.type === "toggle";
  const card = element("div", `field-card${isToggle ? " is-toggle" : ""}`);
  card.dataset.key = field.key;
  const textBlock = document.createElement("div");
  const top = element("div", "field-top");
  const label = element("label", "field-label", field.label || field.title || field.key);
  label.htmlFor = `field-${field.key}`;
  if (isFieldRequired(field)) label.append(element("span", "required-mark", " ·"));
  top.append(label);
  if (field.secret && state.configured[field.key]) top.append(element("span", "configured-badge", "Сохранено"));
  textBlock.append(top);

  const help = element("p", "field-help", field.help || "");
  if (field.link) {
    help.append(" ");
    const anchor = element("a", "", "Инструкция ↗");
    anchor.href = field.link;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    help.append(anchor);
  }
  textBlock.append(help);

  const error = element("p", "field-error", state.errors[field.key] || "");
  let control;
  if (isToggle) {
    const switchLabel = element("label", "switch");
    control = document.createElement("input");
    control.type = "checkbox";
    control.id = `field-${field.key}`;
    control.checked = String(draftValue(field) ?? field.default) === "true";
    switchLabel.append(control, element("span", "switch-track"));
    card.append(textBlock, switchLabel, error);
  } else {
    const wrap = element("div", "control-wrap");
    if (field.type === "select") {
      control = element("select", "field-select");
      for (const option of field.options || []) {
        const optionNode = document.createElement("option");
        optionNode.value = option.value;
        optionNode.textContent = option.label;
        control.append(optionNode);
      }
      control.value = draftValue(field) || field.default || "";
    } else {
      control = element("input", `field-input${field.secret ? " is-password" : ""}`);
      control.type = field.secret ? "password" : ({ number: "number", time: "time" }[field.type] || "text");
      control.value = field.secret ? (state.drafts[field.key] || "") : (draftValue(field) ?? field.default ?? "");
      control.placeholder = field.secret && state.configured[field.key]
        ? "Сохранено — введите только для замены"
        : (field.placeholder || "");
      if (field.type === "number") control.inputMode = "numeric";
    }
    control.id = `field-${field.key}`;
    control.autocomplete = "off";
    wrap.append(control);
    if (field.secret) {
      const reveal = element("button", "reveal-button", "◉");
      reveal.type = "button";
      reveal.title = "Показать или скрыть значение";
      reveal.addEventListener("click", () => {
        control.type = control.type === "password" ? "text" : "password";
      });
      wrap.append(reveal);
    }
    card.append(textBlock, wrap, error);
  }

  control.dataset.key = field.key;
  control.addEventListener("input", () => markDirty(field, control));
  control.addEventListener("change", () => markDirty(field, control));
  state.controls.set(field.key, { control, field, card, error });
  return card;
}

function markDirty(field, control) {
  const current = controlValue(field, control);
  const original = field.secret ? "" : String(state.values[field.key] ?? field.default ?? "");
  if (String(current) === original) {
    state.dirty.delete(field.key);
    delete state.drafts[field.key];
  } else {
    state.dirty.add(field.key);
    state.drafts[field.key] = current;
  }
  delete state.errors[field.key];
  const record = state.controls.get(field.key);
  if (record) {
    record.error.textContent = "";
    record.card.classList.remove("has-error");
  }
  updateSaveBar();
}

function controlValue(field, control) {
  return field.type === "toggle" ? (control.checked ? "true" : "false") : control.value.trim();
}

function isFieldRequired(field) {
  if (field.required) return true;
  if (!field.depends_on) return false;
  return String(currentValue(field.depends_on)) === "true";
}

function fieldValuePresent(field) {
  if (field.secret) return Boolean(state.configured[field.key] || currentValue(field.key));
  return Boolean(String(currentValue(field.key) ?? "").trim());
}

function currentValue(key) {
  const record = state.controls.get(key);
  if (record) return controlValue(record.field, record.control);
  return key in state.drafts ? state.drafts[key] : (state.values[key] ?? "");
}

function draftValue(field) {
  return field.key in state.drafts
    ? state.drafts[field.key]
    : (state.values[field.key] ?? field.default ?? "");
}

function updateSaveBar() {
  const dirty = state.dirty.size > 0;
  $("#save-bar").classList.toggle("is-dirty", dirty);
  $("#save-title").textContent = dirty ? `${state.dirty.size} несохранённых изменений` : "Нет изменений";
  $("#save-copy").textContent = dirty ? "Сохраните или отмените изменения" : "Все настройки сохранены";
  for (const button of [$("#discard-button"), $("#save-button"), $("#save-restart-button"), $("#header-save-button")]) {
    button.disabled = !dirty || state.saving;
  }
}

function discardChanges() {
  state.dirty.clear();
  state.drafts = {};
  state.errors = {};
  if (state.activeSection !== "overview") renderForm(state.activeSection);
  updateSaveBar();
}

async function saveConfig(restart) {
  if (!state.dirty.size || state.saving) return;
  state.saving = true;
  updateSaveBar();
  const values = {};
  for (const key of state.dirty) {
    values[key] = state.drafts[key];
  }
  try {
    const result = await api("/api/config", {
      method: "POST",
      body: JSON.stringify({ values, restart }),
    });
    state.dirty.clear();
    state.drafts = {};
    state.errors = {};
    const refreshed = await api("/api/config");
    state.values = { ...(refreshed.values || {}) };
    state.configured = { ...(refreshed.configured || {}) };
    state.status = await api("/api/status");
    renderEverything();
    const restartResult = result.restart || {};
    if (restart && !restartResult.ok) showToast("Настройки сохранены", restartResult.message || "Сервис нужно перезапустить позже.", true);
    else showToast("Готово", restart ? "Настройки сохранены, Reelay перезапущен." : "Настройки сохранены локально.");
  } catch (error) {
    if (error.payload?.errors) applyErrors(error.payload.errors);
    showToast("Не удалось сохранить", humanError(error), true);
  } finally {
    state.saving = false;
    updateSaveBar();
  }
}

function applyErrors(errors) {
  state.errors = { ...errors };
  const firstKey = Object.keys(errors)[0];
  const field = state.schema.find((item) => item.key === firstKey);
  if (field && field.section !== state.activeSection) activateNavigation(field.section);
  requestAnimationFrame(() => {
    for (const [key, message] of Object.entries(errors)) {
      const record = state.controls.get(key);
      if (!record) continue;
      record.error.textContent = message;
      record.card.classList.add("has-error");
    }
    state.controls.get(firstKey)?.control.focus();
  });
}

function humanError(error) {
  const code = error?.payload?.error || error?.message || "unknown_error";
  const translations = {
    invalid_api_token: "Сессия панели устарела. Перезапустите Config UI.",
    validation_failed: "Проверьте отмеченные поля.",
    config_write_failed: "Не удалось записать локальный .env.",
    Failed_to_fetch: "Локальный сервер настроек недоступен.",
  };
  return translations[code] || String(code).replaceAll("_", " ");
}

function showToast(title, copy, isError = false) {
  const toast = element("div", `toast${isError ? " is-error" : ""}`);
  toast.append(element("strong", "", title), element("span", "", copy));
  $("#toast-stack").append(toast);
  setTimeout(() => toast.remove(), 4800);
}

function activateNavigation(section) {
  showSection(section);
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== "") node.textContent = text;
  return node;
}

$("#navigation").addEventListener("click", (event) => {
  const button = event.target.closest(".nav-item[data-section]");
  if (button) activateNavigation(button.dataset.section);
});
$("#refresh-button").addEventListener("click", async () => {
  try {
    state.status = await api("/api/status");
    renderStatus();
    showToast("Обновлено", "Статус сервиса и очереди перечитан.");
  } catch (error) {
    showToast("Ошибка обновления", humanError(error), true);
  }
});
$("#discard-button").addEventListener("click", discardChanges);
$("#save-button").addEventListener("click", () => saveConfig(false));
$("#header-save-button").addEventListener("click", () => saveConfig(false));
$("#save-restart-button").addEventListener("click", () => saveConfig(true));
window.addEventListener("beforeunload", (event) => {
  if (!state.dirty.size) return;
  event.preventDefault();
  event.returnValue = "";
});

loadAll();
