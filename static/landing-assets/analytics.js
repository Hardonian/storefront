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
})();
