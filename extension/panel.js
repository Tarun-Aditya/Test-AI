// The side panel: paste a scenario (or record a flow), run it on the tab you are on, watch it, fix what did not work. The hub runs it; this is the
// person's window on it. All the work happens in the service worker and the hub.
const $ = (id) => document.getElementById(id);
const ask = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: chrome.runtime.lastError ? chrome.runtime.lastError.message : "No answer" })));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let runId = null, lastRun = null, tabId = null, previewed = null, startedAt = 0;
let current = { steps: [], data: {}, sensitive: [], name: "" };       // what the last run ran: the steps the person can fix and run again
let paused = false, hubBase = "", picking = null;

// a short message for screen readers (progress, results)
function announce(text) { const l = $("live"); l.textContent = ""; setTimeout(() => { l.textContent = text; }, 30); }

// ------------------------------------------------------------------------------------------------ the tab we automate
async function refreshTarget() {
  const r = await ask({ type: "target" });
  const t = r.ok && r.tab;
  tabId = t ? t.id : null;
  $("ttitle").textContent = t ? (t.title || t.url) : "No website tab found";
  $("turl").textContent = t ? t.url : "Open the site you want to test in a tab.";
  $("tdot").className = "dot " + (t && t.ok ? "ok" : "bad");
  if (t && !t.ok) $("turl").textContent = t.url + " (Chrome doesn't let extensions run here)";
  return t;
}
chrome.tabs.onActivated.addListener(() => { if (!runId) refreshTarget(); });
chrome.tabs.onUpdated.addListener((id, info) => { if (!runId && (info.title || info.url || info.status === "complete")) refreshTarget(); });

// ------------------------------------------------------------------------------------------------ the hub: connect, sign in
async function refreshHub() {
  const r = await ask({ type: "health" });
  const s = (await ask({ type: "settings" })).settings || {};
  hubBase = s.hub || hubBase;
  const line = $("hubline"), setup = $("setup");
  $("hubaddr").value = s.hub || $("hubaddr").value;
  if (!r.ok) {
    line.className = "hub bad"; line.textContent = "Not connected to a hub.";
    setup.hidden = false; $("signin").hidden = true;
    const m = $("setupmsg"); m.className = "setupmsg bad"; m.textContent = r.error || "Can't reach the hub.";
    return false;
  }
  const h = r.health || {};
  const needsSignIn = h.auth && !("llm" in h);
  line.className = "hub";
  if (needsSignIn) {
    line.innerHTML = `Hub <b>${esc(s.hub)}</b> · sign in to use it`;
    setup.hidden = false; $("signin").hidden = false; $("setupmsg").className = "setupmsg"; $("setupmsg").textContent = "Connected. This hub has accounts: sign in below.";
    return true;
  }
  line.innerHTML = `Hub <b>${esc(s.hub)}</b> · connected`;
  setup.hidden = true;
  return true;
}
$("hubconnect").onclick = async () => {
  const hub = $("hubaddr").value.trim().replace(/\/+$/, "");
  try { new URL(hub); } catch (e) { const m = $("setupmsg"); m.className = "setupmsg bad"; m.textContent = "That is not a web address (try http://localhost:8000)."; return; }
  $("setupmsg").className = "setupmsg"; $("setupmsg").textContent = "Connecting…";
  await ask({ type: "set-hub", hub });
  const ok = await refreshHub();
  if (ok && $("setup").hidden) announce("Connected to the hub.");
};
$("slogin").onclick = async () => {
  const r = await ask({ type: "signin", username: $("suser").value, password: $("spass").value });
  $("spass").value = "";
  const m = $("setupmsg");
  if (!r.ok) { m.className = "setupmsg bad"; m.textContent = r.error; return; }
  m.className = "setupmsg ok"; m.textContent = `Signed in as ${r.user.username}.`;
  await refreshHub();
};

// ------------------------------------------------------------------------------------------------ data: a table over the text
function parseData() {
  const data = {}, sensitive = [];
  for (const raw of $("data").value.split("\n")) {
    const m = raw.match(/^\s*([\w.\-]+)\s*(\*?)\s*=\s*(.*?)\s*$/);
    if (!m) continue;
    data[m[1]] = m[3]; if (m[2]) sensitive.push(m[1]);
  }
  return { data, sensitive };
}
function renderDataTable() {
  const rows = [];
  for (const raw of $("data").value.split("\n")) { const m = raw.match(/^\s*([\w.\-]+)\s*(\*?)\s*=\s*(.*?)\s*$/); if (m) rows.push({ k: m[1], secret: !!m[2], v: m[3] }); }
  const body = $("datatable").querySelector("tbody");
  body.innerHTML = rows.map((r, i) => `<tr data-i="${i}"><td><input class="dk" value="${esc(r.k)}" aria-label="Name" spellcheck="false"></td><td><input class="dv" type="${r.secret ? "password" : "text"}" value="${esc(r.v)}" aria-label="Value of ${esc(r.k)}" spellcheck="false" autocomplete="off"></td><td><input class="ds" type="checkbox" ${r.secret ? "checked" : ""} aria-label="${esc(r.k)} is a secret"></td><td><button class="x" type="button" aria-label="Remove ${esc(r.k)}">✕</button></td></tr>`).join("");
}
function tableToText() {
  const lines = [];
  for (const tr of $("datatable").querySelectorAll("tbody tr")) {
    const k = tr.querySelector(".dk").value.trim(); if (!k) continue;
    lines.push(`${k}${tr.querySelector(".ds").checked ? "*" : ""} = ${tr.querySelector(".dv").value}`);
  }
  $("data").value = lines.join("\n"); persistDraft();
}
$("datatable").addEventListener("input", tableToText);
$("datatable").addEventListener("change", (e) => { if (e.target.classList.contains("ds")) { const tr = e.target.closest("tr"); tr.querySelector(".dv").type = e.target.checked ? "password" : "text"; tableToText(); } });
$("datatable").addEventListener("click", (e) => { const b = e.target.closest(".x"); if (!b) return; b.closest("tr").remove(); tableToText(); });
$("dataadd").onclick = () => { $("data").value = ($("data").value.trim() ? $("data").value.replace(/\s*$/, "\n") : "") + "name = "; renderDataTable(); const last = $("datatable").querySelectorAll(".dk"); const f = last[last.length - 1]; if (f) { f.focus(); f.select(); } };
$("data").addEventListener("input", renderDataTable);
$("astextbtn").onclick = () => { const open = $("astext").hidden; $("astext").hidden = !open; $("astextbtn").setAttribute("aria-expanded", String(open)); };
function persistDraft() { chrome.storage.session.set({ text: $("text").value, dataLines: $("data").value, runname: $("runname").value }).catch(() => {}); }

