"use strict";
// The pairing token stays in the worker. Only bounded market-data plans enter
// the page; it receives no API key, broker credential, token or arbitrary code.
globalThis.TredScan=(()=>{
  const feeds=new Map();let running=false,lastPoll=0,code="no_socket";
  const validFeed=v=>typeof v==="string"&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(v);
  const terminal=t=>Number.isInteger(t?.id)&&/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\/(?:[a-z]{2}\/)?cabinet(?:\/|[?#]|$)/i.test(t.url||t.pendingUrl||"");
  const collector=t=>terminal(t)&&new URL(t.url||t.pendingUrl).searchParams.get("tred_scanner")==="1";
  let ownedTabId=null;
  async function isCollector(tab){
    if(!terminal(tab))return false;
    if(collector(tab)||ownedTabId===tab.id)return true;
    // Browser-session storage survives worker suspension, but not a browser
    // restart where a saved numeric tab ID may now belong to the user's tab.
    const proof=await chrome.storage.session?.get(["tredCollectorTabId"]);
    if(proof?.tredCollectorTabId===tab.id){ownedTabId=tab.id;return true;}
    return false;
  }
  async function tag(events,sender){
    const {scannerTabId,scannerFeeds=[]}=await chrome.storage.local.get(["scannerTabId","scannerFeeds"]);
    const dedicated=sender.tab.id===scannerTabId&&await isCollector({id:sender.tab.id,url:sender.url});
    if(dedicated){
      const ids=new Set(scannerFeeds.filter(validFeed));
      for(const e of events)if(validFeed(e?.feed_id)){if(e.type==="feed"&&e.connected===false)ids.delete(e.feed_id);else if(e.type==="asset")ids.add(e.feed_id);}
      await chrome.storage.local.set({scannerFeeds:[...ids].slice(-128)});
    }
    return events.map(event=>event?.type==="asset"?{...event,source_role:dedicated?"scanner":"terminal"}:event);
  }
  async function ensureScanner(tabs,plan,stored){
    const id=stored.scannerTabId;
    if(Number.isInteger(id)&&await isCollector(tabs.find(t=>t.id===id))){
      if(Number.isInteger(plan.scan_id)&&stored.scannerJobId!==plan.scan_id&&chrome.tabs.reload){
        await chrome.storage.local.set({scannerJobId:plan.scan_id});
        await chrome.tabs.reload(id);
      }
      return id;
    }
    if(Number.isInteger(id)&&chrome.tabs.get){
      try{
        const existing=await chrome.tabs.get(id);
        if(await isCollector(existing))return id;
        // A live collector may still be at about:blank or the login page.
        // A normal terminal with a reused browser tab ID is not our collector.
        if(!terminal(existing))return null;
      }catch(_){/* It was closed while this worker was asleep. */}
    }
    // A user's explicit close pauses this job. A new request can open a new
    // collector, but an old poll cannot keep recreating a closed tab.
    if(!plan.scanner_active||!chrome.tabs.create||!chrome.tabs.update||stored.scannerPaused&&(plan.scan_id==null||stored.scannerPausedJob===plan.scan_id))return null;
    const source=tabs.find(t=>terminal(t)&&!collector(t));
    if(!source)return null;
    const url=new URL(source.url||source.pendingUrl);url.search="?tred_scanner=1";url.hash="";
    const tab=await chrome.tabs.create({url:"about:blank",active:false});
    if(!Number.isInteger(tab?.id))throw Error("scanner tab");
    ownedTabId=tab.id;
    await chrome.storage.session?.set({tredCollectorTabId:tab.id});
    await chrome.storage.local.set({scannerTabId:tab.id,scannerJobId:plan.scan_id,scannerPausedJob:null,scannerPaused:false,scannerFeeds:[]});
    await chrome.tabs.update(tab.id,{url:url.href});
    return tab.id;
  }
  function remember(events,sender){
    for(const e of events){
      if(!validFeed(e?.feed_id))continue;
      if(e.type==="feed"&&e.connected===false)feeds.delete(e.feed_id);
      else if(["asset","quote"].includes(e.type))feeds.set(e.feed_id,sender.tab.id);
    }
    while(feeds.size>128)feeds.delete(feeds.keys().next().value);
  }
  function reset(){feeds.clear();lastPoll=0;code="no_socket";}
  async function sync(post,endpoint){
    if(running||!chrome.scripting||!chrome.tabs||Date.now()-lastPoll<4000)return;
    running=true;lastPoll=Date.now();
    try{
      const stored=await chrome.storage.local.get(["base","token","needsPairing","marketClock","scannerTabId","scannerJobId","scannerPausedJob","scannerPaused"]);
      if(!stored.base||!stored.token||stored.needsPairing)return;
      const base=endpoint(stored.base),token=stored.token;
      const value=await post(base,"/api/bridge/scan",{code},token);
      if(!value.ok||!value.plan||value.plan.version!==1||!Array.isArray(value.plan.pairs)||value.plan.pairs.length>2000)throw Error("scan response");
      const tabs=await chrome.tabs.query({url:["https://pocketoption.com/*","https://*.pocketoption.com/*"]});
      const scannerTab=await ensureScanner(tabs,value.plan,stored);
      const allowed=new Set(tabs.filter(terminal).map(t=>t.id));
      const missing=[...new Set(value.plan.pairs.map(p=>p.feed_id).filter(validFeed))].filter(id=>!allowed.has(feeds.get(id)));
      if(missing.length&&missing.length<=128){
        const found=new Map();
        for(const tabId of [...allowed].slice(0,32)){
          try{
            const result=await chrome.scripting.executeScript({target:{tabId},world:"MAIN",func:ids=>window.__tredMarketScanner?.connections?.(ids)||[],args:[missing]});
            const ids=result?.[0]?.result;
            if(!Array.isArray(ids)||ids.length>128)continue;
            for(const id of new Set(ids))if(missing.includes(id)){if(!found.has(id))found.set(id,[]);found.get(id).push(tabId);}
          }catch(_){/* A tab may navigate during discovery; another can still attest. */}
        }
        // Never select an arbitrary tab when multiple pages claim the same ID.
        for(const [id,matches] of found)if(matches.length===1)feeds.set(id,matches[0]);
        while(feeds.size>128)feeds.delete(feeds.keys().next().value);
      }
      const grouped=new Map();
      for(const pair of value.plan.pairs){const tab=feeds.get(pair.feed_id);if(!allowed.has(tab))continue;if(!grouped.has(tab))grouped.set(tab,[]);grouped.get(tab).push(pair);}
      code=allowed.size?"no_socket":"no_tab";let accepted=0;
      for(const [tabId,pairs] of grouped){
        const work=tabId===scannerTab&&!value.plan.scanner_active?[]:pairs;
        const result=await chrome.scripting.executeScript({target:{tabId},world:"MAIN",func:plan=>{
          if(plan.dedicated&&typeof document!=="undefined"&&document.body){
            let banner=document.getElementById('tred-collector-banner');
            if(!banner){banner=document.createElement('div');banner.id='tred-collector-banner';banner.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px;background:#171128;color:#eee;font:14px sans-serif;text-align:center';document.body.append(banner);}
            banner.textContent=plan.scanner_active?'TRED: автоматический сбор графиков. Для ручной работы используй основную вкладку Pocket Option.':'TRED: сбор графиков приостановлен до следующего запроса.';
          }
          return window.__tredMarketScanner?.plan(plan)||{code:"unsupported_module"};
        },args:[{...value.plan,pairs:work,dedicated:tabId===scannerTab,offset:stored.marketClock==="po-utc2"?7200:0}]});
        const status=result?.[0]?.result;
        if(status?.code==="ready"){code="ready";accepted+=status.pairs||0;}
        else if(code!=="ready"&&["no_socket","waiting_catalog","request_failed","unsupported_module"].includes(status?.code))code=status.code;
      }
      await chrome.storage.local.set({scannerStatus:{code,pairs:accepted,updated_at:Date.now(),dedicated:Number.isInteger(scannerTab),paused:!!stored.scannerPaused&&!Number.isInteger(scannerTab)}});
    }catch(_){code="request_failed";await chrome.storage.local.set({scannerStatus:{code,pairs:0,updated_at:Date.now()}});}
    finally{running=false;}
  }
  async function status(){
    const {scannerStatus:s}=await chrome.storage.local.get(["scannerStatus"]);
    return s&&["ready","no_socket","no_tab","waiting_catalog","request_failed","unsupported_module"].includes(s.code)&&Number.isInteger(s.pairs)&&s.pairs>=0&&s.pairs<=2000&&Number.isFinite(s.updated_at)?{code:s.code,pairs:s.pairs,updated_at:s.updated_at,dedicated:s.dedicated===true,paused:s.paused===true}:null;
  }
  async function removed(tabId,post,endpoint,enqueue){
    const stored=await chrome.storage.local.get(["scannerTabId","scannerJobId","scannerFeeds","base","token","needsPairing"]);
    const lost=[...new Set([...feeds].filter(([,id])=>id===tabId).map(([id])=>id).concat(stored.scannerTabId===tabId?(stored.scannerFeeds||[]).filter(validFeed):[]))];
    for(const id of lost)feeds.delete(id);
    if(stored.scannerTabId===tabId){
      ownedTabId=null;await chrome.storage.session?.set({tredCollectorTabId:null});
      await chrome.storage.local.set({scannerTabId:null,scannerFeeds:[],scannerPausedJob:stored.scannerJobId,scannerPaused:true});
    }
    if(lost.length&&stored.base&&stored.token&&!stored.needsPairing){
      const events=lost.map(feed_id=>({type:"feed",feed_id,connected:false,time:Date.now()/1000}));
      if(enqueue)enqueue(events);
      else try{await post(endpoint(stored.base),"/api/bridge/events",{events},stored.token);}catch(_){}
    }
  }
  return {remember,sync,reset,status,tag,removed};
})();
