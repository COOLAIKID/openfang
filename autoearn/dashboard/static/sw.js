/* AutoEarn service worker — CLEANUP VERSION
 *
 * This SW immediately unregisters itself and wipes all caches.
 * This prevents any previously cached bad content (GitHub interstitials,
 * stale shells, etc.) from ever being served again.
 *
 * The app works fine without a SW — it just won't have offline caching.
 * A proper SW can be added back once the app is confirmed stable.
 */
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([
      // Delete every cache entry ever stored
      caches.keys().then((keys) =>
        Promise.all(keys.map((k) => caches.delete(k)))
      ),
      // Take control of all pages immediately
      self.clients.claim(),
    ]).then(() => {
      // Unregister this SW so no SW is active going forward
      return self.registration.unregister();
    })
  );
});

// Pass all fetches straight through — no caching at all
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
