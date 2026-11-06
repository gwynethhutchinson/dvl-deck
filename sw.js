// Keeps the Grimoire quick to open and usable on a weak connection. Same-site files only.
const V = "grimoire-v1";
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim())));
self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.endsWith(".json") && !url.pathname.endsWith("meta.json")){
    // card data: answer from cache right away, refresh it in the background
    e.respondWith(caches.open(V).then(async c => { const hit = await c.match(req, {ignoreSearch:true});
      const net = fetch(req).then(r => { if (r.ok) c.put(req, r.clone()); return r; }).catch(() => hit);
      return hit || net; }));
    return;
  }
  if (req.mode === "navigate"){
    // the page itself: always try the network first so updates show up, fall back to the saved copy offline
    e.respondWith(fetch(req).then(r => { const cp = r.clone(); caches.open(V).then(c => c.put(req, cp)); return r; }).catch(() => caches.match(req, {ignoreSearch:true})));
  }
});
