/* AI Pulse 대시보드 — data/dashboard.json 을 읽어 순위표·트렌드·매트릭스를 그린다. */
const PERIODS = ["weekly", "monthly", "yearly", "total"];
const PERIOD_HEAD = { weekly: "주간 (최근 1주일)", monthly: "월간 (지난달)", yearly: "연간 (올해 1월부터 누적)", total: "총 누적 (과거~현재)" };
const PERIOD_SHORT = { weekly: "주간", monthly: "월간", yearly: "연간", total: "누적" };
const SRC_KO = { github: "GitHub", hf_model: "HF 모델", hf_dataset: "HF 데이터셋", paper: "논문" };
const STATUS_KO = { hf_papers: "HF Papers", github: "GitHub", huggingface: "HF Hub", semantic_scholar: "Semantic Scholar", arxiv: "arXiv" };
const METRIC_KO = {
  stars: "스타", forks: "포크", watchers: "워처", issues_prs: "이슈+PR 누적", contributors: "기여자(멘션 가능)",
  commits_1y: "최근 1년 커밋", days_since_push: "마지막 푸시(일 전)", likes: "좋아요", downloads_30d: "다운로드(30일)",
  downloads_all: "다운로드(전체)", trending_score: "trendingScore", derivatives: "파생 모델", family_downloads: "패밀리 다운로드(30일)",
  spaces: "사용 Spaces", hf_upvotes: "HF 업보트", comments: "댓글", code_stars: "코드 저장소 스타", citations: "인용",
  influential_citations: "영향력 있는 인용", venue: "주요 학회 채택",
};
const TOP = 10;
const state = {
  intAxis: "popularity", intFields: new Set(), intPeriod: "weekly", mxPeriod: "weekly",
  srcAxis: "influence", src: "github", srcField: "all", srcPeriod: "weekly",
};
let D = null;
const charts = {};

const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const nf = (n) => n == null ? "–" : Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 1 });
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

try { const s = JSON.parse(localStorage.getItem("aipulse") || "{}"); Object.assign(state, s, { intFields: new Set(s.intFields || []) }); } catch (e) { /* 저장소 없음 */ }
function save() {
  try { localStorage.setItem("aipulse", JSON.stringify({ ...state, intFields: [...state.intFields] })); } catch (e) { /* 무시 */ }
}

fetch("data/dashboard.json", { cache: "no-cache" }).then((r) => r.json()).then((d) => { D = d; init(); })
  .catch((e) => { document.querySelector("main").insertAdjacentHTML("afterbegin", `<div class="note">데이터를 불러오지 못했습니다: ${esc(e)}</div>`); });

function fieldLabel(f) { return D.fields.labels[f] || "미분류"; }
function fieldShort(f) { return (D.fields.labels[f] || "미분류").replace(/^[①-⑥]\s*/, ""); }

/* 필터 조건 */
function matchInt(it) {
  if (!state.intFields.size) return true;
  for (const f of state.intFields) {
    if (D.tags[f] ? (it.g || []).includes(f) : it.f === f) return true;
  }
  return false;
}
function matchSrc(it) {
  if (state.srcField === "all") return true;
  return D.tags[state.srcField] ? (it.g || []).includes(state.srcField) : it.f === state.srcField;
}

/* 필터 적용 후 상위 10 + 전일 대비 순위 변동 */
function topList(key, pred) {
  const cur = (D.lists[key] || []).filter((e) => D.items[e.id] && pred(D.items[e.id])).slice(0, TOP);
  const prevRaw = D.prev[key] || [];
  const prevIds = prevRaw.filter((p) => pred({ f: p[1], g: p[2] || [] })).map((p) => p[0]);
  const pr = new Map(prevIds.map((id, i) => [id, i + 1]));
  return cur.map((e, i) => ({ ...e, rank: i + 1, prev: prevRaw.length ? (pr.get(e.id) ?? null) : undefined }));
}

