"use strict";
const $=s=>document.querySelector(s);
let editing=false,hydrated=false;
const labels={account:"баланс",asset:"каталог пар",quote:"котировки",candle:"свечи",trade:"сделки"};
async function status(){
  const s=await chrome.runtime.sendMessage({action:"status"});
  const paired=s.paired&&!s.needsPairing;
  $("#connection").textContent=s.needsPairing?"Ключ связи отклонён сервером. Получи новый код в кабинете.":paired?"Связь с кабинетом сохранена. Повторный код не нужен.":"Модуль ещё не связан с кабинетом.";
  $("#connection").className=paired?"connected":"";
  $("#form").hidden=paired&&!editing;
  $("#change").hidden=!paired||editing;
  $("#disconnect").hidden=!s.paired;
  if(!hydrated){$("#base").value=s.base||"";hydrated=true;}
  $("#status").textContent=s.lastError?"Передача данных: "+s.lastError:s.lastSeen?"Последняя передача принята сервером: "+new Date(s.lastSeen).toLocaleTimeString():paired?"Ожидаем поддерживаемые события. Обнови график Pocket Option.":"";
  $("#received").textContent=s.receivedKinds?.length?"Сервер принял: "+s.receivedKinds.map(x=>labels[x]).filter(Boolean).join(", ")+".":"";
  $("#diagnostic").hidden=!s.diagnostic;
  $("#diagnostic").textContent=s.diagnostic||"";
}
$("#change").addEventListener("click",()=>{editing=true;$("#form").hidden=false;$("#change").hidden=true;});
$("#form").addEventListener("submit",async e=>{
  e.preventDefault();$("#pair-error").textContent="Подключение…";
  const result=await chrome.runtime.sendMessage({action:"pair",base:$("#base").value.trim(),code:$("#code").value.trim()});
  if(result.error)$("#pair-error").textContent=result.error+" Ранее сохранённая связь не удалена.";
  else{$("#code").value="";$("#pair-error").textContent="";editing=false;await status();}
});
$("#disconnect").addEventListener("click",async()=>{
  await chrome.runtime.sendMessage({action:"disconnect"});editing=false;$("#base").value="";$("#code").value="";$("#pair-error").textContent="";await status();
});
status();
setInterval(()=>status().catch(()=>{}),2000);
