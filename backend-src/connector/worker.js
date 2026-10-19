"use strict";
importScripts("time-check.js");
const WORKER_VERSION="0.3.2";
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
const eventFields={account:["type","mode","account_id","currency","balance","time"],asset:["type","symbol","market","is_open","payout","expiries","time"],quote:["type","symbol","price","time"],candle:["type","symbol","period","time","open","high","low","close"],trade:["type","mode","account_id","id","symbol","direction","stake","opened_at","expires_at","closed_at","pnl","status","time"]};
let queue=[],sending=false,timer,generation=0;
// One flush/second, at most six type-specific requests. No stale payload replay.
function schedule(){if(!timer)timer=setTimeout(()=>{timer=null;flush();},1000);}
async function flush(){
  if(sending||!queue.length)return;
  sending=true;
  const epoch=generation;
  try{
    const stored=await chrome.storage.local.get(["base","token","needsPairing","timeWarnings","receivedKinds"]);
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
    let lastError="",lastSeen=null,needsPairing=false;
    for(const kind of [...TredTime.kinds,"unknown"]){
      const rows=groups.get(kind);
      if(!rows||epoch!==generation)continue;
      const accepted=[],rejected=[];
      for(const event of rows){
        const safe=eventFields[kind]&&Object.keys(event||{}).every(key=>eventFields[kind].includes(key));
        const error=safe?TredTime.problem(event,Date.now()/1000):{message:TredTime.labels[kind]+": неподдерживаемые поля. Событие не передано."};
        if(error)rejected.push(error);else accepted.push(event);
      }
      if(rejected.length)warnings[kind]=rejected[0].message+" Отклонено в пакете: "+rejected.length+".";
      if(!accepted.length)continue;
      try{
        await post(endpoint(base),"/api/bridge/events",{events:accepted},token);
        lastSeen=Date.now();
        if(TredTime.kinds.includes(kind))received.add(kind);
        if(!rejected.length)delete warnings[kind];
      }catch(error){
        lastError=error.message.slice(0,200);
        if(error.status===401){needsPairing=true;break;}
        if(lastError==="Некорректное время данных."||lastError==="Получены устаревшие данные.")warnings[kind]=TredTime.rejectedByServer(kind,accepted,Date.now()/1000);
      }
    }
    if(epoch===generation)await chrome.storage.local.set({lastError,needsPairing,timeWarnings:warnings,receivedKinds:[...received],...(lastSeen===null?{}:{lastSeen})});
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
      generation++;queue=[];
      await chrome.storage.local.set({base,token:result.token,lastError:"",lastSeen:null,needsPairing:false,timeWarnings:{},receivedKinds:[]});
      return {ok:true};
    }
    if(message.action==="disconnect"&&!sender.tab){generation++;queue=[];await chrome.storage.local.clear();return {ok:true};}
    if(message.action==="status"&&!sender.tab){
      const {base,token,lastError,lastSeen,needsPairing,timeWarnings,receivedKinds}=await chrome.storage.local.get(["base","token","lastError","lastSeen","needsPairing","timeWarnings","receivedKinds"]);
      const diagnostic=Object.entries(timeWarnings||{}).filter(([kind,value])=>TredTime.kinds.includes(kind)&&typeof value==="string").map(([,value])=>value).join("\n");
      return {worker_version:WORKER_VERSION,base,paired:!!(base&&token),needsPairing:!!needsPairing,lastError,lastSeen,diagnostic,receivedKinds:(receivedKinds||[]).filter(x=>TredTime.kinds.includes(x))};
    }
    if(message.action==="events"&&sender.tab&&/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\//.test(sender.url||"")&&Array.isArray(message.events)){
      queue.push(...message.events.slice(0,250));queue=queue.slice(-1000);schedule();return {ok:true};
    }
    throw new Error("Действие недоступно.");
  })().then(reply).catch(error=>reply({error:error.message}));
  return true;
});
