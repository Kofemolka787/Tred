"use strict";

// The public Pages build stores UI preferences only. It has no bot token or account session.
// Telegram user metadata labels local preferences; it never authorizes a server request.
window.TredData = (() => {
  const expirations = [3, 5, 10, 15, 30, 60];
  const reason = "Анализ рынка ещё не подключён. Сейчас доступен интерфейс приложения.";

  function validPreferences(value) {
    const candidate = value && typeof value === "object" ? value : {};
    const market = ["normal", "otc"].includes(candidate.market) ? candidate.market : "otc";
    const expiry = expirations.includes(candidate.expiry) ? candidate.expiry : 5;
    const symbol = typeof candidate.symbol === "string" ? candidate.symbol.trim().toUpperCase().replace(/\s+/g, " ") : null;
    const manual = candidate.selection === "manual" && /^[A-Z0-9][A-Z0-9 /_.#-]{1,31}$/.test(symbol || "");
    return { market, expiry, selection: manual ? "manual" : "auto", symbol: manual ? symbol : null, state: "idle" };
  }

  function deviceClient(telegram) {
    const metadata = telegram?.initDataUnsafe?.user;
    const user = { id: Number.isSafeInteger(metadata?.id) && metadata.id > 0 ? metadata.id : "—",
                   first_name: typeof metadata?.first_name === "string" ? metadata.first_name.slice(0, 80) : "Пользователь Telegram" };
    const storageKey = `tred.ui.v1.${user.id}`;
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(storageKey) || "{}"); } catch (_) { /* Corrupt or unavailable local storage. */ }
    let preferences = validPreferences(saved?.preferences);
    let requests = Array.isArray(saved?.requests) ? saved.requests.filter(row => row?.status === "skipped" && typeof row.reason === "string" && Number.isFinite(row.created_at)).slice(0, 10) : [];
    let persistent = true;
    let lastRequest = 0;
    const persist = () => {
      try { localStorage.setItem(storageKey, JSON.stringify({ preferences, requests })); }
      catch (_) { persistent = false; }
    };
    persist();
    const state = () => ({ user, preferences: { ...preferences },
      connection: { authenticated: false, live: false, model_ready: false, reason },
      statistics: { signals: 0, wins: 0, losses: 0, draws: 0, win_rate: null },
      recent_requests: [...requests], catalog: { verified_count: 0 }, minimum_expiry: 3,
      mode: "device", persistent, bot_username: "Tr1edBot_bot" });
    return {
      async request(path, changes = {}) {
        if (path === "/api/state") return state();
        if (path === "/api/settings") {
          const next = { ...preferences, ...changes };
          if (!changes || typeof changes !== "object" || !Object.keys(changes).every(key => ["market", "expiry", "symbol", "selection"].includes(key))
              || !["normal", "otc"].includes(next.market) || !expirations.includes(next.expiry)
              || !["auto", "manual"].includes(next.selection)) throw new Error("Проверь выбранные настройки.");
          if (next.selection === "manual" && (typeof next.symbol !== "string" || !/^[A-Z0-9][A-Z0-9 /_.#-]{1,31}$/.test(next.symbol.trim().toUpperCase()))) throw new Error("Проверь название пары.");
          preferences = validPreferences(next);
          persist();
          return state();
        }
        if (path === "/api/signal") {
          if (Date.now() - lastRequest < 2000) throw new Error("Подожди пару секунд перед новым запросом.");
          lastRequest = Date.now();
          requests.unshift({ ...preferences, created_at: Date.now() / 1000, status: "skipped", reason });
          requests = requests.slice(0, 10);
          persist();
          return { status: "skipped", reason, state: state() };
        }
        if (path === "/api/stop") return state();
        throw new Error("Действие пока недоступно.");
      }
    };
  }

  async function create(telegram) {
    const response = await fetch(new URL("./app-config.json", document.baseURI), { cache: "no-store" });
    if (!response.ok) throw new Error("Не удалось загрузить настройки приложения.");
    const config = await response.json();
    if (config.mode === "device") return deviceClient(telegram);
    if (config.mode !== "api") throw new Error("Неизвестный режим приложения.");
    return { async request(path, body) {
      const abort = new AbortController();
      const timer = setTimeout(() => abort.abort(), 10000);
      try {
        const response = await fetch(path, {
          method: body === undefined ? "GET" : "POST",
          headers: { "X-Telegram-Init-Data": telegram?.initData || "", "Content-Type": "application/json" },
          body: body === undefined ? undefined : JSON.stringify(body), signal: abort.signal,
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Не удалось выполнить запрос.");
        return data;
      } catch (error) {
        if (error.name === "AbortError") throw new Error("Сервер не ответил. Попробуй ещё раз.");
        throw error;
      } finally { clearTimeout(timer); }
    } };
  }
  return { create };
})();