// ------------------------------------------------------------------------------------------------ rendering
function showError(text) { const m = $("msg"); m.textContent = text; m.hidden = !text; if (text) announce(text); }

// the notes ("Check before you trust it"): a quoted wording in a note takes you to that wording in your text
function showNotes(notes, ignored) {
  const n = $("notes"); n.hidden = !(notes && notes.length) && !(ignored && ignored.length);
  const line = (x) => esc(x).replace(/(“[^”]{2,}”|&quot;[^&]{2,}?&quot;)/g, (m) => `<a href="#" class="noteline" data-q="${m.replace(/^(“|&quot;)|(”|&quot;)$/g, "")}">${m}</a>`);
  n.innerHTML = (notes && notes.length ? `<b>Check before you trust it</b><ul>${notes.map((x) => `<li>${line(x)}</li>`).join("")}</ul>` : "")
    + (ignored && ignored.length ? `<div class="ign">Left out as background: ${ignored.map((x) => "“" + esc(x) + "”").join(" · ")}</div>` : "");
}
$("notes").addEventListener("click", (e) => {
  const a = e.target.closest(".noteline"); if (!a) return; e.preventDefault();
  const q = a.dataset.q.replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">"), t = $("text"), i = t.value.toLowerCase().indexOf(q.toLowerCase());
  t.focus(); if (i >= 0) t.setSelectionRange(i, i + q.length);
});

function renderPlan(plan, { selectable = false } = {}) {
  $("steps").innerHTML = plan.map((p, i) => `<li class="step pending" id="s${i}" data-i="${i}">${selectable ? `<input type="checkbox" class="sel" checked aria-label="Run step ${i + 1}">` : `<span class="ic">${i + 1}</span>`}<span class="tx">${esc(p.raw || p.action)}</span><span class="pill" hidden></span>${p.origin ? `<span class="meta" style="grid-column:2/4">from your text: “${esc(p.origin)}”</span>` : ""}</li>`).join("");
  $("steptools").hidden = !plan.length; applyFilters();
}
function stepEl(i) { return $("s" + i); }
function setStep(i, status, extra = {}) {
  const li = stepEl(i); if (!li) return;
  li.className = "step " + (extra.paused ? "paused " : "") + status;
  let ic = li.querySelector(".ic");
  if (!ic) { ic = document.createElement("span"); ic.className = "ic"; const sel = li.querySelector(".sel"); if (sel) sel.replaceWith(ic); else li.prepend(ic); }
  ic.textContent = { running: "●", passed: "✓", failed: "✕", skipped: "–" }[status] || (i + 1);
  ic.setAttribute("aria-label", { running: "running", passed: "passed", failed: "failed", skipped: "skipped" }[status] || "not run yet");
  if (extra.tab > 1) { const p = li.querySelector(".pill"); p.hidden = false; p.textContent = "Tab " + extra.tab; }
  li.querySelectorAll(".err,img,.mt,.tools,.shot-toggle").forEach((x) => x.remove());
  if (extra.ms != null && status !== "running") { const s = document.createElement("span"); s.className = "meta mt"; s.style.gridColumn = "2/4"; s.textContent = (extra.ms / 1000).toFixed(1) + " s" + (extra.picked ? " · element: " + extra.picked : ""); li.append(s); }
  if (extra.error) { const d = document.createElement("div"); d.className = "err"; d.textContent = extra.error; li.append(d); }
  if (extra.shot) {
    const im = document.createElement("img"); im.src = extra.shot; im.alt = `Screenshot after step ${i + 1}`;
    im.tabIndex = 0; im.setAttribute("role", "button");
    im.onclick = () => openZoom(extra.shot, im);
    im.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openZoom(extra.shot, im); } };
    const show = status === "failed" || $("allshots").checked;
    im.hidden = !show; li.append(im);
    const tg = document.createElement("button"); tg.className = "btn small shot-toggle"; tg.type = "button"; tg.textContent = show ? "Hide screenshot" : "Show screenshot"; tg.setAttribute("aria-expanded", String(show));
    tg.onclick = () => { im.hidden = !im.hidden; tg.textContent = im.hidden ? "Show screenshot" : "Hide screenshot"; tg.setAttribute("aria-expanded", String(!im.hidden)); };
    li.append(tg);
  }
  if (status === "failed") addFailTools(li, i);
  if (status === "running") li.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  applyFilters();
}
function applyFilters() {
  const only = $("onlyfailed").checked;
  for (const li of $("steps").querySelectorAll("li.step")) li.hidden = only && !li.classList.contains("failed");
  for (const im of $("steps").querySelectorAll("img")) { const li = im.closest("li"); if (li && !li.classList.contains("failed")) { im.hidden = !$("allshots").checked && !im.dataset.open; const tg = li.querySelector(".shot-toggle"); if (tg) { tg.textContent = im.hidden ? "Show screenshot" : "Hide screenshot"; tg.setAttribute("aria-expanded", String(!im.hidden)); } } }
}
$("onlyfailed").onchange = applyFilters;
$("allshots").onchange = () => { for (const im of $("steps").querySelectorAll("img")) { im.dataset.open = $("allshots").checked ? "1" : ""; } applyFilters(); };

