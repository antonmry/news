const CACHE_NAME = "daily-news-cache-v1";
const OFFLINE_FILES = [
  "./",
  "index.html",
  "manifest.json",
  "https://cdn.jsdelivr.net/npm/marked/marked.min.js",
];

self.addEventListener("install", (event) => {
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
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((resp) => {
        if (request.method === "GET" && resp.status === 200 && resp.type === "basic") {
          const respClone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, respClone));
        }
        return resp;
      }).catch(() => cached || Response.error());
    })
  );
});
