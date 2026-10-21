"use strict";
// Local preferences contain no account data, passwords or session credentials.
window.TredData = (() => {
  const expirations = [5,10,15];
  const defaults = {daily_loss:"10.00",daily_target:"20.00",balance_floor:"90.00",stake:"1.00",max_open:1,max_overlays:0,goal_start:"100.00",goal_target:"1000.00"};
  const reason = "Подключи сервер и модуль браузера для анализа котировок.";
  const metadataUser = tg => ({id:Number.isSafeInteger(tg?.initDataUnsafe?.user?.id) && tg.initDataUnsafe.user.id > 0 ? tg.initDataUnsafe.user.id : "—",first_name:typeof tg?.initDataUnsafe?.user?.first_name === "string" ? tg.initDataUnsafe.user.first_name.slice(0,80) : "Гость"});
  const key = tg => `tred.backend.v1.${metadataUser(tg).id}`;
  const cloudKey="tred_backend_v1";
  const synced={state:"synced",message:"Подключение сохранено в Telegram и доступно на твоих устройствах."};
  const localOnly={state:"local",message:"Подключение работает на этом устройстве. Синхронизация Telegram пока недоступна."};
  let cloudWrites=Promise.resolve();
  function cache(tg,base){try{localStorage.setItem(key(tg),base);}catch(_){}}
  function cloudCall(tg,method,...args){
    if(!tg?.initData||typeof tg.CloudStorage?.[method]!=="function")return Promise.resolve({ok:false,supported:false});
    return new Promise(resolve=>{
      let done=false;
      const finish=value=>{if(done)return;done=true;clearTimeout(timer);resolve(value);};
      const timer=setTimeout(()=>finish({ok:false,supported:true}),4000);
      try{tg.CloudStorage[method](...args,(error,value)=>finish({ok:!error,supported:true,value}));}
      catch(_){finish({ok:false,supported:false});}
    });
  }
  async function sharedRoute(tg){
    const result=await cloudCall(tg,"getItem",cloudKey);
    if(!result.ok)return {state:result.supported?"error":"unsupported"};
    if(result.value===""||result.value==null)return {state:"empty"};
    try{
      if(typeof result.value!=="string"||result.value.length>4096)throw Error();
      const value=JSON.parse(result.value);
      if(!value||!Object.keys(value).every(k=>["v","owner","base"].includes(k))||value.v!==1||!Number.isSafeInteger(value.owner)||value.owner<=0||value.owner!==metadataUser(tg).id)throw Error();
      return {state:"saved",base:endpoint(value.base)};
    }catch(_){return {state:"invalid"};}
  }
  async function saveRoute(tg,base,state,kind,client){
    if(kind==="restore"){client.sync={...synced};return;}
    if(kind==="local"){client.sync={...localOnly};return;}
    if(kind==="migrate"){
      const current=await sharedRoute(tg);
      if(current.state==="saved"){
        client.sync=current.base===base?{...synced}:{state:"changed",message:"В Telegram найдено обновлённое подключение. Подтягиваем его автоматически…"};
        return;
      }
      if(current.state!=="empty"){client.sync={...localOnly};return;}
    }
    // Only the route and verified owner ID are shared. No balance, initData,
    // pairing code, bridge token, Pocket ID, cookies or account session.
    const value=JSON.stringify({v:1,owner:state.user.id,base});
    const result=await cloudCall(tg,"setItem",cloudKey,value);
    client.sync=result.ok&&result.value===true?{...synced}:{...localOnly};
  }
  function discover(tg,client){
    let nextCheck=Date.now()+15000,pending=false;
    client.sync={state:"waiting",message:client.mode==="device"?"Открой TRED на устройстве, где кабинет уже подключён. Связь подтянется через Telegram.":localOnly.message};
    client.checkCloud=async(force=false)=>{
      if(pending||!tg?.initData||!force&&Date.now()<nextCheck)return null;
      nextCheck=Date.now()+15000;pending=true;
      try{
        const route=await sharedRoute(tg);
        if(route.state==="saved"){
          if(route.base===client.base){client.sync={...synced};return null;}
          const restored=await connect(tg,route.base,{persist:false});
          const commit=restored.commit;
          restored.commit=()=>commit("restore");
          return restored;
        }
        if(route.state==="empty"&&client.mode==="api"&&client.base){
          const verified=await connect(tg,client.base,{persist:false});
          await verified.commit("migrate");client.sync=verified.client.sync;
        }
      }catch(error){
        if(client.mode==="device")client.sync={state:"error",message:error.message||"Сохранённое подключение пока недоступно. Повторим автоматически."};
      }
      finally{pending=false;}
      return null;
    };
    return client;
  }
  function validPreferences(value) {
    const p = value && typeof value === "object" ? value : {};
    const sym = typeof p.symbol === "string" ? p.symbol.trim().toUpperCase().replace(/\s+/g," ") : null;
    const manual = p.selection === "manual" && /^[A-Z0-9][A-Z0-9 /_.#-]{1,31}$/.test(sym || "");
    return {market:["normal","otc"].includes(p.market) ? p.market : "normal",expiry:expirations.includes(p.expiry) ? p.expiry : 5,selection:manual ? "manual" : "auto",symbol:manual ? sym : null,state:"idle",allow_otc:p.allow_otc === true,signal_mode:p.signal_mode==="active"?"active":"strict",min_payout:Number.isInteger(p.min_payout)&&p.min_payout>=85&&p.min_payout<=92?p.min_payout:85};
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
      statistics:{signals:0,wins:0,losses:0,draws:0,win_rate:null},recent_requests:[],catalog:{verified_count:0},minimum_expiry:5,
      goal:{start:riskConfig.goal_start,target:riskConfig.goal_target,progress:0,available:false,earned:"0.00",current:null},
      risk:{config:{...riskConfig},running:false,locked:false,reason:null,balance:"100.00",daily_pnl:"0.00",reserved:"0.00",execution:"simulation",real_orders_supported:false}});
    const client={mode:"device",async request(path,changes={}) {
      if (path === "/api/state") return state();
      if (path === "/api/settings") {
        const next = {...preferences,...changes};
        if (!changes || typeof changes !== "object" || !Object.keys(changes).every(k => ["market","expiry","symbol","selection","allow_otc","min_payout","signal_mode"].includes(k)) || !["normal","otc"].includes(next.market) || !expirations.includes(next.expiry) || !["auto","manual"].includes(next.selection) || typeof next.allow_otc !== "boolean" || !["strict","active"].includes(next.signal_mode) || !Number.isInteger(next.min_payout) || next.min_payout<85 || next.min_payout>92) throw new Error("Проверь выбранные настройки.");
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
    return discover(tg,client);
  }
  function endpoint(value) {
    let u;
    try { u = new URL(value); } catch (_) { throw new Error("Вставь HTTPS-адрес сервера."); }
    if (u.protocol !== "https:" || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(u.hostname) || u.port || u.username || u.password || u.search || u.hash || u.pathname !== "/") throw new Error("Нужен адрес твоего сервера https://…trycloudflare.com без пути и параметров.");
    return u.origin;
  }
  function apiClient(tg,base="") {
    const client={mode:"api",base,async request(path,body) {
      const abort = new AbortController(), timer = setTimeout(() => abort.abort(),10000);
      try {
        const response = await fetch(base+path,{method:body === undefined ? "GET" : "POST",headers:{"X-Telegram-Init-Data":tg?.initData || "","Content-Type":"application/json"},body:body === undefined ? undefined : JSON.stringify(body),signal:abort.signal,redirect:"error",credentials:"omit"});
        const data = await response.json();
        if (!response.ok) throw new Error(data.message || "Не удалось выполнить запрос.");
        return data;
      } catch (error) { if (error.name === "AbortError") throw new Error("Сервер не ответил. Проверь, включён ли компьютер."); throw error; }
      finally { clearTimeout(timer); }
    }};
    return discover(tg,client);
  }
  async function connect(tg,value,options={}) {
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
    if(!Number.isSafeInteger(state?.user?.id)||state.user.id<=0||state.user.id!==metadataUser(tg).id)throw new Error("Сервер не подтвердил твой Telegram-аккаунт.");
    let committed;
    const commit=(kind="explicit")=>{
      if(committed)return committed;
      cache(tg,base);
      committed=cloudWrites=cloudWrites.catch(()=>{}).then(()=>saveRoute(tg,base,state,kind,client));
      return committed;
    };
    if(options.persist!==false)await commit();
    return {client,state,commit};
  }
  async function create(tg) {
    let base;
    try { base = localStorage.getItem(key(tg)); } catch (_) {}
    try{base=base?endpoint(base):null;}catch(_){base=null;}
    const shared=await sharedRoute(tg);
    let failure;
    if(shared.state==="saved"){
      try{
        const restored=await connect(tg,shared.base,{persist:false});
        await restored.commit("restore");restored.client.initialState=restored.state;
        return restored.client;
      }catch(error){failure=error;}
    }
    if(base&&base!==shared.base){
      try{
        const restored=await connect(tg,base,{persist:false});
        await restored.commit(shared.state==="empty"?"migrate":"local");
        restored.client.initialState=restored.state;return restored.client;
      }catch(error){failure=error;}
    }
    if(failure)throw failure;
    const response = await fetch(new URL("./app-config.json",document.baseURI),{cache:"no-store"});
    if (!response.ok) throw new Error("Не удалось загрузить настройки приложения.");
    const config = await response.json();
    if (config.mode === "api") return apiClient(tg);
    if (config.mode !== "device") throw new Error("Неизвестный режим приложения.");
    const client=deviceClient(tg);
    if(!tg?.initData)client.sync={state:"unsupported",message:"Для общего кабинета открой TRED из Telegram."};
    else if(shared.state==="unsupported")client.sync={state:"unsupported",message:"Этот клиент Telegram не поддерживает синхронизацию подключения. Обнови Telegram."};
    else if(["error","invalid"].includes(shared.state))client.sync={state:"error",message:"Не удалось получить подключение из Telegram. Попробуем снова автоматически."};
    return client;
  }
  return {create,connect,deviceClient};
})();
