"use strict";
// Observes data and connects the bounded read-only market scanner to the
// existing socket. Login, cookies and outgoing auth frames are never copied.
(() => {
  if(window.__tredObserverVersion==="0.4.1")return;
  window.__tredObserverVersion="0.4.1";
  const Native = window.WebSocket;
  const contexts=new WeakMap();
  const number = v => typeof v === "number" && Number.isFinite(v);
  const safeSymbol = v => typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_./ #-]{1,31}$/.test(v);
  const ts = v => number(v) ? (v > 1e11 ? v / 1000 : v) : null;
  const emit = events => { for(let i=0;i<events.length;i+=250) window.postMessage({source:"tred-observer-v1",observer_version:"0.4.1",events:events.slice(i,i+250)},location.origin); };
  const accountId = value => (typeof value === "string" || typeof value === "number" && Number.isSafeInteger(value)) && /^\d{1,20}$/.test(String(value)) && !/^0+$/.test(String(value)) ? String(value) : null;
  const idType = value => value===undefined?"none":value===null||value===""?"empty":value===0||typeof value==="string"&&/^0+$/.test(value)?"zero":accountId(value)?typeof value==="number"?"integer":"digits":Array.isArray(value)?"array":["number","string","boolean","object"].includes(typeof value)?typeof value==="string"?"text":typeof value:"other";
  const identity = async (name,data) => {
    // UID is authoritative. Secondary account/resource IDs are not required to
    // equal it. A generic balance/order `id` is never an account identifier.
    const fields = name === "successauth" ? ["uid","accountId","id"] : ["uid","accountId"];
    const shape=Object.fromEntries(fields.map(key=>[key,idType(data[key])]));
    const present=fields.some(key=>!["none","empty","zero"].includes(shape[key]));
    const primary=accountId(data.uid);
    if(primary)return {uid:primary,field:"uid",status:"present",present:true,shape,fingerprint:"uid:"+primary};
    const secondary=fields.filter(key=>key!=="uid").map(key=>({field:key,value:accountId(data[key])})).filter(row=>row.value);
    const opaque=fields.some(key=>!["none","empty","zero","integer","digits"].includes(shape[key]));
    if(!opaque&&new Set(secondary.map(row=>row.value)).size===1){
      const chosen=secondary[0];return {uid:chosen.value,field:chosen.field,status:"present",present:true,shape,fingerprint:chosen.field+":"+chosen.value};
    }
    // Raw opaque values never leave this socket. A domain-separated digest of
    // an account identifier may be used ONLY inside the extension to reconnect
    // a user-confirmed profile. It is stripped before the server API.
    const opaqueUid=!["none","empty","zero"].includes(shape.uid);
    const anchorFields=opaqueUid?["uid"]:fields;
    const parts=anchorFields.map(key=>[key,shape[key],["string","number","boolean"].includes(typeof data[key])?data[key]:null]);
    const complex=anchorFields.some(key=>["object","array"].includes(shape[key]));
    const digest=complex?null:await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify(parts)));
    const fingerprint=complex?crypto.randomUUID():Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,"0")).join("");
    const stableUid=typeof data.uid==="string"&&/^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$/.test(data.uid)&&!/^0+$/.test(data.uid);
    const stableAliases=!opaqueUid&&!opaque&&secondary.length>1;
    const sourceAccount=stableUid||stableAliases?"po1:"+Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",new TextEncoder().encode("TRED-account-v1:"+fingerprint))),byte=>byte.toString(16).padStart(2,"0")).join(""):null;
    return {uid:null,field:opaqueUid?"opaque-uid":secondary.length>1?"ambiguous-account":null,status:secondary.length>1?"ambiguous":opaque?"unsupported":"missing",present,shape,fingerprint,sourceAccount};
  };
  const currency = value => value == null ? null : typeof value === "string" && /^[A-Za-z]{3}$/.test(value) ? value.toUpperCase() : "unsupported";
  const historyNames=["loadHistoryPeriod","loadHistoryPeriodFast","updateHistory","updateHistoryNew","updateHistoryNewFast","candles","history"];
  const requestIndex=value=>typeof value==="string"&&/^\d{1,15}$/.test(value)?Number(value):value;
  function historyArrays(data){
    const arrays=[data.candles,data.data,data.history].filter(Array.isArray);
    const times=data.timestamps??data.t;
    if(Array.isArray(times)&&times.length<=60000&&[data.o,data.h,data.l,data.c].every(a=>Array.isArray(a)&&a.length===times.length)){
      arrays.push(times.map((time,i)=>({time,open:data.o[i],high:data.h[i],low:data.l[i],close:data.c[i]})));
    }
    return arrays;
  }
  const isTickArray=r=>Array.isArray(r)&&(r.length===2||r.length===3&&r[2]===null);
  function marketPayload(data,state){
    if(!data||typeof data!=="object"||Array.isArray(data))return null;
    const request=state.requests.get(requestIndex(data.index));
    // An omitted asset/period can only come from an exact outgoing request
    // index; the active chart or arrival order never supplies missing identity.
    const asset=data.asset===undefined||data.asset===""?request?.asset:data.asset;
    if(!safeSymbol(asset)||request&&request.asset!==asset)return null;
    return {...data,asset};
  }
  function historyPeriod(data,state){
    if(Number.isInteger(data.period))return data.period;
    const index=requestIndex(data.index);
    const request=state.requests.get(index);
    if(request?.asset===data.asset)return request.period;
    // Unindexed Fast OHLC can prove its period through at least three
    // contiguous timestamps. A single bar, ticks, gaps or mixed spacing cannot.
    const periods=new Set();
    for(const rows of historyArrays(data)){
      if(!rows.length)continue;
      const tail=rows.slice(-1600);
      const bars=tail.map(row=>Array.isArray(row)&&row.length>=5?{time:ts(row[0]),open:row[1],close:row[2],high:row[3],low:row[4]}:
        row&&!Array.isArray(row)?{time:ts(row.time??row.timestamp),open:row.open,close:row.close,high:row.high,low:row.low}:null);
      if(bars.some(b=>!b||!Object.values(b).every(number)))continue;
      if(bars.length<3)return null;
      if(bars.some(b=>b.low<=0||b.low>Math.min(b.open,b.close)||b.high<Math.max(b.open,b.close)))return null;
      const times=bars.map(b=>b.time).sort((a,b)=>a-b),period=times[1]-times[0];
      if(![60,120,300,600,900].includes(period)||times.some((t,i)=>i&&t-times[i-1]!==period))return null;
      periods.add(period);
    }
    return periods.size===1?[...periods][0]:null;
  }
  function tickBars(rows){
    const ticks=rows.slice(-60000).map(r=>isTickArray(r)?{time:ts(r[0]),price:r[1]}:r&&number(r.price)?{time:ts(r.time??r.timestamp),price:r.price}:null).filter(r=>r&&number(r.time)&&number(r.price)&&r.price>0).sort((a,b)=>a.time-b.time);
    if(!ticks.length)return [];
    const groups=new Map();
    for(const t of ticks){const start=Math.floor(t.time/60)*60;let b=groups.get(start);
      if(!b){b={time:start,open:t.price,high:t.price,low:t.price,close:t.price,first:t.time,last:t.time,gap:0,count:1};groups.set(start,b);}
      else if(t.time>b.last){b.high=Math.max(b.high,t.price);b.low=Math.min(b.low,t.price);b.close=t.price;b.gap=Math.max(b.gap,t.time-b.last);b.last=t.time;b.count++;}
    }
    const end=ticks.at(-1).time;
    return [...groups.values()].filter(b=>b.time+60<=end&&b.first<=b.time+5&&b.last>=b.time+50&&b.gap<=10&&b.count>=2).slice(-1600).map(({time,open,high,low,close})=>({period:60,time,open,high,low,close,origin:"ticks"}));
  }
  async function normalize(name, data, state) {
    const now = Date.now()/1000;
    const events = [];
    if (data && !Array.isArray(data) && (name === "successauth" || name === "successupdateBalance" || name === "balance")) {
      // Identity/currency belong to this socket, never another account's socket.
      // A missing mode or currency is forwarded as missing, never guessed.
      const incoming = await identity(name,data);
      // Empty UID placeholders do not erase an ID established on this socket.
      // Secondary IDs also cannot replace a known primary UID on balance data.
      const keepPrimary=name!=="successauth"&&["uid","opaque-uid","ambiguous-account"].includes(state.idField)&&!accountId(data.uid)&&["none","empty","zero"].includes(incoming.shape.uid);
      if (name === "successauth" || incoming.present&&!keepPrimary) {
        if (name === "successauth" || incoming.fingerprint !== state.idFingerprint) {
          state.stream=crypto.randomUUID();state.currencies={};state.account=null;state.mode=null;
        }
        state.uid=incoming.uid;state.idStatus=incoming.status;state.idField=incoming.field;state.idFingerprint=incoming.fingerprint;state.idShape=incoming.shape;state.sourceAccount=incoming.sourceAccount??null;
      }
      if(Object.hasOwn(data,"isDemo"))state.mode=[0,1,false,true].includes(data.isDemo)?data.isDemo===1||data.isDemo===true?"demo":"real":null;
      const mode=state.mode??null;
      if (mode && Object.hasOwn(data,"currency")) state.currencies[mode] = currency(data.currency);
      const unit = Object.hasOwn(data,"currency") ? currency(data.currency) : mode ? state.currencies[mode] ?? null : null;
      state.account = state.uid && mode ? {account_id:state.uid,mode,currency:unit} : null;
      if (number(data.balance) || typeof data.balance === "string" && /^\d{1,8}(\.\d{1,2})?$/.test(data.balance)) {
        events.push({type:"account",account_id:state.uid,mode,currency:unit,balance:String(data.balance),time:now,source_stream:state.stream,id_status:state.idStatus,id_shape:state.idShape,source_account:state.sourceAccount??null});
      }
    }
    if (Array.isArray(data) && (name === "updateAssets" || name === "assets")) {
      for (const row of data) {
        if (!Array.isArray(row) || row.length < 16 || !safeSymbol(row[1]) || typeof row[3]!=="string" || !row[3] || !number(row[5]) || row[5] < 0 || row[5] > 100 || ![0,1,false,true].includes(row[9]) || ![0,1,false,true].includes(row[14])) continue;
        const expiries = Array.isArray(row[15]) ? row[15].map(x=>typeof x?.time==="string"&&/^\d+$/.test(x.time)?Number(x.time):x?.time).filter(x=>Number.isInteger(x)&&x>0&&x%60===0).map(x=>x/60) : [];
        state.catalog.set(row[1],{is_open:!!row[14]});
        events.push({type:"asset",symbol:row[1],market:row[9] ? "otc" : "normal",is_open:!!row[14],payout:row[5]/100,expiries,time:now,feed_id:state.feed,history_scoped:true});
      }
    }
    if (name === "updateStream" && Array.isArray(data)) {
      for (const row of data) if (Array.isArray(row) && safeSymbol(row[0]) && number(row[1]) && number(row[2]) && row[2] > 0) events.push({type:"quote",symbol:row[0],time:ts(row[1]),price:row[2],feed_id:state.feed,source_clock:"po-market-native-v1"});
    }
    if(historyNames.includes(name))data=marketPayload(data,state);
    if (historyNames.includes(name) && data) {
      const period=historyPeriod(data,state);
      const arrays=historyArrays(data);
      const before=events.length;
      let rowCount=0,ohlcRows=0;
      for(const rows of arrays){
        rowCount+=rows.length;
        if([60,120,300,600,900].includes(period))for(const row of rows.slice(-1600)){
          const c=Array.isArray(row)&&row.length>=5?{time:ts(row[0]),open:row[1],close:row[2],high:row[3],low:row[4]}:row&&!Array.isArray(row)?{time:ts(row.time??row.timestamp),open:row.open,close:row.close,high:row.high,low:row.low}:{};
          if(Object.keys(c).length===5&&Object.values(c).every(number)){events.push({type:"candle",symbol:data.asset,period,...c,feed_id:state.feed,source_clock:"po-market-native-v1"});ohlcRows++;}
        }
        for(const bar of tickBars(rows))events.push({type:"candle",symbol:data.asset,...bar,feed_id:state.feed,source_clock:"po-market-native-v1"});
      }
      const accepted=events.length-before;
      // A tick carries an actual observed price/time. An OHLC close is never
      // passed off as a live quote merely because history just arrived.
      const ticks=arrays.flatMap(rows=>rows.slice(-60000).map(r=>isTickArray(r)?{time:ts(r[0]),price:r[1]}:r&&number(r.price)?{time:ts(r.time??r.timestamp),price:r.price}:null)).filter(r=>r&&number(r.time)&&number(r.price)&&r.price>0);
      if(ticks.length){const last=ticks.reduce((a,b)=>a.time>b.time?a:b);events.push({type:"quote",symbol:data.asset,...last,feed_id:state.feed,source_clock:"po-market-native-v1"});}
      window.__tredMarketScanner?.received(state,data,period,name);
      if(period!==1)events.push({type:"history_status",symbol:data.asset,event:name,period:Number.isInteger(period)&&period>0&&period<=86400?period:0,rows:Math.min(rowCount,60000),bars:Math.min(accepted,10000),code:accepted?ohlcRows?"ohlc":"ticks":!rowCount?"empty":period===null?"period_missing":![60,120,300,600,900].includes(period)?"unsupported_period":"format_unrecognized",time:now});
    }

    // This strict variant accepts only explicit complete trade records. Unknown
    // broker variants remain unsupported until compared with the actual terminal.
    const account = state.account;
    if (["successopenOrder","successcloseOrder","updateOpenedDeals","updateClosedDeals"].includes(name) && account) {
      const rows = Array.isArray(data) ? data : data?.deals || [data];
      for (const r of rows) {
        if (!r || !safeSymbol(r.asset) || !/^[-\w]{1,80}$/.test(String(r.id)) || !["call","put"].includes(r.action) || !number(r.amount) || !number(r.openTime) || !number(r.closeTime) || ![0,1].includes(r.isDemo) || (r.isDemo === 1 ? "demo" : "real") !== account.mode) continue;
        const closed = name === "successcloseOrder" || name === "updateClosedDeals";
        if (closed && (!number(r.profit) || !number(r.closedAt))) continue;
        events.push({type:"trade",mode:account.mode,account_id:account.account_id,id:String(r.id),symbol:r.asset,direction:r.action === "call" ? "up" : "down",stake:String(r.amount),opened_at:ts(r.openTime),expires_at:ts(r.closeTime),status:closed ? r.profit > 0 ? "win" : r.profit < 0 ? "loss" : "draw" : "open",...(closed ? {closed_at:ts(r.closedAt),pnl:String(r.profit)} : {}),time:now});
      }
    }
    for(const event of events)if(event.type==="quote")window.__tredMarketScanner?.quoted(state,event.symbol,event.time);
    emit(events);
    // A native message can progress read-only collection even when Opera
    // delays the hidden page's timeout. Scanner pacing/concurrency still apply.
    window.__tredMarketScanner?.wake?.();
  }
  class ObservedSocket extends Native {
    send(frame){
      // Read only these non-auth request shapes to recover a missing response
      // period by its matching index. All outgoing frames pass through unchanged.
      const state=contexts.get(this);
      if(state&&typeof frame==="string"&&frame.length<4096&&/^42\["loadHistoryPeriod",/.test(frame)){
        try{const [,data]=JSON.parse(frame.slice(2));if(data&&safeSymbol(data.asset)&&[60,120,300,600,900].includes(data.period)&&Number.isSafeInteger(data.index)){state.requests.set(data.index,{asset:data.asset,period:data.period});while(state.requests.size>40)state.requests.delete(state.requests.keys().next().value);}}catch(_){}
      }
      return super.send(frame);
    }
    constructor(...args) {
      super(...args);
      let host;
      try { host = new URL(args[0]).hostname; } catch (_) { return; }
      if (!(host === "po.market" || host.endsWith(".po.market"))) return;
      const state = {uid:null,idStatus:"missing",idField:null,idFingerprint:null,idShape:{uid:"none",accountId:"none"},currencies:{},account:null,stream:crypto.randomUUID()};
      state.feed=crypto.randomUUID();state.requests=new Map();state.catalog=new Map();contexts.set(this,state);
      window.__tredMarketScanner?.attach(this,state,frame=>Native.prototype.send.call(this,frame));
      let pending = null;
      let queue = Promise.resolve();
      // Keep close after messages already being decoded (including Blobs).
      this.addEventListener("close",()=>{window.__tredMarketScanner?.close(state.feed);queue=queue.then(()=>emit([{type:"feed",feed_id:state.feed,connected:false,time:Date.now()/1000}]));return queue;});
      this.addEventListener("message", event => {
        queue = queue.then(async () => {
          let frame = event.data;
          if (frame instanceof Blob) frame = await frame.text();
          else if (frame instanceof ArrayBuffer) frame = new TextDecoder().decode(frame);
          if (typeof frame !== "string" || frame.length > 4000000) return;
          // Engine.IO control frames can arrive between an attachment header
          // and its binary payload; they must not consume that pending event.
          if(["2","3","40","41"].includes(frame))return;
          try {
            if (pending&&!frame.startsWith("42")&&!/^45[1-4]-/.test(frame)) {
              pending.parts.push(JSON.parse(frame));
              if(pending.parts.length<pending.count)return;
              const message=pending;pending=null;
              const restore=(value,depth=0)=>{
                if(depth>12)throw Error('nested attachment');
                if(value&&typeof value==="object"){
                  if(value._placeholder===true){if(!Number.isInteger(value.num)||value.num<0||value.num>=message.parts.length)throw Error('attachment index');return message.parts[value.num];}
                  if(Array.isArray(value))return value.map(v=>restore(v,depth+1));
                  return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,restore(v,depth+1)]));
                }
                return value;
              };
              const parsed=restore(message.payload);
              await normalize(parsed[0],parsed[1],state);
            } else if (/^45[1-4]-/.test(frame)) {
              const parsed = JSON.parse(frame.slice(4));
              const allowed = ["successauth","successupdateBalance","balance","updateAssets","assets","updateStream",...historyNames,"successopenOrder","successcloseOrder","updateOpenedDeals","updateClosedDeals"];
              if(Array.isArray(parsed))pending={payload:allowed.includes(parsed[0])?parsed:["unsupported",{_placeholder:true,num:0}],count:Number(frame[2]),parts:[]};
            } else if (frame.startsWith("42")) {
              const parsed = JSON.parse(frame.slice(2));
              await normalize(parsed[0],parsed[1],state);
            } else if (frame.startsWith("{")) {
              const parsed = JSON.parse(frame);
              if (parsed && Object.hasOwn(parsed,"balance")) await normalize("balance",parsed,state);
              else if(parsed&&historyArrays(parsed).length&&(safeSymbol(parsed.asset)||state.requests.has(requestIndex(parsed.index))))await normalize("history",parsed,state);
            } else if (frame.startsWith("[")) {
              const parsed = JSON.parse(frame);
              if (Array.isArray(parsed) && safeSymbol(parsed[0]) && number(parsed[1]) && number(parsed[2])) await normalize("updateStream",[parsed],state);
              else if (Array.isArray(parsed) && Array.isArray(parsed[0])) {
                const first=parsed[0];
                await normalize(safeSymbol(first[0])&&number(first[1])&&number(first[2])?"updateStream":"assets",parsed,state);
              }
            }
          } catch (_) { pending = null; }
        }).catch(() => { pending = null; });
        return queue;
      });
    }
  }
  Object.defineProperty(window,"WebSocket",{value:ObservedSocket,writable:true,configurable:true});
})();
