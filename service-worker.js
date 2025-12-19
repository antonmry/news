const CACHE_NAME = "daily-news-cache-v2";
const OFFLINE_FILES = [
  "./",
  "index.html",
  "manifest.json",
  "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(OFFLINE_FILES))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => key !== CACHE_NAME && caches.delete(key)))
    )
  );
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);

  // Always hit network for markdown content to avoid stale cache
  if (url.pathname.endsWith(".md")) {
    event.respondWith(
      fetch(request, { cache: "no-store" }).catch(() => caches.match(request))
    );
    return;
  }

  // Network-first for the shell so updates land quickly
  if (url.origin === location.origin && (url.pathname === "/" || url.pathname.endsWith("index.html"))) {
    event.respondWith(
      fetch(request, { cache: "no-store" })
        .then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
          return resp;
        })
        .catch(() => caches.match(request))
    );
    return;
  }

  // Cache-first for everything else, with background fill
  event.respondWith(
    caches.match(request).then((cached) => {
      const fetchPromise = fetch(request).then((resp) => {
        if (resp.status === 200 && resp.type === "basic") {
          caches.open(CACHE_NAME).then((cache) => cache.put(request, resp.clone()));
        }
        return resp;
      }).catch(() => cached);
      return cached || fetchPromise;
    })
  );
});
