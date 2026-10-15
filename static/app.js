const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const STATUS_KO = { queued: "대기", running: "진행 중", passed: "검증 통과", needs_review: "검토 필요", failed: "실패", saved: "저장됨" };

// 로컬(브리지가 직접 서비스)에서 열면 같은 주소, irodmas.com 등에서 열면 이 PC 의 브리지 주소
const store = {
  get(k, d) { try { const v = localStorage.getItem("fpAgent." + k); return v == null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("fpAgent." + k, v); } catch {} },
};
const LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
let BRIDGE = LOCAL ? location.origin : store.get("bridge", "http://127.0.0.1:8766");

let currentJob = null;
let pollTimer = null;
let lastLogLen = 0;

async function api(path, opts = {}) {
  const r = await fetch(BRIDGE + path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}

// ---------- 상태 표시
async function loadStatus() {
  $("#bridgeUrlText").textContent = BRIDGE === location.origin ? "" : BRIDGE;
  $("#bridgeBtn").hidden = BRIDGE === location.origin;
  let s;
  try { s = await api("/api/status"); }
  catch (e) {
    // 로컬 정적 서버(미리보기 등)에서 연 경우: 저장된 브리지 주소로 한 번 더 시도
    if (BRIDGE === location.origin) {
      BRIDGE = store.get("bridge", "http://127.0.0.1:8766");
      $("#bridgeBtn").hidden = false;
      if (BRIDGE !== location.origin) return loadStatus();
    }
    $("#bridgeDown").hidden = false;
    $("#status").innerHTML = `<span class="chip bad">브리지 연결 안 됨</span>`;
    return false;
  }
  $("#bridgeDown").hidden = true;
  $("#status").innerHTML = [
    `<span class="chip">${esc(s.model)}</span>`,
    s.backend === "cli"
      ? `<span class="chip ${s.claude_cli ? "ok" : "bad"}" title="API 키 없이 로그인된 Claude Code CLI 사용">${s.claude_cli ? "Claude CLI 연결" : "Claude CLI 없음"}</span>`
      : `<span class="chip ${s.api_key ? "ok" : "bad"}">${s.api_key ? "API 키 설정됨" : "API 키 없음"}</span>`,
    `<span class="chip ${s.kicad_cli ? "ok" : "bad"}" title="${esc(s.kicad_cli || "")}">${s.kicad_cli ? "kicad-cli 사용 가능" : "kicad-cli 없음"}</span>`,
  ].join("");
  return true;
}
$("#retryBtn").onclick = async () => { if (await loadStatus()) { loadJobs(); loadLib(); } };
$("#bridgeBtn").onclick = async () => {
  const v = prompt("로컬 브리지 주소", BRIDGE);
  if (!v) return;
  BRIDGE = v.trim().replace(/\/+$/, "");
  store.set("bridge", BRIDGE);
  if (await loadStatus()) { loadJobs(); loadLib(); }
};

// ---------- 입력 폼
const drop = $("#drop"), fileInput = $("#file");
drop.onclick = () => fileInput.click();
drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
drop.ondragleave = () => drop.classList.remove("over");
drop.ondrop = (e) => {
  e.preventDefault(); drop.classList.remove("over");
  if (e.dataTransfer.files.length) { fileInput.files = e.dataTransfer.files; showFile(); }
};
fileInput.onchange = showFile;
function showFile() {
  const f = fileInput.files[0];
  drop.classList.toggle("has", !!f);
  $("#dropText").innerHTML = f ? `📄 ${esc(f.name)} (${(f.size / 1048576).toFixed(1)} MB) · <u>변경</u>` : "PDF 를 끌어놓거나 <u>클릭해서 선택</u>";
}

$("#form").onsubmit = async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  fd.set("ai_review", e.target.ai_review.checked);
  fd.set("auto_save", e.target.auto_save.checked);
  if (!fileInput.files.length) fd.delete("file");
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true;
  try {
    const { id } = await api("/api/jobs", { method: "POST", body: fd });
    openJob(id);
    loadJobs();
  } catch (err) { alert(err.message); }
  finally { btn.disabled = false; }
};

// ---------- 작업 목록 / 라이브러리
async function loadJobs() {
  const jobs = await api("/api/jobs");
  $("#jobs").innerHTML = jobs.map((j) => `<li data-id="${j.id}" class="${j.id === currentJob ? "sel" : ""}">
    <span>${esc(j.part || "(미정)")}<div class="sub">${esc(j.created.replace("T", " "))}</div></span>
    <span class="badge ${j.status}">${STATUS_KO[j.status] || j.status}</span></li>`).join("") || `<li class="sub">없음</li>`;
  $$("#jobs li[data-id]").forEach((li) => (li.onclick = () => openJob(li.dataset.id)));
}
async function loadLib() {
  const lib = await api("/api/library");
  $("#libDir").textContent = lib.dir;
  $("#lib").innerHTML = lib.parts.map((p) => `<li title="${esc(p.footprint)}"><span>${esc(p.part_number)}<div class="sub">${esc(p.package)} · ${esc(p.manufacturer)}</div></span><span class="sub">${esc((p.saved_at || "").slice(0, 10))}</span></li>`).join("") || `<li class="sub">저장된 부품 없음</li>`;
}
$("#refreshLib").onclick = loadLib;