function chgHtml(e) {
  if (e.prev === undefined) return "";
  if (e.prev === null || e.prev > TOP) return `<span class="chg new">NEW</span>`;
  const d = e.prev - e.rank;
  if (d > 0) return `<span class="chg up" aria-label="${d}계단 상승">▲${d}</span>`;
  if (d < 0) return `<span class="chg down" aria-label="${-d}계단 하락">▼${-d}</span>`;
  return `<span class="muted">–</span>`;
}

function cellHtml(e) {
  const it = D.items[e.id];
  const fam = (e.fam || []).map((m) => D.items[m]).filter(Boolean);
  const famSrc = [...new Set(fam.map((m) => m.s).filter((s) => s !== it.s))];
  return `<div class="cell" tabindex="0" data-id="${esc(e.id)}" data-fam="${esc((e.fam || []).join(","))}" data-text="${esc(e.text)}">
    <div class="ttl">${esc(it.t)}</div>
    <div class="foot"><span class="badge src-${it.s}">${SRC_KO[it.s]}</span><span class="badge">${esc(fieldShort(it.f))}</span>
      ${famSrc.map((s) => `<span class="badge fam" title="연결된 ${SRC_KO[s]}">+${SRC_KO[s]}</span>`).join("")}
      ${e.flag ? `<span class="badge warn" title="비정상 급증 — 인기점수 50% 감점">검증 필요</span>` : ""}</div>
    <div class="desc">${esc(it.d)}</div>
    <div class="met">${esc(e.text)}</div>
    <div class="foot"><a href="${esc(it.u)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">🔗 원본</a>
      <span>·</span>${chgHtml(e)}<span class="muted" title="점수(0~100)">${e.score.toFixed(1)}점</span></div>
  </div>`;
}

function tableHtml(view, axis, pred, period) {
  const cols = PERIODS.map((p) => topList(`${view}|${p}|${axis}`, pred));
  const head = `<thead><tr><th>순위</th>${PERIODS.map((p) =>
    `<th class="pcol ${p === period ? "show" : ""}">${PERIOD_HEAD[p]}<small>${esc(D.periods[p].label)}${modeHint(view, p)}</small></th>`).join("")}</tr></thead>`;
  let rows = "";
  for (let r = 0; r < TOP; r++) {
    rows += `<tr><td>${r + 1}</td>${cols.map((c, j) =>
      `<td class="pcol ${PERIODS[j] === period ? "show" : ""}">${c[r] ? cellHtml(c[r]) : `<span class="empty">${r === 0 ? "해당 항목 없음" : ""}</span>`}</td>`).join("")}</tr>`;
  }
  return head + `<tbody>${rows}</tbody>`;
}

function modeHint(view, p) {
  const m = D.periods[p].modes;
  if (p === "total") return "";
  if (view === "paper") return " · 기간 내 공개 논문";
  const srcs = view === "integrated" ? ["github", "hf_model", "hf_dataset"] : [view];
  return srcs.some((s) => m[s] === "cohort") ? " · 기간 내 신규 공개 기준" : " · 기간 내 증가량";
}

function noteFor(view) {
  const cold = PERIODS.filter((p) => p !== "total").filter((p) => {
    const srcs = view === "integrated" ? ["github", "hf_model", "hf_dataset"] : view === "paper" ? [] : [view];
    return srcs.some((s) => D.periods[p].modes[s] === "cohort");
  }).map((p) => PERIOD_SHORT[p]);
  const parts = [];
  if (cold.length) parts.push(`스냅샷 축적 중 — ${cold.join("·")} 순위의 저장소·모델은 ‘그 기간에 새로 공개된 항목’ 안에서 평가합니다(누적값 = 기간 내 증가량). 기준일 스냅샷이 쌓이면 모든 항목의 증가량 순위로 자동 전환됩니다.`);
  if (view === "paper" || view === "integrated") parts.push("논문은 공개 시기 코호트(그 기간에 공개된 논문끼리) 비교 방식입니다.");
  return parts.join(" ");
}

