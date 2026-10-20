"use strict";
// Only read-only market requests on an already-open native Pocket socket.
// No login, account mutation, chart switch, order or credential API exists here.
(()=>{
  const VERSION="0.3.12";
  if(window.__tredMarketScanner?.version===VERSION)return;
  window.__tredMarketScanner?.stop();
  const sockets=new Map(),attempts=new Map(),pending=new Map();
  const pair=v=>typeof v==="string"&&/^[A-Za-z0-9][A-Za-z0-9_./ #-]{1,31}$/.test(v);
  const feed=v=>typeof v==="string"&&/^[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/.test(v);
  let wanted=[],until=0,offset=0,timer=null,index=Math.floor(Date.now()/10),stopped=false;
  const key=t=>t.feed_id+":"+t.symbol+":"+t.period;
  function schedule(){if(!stopped&&!timer&&wanted.length&&Date.now()/1000<until)timer=setTimeout(()=>{timer=null;tick();},400);}
  function tick(){
    if(stopped||Date.now()/1000>=until)return;
    const now=Date.now()/1000;
    for(const [id,t] of pending)if(now-t.at>8)pending.delete(id);
    // Unvisited work wins over retries, so one unsupported instrument cannot
    // keep the rest of a large catalog waiting forever.
    for(const task of [...wanted].sort((a,b)=>(attempts.get(key(a))||0)-(attempts.get(key(b))||0))){
      const stream=sockets.get(task.feed_id);
      if(!stream||stream.socket.readyState!==1||!stream.state.catalog.get(task.symbol)?.is_open)continue;
      const taskKey=key(task),last=attempts.get(taskKey)||0;
      if(task.period<=1&&now-(stream.quotes.get(task.symbol)||0)<=8)continue;
      if(now-last<12||[...pending.values()].some(p=>p.key===taskKey))continue;
      if(task.period!==0&&pending.size>=2)continue;
      try{
        if(task.period===0){
          stream.send('42'+JSON.stringify(["subfor",task.symbol]));
        }else{
          index=Math.max(index+1,Math.floor(Date.now()/10));
          const request={asset:task.symbol,index,offset:task.period*task.bars,period:task.period,time:Math.floor((now+offset)/task.period)*task.period};
          stream.state.requests.set(index,{asset:task.symbol,period:task.period});
          while(stream.state.requests.size>80)stream.state.requests.delete(stream.state.requests.keys().next().value);
          pending.set(index,{key:taskKey,at:now,feed_id:task.feed_id,asset:task.symbol,period:task.period});
          stream.send('42'+JSON.stringify(["loadHistoryPeriod",request]));
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
    const tasks=[];
    for(const row of value.pairs){
      if(!row||!pair(row.symbol)||!feed(row.feed_id)||!Array.isArray(row.history)||row.history.length>2)return {code:"request_failed"};
      if(row.quote!==undefined&&typeof row.quote!=="boolean")return {code:"request_failed"};
      tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:0});
      // A short period-1 history contains real timestamped ticks. It does not
      // change the visible chart, and is never interpreted as OHLC research.
      if(row.quote!==false)tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:1,bars:20});
      for(const h of row.history){
        if(!h||![60,120,300,600,900].includes(h.period)||![5,160].includes(h.bars))return {code:"request_failed"};
        tasks.push({symbol:row.symbol,feed_id:row.feed_id,period:h.period,bars:h.bars});
      }
    }
    wanted=tasks;until=value.until;offset=value.offset;stopped=false;schedule();
    const available=value.pairs.filter(r=>sockets.get(r.feed_id)?.socket.readyState===1&&sockets.get(r.feed_id).state.catalog.has(r.symbol)).length;
    return {code:available?"ready":sockets.size?"waiting_catalog":"no_socket",pairs:available};
  }
  function attach(socket,state,send){sockets.set(state.feed,{socket,state,send,quotes:new Map()});}
  function quoted(state,symbol,time){
    const at=time-offset,now=Date.now()/1000,stream=sockets.get(state.feed);
    if(stream&&Number.isFinite(at)&&at>=now-10&&at<=now+5)stream.quotes.set(symbol,at);
  }
  function received(state,data,period){
    if(Number.isSafeInteger(data.index)){const p=pending.get(data.index);if(p?.feed_id===state.feed&&p.asset===data.asset&&p.period===period)pending.delete(data.index);}
  }
  function close(id){sockets.delete(id);for(const [index,p] of pending)if(p.feed_id===id)pending.delete(index);}
  function stop(){stopped=true;clearTimeout(timer);timer=null;wanted=[];}
  window.__tredMarketScanner={version:VERSION,plan,attach,received,quoted,close,stop};
})();
