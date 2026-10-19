"use strict";
// Observes INCOMING messages only. No send() call, login, cookie access or auth export.
(() => {
  const Native = window.WebSocket;
  const number = v => typeof v === "number" && Number.isFinite(v);
  const safeSymbol = v => typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_./ #-]{1,31}$/.test(v);
  const ts = v => number(v) ? (v > 1e11 ? v / 1000 : v) : null;
  const emit = events => { if (events.length) window.postMessage({source:"tred-observer-v1", events:events.slice(0,250)}, location.origin); };
  let account = null;
  let supported = 0;
  function normalize(name, data) {
    const now = Date.now()/1000;
    const events = [];
    if (data && !Array.isArray(data) && (name === "successauth" || name === "successupdateBalance" || name === "balance")) {
      // Never assume demo/real or USD when the message does not specify it.
      if (/^\d{1,20}$/.test(String(data.uid)) && [0,1].includes(data.isDemo) && data.currency === "USD") {
        account = {account_id:String(data.uid), mode:data.isDemo === 1 ? "demo" : "real", currency:"USD"};
      }
      if (account && number(data.balance) && [0,1].includes(data.isDemo) && (data.isDemo === 1 ? "demo" : "real") === account.mode) {
        events.push({type:"account", ...account, balance:String(data.balance), time:now});
      }
    }
    if (Array.isArray(data) && (name === "updateAssets" || name === "assets")) {
      for (const row of data) {
        if (!Array.isArray(row) || row.length < 16 || !safeSymbol(row[1]) || row[3] !== "currency" || !number(row[5]) || row[5] <= 0 || row[5] > 100 || ![0,1,false,true].includes(row[9]) || ![0,1,false,true].includes(row[14])) continue;
        const expiries = Array.isArray(row[15]) ? row[15].filter(x => Number.isInteger(x?.time) && x.time > 0 && x.time % 60 === 0).map(x => x.time/60) : [];
        events.push({type:"asset",symbol:row[1],market:row[9] ? "otc" : "normal",is_open:!!row[14],payout:row[5]/100,expiries,time:now});
      }
    }
    if (name === "updateStream" && Array.isArray(data)) {
      for (const row of data) if (Array.isArray(row) && safeSymbol(row[0]) && number(row[1]) && number(row[2]) && row[2] > 0) events.push({type:"quote",symbol:row[0],time:ts(row[1]),price:row[2]});
    }
    if (["loadHistoryPeriod","updateHistoryNew","candles"].includes(name) && data && safeSymbol(data.asset) && [60,300,900].includes(data.period)) {
      const rows = data.candles || data.history || data.data;
      if (Array.isArray(rows)) for (const row of rows.slice(-200)) {
        const c = Array.isArray(row) ? {time:ts(row[0]),open:row[1],close:row[2],high:row[3],low:row[4]} : {time:ts(row.time),open:row.open,close:row.close,high:row.high,low:row.low};
        if (Object.values(c).every(number) && c.time + data.period <= now) events.push({type:"candle",symbol:data.asset,period:data.period,...c});
      }
    }
    // This strict variant accepts only explicit complete trade records. Unknown
    // broker variants remain unsupported until compared with the actual terminal.
    if (["successopenOrder","successcloseOrder","updateOpenedDeals","updateClosedDeals"].includes(name) && account) {
      const rows = Array.isArray(data) ? data : data?.deals || [data];
      for (const r of rows) {
        if (!r || !safeSymbol(r.asset) || !/^[-\w]{1,80}$/.test(String(r.id)) || !["call","put"].includes(r.action) || !number(r.amount) || !number(r.openTime) || !number(r.closeTime) || ![0,1].includes(r.isDemo) || (r.isDemo === 1 ? "demo" : "real") !== account.mode) continue;
        const closed = name === "successcloseOrder" || name === "updateClosedDeals";
        if (closed && (!number(r.profit) || !number(r.closedAt))) continue;
        events.push({type:"trade",mode:account.mode,account_id:account.account_id,id:String(r.id),symbol:r.asset,direction:r.action === "call" ? "up" : "down",stake:String(r.amount),opened_at:ts(r.openTime),expires_at:ts(r.closeTime),status:closed ? r.profit > 0 ? "win" : r.profit < 0 ? "loss" : "draw" : "open",...(closed ? {closed_at:ts(r.closedAt),pnl:String(r.profit)} : {}),time:now});
      }
    }
    if (events.length) supported += events.length;
    emit(events);
  }
  class ObservedSocket extends Native {
    constructor(...args) {
      super(...args);
      let host;
      try { host = new URL(args[0]).hostname; } catch (_) { return; }
      if (!(host === "po.market" || host.endsWith(".po.market"))) return;
      let pending = null;
      let queue = Promise.resolve();
      this.addEventListener("message", event => {
        queue = queue.then(async () => {
          let frame = event.data;
          if (frame instanceof Blob) frame = await frame.text();
          else if (frame instanceof ArrayBuffer) frame = new TextDecoder().decode(frame);
          if (typeof frame !== "string" || frame.length > 4000000) return;
          try {
            if (pending) {
              const binary = JSON.parse(frame);
              const name = pending; pending = null;
              normalize(name,binary);
            } else if (frame.startsWith("451-")) {
              const parsed = JSON.parse(frame.slice(4));
              const allowed = ["successauth","successupdateBalance","updateAssets","assets","updateStream","loadHistoryPeriod","updateHistoryNew","candles","successopenOrder","successcloseOrder","updateOpenedDeals","updateClosedDeals"];
              pending = allowed.includes(parsed[0]) ? parsed[0] : "unsupported";
            } else if (frame.startsWith("42")) {
              const parsed = JSON.parse(frame.slice(2));
              normalize(parsed[0],parsed[1]);
            } else if (frame.startsWith("[")) {
              const parsed = JSON.parse(frame);
              if (Array.isArray(parsed) && Array.isArray(parsed[0])) normalize("assets",parsed);
            }
          } catch (_) { pending = null; }
        }).catch(() => { pending = null; });
      });
    }
  }
  Object.defineProperty(window,"WebSocket",{value:ObservedSocket,writable:true,configurable:true});
})();
