"use strict";
// Only the isolated extension context can reach the worker's pairing token.
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.source !== "tred-observer-v1") return;
  const events = event.data.events;
  if (!Array.isArray(events) || !events.length || events.length > 250) return;
  chrome.runtime.sendMessage({action:"events",events}).catch(() => {});
});
// Keep automatic delivery active while the terminal is open, even with the
// popup closed. A retry preserves the original captured amount and timestamp.
setInterval(()=>chrome.runtime.sendMessage({action:"sync"}).catch(()=>{}),15000);
