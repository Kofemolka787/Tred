"use strict";

const telegram = window.Telegram?.WebApp;
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let current = null;
let authorized = false;
let busy = false;
let toastTimer;
let dataClient;

function notify(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, 5500);
}

function setBusy(value) {
  busy = value;
  document.body.classList.toggle("is-busy", value);
  $$(".settings-control, .expiry-switch button").forEach((button) => { button.disabled = value || !authorized; });
}

async function api(path, body) {
  return dataClient.request(path, body);
}

function text(selector, value) { $(selector).textContent = value; }

function render() {
  if (!current) return;
  const { preferences: prefs, connection, statistics: stats, user } = current;
  if (current.mode === "device") {
    text("#storage-note", current.persistent ? "Настройки сохраняются на этом устройстве. Синхронизацию между устройствами подключим отдельно." : "Хранилище устройства недоступно. Настройки сохранятся только до закрытия приложения.");
    text("#notice", "Интерфейс готов. Анализ рынка подключим следующим этапом.");
    $("#notice").hidden = false;
  }
  const market = prefs.market === "otc" ? "OTC" : "Обычный рынок";
  const mode = prefs.expiry >= 15 ? "long" : "standard";
  $$("[data-market]").forEach((button) => {
    const active = button.dataset.market === prefs.market;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $$("[data-mode]").forEach((button) => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  text("#mode-caption", `${market} · ${mode === "long" ? "15, 30 и 60 минут" : "экспирация от 3 минут"}`);
  const automatic = prefs.selection === "auto";
  $("#auto-card").classList.toggle("selected", automatic);
  $("#auto-card").setAttribute("aria-pressed", String(automatic));
  text("#pair-label", automatic ? "или выбрать пару самому" : `${prefs.symbol} · сменить пару`);
  text("#signal-subtitle", `${automatic ? "Автоподбор" : prefs.symbol} · ${prefs.expiry} мин`);
  text("#connection-reason", connection.reason);
  text("#feed-badge span", connection.live ? "Котировки есть" : "Нет котировок");
  $("#feed-badge").classList.toggle("live", connection.live);
  $("#data-chip").replaceChildren();
  $("#data-chip").append(document.createElement("i"), document.createTextNode(connection.live ? "Ожидаем проверку модели" : "Ожидаем котировки"));
  const expirations = mode === "long" ? [15, 30, 60] : [3, 5, 10];
  const expirySwitch = $("#expiry-switch");
  expirySwitch.replaceChildren();
  expirations.forEach((minutes) => {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `${minutes} мин`;
    button.classList.toggle("active", prefs.expiry === minutes);
    button.setAttribute("aria-pressed", String(prefs.expiry === minutes));
    button.disabled = busy || !authorized;
    button.addEventListener("click", () => update({ expiry: minutes }));
    expirySwitch.append(button);
  });
  const rate = stats.win_rate === null ? "—" : `${Math.round(stats.win_rate * 100)}%`;
  text("#trade-count", stats.signals);
  text("#wins", stats.wins);
  text("#losses", stats.losses);
  text("#trade-rate", rate);
  text("#stats-rate", rate);
  text("#stats-signals", stats.signals);
  text("#stats-sample", stats.wins + stats.losses ? `Завершённых сигналов: ${stats.wins + stats.losses}` : "Пока нет завершённых сигналов");
  text("#stats-data", connection.live ? "Свежие котировки" : "Нет потока");
  text("#profile-id", String(user.id));
  text("#profile-name", user.first_name || "Владелец");
  text("#profile-feed", connection.authenticated ? "сессия подтверждена" : "не подключены");
  const list = $("#request-list");
  list.replaceChildren();
  $("#requests-section").hidden = !current.recent_requests.length;
  current.recent_requests.forEach((request) => {
    const row = document.createElement("div");
    row.className = "request-row";
    const heading = document.createElement("strong");
    heading.textContent = `${request.symbol || "Автоподбор"} · ${request.market === "otc" ? "OTC" : "Обычный"} · ${request.expiry} мин`;
    const description = document.createElement("p");
    description.textContent = `Вход пропущен. ${request.reason}`;
    const stamp = document.createElement("small");
    stamp.textContent = new Intl.DateTimeFormat("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Kiev" }).format(new Date(request.created_at * 1000));
    row.append(heading, description, stamp);
    list.append(row);
  });
}

async function update(changes) {
  if (!authorized || busy) return;
  setBusy(true);
  try {
    current = await api("/api/settings", changes);
    render();
    telegram?.HapticFeedback?.selectionChanged();
  } catch (error) { notify(error.message || "Не удалось сохранить настройки."); }
  finally { setBusy(false); }
}

$$("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => {
    const tab = button.dataset.tab;
    $$("[data-panel]").forEach((panel) => { panel.hidden = panel.dataset.panel !== tab; });
    $$("[data-tab]").forEach((item) => {
      item.classList.toggle("active", item === button);
      if (item === button) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    window.scrollTo({ top: 0, behavior: "instant" });
    telegram?.HapticFeedback?.selectionChanged();
  });
});

$$("[data-market]").forEach((button) => button.addEventListener("click", () => update({ market: button.dataset.market })));
$$("[data-mode]").forEach((button) => button.addEventListener("click", () => update({ expiry: button.dataset.mode === "long" ? 15 : 5 })));
$("#auto-card").addEventListener("click", () => update({ selection: "auto" }));
$("#choose-pair").addEventListener("click", () => {
  if (!authorized) return;
  $("#pair-input").value = current?.preferences.symbol || "";
  $("#pair-dialog").showModal();
  $("#pair-input").focus();
});
$$("[data-symbol]").forEach((button) => button.addEventListener("click", () => { $("#pair-input").value = button.dataset.symbol; }));
$("#pair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = $("#pair-input").value.trim();
  if (!value) return;
  $("#pair-dialog").close();
  await update({ symbol: value, selection: "manual" });
});
$$("[data-close]").forEach((button) => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));
$("#open-guide").addEventListener("click", () => $("#guide-dialog").showModal());
$("#open-pocket").addEventListener("click", () => {
  const url = "https://pocketoption.com/en/cabinet/demo-quick-high-low/";
  if (telegram?.openLink) telegram.openLink(url);
  else window.open(url, "_blank", "noopener,noreferrer");
});
$("#request-signal").addEventListener("click", async () => {
  if (!authorized || busy) return;
  setBusy(true);
  try {
    const result = await api("/api/signal", {});
    current = result.state;
    render();
    notify(`Сигнал не выдан. ${result.reason}`);
  } catch (error) { notify(error.message || "Не удалось выполнить запрос."); }
  finally { setBusy(false); }
});

async function bootstrap() {
  telegram?.ready();
  telegram?.expand();
  telegram?.setHeaderColor?.("#08080f");
  telegram?.setBackgroundColor?.("#08080f");
  setBusy(false);
  if (!telegram?.initData) {
    $("#notice").hidden = false;
    text("#notice", "Для личных настроек открой приложение через @Tr1edBot_bot. Доступ проверяется в Telegram.");
    return;
  }
  try {
    dataClient = await window.TredData.create(telegram);
    current = await api("/api/state");
    authorized = true;
    render();
    setBusy(false);
  } catch (error) {
    $("#notice").hidden = false;
    text("#notice", error.message || "Не удалось подключиться. Перезапусти приложение.");
  }
}

bootstrap();
setInterval(async () => {
  if (!authorized || busy || document.hidden) return;
  try { current = await api("/api/state"); render(); }
  catch (error) { notify(error.message || "Не удалось обновить статус."); }
}, 15000);
