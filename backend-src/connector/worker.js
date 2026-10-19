"use strict";
const endpoint = value => {
  const u = new URL(value);
  if (u.protocol !== "https:" || !/^[a-z0-9-]+\.trycloudflare\.com$/.test(u.hostname) || u.port || u.username || u.password || u.search || u.hash || u.pathname !== "/") throw new Error("Укажи HTTPS-адрес своего TRED-туннеля.");
  return u.origin;
};
async function post(base,path,body,token) {
  const response = await fetch(base+path,{method:"POST",headers:{"Content-Type":"application/json",...(token ? {Authorization:"Bearer "+token} : {})},body:JSON.stringify(body),signal:AbortSignal.timeout(10000),redirect:"error"});
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || "Нет связи с сервером.");
  return result;
}
// Bound storage is private to extension contexts, never content scripts.
chrome.storage.local.setAccessLevel({accessLevel:"TRUSTED_CONTEXTS"});
let queue = [];
let sending = false;
let timer;
function schedule() { if (!timer) timer=setTimeout(() => { timer=null; flush(); },250); }
async function flush() {
  if (sending || !queue.length) return;
  sending = true;
  const {base,token} = await chrome.storage.local.get(["base","token"]);
  if (!base || !token) { queue = []; sending = false; return; }
  const batch = queue.splice(0,250);
  try {
    await post(endpoint(base),"/api/bridge/events",{events:batch},token);
    await chrome.storage.local.set({lastSeen:Date.now(),lastError:""});
  } catch (error) {
    // Do not persist market/account payloads and do not replay stale balances.
    await chrome.storage.local.set({lastError:error.message.slice(0,160)});
  } finally { sending = false; if (queue.length) schedule(); }
}
chrome.runtime.onMessage.addListener((message,sender,reply) => {
  (async () => {
    if (message.action === "pair" && !sender.tab) {
      const base = endpoint(message.base);
      const result = await post(base,"/api/bridge/claim",{code:message.code});
      await chrome.storage.local.set({base,token:result.token,lastError:"",lastSeen:null});
      return {ok:true};
    }
    if (message.action === "disconnect" && !sender.tab) {
      queue = []; await chrome.storage.local.clear(); return {ok:true};
    }
    if (message.action === "status" && !sender.tab) {
      const {base,lastError,lastSeen} = await chrome.storage.local.get(["base","lastError","lastSeen"]);
      return {base,lastError,lastSeen};
    }
    if (message.action === "events" && sender.tab && /^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\//.test(sender.url || "") && Array.isArray(message.events)) {
      queue.push(...message.events.slice(0,250));
      queue = queue.slice(-1000);
      schedule();
      return {ok:true};
    }
    throw new Error("Действие недоступно.");
  })().then(reply).catch(error => reply({error:error.message}));
  return true;
});