// ---- a picture, large (a dialog: focus goes in, Esc or Close returns it)
let zoomFrom = null;
function openZoom(src, from) { zoomFrom = from || document.activeElement; $("zoom").querySelector("img").src = src; $("zoom").hidden = false; $("zoomclose").focus(); }
function closeZoom() { $("zoom").hidden = true; if (zoomFrom && zoomFrom.focus) zoomFrom.focus(); }
$("zoomclose").onclick = closeZoom;
$("zoom").onclick = (e) => { if (e.target === $("zoom")) closeZoom(); };

function setBusy(on) {
  $("run").hidden = on; $("preview").disabled = on; $("runctl").hidden = !on; $("text").readOnly = on; $("stepmode").disabled = on;
  if (!on) { paused = false; syncPause(); }
  $("steps").setAttribute("aria-busy", String(on));
}
function syncPause() {
  $("pause").textContent = paused ? "▶ Resume" : "Ⅱ Pause"; $("pause").setAttribute("aria-pressed", String(paused)); $("next").hidden = !paused;
}
function tabsList(list) {
  const el = $("tabs"); el.hidden = list.length < 2;
  el.innerHTML = list.map((t, i) => `<div class="tab"><b>Tab ${i + 1}</b><span>${esc(t.title || t.url)}</span></div>`).join("");
}

// ------------------------------------------------------------------------------------------------ events from the run
const seenTabs = [];
function onEvent(ev) {
  if (ev && ev.type === "report_done" && ev.runId === lastRun) return reportLine({ run: ev.hubRun, ok: ev.ok, error: ev.error });      // arrives after the run ended
  if (ev && ev.type === "picked") return onPicked(ev.el);
  if (!ev || !ev.runId || ev.runId !== runId) return;
  switch (ev.type) {
    case "run_started": seenTabs.length = 0; seenTabs.push(ev.tab); tabsList(seenTabs); announce("The run has started."); break;
    case "step_started": setStep(ev.index, "running"); break;
    case "step_finished":
      setStep(ev.index, ev.status, { error: ev.error, shot: ev.shot, ms: ev.ms, picked: ev.picked, tab: ev.tab });
      if (ev.status === "failed") announce(`Step ${ev.index + 1} failed. ${ev.error || ""}`);
      break;
    case "paused": { paused = true; syncPause(); const li = stepEl(ev.index); if (li) li.className = "step paused pending"; announce(`Paused before step ${ev.index + 1}.`); break; }
    case "resumed": paused = false; syncPause(); break;
    case "tab": seenTabs[ev.index - 1] = ev.tab; tabsList(seenTabs); break;
    case "debugger_closed": showError("You closed Chrome's “debugging this browser” bar, so the run was stopped. Press Run to start again, and leave that bar alone while it runs."); break;
    case "run_finished": finish(ev); break;
  }
}
chrome.runtime.onMessage.addListener(onEvent);

async function reportLine(r) {
  const el = $("reportline"); el.hidden = false;
  if (!hubBase) hubBase = ((await ask({ type: "settings" })).settings || {}).hub || "";
  const url = r.run ? `${hubBase}/#/runs/${encodeURIComponent(r.run)}` : "";
  if (r.error && !r.run) el.innerHTML = `Not reported to the hub: ${esc(r.error)}`;
  else if (r.saving) el.innerHTML = `Reporting to the hub… <a href="${esc(url)}" target="_blank" rel="noopener">live view</a>`;
  else { el.innerHTML = `${r.ok ? "Saved on the hub:" : "Saved on the hub (some of it didn't arrive):"} <a href="${esc(url)}" target="_blank" rel="noopener">report, video and replay</a>`; showResults(r.run, url); }
}
let resultRun = null;
function showResults(run, url) {
  resultRun = run; $("results").hidden = false; $("reportlink").href = url;
  $("video").hidden = true; $("video").removeAttribute("src"); $("watch").hidden = false;
}
$("watch").onclick = async () => {
  if (!resultRun) return; $("watch").disabled = true; $("watch").textContent = "Loading the video…";
  try {
    const r = await fetch(`${hubBase}/artifacts/${encodeURIComponent(resultRun)}/video.webm`, { credentials: "include" });
    if (!r.ok) throw new Error("The hub has no video for this run.");
    $("video").src = URL.createObjectURL(await r.blob()); $("video").hidden = false; $("watch").hidden = true; $("video").focus();
  } catch (e) { showError(String(e.message || e)); }
  $("watch").disabled = false; $("watch").textContent = "▶ Watch the video";
};
$("savescript").onclick = async () => {
  if (!resultRun) return;
  const r = await ask({ type: "save-run", id: resultRun, name: $("runname").value.trim() || current.name || undefined });
  if (!r.ok) return showError(r.error);
  $("savescript").textContent = "Saved ✓"; announce("Saved to your scripts on the hub."); setTimeout(() => { $("savescript").textContent = "Save as a script"; }, 2000);
};

function finish(ev) {
  setBusy(false);
  const b = $("banner"); b.hidden = false; b.className = "banner " + ev.status;
  const secs = ((Date.now() - startedAt) / 1000).toFixed(1);
  b.innerHTML = `${{ passed: "✓ Passed", failed: "✕ Failed", cancelled: "■ Stopped" }[ev.status] || esc(ev.status)}<small>${secs} s${ev.tabs && ev.tabs.length > 1 ? " · " + ev.tabs.length + " tabs" : ""}</small>`;
  announce({ passed: "The run passed.", failed: "The run failed.", cancelled: "The run was stopped." }[ev.status] || "The run finished.");
  if (ev.error) showError(ev.error);
  if (ev.tabs) tabsList(ev.tabs);
  if (ev.status === "failed") { const bad = $("steps").querySelector("li.failed"); if (bad) bad.querySelector("button")?.focus(); }
  runId = null; refreshTarget();
}

