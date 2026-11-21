// The side panel: paste a scenario, run it on the tab you are on, watch it. All the work happens in the service worker.
const $ = (id) => document.getElementById(id);
const ask = (msg) => new Promise((res) => chrome.runtime.sendMessage(msg, (r) => res(r || { ok: false, error: chrome.runtime.lastError ? chrome.runtime.lastError.message : "No answer" })));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

let runId = null, lastRun = null, tabId = null, previewed = null, startedAt = 0;

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

async function refreshHub() {
  const r = await ask({ type: "health" });
  const s = (await ask({ type: "settings" })).settings || {};
  const line = $("hubline");
  if (!r.ok) { line.className = "hub bad"; line.innerHTML = `Hub: ${esc(r.error)} <a href="#" id="opt">Settings</a>`; $("opt").onclick = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); }; return false; }
  const h = r.health || {};
  line.className = "hub";
  line.innerHTML = h.auth ? `Hub <b>${esc(s.hub)}</b>: <a href="#" id="opt">sign in</a> if it asks` : `Hub <b>${esc(s.hub)}</b> · connected`;
  const o = $("opt"); if (o) o.onclick = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); };
  return true;
}

// ------------------------------------------------------------------------------------------------ data lines
function parseData() {
  const data = {}, sensitive = [];
  for (const raw of $("data").value.split("\n")) {
    const m = raw.match(/^\s*([\w.\-]+)\s*(\*?)\s*=\s*(.*?)\s*$/);
    if (!m) continue;
    data[m[1]] = m[3]; if (m[2]) sensitive.push(m[1]);
  }
  return { data, sensitive };
}