/* ---------- 렌더 ---------- */
function renderInt() {
  const box = $("#int-fields");
  const opts = [["all", "전체"], ...D.fields.order.map((f) => [f, fieldLabel(f)]), ...Object.entries(D.tags)];
  box.innerHTML = opts.map(([k, v]) => {
    const on = k === "all" ? !state.intFields.size : state.intFields.has(k);
    return `<button class="chip ${D.tags[k] ? "tag" : ""} ${on ? "on" : ""}" data-k="${k}" aria-pressed="${on}">${esc(v)}</button>`;
  }).join("");
  $("#int-table").innerHTML = tableHtml("integrated", state.intAxis, matchInt, state.intPeriod);
  $("#int-note").textContent = noteFor("integrated");
}

function renderSrc() {
  const isPaper = state.src === "paper";
  const fields = isPaper ? D.fields.order : ["all", ...D.fields.order];
  if (!fields.includes(state.srcField) && !D.tags[state.srcField]) state.srcField = fields[0];
  const tagBtns = isPaper ? [] : Object.entries(D.tags);
  $("#src-fields").innerHTML = fields.map((f) =>
    `<button data-v="${f}" class="${state.srcField === f ? "on" : ""}">${f === "all" ? "전체" : esc(fieldLabel(f))}</button>`).join("") +
    tagBtns.map(([k, v]) => `<button data-v="${k}" class="${state.srcField === k ? "on" : ""}">#${esc(v)}</button>`).join("");
  $("#src-tabs").querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === state.src));
  $("#src-table").innerHTML = tableHtml(state.src, state.srcAxis, matchSrc, state.srcPeriod);
  $("#src-note").textContent = noteFor(state.src);
}

function renderSegs() {
  document.querySelectorAll(".seg[data-target]").forEach((seg) => {
    const t = seg.dataset.target;
    if (t.endsWith("period")) {
      seg.innerHTML = PERIODS.map((p) => `<button data-v="${p}">${PERIOD_SHORT[p]}</button>`).join("");
    }
    const key = { "int-axis": "intAxis", "src-axis": "srcAxis", "int-period": "intPeriod", "src-period": "srcPeriod", "mx-period": "mxPeriod" }[t];
    seg.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === state[key]));
  });
}

function renderHeader() {
  $("#updated").textContent = `${D.generated_at} KST`;
  const st = D.status || {};
  $("#src-status").innerHTML = Object.entries(STATUS_KO).filter(([k]) => st[k]).map(([k, v]) =>
    `<span class="${st[k].ok ? "" : "bad"}" title="${st[k].ok ? `${nf(st[k].items)}건 수집` : esc(st[k].error || "실패")}">${v}${st[k].ok ? "" : " 지연"}</span>`).join("");
}

function renderTrends() {
  const T = D.trends;
  $("#summary").innerHTML = (T.summary && T.summary.length ? T.summary : ["오늘은 요약을 생성하지 못했습니다."]).map((s) => `<li>${esc(s)}</li>`).join("");
  $("#keywords").innerHTML = (T.keywords || []).map((k) =>
    `<li><span class="kw" title="${esc(k.k)}">${esc(k.k)}</span><span class="ratio">${k.new ? "NEW" : `×${k.ratio}`}</span><span class="cnt">${k.n}건</span></li>`).join("")
    || `<li class="muted">기준(2배 이상·최소 5건)을 넘은 키워드 없음</li>`;
  const ne = (T.new_entries || []).filter((id) => D.items[id]);
  $("#new-entries").innerHTML = ne.length ? ne.map((id) => `<button class="chip" data-id="${esc(id)}">${esc(D.items[id].t)}</button>`).join("")
    : `<span class="muted" style="font-size:13px">${Object.keys(D.prev).some((k) => D.prev[k].length) ? "신규 진입 없음" : "내일부터 전일 대비 비교가 시작됩니다"}</span>`;
  drawShare();
}

