"use strict";
// Never infer an account ID, demo/real mode or USD from prices or the URL.
globalThis.TredAccount = (() => {
  const id = value => typeof value === "string" && /^\d{1,20}$/.test(value) && !/^0+$/.test(value);
  const mode = value => ["real","demo"].includes(value);
  const key = candidate => candidate.account_id+":"+candidate.mode;
  const confirmations = values => Array.isArray(values) ? values.filter(x=>typeof x==="string"&&/^\d{1,20}:(real|demo)$/.test(x)).slice(-20) : [];
  const stream = value => typeof value==="string"&&/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
  const streamKey = event => event.source_stream+":"+event.mode;
  const identities = values => Array.isArray(values)?values.filter(x=>x&&stream(x.source_stream)&&mode(x.mode)&&id(x.account_id)).slice(-20):[];
  const sourceAccount=value=>typeof value==="string"&&/^po1:[a-f0-9]{64}$/.test(value);
  const profiles=values=>Array.isArray(values)?values.filter(x=>x&&mode(x.mode)&&id(x.account_id)&&(x.source_account==null||sourceAccount(x.source_account))).slice(-20):[];
  function storedProfiles(stored){
    if(stored.accountProfiles!==undefined)return profiles(stored.accountProfiles);
    // Upgrade the owner's existing explicit selection; never fill an ID from
    // Telegram or invent one. First observed stable identity pins this profile.
    const old=stored.accountCandidate;
    return old?.identity_source==="manual"&&id(old.account_id)&&mode(old.mode)?[{account_id:old.account_id,mode:old.mode,source_account:null}]:[];
  }
  const manualStatuses=["missing","unsupported","ambiguous"];
  const shapeTypes={none:"нет",empty:"пусто",zero:"ноль",integer:"целое число",digits:"цифры",number:"другой числовой формат",text:"текст",boolean:"логическое значение",object:"объект",array:"массив",other:"неподдерживаемый тип"};
  const validShape=value=>value!==null&&typeof value==="object"&&!Array.isArray(value)&&Object.keys(value).every(key=>["uid","accountId","id"].includes(key)&&Object.hasOwn(shapeTypes,value[key]));
  const shapeText=value=>value?" Поля ID: "+Object.entries(value).map(([key,type])=>key+" — "+shapeTypes[type]).join("; ")+".":"";
  function basic(event){
    if(!mode(event.mode))return "Баланс получен, но брокер не указал демо или реальный режим. Счёт не передан.";
    if(typeof event.balance!=="string"||!/^\d{1,8}(\.\d{1,2})?$/.test(event.balance)||Number(event.balance)>10000000)return "Баланс имеет неподдерживаемый формат. Сумма не округляется автоматически.";
    if(event.currency!=null&&event.currency!=="USD")return "Брокер указал другую или неподдерживаемую валюту. TRED пока поддерживает только USD.";
    return null;
  }
  function resolve(event,bindings,remembered=[]){
    const {source_stream,id_status,id_shape,source_account,...canonical}=event;
    if(source_account!=null&&!sourceAccount(source_account))return {error:"Неподдерживаемый признак аккаунта. Обнови вкладку Pocket Option."};
    if(source_stream!==undefined&&!stream(source_stream)||id_status!==undefined&&![...manualStatuses,"present","invalid","conflict"].includes(id_status)||id_shape!==undefined&&!validShape(id_shape))return {error:"Неподдерживаемые метаданные счёта. Обнови вкладку Pocket Option."};
    if(["invalid","conflict"].includes(id_status))return {error:"Вкладка ещё использует прежний обработчик ID. Обнови график Pocket Option после обновления модуля."};
    const error=basic(canonical);if(error)return {error};
    if(id(canonical.account_id))return {event:canonical};
    if(manualStatuses.includes(id_status)&&stream(source_stream)){
      const selected=identities(bindings).find(x=>streamKey(x)===streamKey(event));
      const profile=profiles(remembered).find(x=>x.mode===event.mode&&(x.source_account==null||x.source_account===source_account));
      const binding=selected||profile;
      if(binding)return {event:{...canonical,account_id:binding.account_id},manual:true,source_stream,automatic:!!profile,profile:{account_id:binding.account_id,mode:event.mode,source_account:source_account??profile?.source_account??null}};
      return {error:"Баланс получен. Подтверди Pocket ID из своего профиля ниже."+shapeText(id_shape),identityCandidate:{source_stream,mode:event.mode,reported_currency:event.currency??null,seen_at:event.time,id_status}};
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
  return {id,mode,key,confirmations,inspect,stream,streamKey,identities,basic,resolve,manualStatuses,profiles,storedProfiles,sourceAccount};
})();