// ------------------------------------------------------------------------------------------------ rendering
function showError(text) { const m = $("msg"); m.textContent = text; m.hidden = !text; }
function showNotes(notes, ignored) {
  const n = $("notes"); n.hidden = !(notes && notes.length) && !(ignored && ignored.length);
  n.innerHTML = (notes && notes.length ? `<b>Check before you trust it</b><ul>${notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "")
    + (ignored && ignored.length ? `<div class="ign">Left out as background: ${ignored.map((x) => "“" + esc(x) + "”").join(" · ")}</div>` : "");
}
function renderPlan(plan) {
  $("steps").innerHTML = plan.map((p, i) => `<li class="step pending" id="s${i}"><span class="ic">${i + 1}</span><span class="tx">${esc(p.raw || p.action)}</span><span class="pill" hidden></span>${p.origin ? `<span class="meta" style="grid-column:2/4">from your text: “${esc(p.origin)}”</span>` : ""}</li>`).join("");
}
function setStep(i, status, extra = {}) {
  const li = $("s" + i); if (!li) return;
  li.className = "step " + status;
  li.querySelector(".ic").textContent = { running: "●", passed: "✓", failed: "✕", skipped: "–" }[status] || (i + 1);
  if (extra.tab > 1) { const p = li.querySelector(".pill"); p.hidden = false; p.textContent = "Tab " + extra.tab; }
  li.querySelectorAll(".err,img,.mt").forEach((x) => x.remove());
  if (extra.ms != null && status !== "running") { const s = document.createElement("span"); s.className = "meta mt"; s.style.gridColumn = "2/4"; s.textContent = (extra.ms / 1000).toFixed(1) + " s" + (extra.picked ? " · element: " + extra.picked : ""); li.append(s); }
  if (extra.error) { const d = document.createElement("div"); d.className = "err"; d.textContent = extra.error; li.append(d); }
  if (extra.shot) { const im = document.createElement("img"); im.src = extra.shot; im.alt = "Step " + (i + 1); im.onclick = () => { $("zoom").querySelector("img").src = extra.shot; $("zoom").hidden = false; }; li.append(im); }
  if (status === "running") li.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
$("zoom").onclick = () => { $("zoom").hidden = true; };

function setBusy(on) {
  $("run").hidden = on; $("preview").disabled = on; $("stop").hidden = !on; $("text").readOnly = on;
}

function tabsList(list) {
  const el = $("tabs"); el.hidden = list.length < 2;
  el.innerHTML = list.map((t, i) => `<div class="tab"><b>Tab ${i + 1}</b><span>${esc(t.title || t.url)}</span></div>`).join("");
}

// ------------------------------------------------------------------------------------------------ events from the run
const seenTabs = [];
function onEvent(ev) {
  if (ev && ev.type === "report_done" && ev.runId === lastRun) return reportLine({ run: ev.hubRun, ok: ev.ok, error: ev.error });      // arrives after the run ended
  if (!ev || !ev.runId || ev.runId !== runId) return;
  switch (ev.type) {
    case "run_started": seenTabs.length = 0; seenTabs.push(ev.tab); tabsList(seenTabs); break;
    case "step_started": setStep(ev.index, "running"); break;
    case "step_finished": setStep(ev.index, ev.status, { error: ev.error, shot: ev.shot, ms: ev.ms, picked: ev.picked, tab: ev.tab }); break;
    case "tab": seenTabs[ev.index - 1] = ev.tab; tabsList(seenTabs); break;
    case "run_finished": finish(ev); break;
  }
}
chrome.runtime.onMessage.addListener(onEvent);

let hubBase = "";
async function reportLine(r) {
  const el = $("reportline"); el.hidden = false;
  if (!hubBase) hubBase = ((await ask({ type: "settings" })).settings || {}).hub || "";
  const url = r.run ? `${hubBase}/#/runs/${encodeURIComponent(r.run)}` : "";
  if (r.error && !r.run) el.innerHTML = `Not reported to the hub: ${esc(r.error)}`;
  else if (r.saving) el.innerHTML = `Reporting to the hub… <a href="${esc(url)}" target="_blank">live view</a>`;
  else el.innerHTML = `${r.ok ? "Saved on the hub:" : "Saved on the hub (some of it didn't arrive):"} <a href="${esc(url)}" target="_blank">report, video and replay</a>`;
}

function finish(ev) {
  setBusy(false);
  const b = $("banner"); b.hidden = false; b.className = "banner " + ev.status;
  const secs = ((Date.now() - startedAt) / 1000).toFixed(1);
  b.innerHTML = `${{ passed: "✓ Passed", failed: "✕ Failed", cancelled: "■ Stopped" }[ev.status] || ev.status}<small>${secs} s${ev.tabs && ev.tabs.length > 1 ? " · " + ev.tabs.length + " tabs" : ""}</small>`;
  if (ev.error) showError(ev.error);
  if (ev.tabs) tabsList(ev.tabs);
  runId = null; refreshTarget();
}

// ------------------------------------------------------------------------------------------------ buttons
$("preview").onclick = async () => {
  showError(""); $("banner").hidden = true;
  const text = $("text").value.trim(); if (!text) return showError("Paste a scenario first.");
  $("preview").disabled = true;
  const r = await ask({ type: "preview", text, ...parseData() });
  $("preview").disabled = false;
  if (!r.ok) return showError(r.error);
  previewed = { text, steps: r.test.steps, data: r.test.data, sensitive: r.test.sensitive };
  renderPlan(r.test.steps.map((s) => ({ raw: s.raw, origin: s.origin, action: s.action })));
  showNotes(r.notes, r.ignored);
  if (r.unparsed && r.unparsed.length) showError("Not understood: " + r.unparsed.join(" · "));
  $("tdot").className = "dot " + (r.tab.ok ? "ok" : "bad");
};

$("run").onclick = async () => {
  showError(""); $("banner").hidden = true; $("tabs").hidden = true; $("reportline").hidden = true;
  const text = $("text").value.trim(); if (!text) return showError("Paste a scenario first.");
  const t = await refreshTarget(); if (!t) return showError("Open the website you want to test in a tab first.");
  const { data, sensitive } = parseData();
  const same = previewed && previewed.text === text;
  setBusy(true); startedAt = Date.now();
  const r = await ask(same ? { type: "run", tabId: t.id, steps: previewed.steps, data: { ...previewed.data, ...data }, sensitive: [...new Set([...previewed.sensitive, ...sensitive])], sourceRun: previewed.sourceRun, name: previewed.name }
                           : { type: "run", tabId: t.id, text, data, sensitive });
  if (!r.ok) { setBusy(false); return showError(r.error); }
  runId = lastRun = r.runId; renderPlan(r.plan); showNotes(r.notes, r.ignored);
  if (r.hubRun) reportLine({ saving: true, run: r.hubRun }); else if (r.reportError) reportLine({ error: r.reportError });
  chrome.storage.session.set({ text, dataLines: $("data").value }).catch(() => {});
};

$("stop").onclick = () => { if (runId) ask({ type: "stop", runId }); $("stop").disabled = true; setTimeout(() => { $("stop").disabled = false; }, 1500); };
$("gear").onclick = () => chrome.runtime.openOptionsPage();
$("text").oninput = () => { previewed = null; };

// ------------------------------------------------------------------------------------------------ from the hub
async function loadHubLists() {
  const box = $("hublists"); box.textContent = "Loading…";
  const r = await ask({ type: "hub-lists" });
  if (!r.ok) { box.textContent = r.error; return; }
  hubBase = hubBase || ((await ask({ type: "settings" })).settings || {}).hub || "";
  const lib = (r.library || []).map((x) => `<div class="it"><span class="d"></span><span class="nm" title="${esc(x.name)}">${esc(x.test_name || x.name)} · ${x.steps} steps</span><span></span><button class="btn" data-k="library" data-id="${esc(x.name)}">▶ Replay</button></div>`).join("");
  const runs = (r.runs || []).slice(0, 10).map((x) => `<div class="it"><span class="d ${esc(x.status)}"></span><span class="nm" title="${esc(x.test_name)}">${esc(x.test_name)}</span><a href="${esc(hubBase)}/#/runs/${encodeURIComponent(x.run_id)}" target="_blank">report</a><button class="btn" data-k="run" data-id="${esc(x.run_id)}">▶ Replay</button></div>`).join("");
  box.innerHTML = `<h4>Saved scripts</h4>${lib || "<small>Nothing saved yet. Record a flow and press Save to hub.</small>"}<h4>Recent runs</h4>${runs || "<small>No runs yet.</small>"}`;
}
$("hubd").ontoggle = () => { if ($("hubd").open) loadHubLists(); };
$("hublists").onclick = async (e) => {
  const b = e.target.closest("button[data-id]"); if (!b) return;
  showError(""); b.disabled = true;
  const r = await ask({ type: "replay-load", kind: b.dataset.k, id: b.dataset.id }); b.disabled = false;
  if (!r.ok) return showError(r.error);
  const text = r.test.steps.map((x) => x.raw).join("\n");
  $("text").value = text;
  previewed = { text, steps: r.test.steps, data: r.test.data || {}, sensitive: r.test.sensitive || [], sourceRun: b.dataset.k === "run" ? b.dataset.id : "library:" + b.dataset.id, name: r.test.name };
  if (r.needs && r.needs.length) {                      // a saved script doesn't keep its secrets: ask for them
    $("data").value = r.needs.map((k) => `${k}* = `).join("\n"); $("datad").open = true;
    return showError(`Enter ${r.needs.join(", ")} under Data, then press Run.`);
  }
  $("run").click();
};

// ------------------------------------------------------------------------------------------------ recorder
let recTest = null, recLive = false, recTimer = null;
function setMode(m) {
  const rec = m === "rec";
  $("mrec").classList.toggle("on", rec); $("mrun").classList.toggle("on", !rec);
  $("recview").hidden = !rec; $("runview").hidden = rec; $("recout").hidden = !rec || !recTest;
  ["steps", "banner", "tabs", "notes"].forEach((id) => { if (rec) $(id).dataset.was = $(id).hidden; $(id).style.display = rec ? "none" : ""; });
}
$("mrun").onclick = () => setMode("run");
$("mrec").onclick = () => setMode("rec");

function renderRec(editable) {
  const st = recTest ? recTest.steps : [];
  $("recout").hidden = !recTest; $("reccount").textContent = st.length ? `· ${st.length}` : "";
  $("recsteps").innerHTML = st.map((x, i) => `<li><span class="n">${i + 1}</span><span>${esc(x.raw || x.action)}${x.locator ? `<small>finds it by ${esc(x.locator.strategy)}: ${esc(x.locator.role ? x.locator.role + " · " : "")}${esc(String(x.locator.value).slice(0, 50))}</small>` : ""}</span>${editable ? `<button class="x" data-i="${i}" title="Remove this step" aria-label="Remove step ${i + 1}">✕</button>` : "<span></span>"}</li>`).join("");
}
$("recsteps").onclick = (e) => { const b = e.target.closest(".x"); if (!b || !recTest) return; recTest.steps.splice(+b.dataset.i, 1); renderRec(!recLive); };

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
  $("recstart").hidden = on; $("recstop").hidden = !on; $("recadd").hidden = !on;
}
$("recstart").onclick = async () => {
  showError(""); recTest = null; renderRec(false);
  const r = await ask({ type: "rec-start" }); if (!r.ok) return showError(r.error);
  recUi(true);
};
$("recstop").onclick = async () => {
  const r = await ask({ type: "rec-stop" }); recUi(false);
  if (!r.ok) return showError(r.error);
  if (!r.events.length || !(await recCompile(r.events))) return;
  renderRec(true);
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
  showError(""); $("recsave").textContent = "Saved ✓"; setTimeout(() => { $("recsave").textContent = "Save to hub"; }, 1800);
};
// Check replay: the recording is replayed now on this tab by the server's engine (the same replay, with the same strategy, as later on), and
// every step that does not reproduce is marked here with why, before anything is saved.
let checkRun = null;
$("reccheck").onclick = async () => {
  if (!recTest || !recTest.steps.length) return;
  showError("");
  const t = await refreshTarget(); if (!t) return showError("Open the page you recorded on in a tab first.");
  const box = $("checkres"); box.hidden = false; box.className = "checkres"; box.textContent = "Replaying on this tab…";
  document.querySelectorAll("#recsteps li").forEach((li) => { li.classList.remove("ok", "bad"); li.querySelectorAll(".why").forEach((x) => x.remove()); });
  $("reccheck").disabled = true;
  const r = await ask({ type: "run", tabId: t.id, steps: recTest.steps, data: recTest.data || {}, sensitive: recTest.sensitive || [], name: "Replay check: " + ($("recname").value.trim() || "recording") });
  if (!r.ok) { $("reccheck").disabled = false; box.hidden = true; return showError(r.error); }
  checkRun = r.runId; checkHub = r.hubRun; checkBad = 0; checkTotal = r.total;
};
let checkHub = null, checkBad = 0, checkTotal = 0;
chrome.runtime.onMessage.addListener((ev) => {
  if (!ev || !checkRun || ev.runId !== checkRun) return;
  const items = Array.from(document.querySelectorAll("#recsteps li"));
  if (ev.type === "step_started") { const li = items[ev.index]; if (li) li.classList.add("run"); }
  if (ev.type === "step_finished") {
    const li = items[ev.index]; if (!li) return;
    li.classList.remove("run");
    if (ev.status === "failed") { li.classList.add("bad"); checkBad++; if (ev.error) { const w = document.createElement("span"); w.className = "why"; w.textContent = ev.error; li.children[1].append(w); } }
    else if (ev.status === "passed") li.classList.add("ok");
  }
  if (ev.type === "run_finished") {
    $("reccheck").disabled = false;
    const box = $("checkres"), skipped = items.filter((li) => !li.classList.contains("ok") && !li.classList.contains("bad")).length;
    if (ev.status === "passed") { box.className = "checkres ok"; box.textContent = `✓ All ${items.length} steps reproduced. It is safe to save.`; }
    else { box.className = "checkres bad"; box.textContent = `✕ ${checkBad || 1} step${checkBad > 1 ? "s" : ""} did not reproduce${skipped ? ` (${skipped} after it not run)` : ""}. Fix the page or the data, or remove the step (✕), then check again.${ev.error ? " " + ev.error : ""}`; }
    checkRun = null;
  }
});