// ------------------------------------------------------------------------------------------------ asking before it touches a site
let confirmFrom = null;
function confirmDialog({ title, bodyHtml, okLabel = "Continue", checkLabel = "" }) {
  return new Promise((resolve) => {
    confirmFrom = document.activeElement;
    $("confirmh").textContent = title; $("confirmb").innerHTML = bodyHtml; $("confirmyes").textContent = okLabel;
    $("confirmalways").hidden = !checkLabel; $("confirmcheck").checked = false; $("confirmchecklabel").textContent = checkLabel;
    $("confirm").hidden = false; $("confirmyes").focus();
    const done = (ok) => { $("confirm").hidden = true; document.removeEventListener("keydown", onKey, true); if (confirmFrom && confirmFrom.focus) confirmFrom.focus(); resolve({ ok, checked: $("confirmcheck").checked }); };
    const onKey = (e) => {
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); done(false); }
      else if (e.key === "Tab") { const f = [...$("confirm").querySelectorAll("button, input")].filter((x) => !x.closest("[hidden]")); if (!f.length) return; const i = f.indexOf(document.activeElement); const n = e.shiftKey ? (i <= 0 ? f.length - 1 : i - 1) : (i === f.length - 1 ? 0 : i + 1); e.preventDefault(); f[n].focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    $("confirmyes").onclick = () => done(true); $("confirmno").onclick = () => done(false);
  });
}
const RISKY = /\b(delete|remove|erase|cancel (?:my |the )?(?:subscription|account|order|plan)|close (?:my )?account|unsubscribe|terminate|pay|purchase|buy|place (?:the |an? )?order|submit (?:the )?order|confirm (?:the )?(?:order|payment|purchase)|checkout|transfer|withdraw|send (?:the |an? )?(?:message|email|payment)|publish|post (?:it|the)|drop)\b/i;
function riskySteps(steps) { return steps.map((s, i) => ({ i, t: s.raw || "" })).filter((x) => RISKY.test(x.t) && !/^verify|^check that/i.test(x.t)).slice(0, 6); }
async function mayRun(tab, steps) {
  const s = await ask({ type: "site-status", url: tab.url });
  if (!s.ok || !s.ask) return true;                                   // asking is switched off in Settings
  if (!s.allowed) {
    const r = await confirmDialog({ title: "Run on this site?", bodyHtml: `<p>Autopilot will click and type on <b>${esc(s.origin)}</b> as you, exactly like a person would. Make sure this is a site (and an account) where that is safe.</p>`, okLabel: "Run here", checkLabel: `Always allow ${s.origin}` });
    if (!r.ok) return false;
    if (r.checked) await ask({ type: "site-allow", origin: s.origin });
  }
  const risky = riskySteps(steps);
  if (risky.length) {
    const r = await confirmDialog({ title: "This scenario does things that are hard to undo", bodyHtml: `<p>It will:</p><ul>${risky.map((x) => `<li>${esc(x.t)}</li>`).join("")}</ul><p>Run it on a test account, not a real one.</p>`, okLabel: "Run it anyway" });
    if (!r.ok) return false;
  }
  return true;
}

// ------------------------------------------------------------------------------------------------ run
async function previewNow({ silent = false } = {}) {
  const text = $("text").value.trim(); if (!text) { if (!silent) showError("Paste a scenario first."); return null; }
  const r = await ask({ type: "preview", text, ...parseData() });
  if (!r.ok) { if (!silent) showError(r.error); return null; }
  previewed = { text, steps: r.test.steps, data: r.test.data, sensitive: r.test.sensitive, name: r.test.name };
  $("runname").placeholder = r.test.name ? `Name this run (now: ${r.test.name})` : "Name this run (optional)";
  renderPlan(r.test.steps.map((s) => ({ raw: s.raw, origin: s.origin, action: s.action })), { selectable: true });
  showNotes(r.notes, r.ignored);
  if (r.unparsed && r.unparsed.length) showError("Not understood: " + r.unparsed.join(" · "));
  $("tdot").className = "dot " + (r.tab.ok ? "ok" : "bad");
  return r;
}
$("preview").onclick = async () => { showError(""); $("banner").hidden = true; $("results").hidden = true; $("preview").disabled = true; await previewNow(); $("preview").disabled = false; };

// run the typed scenario, a replay, or (opts.steps) the steps handed in: a fixed run from a failed step, a recording
async function startRun(opts = {}) {
  showError(""); $("banner").hidden = true; $("tabs").hidden = true; $("reportline").hidden = true; $("results").hidden = true;
  const t = await refreshTarget(); if (!t) return showError("Open the website you want to test in a tab first.");
  const { data, sensitive } = parseData();
  let steps, d, sens, sourceRun = null, name = $("runname").value.trim();
  if (opts.steps) { steps = opts.steps; d = { ...(opts.data || {}), ...data }; sens = [...new Set([...(opts.sensitive || []), ...sensitive])]; name = name || opts.name || ""; }
  else {
    const text = $("text").value.trim(); if (!text) return showError("Paste a scenario first.");
    if (!(previewed && previewed.text === text)) { const pr = await previewNow(); if (!pr) return; if (pr.unparsed && pr.unparsed.length) return; }
    steps = previewed.steps; d = { ...previewed.data, ...data }; sens = [...new Set([...previewed.sensitive, ...sensitive])]; sourceRun = previewed.sourceRun || null; name = name || previewed.name || "";
    const boxes = [...$("steps").querySelectorAll("li.step .sel")];
    if (boxes.length === steps.length && boxes.some((b) => !b.checked)) { steps = steps.filter((_, i) => boxes[i].checked); sourceRun = null; }      // run only the ticked steps
    if (!steps.length) return showError("Tick at least one step to run.");
  }
  if (!(await mayRun(t, steps))) return;
  setBusy(true); startedAt = Date.now();
  const r = await ask({ type: "run", tabId: t.id, steps, data: d, sensitive: sens, sourceRun, name: name || undefined, noOpen: !!opts.noOpen, config: { startPaused: $("stepmode").checked } });
  if (!r.ok) { setBusy(false); return showError(r.error); }
  runId = lastRun = r.runId; current = { steps: r.steps || steps, data: d, sensitive: sens, name };
  renderPlan(r.plan); showNotes(r.notes, r.ignored);
  if (r.hubRun) reportLine({ saving: true, run: r.hubRun }); else if (r.reportError) reportLine({ error: r.reportError });
  persistDraft(); firstRunNote();
  if ($("stepmode").checked) { paused = true; syncPause(); }
}
$("run").onclick = () => startRun();

