"use strict";
importScripts("time-check.js","account-check.js","scan-worker.js");
const WORKER_VERSION="0.4.2";
const endpoint=value=>{
  const u=new URL(value);
  if(u.protocol!=="https:"||!/^[a-z0-9-]+\.trycloudflare\.com$/.test(u.hostname)||u.port||u.username||u.password||u.search||u.hash||u.pathname!=="/")throw new Error("Укажи HTTPS-адрес своего TRED-туннеля.");
  return u.origin;
};
async function post(base,path,body,token){
  const response=await fetch(base+path,{method:"POST",headers:{"Content-Type":"application/json",...(token?{Authorization:"Bearer "+token}:{})},body:JSON.stringify(body),signal:AbortSignal.timeout(10000),redirect:"error",credentials:"omit"});
  let result;
  try{result=await response.json();}
  catch(_){throw new Error("Вместо API получена страница (HTTP "+response.status+"). Проверь адрес из текущего окна сервера TRED.");}
  if(!result||typeof result!=="object"||Array.isArray(result))throw new Error("Сервер вернул неподдерживаемый ответ. Проверь текущий адрес TRED.");
  if(!response.ok){const error=new Error(result.message||"Нет связи с сервером.");error.status=response.status;throw error;}
  if(path==="/api/bridge/events"&&result.ok!==true)throw new Error("Сервер не подтвердил приём данных.");
  return result;
}
chrome.storage.local.setAccessLevel({accessLevel:"TRUSTED_CONTEXTS"});
const eventFields={account:["type","mode","account_id","currency","balance","time","source_stream","id_status","id_shape","source_account","account_source"],asset:["type","symbol","market","is_open","payout","expiries","time","feed_id","history_scoped","source_role"],quote:["type","symbol","price","time","source_clock","feed_id"],candle:["type","symbol","period","time","open","high","low","close","source_clock","origin","feed_id"],feed:["type","feed_id","connected","time"],history_status:["type","symbol","event","period","rows","bars","code","time"],trade:["type","mode","account_id","id","symbol","direction","stake","opened_at","expires_at","closed_at","pnl","status","time"]};
let queue=[],sending=false,timer,generation=0;
// Large candle histories must not delay or evict live balance/price changes.
const priority=event=>({account:0,feed:0,trade:1,asset:2,quote:3,candle:4}[event?.type]??5);
function boundQueue(){
  if(queue.length<=5000)return;
  const critical=queue.filter(e=>priority(e)<3).sort((a,b)=>priority(a)-priority(b)).slice(0,5000);
  const quoteRoom=5000-critical.length;
  const quotes=quoteRoom?queue.filter(e=>e?.type==="quote").slice(-quoteRoom):[];
  const urgent=critical.concat(quotes);
  const room=5000-urgent.length;
  queue=urgent.concat(room?queue.filter(e=>priority(e)>=4).slice(-room):[]);
}
function latestQuotes(events,profile){
  const other=[],latest=new Map(),now=Date.now()/1000;
  for(const event of events){
    if(event?.type!=="quote"||!Object.keys(event).every(k=>eventFields.quote.includes(k))||typeof event.price!=="number"||!Number.isFinite(event.price)||event.price<=0){other.push(event);continue;}
    const decoded=TredTime.decode(event,profile);
    if(decoded.error||TredTime.problem(decoded.event,now)){other.push(event);continue;}
    const key=(event.feed_id||"")+":"+event.symbol;
    const previous=latest.get(key);
    if(!previous||previous.time<event.time)latest.set(key,event);
  }
  return other.concat([...latest.values()]); // Original source times are kept.
}
// Pending balance is usable for <=60 seconds, in RAM, at its ORIGINAL time.
// It may be delivered once after an explicit identity confirmation, never aged
// forward or stored on disk. Confirmed profiles survive reconnects; modes stay separate.
const pendingAccounts=new Map();
const streamTabs=new Map();
const tabAccounts=new Map();
const retryAccounts=new Map();
const deliveryRetries=new WeakMap();
const fromPocket=sender=>sender.tab&&/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\//.test(sender.url||"");
function prunePending(){
  const now=Date.now()/1000;
  for(const [key,event] of pendingAccounts)if(now-event.time>60||event.time>now+5)pendingAccounts.delete(key);
  while(pendingAccounts.size>20)pendingAccounts.delete(pendingAccounts.keys().next().value);
}
// Coalesce small live bursts; drain a history backlog without adding a whole
// second between every 250 bars. Never run two deliveries simultaneously.
function schedule(delay=1000){if(!timer&&!sending)timer=setTimeout(()=>{timer=null;flush();},delay);}
async function flush(){
  if(sending||!queue.length)return;
  if(timer){clearTimeout(timer);timer=null;}
  sending=true;
  const epoch=generation;let backlogDelay=50;
  try{
    const stored=await chrome.storage.local.get(["base","token","needsPairing","timeWarnings","receivedKinds","marketClock","clockSamples","accountCandidate","confirmedUsdAccounts","identityCandidate","manualIdentities","accountProfiles","lastBalance"]);
    const {base,token}=stored;
    if(!base||!token||stored.needsPairing){queue=[];return;}
    queue=latestQuotes(queue,stored.marketClock);
    queue.sort((a,b)=>priority(a)-priority(b));
    const batch=queue.splice(0,250),groups=new Map();
    for(const event of batch){
      const kind=TredTime.known(event?.type);
      if(!groups.has(kind))groups.set(kind,[]);
      groups.get(kind).push(event);
    }
    const warnings=Object.fromEntries(Object.entries(stored.timeWarnings||{}).filter(([kind,value])=>TredTime.kinds.includes(kind)&&typeof value==="string"));
    const received=new Set((stored.receivedKinds||[]).filter(x=>TredTime.kinds.includes(x)));
    const clockSamples={...stored.clockSamples};
    let accountCandidate=stored.accountCandidate||null;
    let identityCandidate=stored.identityCandidate||null;
    let accountProfiles=TredAccount.storedProfiles(stored);
    prunePending();
    let lastError="",lastSeen=null,needsPairing=false,lastBalance=stored.lastBalance||null;
    for(const kind of [...TredTime.kinds,"unknown"]){
      const rows=groups.get(kind);
      if(!rows||epoch!==generation)continue;
      const accepted=[],originals=[],rejected=[],consumed=[],accountSources=new Map();let sourceSample;
      for(const event of rows){
        const safe=eventFields[kind]&&Object.keys(event||{}).every(key=>eventFields[kind].includes(key));
        if(!safe){rejected.push({message:TredTime.labels[kind]+": неподдерживаемые поля. Событие не передано."});continue;}
        const decoded=TredTime.decode(event,stored.marketClock),now=Date.now()/1000;
        const error=decoded.error?{message:decoded.error}:TredTime.problem(decoded.event,now);
        if(error){rejected.push(error);continue;}
        if(kind==="account"){
          const resolved=TredAccount.resolve(decoded.event,stored.manualIdentities,accountProfiles);
          accountCandidate=null;identityCandidate=resolved.identityCandidate?{...resolved.identityCandidate,tab_id:streamTabs.get(event.source_stream)??null}:null;
          if(identityCandidate){pendingAccounts.set(TredAccount.streamKey(event),{...event});prunePending();}
          if(resolved.error){rejected.push({message:resolved.error});continue;}
          const checked=TredAccount.inspect(resolved.event,stored.confirmedUsdAccounts);
          accountCandidate=checked.candidate;
          if(resolved.manual&&accountCandidate)Object.assign(accountCandidate,{identity_source:"manual",source_stream:resolved.source_stream,automatic:!!resolved.profile||resolved.origin==="terminal-dom",origin:resolved.origin||"websocket"});
          if(checked.error){rejected.push({message:checked.error});continue;}
          if(resolved.profile){accountProfiles=accountProfiles.filter(x=>x.mode!==resolved.profile.mode);accountProfiles.push(resolved.profile);}
          decoded.event=checked.event;
          if(resolved.manual)consumed.push({key:TredAccount.streamKey(event),time:event.time});
        }
        // The terminal includes the current, unfinished candle in its history.
        // Never send it to research, including after UTC+2 source decoding.
        if(kind==="candle"){
          const c=decoded.event;
          if(![60,120,300,600,900].includes(c.period)||![c.open,c.high,c.low,c.close].every(v=>typeof v==="number"&&Number.isFinite(v)&&v>0&&v<=1e9)||c.low>Math.min(c.open,c.close)||c.high<Math.max(c.open,c.close)||c.time%c.period>1||c.origin!==undefined&&!["broker","ticks"].includes(c.origin)){
            rejected.push({message:"Свеча: неверный OHLC, период или начало интервала. Остальные корректные свечи передаются."});continue;
          }
          if(c.time+c.period>now)continue;
        }
        accepted.push(decoded.event);
        originals.push(event);
        if(kind==="account")accountSources.set(TredAccount.key(decoded.event),event);
        if(decoded.source)sourceSample=decoded.source;
      }
      if(rejected.length)warnings[kind]=rejected[0].message+" Отклонено в пакете: "+rejected.length+".";
      if(!accepted.length)continue;
      try{
        await post(endpoint(base),"/api/bridge/events",{events:accepted},token);
        if(epoch!==generation)return;
        lastSeen=Date.now();
        if(TredTime.kinds.includes(kind))received.add(kind);
        if(kind==="account")for(const event of accepted){retryAccounts.delete(TredAccount.key(event));lastBalance={mode:event.mode,received_at:Date.now(),observed_at:event.time,origin:accountSources.get(TredAccount.key(event))?.account_source||"websocket"};}
        if(sourceSample)clockSamples[kind]=sourceSample;
        for(const item of consumed)if(pendingAccounts.get(item.key)?.time===item.time)pendingAccounts.delete(item.key);
        if(!rejected.length)delete warnings[kind];
      }catch(error){
        if(epoch!==generation)return;
        backlogDelay=1000;
        lastError=error.status===400&&error.message==="Пакет содержит неподдерживаемые поля."?"Сервер не поддерживает данные модуля. Обнови весь пакет TRED 0.4.2 и перезапусти сервер.":error.message.slice(0,200);
        if(kind==="account"&&(!error.status||error.status===429||error.status>=500))for(const event of accepted)retryAccounts.set(TredAccount.key(event),accountSources.get(TredAccount.key(event)));
        if(kind!=="account"&&(!error.status||error.status===429||error.status>=500)){
          // Keep the original source event, including its clock tag and time.
          // A transient tunnel error must not throw away a whole history. The
          // retry is bounded; normal freshness validation still runs each time.
          for(const event of originals){const n=deliveryRetries.get(event)||0;if(n<3){deliveryRetries.set(event,n+1);queue.push(event);}}
          boundQueue();backlogDelay=2000;
        }
        if(error.status===401){needsPairing=true;break;}
        if(lastError==="Некорректное время данных."||lastError==="Получены устаревшие данные.")warnings[kind]=TredTime.rejectedByServer(kind,accepted,Date.now()/1000);
      }
    }
    if(epoch===generation)await chrome.storage.local.set({lastError,needsPairing,timeWarnings:warnings,receivedKinds:[...received],clockSamples,accountCandidate,identityCandidate,accountProfiles,lastBalance,...(lastSeen===null?{}:{lastSeen})});
  }catch(error){
    backlogDelay=1000;
    if(epoch===generation)await chrome.storage.local.set({lastError:error.message.slice(0,200)});
  }finally{sending=false;if(queue.length)schedule(backlogDelay);}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  (async()=>{
    if(message.action==="pair"&&!sender.tab){
      const base=endpoint(message.base);
      const result=await post(base,"/api/bridge/claim",{code:message.code});
      if(typeof result.token!=="string"||!/^[A-Za-z0-9_-]{32,100}$/.test(result.token))throw new Error("Сервер не подтвердил связь. Прежние настройки сохранены.");
      // A rejected claim preserves the previously stored binding.
      generation++;queue=[];pendingAccounts.clear();streamTabs.clear();tabAccounts.clear();retryAccounts.clear();
      await chrome.storage.local.set({base,token:result.token,lastError:"",lastSeen:null,needsPairing:false,timeWarnings:{},receivedKinds:[],clockSamples:{},accountCandidate:null,confirmedUsdAccounts:[],identityCandidate:null,manualIdentities:[],accountProfiles:[],lastManualAccountId:"",lastBalance:null,relayLastSeen:null,terminalCode:null});
      return {ok:true};
    }
    if(message.action==="confirm-account-id"&&!sender.tab){
      const epoch=generation;
      const stored=await chrome.storage.local.get(["base","token","needsPairing","identityCandidate","manualIdentities","confirmedUsdAccounts","accountProfiles","accountCandidate"]);
      if(epoch!==generation)throw new Error("Подключение изменилось. Открой модуль снова.");
      if(!stored.base||!stored.token||stored.needsPairing)throw new Error("Сначала свяжи кабинет.");
      const target=stored.identityCandidate;
      if(!TredAccount.id(message.account_id))throw new Error("Pocket ID должен содержать только цифры, от 1 до 20.");
      if(!target||!TredAccount.stream(target.source_stream)||target.source_stream!==message.source_stream||target.mode!==message.mode)throw new Error("График или счёт изменился. Проверь данные в модуле и повтори.");
      if(!Number.isInteger(message.active_tab_id)||target.tab_id!==message.active_tab_id)throw new Error("Открой TRED на вкладке с нужным графиком Pocket Option, затем подтверди ID.");
      if(!streamTabs.has(target.source_stream))throw new Error("Свежие сведения о счёте недоступны. Обнови график Pocket Option и подтверди ID снова.");
      if(streamTabs.get(target.source_stream)!==message.active_tab_id)throw new Error("График изменился. Открой модуль на нужной вкладке Pocket Option.");
      prunePending();
      const pending=pendingAccounts.get(TredAccount.streamKey(target));
      if(!pending||TredTime.problem(pending,Date.now()/1000)||!TredAccount.manualStatuses.includes(pending.id_status)||pending.account_id!=null||TredAccount.basic(pending))throw new Error("Свежий баланс уже недоступен. Обнови график Pocket Option, затем подтверди ID в течение минуты.");
      if(pending.currency==null&&message.confirm_usd!==true)throw new Error("Если счёт в USD, отметь подтверждение валюты. Другие валюты пока не поддерживаются.");
      generation++;queue=[];
      const manualIdentities=TredAccount.identities(stored.manualIdentities).filter(x=>TredAccount.streamKey(x)!==TredAccount.streamKey(target));
      manualIdentities.push({source_stream:target.source_stream,mode:target.mode,account_id:message.account_id});
      const accountProfiles=TredAccount.storedProfiles(stored).filter(x=>x.mode!==target.mode);
      accountProfiles.push({account_id:message.account_id,mode:target.mode,source_account:pending.source_account??null});
      const confirmedUsdAccounts=TredAccount.confirmations(stored.confirmedUsdAccounts);
      const currencyKey=TredAccount.key({account_id:message.account_id,mode:target.mode});
      if(pending.currency==null&&!confirmedUsdAccounts.includes(currencyKey))confirmedUsdAccounts.push(currencyKey);
      await chrome.storage.local.set({accountProfiles,manualIdentities:manualIdentities.slice(-20),lastManualAccountId:message.account_id,confirmedUsdAccounts:confirmedUsdAccounts.slice(-20)});
      // Revalidate this same captured value through the ordinary pipeline.
      // Its original time is preserved; no cached balance survives a restart.
      queue.push(pending);await flush();if(queue.length)schedule();
      return {ok:true};
    }
    if(message.action==="forget-account-id"&&!sender.tab){
      const stored=await chrome.storage.local.get(["accountCandidate","accountProfiles","manualIdentities"]);
      const target=stored.accountCandidate;
      if(!target||target.identity_source!=="manual"||target.account_id!==message.account_id||target.mode!==message.mode)throw new Error("Счёт изменился. Открой модуль снова.");
      generation++;queue=[];pendingAccounts.clear();retryAccounts.clear();
      const accountProfiles=TredAccount.storedProfiles(stored).filter(x=>x.mode!==target.mode);
      const manualIdentities=TredAccount.identities(stored.manualIdentities).filter(x=>x.mode!==target.mode);
      await chrome.storage.local.set({accountProfiles,manualIdentities,accountCandidate:null,identityCandidate:null});
      return {ok:true};
    }
    if(["confirm-usd","forget-usd"].includes(message.action)&&!sender.tab){
      const stored=await chrome.storage.local.get(["base","token","needsPairing","accountCandidate","confirmedUsdAccounts","timeWarnings","receivedKinds"]);
      const candidate=stored.accountCandidate;
      if(!stored.base||!stored.token||stored.needsPairing)throw new Error("Сначала свяжи кабинет.");
      if(!candidate||!TredAccount.id(candidate.account_id)||!TredAccount.mode(candidate.mode)||candidate.account_id!==message.account_id||candidate.mode!==message.mode)throw new Error("Счёт изменился. Проверь его в окне модуля и повтори подтверждение.");
      if(message.action==="confirm-usd"&&(candidate.reported_currency!==null||typeof candidate.seen_at!=="number"||!Number.isFinite(candidate.seen_at)||Date.now()/1000-candidate.seen_at>300||candidate.seen_at>Date.now()/1000+5))throw new Error("Для подтверждения обнови Pocket Option. Валюта не может заменять указанную брокером.");
      generation++;queue=[];
      const values=TredAccount.confirmations(stored.confirmedUsdAccounts).filter(value=>value!==TredAccount.key(candidate));
      if(message.action==="confirm-usd")values.push(TredAccount.key(candidate));
      const timeWarnings=Object.fromEntries(Object.entries(stored.timeWarnings||{}).filter(([kind])=>kind!=="account"));
      await chrome.storage.local.set({confirmedUsdAccounts:values.slice(-20),timeWarnings,receivedKinds:(stored.receivedKinds||[]).filter(kind=>kind!=="account")});
      return {ok:true};
    }
    if(message.action==="market-clock"&&!sender.tab){
      if(!["utc","po-utc2"].includes(message.profile))throw new Error("Неподдерживаемый формат времени.");
      generation++;queue=[];
      const stored=await chrome.storage.local.get(["timeWarnings","receivedKinds"]);
      const timeWarnings=Object.fromEntries(Object.entries(stored.timeWarnings||{}).filter(([kind])=>!["quote","candle"].includes(kind)));
      await chrome.storage.local.set({marketClock:message.profile,timeWarnings,clockSamples:{},receivedKinds:(stored.receivedKinds||[]).filter(kind=>!["quote","candle"].includes(kind))});
      return {ok:true};
    }
    if(message.action==="disconnect"&&!sender.tab){generation++;queue=[];pendingAccounts.clear();streamTabs.clear();tabAccounts.clear();retryAccounts.clear();await chrome.storage.local.clear();return {ok:true};}
    if(message.action==="status"&&!sender.tab){
      const {base,token,lastError,lastSeen,needsPairing,timeWarnings,receivedKinds,marketClock,clockSamples,accountCandidate,confirmedUsdAccounts,identityCandidate,lastManualAccountId,lastBalance,relayLastSeen,terminalCode}=await chrome.storage.local.get(["base","token","lastError","lastSeen","needsPairing","timeWarnings","receivedKinds","marketClock","clockSamples","accountCandidate","confirmedUsdAccounts","identityCandidate","lastManualAccountId","lastBalance","relayLastSeen","terminalCode"]);
      const diagnostic=Object.entries(timeWarnings||{}).filter(([kind,value])=>TredTime.kinds.includes(kind)&&typeof value==="string").map(([,value])=>value).join("\n");
      const identityNeedsTab=!!identityCandidate&&identityCandidate.tab_id!==message.active_tab_id;
      if(base&&token&&(!relayLastSeen||Date.now()-relayLastSeen>20000))await attachOpenTerminals();
      return {worker_version:WORKER_VERSION,scannerStatus:await TredScan.status(),autoRefreshSeconds:5,lastBalance,relayLastSeen,terminalCode,base,paired:!!(base&&token),needsPairing:!!needsPairing,lastError,lastSeen,diagnostic,receivedKinds:(receivedKinds||[]).filter(x=>TredTime.kinds.includes(x)),marketClock:TredTime.profile(marketClock),clockSamples:clockSamples||{},accountCandidate:accountCandidate||null,usdConfirmed:!!(accountCandidate&&TredAccount.confirmations(confirmedUsdAccounts).includes(TredAccount.key(accountCandidate))),identityCandidate:identityNeedsTab?null:identityCandidate||null,identityNeedsTab,lastManualAccountId:TredAccount.id(lastManualAccountId)?lastManualAccountId:""};
    }
    if(message.action==="terminal-balance"&&fromPocket(sender)){
      const epoch=generation;
      const snapshot=message.snapshot;
      if(!snapshot||typeof snapshot!=="object"||Array.isArray(snapshot)||!Object.keys(snapshot).every(k=>["mode","currency","balance","time"].includes(k))||snapshot.currency!=="USD"||TredAccount.basic(snapshot)||TredTime.problem({...snapshot,type:"account"},Date.now()/1000))throw new Error("Неподдерживаемый снимок баланса терминала.");
      const stored=await chrome.storage.local.get(["base","token","needsPairing","accountProfiles","accountCandidate"]);
      if(epoch!==generation)return {ok:false};
      if(!stored.base||!stored.token||stored.needsPairing)return {ok:false};
      const profile=TredAccount.storedProfiles(stored).find(x=>x.mode===snapshot.mode);
      const context=tabAccounts.get(sender.tab.id+":"+snapshot.mode);
      const candidate=stored.accountCandidate;
      const accountId=profile?.account_id||(context&&TredAccount.id(context.account_id)?context.account_id:null)||(candidate?.mode===snapshot.mode&&TredAccount.id(candidate.account_id)?candidate.account_id:null);
      if(!accountId)return {ok:false};
      if(context&&(TredAccount.id(context.account_id)&&context.account_id!==accountId||context.source_account&&profile?.source_account&&context.source_account!==profile.source_account))return {ok:false};
      queue.push({type:"account",...snapshot,account_id:accountId,account_source:"terminal-dom"});boundQueue();
      await chrome.storage.local.set({relayLastSeen:Date.now(),terminalCode:"ready"});
      schedule();return {ok:true};
    }
    if(message.action==="sync"&&fromPocket(sender)){
      const codes=["ready","not_terminal","ambiguous_header","header_not_found","unsupported_currency","reader_missing","reader_error"];
      await chrome.storage.local.set({relayLastSeen:Date.now(),...(codes.includes(message.terminal_code)?{terminalCode:message.terminal_code}:{})});
      for(const [key,event] of retryAccounts){
        retryAccounts.delete(key);
        if(!TredTime.problem(event,Date.now()/1000))queue.push(event);
      }
      await flush();await TredScan.sync(post,endpoint);return {ok:true};
    }
    if(message.action==="events"&&fromPocket(sender)&&Array.isArray(message.events)){
      const epoch=generation;
      const events=await TredScan.tag(message.events.slice(0,250).filter(event=>{
        if(event&&Object.hasOwn(event,"account_source"))return false;
        if(event?.type==="account"&&TredAccount.mode(event.mode)&&Object.keys(event).every(k=>eventFields.account.includes(k))&&(event.source_account==null||TredAccount.sourceAccount(event.source_account)))tabAccounts.set(sender.tab.id+":"+event.mode,{account_id:event.account_id,source_account:event.source_account??null});
        while(tabAccounts.size>20)tabAccounts.delete(tabAccounts.keys().next().value);
        if(event?.type==="account"&&TredAccount.mode(event.mode))for(const [key,old] of retryAccounts)if(old.mode===event.mode)retryAccounts.delete(key);
        if(event?.type!=="account"||!TredAccount.stream(event.source_stream))return true;
        if(streamTabs.has(event.source_stream)&&streamTabs.get(event.source_stream)!==sender.tab.id)return false;
        streamTabs.set(event.source_stream,sender.tab.id);
        while(streamTabs.size>20)streamTabs.delete(streamTabs.keys().next().value);
        return true;
      }),sender);
      if(epoch!==generation)return {ok:false};
      TredScan.remember(events,sender);
      queue.push(...events);
      // A queued catalog must not revive its socket after close was delivered
      // first by the priority queue. Other sockets and balances stay intact.
      const closed=new Set(events.filter(e=>e?.type==="feed"&&e.connected===false&&typeof e.feed_id==="string").map(e=>e.feed_id));
      if(closed.size)queue=queue.filter(e=>!["asset","quote"].includes(e?.type)||!closed.has(e.feed_id));
      boundQueue();schedule();
      // Do not rely only on the relay's page timer to renew the 15s plan.
      // Native events wake MV3 workers; sync itself is bounded to once per 4s.
      await TredScan.sync(post,endpoint);return {ok:true};
    }
    throw new Error("Действие недоступно.");
  })().then(reply).catch(error=>reply({error:error.message}));
  return true;
});