$("recreplay").onclick = () => {
  if (!recTest || !recTest.steps.length) return;
  const text = english(); $("text").value = text;
  previewed = { text, steps: recTest.steps, data: recTest.data || {}, sensitive: recTest.sensitive || [], name: $("recname").value.trim() || "Recorded flow" };
  setMode("run"); $("run").click();
};

// ------------------------------------------------------------------------------------------------ start
(async () => {
  // the scenario you were working on stays while the browser is open (never written to disk: it may hold a password)
  try { const s = await chrome.storage.session.get({ text: "", dataLines: "" }); $("text").value = s.text; $("data").value = s.dataLines; } catch (e) { /* no session storage */ }
  await refreshTarget(); refreshHub();
  const rs = await ask({ type: "rec-state" });                // the panel was closed while recording
  if (rs.ok && rs.active) { setMode("rec"); recUi(true); if (await recCompile(rs.events)) renderRec(false); }
  const st = await ask({ type: "state" });                  // the panel was closed and reopened mid-run
  if (st.ok && st.runId && st.status === "running") {
    runId = lastRun = st.runId; setBusy(true); startedAt = Date.now();
    const start = st.log.find((e) => e.type === "run_started"); if (start) renderPlan(Array.from({ length: start.total }, (_, i) => ({ raw: st.log.find((e) => e.type === "step_started" && e.index === i)?.raw || "Step " + (i + 1) })));
    st.log.forEach(onEvent);                                 // catch up with everything that happened while it was closed
  }
})();