function drawShare() {
  const S = D.trends.share;
  if (!S || !window.Chart) return;
  const colors = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6"].map(css);
  const ds = D.fields.order.map((f, i) => ({
    label: fieldLabel(f), data: S.share[f], borderColor: colors[i], backgroundColor: colors[i],
    borderWidth: 2, pointRadius: 0, pointHoverRadius: 5, tension: 0.25,
  }));
  charts.share?.destroy();
  charts.share = new Chart($("#share-chart"), {
    type: "line", data: { labels: S.weeks, datasets: ds },
    options: {
      maintainAspectRatio: false, interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, color: css("--text-2"), font: { size: 12 } } },
        tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y}%`, title: (c) => `${c[0].label} 주` } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: css("--muted"), maxRotation: 0, autoSkip: true } },
        y: { beginAtZero: true, grid: { color: css("--line") }, ticks: { color: css("--muted"), callback: (v) => v + "%" } },
      },
    },
  });
  $("#share-table").innerHTML = `<div class="tv-scroll"><table><tr><th>분야</th>${S.weeks.map((w) => `<th>${w}</th>`).join("")}</tr>` +
    D.fields.order.map((f) => `<tr><th style="text-align:left">${esc(fieldLabel(f))}</th>${S.share[f].map((v) => `<td>${v}</td>`).join("")}</tr>`).join("") + "</table></div>";
}

function drawMatrix() {
  if (!window.Chart) return;
  const pts = (D.matrix[state.mxPeriod] || []).filter((p) => D.items[p[0]]).map((p) => ({ x: p[1], y: p[2], id: p[0] }));
  charts.mx?.destroy();
  const quad = {
    id: "quad", beforeDraw(c) {
      const { ctx, chartArea: a, scales: { x, y } } = c;
      ctx.save(); ctx.strokeStyle = css("--muted"); ctx.setLineDash([4, 4]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x.getPixelForValue(50), a.top); ctx.lineTo(x.getPixelForValue(50), a.bottom);
      ctx.moveTo(a.left, y.getPixelForValue(50)); ctx.lineTo(a.right, y.getPixelForValue(50)); ctx.stroke(); ctx.restore();
    },
  };
  charts.mx = new Chart($("#matrix-chart"), {
    type: "scatter", plugins: [quad],
    data: { datasets: [{ data: pts, backgroundColor: css("--s1") + "cc", borderColor: css("--surface"), borderWidth: 2, pointRadius: 6, pointHoverRadius: 9, pointHitRadius: 10 }] },
    options: {
      maintainAspectRatio: false,
      onClick: (_, el) => { if (el.length) openDetail(pts[el[0].index].id); },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (c) => { const it = D.items[c.raw.id]; return ` ${it.t} (${SRC_KO[it.s]}) — 인기 ${c.raw.x.toFixed(0)} · 영향력 ${c.raw.y.toFixed(0)}`; } } },
      },
      scales: {
        x: { min: 0, max: 100, title: { display: true, text: "인기 점수 →", color: css("--text-2") }, grid: { color: css("--line") }, ticks: { color: css("--muted") } },
        y: { min: 0, max: 100, title: { display: true, text: "영향력 점수 →", color: css("--text-2") }, grid: { color: css("--line") }, ticks: { color: css("--muted") } },
      },
    },
  });
}

function renderMethod() {
  const W = {
    "GitHub 저장소": { 인기: [["기간 내 스타 증가량", .6], ["기간 내 포크 증가량", .2], ["기간 내 이슈·PR 생성", .2]], 영향력: [["의존 저장소(Used by)*", .35], ["패키지 다운로드*", .25], ["기여자 수", .15], ["건강도(최근 커밋·푸시)", .15], ["누적 포크", .1]] },
    "Hugging Face 모델·데이터셋": { 인기: [["기간 내 좋아요 증가량", .5], ["trendingScore", .3], ["최근 30일 다운로드", .2]], 영향력: [["파생 모델 수", .4], ["패밀리 다운로드", .3], ["사용 Spaces 수", .2], ["누적 좋아요", .1]] },
    "AI 논문": { 인기: [["HF Daily Papers 업보트", .4], ["코드 저장소 스타", .3], ["인용 속도", .3]], 영향력: [["영향력 있는 인용", .4], ["연령 보정 인용", .3], ["코드·모델 채택", .2], ["학회 인정", .1]] },
  };
  $("#method").innerHTML = Object.entries(W).map(([k, ax]) => `<div><h3>${k}</h3><table>${Object.entries(ax).map(([a, rows]) =>
    `<tr><td class="ax" colspan="2">${a}</td></tr>` + rows.map(([n, w]) => `<tr><td>${n}</td><td>${w.toFixed(2)}</td></tr>`).join("")).join("")}</table></div>`).join("") +
    `<p style="grid-column:1/-1">점수 = Σ 가중치 × 백분위. 지표는 log(1+x) 변환 후 백분위(0~100)로 정규화하며, 인기는 소스 전체, 영향력은 ‘분야 × 시기’ 그룹 안에서 계산합니다.
    값이 없는 지표(* 표시: 공식 API로 수집 불가)는 가중치를 나머지에 비례 재분배합니다. 스타·좋아요가 평소 대비 z≥4로 급증하면서 동반 지표 변화가 없으면 인기점수를 50% 감점하고 ‘검증 필요’로 표시합니다.</p>`;
}

/* ---------- 상세 패널 ---------- */
function openDetail(id, text) {
  const it = D.items[id];
  if (!it) return;
  const famIds = new Set();
  for (const lst of Object.values(D.lists)) for (const e of lst) if (e.id === id && e.fam) e.fam.forEach((m) => famIds.add(m));
  const links = [`<a href="${esc(it.u)}" target="_blank" rel="noopener">원본 열기 ↗</a>`, ...Object.entries(it.l || {}).map(([k, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(k)} ↗</a>`)];
  const ranks = [];
  for (const [key, lst] of Object.entries(D.lists)) {
    const [v, p, a] = key.split("|");
    const i = lst.findIndex((e) => e.id === id);
    if (i >= 0 && i < 50) ranks.push(`<tr><td>${v === "integrated" ? "통합" : SRC_KO[v]} · ${PERIOD_SHORT[p]} · ${a === "popularity" ? "인기" : "영향력"}</td><td>${i + 1}위 (${lst[i].score.toFixed(1)}점)</td></tr>`);
  }
  const meta = [["분야", fieldLabel(it.f)], ["공개/생성일", it.c], ["arXiv 분류", it.cat], ["파이프라인", it.pt], ["학회", it.v],
    ["관심 태그", (it.g || []).map((g) => D.tags[g]).join(", ")]].filter(([, v]) => v);
  $("#d-body").innerHTML = `
    <span class="badge src-${it.s}">${SRC_KO[it.s]}</span>
    <div class="d-title" id="d-title">${esc(it.t)}</div>
    <div class="d-desc">${esc(it.d)}${it.ko ? "" : ""}</div>
    ${(it.kw || []).length ? `<div class="chips" style="margin-top:8px">${it.kw.map((k) => `<span class="badge">${esc(k)}</span>`).join("")}</div>` : ""}
    <div class="d-links">${links.join("")}</div>
    <table class="d-table">${meta.map(([k, v]) => `<tr><td>${k}</td><td>${esc(v)}</td></tr>`).join("")}</table>
    <h3>현재 지표</h3>
    <table class="d-table">${Object.entries(it.m || {}).map(([k, v]) => `<tr><td>${METRIC_KO[k] || k}</td><td>${k === "venue" ? (v ? "예" : "아니오") : nf(v)}</td></tr>`).join("")}</table>
    <div class="d-chart" ${D.history[id] ? "" : 'style="display:none"'}><canvas id="d-canvas"></canvas></div>
    ${D.history[id] ? "" : `<p class="muted" style="font-size:12px;margin-top:8px">지표 추이 그래프는 스냅샷이 2일 이상 쌓이면 표시됩니다.</p>`}
    ${ranks.length ? `<h3>순위 (상위 50위 이내)</h3><table class="d-table">${ranks.join("")}</table>` : ""}
    ${famIds.size ? `<h3>연결된 논문·코드·모델 (연구 패밀리)</h3><div class="fam-list">${[...famIds].filter((m) => D.items[m]).map((m) =>
      `<a href="#" data-id="${esc(m)}"><span class="badge src-${D.items[m].s}">${SRC_KO[D.items[m].s]}</span> ${esc(D.items[m].t)}</a>`).join("")}</div>` : ""}`;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  charts.d?.destroy();
  const h = D.history[id];
  if (h && window.Chart) {
    const [m1] = Object.keys(h);
    charts.d = new Chart($("#d-canvas"), {
      type: "line",
      data: { labels: h[m1].map((x) => x[0].slice(5)), datasets: [{ label: METRIC_KO[m1] || m1, data: h[m1].map((x) => x[1]), borderColor: css("--s1"), borderWidth: 2, pointRadius: 2, tension: .2 }] },
      options: { maintainAspectRatio: false, interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false }, title: { display: true, text: `${METRIC_KO[m1] || m1} 추이`, color: css("--text-2") } },
        scales: { x: { grid: { display: false }, ticks: { color: css("--muted") } }, y: { grid: { color: css("--line") }, ticks: { color: css("--muted") } } } },
    });
  }
  $(".drawer .x").focus();
}
function closeDetail() { $("#drawer").classList.remove("open"); $("#drawer").setAttribute("aria-hidden", "true"); }