// ---------- 작업 보기
function openJob(id) {
  showView("work");
  currentJob = id; lastLogLen = 0;
  $("#log").innerHTML = "";
  $("#empty").hidden = true; $("#jobView").hidden = false;
  clearTimeout(pollTimer);
  poll();
}

async function poll() {
  if (!currentJob) return;
  const id = currentJob;
  let j;
  try { j = await api(`/api/jobs/${id}`); } catch (e) { return; }
  if (id !== currentJob) return;
  render(j);
  if (["queued", "running"].includes(j.status)) pollTimer = setTimeout(poll, 1200);
  else { loadJobs(); loadLib(); }
}

const ORDER = ["acquire", "extract", "generate", "verify", "review", "save"];
function stageIndex(j) {
  const s = (j.stage || "").split("#")[0];
  if (j.status === "saved") return 6;
  if (s === "done") return 5;
  if (s === "search") return 0;
  return Math.max(0, ORDER.indexOf(s));
}

function render(j) {
  const spec = j.result.spec || {};
  $("#jobTitle").textContent = spec.part_number || j.input.part_number || "(부품 미정)";
  const st = $("#jobState");
  st.className = `badge ${j.status}`; st.textContent = STATUS_KO[j.status] || j.status;

  const idx = stageIndex(j);
  $$("#stages li").forEach((li, i) => {
    li.className = i < idx ? "done" : i === idx && ["running", "queued"].includes(j.status) ? "cur" : "";
    if (j.status === "failed" && i === idx) li.className = "bad";
    if (!j.input.ai_review && li.dataset.s === "review") li.className = "";
  });

  const log = $("#log");
  for (const l of j.log.slice(lastLogLen)) {
    const d = document.createElement("div");
    d.className = l.level;
    d.innerHTML = `<span class="t">${esc(l.t)}</span>${esc(l.msg)}`;
    log.appendChild(d);
  }
  if (j.log.length !== lastLogLen) log.scrollTop = log.scrollHeight;
  lastLogLen = j.log.length;

  const has = !!j.result.files;
  $("#resultBox").hidden = !has;
  $("#saveBtn").disabled = !has || j.status === "saved";
  $("#saveBtn").textContent = j.status === "saved" ? "저장됨" : j.status === "needs_review" ? "확인 후 강제 저장" : "라이브러리에 저장";
  $("#dlSym").href = `${BRIDGE}/api/jobs/${j.id}/download/symbol`;
  $("#dlFp").href = `${BRIDGE}/api/jobs/${j.id}/download/footprint`;
  $("#dlSym").hidden = $("#dlFp").hidden = !has;
  if (has && !["queued", "running"].includes(j.status) && render.last !== j.id + j.status) {
    render.last = j.id + j.status;
    renderResult(j);
  }
}

function svgInto(el, svg, dark) {
  el.classList.toggle("dark", !!dark);
  if (!svg) { el.innerHTML = `<span class="hint">미리보기 없음 (kicad-cli 필요)</span>`; return; }
  el.innerHTML = svg.replace(/<script[\s\S]*?<\/script>/gi, "");
  const s = el.querySelector("svg");
  if (s) { s.removeAttribute("width"); s.removeAttribute("height"); s.setAttribute("preserveAspectRatio", "xMidYMid meet"); }
}

let lastResult = null;
function showPreviews(r) {
  lastResult = r;
  const k = $("#kicadView").checked;
  svgInto($("#symSvg"), k ? r.kicad_symbol_svg : r.symbol_svg, false);
  svgInto($("#fpSvg"), k ? r.kicad_footprint_svg : r.footprint_svg, k);
}
$("#kicadView").onchange = () => lastResult && showPreviews(lastResult);

function table(cols, rows) {
  return `<table><thead><tr>${cols.map((c) => `<th>${esc(c[1])}</th>`).join("")}</tr></thead><tbody>${rows
    .map((r) => `<tr>${cols.map((c) => `<td class="${typeof r[c[0]] === "number" ? "num" : ""}">${esc(r[c[0]] ?? "")}</td>`).join("")}</tr>`)
    .join("")}</tbody></table>`;
}

