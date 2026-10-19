"use strict";
const telegram = window.Telegram?.WebApp;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
let current = null;
let authorized = false;
let busy = false;
let dataClient;
let toastTimer;
let refreshFailed = false;
const usd = value => value === null || value === undefined ? "—" : "$" + Number(value).toLocaleString("en-US", {minimumFractionDigits:2,maximumFractionDigits:2});
const shortUsd = value => "$" + Number(value).toLocaleString("en-US", {maximumFractionDigits:2});
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
  text("#profile-id", String(user.id));
  text("#profile-name", user.first_name || "Гость");
  text("#storage-note", current.persistent === false ? "Настройки доступны до закрытия приложения." : "Настройки сохраняются на этом устройстве.");
  renderAccount();
  renderGoal();
  renderTrades();
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
function renderGoal() {
  const goal = current.goal || {start:100,target:1000,available:false,progress:0};
  const risk = current.risk || {config:{daily_loss:10},balance:100,daily_pnl:0,reserved:0};
  text("#goal-start", shortUsd(goal.start)); text("#goal-target", shortUsd(goal.target));
  const percent = Math.round(Math.min(1, Math.max(0,goal.progress || 0))*100);
  $("#goal-fill").style.width = percent+"%";
  $(".goal-meter").setAttribute("aria-valuenow",String(percent));
  text("#goal-percent",percent+"%");
  text("#goal-progress",goal.available ? "Закрытые сделки · реальный счёт" : "Нет данных реального счёта");
  text("#goal-earned",goal.available ? usd(goal.earned) : "—");
  text("#goal-remaining",goal.available ? usd(Math.max(0,Number(goal.target)-Number(goal.current))) : "—");
  text("#goal-stop",usd(risk.config.daily_loss));
  const milestones = [Number(goal.start), Number(goal.start)+(Number(goal.target)-Number(goal.start))/6, Number(goal.start)+(Number(goal.target)-Number(goal.start))*4/9, Number(goal.target)];
  $$(".goal-milestones>span").forEach((el,i) => { el.firstChild.textContent = shortUsd(milestones[i]); });
  text("#paper-status",current.stale ? "Нет связи" : risk.locked ? "Дневной стоп" : risk.running ? "Работает" : "Остановлен");
  text("#paper-balance",usd(risk.balance)); text("#paper-pnl",usd(risk.daily_pnl)); text("#paper-reserved",usd(risk.reserved));
  text("#risk-reason",risk.reason || "Перед каждым входом учитываются дневной результат и все открытые ставки.");
  $("#paper-start").disabled = !!risk.running || !!risk.locked || busy || !!current.stale;
}
function renderAccount() {
  const accounts = current.accounts || [];
  const fresh = a => !current.stale && a.live && Date.now()/1000-a.updated_at <= 15;
  text("#account-status",accounts.some(fresh) ? "Данные поступают" : accounts.length ? "Нет свежего обновления" : "Нет связи");
  $("#unlink-account").hidden = current.mode !== "api";
  text("#connect-backend",current.mode === "api" ? "Связь и код подключения" : "Подключить кабинет");
  const list = $("#account-list"); list.replaceChildren();
  (accounts.length ? accounts : [{balance:null,id:null,mode:null}]).forEach(a => {
    const card = document.createElement("div"); card.className = "account-card" + (fresh(a) ? " live" : "");
    const label = document.createElement("span"), balance = document.createElement("strong"), detail = document.createElement("small");
    label.textContent = a.mode === "real" ? "Реальный счёт · USD" : a.mode === "demo" ? "Демосчёт · USD" : "Баланс Pocket Option";
    balance.textContent = usd(a.balance);
    detail.textContent = a.id ? `ID ${a.id} · последнее обновление ${new Date(a.updated_at*1000).toLocaleTimeString()}` : "Счёт ещё не подключён";
    card.append(label,balance,detail); list.append(card);
  });
  text("#analysis-caption",current.connection.live ? `Анализ · доступных пар ${current.catalog?.verified_count || 0}` : "Анализ: ждём подключение данных");
}
function fillTrades(selector,trades,paper=false) {
  const list = $(selector); list.replaceChildren();
  trades.forEach(t => {
    const row = document.createElement("div"); row.className="trade-entry";
    const top=document.createElement("div"), title=document.createElement("strong"), result=document.createElement("strong"), detail=document.createElement("p");
    title.textContent=t.symbol+" "+(t.direction === "up" ? "↑" : "↓");
    result.textContent=t.status === "open" ? "Открыта" : t.status === "unresolved" ? "Нет цены закрытия" : usd(t.pnl);
    result.className=t.status === "win" ? "positive" : t.status === "loss" ? "negative" : "";
    detail.textContent=`${paper ? "СИМУЛЯЦИЯ" : t.mode === "real" ? "РЕАЛЬНЫЙ СЧЁТ" : "ДЕМО"} · ${usd(t.stake)} · ${new Date(t.opened_at*1000).toLocaleString()}`;
    top.append(title,result); row.append(top,detail); list.append(row);
  });
}
function renderTrades() {
  const trades=current.trades || [], paper=current.paper_trades || [];
  $("#trades-empty").hidden=trades.length>0;
  text("#trade-summary",trades.length ? "Полученные сделки Pocket Option. Симуляция показана отдельно." : "Подключи счёт, чтобы видеть реальные сделки.");
  fillTrades("#trade-list",trades); fillTrades("#paper-trade-list",paper,true);
  $("#paper-trade-section").hidden=!paper.length;
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
  if (current.mode === "device") { $("#connect-dialog").showModal(); return; }
  setBusy(true);
  try {
    const result = await api("/api/signal", {});
    current = result.state;
    render();
    text("#analysis-title",result.status === "candidate" ? `${result.symbol} · ${result.direction === "up" ? "ВВЕРХ" : "ВНИЗ"}` : "Подходящего входа пока нет");
    text("#analysis-result",result.reason);
    $("#analysis-evidence").replaceChildren();
    (result.evidence || []).forEach(line => { const li=document.createElement("li"); li.textContent=line; $("#analysis-evidence").append(li); });
    $("#analysis-dialog").showModal();
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
    dataClient = window.TredData.deviceClient(telegram);
    current = await dataClient.request("/api/state");
    authorized = true;
    render();
    $("#notice").hidden = false;
    text("#notice", error.message || "Не удалось открыть приложение. Попробуй ещё раз.");
  } finally { setBusy(false); }
}
bootstrap();
setInterval(async () => {
  if (!authorized || busy || document.hidden || current?.mode === "device") return;
  try { current = await api("/api/state"); refreshFailed=false; $("#notice").hidden=true; render(); }
  catch (error) {
    current.stale=true; current.connection.live=false; render();
    $("#notice").hidden=false; text("#notice","Нет связи с сервером. Показаны последние полученные данные. Управление симуляцией недоступно.");
    if (!refreshFailed) notify(error.message || "Не удалось обновить статус.");
    refreshFailed=true;
  }
}, 2000);