/* ---------- 이벤트 ---------- */
function init() {
  renderHeader(); renderSegs(); renderTrends(); renderInt(); renderSrc(); drawMatrix(); renderMethod();
  document.addEventListener("click", (ev) => {
    const seg = ev.target.closest(".seg[data-target] button");
    if (seg) {
      const t = seg.parentElement.dataset.target;
      const key = { "int-axis": "intAxis", "src-axis": "srcAxis", "int-period": "intPeriod", "src-period": "srcPeriod", "mx-period": "mxPeriod" }[t];
      state[key] = seg.dataset.v; save(); renderSegs();
      if (t.startsWith("int")) renderInt(); else if (t.startsWith("src")) renderSrc(); else drawMatrix();
      return;
    }
    const chip = ev.target.closest("#int-fields .chip");
    if (chip) {
      const k = chip.dataset.k;
      if (k === "all") state.intFields.clear(); else state.intFields.has(k) ? state.intFields.delete(k) : state.intFields.add(k);
      save(); renderInt(); return;
    }
    const tab = ev.target.closest("#src-tabs button");
    if (tab) { state.src = tab.dataset.v; if (state.src === "paper" && !D.fields.order.includes(state.srcField)) state.srcField = D.fields.order[0]; save(); renderSrc(); return; }
    const sub = ev.target.closest("#src-fields button");
    if (sub) { state.srcField = sub.dataset.v; save(); renderSrc(); return; }
    const cell = ev.target.closest(".cell, .new-entries .chip, .fam-list a");
    if (cell) { ev.preventDefault(); openDetail(cell.dataset.id); return; }
    if (ev.target.closest("[data-close]")) closeDetail();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeDetail();
    if (ev.key === "Enter" && ev.target.classList?.contains("cell")) openDetail(ev.target.dataset.id);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener?.("change", () => { drawShare(); drawMatrix(); });
}
