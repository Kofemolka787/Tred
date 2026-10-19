"use strict";
importScripts("time-check.js","account-check.js");
const WORKER_VERSION="0.3.6";
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
const eventFields={account:["type","mode","account_id","currency","balance","time","source_stream","id_status","id_shape"],asset:["type","symbol","market","is_open","payout","expiries","time"],quote:["type","symbol","price","time","source_clock"],candle:["type","symbol","period","time","open","high","low","close","source_clock"],trade:["type","mode","account_id","id","symbol","direction","stake","opened_at","expires_at","closed_at","pnl","status","time"]};
let queue=[],sending=false,timer,generation=0;
// Pending balance is usable for <=60 seconds, in RAM, at its ORIGINAL time.
// It may be delivered once after an explicit identity confirmation, never aged
// forward or stored on disk. New auth/socket/mode needs a fresh confirmation.
const pendingAccounts=new Map();
const streamTabs=new Map();
function prunePending(){
  const now=Date.now()/1000;
  for(const [key,event] of pendingAccounts)if(now-event.time>60||event.time>now+5)pendingAccounts.delete(key);
  while(pendingAccounts.size>20)pendingAccounts.delete(pendingAccounts.keys().next().value);
}
// One flush/second, at most six type-specific requests.
function schedule(){if(!timer)timer=setTimeout(()=>{timer=null;flush();},1000);}
async function flush(){
  if(sending||!queue.length)return;
  sending=true;
  const epoch=generation;
  try{
    const stored=await chrome.storage.local.get(["base","token","needsPairing","timeWarnings","receivedKinds","marketClock","clockSamples","accountCandidate","confirmedUsdAccounts","identityCandidate","manualIdentities"]);
    const {base,token}=stored;
    if(!base||!token||stored.needsPairing){queue=[];return;}
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
    prunePending();
    let lastError="",lastSeen=null,needsPairing=false;
    for(const kind of [...TredTime.kinds,"unknown"]){
      const rows=groups.get(kind);
      if(!rows||epoch!==generation)continue;
      const accepted=[],rejected=[],consumed=[];let sourceSample;
      for(const event of rows){
        const safe=eventFields[kind]&&Object.keys(event||{}).every(key=>eventFields[kind].includes(key));
        if(!safe){rejected.push({message:TredTime.labels[kind]+": неподдерживаемые поля. Событие не передано."});continue;}
        const decoded=TredTime.decode(event,stored.marketClock),now=Date.now()/1000;
        const error=decoded.error?{message:decoded.error}:TredTime.problem(decoded.event,now);
        if(error){rejected.push(error);continue;}
        if(kind==="account"){
          const resolved=TredAccount.resolve(decoded.event,stored.manualIdentities);
          accountCandidate=null;identityCandidate=resolved.identityCandidate?{...resolved.identityCandidate,tab_id:streamTabs.get(event.source_stream)??null}:null;
          if(identityCandidate){pendingAccounts.set(TredAccount.streamKey(event),{...event});prunePending();}
          if(resolved.error){rejected.push({message:resolved.error});continue;}
          const checked=TredAccount.inspect(resolved.event,stored.confirmedUsdAccounts);
          accountCandidate=checked.candidate;
          if(resolved.manual&&accountCandidate)Object.assign(accountCandidate,{identity_source:"manual",source_stream:resolved.source_stream});
          if(checked.error){rejected.push({message:checked.error});continue;}
          decoded.event=checked.event;
          if(resolved.manual)consumed.push({key:TredAccount.streamKey(event),time:event.time});
        }
        // The terminal includes the current, unfinished candle in its history.
        // Never send it to research, including after UTC+2 source decoding.
        if(kind==="candle"&&[60,300,900].includes(decoded.event.period)&&decoded.event.time+decoded.event.period>now)continue;
        accepted.push(decoded.event);
        if(decoded.source)sourceSample=decoded.source;
      }
      if(rejected.length)warnings[kind]=rejected[0].message+" Отклонено в пакете: "+rejected.length+".";
      if(!accepted.length)continue;
      try{
        await post(endpoint(base),"/api/bridge/events",{events:accepted},token);
        lastSeen=Date.now();
        if(TredTime.kinds.includes(kind))received.add(kind);
        if(sourceSample)clockSamples[kind]=sourceSample;
        for(const item of consumed)if(pendingAccounts.get(item.key)?.time===item.time)pendingAccounts.delete(item.key);
        if(!rejected.length)delete warnings[kind];
      }catch(error){
        lastError=error.message.slice(0,200);
        if(error.status===401){needsPairing=true;break;}
        if(lastError==="Некорректное время данных."||lastError==="Получены устаревшие данные.")warnings[kind]=TredTime.rejectedByServer(kind,accepted,Date.now()/1000);
      }
    }
    if(epoch===generation)await chrome.storage.local.set({lastError,needsPairing,timeWarnings:warnings,receivedKinds:[...received],clockSamples,accountCandidate,identityCandidate,...(lastSeen===null?{}:{lastSeen})});
  }catch(error){
    if(epoch===generation)await chrome.storage.local.set({lastError:error.message.slice(0,200)});
  }finally{sending=false;if(queue.length)schedule();}
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  (async()=>{
    if(message.action==="pair"&&!sender.tab){
      const base=endpoint(message.base);
      const result=await post(base,"/api/bridge/claim",{code:message.code});
      if(typeof result.token!=="string"||!/^[A-Za-z0-9_-]{32,100}$/.test(result.token))throw new Error("Сервер не подтвердил связь. Прежние настройки сохранены.");
      // A rejected claim preserves the previously stored binding.
      generation++;queue=[];pendingAccounts.clear();streamTabs.clear();
      await chrome.storage.local.set({base,token:result.token,lastError:"",lastSeen:null,needsPairing:false,timeWarnings:{},receivedKinds:[],clockSamples:{},accountCandidate:null,confirmedUsdAccounts:[],identityCandidate:null,manualIdentities:[],lastManualAccountId:""});
      return {ok:true};
    }
    if(message.action==="confirm-account-id"&&!sender.tab){
      const epoch=generation;
      const stored=await chrome.storage.local.get(["base","token","needsPairing","identityCandidate","manualIdentities","confirmedUsdAccounts"]);
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
      const confirmedUsdAccounts=TredAccount.confirmations(stored.confirmedUsdAccounts);
      const currencyKey=TredAccount.key({account_id:message.account_id,mode:target.mode});
      if(pending.currency==null&&!confirmedUsdAccounts.includes(currencyKey))confirmedUsdAccounts.push(currencyKey);
      await chrome.storage.local.set({manualIdentities:manualIdentities.slice(-20),lastManualAccountId:message.account_id,confirmedUsdAccounts:confirmedUsdAccounts.slice(-20)});
      // Revalidate this same captured value through the ordinary pipeline.
      // Its original time is preserved; no cached balance survives a restart.
      queue.push(pending);await flush();if(queue.length)schedule();
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
    if(message.action==="disconnect"&&!sender.tab){generation++;queue=[];pendingAccounts.clear();streamTabs.clear();await chrome.storage.local.clear();return {ok:true};}
    if(message.action==="status"&&!sender.tab){
      const {base,token,lastError,lastSeen,needsPairing,timeWarnings,receivedKinds,marketClock,clockSamples,accountCandidate,confirmedUsdAccounts,identityCandidate,lastManualAccountId}=await chrome.storage.local.get(["base","token","lastError","lastSeen","needsPairing","timeWarnings","receivedKinds","marketClock","clockSamples","accountCandidate","confirmedUsdAccounts","identityCandidate","lastManualAccountId"]);
      const diagnostic=Object.entries(timeWarnings||{}).filter(([kind,value])=>TredTime.kinds.includes(kind)&&typeof value==="string").map(([,value])=>value).join("\n");
      const identityNeedsTab=!!identityCandidate&&identityCandidate.tab_id!==message.active_tab_id;
      return {worker_version:WORKER_VERSION,base,paired:!!(base&&token),needsPairing:!!needsPairing,lastError,lastSeen,diagnostic,receivedKinds:(receivedKinds||[]).filter(x=>TredTime.kinds.includes(x)),marketClock:TredTime.profile(marketClock),clockSamples:clockSamples||{},accountCandidate:accountCandidate||null,usdConfirmed:!!(accountCandidate&&TredAccount.confirmations(confirmedUsdAccounts).includes(TredAccount.key(accountCandidate))),identityCandidate:identityNeedsTab?null:identityCandidate||null,identityNeedsTab,lastManualAccountId:TredAccount.id(lastManualAccountId)?lastManualAccountId:""};
    }
    if(message.action==="events"&&sender.tab&&/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\//.test(sender.url||"")&&Array.isArray(message.events)){
      const events=message.events.slice(0,250).filter(event=>{
        if(event?.type!=="account"||!TredAccount.stream(event.source_stream))return true;
        if(streamTabs.has(event.source_stream)&&streamTabs.get(event.source_stream)!==sender.tab.id)return false;
        streamTabs.set(event.source_stream,sender.tab.id);
        while(streamTabs.size>20)streamTabs.delete(streamTabs.keys().next().value);
        return true;
      });
      queue.push(...events);queue=queue.slice(-1000);schedule();return {ok:true};
    }
    throw new Error("Действие недоступно.");
  })().then(reply).catch(error=>reply({error:error.message}));
  return true;
});
