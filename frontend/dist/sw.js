/* Echolocate service worker. Caches the app shell + handles push notifications. */

const CACHE = "echolocate-v1";
const SHELL = ["/app/", "/app/index.html", "/app/app.js", "/app/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL).catch(() => {}))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Only cache same-origin, GET, app-shell paths. API calls always go to network.
  if (event.request.method !== "GET" || url.pathname.startsWith("/api/") || url.pathname === "/ws") return;
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request).catch(() => caches.match("/app/")))
  );
});

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) {}
  const title = data.title || "Echolocate";
  const options = {
    body: data.body || "Echolocate alert",
    icon: data.icon || "icon-192.png",
    badge: data.badge || "icon-192.png",
    tag: data.tag || "echolocate",
    data: data.data || {},
    vibrate: [180, 90, 180],
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data.url || "/app/"));
});