// Restore the collector in already-open tabs after an extension update. This
// never reloads the trading page and requests no broker/network operation.
let lastAttachAttempt=0;
async function attachOpenTerminals(){
  if(!chrome.scripting||!chrome.tabs||Date.now()-lastAttachAttempt<20000)return;
  lastAttachAttempt=Date.now();
  try{
    const tabs=await chrome.tabs.query({url:["https://pocketoption.com/*","https://*.pocketoption.com/*"]});
    for(const tab of tabs){
      if(!Number.isInteger(tab.id)||!/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\/(?:[a-z]{2}\/)?cabinet(?:\/|[?#]|$)/i.test(tab.url||""))continue;
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:"MAIN",files:["market-scan.js","observer.js"]});
      await chrome.scripting.executeScript({target:{tabId:tab.id},world:"ISOLATED",files:["balance-read.js","relay.js"]});
    }
  }catch(_){/* Next popup status retries; no credentials or raw errors logged. */}
}
chrome.runtime.onInstalled?.addListener(()=>{void attachOpenTerminals();});
chrome.runtime.onStartup?.addListener(()=>{void attachOpenTerminals();});
chrome.tabs?.onRemoved?.addListener(tabId=>{
  const epoch=generation;
  void TredScan.removed(tabId,post,endpoint,events=>{if(epoch===generation){queue.push(...events);boundQueue();schedule();}});
});
