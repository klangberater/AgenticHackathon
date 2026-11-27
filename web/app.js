// Everything from the agent is set with textContent, and only http(s) links become anchors:
// the verdict quotes third-party pages, so it is treated as untrusted text.
const $ = (id) => document.getElementById(id);
if (new URLSearchParams(location.search).get("theme") === "paper") document.documentElement.dataset.theme = "paper";
const views = ["empty", "running", "failed", "result"];
const CHECK_LABEL = { ok: "Quote found", weak: "Partly matched", unreadable: "Could not re-read" };
const FLAG = { green: "Green flag", yellow: "Yellow flag", red: "Red flag" };
let timer = null;

function show(view) {
  for (const v of views) $(v).hidden = v !== view;
  $("sheet").setAttribute("aria-busy", String(view === "running"));
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function tag(text, tone) {
  const t = el("span", "tag", text);
  if (tone) t.dataset.tone = tone;
  return t;
}

function link(url, quote) {
  if (!/^https?:\/\//i.test(url)) return el("span", null, url);
  const a = el("a", null, url.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, ""));
  a.href = url + fragment(url, quote); a.target = "_blank"; a.rel = "noreferrer noopener";
  return a;
}

// A text fragment makes the browser jump to the quoted passage and highlight it.
function fragment(url, quote) {
  const words = (quote || "").split(/\s+/).filter(Boolean);
  if (url.includes("#") || words.length < 4) return "";
  const enc = (ws) => encodeURIComponent(ws.join(" ")).replace(/-/g, "%2D");
  return "#:~:text=" + (words.length <= 8 ? enc(words) : `${enc(words.slice(0, 4))},${enc(words.slice(-4))}`);
}

function setUrlState(params) {
  const q = new URLSearchParams(params).toString();
  history.replaceState(null, "", q ? `?${q}` : location.pathname);
}

function fail(text) {
  clearInterval(timer);
  $("fail-text").textContent = text;
  show("failed");
  busy(false);
}

function busy(on) {
  $("go").disabled = on;
  $("url").disabled = on;
  $("go").textContent = on ? "Checking" : "Check vendor";
}

function render(v) {
  clearInterval(timer);
  busy(false);
  if (v.error) return fail(v.error);
  document.title = `${v.product_name}: ${v.verdict}`;
  $("v-word").textContent = v.verdict[0].toUpperCase() + v.verdict.slice(1);
  $("a-life").textContent = v.lifecycle.replace(/_/g, " ");
  $("v-former").textContent = v.former_names.length ? `Formerly ${v.former_names.join(", ")}` : "";
  $("v-word").parentElement.dataset.verdict = v.verdict;
  $("v-name").textContent = v.product_name;
  $("v-vendor").textContent = [v.product_category, v.vendor_name].filter(Boolean).join(". ");
  $("v-url").replaceWith(Object.assign(link(v.url), { id: "v-url" }));
  $("a-product").textContent = v.product_real_and_alive;
  $("a-company").textContent = v.company_trustworthy;
  $("a-conf").textContent = v.confidence;
  $("v-summary").textContent = v.summary;

  // Flags are filed by colour, the most serious first, the way a buyer reads them.
  const list = $("reasons");
  list.replaceChildren();
  const n = (c) => v.reasons.filter((r) => r.flag === c).length;
  for (const colour of ["red", "yellow", "green"]) {
    if (!n(colour)) continue;
    const group = el("section", "flags panel");
    group.dataset.flag = colour;
    const head = el("h3", "panel-head", `${FLAG[colour]}s`);
    head.append(el("span", "count", String(n(colour))));
    const rows = el("ol");
    for (const r of v.reasons.filter((x) => x.flag === colour)) {
      const li = el("li");
      li.append(el("p", "claim", r.claim), el("blockquote", "quote", r.quote));
      const src = el("p", "source");
      src.append(link(r.source_url, r.check && r.check.status === "ok" ? r.quote : ""), tag(r.category));
      if (r.independent) src.append(tag("Independent source"));
      const status = r.check ? r.check.status : "";
      if (status) src.append(tag(CHECK_LABEL[status] || status, status));
      li.append(src);
      rows.append(li);
    }
    group.append(head, rows);
    list.append(group);
  }
  $("reasons-head").textContent = `Flags: ${n("red")} red, ${n("yellow")} yellow, ${n("green")} green`;

  const fill = (boxId, listId, items, text) => {
    $(boxId).hidden = !items.length;
    $(listId).replaceChildren(...items.map((i) => el("li", null, text(i))));
  };
  fill("unchecked-box", "unchecked", v.could_not_check || [], (s) => s);
  fill("dropped-box", "dropped", v.dropped_reasons || [], (r) => `${r.claim} (${r.source_url})`);
  const secs = v._meta && v._meta.wall_seconds;
  $("v-pdf").href = `/api/report/${slugOf(v.url)}.pdf`;
  $("v-foot").textContent = secs ? `Checked in ${Math.floor(secs / 60)} min ${secs % 60} s.` : "";
  show("result");
  loadRecent();
}

// Same naming as the server uses for result files.
function slugOf(url) {
  return url.replace(/^https?:\/\//i, "").toLowerCase().replace(/^www\./, "").replace(/[^a-z0-9.-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 120);
}

function stageOf(steps) {
  const last = steps.length ? steps[steps.length - 1].text : "";
  if (/repairing|sources did not|each source says|revising/.test(last)) return "verify";
  if (/investigating/.test(last)) return "investigate";
  return "collect";
}

function paintStages(now) {
  const order = ["collect", "investigate", "verify"];
  for (const li of $("stages").children) {
    const i = order.indexOf(li.dataset.stage), n = order.indexOf(now);
    li.dataset.state = i < n ? "done" : i === n ? "now" : "next";
  }
}

async function follow(id, url) {
  busy(true);
  $("run-title").textContent = `Checking ${url.replace(/^https?:\/\//, "")}`;
  paintStages("collect");
  show("running");
  clearInterval(timer);
  const tick = async () => {
    let job;
    try {
      const res = await fetch(`/api/jobs/${id}`);
      job = await res.json();
      if (res.status === 404 && url) { clearInterval(timer); busy(false); return openResult(slugOf(url)); }
      if (!res.ok) return fail(job.error);
    } catch {
      return fail("Lost the connection to the local server. Is it still running?");
    }
    const s = Math.round(Date.now() / 1000 - job.started);
    $("run-clock").textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
    paintStages(stageOf(job.steps));
    if (job.done) {
      setUrlState({ result: slugOf(job.url) });
      render(job.result || { error: "The run ended without a result." });
    }
  };
  timer = setInterval(tick, 1500);
  tick();
}

async function openResult(file) {
  try {
    const res = await fetch(`/api/results/${encodeURIComponent(file)}`);
    const v = await res.json();
    if (!res.ok) return fail(v.error);
    setUrlState({ result: file });
    render(v);
  } catch {
    fail("Could not load that result from the local server.");
  }
}

async function loadRecent() {
  let rows = [];
  try { rows = await (await fetch("/api/recent")).json(); } catch { /* the list is a convenience */ }
  $("recent-empty").hidden = rows.length > 0;
  $("recent").replaceChildren(...rows.map((r) => {
    const li = el("li");
    const a = el("a", null, r.product);
    a.href = `?result=${encodeURIComponent(r.file)}`;
    a.addEventListener("click", (e) => { e.preventDefault(); openResult(r.file); });
    li.append(a, tag(r.verdict, r.verdict));
    return li;
  }));
}

async function start(url) {
  const err = $("ask-error");
  err.hidden = true;
  $("url").removeAttribute("aria-invalid");
  if (!url) {
    err.textContent = "Enter the product's website first.";
    err.hidden = false;
    $("url").setAttribute("aria-invalid", "true");
    return $("url").focus();
  }
  busy(true);
  try {
    const res = await fetch("/api/jobs", { method: "POST", body: JSON.stringify({ url }),
      headers: { "Content-Type": "application/json", "X-Access-Code": $("code").value } });
    const body = await res.json();
    if (!res.ok) {
      busy(false);
      err.textContent = body.error;
      err.hidden = false;
      $("url").setAttribute("aria-invalid", "true");
      return;
    }
    setUrlState({ job: body.id, url });
    follow(body.id, url);
  } catch {
    busy(false);
    fail("Could not reach the local server. Start it with: python3 -m taste.server");
  }
}

$("ask").addEventListener("submit", (e) => { e.preventDefault(); start($("url").value.trim()); });
$("retry").addEventListener("click", () => { show("empty"); $("url").focus(); });

// A static export has no server to run checks; a public server may ask for an access code.
fetch("/api/config").then((r) => r.json()).then((c) => {
  $("ask").hidden = c.static;
  if (c.static || !/^(localhost|127\.0\.0\.1)$/.test(location.hostname)) document.querySelector(".bar-note").textContent = "";
  $("code-field").hidden = !c.token_required;
  if (c.static) $("empty-text").textContent = "Pick a finished check from the list to read its verdict and sources.";
}).catch(() => {});

const q = new URLSearchParams(location.search);
if (q.get("job")) { $("url").value = q.get("url") || ""; follow(q.get("job"), q.get("url") || ""); }
else if (q.get("result")) openResult(q.get("result"));
loadRecent();
