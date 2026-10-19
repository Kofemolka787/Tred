"use strict";
const $=s=>document.querySelector(s);
let editing=false,hydrated=false,pairing=false;
const UI_VERSION="0.3.2";
const labels={account:"баланс",asset:"каталог пар",quote:"котировки",candle:"свечи",trade:"сделки"};
function outdated(){
  $("#connection").textContent="Окно обновилось, а фоновый модуль ещё работает в другой версии. Нажми «Перезапустить модуль», затем открой TRED снова.";
  $("#connection").className="";$("#reload-module").hidden=false;
  for(const id of ["#form","#change","#disconnect","#diagnostic"])$(id).hidden=true;
  $("#status").textContent="Настройки связи не удалены.";$("#received").textContent="";
}
async function status(){
  let s;
  try{s=await chrome.runtime.sendMessage({action:"status"});}catch(_){outdated();return;}
  if(!s||s.worker_version!==UI_VERSION||typeof s.paired!=="boolean"){outdated();return;}
  $("#reload-module").hidden=true;
  const paired=s.paired&&!s.needsPairing;
  $("#connection").textContent=s.needsPairing?"Ключ связи отклонён сервером. Получи новый код в кабинете.":paired?"Связь с кабинетом сохранена. Повторный код не нужен.":"Модуль ещё не связан с кабинетом.";
  $("#connection").className=paired?"connected":"";
  $("#form").hidden=paired&&!editing;
  $("#change").hidden=!paired||editing;
  $("#disconnect").hidden=!s.paired;
  if(!hydrated){$("#base").value=s.base||"";hydrated=true;}
  $("#status").textContent=!s.paired?"Передача данных начнётся после привязки.":s.lastError?"Передача данных: "+s.lastError:s.lastSeen?"Последняя передача принята сервером: "+new Date(s.lastSeen).toLocaleTimeString():paired?"Ожидаем поддерживаемые события. Обнови график Pocket Option.":"";
  $("#received").textContent=s.paired&&s.receivedKinds?.length?"Сервер принял: "+s.receivedKinds.map(x=>labels[x]).filter(Boolean).join(", ")+".":"";
  $("#diagnostic").hidden=!s.paired||!s.diagnostic;
  $("#diagnostic").textContent=s.paired?s.diagnostic||"":"";
}
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
