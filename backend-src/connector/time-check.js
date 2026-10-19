"use strict";
// Freshness is always checked in UTC. Source decoding is explicit, never fitted
// to arrival time. Only the opted-in Pocket Option market clock can shift.
globalThis.TredTime = (() => {
  const kinds=["account","asset","quote","candle","trade"];
  const labels={account:"Баланс",asset:"Каталог пар",quote:"Котировка",candle:"Свеча",trade:"Сделка",unknown:"Неизвестное событие"};
  const known=value=>kinds.includes(value)?value:"unknown";
  const profile=value=>value==="po-utc2"?"po-utc2":"utc";
  function decode(event,selected){
    if(!["quote","candle"].includes(event.type))return {event};
    if(event.source_clock!==undefined&&event.source_clock!=="po-market-native-v1")return {error:"Неподдерживаемый источник времени. Событие не передано."};
    const offset=profile(selected)==="po-utc2"?7200:0;
    if(offset&&event.source_clock!=="po-market-native-v1")return {error:"Обнови вкладку Pocket Option: она ещё использует старый обработчик времени. Событие не передано."};
    const {source_clock,...canonical}=event;
    // Do not turn null, strings or millisecond values into plausible seconds.
    if(typeof event.time==="number"&&Number.isFinite(event.time)&&event.time>=1e9&&event.time<1e11)canonical.time=event.time-offset;
    return {event:canonical,source:{profile:profile(selected),raw_time:event.time,utc_time:canonical.time,offset_seconds:offset}};
  }
  function problem(event,now) {
    const kind=known(event?.type),history=kind==="trade"||kind==="candle";
    const fields=[["time",history]];
    if(kind==="trade"){
      fields.push(["opened_at",true]);
      if(event.status!=="open")fields.push(["closed_at",true]);
    }
    for(const [field,historical] of fields){
      const value=event?.[field];let reason;
      if(typeof value!=="number"||!Number.isFinite(value))reason="метка должна быть числом";
      else if(value<1e9||value>=1e11)reason="неподдерживаемый формат Unix-времени в секундах";
      else if(value>now+5)reason="опережает часы браузера примерно на "+Math.ceil(value-now)+" с";
      else if(!historical&&now-value>60)reason="устарела примерно на "+Math.floor(now-value)+" с";
      if(reason)return {kind,field,message:labels[kind]+" · "+field+": "+reason+". Событие не передано; время не исправляется автоматически."};
    }
    return null;
  }
  function rejectedByServer(kind,events,now){
    const offsets=events.flatMap(e=>[e?.time,e?.opened_at,e?.closed_at]).filter(x=>typeof x==="number"&&Number.isFinite(x)&&x>=1e9&&x<1e11).map(x=>Math.round(x-now));
    const range=offsets.length?" Разница меток с часами браузера: от "+Math.min(...offsets)+" до "+Math.max(...offsets)+" с.":"";
    return labels[known(kind)]+": сервер отклонил время данных."+range+" Требуется проверить время сервера и формат источника.";
  }
  return {kinds,labels,known,profile,decode,problem,rejectedByServer};
})();