$("stop").onclick = () => { if (runId) ask({ type: "stop", runId }); $("stop").disabled = true; setTimeout(() => { $("stop").disabled = false; }, 1500); };
$("pause").onclick = async () => { if (!runId) return; const r = await ask({ type: "run-control", runId, action: paused ? "resume" : "pause" }); if (!r.ok) showError(r.error); else if (!paused) announce("Pausing before the next step."); };
$("next").onclick = async () => { if (!runId) return; const r = await ask({ type: "run-control", runId, action: "step" }); if (!r.ok) showError(r.error); };
$("gear").onclick = () => chrome.runtime.openOptionsPage();

// the text changed: what was previewed no longer applies; if there were notes to fix, show the new reading a moment after you stop typing
let reprevTimer = null;
$("text").oninput = () => {
  previewed = null; persistDraft(); clearTimeout(reprevTimer);
  if (!$("notes").hidden) reprevTimer = setTimeout(() => { if (!runId) previewNow({ silent: true }); }, 900);
};
$("runname").oninput = persistDraft;

// ---- the first run: what Chrome's debugging bar is
async function firstRunNote() {
  const s = await chrome.storage.local.get({ seenBarNote: false });
  if (!s.seenBarNote) $("barnote").hidden = false;
}
$("barok").onclick = () => { $("barnote").hidden = true; chrome.storage.local.set({ seenBarNote: true }); };
$("welcomeok").onclick = () => { $("welcome").hidden = true; chrome.storage.local.set({ welcomed: true }); };

// ------------------------------------------------------------------------------------------------ fixing a failed step: show the right element
function addFailTools(li, i) {
  const d = document.createElement("div"); d.className = "tools";
  d.innerHTML = `<button class="btn small" type="button" data-act="pick">◎ Pick the right element</button><button class="btn small" type="button" data-act="rerun" hidden>▶ Run again from here</button>`;
  li.append(d);
}
$("steps").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const li = b.closest("li"), i = +li.dataset.i;
  if (b.dataset.act === "pick") {
    const t = await refreshTarget(); if (!t) return showError("Open the page first.");
    picking = { i, li }; showError("");
    const r = await ask({ type: "pick-start", tabId: t.id }); if (!r.ok) { picking = null; return showError(r.error); }
    b.textContent = "Click it on the page… (Esc cancels)"; b.disabled = true; announce("Click the right element on the page. Press Escape to cancel.");
  } else if (b.dataset.act === "rerun") {
    startRun({ steps: current.steps.slice(i), data: current.data, sensitive: current.sensitive, noOpen: true, name: (current.name || "Run") + " (fixed)" });
  }
});
async function onPicked(el) {
  if (!picking) return; const { i, li } = picking; picking = null;
  const pickBtn = li.querySelector('[data-act="pick"]'); if (pickBtn) { pickBtn.disabled = false; pickBtn.textContent = "◎ Pick the right element"; }
  if (!el) return announce("Picking cancelled.");
  const step = current.steps[i]; if (!step) return showError("That step is not available any more.");
  const r = await ask({ type: "step-from-element", step, el });
  if (!r.ok) return showError(r.error);
  current.steps[i] = r.step;
  const note = document.createElement("div"); note.className = "meta"; note.style.gridColumn = "2/4"; note.textContent = `Now uses: ${r.described_as}. Run again from here to try it.`;
  const anchor = li.querySelector(".mt"); if (anchor) anchor.after(note); else li.append(note);
  const rr = li.querySelector('[data-act="rerun"]'); if (rr) { rr.hidden = false; rr.focus(); }
  announce(`Step ${i + 1} now uses ${r.described_as}.`);
}
document.addEventListener("keydown", async (e) => {
  if (e.key === "Escape" && picking) { picking = null; await ask({ type: "pick-stop" }); const b = document.querySelector('[data-act="pick"]:disabled'); if (b) { b.disabled = false; b.textContent = "◎ Pick the right element"; } announce("Picking cancelled."); }
  else if (e.key === "Escape" && !$("zoom").hidden) closeZoom();
  else if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !$("runview").hidden && !runId) { e.preventDefault(); $("run").click(); }
});

