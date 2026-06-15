/* AutoEarn service worker — makes the app installable and openable offline.
 *
 * Strategy:
 *   • Static assets (Alpine, icons, manifest) are precached.
 *   • The HTML shell (/) is cached ONLY after a successful, verified
 *     first load — never during install, because in Codespace/cloud
 *     environments the first request to "/" may be an auth interstitial
 *     or redirect that we must never cache as the real app.
 *   • API calls use network-first, cached for offline fallback.
 */
const VERSION = "autoearn-v5";
const SHELL = "shell-" + VERSION;
const DATA  = "data-"  + VERSION;

// "/" is intentionally excluded — it is only cached after a verified
// successful load in the fetch handler below.
const STATIC_ASSETS = [
  "/static/alpine.min.js",
  "/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/maskable-512.png",
  "/static/icons/apple-touch-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL)
      .then((c) => c.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  // Delete every cache that isn't this version — clears interstitials
  // or stale shells cached by previous service worker versions.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

/** Return true only if this response is the real AutoEarn dashboard. */
function isRealDashboard(res) {
  if (!res || !res.ok || res.redirected) return false;
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("text/html")) return false;
  // Must come from our own origin, not a GitHub interstitial proxy
  try {
    const u = new URL(res.url);
    return u.origin === self.location.origin && u.pathname === "/";
  } catch (_) { return false; }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Page navigations: always try the network first.
  // Only cache "/" when we're certain it's the real dashboard (not an interstitial).
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (isRealDashboard(res)) {
            const copy = res.clone();
            caches.open(SHELL).then((c) => c.put("/", copy));
          }
          return res;
        })
        .catch(() =>
          caches.match("/")
            .then((r) => r || caches.match(req))
        )
    );
    return;
  }

  // API reads: network-first, cache for offline fallback.
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(DATA).then((c) => c.put(req, copy));
          }
          return res;
        })
        .catch(() =>
          caches.match(req).then(
            (r) => r || new Response(
              JSON.stringify({ offline: true }),
              { headers: { "Content-Type": "application/json" }, status: 503 }
            )
          )
        )
    );
    return;
  }

  // Static assets: cache-first (they never change without a version bump).
  event.respondWith(
    caches.match(req).then((cached) =>
      cached ||
      fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(req, copy));
        }
        return res;
      })
    )
  );
});
