/* AutoEarn service worker — makes the app installable and openable offline.
 *
 * Strategy:
 *   • App shell (HTML, Alpine, icons) is precached so the app opens instantly,
 *     even with no signal.
 *   • API calls use network-first, but every successful response is cached, so
 *     when you're offline the app shows the LAST numbers it saw instead of an
 *     error. (Workers still need the server online to actually earn — this is
 *     just so the screen always opens.)
 */
const VERSION = "autoearn-v3";
const SHELL = "shell-" + VERSION;
const DATA = "data-" + VERSION;

const SHELL_ASSETS = [
  "/",
  "/static/alpine.min.js",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(SHELL_ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== SHELL && k !== DATA)
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return; // never cache writes (chat, triggers, edits)

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // ignore cross-origin

  // Page navigations: network-first, fall back to cached shell.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put("/", copy));
          return res;
        })
        .catch(() => caches.match("/").then((r) => r || caches.match(req)))
    );
    return;
  }

  // API reads: network-first, cache the result, fall back to cache offline.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(DATA).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() =>
          caches.match(req).then(
            (r) =>
              r ||
              new Response(
                JSON.stringify({ offline: true }),
                { headers: { "Content-Type": "application/json" }, status: 503 }
              )
          )
        )
    );
    return;
  }

  // Everything else (static assets): cache-first.
  event.respondWith(
    caches.match(req).then((cached) =>
      cached ||
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(req, copy));
        return res;
      })
    )
  );
});
