const CACHE = "miniclaw-offline-v1";
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(["/offline.html", "/icon-192.svg"])));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(fetch(event.request).then(async (response) => {
    if (response.ok) return response;
    return (await caches.match("/offline.html")) || response;
  }).catch(async () => (await caches.match("/offline.html")) || Response.error()));
});
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch { data = { body: event.data?.text() || "有一条新的提醒。" }; }
  event.waitUntil(self.registration.showNotification(data.title || "MiniClaw 提醒", {
    body: data.body || "到提醒时间了。", icon: "/icon-192.svg", badge: "/icon-192.svg",
    tag: data.reminder_id || "miniclaw-reminder", renotify: true,
    data: { url: data.url || "/", delivery_id: data.delivery_id || "" }
  }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const destination = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
    const existing = clients.find((client) => client.url.startsWith(self.location.origin));
    return existing ? existing.focus().then(() => existing.navigate(destination)) : self.clients.openWindow(destination);
  }));
});
