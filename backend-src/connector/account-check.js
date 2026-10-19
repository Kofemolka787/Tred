"use strict";
// Never infer an account ID, demo/real mode or USD from prices or the URL.
globalThis.TredAccount = (() => {
  const id = value => typeof value === "string" && /^\d{1,20}$/.test(value);
  const mode = value => ["real","demo"].includes(value);
  const key = candidate => candidate.account_id+":"+candidate.mode;
  const confirmations = values => Array.isArray(values) ? values.filter(x=>typeof x==="string"&&/^\d{1,20}:(real|demo)$/.test(x)).slice(-20) : [];
  const stream = value => typeof value==="string"&&/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
  const streamKey = event => event.source_stream+":"+event.mode;
  const identities = values => Array.isArray(values)?values.filter(x=>x&&stream(x.source_stream)&&mode(x.mode)&&id(x.account_id)).slice(-20):[];
  function basic(event){
    if(!mode(event.mode))return "Баланс получен, но брокер не указал демо или реальный режим. Счёт не передан.";
    if(typeof event.balance!=="string"||!/^\d{1,8}(\.\d{1,2})?$/.test(event.balance)||Number(event.balance)>10000000)return "Баланс имеет неподдерживаемый формат. Сумма не округляется автоматически.";
    if(event.currency!=null&&event.currency!=="USD")return "Брокер указал другую или неподдерживаемую валюту. TRED пока поддерживает только USD.";
    return null;
  }
  function resolve(event,bindings){
    const {source_stream,id_status,...canonical}=event;
    if(source_stream!==undefined&&!stream(source_stream)||id_status!==undefined&&!["missing","present","invalid","conflict"].includes(id_status))return {error:"Неподдерживаемые метаданные счёта. Обнови вкладку Pocket Option."};
    if(["invalid","conflict"].includes(id_status))return {error:"Брокер передал некорректные или противоречивые ID счёта. Номер не подставляется вручную."};
    const error=basic(canonical);if(error)return {error};
    if(id(canonical.account_id))return {event:canonical};
    if(id_status==="missing"&&stream(source_stream)){
      const selected=identities(bindings).find(x=>streamKey(x)===streamKey(event));
      if(selected)return {event:{...canonical,account_id:selected.account_id},manual:true,source_stream};
      return {error:"Баланс получен без Pocket ID. Подтверди номер из своего профиля Pocket Option ниже.",identityCandidate:{source_stream,mode:event.mode,reported_currency:event.currency??null,seen_at:event.time}};
    }
    return {event:canonical};
  }
  function inspect(event,confirmed) {
    if(!id(event.account_id))return {error:"Баланс получен, но ID Pocket-счёта не определён. Не подставляем Telegram ID. Обнови торговый терминал.",candidate:null};
    const error=basic(event);if(error)return {error,candidate:null};
    const candidate={account_id:event.account_id,mode:event.mode,reported_currency:event.currency==null?null:event.currency==="USD"?"USD":"unsupported",seen_at:event.time};
    if(event.currency==="USD")return {event,candidate};
    if(event.currency!=null)return {error:"Брокер указал другую или неподдерживаемую валюту. TRED пока поддерживает только USD.",candidate};
    if(confirmations(confirmed).includes(key(candidate)))return {event:{...event,currency:"USD"},candidate};
    return {error:"ID и режим счёта получены, но валюта отсутствует в сообщении брокера. Подтверди USD ниже, только если счёт в долларах.",candidate};
  }
  return {id,mode,key,confirmations,inspect,stream,streamKey,identities,basic,resolve};
})();