// ------------------------------------------------------------------------------------------------ from the hub
let hubData = { library: [], runs: [] }, hubShown = 10;
async function loadHubLists() {
  const box = $("hublists"); box.textContent = "Loading…";
  const r = await ask({ type: "hub-lists" });
  if (!r.ok) { box.textContent = r.error; return; }
  hubBase = hubBase || ((await ask({ type: "settings" })).settings || {}).hub || "";
  hubData = { library: r.library || [], runs: r.runs || [] }; hubShown = 10; renderHubLists();
}
function renderHubLists() {
  const q = $("hubsearch").value.trim().toLowerCase(), box = $("hublists"), has = (s) => !q || String(s || "").toLowerCase().includes(q);
  const libAll = hubData.library.filter((x) => has(x.test_name) || has(x.name)), runAll = hubData.runs.filter((x) => has(x.test_name));
  const lib = libAll.slice(0, hubShown).map((x) => `<div class="it"><span class="d" aria-hidden="true"></span><span class="nm" title="${esc(x.name)}">${esc(x.test_name || x.name)} · ${x.steps} steps</span><span></span><button class="btn" data-k="library" data-id="${esc(x.name)}" aria-label="Replay ${esc(x.test_name || x.name)}">▶ Replay</button></div>`).join("");
  const runs = runAll.slice(0, hubShown).map((x) => `<div class="it"><span class="d ${esc(x.status)}" role="img" aria-label="${esc(x.status)}"></span><span class="nm" title="${esc(x.test_name)}">${esc(x.test_name)}</span><a href="${esc(hubBase)}/#/runs/${encodeURIComponent(x.run_id)}" target="_blank" rel="noopener">report</a><button class="btn" data-k="run" data-id="${esc(x.run_id)}" aria-label="Replay ${esc(x.test_name)}">▶ Replay</button></div>`).join("");
  box.innerHTML = `<h3 class="h4">Saved scripts</h3>${lib || `<small>${q ? "No saved script matches." : "Nothing saved yet. Record a flow and press Save to hub."}</small>`}<h3 class="h4">Recent runs</h3>${runs || `<small>${q ? "No run matches." : "No runs yet."}</small>`}`;
  $("hubmore").hidden = !(libAll.length > hubShown || runAll.length > hubShown);
}
$("hubd").ontoggle = () => { if ($("hubd").open) loadHubLists(); };
$("hubsearch").oninput = () => { hubShown = 10; renderHubLists(); };
$("hubmore").onclick = () => { hubShown += 20; renderHubLists(); };
$("hublists").onclick = async (e) => {
  const b = e.target.closest("button[data-id]"); if (!b) return;
  showError(""); b.disabled = true;
  const r = await ask({ type: "replay-load", kind: b.dataset.k, id: b.dataset.id }); b.disabled = false;
  if (!r.ok) return showError(r.error);
  const text = r.test.steps.map((x) => x.raw).join("\n");
  $("text").value = text;
  previewed = { text, steps: r.test.steps, data: r.test.data || {}, sensitive: r.test.sensitive || [], sourceRun: b.dataset.k === "run" ? b.dataset.id : "library:" + b.dataset.id, name: r.test.name };
  if (r.needs && r.needs.length) {                      // a saved script doesn't keep its secrets: ask for them
    $("data").value = r.needs.map((k) => `${k}* = `).join("\n"); renderDataTable(); $("datad").open = true;
    const first = $("datatable").querySelector(".dv"); if (first) first.focus();
    return showError(`Enter ${r.needs.join(", ")} under Data, then press Run.`);
  }
  startRun();
};

// ------------------------------------------------------------------------------------------------ recorder
let recTest = null, recLive = false, recTimer = null, recPaused = false;
function setMode(m) {
  const rec = m === "rec";
  $("mrec").classList.toggle("on", rec); $("mrun").classList.toggle("on", !rec);
  $("mrec").setAttribute("aria-selected", String(rec)); $("mrun").setAttribute("aria-selected", String(!rec));
  $("recview").hidden = !rec; $("runview").hidden = rec; $("recout").hidden = !rec || !recTest;
  ["steps", "banner", "tabs", "notes", "steptools", "results", "reportline"].forEach((id) => { $(id).style.display = rec ? "none" : ""; });
}
$("mrun").onclick = () => setMode("run");
$("mrec").onclick = () => setMode("rec");

