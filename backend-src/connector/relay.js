"use strict";
// Only the isolated extension context can reach the worker's pairing token.
window.addEventListener("message", event => {
  if (event.source !== window || event.origin !== location.origin || event.data?.source !== "tred-observer-v1") return;
  const events = event.data.events;
  if (!Array.isArray(events) || !events.length || events.length > 250) return;
  chrome.runtime.sendMessage({action:"events",events}).catch(() => {});
});
