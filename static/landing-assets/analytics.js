// Local-first conversion tracking (fire-and-forget, never blocks the page).
// POSTs to /api/track — the storefront records events in revenue-os.db.
(function () {
  function track(event, extra) {
    try {
      var payload = {
        event: event,
        page: location.pathname,
        product_slug: null,
        checkout_url: null,
        session_id: (function () {
          var s = sessionStorage.getItem("lab_sid");
          if (!s) { s = (Date.now().toString(36) + Math.random().toString(36).slice(2, 8)); sessionStorage.setItem("lab_sid", s); }
          return s;
        })(),
        referrer: document.referrer || null,
      };
      if (extra) { for (var k in extra) payload[k] = extra[k]; }
      // derive product slug from path /p/<slug>
      var m = location.pathname.match(/\/p\/([^/]+)/);
      if (m) payload.product_slug = m[1];
      navigator.sendBeacon("/api/track", new Blob([JSON.stringify(payload)], { type: "application/json" }));
    } catch (e) { /* resilient: ignore */ }
  }
  // page_view on load
  if (document.readyState !== "loading") track("page_view");
  else document.addEventListener("DOMContentLoaded", function () { track("page_view"); });
  // checkout_click on any stripe/checkout link click
  document.addEventListener("click", function (e) {
    var a = e.target.closest("a");
    if (a && /buy\.stripe\.com|checkout|gumroad\.com/i.test(a.href || "")) {
      track("checkout_click", { checkout_url: a.href });
    }
  });

  // ── Core Web Vitals RUM (#68): send LCP/CLS/INP/FCP to /api/track ──
  function sendVital(name, value) {
    try {
      navigator.sendBeacon("/api/track", new Blob([JSON.stringify({
        event: "web_vitals", metric: name, value: Math.round(value),
        page: location.pathname, session_id: (sessionStorage.getItem("lab_sid") || "na")
      })], { type: "application/json" }));
    } catch (e) {}
  }
  if ("PerformanceObserver" in window) {
    try {
      new PerformanceObserver(function (l) { var e = l.getEntries(); if (e.length) sendVital("LCP", e[e.length-1].startTime); })
        .observe({ type: "largest-contentful-paint", buffered: true });
      new PerformanceObserver(function (l) { var e = l.getEntries(); if (e.length) sendVital("CLS", e[e.length-1].value); })
        .observe({ type: "layout-shift", buffered: true });
      new PerformanceObserver(function (l) { l.getEntries().forEach(function (e) { if (e.hadRecentInput) return; sendVital("INP", e.duration || e.processingEnd - e.startTime); }); })
        .observe({ type: "event", buffered: true });
      new PerformanceObserver(function (l) { var e = l.getEntries(); if (e.length) sendVital("FCP", e[e.length-1].startTime); })
        .observe({ type: "paint", buffered: true });
    } catch (e) {}
  }
})();