// the English a step is written as
function rawFor(s) {
  const t = s.target || "", v = s.value == null ? "" : s.value;
  switch (s.action) {
    case "type": case "append": return `type "${v}" into ${t}`;
    case "select": return `select "${v}" from ${t}`;
    case "assert_text": return `verify the page shows "${v}"`;
    case "click": return `click ${t}`;
    case "check": return `check ${t}`;
    case "uncheck": return `uncheck ${t}`;
    case "hover": return `hover over ${t}`;
    default: return s.raw;
  }
}
const MOVABLE_VALUE = new Set(["type", "append", "select", "assert_text"]);
let editing = null;                                      // { i, kind } the row whose inline form is open
function renderRec(editable) {
  const st = recTest ? recTest.steps : [];
  $("recout").hidden = !recTest; $("reccount").textContent = st.length ? `· ${st.length}` : "";
  $("recsteps").innerHTML = st.map((x, i) => {
    const tools = editable ? `<span class="rowtools" role="group" aria-label="Step ${i + 1}">
      <button data-op="up" data-i="${i}" title="Move up" aria-label="Move step ${i + 1} up" ${i === 0 ? "disabled" : ""}>↑</button>
      <button data-op="down" data-i="${i}" title="Move down" aria-label="Move step ${i + 1} down" ${i === st.length - 1 ? "disabled" : ""}>↓</button>
      <button data-op="edit" data-i="${i}" title="Edit" aria-label="Edit step ${i + 1}">✎</button>
      ${MOVABLE_VALUE.has(x.action) && x.value && !/^\$\{/.test(x.value) && x.action !== "assert_text" ? `<button data-op="var" data-i="${i}" title="Use a data value instead of this text" aria-label="Make step ${i + 1} use a data variable">{ }</button>` : ""}
      <button data-op="check" data-i="${i}" title="Add a check after this step" aria-label="Add a check after step ${i + 1}">✓+</button>
      <button class="x" data-op="del" data-i="${i}" title="Remove this step" aria-label="Remove step ${i + 1}">✕</button></span>` : "<span></span>";
    const form = editing && editing.i === i ? editForm(x, editing.kind) : "";
    return `<li><span class="n">${i + 1}</span><span>${esc(x.raw || x.action)}${x.locator ? `<small>finds it by ${esc(x.locator.strategy)}: ${esc(x.locator.role ? x.locator.role + " · " : "")}${esc(String(x.locator.value).slice(0, 50))}</small>` : ""}</span>${tools}${form}</li>`;
  }).join("");
  const f = $("recsteps").querySelector(".edit input"); if (f) f.focus();
}
function editForm(s, kind) {
  if (kind === "edit") return `<form class="edit" data-form="edit">${s.value != null && s.action !== "navigate" ? `<label class="sr-only" for="ev">Value</label><input id="ev" name="v" value="${esc(s.value)}" aria-label="Value">` : ""}${s.target ? `<label class="sr-only" for="et">Description</label><input id="et" name="t" value="${esc(s.target)}" aria-label="Which element">` : ""}<button class="btn small primary" type="submit">Save</button><button class="btn small" type="button" data-op="cancel">Cancel</button></form>`;
  if (kind === "var") return `<form class="edit" data-form="var"><label class="sr-only" for="vn">Variable name</label><input id="vn" name="n" placeholder="name, e.g. email" aria-label="Variable name" value="${/pass|secret|token|pin/i.test(s.target || "") ? "password" : ""}"><button class="btn small primary" type="submit">Use as data</button><button class="btn small" type="button" data-op="cancel">Cancel</button></form>`;
  return `<form class="edit" data-form="check"><label class="sr-only" for="ct">The page should show</label><input id="ct" name="c" placeholder="The page should show…" aria-label="The page should show"><button class="btn small primary" type="submit">Add check</button><button class="btn small" type="button" data-op="cancel">Cancel</button></form>`;
}
$("recsteps").addEventListener("click", (e) => {
  const b = e.target.closest("[data-op]"); if (!b || !recTest) return;
  const i = +b.dataset.i, st = recTest.steps, op = b.dataset.op;
  if (op === "del") { st.splice(i, 1); editing = null; }
  else if (op === "up" && i > 0) { [st[i - 1], st[i]] = [st[i], st[i - 1]]; announce(`Step moved up to ${i}.`); }
  else if (op === "down" && i < st.length - 1) { [st[i + 1], st[i]] = [st[i], st[i + 1]]; announce(`Step moved down to ${i + 2}.`); }
  else if (op === "edit" || op === "var" || op === "check") editing = { i, kind: op };
  else if (op === "cancel") editing = null;
  renderRec(!recLive);
});
$("recsteps").addEventListener("submit", (e) => {
  e.preventDefault(); const f = e.target.closest("form[data-form]"); if (!f || !editing) return;
  const i = editing.i, s = recTest.steps[i], fd = new FormData(f), kind = f.dataset.form;
  if (kind === "edit") { if (fd.has("v")) s.value = fd.get("v"); if (fd.has("t")) s.target = fd.get("t"); s.raw = rawFor(s); s.source = "user"; }
  else if (kind === "var") {
    const n = String(fd.get("n") || "").trim().replace(/[^\w.\-]/g, "_"); if (!n) return;
    recTest.data = recTest.data || {}; recTest.data[n] = s.value; s.value = "${" + n + "}"; s.raw = rawFor(s); s.source = "user";
    if (/pass|secret|token|pin/i.test(n)) recTest.sensitive = [...new Set([...(recTest.sensitive || []), n])];
    announce(`The value is now the data variable ${n}.`);
  } else if (kind === "check") {
    const c = String(fd.get("c") || "").trim(); if (!c) return;
    recTest.steps.splice(i + 1, 0, { action: "assert_text", target: null, value: c, raw: `verify the page shows "${c}"`, source: "user", fallbacks: [], optional: false });
  }
  editing = null; renderRec(!recLive);
});

async function recCompile(events, name) {
  const r = await ask({ type: "compile", events, name: name || $("recname").value || "Recorded flow" });
  if (!r.ok) { showError(r.error); return false; }
  recTest = r.test; showError(""); return r;
}
chrome.runtime.onMessage.addListener((m) => {
  if (!m || m.type !== "rec_changed" || !recLive) return;
  clearTimeout(recTimer);
  recTimer = setTimeout(async () => { const st = await ask({ type: "rec-state" }); if (st.ok && recLive && (await recCompile(st.events))) renderRec(false); }, 350);
});

