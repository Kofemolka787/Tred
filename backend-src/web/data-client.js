"use strict";
// Local preferences contain no account data, passwords or session credentials.
window.TredData = (() => {
  const expirations = [3,5,10,15,30,60];
  const defaults = {daily_loss:"10.00",daily_target:"20.00",balance_floor:"90.00",stake:"1.00",max_open:1,max_overlays:0,goal_start:"100.00",goal_target:"1000.00"};
  const reason = "Подключи сервер и модуль браузера для анализа котировок.";
  const metadataUser = tg => ({id:Number.isSafeInteger(tg?.initDataUnsafe?.user?.id) && tg.initDataUnsafe.user.id > 0 ? tg.initDataUnsafe.user.id : "—",first_name:typeof tg?.initDataUnsafe?.user?.first_name === "string" ? tg.initDataUnsafe.user.first_name.slice(0,80) : "Гость"});
  const key = tg => `tred.backend.v1.${metadataUser(tg).id}`;
  function validPreferences(value) {
    const p = value && typeof value === "object" ? value : {};
    const sym = typeof p.symbol === "string" ? p.symbol.trim().toUpperCase().replace(/\s+/g," ") : null;
    const manual = p.selection === "manual" && /^[A-Z0-9][A-Z0-9 /_.#-]{1,31}$/.test(sym || "");
    return {market:["normal","otc"].includes(p.market) ? p.market : "normal",expiry:expirations.includes(p.expiry) ? p.expiry : 5,selection:manual ? "manual" : "auto",symbol:manual ? sym : null,state:"idle",allow_otc:p.allow_otc === true};
  }
  function validRisk(value) {
    const result = {...defaults};
    if (!value || typeof value !== "object") return result;
    for (const name of ["daily_loss","daily_target","balance_floor","stake","goal_start","goal_target"]) {
      if (/^\d{1,8}(\.\d{1,2})?$/.test(String(value[name])) && Number(value[name]) > 0 && Number(value[name]) <= 10000000) result[name] = Number(value[name]).toFixed(2);
    }
    if (Number(result.goal_target) <= Number(result.goal_start)) { result.goal_start = defaults.goal_start; result.goal_target = defaults.goal_target; }
    if (Number(result.stake) > Number(result.daily_loss)) result.stake = defaults.stake;
    if (Number.isInteger(value.max_open) && value.max_open >= 1 && value.max_open <= 3) result.max_open = value.max_open;
    return result;
  }
  function deviceClient(tg) {
    const user = metadataUser(tg), storageKey = `tred.ui.v1.${user.id}`;
    let saved = {}, persistent = true;
    try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch (_) {}
    let preferences = validPreferences(saved?.preferences), riskConfig = validRisk(saved?.risk);
    const persist = () => { try { localStorage.setItem(storageKey,JSON.stringify({preferences,risk:riskConfig})); } catch (_) { persistent = false; } };
    persist();
    const state = () => ({mode:"device",persistent,user,preferences:{...preferences},accounts:[],trades:[],paper_trades:[],
      connection:{authenticated:false,live:false,model_ready:false,reason},
      statistics:{signals:0,wins:0,losses:0,draws:0,win_rate:null},recent_requests:[],catalog:{verified_count:0},minimum_expiry:3,
      goal:{start:riskConfig.goal_start,target:riskConfig.goal_target,progress:0,available:false,earned:"0.00",current:null},
      risk:{config:{...riskConfig},running:false,locked:false,reason:null,balance:"100.00",daily_pnl:"0.00",reserved:"0.00",execution:"simulation",real_orders_supported:false}});
    return {mode:"device",async request(path,changes={}) {
      if (path === "/api/state") return state();
      if (path === "/api/settings") {
        const next = {...preferences,...changes};
        if (!changes || typeof changes !== "object" || !Object.keys(changes).every(k => ["market","expiry","symbol","selection","allow_otc"].includes(k)) || !["normal","otc"].includes(next.market) || !expirations.includes(next.expiry) || !["auto","manual"].includes(next.selection) || typeof next.allow_otc !== "boolean") throw new Error("Проверь выбранные настройки.");
        if (next.selection === "manual" && (typeof next.symbol !== "string" || !/^[A-Z0-9][A-Z0-9 /_.#-]{1,31}$/.test(next.symbol.trim().toUpperCase()))) throw new Error("Проверь название пары.");
        preferences = validPreferences(next); persist(); return state();
      }
      if (path === "/api/risk") {
        if (!changes || typeof changes !== "object" || !Object.keys(changes).every(k => Object.hasOwn(defaults,k)) || changes.max_overlays !== undefined && changes.max_overlays !== 0) throw new Error("Проверь лимиты.");
        const next = {...riskConfig,...changes};
        if (Object.entries(next).some(([k,v]) => !["max_open","max_overlays"].includes(k) && (!/^\d{1,8}(\.\d{1,2})?$/.test(String(v)) || Number(v) <= 0 || Number(v)>10000000)) || Number(next.goal_target)<=Number(next.goal_start) || Number(next.stake)>Number(next.daily_loss) || !Number.isInteger(next.max_open) || next.max_open<1 || next.max_open>3) throw new Error("Проверь суммы: ставка не больше стопа, цель выше начала.");
        riskConfig = validRisk(next); persist(); return state();
      }
      if (path === "/api/signal") return {status:"skipped",reason,state:state()};
      if (path === "/api/paper" && changes.running === false) return state();
      throw new Error("Сначала подключи сервер и модуль Pocket Option в кабинете.");
    }};
  }
  function endpoint(value) {
    let u;
    try { u = new URL(value); } catch (_) { throw new Error("Вставь HTTPS-адрес сервера."); }
    if (u.protocol !== "https:" || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(u.hostname) || u.port || u.username || u.password || u.search || u.hash || u.pathname !== "/") throw new Error("Нужен адрес твоего сервера https://…trycloudflare.com без пути и параметров.");
    return u.origin;
  }
  function apiClient(tg,base="") {
    return {mode:"api",base,async request(path,body) {
      const abort = new AbortController(), timer = setTimeout(() => abort.abort(),10000);
      try {
        const response = await fetch(base+path,{method:body === undefined ? "GET" : "POST",headers:{"X-Telegram-Init-Data":tg?.initData || "","Content-Type":"application/json"},body:body === undefined ? undefined : JSON.stringify(body),signal:abort.signal,redirect:"error",credentials:"omit"});
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Не удалось выполнить запрос.");
        return data;
      } catch (error) { if (error.name === "AbortError") throw new Error("Сервер не ответил. Проверь, включён ли компьютер."); throw error; }
      finally { clearTimeout(timer); }
    }};
  }
  async function connect(tg,value) {
    if (!tg?.initData) throw new Error("Открой TRED из Telegram, чтобы связать личный кабинет.");
    const base = endpoint(value);
    let response;
    try {
      response = await fetch(base+"/healthz",{signal:AbortSignal.timeout(10000),redirect:"error",credentials:"omit"});
    } catch (_) {
      throw new Error("Устройство не смогло связаться с сервером. Оставь окно TRED на компьютере открытым и проверь актуальный адрес. На телефоне можно попробовать мобильный интернет вместо Wi-Fi.");
    }
    let health;
    try { health = await response.json(); }
    catch (_) { throw new Error("По этому адресу не найден сервер TRED."); }
    if (!response.ok || health?.ok !== true || health.service !== "tred-backend" || health.api_version !== 1) throw new Error("По этому адресу не найден сервер TRED.");
    const client = apiClient(tg,base);
    const state = await client.request("/api/state"); // Verified by server; metadata ID never authorizes access.
    try { localStorage.setItem(key(tg),base); } catch (_) {}
    return {client,state};
  }
  async function create(tg) {
    let base;
    try { base = localStorage.getItem(key(tg)); } catch (_) {}
    if (base) return apiClient(tg,endpoint(base));
    const response = await fetch(new URL("./app-config.json",document.baseURI),{cache:"no-store"});
    if (!response.ok) throw new Error("Не удалось загрузить настройки приложения.");
    const config = await response.json();
    if (config.mode === "api") return apiClient(tg);
    if (config.mode !== "device") throw new Error("Неизвестный режим приложения.");
    return deviceClient(tg);
  }
  return {create,connect,deviceClient};
})();