function renderResult(j) {
  const r = j.result, spec = r.spec;
  $("#symName").textContent = r.symbol_name;
  $("#fpName").textContent = r.footprint_name;
  showPreviews(r);
  $("#landNotes").textContent = (r.land_notes || []).map((n) => "• " + n).join("\n");

  $("#pinsTable").innerHTML = table(
    [["number", "번호"], ["name", "이름"], ["electrical_type", "전기적 타입"], ["side", "배치"], ["group", "그룹"], ["description", "설명"]],
    spec.pins
  );
  const p = spec.package;
  const kv = [["패키지", p.name], ["유형", p.family], ["핀 수", p.pin_count], ["피치", p.pitch], ["몸체 E1×D", `${p.body_width ?? "?"} × ${p.body_length ?? "?"}`],
    ["리드 스팬 E", `${p.lead_span_min ?? "?"} – ${p.lead_span_max ?? "?"}`], ["리드 길이 L", `${p.lead_length_min ?? "?"} – ${p.lead_length_max ?? "?"}`],
    ["리드 폭 b", `${p.lead_width_min ?? "?"} – ${p.lead_width_max ?? "?"}`], ["높이 A", p.height_max], ["EP", p.exposed_pad ? `${p.exposed_pad.width} × ${p.exposed_pad.length}` : "-"],
    ["랜드 패턴", p.land_pattern_source === "datasheet_recommended" ? "데이터시트 권장" : "IPC-7351 계산"]];
  $("#pkgInfo").innerHTML = `<div class="kv">${kv.map(([k, v]) => `<div><span>${esc(k)}</span>${esc(v ?? "-")}</div>`).join("")}</div>` +
    (p.notes ? `<p class="hint">${esc(p.notes)}</p>` : "");
  $("#padsTable").innerHTML = table(
    [["number", "번호"], ["x", "X"], ["y", "Y"], ["w", "폭"], ["h", "높이"], ["rot", "회전"], ["shape", "모양"], ["type", "종류"], ["drill", "드릴"], ["layers", "레이어"]],
    r.pads
  );

  $("#checks").innerHTML = r.checks.map((c) => `<li class="${c.level}"><b>${c.level === "ok" ? "✓" : c.level === "warning" ? "!" : "✕"}</b>${esc(c.check)} — ${esc(c.message)}</li>`).join("") +
    (r.kicad && r.kicad.cli ? `<li class="${r.kicad.symbol_ok && r.kicad.footprint_ok ? "ok" : "error"}"><b>${r.kicad.symbol_ok && r.kicad.footprint_ok ? "✓" : "✕"}</b>kicad-cli — 심볼 ${r.kicad.symbol_ok ? "OK" : "실패"}, 풋프린트 ${r.kicad.footprint_ok ? "OK" : "실패"}</li>` : "");
  $("#kicadLog").textContent = r.kicad?.log || "";

  const rv = r.review;
  const hist = r.review_history || [];
  $("#review").innerHTML = !rv ? `<p class="hint">AI 검토를 실행하지 않았습니다.</p>` :
    hist.map((h, i) => `<h2>${i + 1}회차 <span class="badge ${h.verdict === "pass" ? "passed" : "failed"}">${h.verdict}</span></h2>
      <p>${esc(h.summary)}</p>${h.issues.map((x) => `<div class="issue ${x.severity}"><b>[${esc(x.severity)}] ${esc(x.area)}</b> ${esc(x.description)}${x.evidence ? `<small>${esc(x.evidence)}</small>` : ""}</div>`).join("") || `<p class="hint">지적 사항 없음</p>`}`).join("") +
    (spec.evidence?.length ? `<h2>근거</h2><ul>${spec.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : "") +
    (spec.extraction_notes ? `<p class="hint">${esc(spec.extraction_notes)}</p>` : "");

  $("#specEdit").value = JSON.stringify(spec, null, 2);
  $("#editMsg").textContent = "";
}

// ---------- 탭
$$(".tabs button").forEach((b) => (b.onclick = () => {
  $$(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".pane").forEach((p) => p.classList.toggle("on", p.dataset.pane === b.dataset.tab));
}));

// ---------- 저장 / 재생성
$("#saveBtn").onclick = async () => {
  const j = await api(`/api/jobs/${currentJob}`);
  const force = j.status === "needs_review";
  if (force && !confirm("자동 검증을 통과하지 못한 결과입니다. 내용을 확인했고 저장하시겠습니까?")) return;
  try { await api(`/api/jobs/${currentJob}/save?force=${force}`, { method: "POST" }); render.last = null; poll(); }
  catch (e) { alert(e.message); }
};

async function resubmit(aiReview) {
  let spec;
  try { spec = JSON.parse($("#specEdit").value); } catch (e) { $("#editMsg").textContent = "JSON 문법 오류: " + e.message; return; }
  const libName = $("#form").lib_name.value || "AutoParts";
  try {
    const { id } = await api("/api/jobs/spec", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec, lib_name: libName, ai_review: aiReview, auto_save: false, from_job: currentJob }),
    });
    openJob(id); loadJobs();
  } catch (e) { $("#editMsg").textContent = e.message; }
}
$("#previewBtn").onclick = () => resubmit(false);
$("#rerunBtn").onclick = () => resubmit(true);

// ---------- 상단 메뉴
function showView(v) {
  $$("#menu button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  ["work", "help", "pipe"].forEach((k) => ($(`#view-${k}`).hidden = k !== v));
  if (location.hash !== "#" + v) history.replaceState(null, "", location.pathname + location.search + (v === "work" ? "" : "#" + v));
}
$$("#menu button").forEach((b) => (b.onclick = () => showView(b.dataset.view)));
showView(["help", "pipe"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "work");

loadStatus().then((ok) => {
  if (!ok) return;
  loadJobs(); loadLib();
  const jobParam = new URLSearchParams(location.search).get("job");  // ?job=<id> 로 결과 바로 열기
  if (jobParam) openJob(jobParam);
});
