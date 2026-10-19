"use strict";
// The pairing token stays in the worker. Only bounded market-data plans enter
// the page; it receives no API key, broker credential, token or arbitrary code.
globalThis.TredScan=(()=>{
  const feeds=new Map();let running=false,lastPoll=0,code="no_socket";
  const validFeed=v=>typeof v==="string"&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(v);
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
      const stored=await chrome.storage.local.get(["base","token","needsPairing","marketClock"]);
      if(!stored.base||!stored.token||stored.needsPairing)return;
      const base=endpoint(stored.base),token=stored.token;
      const value=await post(base,"/api/bridge/scan",{code},token);
      if(!value.ok||!value.plan||value.plan.version!==1||!Array.isArray(value.plan.pairs)||value.plan.pairs.length>2000)throw Error("scan response");
      const tabs=await chrome.tabs.query({url:["https://pocketoption.com/*","https://*.pocketoption.com/*"]});
      const allowed=new Set(tabs.filter(t=>Number.isInteger(t.id)&&/^https:\/\/([a-z0-9-]+\.)*pocketoption\.com\/(?:[a-z]{2}\/)?cabinet\//i.test(t.url||"")).map(t=>t.id));
      const grouped=new Map();
      for(const pair of value.plan.pairs){const tab=feeds.get(pair.feed_id);if(!allowed.has(tab))continue;if(!grouped.has(tab))grouped.set(tab,[]);grouped.get(tab).push(pair);}
      code=allowed.size?"no_socket":"no_tab";let accepted=0;
      for(const [tabId,pairs] of grouped){
        const result=await chrome.scripting.executeScript({target:{tabId},world:"MAIN",func:plan=>window.__tredMarketScanner?.plan(plan)||{code:"unsupported_module"},args:[{...value.plan,pairs,offset:stored.marketClock==="po-utc2"?7200:0}]});
        const status=result?.[0]?.result;
        if(status?.code==="ready"){code="ready";accepted+=status.pairs||0;}
        else if(code!=="ready"&&["no_socket","waiting_catalog","request_failed","unsupported_module"].includes(status?.code))code=status.code;
      }
      await chrome.storage.local.set({scannerStatus:{code,pairs:accepted,updated_at:Date.now()}});
    }catch(_){code="request_failed";await chrome.storage.local.set({scannerStatus:{code,pairs:0,updated_at:Date.now()}});}
    finally{running=false;}
  }
  return {remember,sync,reset};
})();
