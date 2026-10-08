const $ = (selector) => document.querySelector(selector);
const state = { categories: [], runs: [], reports: [], usage: null, poller: null, activeRunId: null };
const adminKey = () => localStorage.getItem("cognivexAdminKey") || "";
const pageNames = { "/": "Overview", "/workspace/runs": "Runs", "/workspace/reports": "Reports", "/workspace/categories": "Categories", "/workspace/settings": "Settings" };

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body) headers.set("Content-Type", "application/json");
  if (adminKey()) headers.set("X-Admin-API-Key", adminKey());
  const response = await fetch(path, { ...options, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message) {
  const el = $("#toast"); el.textContent = message; el.classList.add("show");
  window.setTimeout(() => el.classList.remove("show"), 3000);
}
function escapeHtml(value = "") {
  return String(value).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function prettyDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleString([], { month:"short", day:"numeric", hour:"numeric", minute:"2-digit" });
}
function statusPill(status) {
  return `<span class="status-pill status-${escapeHtml(status)}">${escapeHtml(status)}</span>`;
}
function providerName(provider = "") {
  const names = { tavily:"Tavily", hacker_news:"Hacker News", hn:"Hacker News", gemini:"Gemini", groq:"Groq", anthropic:"Anthropic" };
  return names[provider.toLowerCase()] || provider || "—";
}
function stageTitle(stageId) {
  return runStages.find(stage => stage.id === stageId)?.title || stageId;
}
function formatTokens(value) {
  return new Intl.NumberFormat(undefined, { notation:"compact", maximumFractionDigits:1 }).format(Number(value || 0));
}
function renderUsage() {
  const usage = state.usage || { input_tokens:0, output_tokens:0, total_tokens:0 };
  if ($("#tokenMetric")) $("#tokenMetric").textContent = formatTokens(usage.total_tokens);
  if ($("#tokenBreakdown")) $("#tokenBreakdown").textContent = `${formatTokens(usage.input_tokens)} input · ${formatTokens(usage.output_tokens)} output`;
}
function updateRouteChrome(path) {
  document.body.classList.toggle("route-page", path !== "/");
  document.querySelectorAll("[data-route]").forEach(link => link.classList.toggle("active", link.dataset.route === path));
  $(".breadcrumbs b").textContent = pageNames[path] || "Overview";
  $("#runCount").textContent = state.runs.length;
}
function renderRuns() {
  const runs = state.runs;
  if ($("#runMetric")) $("#runMetric").textContent = runs.length;
  if (!$("#runsBody")) return;
  $("#runsBody").innerHTML = runs.length ? runs.slice(0, 8).map(run => `
    <tr><td><span class="run-id">#${run.id}</span></td><td>${escapeHtml(run.category)}</td><td>${statusPill(run.status)}${run.failed_stages?.length ? `<small class="run-failed-stage">Failed: ${run.failed_stages.map(stageTitle).map(escapeHtml).join(", ")}</small>` : ""}</td>
    <td>$${Number(run.cost_usd || 0).toFixed(3)}</td><td>${prettyDate(run.started_at || run.created_at)}</td>
    <td><button class="row-open" data-run="${run.id}" aria-label="View run details and report">View →</button></td></tr>`).join("")
    : `<tr><td colspan="6" class="empty-row">No research runs yet. Start one to begin discovering products.</td></tr>`;
  document.querySelectorAll("#runsBody [data-run]").forEach(button => button.addEventListener("click", () => showRun(button.dataset.run)));
}
function renderCategories() {
  const categories = state.categories;
  if ($("#categoryMetric")) $("#categoryMetric").textContent = categories.filter(c => c.active).length;
  const active = categories.filter(c => c.active);
  if ($("#categorySelect")) {
    $("#categorySelect").innerHTML = active.length
      ? active.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join("")
      : `<option value="">Add an active category first</option>`;
  }
  if ($("#categoryList")) {
    $("#categoryList").innerHTML = categories.length ? categories.map(c => `
      <div class="category-row"><span class="category-name">${escapeHtml(c.name)}</span><span class="category-state">${c.active ? "Active" : "Paused"}
      <button class="switch ${c.active ? "on" : ""}" data-category="${c.id}" data-active="${c.active}" aria-label="Toggle ${escapeHtml(c.name)}"></button></span></div>`).join("")
      : `<div class="empty-state">No categories configured.</div>`;
    document.querySelectorAll("#categoryList [data-category]").forEach(button => button.addEventListener("click", () => toggleCategory(button.dataset.category, button.dataset.active !== "true")));
  }
}
function renderWorkspacePage(path) {
  const page = $("#workspacePage");
  if (!page) return;
  if (path === "/") { page.innerHTML = ""; return; }
  if (path === "/workspace/runs") {
    page.innerHTML = `<section class="workspace-heading"><div><div class="eyebrow">WORKSPACE / RUNS</div><h1>Research runs</h1><p>Monitor every run, control active work, and inspect stage decisions.</p></div><button class="primary-button" id="pageStartRun">＋ Start a research run</button></section><section class="panel workspace-panel"><div class="toolbar"><div class="filter-tabs" id="runFilters"><button class="filter-tab active" data-filter="all">All</button><button class="filter-tab" data-filter="running">Active</button><button class="filter-tab" data-filter="completed">Completed</button><button class="filter-tab" data-filter="failed">Needs attention</button></div><button class="secondary-button" id="pageRefreshRuns">↻ Refresh</button></div><div class="table-wrap"><table><thead><tr><th>RUN</th><th>CATEGORY</th><th>STATUS</th><th>COST</th><th>STARTED</th><th></th></tr></thead><tbody id="allRunsBody"></tbody></table></div></section>`;
    renderAllRuns("all");
    $("#pageStartRun").addEventListener("click", () => { navigate("/"); $("#categorySelect").focus(); });
    $("#pageRefreshRuns").addEventListener("click", refresh);
    $("#runFilters").addEventListener("click", event => { const filter = event.target.closest("[data-filter]")?.dataset.filter; if (filter) { document.querySelectorAll(".filter-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.filter === filter)); renderAllRuns(filter); } });
  } else if (path === "/workspace/reports") {
    page.innerHTML = `<section class="workspace-heading"><div><div class="eyebrow">WORKSPACE / REPORTS</div><h1>Report library</h1><p>Open evidence-backed comparisons and trace each report to its source run.</p></div><span class="section-badge">${state.reports.length} reports</span></section><section class="panel workspace-panel"><div class="report-grid full-report-grid" id="allReportsGrid"></div></section>`;
    renderAllReports();
  } else if (path === "/workspace/categories") {
    page.innerHTML = `<section class="workspace-heading"><div><div class="eyebrow">WORKSPACE / CATEGORIES</div><h1>Research categories</h1><p>Keep discovery focused by activating only the topics your team is tracking.</p></div></section><section class="panel workspace-panel"><form class="category-form" id="pageCategoryForm"><input class="input" id="pageNewCategory" placeholder="e.g. AI meeting notes" maxlength="200"><button class="primary-button" type="submit">Add category</button></form><div class="category-list" id="allCategoryList"></div></section>`;
    renderAllCategories();
    $("#pageCategoryForm").addEventListener("submit", event => addCategory(event, "pageNewCategory"));
  } else if (path === "/workspace/settings") {
    page.innerHTML = `<section class="workspace-heading"><div><div class="eyebrow">WORKSPACE / SETTINGS</div><h1>Workspace settings</h1><p>Review the active research configuration and local API access.</p></div></section><section class="settings-grid"><article class="panel settings-card"><div class="panel-heading"><div><h2>API access</h2><p>Used for protected actions from this browser.</p></div><span class="status-pill" id="settingsKeyStatus">Checking</span></div><button class="primary-button" id="pageOpenKey">Manage API key</button></article><article class="panel settings-card"><div class="panel-heading"><div><h2>Research engine</h2><p>Current runtime limits and providers.</p></div></div><div class="settings-list" id="settingsList">Loading configuration…</div></article></section>`;
    $("#pageOpenKey").addEventListener("click", () => { $("#adminKey").value = adminKey(); $("#keyDialog").showModal(); });
    loadPublicSettings();
  }
}
function renderAllRuns(filter) {
  const runs = filter === "all" ? state.runs : state.runs.filter(run => filter === "running" ? ["pending", "running", "paused"].includes(run.status) : filter === "failed" ? ["failed", "partial"].includes(run.status) : run.status === filter);
  $("#allRunsBody").innerHTML = runs.length ? runs.map(run => `<tr><td><span class="run-id">#${run.id}</span></td><td>${escapeHtml(run.category)}</td><td>${statusPill(run.status)}</td><td>$${Number(run.cost_usd || 0).toFixed(3)}</td><td>${prettyDate(run.started_at)}</td><td><button class="row-open" data-run="${run.id}">View →</button></td></tr>`).join("") : `<tr><td colspan="6" class="empty-row">No runs match this filter.</td></tr>`;
  document.querySelectorAll("#allRunsBody [data-run]").forEach(button => button.addEventListener("click", () => showRun(button.dataset.run)));
}
function renderAllReports() {
  $("#allReportsGrid").innerHTML = state.reports.length ? state.reports.map(report => { const run = state.runs.find(item => item.id === report.run_id); return `<article class="report-card"><div class="report-card-top"><span class="report-mark">▤</span><span class="report-date">${prettyDate(report.created_at)}</span></div><h3>Product comparison #${report.id}</h3><p>${escapeHtml(run?.category || "Research report")} · ${escapeHtml(report.confidence_notes || "Evidence-linked comparison")}</p><div class="report-card-bottom"><button class="report-run-link" data-run="${report.run_id}">Run #${report.run_id}</button><button class="report-open" data-report="${report.id}">Open report →</button></div></article>`; }).join("") : `<div class="empty-state">No reports have been generated yet.</div>`;
  document.querySelectorAll("#allReportsGrid [data-report]").forEach(button => button.addEventListener("click", () => showReport(button.dataset.report)));
  document.querySelectorAll("#allReportsGrid [data-run]").forEach(button => button.addEventListener("click", () => showRun(button.dataset.run)));
}
function renderAllCategories() {
  $("#allCategoryList").innerHTML = state.categories.length ? state.categories.map(c => `<div class="category-row"><span><b class="category-name">${escapeHtml(c.name)}</b><small class="category-description">${c.active ? "Included in new research runs" : "Excluded from new research runs"}</small></span><span class="category-state">${c.active ? "Active" : "Paused"}<button class="switch ${c.active ? "on" : ""}" data-category="${c.id}" data-active="${c.active}" aria-label="Toggle ${escapeHtml(c.name)}"></button></span></div>`).join("") : `<div class="empty-state">No categories configured.</div>`;
  document.querySelectorAll("#allCategoryList [data-category]").forEach(button => button.addEventListener("click", () => toggleCategory(button.dataset.category, button.dataset.active !== "true")));
}
async function loadPublicSettings() {
  try { const settings = await api("/settings"); $("#settingsKeyStatus").textContent = settings.admin_key_enabled ? "Protected" : "Local access"; $("#settingsList").innerHTML = [["Default category", settings.category], ["Search provider", settings.search_provider], ["LLM provider", settings.llm_provider], ["Models", `${settings.fast_model} · ${settings.strong_model}`], ["Run budget", `${settings.max_run_minutes} min · $${settings.max_run_cost_usd}`], ["Scheduled runs", settings.schedule_cron]].map(item => `<div><span>${escapeHtml(item[0])}</span><b>${escapeHtml(item[1])}</b></div>`).join(""); } catch (error) { toast(error.message); }
}
function renderReports() {
  const reports = state.reports;
  if ($("#reportCount")) $("#reportCount").textContent = reports.length;
  if (!$("#reportGrid")) return;
  $("#reportMetric").textContent = reports.length;
  $("#reportBadge").textContent = `${reports.length} report${reports.length === 1 ? "" : "s"}`;
  $("#reportGrid").innerHTML = reports.length ? reports.slice(0, 6).map(report => {
    const run = state.runs.find(item => item.id === report.run_id);
    return `<article class="report-card"><div class="report-card-top"><span class="report-mark">▤</span><span class="report-date">${prettyDate(report.created_at)}</span></div>
      <h3>Product comparison #${report.id}</h3><p>${escapeHtml(run?.category || "Research report")} · ${escapeHtml(report.confidence_notes || "Evidence-linked comparison")}</p>
      <div class="report-card-bottom"><button class="report-run-link" data-run="${report.run_id}">Run #${report.run_id}</button><button class="report-open" data-report="${report.id}">Open report →</button></div></article>`;
  }).join("") : `<div class="empty-state">Your reports will appear here after a research run finishes.</div>`;
  document.querySelectorAll("#reportGrid [data-report]").forEach(button => button.addEventListener("click", () => showReport(button.dataset.report)));
  document.querySelectorAll("#reportGrid [data-run]").forEach(button => button.addEventListener("click", () => showRun(button.dataset.run)));
}
async function refresh() {
  try {
    const [categories, runs, reports, usage] = await Promise.all([api("/categories"), api("/runs?limit=30"), api("/reports"), api("/usage")]);
    state.categories = categories; state.runs = runs; state.reports = reports; state.usage = usage;
    renderCategories(); renderRuns(); renderReports(); renderUsage(); updateRouteChrome(window.location.pathname);
    if ($("#workspacePage")) renderWorkspacePage(window.location.pathname);
    else if (document.body.dataset.page === "runs") renderAllRuns("all");
    else if (document.body.dataset.page === "reports") renderAllReports();
    else if (document.body.dataset.page === "categories") renderAllCategories();
    else if (document.body.dataset.page === "settings") loadPublicSettings();
    if (state.activeRunId && $("#runDialog").open) await loadRunDetails(state.activeRunId);
    if (runs.some(run => ["pending", "running"].includes(run.status))) {
      if (!state.poller) state.poller = setInterval(refresh, 5000);
    } else if (state.poller) { clearInterval(state.poller); state.poller = null; }
  } catch (error) { toast(error.message); }
}
async function startRun() {
  const category = $("#categorySelect").value;
  if (!category) return toast("Choose or add an active category first.");
  try {
    const result = await api("/runs", { method:"POST", body:JSON.stringify({category}) });
    toast(`Run #${result.run_id} queued for ${category}.`); await refresh(); showRun(result.run_id);
    if (!state.poller) state.poller = setInterval(refresh, 5000);
  } catch (error) {
    toast(error.message);
  }
}
async function toggleCategory(id, active) {
  try { await api(`/categories/${id}`, {method:"PATCH", body:JSON.stringify({active})}); await refresh(); }
  catch (error) { toast(error.message); }
}
async function addCategory(event, inputId = "newCategory") {
  event.preventDefault(); const input = $(`#${inputId}`); const name = input.value.trim();
  if (!name) return;
  try { await api("/categories", {method:"POST", body:JSON.stringify({name})}); input.value=""; await refresh(); toast("Category added."); }
  catch (error) { toast(error.message); }
}
const runStages = [
  { id:"discover", title:"Discover candidates", description:"Search public sources and extract product candidates." },
  { id:"validate", title:"Validate product sites", description:"Check that each candidate is a reachable public product site." },
  { id:"select", title:"Choose a comparison pair", description:"Select two new products with enough category overlap." },
  { id:"research", title:"Research evidence", description:"Map product sites, select useful pages, and verify facts." },
  { id:"screenshot_capture", title:"Capture and review screenshots", description:"Render product pages in Chromium and check each image." },
  { id:"compare_render", title:"Verify and build report", description:"Compare sourced facts and render HTML and Markdown." },
];

function stageTools(stageId, run, decisions) {
  const calls = decisions.filter(call => {
    if (stageId === "discover") return ["query_generator", "candidate_extractor", "alternative_query_generator", "alternative_candidate_extractor"].includes(call.stage);
    if (stageId === "select") return call.stage === "pair_selector";
    if (stageId === "research") return ["page_picker", "fact_extractor", "gap_filler"].includes(call.stage);
    if (stageId === "screenshot_capture") return call.stage === "screenshot_judge";
    if (stageId === "compare_render") return ["comparator", "writer", "verifier", "verifier_recheck"].includes(call.stage);
    return false;
  });
  const actualModels = [...new Set(calls.map(call => `${providerName(call.provider)} · ${call.model}`))];
  const tools = run.tools || {};
  const fastModel = `${providerName(tools.llm_provider)} · ${tools.fast_model || "fast model"}`;
  const strongModel = `${providerName(tools.llm_provider)} · ${tools.strong_model || "strong model"}`;
  if (stageId === "discover") return [`${providerName(tools.search_provider)} Search`, ...(actualModels.length ? actualModels : [fastModel])];
  if (stageId === "validate") return ["Safe URL checks", "Public-site HTTP validation"];
  if (stageId === "select") return actualModels.length ? actualModels : [strongModel];
  if (stageId === "research") return ["Sitemap + safe HTTP", ...(actualModels.length ? actualModels : [fastModel])];
  if (stageId === "screenshot_capture") return ["Playwright Chromium", ...(actualModels.length ? actualModels : [`${providerName(tools.llm_provider)} vision · ${tools.vision_model || "vision model"}`])];
  return [...(actualModels.length ? actualModels : [strongModel]), "Evidence verification", "HTML + Markdown renderer"];
}

function renderRunDetails(run) {
  const stagesById = new Map((run.stages || []).map(stage => [stage.stage, stage]));
  const selection = stagesById.get("select");
  const discovery = stagesById.get("discover");
  const validation = stagesById.get("validate");
  const alternativeScope = discovery?.output_json?.comparison_scope === "category_alternatives"
    || validation?.output_json?.comparison_scope === "category_alternatives";
  const selectionFailed = selection?.status === "failed" || selection?.output_json?.insufficient_candidates;
  const selectionIndex = runStages.findIndex(stage => stage.id === "select");
  const effectiveStatus = (definition, index) => {
    if (selectionFailed && index === selectionIndex) return "failed";
    if (selectionFailed && index > selectionIndex) return "pending";
    return stagesById.get(definition.id)?.status || (run.status === "completed" ? "completed" : "pending");
  };
  const completed = runStages.filter((stage, index) => effectiveStatus(stage, index) === "completed").length;
  const active = runStages.find((stage, index) => effectiveStatus(stage, index) === "running");
  const failedStages = (run.stages || []).filter(stage => stage.status === "failed").map(stage => stageTitle(stage.stage));
  if (selectionFailed && !failedStages.includes(stageTitle("select"))) failedStages.push(stageTitle("select"));
  const progressLabel = active ? `Now: ${active.title}`
    : failedStages.length ? `Failed: ${failedStages.join(", ")}` : run.status;
  const progress = Math.round((completed + (active ? 0.35 : 0)) / runStages.length * 100);
  const stageRows = runStages.map((definition, index) => {
    const stage = stagesById.get(definition.id);
    const status = effectiveStatus(definition, index);
    const icon = status === "running" ? `<span class="stage-spinner" aria-label="Loading"></span>`
      : status === "completed" ? `<span class="stage-check">✓</span>`
      : status === "failed" ? `<span class="stage-failed">!</span>`
      : `<span class="stage-number">${index + 1}</span>`;
    const tools = stageTools(definition.id, run, run.decisions || []);
    const screenshotProducts = stage?.output_json?.products || [];
    const screenshotCount = screenshotProducts.reduce((total, item) => total + Number(item.screenshot_count || 0), 0);
    const screenshotErrors = screenshotProducts.flatMap(item => item.screenshot_errors || []);
    const screenshotDetail = definition.id === "screenshot_capture" && stage?.status && stage.status !== "pending"
      ? `${screenshotCount} images captured and reviewed${screenshotErrors.length ? ` · ${screenshotErrors[0]}` : ""}` : null;
    const detail = stage?.error || screenshotDetail || (definition.id === "validate" && alternativeScope
      ? validation?.output_json?.scope_note || discovery?.output_json?.scope_note || "Using current category alternatives; recent launches were not sufficiently verified."
      : selectionFailed && index === selectionIndex
      ? stage?.output_json?.reasoning || "Fewer than two new validated product sites were available."
      : selectionFailed && index > selectionIndex ? "Not run: comparison selection did not find two eligible products."
      : stage?.duration_seconds ? `Completed in ${Number(stage.duration_seconds).toFixed(1)} sec` : definition.description);
    return `<div class="pipeline-stage stage-${escapeHtml(status)}"><div class="stage-marker">${icon}</div><div class="pipeline-stage-content"><div class="pipeline-stage-heading"><b>${escapeHtml(definition.title)}</b>${statusPill(status)}</div><small>${escapeHtml(detail)}</small><div class="tool-chips">${tools.map(tool => `<span class="tool-chip">${escapeHtml(tool)}</span>`).join("")}</div></div></div>`;
  }).join("");
  const decisions = (run.decisions || []).map(call => `<div class="decision"><b>${escapeHtml(call.stage)}</b> · ${escapeHtml(providerName(call.provider))} · ${escapeHtml(call.model)} · ${Number(call.input_tokens || 0) + Number(call.output_tokens || 0)} tokens · $${Number(call.cost_usd || 0).toFixed(4)}<br>${escapeHtml(call.reasoning || "Structured response recorded")}</div>`).join("");
  const runInputTokens = (run.decisions || []).reduce((total, call) => total + Number(call.input_tokens || 0), 0);
  const runOutputTokens = (run.decisions || []).reduce((total, call) => total + Number(call.output_tokens || 0), 0);
  const report = state.reports.find(item => Number(item.id) === Number(run.report_id));
  const reportAction = report
    ? `<button class="primary-button run-report-button" data-report="${report.id}">Open report for this run →</button>`
    : ["completed", "partial", "failed"].includes(run.status)
      ? `<p class="run-report-empty">This run did not generate a comparison report.${run.error ? ` ${escapeHtml(run.error)}` : ""}</p>`
      : `<p class="run-report-empty">The report link will appear here when the comparison is ready.</p>`;
  const runActions = ["pending", "running"].includes(run.status)
    ? `<button class="secondary-button" data-run-action="pause">Pause</button><button class="danger-button" data-run-action="cancel">Cancel</button>`
    : run.status === "paused"
      ? `<button class="primary-button" data-run-action="resume">Resume</button><button class="danger-button" data-run-action="cancel">Cancel</button>`
      : ["failed", "partial", "cancelled"].includes(run.status)
        ? `<button class="primary-button" data-run-action="retry">Retry run</button>`
      : "";
  const scroll = $("#runDetails").scrollTop;
  $("#runDetails").innerHTML = `<div class="dialog-body">
    <div class="run-progress-summary"><div><b>${completed} of ${runStages.length} steps complete</b><span class="${failedStages.length ? "run-failed-stage" : ""}">${escapeHtml(progressLabel)}</span></div><div class="run-summary-actions">${runActions}${statusPill(run.status)}</div></div>
    <div class="progress-track" role="progressbar" aria-valuenow="${progress}" aria-valuemin="0" aria-valuemax="100"><span style="width:${progress}%"></span></div>
    <p class="run-meta">Started ${prettyDate(run.started_at)} · Cost $${Number(run.cost_usd || 0).toFixed(4)} · ${formatTokens(runInputTokens + runOutputTokens)} tokens (${formatTokens(runInputTokens)} in · ${formatTokens(runOutputTokens)} out)</p>
    <h3 class="run-section-title">Pipeline steps and tools</h3><div class="pipeline-stages">${stageRows}</div>
    <div class="run-report-action">${reportAction}</div>
    <h3 class="run-section-title">Model calls and decisions</h3>${decisions || `<p class="empty-state">No model calls have been recorded yet. They will appear here as steps finish.</p>`}
    ${run.error ? `<p class="decision run-error">${escapeHtml(run.error)}</p>` : ""}</div>`;
  $("#runDetails").scrollTop = scroll;
}

async function loadRunDetails(id) {
  const run = await api(`/runs/${id}`);
  $("#runTitle").textContent = `Run #${run.run_id} · ${run.category}`;
  renderRunDetails(run);
}

async function updateRunState(action) {
  try {
    const result = await api(`/runs/${state.activeRunId}/${action}`, {method:"POST"});
    await refresh();
    if (action === "retry" && result?.run_id) showRun(result.run_id);
  } catch (error) { toast(error.message); }
}

async function showRun(id) {
  try {
    state.activeRunId = id;
    await loadRunDetails(id);
    if (!$("#runDialog").open) $("#runDialog").showModal();
  } catch (error) { toast(error.message); }
}
function showReport(id) {
  const report = state.reports.find(item => String(item.id) === String(id));
  if ($("#runDialog").open) $("#runDialog").close();
  state.activeRunId = null;
  $("#reportTitle").textContent = `Product comparison #${id}`;
  $("#reportDate").textContent = prettyDate(report?.created_at);
  $("#reportFrame").src = `/reports/${id}`;
  $("#markdownLink").href = `/reports/${id}?format=md`;
  $("#reportDialog").showModal();
}

document.addEventListener("DOMContentLoaded", () => {
  if ($("#today")) $("#today").textContent = new Date().toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"});
  if ($("#refreshButton")) $("#refreshButton").addEventListener("click", refresh);
  if ($("#startRun")) $("#startRun").addEventListener("click", () => { $("#categorySelect").focus(); window.scrollTo({top:0,behavior:"smooth"}); });
  if ($("#launchRun")) $("#launchRun").addEventListener("click", startRun);
  if ($("#categoryForm")) $("#categoryForm").addEventListener("submit", addCategory);
  document.querySelectorAll("[data-route]").forEach(link => link.addEventListener("click", event => { event.preventDefault(); navigate(link.dataset.route); }));
  if ($("#pageRefreshRuns")) $("#pageRefreshRuns").addEventListener("click", refresh);
  if ($("#runFilters")) $("#runFilters").addEventListener("click", event => { const filter = event.target.closest("[data-filter]")?.dataset.filter; if (filter) { document.querySelectorAll(".filter-tab").forEach(tab => tab.classList.toggle("active", tab.dataset.filter === filter)); renderAllRuns(filter); } });
  if ($("#pageCategoryForm")) $("#pageCategoryForm").addEventListener("submit", event => addCategory(event, "pageNewCategory"));
  if ($("#pageOpenKey")) $("#pageOpenKey").addEventListener("click", () => { $("#adminKey").value = adminKey(); $("#keyDialog").showModal(); });
  if ($("#saveKey")) $("#saveKey").addEventListener("click", () => { localStorage.setItem("cognivexAdminKey", $("#adminKey").value); toast("Admin key saved in this browser."); });
  document.querySelectorAll(".close-dialog").forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  if ($("#runDialog")) $("#runDialog").addEventListener("close", () => { state.activeRunId = null; });
  if ($("#runDetails")) $("#runDetails").addEventListener("click", event => {
    const button = event.target.closest("[data-report]");
    if (button) showReport(button.dataset.report);
    const action = event.target.closest("[data-run-action]")?.dataset.runAction;
    if (action) updateRunState(action);
  });
  if ($("#viewAllRuns")) $("#viewAllRuns").addEventListener("click", () => showRun(state.runs[0]?.id));
  window.addEventListener("popstate", () => { updateRouteChrome(window.location.pathname); renderWorkspacePage(window.location.pathname); });
  updateRouteChrome(window.location.pathname); renderWorkspacePage(window.location.pathname);
  refresh();
});

function navigate(path) { window.history.pushState({}, "", path); updateRouteChrome(path); renderWorkspacePage(path); }
