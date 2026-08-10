(() => {
  "use strict";

  const byId = (id) => document.getElementById(id);
  const stateIds = ["loading-state", "empty-state", "unavailable-state"];
  const publicDemo = window.location.pathname.startsWith("/private-ai-operations-demo/");
  let source = publicDemo ? "demo" : (new URLSearchParams(window.location.search).get("source") || "demo");
  const endpointForSource = () => source === "live" ? "/api/v1/overview" : "demo-data.json";
  const tourSteps = [
    ["Know what you are viewing", "This dashboard starts with a deterministic synthetic fixture so evaluation is repeatable and cannot be mistaken for live evidence."],
    ["Inspect honest states", "Loading, empty, and unavailable responses are distinct. The interface never substitutes stale or synthetic values for an unavailable live source."],
    ["Review evidence", "Service checks include a timestamp and findings include their evidence. Treat the fixed score as illustrative, not a certification."],
    ["Make a recorded decision", "Use the evaluation guide to test a non-critical environment, capture evidence, and decide to adopt, pilot, or reject."],
  ];
  let tourIndex = 0;

  function setState(state, detail) {
    stateIds.forEach((id) => { byId(id).hidden = id !== `${state}-state`; });
    byId("dashboard").hidden = state !== "ready";
    if (detail) byId("unavailable-detail").textContent = detail;
    byId("status-announcer").textContent = state === "ready" ? "Evaluation data loaded." : `Dashboard state: ${state}.`;
  }

  function addText(parent, tag, value, className) {
    const element = document.createElement(tag);
    element.textContent = value;
    if (className) element.className = className;
    parent.appendChild(element);
    return element;
  }

  function renderMetric(label, value) {
    const card = document.createElement("div");
    card.className = "metric";
    addText(card, "strong", String(value));
    addText(card, "span", label);
    byId("metrics").appendChild(card);
  }

  function render(data) {
    if (!Array.isArray(data.services) || !Array.isArray(data.findings)) throw new Error("The response schema is not supported.");
    if (data.services.length === 0 && data.findings.length === 0) { setState("empty"); return; }
    if (data.data_classification !== "synthetic-demo-only" && source === "demo") throw new Error("Demo classification is missing.");

    byId("metrics").replaceChildren();
    renderMetric("Services ready", `${data.summary.services_ready}/${data.summary.services_total}`);
    renderMetric("Open findings", data.summary.open_findings);
    renderMetric("Evaluation score", `${data.summary.evaluation_score}/100`);
    renderMetric("Fixture version", "v1");
    byId("snapshot-time").textContent = `Fixed at ${data.generated_at}`;

    const body = byId("services-body");
    body.replaceChildren();
    data.services.forEach((service) => {
      const row = document.createElement("tr");
      addText(row, "th", service.name).scope = "row";
      const statusCell = document.createElement("td");
      addText(statusCell, "span", service.status, `status ${service.status}`);
      row.appendChild(statusCell);
      addText(row, "td", `${service.latency_ms} ms`);
      addText(row, "td", service.checked_at);
      body.appendChild(row);
    });

    const list = byId("finding-list");
    list.replaceChildren();
    data.findings.forEach((finding) => {
      const item = document.createElement("li");
      addText(item, "strong", `${finding.severity.toUpperCase()}: ${finding.title}`);
      addText(item, "p", finding.evidence);
      list.appendChild(item);
    });
    setState("ready");
  }

  async function load() {
    setState("loading");
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    try {
      const headers = { Accept: "application/json" };
      if (source === "live") {
        const key = sessionStorage.getItem("ops-api-key") || "";
        const tenant = sessionStorage.getItem("ops-tenant-id") || "local";
        if (!key) throw new Error("Enter an API key before loading live data.");
        headers["X-API-Key"] = key;
        headers["X-Tenant-ID"] = tenant;
      }
      const response = await fetch(endpointForSource(), { signal: controller.signal, headers });
      if (!response.ok) throw new Error(`Source returned HTTP ${response.status}.`);
      render(await response.json());
    } catch (error) {
      const reason = error instanceof Error ? error.message : "Unknown source error.";
      setState("unavailable", `${reason} No values are shown as current.`);
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function showTour() {
    tourIndex = 0;
    byId("onboarding").hidden = false;
    updateTour();
    byId("tour-skip").focus();
  }

  function updateTour() {
    const [title, copy] = tourSteps[tourIndex];
    byId("onboarding-title").textContent = title;
    byId("tour-copy").textContent = copy;
    byId("tour-count").textContent = `Step ${tourIndex + 1} of ${tourSteps.length}`;
    byId("tour-progress").style.width = `${((tourIndex + 1) / tourSteps.length) * 100}%`;
    byId("tour-next").textContent = tourIndex === tourSteps.length - 1 ? "Finish" : "Next step";
  }

  function closeTour() {
    byId("onboarding").hidden = true;
    localStorage.setItem("ops-evaluation-tour-v1", "complete");
    byId("restart-tour").focus();
  }

  function selectSource(nextSource) {
    source = publicDemo ? "demo" : nextSource;
    const live = source === "live";
    const badge = byId("mode-badge");
    badge.textContent = live ? "◆ Live local observation" : "◆ Synthetic demo data";
    badge.setAttribute("aria-label", live ? "Data source: authenticated live local observation" : "Data source: synthetic demo fixture");
    window.history.replaceState({}, "", live ? "?source=live" : "?source=demo");
    load();
  }

  if (publicDemo) byId("source-form").hidden = true;
  byId("source-form").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!event.currentTarget.reportValidity()) return;
    sessionStorage.setItem("ops-api-key", byId("api-key").value);
    sessionStorage.setItem("ops-tenant-id", byId("tenant-id").value);
    selectSource("live");
  });
  byId("load-demo").addEventListener("click", () => selectSource("demo"));
  byId("tour-next").addEventListener("click", () => {
    if (tourIndex === tourSteps.length - 1) closeTour();
    else { tourIndex += 1; updateTour(); }
  });
  byId("tour-skip").addEventListener("click", closeTour);
  byId("restart-tour").addEventListener("click", showTour);
  document.querySelectorAll(".retry").forEach((button) => button.addEventListener("click", () => selectSource("demo")));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !byId("onboarding").hidden) closeTour(); });

  selectSource(source);
})();
