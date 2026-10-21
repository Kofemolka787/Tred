"use strict";
const $=s=>document.querySelector(s);
let editing=false,hydrated=false,pairing=false,changingClock=false,marketClock="utc",accountTarget=null,confirmingCurrency=false;
let identityTarget=null,identityFormKey=null,confirmingIdentity=false;
let activeTabId=null;
const UI_VERSION="0.4.3";
const labels={account:"баланс",asset:"каталог пар",quote:"котировки",candle:"свечи",trade:"сделки",feed:"состояние канала",history_status:"диагностика истории"};
function outdated(){
  $("#connection").textContent="Окно обновилось, а фоновый модуль ещё работает в другой версии. Нажми «Перезапустить модуль», затем открой TRED снова.";
  $("#connection").className="";$("#reload-module").hidden=false;
  for(const id of ["#form","#change","#disconnect","#diagnostic","#market-time","#account-currency","#manual-identity","#manual-id-status","#identity-tab-help","#forget-account-id","#auto-refresh-status","#market-delivery"])$(id).hidden=true;
  $("#status").textContent="Настройки связи не удалены.";$("#received").textContent="";
  $("#balance-delivery").textContent="";$("#terminal-reader-status").textContent="";
}
async function status(){
  let s;
  try{
    try{const tabs=await chrome.tabs.query({active:true,currentWindow:true});activeTabId=tabs[0]?.id??null;}catch(_){activeTabId=null;}
    s=await chrome.runtime.sendMessage({action:"status",active_tab_id:activeTabId});
  }catch(_){outdated();return;}
  if(!s||s.worker_version!==UI_VERSION||typeof s.paired!=="boolean"){outdated();return;}
  $("#reload-module").hidden=true;
  const paired=s.paired&&!s.needsPairing;
  $("#connection").textContent=s.needsPairing?"Ключ связи отклонён сервером. Получи новый код в кабинете.":paired?"Связь с кабинетом сохранена. Повторный код не нужен.":"Модуль ещё не связан с кабинетом.";
  $("#connection").className=paired?"connected":"";
  $("#form").hidden=paired&&!editing;
  $("#change").hidden=!paired||editing;
  $("#disconnect").hidden=!s.paired;
  if(!hydrated){$("#base").value=s.base||"http://127.0.0.1:8790";hydrated=true;}
  $("#status").textContent=!s.paired?"Передача данных начнётся после привязки.":s.lastError?"Передача данных: "+s.lastError:s.lastSeen?"Последняя передача принята сервером: "+new Date(s.lastSeen).toLocaleTimeString():paired?"Ожидаем поддерживаемые события. Обнови график Pocket Option.":"";
  $("#received").textContent=s.paired&&s.receivedKinds?.length?"Сервер принял: "+s.receivedKinds.map(x=>labels[x]).filter(Boolean).join(", ")+".":"";
  const scanner=s.scannerStatus;
  const scanLabels={no_tab:"Открой Pocket Option на вкладке с графиком.",no_socket:"Рыночный канал пока не найден. Обнови вкладку Pocket Option один раз.",waiting_catalog:"Ждём каталог открытого рыночного канала.",request_failed:"Не удалось получить задание сбора. Сверь адрес в расширении с текущим окном сервера TRED.",unsupported_module:"Обнови модуль и вкладку Pocket Option."};
  $("#market-delivery").hidden=!paired;
  $("#market-delivery").textContent=!paired?"":!scanner?"Ожидаем статус сборщика рынка…":Date.now()-scanner.updated_at>20000?"Сборщик давно не отвечал. Проверь, что Pocket Option открыт и адрес сервера актуален.":scanner.code==="ready"?`Сборщик активен: заданий для пар ${scanner.pairs}. Это не число готовых графиков.`:scanLabels[scanner.code]||"Ожидаем данные рынка.";
  if(paired&&scanner?.dedicated)$("#market-delivery").textContent+=" Служебная вкладка поддерживает графики готовыми между запросами. Закрой её, чтобы приостановить сбор.";
  if(paired&&scanner?.paused)$("#market-delivery").textContent="Служебная вкладка закрыта. Новый запрос сигнала возобновит сбор в новой служебной вкладке.";
  $("#balance-delivery").textContent=s.paired&&s.lastBalance?"Последний баланс принят: "+new Date(s.lastBalance.received_at).toLocaleTimeString()+" · "+(s.lastBalance.mode==="demo"?"демосчёт":"реальный счёт"):"";
  $("#terminal-reader-status").textContent=!paired?"":s.terminalCode==="ready"?"Видимый баланс терминала распознан.":s.terminalCode?"Блок баланса пока не распознан. Передача поддерживаемых событий Pocket продолжается.":"Подключаем чтение открытого терминала…";
  $("#diagnostic").hidden=!s.paired||!s.diagnostic;
  $("#diagnostic").textContent=s.paired?s.diagnostic||"":"";
  $("#market-time").hidden=!paired;
  marketClock=s.marketClock==="po-utc2"?"po-utc2":"utc";
  $("#clock-status").textContent=marketClock==="po-utc2"?"Время котировок и свечей: Pocket Option UTC+2 → UTC. Проверка свежести включена.":"Время котировок и свечей: UTC, без пересчёта.";
  $("#clock-toggle").textContent=marketClock==="po-utc2"?"Вернуть формат UTC":"Использовать Pocket Option UTC+2";
  accountTarget=s.accountCandidate;
  const canConfirm=accountTarget&&/^\d{1,20}$/.test(accountTarget.account_id)&&["real","demo"].includes(accountTarget.mode)&&accountTarget.reported_currency===null;
  $("#account-currency").hidden=!paired||!canConfirm;
  $("#account-identity").textContent=canConfirm?`Pocket ID ${accountTarget.account_id} · ${accountTarget.mode==="demo"?"демосчёт":"реальный счёт"}`:"";
  $("#currency-status").textContent=s.usdConfirmed?"USD подтверждён тобой для этого счёта и режима.":"Брокер не указал валюту в сообщении баланса.";
  $("#confirm-usd").hidden=!!s.usdConfirmed;
  $("#forget-usd").hidden=!s.usdConfirmed;
  identityTarget=s.identityCandidate;
  const needsId=identityTarget&&typeof identityTarget.source_stream==="string"&&["real","demo"].includes(identityTarget.mode);
  $("#manual-identity").hidden=!paired||!needsId;
  $("#identity-tab-help").hidden=!paired||!s.identityNeedsTab;
  if(needsId){
    const nextKey=identityTarget.source_stream+":"+identityTarget.mode;
    if(nextKey!==identityFormKey){
      identityFormKey=nextKey;$("#pocket-id").value=s.lastManualAccountId||"";$("#identity-usd").checked=false;$("#identity-error").textContent="";
    }
    $("#identity-mode").textContent=identityTarget.mode==="real"?"Реальный счёт":"Демосчёт";
    $("#identity-usd-label").hidden=identityTarget.reported_currency==="USD";
    $("#identity-usd").required=identityTarget.reported_currency!=="USD";
  }
  const manual=paired&&accountTarget?.identity_source==="manual";
  $("#manual-id-status").hidden=!manual;
  $("#manual-id-status").textContent=manual?`Pocket ID ${accountTarget.account_id} сохранён тобой · ${accountTarget.mode==="demo"?"демосчёт":"реальный счёт"}. Повторное подтверждение при обновлении баланса не нужно. Для другого Pocket-аккаунта нажми «Сменить Pocket ID».`:"";
  $("#forget-account-id").hidden=!manual;
  $("#auto-refresh-status").hidden=!paired;
}
$("#forget-account-id").addEventListener("click",async()=>{
  if(!accountTarget)return;
  const result=await chrome.runtime.sendMessage({action:"forget-account-id",account_id:accountTarget.account_id,mode:accountTarget.mode});
  if(result.error)$("#status").textContent=result.error;
  else await status();
});
$("#identity-form").addEventListener("submit",async event=>{
  event.preventDefault();if(confirmingIdentity||!identityTarget)return;
  confirmingIdentity=true;const target={...identityTarget};$("#identity-form button").disabled=true;$("#identity-error").textContent="";
  try{
    const result=await chrome.runtime.sendMessage({action:"confirm-account-id",account_id:$("#pocket-id").value.trim(),source_stream:target.source_stream,mode:target.mode,confirm_usd:$("#identity-usd").checked,active_tab_id:activeTabId});
    if(result.error)$("#identity-error").textContent=result.error;
    else await status();
  }catch(_){outdated();}
  finally{confirmingIdentity=false;$("#identity-form button").disabled=false;}
});
async function currencyAction(action){
  if(confirmingCurrency||!accountTarget)return;
  confirmingCurrency=true;const target={...accountTarget};
  $("#confirm-usd").disabled=true;$("#forget-usd").disabled=true;$("#currency-error").textContent="";
  try{
    const result=await chrome.runtime.sendMessage({action,account_id:target.account_id,mode:target.mode});
    if(result.error)$("#currency-error").textContent=result.error;
    else{await status();$("#currency-error").textContent=action==="confirm-usd"?"USD сохранён. Следующее изменение баланса передастся автоматически.":"Подтверждение отменено.";}
  }catch(_){outdated();}
  finally{confirmingCurrency=false;$("#confirm-usd").disabled=false;$("#forget-usd").disabled=false;}
}
$("#confirm-usd").addEventListener("click",()=>currencyAction("confirm-usd"));
$("#forget-usd").addEventListener("click",()=>currencyAction("forget-usd"));
$("#clock-toggle").addEventListener("click",async()=>{
  if(changingClock)return;changingClock=true;$("#clock-toggle").disabled=true;$("#clock-error").textContent="";
  try{
    const result=await chrome.runtime.sendMessage({action:"market-clock",profile:marketClock==="po-utc2"?"utc":"po-utc2"});
    if(result.error)$("#clock-error").textContent=result.error;
    else await status();
  }catch(_){outdated();}
  finally{changingClock=false;$("#clock-toggle").disabled=false;}
});
$("#reload-module").addEventListener("click",()=>chrome.runtime.reload());
$("#change").addEventListener("click",()=>{editing=true;$("#form").hidden=false;$("#change").hidden=true;});
$("#form").addEventListener("submit",async e=>{
  e.preventDefault();if(pairing)return;pairing=true;$("#form button").disabled=true;
  $("#pair-error").textContent="Подключение…";
  try{
    const result=await chrome.runtime.sendMessage({action:"pair",base:$("#base").value.trim(),code:$("#code").value.trim()});
    if(result.error)$("#pair-error").textContent=result.error+" Скопируй текущий код для этого же адреса из мини-приложения.";
    else{$("#code").value="";$("#pair-error").textContent="";editing=false;await status();}
  }catch(_){outdated();}
  finally{pairing=false;$("#form button").disabled=false;}
});
$("#disconnect").addEventListener("click",async()=>{
  await chrome.runtime.sendMessage({action:"disconnect"});editing=false;$("#base").value="";$("#code").value="";$("#pair-error").textContent="";await status();
});
status();
setInterval(()=>status().catch(()=>{}),2000);
