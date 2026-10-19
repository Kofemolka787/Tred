"use strict";
const telegram = window.Telegram?.WebApp;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let current = null;
let authorized = false;
let busy = false;
let dataClient;
let toastTimer;
const text = (selector, value) => { const element = $(selector); if (element) element.textContent = value; };

function notify(message) {
  text("#toast", message);
  $("#toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("#toast").hidden = true; }, 4500);
}
function setBusy(value) {
  busy = value;
  document.body.classList.toggle("is-busy", value);
  $$(".settings-control, .expiry-switch button").forEach(button => { button.disabled = value || !authorized; });
}
function haptic() { try { telegram?.HapticFeedback?.selectionChanged(); } catch (_) {} }
function soon(message) {
  text("#soon-copy", message || "Сигналы появятся в следующих обновлениях. Сейчас можно выбрать формат и познакомиться с приложением.");
  if (!$("#soon-dialog").open) $("#soon-dialog").showModal();
  haptic();
}
async function api(path, body) { return dataClient.request(path, body); }

function render() {
  if (!current) return;
  const { preferences: prefs, statistics: stats, user, connection } = current;
  document.body.classList.toggle("device-mode", current.mode === "device");
  const market = prefs.market === "otc" ? "OTC" : "Обычный рынок";
  const mode = prefs.expiry >= 15 ? "long" : "standard";
  $$("[data-market]").forEach(button => {
    const active = button.dataset.market === prefs.market;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  $$("[data-mode]").forEach(button => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  text("#feed-badge span", prefs.market === "otc" ? "OTC-пары" : "Валютные пары");
  $("#feed-badge").classList.toggle("normal", prefs.market === "normal");
  text("#mode-caption", `${market} · ${mode === "long" ? "15, 30 и 60 минут" : "экспирация от 3 минут"}`);
  const automatic = prefs.selection === "auto";
  $("#auto-card").classList.toggle("selected", automatic);
  $("#auto-card").setAttribute("aria-pressed", String(automatic));
  text("#pair-label", automatic ? "или выбрать пару самому" : `${prefs.symbol} · сменить пару`);
  text("#signal-subtitle", `${automatic ? "Лучшая " + (prefs.market === "otc" ? "OTC-пара" : "валютная пара") : prefs.symbol} · ${prefs.expiry} мин`);
  $("#data-chip").replaceChildren(document.createElement("i"), document.createTextNode(automatic ? "Автоподбор" : "Ручной выбор"));
  text("#connection-reason", connection.reason);
  const expirySwitch = $("#expiry-switch");
  expirySwitch.replaceChildren();
  (mode === "long" ? [15, 30, 60] : [3, 5, 10]).forEach(minutes => {
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
  text("#stats-sample", stats.wins + stats.losses ? `Завершённых сигналов: ${stats.wins + stats.losses}` : "Твоя история начинается здесь");
  text("#stats-data", connection.live ? "Свежие котировки" : "Не подключено");
  text("#profile-id", String(user.id));
  text("#profile-name", user.first_name || "Гость");
  text("#storage-note", current.persistent === false ? "Настройки доступны до закрытия приложения." : "Настройки сохраняются на этом устройстве.");
  const requests = current.mode === "device" ? [] : current.recent_requests;
  $("#requests-section").hidden = !requests.length;
  const list = $("#request-list");
  list.replaceChildren();
  requests.forEach(request => {
    const row = document.createElement("div");
    row.className = "request-row";
    const title = document.createElement("strong");
    title.textContent = `${request.symbol || "Автоподбор"} · ${request.expiry} мин`;
    const description = document.createElement("p");
    description.textContent = request.reason;
    row.append(title, description);
    list.append(row);
  });
}
async function update(changes) {
  if (!authorized || busy) return;
  setBusy(true);
  try { current = await api("/api/settings", changes); render(); haptic(); }
  catch (error) { notify(error.message || "Не удалось сохранить выбор."); }
  finally { setBusy(false); }
}
$$("[data-tab]").forEach(button => button.addEventListener("click", () => {
  const tab = button.dataset.tab;
  $$("[data-panel]").forEach(panel => { panel.hidden = panel.dataset.panel !== tab; });
  $$("[data-tab]").forEach(item => {
    item.classList.toggle("active", item === button);
    if (item === button) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  $("#workspace").scrollTop = 0;
  haptic();
}));
$$("[data-market]").forEach(button => button.addEventListener("click", () => update({ market: button.dataset.market })));
$$("[data-mode]").forEach(button => button.addEventListener("click", () => update({ expiry: button.dataset.mode === "long" ? 15 : 5 })));
$$("[data-soon]").forEach(button => button.addEventListener("click", () => soon(`${button.dataset.soon} появится в следующих обновлениях TRED.`)));
$("#auto-card").addEventListener("click", () => update({ selection: "auto" }));
$("#choose-pair").addEventListener("click", () => {
  if (!authorized) return;
  $("#pair-input").value = current?.preferences.symbol || "";
  $("#pair-dialog").showModal();
  $("#pair-input").focus();
});
$$("[data-symbol]").forEach(button => button.addEventListener("click", () => { $("#pair-input").value = button.dataset.symbol; }));
$("#pair-form").addEventListener("submit", async event => {
  event.preventDefault();
  const value = $("#pair-input").value.trim();
  if (!value) return;
  $("#pair-dialog").close();
  await update({ symbol: value, selection: "manual" });
});
$$("[data-close]").forEach(button => button.addEventListener("click", () => document.getElementById(button.dataset.close).close()));
$("#open-guide").addEventListener("click", () => $("#guide-dialog").showModal());
$("#open-pocket").addEventListener("click", () => {
  const url = "https://pocketoption.com/en/cabinet/demo-quick-high-low/";
  if (telegram?.openLink) telegram.openLink(url); else window.open(url, "_blank", "noopener,noreferrer");
});
$("#request-signal").addEventListener("click", async () => {
  if (!authorized || busy) return;
  if (current.mode === "device") return soon();
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
  try {
    telegram?.ready();
    telegram?.expand();
    if (telegram?.isVersionAtLeast?.("6.1")) { telegram.setHeaderColor("#08080e"); telegram.setBackgroundColor("#08080e"); }
  } catch (_) { /* UI also works in an ordinary browser. */ }
  setBusy(true);
  try {
    dataClient = await window.TredData.create(telegram);
    if (dataClient.mode === "api" && !telegram?.initData) throw new Error("Открой приложение из Telegram для личных настроек.");
    current = await api("/api/state");
    authorized = true;
    render();
  } catch (error) {
    $("#notice").hidden = false;
    text("#notice", error.message || "Не удалось открыть приложение. Попробуй ещё раз.");
  } finally { setBusy(false); }
}
bootstrap();
setInterval(async () => {
  if (!authorized || busy || document.hidden || current?.mode === "device") return;
  try { current = await api("/api/state"); render(); } catch (error) { notify(error.message || "Не удалось обновить статус."); }
}, 15000);
