"use strict";
// Native chart history is requested only in the worker-owned collector tab.
// The foreground terminal keeps the passive/advanced-history path. No orders,
// login frames, account changes or credential APIs are available here.
(()=>{
  const VERSION="0.4.3";
  if(window.__tredMarketScanner?.version===VERSION)return;
  window.__tredMarketScanner?.stop();
  const sockets=new Map(),attempts=new Map(),pending=new Map();
  const pair=v=>typeof v==="string"&&/^[A-Za-z0-9][A-Za-z0-9_./ #-]{1,31}$/.test(v);
  const feed=v=>typeof v==="string"&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(v);
  let wanted=[],until=0,offset=0,timer=null,index=Math.floor(Date.now()/10),stopped=false,turn=0,lastTick=0,dedicated=false;
  const key=t=>t.feed_id+":"+t.symbol+":"+t.period+":"+(t.focus?"focus":t.before??"latest");
  function schedule(){if(!stopped&&!timer&&wanted.length&&Date.now()/1000<until)timer=setTimeout(()=>{timer=null;tick();},400);}
  function tick(){
    if(stopped||Date.now()/1000>=until)return;
    const now=Date.now()/1000;
    // Broker messages can wake this queue when a hidden tab's timers are
    // throttled. The same global pacing applies to timer and message wakes.
    if(now-lastTick<.4-1e-6){schedule();return;}
    lastTick=now;
    for(const [id,t] of pending)if(now-t.at>(t.chart?12:8))pending.delete(id);
    // The native libraries initialize a chart, then subscribe to its stream.
    // Keep those two messages separate so the global pacing still applies.
    if(dedicated)for(const stream of sockets.values())if(stream.subscribe){
      const symbol=stream.subscribe;stream.subscribe=null;
      if(stream.socket.readyState===1&&stream.state.catalog.get(symbol)?.is_open){
        try{stream.send('42'+JSON.stringify(["subfor",symbol]));attempts.set(key({feed_id:stream.state.feed,symbol,period:0}),now);}catch(_){}
        schedule();return;
      }
    }
    // Unvisited work wins over retries, so one unsupported instrument cannot
    // keep the rest of a large catalog waiting forever.
    const sorted=[...wanted].sort((a,b)=>Number(!!a.background)-Number(!!b.background)||(b.priority&&b.period===1?1:0)-(a.priority&&a.period===1?1:0)||(attempts.get(key(a))||0)-(attempts.get(key(b))||0));
    const quotes=sorted.filter(t=>t.period===1),other=sorted.filter(t=>t.period!==1);
    // Share the paced request budget between quotes and other work. Repeated
    // silent subscriptions or candle refreshes cannot consume every turn.
    const focused=quotes.some(t=>t.priority&&now-(sockets.get(t.feed_id)?.quotes.get(t.symbol)||0)>5&&![...pending.values()].some(p=>p.key===key(t))),slot=turn++;
    const quoteTurn=focused?slot%4!==3:slot%2===0;
    let tasks=quoteTurn?[...quotes,...other]:[...other,...quotes];
    if(dedicated){
      const focus=other.filter(t=>t.focus);
      tasks=[...focus,...tasks.filter(t=>!t.focus)];
    }
    if(dedicated&&!pending.size){
      const charts=other.filter(t=>!t.focus&&t.period>=60&&t.before===undefined);
      tasks=[...tasks.filter(t=>t.focus),...charts,...tasks.filter(t=>!t.focus&&!charts.includes(t))];
    }
    for(const task of tasks){
      const stream=sockets.get(task.feed_id);
      if(!stream||stream.socket.readyState!==1||!stream.state.catalog.get(task.symbol)?.is_open)continue;
      const taskKey=key(task),last=attempts.get(taskKey)||0;
      if(task.focus&&now-(stream.quotes.get(task.symbol)||0)<=5)continue;
      // Give the normal subscription its first chance before spending a
      // fallback tick request. A working stream needs no polling probe.
      if(task.period===1&&!attempts.has(key({...task,period:0})))continue;
      if((task.period===1||task.period===0&&last)&&now-(stream.quotes.get(task.symbol)||0)<=(task.priority?5:8))continue;
      if(now-last<(task.focus?4:task.period===0?30:task.priority&&task.period===1?4:12)||[...pending.values()].some(p=>p.key===taskKey))continue;
      // Replies without an index must never be confused with another request
      // for this same instrument. Other instruments still use both slots.
      if(task.period!==0&&[...pending.values()].some(p=>p.feed_id===task.feed_id&&p.asset===task.symbol))continue;
      if(task.period!==0&&pending.size>=2)continue;
      if(quotes.length&&task.period!==0&&[...pending.values()].some(p=>(p.period===1)===(task.period===1)))continue;
      const chart=dedicated&&task.period>=60&&task.before===undefined;
      if(chart&&[...pending.values()].some(p=>p.chart))continue;
      try{
        if(task.period===0){
          stream.send('42'+JSON.stringify(["subfor",task.symbol]));
        }else{
          index=Math.max(index+1,Math.floor(Date.now()/10));
          const end=task.before??now;
          const request={asset:task.symbol,index,offset:task.period*task.bars,period:task.period,time:Math.floor((end+offset)/task.period)*task.period};
          stream.state.requests.set(index,{asset:task.symbol,period:task.period});
          while(stream.state.requests.size>80)stream.state.requests.delete(stream.state.requests.keys().next().value);
          pending.set(index,{key:taskKey,at:now,feed_id:task.feed_id,asset:task.symbol,period:task.period,chart});
          if(chart){
            stream.send('42'+JSON.stringify(["changeSymbol",{asset:task.symbol,period:task.period}]));
            stream.subscribe=task.symbol;
          }else stream.send('42'+JSON.stringify(["loadHistoryPeriod",request]));
        }
        attempts.set(taskKey,now);
        while(attempts.size>8000)attempts.delete(attempts.keys().next().value);
      }catch(_){attempts.set(taskKey,now);}
      break; // One request per 400 ms, at most two unanswered histories.
    }
    schedule();
  }
  function plan(value){
    if(!value||value.version!==1||!Number.isFinite(value.until)||value.until<Date.now()/1000||value.until>Date.now()/1000+25||![0,7200].includes(value.offset)||!Array.isArray(value.pairs)||value.pairs.length>2000)return {code:"request_failed"};
    if(value.dedicated!==undefined&&typeof value.dedicated!=="boolean")return {code:"request_failed"};
    const tasks=[];
    for(const row of value.pairs){
      if(!row||!pair(row.symbol)||!feed(row.feed_id)||!Array.isArray(row.history)||row.history.length>2)return {code:"request_failed"};
      if(row.warm_history!==undefined&&(!Array.isArray(row.warm_history)||row.warm_history.length>3))return {code:"request_failed"};
      if(row.quote!==undefined&&typeof row.quote!=="boolean")return {code:"request_failed"};
      if(row.priority!==undefined&&typeof row.priority!=="boolean")return {code:"request_failed"};
      const priority=row.priority===true;
      if(value.dedicated===true&&priority&&row.chart_period!==undefined){
        if(![60,120,300,600,900].includes(row.chart_period))return {code:"request_failed"};
        tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:row.chart_period,bars:5,priority:true,focus:true});
      }
      tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:0,priority});
      // A short period-1 history contains real timestamped ticks. It does not
      // change the visible chart, and is never interpreted as OHLC research.
      if(row.quote!==false)tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:1,bars:20,priority});
      const history=row.history.map(h=>({...h,background:false})).concat((row.warm_history||[]).map(h=>({...h,background:true})));
      if(new Set(history.map(h=>h.period)).size!==history.length)return {code:"request_failed"};
      for(const h of history){
        if(!h||![60,120,300,600,900].includes(h.period)||![5,160].includes(h.bars))return {code:"request_failed"};
        if(h.before!==undefined&&(!Number.isSafeInteger(h.before)||h.bars!==160||h.before>Date.now()/1000||h.before<Date.now()/1000-1600*h.period||h.before%h.period>1))return {code:"request_failed"};
        tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:h.period,bars:h.bars,priority,background:h.background,...(h.before===undefined?{}:{before:h.before})});
      }
    }
    wanted=tasks;until=value.until;offset=value.offset;dedicated=value.dedicated===true;stopped=false;schedule();
    if(!tasks.length)for(const stream of sockets.values())stream.subscribe=null;
    const available=value.pairs.filter(r=>sockets.get(r.feed_id)?.socket.readyState===1&&sockets.get(r.feed_id).state.catalog.has(r.symbol)).length;
    if(dedicated&&available)tick();
    return {code:available?"ready":sockets.size?"waiting_catalog":"no_socket",pairs:available};
  }
  function attach(socket,state,send){sockets.set(state.feed,{socket,state,send,quotes:new Map()});}
  function quoted(state,symbol,time){
    const at=time-offset,now=Date.now()/1000,stream=sockets.get(state.feed);
    if(stream&&Number.isFinite(at)&&at>=now-10&&at<=now+5){
      stream.quotes.set(symbol,at);
      // A genuine fresh tick satisfies a quote probe, even if its response
      // carries neither the requested index nor a period.
      for(const [id,p] of pending)if(p.feed_id===state.feed&&p.asset===symbol&&p.period===1)pending.delete(id);
    }
  }
  function received(state,data,period,name){
    if(["updateHistory","updateHistoryNew","updateHistoryNewFast","history","candles"].includes(name)){
      const charts=[...pending].filter(([,p])=>p.chart&&p.feed_id===state.feed&&p.asset===data.asset&&p.period===period);
      if(charts.length===1){pending.delete(charts[0][0]);return;}
    }
    const id=typeof data.index==="string"&&/^\d{1,15}$/.test(data.index)?Number(data.index):data.index;
    if(Number.isSafeInteger(id)){
      const p=pending.get(id);
      if(p?.feed_id===state.feed&&p.asset===data.asset&&p.period===period)pending.delete(id);
      return;
    }
    if(data.index!==undefined&&data.index!==null)return;
    // Explicit or independently verified candle spacing identifies the
    // period; never infer it from a pending request or the active chart.
    const matches=[...pending].filter(([,p])=>p.feed_id===state.feed&&p.asset===data.asset&&p.period===period);
    if(matches.length===1)pending.delete(matches[0][0]);
  }
  function close(id){sockets.delete(id);for(const [index,p] of pending)if(p.feed_id===id)pending.delete(index);}
  // A sleeping/restarted worker loses its RAM map. Attest only IDs from its
  // existing server plan, against actual live sockets with their own catalog.
  // No catalog/timestamp is replayed, and no broker request is generated here.
  function connections(ids){
    if(!Array.isArray(ids)||ids.length>128||!ids.every(feed))return [];
    return [...new Set(ids)].filter(id=>{const s=sockets.get(id);return s?.socket.readyState===1&&s.state.catalog.size>0;});
  }
  function stop(){stopped=true;clearTimeout(timer);timer=null;wanted=[];}
  window.__tredMarketScanner={version:VERSION,plan,attach,received,quoted,close,connections,wake:tick,stop};
})();