function recUi(on) {
  recLive = on; $("recview").classList.toggle("rec-on", on);
  $("recstart").hidden = on; $("recstop").hidden = !on; $("recadd").hidden = !on; $("recpause").hidden = !on; $("recundo").hidden = !on;
  if (!on) { recPaused = false; syncRecPause(); }
}
function syncRecPause() { $("recpause").textContent = recPaused ? "● Resume" : "Ⅱ Pause"; $("recpause").setAttribute("aria-pressed", String(recPaused)); }
$("recstart").onclick = async () => {
  showError(""); recTest = null; editing = null; renderRec(false);
  const r = await ask({ type: "rec-start" }); if (!r.ok) return showError(r.error);
  recUi(true); announce("Recording. Use the page as you normally would.");
};
$("recpause").onclick = async () => { const r = await ask({ type: "rec-pause", on: !recPaused }); if (!r.ok) return showError(r.error); recPaused = r.paused; syncRecPause(); announce(recPaused ? "Recording paused." : "Recording resumed."); };
$("recundo").onclick = async () => { const r = await ask({ type: "rec-undo" }); if (!r.ok) return showError(r.error); const st = await ask({ type: "rec-state" }); if (st.ok && (await recCompile(st.events))) renderRec(false); announce("The last thing recorded was removed."); };
$("recstop").onclick = async () => {
  const r = await ask({ type: "rec-stop" }); recUi(false);
  if (!r.ok) return showError(r.error);
  if (!r.events.length || !(await recCompile(r.events))) return;
  renderRec(true); announce(`Recording stopped. ${recTest.steps.length} steps.`);
};
$("checkadd").onclick = async () => {
  const text = $("checktext").value.trim(); if (!text) return;
  const r = await ask({ type: "rec-check", text }); if (!r.ok) return showError(r.error);
  $("checktext").value = ""; const st = await ask({ type: "rec-state" }); if (st.ok && (await recCompile(st.events))) renderRec(false);
};
$("checksel").onclick = async () => { const r = await ask({ type: "rec-selection" }); if (r.ok && r.text) $("checktext").value = r.text.slice(0, 200); else showError("Select some text on the page first."); };
$("chromefile").onchange = async (e) => {
  const f = e.target.files[0]; if (!f) return; showError("");
  const r = await ask({ type: "import-chrome", recording: await f.text(), name: null });
  e.target.value = "";
  if (!r.ok) return showError(r.error);
  recTest = r.test; $("recname").value = r.test.name || "Chrome recording"; renderRec(true); showNotes(r.notes, []); $("notes").style.display = "";
};
const english = () => recTest.steps.map((x) => x.raw).join("\n");
$("reccopy").onclick = async () => { try { await navigator.clipboard.writeText(english()); $("reccopy").textContent = "Copied ✓"; setTimeout(() => { $("reccopy").textContent = "Copy as English"; }, 1500); } catch (e) { showError("Couldn't copy."); } };
$("recsave").onclick = async () => {
  const name = $("recname").value.trim() || "Recorded flow";
  const r = await ask({ type: "save-library", name, test: { ...recTest, name } });
  if (!r.ok) return showError(r.error);
  showError(""); $("recsave").textContent = "Saved ✓"; announce("Saved to the hub."); setTimeout(() => { $("recsave").textContent = "Save to hub"; }, 1800);
};
// Check replay: the recording is replayed now on this tab by the server's engine (the same replay, with the same strategy, as later on), and
// every step that does not reproduce is marked here with why, before anything is saved.
let checkRun = null;
$("reccheck").onclick = async () => {
  if (!recTest || !recTest.steps.length) return;
  showError("");
  const t = await refreshTarget(); if (!t) return showError("Open the page you recorded on in a tab first.");
  if (!(await mayRun(t, recTest.steps))) return;
  const box = $("checkres"); box.hidden = false; box.className = "checkres"; box.textContent = "Replaying on this tab…";
  document.querySelectorAll("#recsteps li").forEach((li) => { li.classList.remove("ok", "bad"); li.querySelectorAll(".why").forEach((x) => x.remove()); });
  $("reccheck").disabled = true;
  const r = await ask({ type: "run", tabId: t.id, steps: recTest.steps, data: recTest.data || {}, sensitive: recTest.sensitive || [], name: "Replay check: " + ($("recname").value.trim() || "recording"), config: {} });
  if (!r.ok) { $("reccheck").disabled = false; box.hidden = true; return showError(r.error); }
  checkRun = r.runId; checkBad = 0;
};
let checkBad = 0;
chrome.runtime.onMessage.addListener((ev) => {
  if (!ev || !checkRun || ev.runId !== checkRun) return;
  const items = Array.from(document.querySelectorAll("#recsteps li"));
  if (ev.type === "step_started") { const li = items[ev.index]; if (li) li.classList.add("run"); }
  if (ev.type === "step_finished") {
    const li = items[ev.index]; if (!li) return;
    li.classList.remove("run");
    if (ev.status === "failed") {
      li.classList.add("bad"); checkBad++;
      if (ev.error) { const w = document.createElement("span"); w.className = "why"; w.textContent = ev.error; li.children[1].append(w); }
      if (ev.shot) { const b = document.createElement("button"); b.type = "button"; b.className = "btn small"; b.textContent = "Show what the page looked like"; b.onclick = () => openZoom(ev.shot, b); li.children[1].append(b); }
    } else if (ev.status === "passed") li.classList.add("ok");
  }
  if (ev.type === "run_finished") {
    $("reccheck").disabled = false;
    const box = $("checkres"), skipped = items.filter((li) => !li.classList.contains("ok") && !li.classList.contains("bad")).length;
    if (ev.status === "passed") { box.className = "checkres ok"; box.textContent = `✓ All ${items.length} steps reproduced. It is safe to save.`; }
    else { box.className = "checkres bad"; box.textContent = `✕ ${checkBad || 1} step${checkBad > 1 ? "s" : ""} did not reproduce${skipped ? ` (${skipped} after it not run)` : ""}. Fix the page or the data, or edit or remove the step, then check again.${ev.error ? " " + ev.error : ""}`; }
    announce(box.textContent); checkRun = null;
  }
});

$("recreplay").onclick = () => {
  if (!recTest || !recTest.steps.length) return;
  const text = english(); $("text").value = text;
  previewed = { text, steps: recTest.steps, data: recTest.data || {}, sensitive: recTest.sensitive || [], name: $("recname").value.trim() || "Recorded flow" };
  setMode("run"); renderPlan(recTest.steps.map((s) => ({ raw: s.raw, action: s.action })), { selectable: true }); $("run").click();
};

// ------------------------------------------------------------------------------------------------ start
(async () => {
  // the scenario you were working on stays while the browser is open (never written to disk: it may hold a password)
  try { const s = await chrome.storage.session.get({ text: "", dataLines: "", runname: "" }); $("text").value = s.text; $("data").value = s.dataLines; $("runname").value = s.runname; renderDataTable(); } catch (e) { /* no session storage */ }
  try { const w = await chrome.storage.local.get({ welcomed: false }); $("welcome").hidden = w.welcomed; } catch (e) { /* ok */ }
  await refreshTarget(); refreshHub();
  const rs = await ask({ type: "rec-state" });                // the panel was closed while recording
  if (rs.ok && rs.active) { setMode("rec"); recUi(true); recPaused = !!rs.paused; syncRecPause(); if (await recCompile(rs.events)) renderRec(false); }
  const st = await ask({ type: "state" });                  // the panel was closed and reopened mid-run
  if (st.ok && st.runId && st.status === "running") {
    runId = lastRun = st.runId; setBusy(true); startedAt = Date.now();
    const start = st.log.find((e) => e.type === "run_started"); if (start) renderPlan(Array.from({ length: start.total }, (_, i) => ({ raw: st.log.find((e) => e.type === "step_started" && e.index === i)?.raw || "Step " + (i + 1) })));
    st.log.forEach(onEvent);                                 // catch up with everything that happened while it was closed
  }
})();
