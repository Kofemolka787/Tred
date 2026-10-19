"use strict";
// Never infer an account ID, demo/real mode or USD from prices or the URL.
globalThis.TredAccount = (() => {
  const id = value => typeof value === "string" && /^\d{1,20}$/.test(value);
  const mode = value => ["real","demo"].includes(value);
  const key = candidate => candidate.account_id+":"+candidate.mode;
  const confirmations = values => Array.isArray(values) ? values.filter(x=>typeof x==="string"&&/^\d{1,20}:(real|demo)$/.test(x)).slice(-20) : [];
  function inspect(event,confirmed) {
    if(!id(event.account_id))return {error:"Баланс получен, но ID Pocket-счёта не определён. Не подставляем Telegram ID. Обнови торговый терминал.",candidate:null};
    if(!mode(event.mode))return {error:"Баланс получен, но брокер не указал демо или реальный режим. Счёт не передан.",candidate:null};
    if(typeof event.balance!=="string"||!/^\d{1,8}(\.\d{1,2})?$/.test(event.balance)||Number(event.balance)>10000000)return {error:"Баланс имеет неподдерживаемый формат. Сумма не округляется автоматически.",candidate:null};
    const candidate={account_id:event.account_id,mode:event.mode,reported_currency:event.currency==null?null:event.currency==="USD"?"USD":"unsupported",seen_at:event.time};
    if(event.currency==="USD")return {event,candidate};
    if(event.currency!=null)return {error:"Брокер указал другую или неподдерживаемую валюту. TRED пока поддерживает только USD.",candidate};
    if(confirmations(confirmed).includes(key(candidate)))return {event:{...event,currency:"USD"},candidate};
    return {error:"ID и режим счёта получены, но валюта отсутствует в сообщении брокера. Подтверди USD ниже, только если счёт в долларах.",candidate};
  }
  return {id,mode,key,confirmations,inspect};
})();