$("#edit-goal").addEventListener("click", () => {
  const c=current?.risk?.config || {daily_loss:10,daily_target:20,balance_floor:90,stake:1,max_open:1,goal_start:100,goal_target:1000};
  for (const [id,key] of [["risk-loss","daily_loss"],["risk-target","daily_target"],["risk-floor","balance_floor"],["risk-stake","stake"],["risk-open","max_open"],["goal-start-input","goal_start"],["goal-target-input","goal_target"]]) $("#"+id).value=c[key];
  $("#loss-output").value=c.daily_loss; $("#allow-otc").checked=current?.preferences.allow_otc === true;
  $("#risk-dialog").showModal();
});
$("#risk-loss").addEventListener("input", () => { $("#loss-output").value=$("#risk-loss").value; });
$("#risk-form").addEventListener("submit",async e => {
  e.preventDefault();
  try {
    const config={daily_loss:$("#risk-loss").value,daily_target:$("#risk-target").value,balance_floor:$("#risk-floor").value,stake:$("#risk-stake").value,max_open:Number($("#risk-open").value),max_overlays:0,goal_start:$("#goal-start-input").value,goal_target:$("#goal-target-input").value};
    current=await api("/api/risk",config);
    // Old local administrative API lacks allow_otc. New backend and device UI support it.
    if (current.risk) current=await api("/api/settings",{allow_otc:$("#allow-otc").checked});
    render(); $("#risk-dialog").close(); notify("Цель и границы сохранены.");
  } catch(error) { notify(error.message); }
});
async function paper(running) {
  try { current=await api("/api/paper",{running}); render(); notify(running ? "Симуляция запущена. Реальные ордера не отправляются." : "Новые входы симуляции остановлены."); }
  catch(error) { notify(error.message); }
}
$("#paper-start").addEventListener("click", () => paper(true));
$("#paper-stop").addEventListener("click", () => paper(false));
$("#show-method").addEventListener("click", () => {
  text("#analysis-title","Как анализирует TRED");
  text("#analysis-result","Структура swing-уровней, пробой, снятие локального экстремума и возврат к ценовому разрыву. Условия должны согласоваться на двух таймфреймах. Это правила анализа; обученной нейросети и измеренной проходимости пока нет.");
  $("#analysis-evidence").replaceChildren(); $("#analysis-dialog").showModal();
});
$("#connect-backend").addEventListener("click",async () => {
  $("#connect-dialog").showModal();
  if (dataClient?.base) $("#backend-url").value=dataClient.base;
  if (current?.mode === "api" && current.risk) await pair();
});
let pairingTimer,pairGeneration=0,connectGeneration=0,activePair=null;
let pairChain=Promise.resolve();
function clearPair() {
  pairGeneration++;activePair=null;clearTimeout(pairingTimer);
  text("#pair-code","");text("#pair-server","");
  $("#pairing-box").hidden=true;$("#copy-code").disabled=true;
}
async function pair(renew=false) {
  const client=dataClient;
  if(activePair?.client===client&&activePair.expires>Date.now()&&!renew){$("#pairing-box").hidden=false;return;}
  clearPair();const sequence=pairGeneration;
  try {
    // Serialize code creation: an older request must not invalidate a newer one.
    const request=pairChain.then(()=>{
      if(sequence!==pairGeneration||client!==dataClient)return null;
      const started=Date.now();
      return client.request("/api/pair",{}).then(result=>({result,started}));
    });
    pairChain=request.catch(()=>{});
    const reply=await request;
    if(!reply||sequence!==pairGeneration||client!==dataClient)return;
    const {result,started}=reply;
    if(!/^[a-f0-9]{24}$/.test(result?.code)||!Number.isFinite(result.expires_in)||result.expires_in<=0||result.expires_in>300)throw new Error("Сервер не выдал действительный код связи.");
    const expires=started+result.expires_in*1000;
    if(expires<=Date.now())throw new Error("Код истёк во время запроса. Получи новый код.");
    activePair={client,code:result.code,expires};
    text("#pair-code",result.code);text("#pair-server","Код для сервера: "+(client.base||location.origin));
    $("#pairing-box").hidden=false;$("#copy-code").disabled=false;
    pairingTimer=setTimeout(()=>{
      if(sequence!==pairGeneration)return;
      activePair=null;text("#pair-code","Код истёк");$("#copy-code").disabled=true;
      text("#connect-status","Нажми «Получить новый код».");
    },expires-Date.now());
  } catch(error) {
    if(sequence===pairGeneration&&client===dataClient)text("#connect-status",error.message);
  }
}
$("#backend-url").addEventListener("input",()=>{connectGeneration++;clearPair();});
$("#backend-form").addEventListener("submit",async e => {
  e.preventDefault();const sequence=++connectGeneration;clearPair();text("#connect-status","Проверяем сервер…");
  try {
    const result=await window.TredData.connect(telegram,$("#backend-url").value.trim(),{persist:false});
    if(sequence!==connectGeneration)return;
    result.commit?.();
    dataClient=result.client; current=result.state; authorized=true; render(); setBusy(false);
    text("#connect-status","Сервер подключён. Вставь адрес и код в модуль браузера."); await pair();
  } catch(error) { if(sequence===connectGeneration)text("#connect-status",error.message || "Нет связи с сервером."); }
});
$("#renew-code").addEventListener("click",()=>pair(true));
$("#copy-code").addEventListener("click",async () => {
  if(!activePair||activePair.client!==dataClient||activePair.expires<=Date.now()){notify("Получи действительный код для текущего сервера.");return;}
  try { await navigator.clipboard.writeText(activePair.code); notify("Код скопирован."); } catch(_) { notify("Выдели и скопируй код вручную."); }
});
$("#unlink-account").addEventListener("click",async () => { try { current=await api("/api/unlink",{}); clearPair();render();notify("Ключ модуля отозван. Симуляция остановлена."); } catch(error) { notify(error.message); } });
