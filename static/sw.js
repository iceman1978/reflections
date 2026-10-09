// Reflections' service worker. Its only job is to let browsers offer
// "Install app". It deliberately stores nothing: no pages, no entries, no
// cache. Every request goes to the server exactly as it would without it.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {
  // No respondWith(): the browser handles every request normally.
});
