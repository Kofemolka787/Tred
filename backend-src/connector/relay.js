"use strict";
// Only this isolated context can reach the worker. Reinstallation replaces a
// disposed relay in an already-open tab, without navigating the trading page.
(()=>{
  const VERSION="0.3.14",old=globalThis.__tredRelay;
  if(old?.version===VERSION&&old.active){old.scan();return;}
  old?.dispose();
  let stopped=false,interval,settleTimer,pendingKey="",lastKey="",checking=false;
  let terminalCode="header_not_found";
  const observer=new MutationObserver(()=>scan());
  function dispose(){
    if(stopped)return;stopped=true;clearInterval(interval);clearTimeout(settleTimer);
    observer.disconnect();window.removeEventListener("message",relay,true);
  }
  async function send(message){
    if(stopped)return null;
    try{return await chrome.runtime.sendMessage(message);}
    catch(error){
      // sendMessage can throw before returning a Promise on extension reload.
      if(/context invalidated/i.test(String(error?.message))||!chrome.runtime?.id)dispose();
      return null;
    }
  }
  function relay(event){
    if(event.source!==window||event.origin!==location.origin||event.data?.source!=="tred-observer-v1")return;
    event.stopImmediatePropagation();
    if(event.data.observer_version!==VERSION)return;
    const events=event.data.events;
    if(!Array.isArray(events)||!events.length||events.length>250)return;
    void send({action:"events",events});
  }
  function reading(){try{return globalThis.TredTerminalBalance?.read()||{code:"reader_missing"};}catch(_){return {code:"reader_error"};}}
  function scan(){
    if(stopped)return;
    const value=reading();terminalCode=value.code;
    if(!value.snapshot){pendingKey="";clearTimeout(settleTimer);return;}
    const key=JSON.stringify(value.snapshot);
    if(key===lastKey||key===pendingKey)return;
    pendingKey=key;clearTimeout(settleTimer);
    // Avoid intermediate animation values and account-switching transitions.
    settleTimer=setTimeout(async()=>{
      const latest=reading();
      if(!latest.snapshot||JSON.stringify(latest.snapshot)!==key){pendingKey="";scan();return;}
      const result=await send({action:"terminal-balance",snapshot:{...latest.snapshot,time:Date.now()/1000}});
      if(result?.ok)lastKey=key;
      pendingKey="";
    },250);
  }
  async function tick(){
    if(stopped||checking)return;checking=true;
    try{scan();await send({action:"sync",terminal_code:terminalCode});}
    finally{checking=false;}
  }
  window.addEventListener("message",relay,true);
  if(document.documentElement)observer.observe(document.documentElement,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:["class","style","hidden"]});
  interval=setInterval(()=>{void tick();},5000);
  globalThis.__tredRelay={version:VERSION,get active(){return !stopped;},dispose,scan};
  void tick();
})();
