"use strict";
const $ = s => document.querySelector(s);
async function status() {
  const s = await chrome.runtime.sendMessage({action:"status"});
  $("#base").value = s.base || "";
  $("#status").textContent = s.lastError || (s.lastSeen ? "Данные переданы: "+new Date(s.lastSeen).toLocaleTimeString() : s.base ? "Связь создана. Обнови Pocket Option; ожидаем поддерживаемые события." : "Модуль ещё не связан с кабинетом.");
}
$("#form").addEventListener("submit",async e => {
  e.preventDefault();
  $("#status").textContent = "Подключение…";
  const result = await chrome.runtime.sendMessage({action:"pair",base:$("#base").value.trim(),code:$("#code").value.trim()});
  if (result.error) $("#status").textContent = result.error;
  else { $("#code").value = ""; await status(); }
});
$("#disconnect").addEventListener("click",async () => { await chrome.runtime.sendMessage({action:"disconnect"}); await status(); });
status();
