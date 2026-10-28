"use strict";
/* Tennis Value : interface (aucune dépendance). Données : API JSON de tennisvalue.web.api. */

const TZ = "Europe/Paris";
const $ = (sel, root = document) => root.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const NF = {};
const nf = (d) => (NF[d] ||= new Intl.NumberFormat("fr-FR", { minimumFractionDigits: d, maximumFractionDigits: d }));
const num = (x, d = 2) => (x == null ? "–" : nf(d).format(x));
const euros = (x) => (x == null ? "–" : nf(Math.abs(x - Math.round(x)) < 0.005 ? 0 : 2).format(x) + " €");
const signed = (s, x) => (x > 0 ? "+" : "") + s;
const pct = (x, d = 0, sign = false) => (x == null ? "–" : (sign && x > 0 ? "+" : "") + nf(d).format(100 * x) + " %");
const pts = (x) => (x == null ? "–" : (x > 0 ? "+" : "") + nf(1).format(x) + " pt" + (Math.abs(x) >= 2 ? "s" : ""));
const short = (name) => { const p = String(name || "").trim().split(/\s+/); return p.length > 1 ? p.slice(1).join(" ") : p[0]; };

const DAY = new Intl.DateTimeFormat("fr-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
const HM = new Intl.DateTimeFormat("fr-FR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
const WD = new Intl.DateTimeFormat("fr-FR", { timeZone: TZ, weekday: "short", day: "2-digit", month: "2-digit" });
const FULL = new Intl.DateTimeFormat("fr-FR", { timeZone: TZ, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
const dayKey = (d) => DAY.format(d);
function dayLabel(iso) {
  const d = new Date(iso), today = new Date();
  const diff = Math.round((Date.parse(dayKey(d)) - Date.parse(dayKey(today))) / 864e5);
  return diff === 0 ? "aujourd'hui" : diff === 1 ? "demain" : diff === -1 ? "hier" : WD.format(d);
}
const hm = (iso) => HM.format(new Date(iso));
const when = (iso) => `${dayLabel(iso)} ${hm(iso)}`;
const full = (iso) => (iso ? FULL.format(new Date(iso)) : "–");
function ago(iso) {
  if (!iso) return "jamais";
  const m = Math.floor((Date.now() - Date.parse(iso)) / 60000);
  if (m < 1) return "à l'instant";
  if (m < 60) return `il y a ${m} min`;
  if (m < 48 * 60) { const h = Math.floor(m / 60), r = m % 60; return r ? `il y a ${h} h ${String(r).padStart(2, "0")}` : `il y a ${h} h`; }
  return `il y a ${Math.floor(m / 1440)} jours`;
}

/* ------------------------------------------------------------------ API, messages */
async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new Error((data && data.detail) || `Erreur ${r.status}`);
  return data;
}
let toastTimer;
function toast(msg, error = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (error ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), error ? 7000 : 3500);
}
async function busy(text, fn) {
  $("#overlay-text").innerHTML = text;
  $("#overlay").classList.remove("hidden");
  try { return await fn(); } finally { $("#overlay").classList.add("hidden"); }
}

/* ------------------------------------------------------------------ état */
const S = {
  status: null, matches: [], details: {}, news: {}, open: new Set(), slip: null, page: "bons-coups",
  filter: { tour: "all", tournament: null, value: false }, moversHours: 0, oddsSide: {}, bets: null,
};
const ORIGIN = {
  "informé": { cls: "informe", label: "Mouvement sharp" },
  "grand public": { cls: "public", label: "Mouvement FR" },
  "contradictoire": { cls: "contra", label: "Contradictoire" },
  "stable": { cls: "stable", label: "Stable" },
};
const PALETTE = ["#ff4d5e", "#60a5fa", "#22c55e", "#fbbf24", "#c084fc", "#fb923c", "#2dd4bf", "#f472b6", "#a3e635"];
const colorFor = (() => { const m = {}; let i = 0; return (key, sharp) => (sharp ? (key === "pinnacle" ? "#e5e7eb" : "#9ca3af") : (m[key] ||= PALETTE[i++ % PALETTE.length])); })();

async function loadStatus() {
  S.status = await api("/api/status");
  renderStatus();
}
async function loadMatches() {
  S.matches = await api("/api/matches");
  S.details = {};
  S.news = {};
  renderTournaments();
}

function renderStatus() {
  const st = S.status, pill = $("#status-pill");
  let dot = "bad", txt = "Aucun relevé de cotes";
  if (st.last_fetch) {
    const h = (Date.now() - Date.parse(st.last_fetch)) / 36e5;
    dot = h < 4 ? "ok" : h < 24 ? "warn" : "bad";
    txt = `Cotes relevées ${ago(st.last_fetch)} · ${st.upcoming} match${st.upcoming > 1 ? "s" : ""} à venir`;
  }
  pill.innerHTML = `<span class="dot ${dot}"></span><span class="txt">${esc(txt)}</span>`;
  const f = $("#btn-fetch");
  f.disabled = !st.can_fetch;
  f.title = st.can_fetch ? "Nouveau relevé tout de suite (≈ 2 crédits The Odds API par tournoi ouvert)"
    : "Indisponible : ni clé locale ni GitHub CLI connecté (voir Réglages)";
}

function renderTournaments() {
  const counts = {};
  for (const m of S.matches) counts[m.tournament] = (counts[m.tournament] || 0) + 1;
  $("#count-matchs").textContent = S.matches.length || "";
  $("#count-picks").textContent = S.matches.reduce((n, m) => n + (m.verdict_1 === "haute") + (m.verdict_2 === "haute"), 0) || "";
  $("#nav-tournaments").innerHTML = Object.entries(counts).map(([t, n]) =>
    `<a class="item tournament ${S.filter.tournament === t ? "active" : ""}" href="#/matchs" data-act="tournament" data-t="${esc(t)}">${esc(t)}<span class="count">${n}</span></a>`
  ).join("") || `<p class="disclaimer" style="margin-top:4px">Aucun tournoi ouvert.</p>`;
}

/* ------------------------------------------------------------------ graphiques (SVG) */
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo, step0 = span / n, mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
  return out;
}

function lineChart(el, cfg) {
  const { series, height = 220, yFmt = (v) => num(v), xFmt = null, tipX = null } = cfg;
  el.classList.add("chart");
  const W = Math.max(el.clientWidth, 280), H = height, m = { l: 48, r: 12, t: 10, b: 24 };
  const vis = series.filter((s) => !s.hidden && s.points.length);
  const all = vis.flatMap((s) => s.points);
  const legend = series.length > 1 ? `<div class="legend">${series.map((s, i) =>
    `<span data-i="${i}" class="${s.hidden ? "off" : ""}"><i style="background:${s.color}${s.dashed ? ";opacity:.7" : ""}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  if (!all.length) { el.innerHTML = `<p class="muted">Pas encore de données.</p>${legend}`; bindLegend(); return; }
  let x0 = Math.min(...all.map((p) => p[0])), x1 = Math.max(...all.map((p) => p[0]));
  if (x0 === x1) { const d = xFmt ? 1 : 36e5; x0 -= d; x1 += d; }
  let y0 = Math.min(...all.map((p) => p[1])), y1 = Math.max(...all.map((p) => p[1]));
  const pad = (y1 - y0) * 0.15 || Math.abs(y1) * 0.04 || 1;
  y0 -= pad; y1 += pad;
  const X = (t) => m.l + ((t - x0) / (x1 - x0)) * (W - m.l - m.r);
  const Y = (v) => m.t + (1 - (v - y0) / (y1 - y0)) * (H - m.t - m.b);
  const yt = niceTicks(y0, y1, 4);
  const nx = Math.max(2, Math.min(6, Math.floor((W - m.l) / 110)));
  const xt = Array.from({ length: nx }, (_, i) => x0 + ((x1 - x0) * i) / (nx - 1));
  const longSpan = x1 - x0 > 20 * 36e5;
  const xLabel = xFmt || ((t) => (longSpan ? `${WD.format(new Date(t)).split(" ")[0]} ${HM.format(new Date(t))}` : HM.format(new Date(t))));
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><g class="grid">`;
  svg += yt.map((v) => `<line x1="${m.l}" x2="${W - m.r}" y1="${Y(v)}" y2="${Y(v)}"/>`).join("");
  svg += `</g><g class="axis">`;
  svg += yt.map((v) => `<text x="${m.l - 6}" y="${Y(v) + 3.5}" text-anchor="end">${esc(yFmt(v))}</text>`).join("");
  svg += xt.map((t, i) => `<text x="${X(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === nx - 1 ? "end" : "middle"}">${esc(xLabel(t))}</text>`).join("");
  svg += `</g>`;
  for (const s of vis) {
    const d = s.points.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    svg += `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dashed ? 'stroke-dasharray="5 4"' : ""} stroke-linejoin="round"/>`;
    if (s.points.length < 40) svg += s.points.map((p) => `<circle cx="${X(p[0])}" cy="${Y(p[1])}" r="2.6" fill="${s.color}"/>`).join("");
  }
  svg += `<line class="hover-line hidden" y1="${m.t}" y2="${H - m.b}"/><rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent"/></svg>`;
  el.innerHTML = svg + `<div class="tooltip hidden"></div>` + legend;

  const times = [...new Set(all.map((p) => p[0]))].sort((a, b) => a - b);
  const line = el.querySelector(".hover-line"), tip = el.querySelector(".tooltip"), rect = el.querySelector("rect");
  rect.addEventListener("mousemove", (ev) => {
    const box = el.querySelector("svg").getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const t = x0 + ((px - m.l) / (W - m.l - m.r)) * (x1 - x0);
    const near = times.reduce((a, b) => (Math.abs(b - t) < Math.abs(a - t) ? b : a));
    line.setAttribute("x1", X(near)); line.setAttribute("x2", X(near)); line.classList.remove("hidden");
    const rows = vis.map((s) => {
      const p = [...s.points].reverse().find((q) => q[0] <= near);
      return p ? `<div class="row"><i style="width:10px;height:3px;background:${s.color};display:inline-block"></i>${esc(s.name)}<b>${esc(yFmt(p[1]))}</b></div>` : "";
    }).join("");
    tip.innerHTML = `<div class="t">${esc(tipX ? tipX(near) : full(new Date(near).toISOString()))}</div>${rows}`;
    tip.classList.remove("hidden");
    const left = (X(near) / W) * box.width;
    tip.style.left = `${Math.min(left + 12, box.width - tip.offsetWidth - 4)}px`;
    tip.style.top = `8px`;
  });
  rect.addEventListener("mouseleave", () => { line.classList.add("hidden"); tip.classList.add("hidden"); });
  bindLegend();
  function bindLegend() {
    el.querySelectorAll(".legend span").forEach((sp) => sp.addEventListener("click", () => {
      const s = series[+sp.dataset.i]; s.hidden = !s.hidden; lineChart(el, cfg);
    }));
  }
}

function sparkline(values, color) {
  const v = values.filter((x) => x != null);
  if (v.length < 2) return `<span class="faint">–</span>`;
  const W = 120, H = 30, span = Math.max(Math.max(...v) - Math.min(...v), 0.04);
  const lo = (Math.max(...v) + Math.min(...v)) / 2 - span / 2;
  const d = v.map((x, i) => `${i ? "L" : "M"}${((i / (v.length - 1)) * (W - 4) + 2).toFixed(1)},${(H - 3 - ((x - lo) / span) * (H - 6)).toFixed(1)}`).join("");
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}"><path d="${d}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}

/* ------------------------------------------------------------------ page Matchs */
function valueSide(m) { return m.statut === "VALUE" ? (m.pari === m.player_1 ? 1 : 2) : 0; }
function pSide(m, side) { return m.p_decision == null ? null : side === 1 ? m.p_decision : 1 - m.p_decision; }

function oddButton(m, side) {
  const cote = m[`cote_${side}`], ouv = m[`ouv_${side}`], v = m[`verdict_${side}`];
  if (cote == null) return `<div class="odd" style="cursor:default"><span class="lbl">${esc(short(m[`player_${side}`]))}</span><span class="val">–</span><span class="bk">aucune cote FR</span></div>`;
  const arrow = ouv != null && cote > ouv + 1e-9 ? `<span class="arrow-up" title="Cote en hausse depuis l'ouverture (${num(ouv)})">▲</span>`
    : ouv != null && cote < ouv - 1e-9 ? `<span class="arrow-down" title="Cote en baisse depuis l'ouverture (${num(ouv)})">▼</span>` : "";
  const sel = S.slip && S.slip.ev === m.event_id && S.slip.side === side ? " selected" : "";
  const cls = v === "haute" ? " good-odd" : v === "basse" ? " trap-odd" : "";
  const tip = [m[`ma_cote_${side}`] != null ? `Ma cote : ${num(m[`ma_cote_${side}`])}` : "", m[`cote_juste_${side}`] != null ? `cote juste (Pinnacle) : ${num(m[`cote_juste_${side}`])}` : ""].filter(Boolean).join(" · ");
  return `<button class="odd${sel}${cls}" data-act="pick" data-ev="${esc(m.event_id)}" data-side="${side}" title="${esc(tip || "Ajouter au pari")}">
    <span class="lbl">${esc(short(m[`player_${side}`]))}</span><span class="val num">${num(cote)}${arrow}</span><span class="bk">${esc(m[`book_${side}_name`] || "")}</span></button>`;
}

const NEWS_TAGS = { blessure: "blessure / forfait", fatigue: "fatigue", moral: "moral en baisse" };
function newsAlert(m) {
  for (const side of [1, 2]) {
    const n = m.news && m.news[side];
    const hits = n ? Object.keys(NEWS_TAGS).filter((k) => n[k]) : [];
    if (hits.length) return `⚠️ ${short(m[`player_${side}`])} : ${hits.map((k) => NEWS_TAGS[k]).join(", ")} dans l'actualité`;
  }
  return "";
}
const disagrees = (m) => m.historique_ok && m.ecart_modele != null && Math.abs(m.ecart_modele) >= S.status.settings.disagree_pp;

function analysisBlock(m) {
  const lines = [];
  if (m.ma_cote_1 != null) lines.push(`<div title="Mes cotes : d'après le niveau, la surface, la forme, le tournoi, la fatigue, les blessures, le mental et le face-à-face">🎯 Mes cotes <b class="num">${num(m.ma_cote_1)}</b> · <b class="num">${num(m.ma_cote_2)}</b></div>`);
  for (const side of [1, 2]) {
    const v = m[`verdict_${side}`], who = esc(short(m[`player_${side}`]));
    if (v === "haute") lines.push(`<div class="verdict good">✅ ${who} : cote trop haute (${pct(m[`value_${side}`], 0, true)})</div>`);
    else if (v === "basse") lines.push(`<div class="verdict trap">⛔ ${who} : cote trop basse (surcoté de ${num(m[`ecart_fr_${side}`], 0)} pts)</div>`);
  }
  const f = m.flux;
  if (f && f.n_releves >= 2 && f.vers) {
    const o = ORIGIN[f.origine] || ORIGIN.stable;
    lines.push(`<div class="faint" title="Évolution de la chance donnée par les bookmakers depuis l'ouverture">📉 → ${esc(short(m[`player_${f.vers}`]))} ${pts(Math.abs(f.flux))} · <span class="origin o-${o.cls}">${o.label}</span></div>`);
  }
  const alert = newsAlert(m);
  if (alert) lines.push(`<div class="newsflag">${esc(alert)}</div>`);
  return `<div class="flux">${lines.join("") || `<span class="faint">${m.p_model == null && !S.status.model_ready ? "Modèle à calculer" : "Pas de cote Pinnacle : pas de verdict"}</span>`}</div>`;
}

function matchCard(m) {
  const vs = valueSide(m), open = S.open.has(m.event_id);
  const tags = [];
  if (vs) tags.push(`<span class="tag value">VALUE ${pct(m.value, 1, true)} sur ${esc(short(m.pari))}</span>`);
  if (disagrees(m)) tags.push(`<span class="tag dig" title="Mon modèle et le marché diffèrent d'au moins ${num(S.status.settings.disagree_pp, 0)} points">🔍 À creuser</span>`);
  if (m.steam) tags.push(`<span class="tag steam" title="La cote Pinnacle / Betfair a bougé brusquement : vérifier l'actualité (forfait, blessure)">⚡ Mouvement brusque</span>`);
  if (m.my_bets && m.my_bets.length) tags.push(`<span class="tag bet">✔ Parié</span>`);
  return `<div class="match${vs ? " value" : ""}${open ? " open" : ""}" id="m-${esc(m.event_id)}">
    <div class="match-row" data-act="toggle" data-ev="${esc(m.event_id)}">
      <div class="when"><b>${hm(m.start)}</b>${esc(dayLabel(m.start))}</div>
      <div class="players"><div>${esc(m.player_1)}</div><div>${esc(m.player_2)}</div>${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}</div>
      <div class="odds">${oddButton(m, 1)}${oddButton(m, 2)}</div>
      ${analysisBlock(m)}
      <div class="chev">›</div>
    </div>
    ${open ? `<div class="detail" id="d-${esc(m.event_id)}">${S.details[m.event_id] ? "" : `<p class="muted">Chargement…</p>`}</div>` : ""}
  </div>`;
}

function banners() {
  const st = S.status, out = [];
  if (st.model_outdated) out.push(`<div class="banner warn"><span>🧮</span><span class="grow">Nouveau modèle (forme, surface, tournoi, fatigue, blessures, mental) : à calculer une fois pour obtenir mes cotes (1 à 3 minutes).</span><button class="btn small primary" data-act="rebuild">Calculer le modèle</button></div>`);
  else if (!st.model_ready) out.push(`<div class="banner warn"><span>🧮</span><span class="grow">Le modèle n'est pas encore calculé : mes cotes, les verdicts et les mises n'apparaîtront qu'après (1 à 3 minutes).</span><button class="btn small primary" data-act="rebuild">Calculer le modèle</button></div>`);
  else if (st.results_newer) out.push(`<div class="banner warn"><span>🆕</span><span class="grow">De nouveaux résultats de matchs sont arrivés.</span><button class="btn small primary" data-act="rebuild">Recalculer le modèle</button></div>`);
  if (st.data_until && (Date.now() - Date.parse(st.data_until)) / 864e5 > 10)
    out.push(`<div class="banner warn"><span>📅</span><span class="grow">Résultats arrêtés au ${esc(new Date(st.data_until).toLocaleDateString("fr-FR"))} : la forme récente des joueurs n'est plus à jour. Ajoute les nouveaux fichiers tennis-data dans Réglages.</span><a class="btn small" href="#/reglages">Réglages</a></div>`);
  return out.join("");
}

function renderMatchs() {
  const f = S.filter;
  let ms = S.matches;
  if (f.tour !== "all") ms = ms.filter((m) => m.tour === f.tour);
  if (f.tournament) ms = ms.filter((m) => m.tournament === f.tournament);
  if (f.value) ms = ms.filter((m) => m.statut === "VALUE");
  const nValue = S.matches.filter((m) => m.statut === "VALUE").length;
  const groups = new Map();
  for (const m of ms) { if (!groups.has(m.tournament)) groups.set(m.tournament, []); groups.get(m.tournament).push(m); }
  const chip = (act, val, label, active) => `<button class="chip${active ? " active" : ""}" data-act="${act}" data-v="${val}">${label}</button>`;
  $("#main").innerHTML = `
    <h1>${f.tournament ? esc(f.tournament) : "Matchs à venir"}</h1>
    <p class="subtitle">Meilleure cote française pour chaque joueur (▲ ▼ : depuis l'ouverture), encadrée en vert si elle est trop haute, en rouge si elle est trop basse. Clique sur un match pour mes cotes, les points forts et faibles, l'actualité et l'évolution des cotes.</p>
    ${banners()}
    <div class="toolbar">
      ${chip("tour", "all", "Tous", f.tour === "all")}${chip("tour", "ATP", "ATP", f.tour === "ATP")}${chip("tour", "WTA", "WTA", f.tour === "WTA")}
      ${chip("value", "", `✅ Paris conseillés (${nValue})`, f.value)}
      ${f.tournament ? `<button class="chip" data-act="tournament" data-t="">✕ ${esc(f.tournament)}</button>` : ""}
      <span class="spacer"></span><span class="muted">${ms.length} match${ms.length > 1 ? "s" : ""}</span>
    </div>
    ${ms.length ? [...groups].map(([t, list]) => `<div class="group"><div class="group-title"><span class="tour-badge">${esc(list[0].tour)}</span>${esc(t)}</div>${list.map(matchCard).join("")}</div>`).join("")
      : `<div class="empty"><div class="big">🎾</div>${S.matches.length ? "Aucun match pour ce filtre." : "Aucun match à venir dans les relevés.<br>Clique sur <b>⟳ Mettre à jour</b> ; s'il n'y a toujours rien, aucun tournoi couvert n'est ouvert aux paris."}</div>`}
    <p class="footer-note">Mes cotes : mon modèle, d'après le niveau, la surface, la forme, le tournoi, la fatigue, les blessures, le mental et le face-à-face. Verdict : meilleure cote française comparée à la cote juste (Pinnacle sans marge). 📉 « Mouvement sharp » : Pinnacle / Betfair ont bougé ; « Mouvement FR » : seuls les bookmakers français ont bougé.</p>`;
  for (const ev of S.open) if (S.details[ev]) renderDetail(ev);
}

async function toggleMatch(ev) {
  if (S.open.has(ev)) { S.open.delete(ev); renderMatchs(); return; }
  S.open.add(ev);
  renderMatchs();
  try {
    S.details[ev] = await api(`/api/matches/${encodeURIComponent(ev)}`);
    renderDetail(ev);
  } catch (e) { toast(e.message, true); }
}

function renderDetail(ev) {
  const d = S.details[ev], box = $(`#d-${CSS.escape(ev)}`);
  if (!d || !box) return;
  const side = S.oddsSide[ev] || 1;
  const f = d.flux, o = f ? ORIGIN[f.origine] || ORIGIN.stable : null;
  const c = d.consensus || [];
  const firstFr = c.find((r) => r[1] != null), lastFr = [...c].reverse().find((r) => r[1] != null);
  let moveText = "Pas encore assez de relevés pour mesurer un mouvement.";
  if (f && f.n_releves >= 2) {
    const who = f.vers ? d[`player_${f.vers}`] : null;
    moveText = `${f.n_releves} relevés depuis ${esc(when(f.debut))}. `;
    if (firstFr && lastFr) moveText += `Chance de <b>${esc(d.player_1)}</b> selon les bookmakers français : ${pct(firstFr[1])} → <b>${pct(lastFr[1])}</b> (${pts(f.delta_fr)}). `;
    if (f.delta_sharp != null) moveText += `Selon Pinnacle / Betfair : ${pts(f.delta_sharp)}. `;
    moveText += who ? `<br><span class="origin o-${o.cls}">${o.label}</span> : les cotes évoluent en faveur de <b>${esc(who)}</b>.` : `<br>Pas de mouvement net.`;
    if (d.flux_24h && d.flux_24h.n_releves >= 2 && d.flux_24h.n_releves < f.n_releves) moveText += ` Sur 24 h : ${pts(d.flux_24h.flux)} pour ${esc(short(d.player_1))}.`;
  }
  const shown = d.books.filter((b) => b.french || b.sharp), others = d.books.length - shown.length;
  const best1 = Math.max(...d.books.filter((b) => b.french).map((b) => b.odds_1)), best2 = Math.max(...d.books.filter((b) => b.french).map((b) => b.odds_2));
  const rows = shown.map((b) => `<tr><td>${esc(b.name)}${b.sharp ? ' <span class="tag">référence</span>' : ""}</td>
      <td class="r num"${b.french && b.odds_1 === best1 ? ' style="color:var(--green);font-weight:800"' : ""}>${num(b.odds_1)}</td>
      <td class="r num"${b.french && b.odds_2 === best2 ? ' style="color:var(--green);font-weight:800"' : ""}>${num(b.odds_2)}</td>
      <td class="r num">${pct(b.margin, 1)}</td></tr>`).join("");
  box.innerHTML = `
    ${myOddsCard(d)}
    ${factorsCard(d)}
    <div class="card"><h3>📉 Évolution des cotes<span class="right"><span class="seg">
      <button class="${side === 1 ? "active" : ""}" data-act="side" data-ev="${esc(ev)}" data-side="1">${esc(short(d.player_1))}</button>
      <button class="${side === 2 ? "active" : ""}" data-act="side" data-ev="${esc(ev)}" data-side="2">${esc(short(d.player_2))}</button></span></span></h3>
      <div id="co-${esc(ev)}"></div></div>
    ${compareCard(d)}
    <div class="card wide" id="news-${esc(ev)}"><h3>📰 Actualités de la semaine</h3><p class="muted">Recherche des derniers articles…</p></div>
    <div class="card"><h3>📈 Qui fait bouger les cotes ${o ? `<span class="origin o-${o.cls}" style="font-size:12px">${o.label}</span>` : ""}</h3>
      <div id="cf-${esc(ev)}"></div><p>${moveText}</p></div>
    <div class="card"><h3>🏦 Cotes par bookmaker</h3><div class="table-wrap"><table>
      <tr><th>Bookmaker</th><th class="r">${esc(short(d.player_1))}</th><th class="r">${esc(short(d.player_2))}</th><th class="r">Marge</th></tr>${rows}</table></div>
      ${others ? `<p class="faint">${others} bookmaker${others > 1 ? "s" : ""} étranger${others > 1 ? "s" : ""} non affiché${others > 1 ? "s" : ""}.</p>` : ""}</div>`;
  const oddsSeries = d.series.filter((s) => s.french || s.sharp).map((s) => ({ name: s.name, color: colorFor(s.key, s.sharp), dashed: s.sharp,
    points: s.points.map((p) => [Date.parse(p[0]), p[side]]) }));
  lineChart($(`#co-${CSS.escape(ev)}`), { series: oddsSeries, yFmt: (v) => num(v) });
  lineChart($(`#cf-${CSS.escape(ev)}`), {
    series: [
      { name: `Bookmakers français (chance de ${short(d.player_1)})`, color: "#60a5fa", width: 2.5, points: c.filter((r) => r[1] != null).map((r) => [Date.parse(r[0]), 100 * r[1]]) },
      { name: "Pinnacle / Betfair", color: "#e5e7eb", dashed: true, points: c.filter((r) => r[2] != null).map((r) => [Date.parse(r[0]), 100 * r[2]]) },
      ...(d.p_model != null ? [{ name: "Mon modèle", color: "#22c55e", points: c.length ? [[Date.parse(c[0][0]), 100 * d.p_model], [Date.parse(c[c.length - 1][0]), 100 * d.p_model]] : [] }] : []),
    ], yFmt: (v) => `${num(v, 0)} %`,
  });
  loadNews(ev);
}

const side_ = (p, s) => (p == null ? null : s === 1 ? p : 1 - p);

function myOddsCard(d) {
  const rows = [1, 2].map((s) => {
    const v = d[`verdict_${s}`];
    const verdict = v === "haute" ? `<span class="verdict good">✅ Trop haute (${pct(d[`value_${s}`], 0, true)})</span>`
      : v === "basse" ? `<span class="verdict trap">⛔ Trop basse</span>` : d[`cote_juste_${s}`] != null ? `<span class="faint">correcte</span>` : `<span class="faint">–</span>`;
    return `<tr><td><b>${esc(d[`player_${s}`])}</b></td>
      <td class="r num"><b>${num(d[`ma_cote_${s}`])}</b> <span class="faint">${pct(side_(d.p_model, s))}</span></td>
      <td class="r num"><b>${num(d[`cote_juste_${s}`])}</b> <span class="faint">${pct(side_(d.p_decision, s))}</span></td>
      <td class="r num">${pct(side_(d.p_fr, s))}</td>
      <td class="r num"><b>${num(d[`cote_${s}`])}</b> <span class="faint">${esc(d[`book_${s}_name`] || "")}</span></td><td>${verdict}</td></tr>`;
  }).join("");
  const notes = [];
  for (const s of [1, 2]) {
    const who = esc(d[`player_${s}`]);
    if (d[`verdict_${s}`] === "haute") {
      const ok = d[`modele_ok_${s}`];
      notes.push(`<p class="good-text">✅ La meilleure cote française sur <b>${who}</b> (${num(d[`cote_${s}`])} chez ${esc(d[`book_${s}_name`])}) paie plus que sa cote juste (${num(d[`cote_juste_${s}`])}) : value ${pct(d[`value_${s}`], 1, true)}.
        ${ok == null ? "" : ok ? "Mon modèle va dans le même sens." : `Mon modèle n'est pas d'accord (ma cote : ${num(d[`ma_cote_${s}`])}) : prudence.`}</p>`);
    } else if (d[`verdict_${s}`] === "basse") {
      notes.push(`<p class="trap-text">⛔ Les bookmakers français donnent <b>${who}</b> à ${pct(side_(d.p_fr, s))} de chances, Pinnacle à ${pct(side_(d.p_decision, s))} (${pts(d[`ecart_fr_${s}`])}) : sa cote est trop basse. C'est souvent le cas des joueurs populaires, sur qui le grand public mise de toute façon.</p>`);
    }
  }
  if (disagrees(d)) {
    const s = d.ecart_modele > 0 ? 1 : 2;
    notes.push(`<p>🔍 Mon modèle voit <b>${esc(d[`player_${s}`])}</b> à ${pct(side_(d.p_model, s))}, le marché à ${pct(side_(d.p_decision, s))}. Un tel écart vient souvent d'une information que le marché a et pas mon modèle (blessure, forfait, conditions) : lis les actualités avant de parier.</p>`);
  }
  if (d.statut === "VALUE") notes.push(`<p>Pari conseillé : <b>${esc(d.pari)}</b> à ${num(d.cote)}, mise conseillée <b>${euros(d.mise)}</b>.
    <button class="btn small primary" data-act="pick" data-ev="${esc(d.event_id)}" data-side="${d.pari === d.player_1 ? 1 : 2}">Préparer ce pari</button></p>`);
  if (d.steam) notes.push(`<p style="color:var(--gold)">⚡ Mouvement brusque de la cote Pinnacle / Betfair : vérifier l'actualité avant de parier.</p>`);
  if (d.tournoi_connu === false) notes.push(`<p class="faint">Tournoi non retrouvé dans l'historique : le facteur « Ce tournoi » est ignoré.</p>`);
  if (d.alerte_donnees) notes.push(`<p class="faint">⚠️ ${esc(d.alerte_donnees.replace(/[ ;]+$/, ""))}</p>`);
  return `<div class="card wide"><h3>🎯 Mes cotes et verdict</h3><div class="table-wrap"><table class="my-odds">
    <tr><th>Joueur</th><th class="r" title="Mon modèle : cote et chance">Ma cote</th><th class="r" title="Pinnacle / Betfair Exchange, marge retirée : cote et chance">Cote juste</th>
      <th class="r" title="Moyenne des bookmakers français, marge retirée">Chance FR</th><th class="r">Meilleure cote FR</th><th>Verdict</th></tr>${rows}</table></div>
    ${notes.join("") || `<p>Aucune cote française ne s'écarte assez de la cote juste pour l'instant.</p>`}
    <p class="faint" style="font-size:11.5px">Ma cote : mon modèle, d'après les performances des joueurs. Cote juste : Pinnacle sans sa marge, qui prédit mieux que mon modèle sur 2019-2026 (onglet Analyse) ; c'est elle qui décide du verdict.</p></div>`;
}

const FACT = { niveau: "Niveau général", surface: "Surface", forme: "Forme récente", tournoi: "Ce tournoi", fatigue: "Fatigue",
  physique: "Blessure / reprise", mental: "Mental", h2h: "Face-à-face" };
const FACT_HELP = { niveau: "Elo, note aux points, classement, expérience", surface: "Elo et résultats sur la surface (12 mois)",
  forme: "10 derniers matchs comparés à l'attendu, dynamique du classement, matchs enchaînés", tournoi: "Résultats dans ce tournoi les années passées",
  fatigue: "Jeux joués sur 7 et 14 jours, longueur du dernier match", physique: "Jours sans jouer, reprise après une pause, abandon ou forfait récent",
  mental: "Tie-breaks et sets décisifs gagnés, matchs remontés après la perte du 1er set", h2h: "Confrontations directes" };

function factorsCard(d) {
  if (!d.facteurs) return `<div class="card"><h3>🧩 Pourquoi ces chances</h3><p>Modèle non calculé, ou joueur absent de l'historique.</p></div>`;
  const items = Object.entries(d.facteurs).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const max = Math.max(0.04, ...items.map(([, v]) => Math.abs(v)));
  const bars = items.map(([k, v]) => {
    const w = (Math.abs(v) / max) * 50, who = v > 0 ? d.player_1 : d.player_2;
    return `<div class="fx" title="${esc(FACT_HELP[k])}"><span class="fx-name">${FACT[k]}</span>
      <div class="fx-bar"><span class="mid"></span><span class="fill ${v > 0 ? "p1" : "p2"}" style="${v > 0 ? "right" : "left"}:50%;width:${w.toFixed(1)}%"></span></div>
      <span class="fx-val">${Math.abs(v) < 0.005 ? `<span class="faint">égalité</span>` : `${pts(100 * Math.abs(v))} <span class="faint">${esc(short(who))}</span>`}</span></div>`;
  }).join("");
  return `<div class="card"><h3>🧩 Pourquoi ces chances</h3>
    <div class="fx-head"><span><i class="p1"></i>◀ avantage ${esc(short(d.player_1))}</span><span><i class="p2"></i>avantage ${esc(short(d.player_2))} ▶</span></div>${bars}
    <p class="faint" style="font-size:11.5px">Points de % gagnés grâce à chaque famille de facteurs selon mon modèle (survol : détail).</p></div>`;
}

const SURF_FR = { Hard: "dur", Clay: "terre battue", Grass: "gazon", Carpet: "moquette" };
function formStr(s) { return `<span class="form-str">${[...(s || "")].map((c) => `<span class="${c === "V" ? "w" : "l"}">${c}</span>`).join("")}</span>`; }

function compareCard(d) {
  const a = d.profil_1, b = d.profil_2;
  if (!a || !b) return "";
  const rate = (w, n) => (w + 2) / (n + 4);
  const wl = (w, l) => (w + l ? `${w}–${l} <span class="faint">(${pct(w / (w + l))})</span>` : `<span class="faint">aucun</span>`);
  const rankTxt = (p) => (p.rank ? `${num(p.rank, 0)}<sup>e</sup>${p.rank_3m && p.rank_3m !== p.rank ? ` <span class="${p.rank < p.rank_3m ? "arrow-up" : "arrow-down"}">${p.rank < p.rank_3m ? "▲" : "▼"} ${num(Math.abs(p.rank_3m - p.rank), 0)}</span>` : ""}` : "–");
  const last = (p) => (p.days_since >= 365 ? "–" : p.last_games && p.days_since <= 3 ? `il y a ${p.days_since} j (${num(p.last_games, 0)} jeux)` : `il y a ${p.days_since} j`);
  const pause = (p) => (p.break_days ? (p.since_break === 0 ? `<b>1er match après ${p.break_days} j d'arrêt</b>` : `${p.since_break} match${p.since_break > 1 ? "s" : ""} depuis ${p.break_days} j d'arrêt`) : "pas de longue pause");
  const inj = (p) => (p.inj_days <= 90 ? `<b>il y a ${p.inj_days} j</b>${p.inj_365 > 1 ? ` (${p.inj_365} en 1 an)` : ""}` : p.inj_365 ? `${p.inj_365} en 1 an` : "aucun");
  // [libellé, valeur J1, valeur J2, score J1, score J2 (le plus haut est mis en avant ; null : pas de comparaison)]
  const R = [
    ["Classement (évolution sur 3 mois)", rankTxt(a), rankTxt(b), a.rank ? -a.rank : null, b.rank ? -b.rank : null],
    [`Elo sur ${SURF_FR[d.surface] || "la surface"}`, num(a.elo_surf, 0), num(b.elo_surf, 0), a.elo_surf, b.elo_surf],
    ["10 derniers matchs (le plus récent à gauche)", `${formStr(a.form10)} ${a.n10 ? `${a.w10}/${a.n10}` : ""}`, `${formStr(b.form10)} ${b.n10 ? `${b.w10}/${b.n10}` : ""}`, a.surplus10 / (a.n10 + 3), b.surplus10 / (b.n10 + 3)],
    [`Sur ${SURF_FR[d.surface] || "la surface"} (12 mois)`, wl(a.surf_w, a.surf_n - a.surf_w), wl(b.surf_w, b.surf_n - b.surf_w), rate(a.surf_w, a.surf_n), rate(b.surf_w, b.surf_n)],
    ["Ce tournoi (années passées)", wl(a.tourn_w, a.tourn_n - a.tourn_w), wl(b.tourn_w, b.tourn_n - b.tourn_w), rate(a.tourn_w, a.tourn_n), rate(b.tourn_w, b.tourn_n)],
    ["Tie-breaks (2 ans)", wl(a.tb_w, a.tb_l), wl(b.tb_w, b.tb_l), rate(a.tb_w, a.tb_w + a.tb_l), rate(b.tb_w, b.tb_w + b.tb_l)],
    ["Sets décisifs (2 ans)", wl(a.dec_w, a.dec_l), wl(b.dec_w, b.dec_l), rate(a.dec_w, a.dec_w + a.dec_l), rate(b.dec_w, b.dec_w + b.dec_l)],
    ["Matchs remontés après le 1er set perdu", `${a.comebacks}/${a.lost_first}`, `${b.comebacks}/${b.lost_first}`, (a.comebacks + 0.5) / (a.lost_first + 2), (b.comebacks + 0.5) / (b.lost_first + 2)],
    ["Jeux joués sur 7 jours", num(a.games_7d, 0), num(b.games_7d, 0), null, null],
    ["Dernier match", last(a), last(b), null, null],
    ["Reprise après une pause", pause(a), pause(b), a.since_break, b.since_break],
    ["Abandon ou forfait récent", inj(a), inj(b), a.inj_days <= 90 ? -1 : 0, b.inj_days <= 90 ? -1 : 0],
    ["Face-à-face", num(d.h2h ? d.h2h[0] : 0, 0), num(d.h2h ? d.h2h[1] : 0, 0), d.h2h && d.h2h[0], d.h2h && d.h2h[1]],
  ];
  const rows = R.map(([lbl, x, y, sx, sy]) => {
    const better = sx == null || sy == null || Math.abs(sx - sy) < 1e-9 ? 0 : sx > sy ? 1 : 2;
    return `<tr><td class="${better === 1 ? "better" : ""}">${x}</td><th>${lbl}</th><td class="r ${better === 2 ? "better" : ""}">${y}</td></tr>`;
  }).join("");
  return `<div class="card wide"><h3>📋 Points forts, points faibles</h3><div class="table-wrap"><table class="cmp">
    <tr><td><b>${esc(short(d.player_1))}</b></td><th></th><td class="r"><b>${esc(short(d.player_2))}</b></td></tr>${rows}</table></div>
    <p class="faint" style="font-size:11.5px">En vert : l'avantage. Source : résultats tennis-data (pas de statistiques de service ni de durée des matchs).</p></div>`;
}

async function loadNews(ev) {
  const box = () => $(`#news-${CSS.escape(ev)}`);
  let r;
  try { r = S.news[ev] || (S.news[ev] = await api(`/api/matches/${encodeURIComponent(ev)}/news`)); } catch (e) { if (box()) box().innerHTML = `<h3>📰 Actualités de la semaine</h3><p class="muted">${esc(e.message)}</p>`; return; }
  const d = S.details[ev], el = box();
  if (!el || !d) return;
  const col = (s) => {
    const n = r.players[s], name = d[`player_${s}`];
    if (n.error && !n.items.length) return `<div><h4>${esc(name)}</h4><p class="muted">Actualités injoignables (connexion internet ?).</p></div>`;
    if (!n.items.length) return `<div><h4>${esc(name)}</h4><p class="muted">Aucun article cette semaine.</p></div>`;
    const sum = n.summary || {};
    const chips = Object.keys(r.labels).filter((k) => sum[k]).map((k) => `<span class="ntag ${k}">${esc(r.labels[k])} × ${sum[k]}</span>`).join(" ");
    return `<div><h4>${esc(name)} ${chips}</h4>${n.items.map((it) => `<div class="news-item"><a href="${esc(it.link)}" target="_blank" rel="noopener noreferrer">${esc(it.title)}</a>
      <div class="faint">${esc(it.source)}${it.published ? ` · ${esc(ago(it.published))}` : ""} ${it.tags.map((k) => `<span class="ntag ${k}">${esc(r.labels[k])}</span>`).join("")}</div></div>`).join("")}</div>`;
  };
  el.innerHTML = `<h3>📰 Actualités de la semaine</h3><div class="news-grid">${col(1)}${col(2)}</div>
    <p class="faint" style="font-size:11.5px">Titres de la presse française et anglophone (Google Actualités), repérés par mots-clés : blessure, fatigue, moral, confiance. Lis-les : un titre peut parler de l'adversaire. Ils n'entrent pas dans mes cotes : aucun historique ne permet de mesurer leur effet.</p>`;
  if (r.players[1].summary || r.players[2].summary) {           // alerte dans la liste, sans attendre le prochain chargement
    const m = S.matches.find((x) => x.event_id === ev);
    if (m) m.news = { 1: r.players[1].summary, 2: r.players[2].summary };
  }
}

/* ------------------------------------------------------------------ coupon */
function slipMatch() { return S.slip && S.matches.find((m) => m.event_id === S.slip.ev); }

function suggestedStake(p, odds) {
  const s = S.status.settings;
  if (p == null || !(odds > 1) || p * odds - 1 < s.min_ev) return 0;
  let f = Math.max((p * odds - 1) / (odds - 1), 0) * s.kelly_multiplier;
  f = Math.min(f, s.max_stake_pct);
  let stake = Math.round(s.bankroll * f * 100) / 100;
  if (stake < S.status.min_stake) return 0;
  stake = Math.min(stake, Math.max(s.bankroll * s.max_exposure_pct - S.status.open_total, 0));
  return Math.round(stake * 2) / 2;
}

function renderSlip() {
  const el = $("#slip"), m = slipMatch();
  $("#slip-count").textContent = m ? "(1)" : "";
  if (!m) {
    el.innerHTML = `<h2>📝 Mon pari</h2><p class="hint">Tennis Value ne place pas de pari : place-le chez ton bookmaker, puis enregistre-le ici pour suivre ta bankroll et ta CLV.</p>
      <div class="slip-empty">Clique sur une cote<br>pour préparer un pari.</div>`;
    return;
  }
  const side = S.slip.side, player = m[`player_${side}`];
  const books = m.books.filter((b) => b.french).sort((a, b) => b[`odds_${side}`] - a[`odds_${side}`]);
  const book = S.slip.book || m[`book_${side}`] || (books[0] && books[0].key);
  const odds = S.slip.odds ?? (books.find((b) => b.key === book) || {})[`odds_${side}`];
  const p = pSide(m, side), stake = S.slip.stake ?? suggestedStake(p, odds);
  el.innerHTML = `<h2>📝 Mon pari</h2><p class="hint">Place le pari chez le bookmaker, puis enregistre-le ici.</p>
    <div class="sel">
      <div class="top"><div><div class="pick">${esc(player)}</div><div class="muted" style="font-size:12.5px;margin-top:2px">${esc(m.player_1)} – ${esc(m.player_2)}<br>${esc(m.tournament)} · ${esc(when(m.start))}</div></div>
        <button class="x" data-act="slip-clear" title="Retirer">✕</button></div>
      <div class="field"><label>Bookmaker</label><select id="slip-book">${books.map((b) => `<option value="${esc(b.key)}"${b.key === book ? " selected" : ""}>${esc(b.name)} — ${num(b[`odds_${side}`])}</option>`).join("")}<option value="autre"${book === "autre" ? " selected" : ""}>Autre bookmaker</option></select></div>
      <div class="two">
        <div class="field"><label>Cote obtenue</label><input id="slip-odds" type="number" step="0.01" min="1.01" value="${odds ?? ""}"></div>
        <div class="field"><label>Mise (€)</label><input id="slip-stake" type="number" step="0.5" min="0.5" value="${stake || ""}" placeholder="0"></div>
      </div>
      <div id="slip-calc"></div>
    </div>
    <button class="btn primary" data-act="slip-save">✔ J'ai placé ce pari</button>`;
  slipCalc();
}

function slipCalc() {
  const m = slipMatch();
  if (!m) return;
  const side = S.slip.side, p = pSide(m, side), s = S.status.settings;
  const odds = parseFloat($("#slip-odds").value), stake = parseFloat($("#slip-stake").value) || 0;
  const sug = suggestedStake(p, odds);
  let advice;
  if (p == null) advice = `<div class="advice bad">Pas d'estimation de chance (cote Pinnacle / Betfair absente ou modèle non calculé) : pas de mise conseillée.</div>`;
  else {
    const ev = p * odds - 1;
    if (odds < s.min_odds || odds > s.max_odds) advice = `<div class="advice bad">Cote hors de tes limites (${num(s.min_odds)} – ${num(s.max_odds)}).</div>`;
    else if (ev >= s.min_ev) advice = `<div class="advice good">✅ Value ${pct(ev, 1, true)} (chance estimée ${pct(p)}). Mise conseillée : <b>${euros(sug)}</b>${sug === 0 ? " (plafond d'exposition atteint)" : ""}.</div>`;
    else advice = `<div class="advice bad">Value ${pct(ev, 1, true)} à cette cote (seuil ${pct(s.min_ev)}) : pari déconseillé.</div>`;
  }
  $("#slip-calc").innerHTML = `
    <div class="summary-line"><span>Gain potentiel</span><b class="num">${odds > 1 && stake ? euros(stake * odds) : "–"}</b></div>
    <div class="summary-line"><span>Bénéfice si gagné</span><b class="num">${odds > 1 && stake ? signed(euros(stake * (odds - 1)), 1) : "–"}</b></div>${advice}`;
}

async function saveSlip() {
  const m = slipMatch(), side = S.slip.side;
  const odds = parseFloat($("#slip-odds").value), stake = parseFloat($("#slip-stake").value);
  if (!(odds > 1) || !(stake > 0)) return toast("Indique la cote obtenue et la mise.", true);
  try {
    await api("/api/bets", { method: "POST", body: JSON.stringify({
      match: `${m.player_1} - ${m.player_2}`, selection: m[`player_${side}`], book: $("#slip-book").value, odds, stake,
      p: pSide(m, side), event_id: m.event_id, tour: m.tour }) });
  } catch (e) { return toast(e.message, true); }
  toast(`Pari enregistré : ${euros(stake)} sur ${m[`player_${side}`]} à ${num(odds)}.`);
  S.slip = null;
  $("#slip").classList.remove("show");
  await Promise.all([loadStatus(), loadMatches()]);
  render();
}

/* ------------------------------------------------------------------ page Bons coups */
async function renderBonsCoups() {
  const st = S.status.settings;
  $("#main").innerHTML = `<h1>Bons coups</h1>
    <p class="subtitle">Les cotes des bookmakers français comparées à la cote juste (Pinnacle sans sa marge) et à mes cotes.</p>
    ${banners()}
    <div class="explain">
      <div class="card"><b style="color:var(--green)">✅ Cote trop haute</b>Le bookmaker paie plus que la vraie chance du joueur : value d'au moins ${pct(st.min_ev)}. C'est là qu'on parie.</div>
      <div class="card"><b style="color:var(--red)">⛔ Cote trop basse</b>Les bookmakers français surestiment le joueur d'au moins ${num(st.trap_gap_pp, 0)} points : piège typique des joueurs populaires, sur qui le public mise quand même.</div>
      <div class="card"><b style="color:var(--purple)">🔍 À creuser</b>Mon modèle et le marché diffèrent d'au moins ${num(st.disagree_pp, 0)} points. Le marché sait souvent quelque chose (blessure, forfait) : lire l'actualité.</div>
    </div>
    <div id="picks"><p class="muted">Chargement…</p></div>
    <p class="footer-note">Cote juste = Pinnacle / Betfair Exchange sans marge : sur 2019-2026, elle prédit mieux que mon modèle (onglet Analyse), c'est donc elle qui décide. Mes cotes, calculées d'après les performances des joueurs, servent de second avis. Une value est une espérance sur beaucoup de paris, jamais une garantie.</p>`;
  let p;
  try { p = await api("/api/picks"); } catch (e) { return toast(e.message, true); }
  if (S.page !== "bons-coups") return;
  const where = (x) => `${esc(x.player_1)} – ${esc(x.player_2)} · ${esc(x.tournament)} · ${esc(when(x.start))}`;
  const alert = (x) => { const a = newsAlert(x); return a ? `<div class="newsflag">${esc(a)}</div>` : ""; };
  const actions = (x, bet) => `<div class="actions"><button class="btn small" data-act="goto" data-ev="${esc(x.event_id)}">Voir le match</button>${bet ? `<button class="btn small primary" data-act="pick" data-ev="${esc(x.event_id)}" data-side="${x.side}">Parier</button>` : ""}</div>`;
  const good = p.hautes.map((x) => `<div class="pick good">
      <div class="top"><div><div class="name">${esc(x.player)}</div><div class="sub">${where(x)}</div></div><div class="big" style="color:var(--green)">${pct(x.value, 0, true)}</div></div>
      <div class="row"><span>${esc(x.book_name || "")} <b class="num">${num(x.cote)}</b></span><span>cote juste <b class="num">${num(x.cote_juste)}</b></span><span>ma cote <b class="num">${num(x.ma_cote)}</b></span></div>
      <div class="row">${x.modele_ok == null ? `<span class="faint">Mon modèle : pas d'avis</span>` : x.modele_ok ? `<span class="good-text">✔ Mon modèle est d'accord</span>` : `<span class="trap-text">✖ Mon modèle n'est pas d'accord</span>`}</div>
      ${alert(x)}${actions(x, true)}</div>`).join("");
  const traps = p.basses.map((x) => `<div class="pick trap">
      <div class="top"><div><div class="name">${esc(x.player)}</div><div class="sub">${where(x)}</div></div><div class="big" style="color:var(--red)">${pts(x.ecart_fr)}</div></div>
      <div class="row"><span>Bookmakers FR <b>${pct(side_(x.p_fr, x.side))}</b></span><span>Pinnacle <b>${pct(side_(x.p_decision, x.side))}</b></span><span>ma chance <b>${pct(side_(x.p_model, x.side))}</b></span></div>
      <div class="row"><span>${esc(x.book_name || "")} <b class="num">${num(x.cote)}</b> au lieu de <b class="num">${num(x.cote_juste)}</b></span></div>
      ${alert(x)}${actions(x, false)}</div>`).join("");
  const dig = p.desaccords.map((x) => `<div class="pick dig">
      <div class="top"><div><div class="name">${esc(x.player)}</div><div class="sub">${where(x)}</div></div><div class="big" style="color:var(--purple)">${pts(x.ecart)}</div></div>
      <div class="row"><span>ma chance <b>${pct(side_(x.p_model, x.side))}</b></span><span>marché <b>${pct(side_(x.p_decision, x.side))}</b></span></div>
      <div class="row"><span>ma cote <b class="num">${num(x.ma_cote)}</b></span><span>cote juste <b class="num">${num(x.cote_juste)}</b></span><span>${esc(x.book_name || "FR")} <b class="num">${num(x.cote)}</b></span></div>
      ${alert(x)}${actions(x, false)}</div>`).join("");
  const none = (txt) => `<p class="muted" style="margin:0 0 18px">${txt}</p>`;
  $("#picks").innerHTML = `
    <h2 class="section-title">✅ Cotes trop hautes : à jouer (${p.hautes.length})</h2>${good ? `<div class="picks">${good}</div>` : none("Aucune pour l'instant : les cotes françaises sont alignées sur la cote juste. Ça change souvent juste après un relevé.")}
    <h2 class="section-title">⛔ Cotes trop basses : pièges du grand public (${p.basses.length})</h2>${traps ? `<div class="picks">${traps}</div>` : none("Aucune pour l'instant.")}
    <h2 class="section-title">🔍 Mon modèle n'est pas d'accord avec le marché (${p.desaccords.length})</h2>${dig ? `<div class="picks">${dig}</div>` : none("Aucun gros désaccord.")}`;
}

/* ------------------------------------------------------------------ page Tendances */
async function renderTendances() {
  const h = S.moversHours;
  $("#main").innerHTML = `<h1>Tendances : les cotes qui bougent</h1>
    <p class="subtitle">Joueurs dont la chance estimée par les bookmakers a le plus monté.</p>
    <div class="explain">
      <div class="card"><b class="o-informe">Mouvement sharp</b>Pinnacle / Betfair ont bougé : parieurs avisés ou information nouvelle (blessure, forfait). Le signal le plus fiable ; une cote française restée en arrière devient intéressante.</div>
      <div class="card"><b class="o-public">Mouvement FR</b>Seuls les bookmakers français ont bougé : mises du public ou prudence du bookmaker, souvent sur les favoris connus.</div>
      <div class="card"><b class="o-contra">Contradictoire</b>Français et sharp bougent en sens opposés : une cote française peut être en retard sur le vrai marché.</div>
    </div>
    <div class="toolbar"><span class="seg">
      <button class="${h === 0 ? "active" : ""}" data-act="movers" data-v="0">Depuis l'ouverture</button>
      <button class="${h === 24 ? "active" : ""}" data-act="movers" data-v="24">24 h</button>
      <button class="${h === 6 ? "active" : ""}" data-act="movers" data-v="6">6 h</button></span></div>
    <div class="movers" id="movers"><p class="muted">Chargement…</p></div>
    <p class="footer-note">Plus les relevés sont fréquents, plus la mesure est fine.</p>`;
  let list;
  try { list = await api(`/api/movers?hours=${h}`); } catch (e) { return toast(e.message, true); }
  if (S.page !== "tendances") return;
  $("#movers").innerHTML = list.length ? list.map((m, i) => {
    const o = ORIGIN[m.origine] || ORIGIN.stable, moved = m.n_releves >= 2;
    const arrow = m.cote != null && m.cote_ouv != null ? (m.cote < m.cote_ouv ? "▼" : m.cote > m.cote_ouv ? "▲" : "=") : "";
    return `<div class="mover" data-act="goto" data-ev="${esc(m.event_id)}">
      <div class="rank">${i + 1}</div>
      <div><div class="name">${esc(m.player)}</div><div class="sub">${esc(m.player_1)} – ${esc(m.player_2)} · ${esc(m.tournament)} · ${esc(when(m.start))}</div></div>
      <div><div class="big-delta o-${o.cls}">${moved ? pts(m.flux) : "–"}</div><div class="origin o-${o.cls}" style="font-size:12px">${moved ? o.label : "1 relevé"}</div></div>
      <div class="odds-move num">${m.cote_ouv != null ? `${num(m.cote_ouv)} → <b>${num(m.cote)}</b> <span class="${arrow === "▼" ? "arrow-down" : "arrow-up"}">${arrow}</span>` : "–"}<div class="faint" style="font-size:11.5px">meilleure cote FR</div></div>
      <div class="spark">${sparkline(m.spark, o.cls === "stable" ? "#6c6f7d" : getComputedStyle(document.documentElement).getPropertyValue(`--${{ informe: "gold", public: "blue", contra: "purple" }[o.cls]}`).trim())}</div>
    </div>`;
  }).join("") : `<div class="empty"><div class="big">📈</div>Aucun match à venir dans les relevés.</div>`;
}

/* ------------------------------------------------------------------ page Mes paris */
const RESULT = { gagne: "✔ Gagné", perdu: "✖ Perdu", annule: "↩ Annulé", ouvert: "⏳ En attente" };
async function renderParis() {
  $("#main").innerHTML = `<h1>Mes paris</h1><p class="subtitle">Bilan, paris en attente et historique.</p><div id="paris"><p class="muted">Chargement…</p></div>`;
  let r;
  try { r = S.bets = await api("/api/bets"); } catch (e) { return toast(e.message, true); }
  if (S.page !== "paris") return;
  const open = r.bets.filter((b) => b.status === "ouvert"), settled = r.bets.filter((b) => b.status !== "ouvert");
  $("#count-paris").textContent = open.length || "";
  const cls = (x) => (x > 0 ? "pos" : x < 0 ? "neg" : "");
  $("#paris").innerHTML = `
    <div class="kpis">
      <div class="kpi"><div class="k">Bankroll</div><div class="v num">${euros(r.bankroll)}</div></div>
      <div class="kpi"><div class="k">Bénéfice</div><div class="v num ${cls(r.benefice)}">${signed(euros(r.benefice), r.benefice)}</div></div>
      <div class="kpi"><div class="k">Rendement (yield)</div><div class="v num ${cls(r.yield)}">${pct(r.yield, 1, true)}</div></div>
      <div class="kpi" title="Cote prise comparée à la cote juste de clôture : le meilleur indicateur d'un vrai avantage."><div class="k">CLV moyenne (${r.n_clv})</div><div class="v num ${cls(r.clv_moyenne)}">${pct(r.clv_moyenne, 1, true)}</div></div>
      <div class="kpi"><div class="k">En jeu</div><div class="v num">${euros(r.en_cours)}</div></div>
    </div>
    ${r.n_regles > 0 && r.n_regles < 300 ? `<p class="muted">${r.n_regles} pari(s) réglé(s) : trop peu pour conclure. Il en faut plusieurs centaines ; en attendant, suivre surtout la CLV.</p>` : ""}
    <h2 class="section-title">En attente de résultat</h2>
    ${open.length ? open.map((b) => `<div class="bet-open"><div class="grow"><div>${esc(b.selection)} à ${num(b.odds)} · ${euros(b.stake)}</div>
        <div class="muted" style="font-size:12.5px">${esc(b.match)} · ${esc(b.book_name)} · placé le ${esc(full(b.placed_at))}</div></div>
        <button class="btn small win" data-act="settle" data-id="${b.bet_id}" data-r="gagne">✔ Gagné</button>
        <button class="btn small lose" data-act="settle" data-id="${b.bet_id}" data-r="perdu">✖ Perdu</button>
        <button class="btn small" data-act="settle" data-id="${b.bet_id}" data-r="annule" title="Match non joué ou abandon remboursé">↩ Annulé</button>
        <button class="btn small ghost" data-act="del" data-id="${b.bet_id}" title="Supprimer (saisi par erreur)">🗑</button></div>`).join("")
      : `<p class="muted">Aucun pari en attente. Clique sur une cote dans « Matchs » pour en enregistrer un.</p>`}
    ${settled.length ? `<h2 class="section-title">Évolution de la bankroll</h2><div class="card"><div id="curve"></div></div>` : ""}
    ${r.bets.length ? `<h2 class="section-title">Historique</h2><div class="card table-wrap"><table>
      <tr><th>Date</th><th>Match</th><th>Pari</th><th>Bookmaker</th><th class="r">Cote</th><th class="r">Mise</th><th>Résultat</th><th class="r">Gain</th><th class="r">CLV</th></tr>
      ${r.bets.map((b) => `<tr><td>${esc(full(b.placed_at))}</td><td>${esc(b.match)}</td><td>${esc(b.selection)}</td><td>${esc(b.book_name)}</td>
        <td class="r num">${num(b.odds)}</td><td class="r num">${euros(b.stake)}</td><td class="status-badge status-${b.status}">${RESULT[b.status] || esc(b.status)}</td>
        <td class="r num">${b.pnl == null ? "" : signed(euros(b.pnl), b.pnl)}</td><td class="r num">${b.clv == null ? "" : pct(b.clv, 1, true)}</td></tr>`).join("")}</table></div>` : ""}
    <details class="card" style="margin-top:16px"><summary style="cursor:pointer;font-weight:700">➕ Ajouter un pari à la main</summary>
      <div class="form-grid">
        <div class="field"><label>Match</label><input id="man-match" placeholder="Alcaraz - Sinner"></div>
        <div class="field"><label>Joueur parié</label><input id="man-sel" placeholder="Carlos Alcaraz"></div>
        <div class="field"><label>Bookmaker</label><input id="man-book" placeholder="Winamax"></div>
        <div class="two"><div class="field"><label>Cote</label><input id="man-odds" type="number" step="0.01" min="1.01"></div>
          <div class="field"><label>Mise (€)</label><input id="man-stake" type="number" step="0.5" min="0.5"></div></div>
      </div><button class="btn primary" style="margin-top:12px" data-act="manual">Enregistrer</button>
      <p class="faint">Pour la CLV automatique, enregistre plutôt le pari depuis la cote du match.</p></details>`;
  if (settled.length) lineChart($("#curve"), {
    series: [{ name: "Bankroll", color: "#22c55e", width: 2.5, points: r.curve.map((v, i) => [i, v]) }],
    yFmt: (v) => `${num(v, 0)} €`, xFmt: (i) => `${Math.round(i)}`, tipX: (i) => `Après ${i} pari${i > 1 ? "s" : ""} réglé${i > 1 ? "s" : ""}`, height: 200,
  });
}

/* ------------------------------------------------------------------ page Analyse */
const STRAT = { modele: "Mon modèle seul", modele_confirme: "Mon modèle confirmé par le sharp", sharp: "Cote juste Pinnacle (cote en retard)" };
const BOOKL = { b365: "Bet365", avg: "Cote moyenne", anj_proxy: "Proxy ANJ", max: "Meilleure cote", ps: "Pinnacle" };
async function renderAnalyse() {
  $("#main").innerHTML = `<h1>Analyse</h1><p class="subtitle">Ce que disent les saisons passées.</p><div id="an"><p class="muted">Chargement…</p></div>`;
  const a = await api("/api/analysis").catch((e) => toast(e.message, true));
  if (S.page !== "analyse") return;
  if (!a) { $("#an").innerHTML = `<div class="empty"><div class="big">📊</div>Pas encore d'analyse.<br><br><button class="btn primary" data-act="rebuild">Calculer le modèle</button></div>`; return; }
  const strat = a.backtest.filter((r) => [0.02, 0.05].includes(r.seuil) && ["b365", "anj_proxy"].includes(r.book));
  const evalRows = a.evaluation.filter((r) => /régression|sharp|Bet365/.test(r.source));
  const w = a.poids_facteurs || [];
  const tours = [...new Set(w.map((r) => r.tour))];
  const maxW = Math.max(1, ...w.map((r) => r.effet_moyen_pts));
  const weights = tours.map((t) => `<div><h4>${esc(t)}</h4>${w.filter((r) => r.tour === t).sort((x, y) => y.effet_moyen_pts - x.effet_moyen_pts).map((r) => `
      <div class="fx"><span class="fx-name">${esc(FACT[r.famille] || r.nom)}</span><div class="fx-bar"><span class="fill p1" style="left:0;width:${(100 * r.effet_moyen_pts / maxW).toFixed(1)}%"></span></div>
      <span class="fx-val">${num(r.effet_moyen_pts, 1)} pts <span class="faint">· ≥ 3 pts : ${pct(r.part_3pts)}</span></span></div>`).join("")}</div>`).join("");
  const bias = a.biais_public || [];
  $("#an").innerHTML = `
    <div class="card" style="margin-bottom:14px"><p style="color:var(--text)">Le <b>marché sharp (Pinnacle / Betfair)</b> prédit mieux que mon modèle, même avec la forme, la surface, le tournoi, la fatigue, les blessures et le mental : ces informations sont déjà dans ses cotes. Les verdicts reposent donc sur sa cote juste ; mes cotes servent de second avis et expliquent le match.
      Calculée le ${esc(new Date(a.genere_le).toLocaleString("fr-FR"))} sur ${num(a.n_matchs, 0)} matchs (${esc(a.periode[0])} → ${esc(a.periode[1])}).</p></div>
    <div class="stack">
      ${w.length ? `<div class="card"><h3>🧩 Ce qui pèse dans mes cotes</h3><p>Effet moyen de chaque famille de facteurs sur la chance d'un joueur (saison en cours), et part des matchs où elle pèse au moins 3 points.</p><div class="news-grid">${weights}</div></div>` : ""}
      ${bias.length ? `<div class="card"><h3>🎭 Les bookmakers grand public face à Pinnacle</h3>
        <p>Écart : chance du mieux classé selon Bet365 moins selon Pinnacle, marges retirées. Rendement : ce que rapporte 1 € misé à la cote Bet365, sur le mieux classé ou sur son adversaire.</p>
        <div class="table-wrap"><table><tr><th>Circuit</th><th>Mieux classé</th><th class="r">Matchs</th><th class="r">Écart</th><th class="r">Gagne vraiment</th><th class="r">Rendement favori</th><th class="r">Rendement adversaire</th></tr>
        ${bias.map((r) => `<tr><td>${esc(r.tour)}</td><td>${esc(r.tranche)}</td><td class="r num">${num(r.n, 0)}</td><td class="r num">${pts(r.ecart_pts)}</td><td class="r num">${pct(r.chance_reelle)}</td>
          <td class="r num" style="color:${r.yield_mieux_classe > 0 ? "var(--green)" : "var(--red)"}">${pct(r.yield_mieux_classe, 1, true)}</td>
          <td class="r num" style="color:${r.yield_adversaire > 0 ? "var(--green)" : "var(--red)"}">${pct(r.yield_adversaire, 1, true)}</td></tr>`).join("")}</table></div>
        <p>Les bookmakers grand public avantagent un peu les mieux classés (écart positif) ; mais ce sont surtout les <b>outsiders face aux tout meilleurs</b> qui sont mal payés. Parier « contre le favori » par principe perd beaucoup : seule la comparaison match par match avec la cote juste trouve les bonnes cotes.</p></div>` : ""}
      <div class="card"><h3>Qualité des probabilités</h3><p>Log loss : plus bas = meilleur.</p><div class="table-wrap"><table>
        <tr><th>Circuit</th><th>Source</th><th class="r">Log loss</th><th class="r">Précision</th></tr>
        ${evalRows.map((r) => `<tr><td>${esc(r.tour)}</td><td>${esc(r.source.replace("Ensemble (régression logistique)", "Mon modèle"))}</td><td class="r num">${num(r.log_loss, 3)}</td><td class="r num">${pct(r.precision, 1)}</td></tr>`).join("")}</table></div></div>
      <div class="card"><h3>Stratégies de paris (backtest)</h3><p>Yield : bénéfice par euro misé. Si la fourchette IC 95 % contient 0, la stratégie n'est pas prouvée.</p><div class="table-wrap"><table>
        <tr><th>Stratégie</th><th>Cotes</th><th class="r">Seuil</th><th class="r">Paris</th><th class="r">Yield</th><th class="r">IC 95 %</th></tr>
        ${strat.map((r) => `<tr><td>${esc(STRAT[r.strategie] || r.strategie)}</td><td>${esc(BOOKL[r.book] || r.book)}</td><td class="r">${pct(r.seuil)}</td><td class="r num">${num(r.n_paris, 0)}</td>
          <td class="r num" style="color:${r.yield > 0 ? "var(--green)" : "var(--red)"}">${pct(r.yield, 1, true)}</td><td class="r num">${pct(r.ic95_bas, 1)} ; ${pct(r.ic95_haut, 1)}</td></tr>`).join("")}</table></div></div>
    </div>`;
}

/* ------------------------------------------------------------------ page Réglages */
const PRUDENCE = { 0.125: "Très prudent (Kelly 1/8)", 0.25: "Prudent (Kelly 1/4, conseillé)", 0.5: "Audacieux (Kelly 1/2)" };
async function renderReglages() {
  const s = S.status.settings, st = S.status;
  const prud = { ...PRUDENCE };
  if (!(s.kelly_multiplier in prud)) prud[s.kelly_multiplier] = `Personnalisé (Kelly × ${num(s.kelly_multiplier)})`;
  const radio = (v, label) => `<label><input type="radio" name="src" value="${v}"${s.decision_source === v ? " checked" : ""}> ${label}</label>`;
  $("#main").innerHTML = `<h1>Réglages</h1><p class="subtitle">Bankroll, mises et sélection des paris.</p>
    <div class="detail" style="padding:0;border:0">
      <div class="card"><h3>💰 Mises</h3>
        <div class="field"><label>Bankroll (€) — somme réservée aux paris, que tu acceptes de perdre</label><input id="r-bankroll" type="number" step="50" min="10" value="${s.bankroll}"></div>
        <div class="field"><label>Prudence des mises</label><select id="r-kelly">${Object.entries(prud).map(([k, v]) => `<option value="${k}"${+k === s.kelly_multiplier ? " selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="two"><div class="field"><label>Mise max par pari (% bankroll)</label><input id="r-maxstake" type="number" step="0.5" min="0.5" max="10" value="${+(100 * s.max_stake_pct).toFixed(2)}"></div>
          <div class="field"><label>Total max en jeu (% bankroll)</label><input id="r-maxexpo" type="number" step="1" min="1" max="50" value="${+(100 * s.max_exposure_pct).toFixed(2)}"></div></div>
      </div>
      <div class="card"><h3>🎯 Sélection des paris</h3>
        <div class="field"><label>Value minimale (%) : en dessous, la cote n'est pas « trop haute »</label><input id="r-minev" type="number" step="0.5" min="0" max="20" value="${+(100 * s.min_ev).toFixed(2)}"></div>
        <div class="two"><div class="field"><label>Piège : surcote minimale (pts)</label><input id="r-trap" type="number" step="0.5" min="1" max="20" value="${s.trap_gap_pp}"></div>
          <div class="field"><label>À creuser : écart modèle / marché (pts)</label><input id="r-disagree" type="number" step="1" min="3" max="30" value="${s.disagree_pp}"></div></div>
        <div class="two"><div class="field"><label>Cote minimale</label><input id="r-minodds" type="number" step="0.05" min="1.01" value="${s.min_odds}"></div>
          <div class="field"><label>Cote maximale</label><input id="r-maxodds" type="number" step="0.5" min="1.1" value="${s.max_odds}"></div></div>
        <div class="field"><label>Probabilité utilisée pour décider</label><div class="radio">
          ${radio("sharp", "Marché sharp (conseillé) : cote juste de Pinnacle / Betfair")}
          ${radio("confirmed", "Confirmé : value exigée selon le sharp et selon le modèle")}
          ${radio("model", "Modèle seul (déconseillé : perdant sur toutes les saisons testées)")}</div></div>
      </div>
    </div>
    <button class="btn primary" style="margin-top:14px" data-act="save-settings">💾 Enregistrer les réglages</button>

    <h2 class="section-title">Données et modèle</h2>
    <div class="detail" style="padding:0;border:0">
      <div class="card"><h3>🧮 Modèle</h3><ul class="list-info">
        <li>Résultats de matchs jusqu'au <b>${st.data_until ? esc(new Date(st.data_until).toLocaleDateString("fr-FR")) : "–"}</b></li>
        <li>Modèle calculé le <b>${st.model_built ? esc(full(st.model_built)) : "jamais"}</b></li>
        <li>Dernier relevé de cotes : <b>${esc(ago(st.last_fetch))}</b></li></ul>
        <button class="btn" data-act="rebuild">Recalculer le modèle</button></div>
      <div class="card"><h3>📥 Nouveaux résultats de matchs</h3>
        <p>Une fois par semaine : sur <a href="http://www.tennis-data.co.uk/alldata.php" target="_blank" rel="noopener">tennis-data.co.uk</a>, télécharge les fichiers Excel de la saison (hommes et femmes), puis dépose-les ici. Le modèle est ensuite recalculé.</p>
        <label class="drop" id="drop">Glisse les fichiers .xlsx ici, ou clique pour les choisir<input id="files" type="file" accept=".xlsx" multiple class="hidden"></label></div>
      <div class="card wide"><h3>📡 Relevés de cotes</h3><p>GitHub relève les cotes vers <b>8 h 30, 11 h 30, 14 h 30, 17 h 30 et 20 h 30</b> (une heure plus tard en été), même ordinateur éteint. L'application les récupère toute seule à l'ouverture puis toutes les 30 minutes (bouton ⟳ pour forcer).
        Les actualités des joueurs sont relevées en même temps, depuis cet ordinateur.
        ${st.can_fetch === "local" ? "« Relever maintenant » utilise la clé The Odds API de cet ordinateur." : st.can_fetch === "github" ? "« Relever maintenant » lance un relevé sur GitHub (1 à 2 minutes)." : "« Relever maintenant » n'est pas disponible : lancer scripts/install_chromebook.sh."}</p></div>
    </div>`;
  const drop = $("#drop"), input = $("#files");
  input.addEventListener("change", () => uploadResults([...input.files]));
  drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("over"); });
  drop.addEventListener("dragleave", () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("over"); uploadResults([...e.dataTransfer.files]); });
}

async function saveSettings() {
  const val = (id) => parseFloat($(id).value);
  const body = {
    bankroll: val("#r-bankroll"), kelly_multiplier: val("#r-kelly"), max_stake_pct: val("#r-maxstake") / 100,
    max_exposure_pct: val("#r-maxexpo") / 100, min_ev: val("#r-minev") / 100, min_odds: val("#r-minodds"),
    max_odds: val("#r-maxodds"), decision_source: (document.querySelector('input[name="src"]:checked') || {}).value,
    trap_gap_pp: val("#r-trap"), disagree_pp: val("#r-disagree"),
  };
  if (Object.values(body).some((v) => v === undefined || Number.isNaN(v))) return toast("Un champ est vide ou invalide.", true);
  try { await api("/api/settings", { method: "PUT", body: JSON.stringify(body) }); } catch (e) { return toast(e.message, true); }
  await Promise.all([loadStatus(), loadMatches()]);
  toast("Réglages enregistrés.");
}

async function uploadResults(files) {
  if (!files.length) return;
  const done = [];
  await busy("Ajout des résultats…", async () => {
    for (const f of files) {
      try {
        const r = await fetch("/api/results", { method: "POST", body: f });
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail);
        done.push(`${d.tour} ${d.season} : ${num(d.n, 0)} matchs`);
      } catch (e) { toast(`${f.name} : ${e.message}`, true); }
    }
  });
  if (done.length) await rebuild(done.join(" ; ") + ". ");
}

/* ------------------------------------------------------------------ actions */
async function rebuild(prefix = "") {
  try {
    await busy("Calcul du modèle…<br><span class='muted'>1 à 3 minutes, ne ferme pas la fenêtre.</span>", () => api("/api/rebuild", { method: "POST" }));
  } catch (e) { return toast(e.message, true); }
  await Promise.all([loadStatus(), loadMatches()]);
  render();
  toast(prefix + "Modèle recalculé.");
}

async function update(silent = false) {
  let r;
  try {
    r = silent ? await api("/api/update", { method: "POST" })
      : await busy("Récupération des derniers relevés…", () => api("/api/update", { method: "POST" }));
  } catch (e) { if (!silent) toast(e.message, true); return; }
  if (r.new_version) newVersionBanner();
  if (silent && !r.imported) return;
  await Promise.all([loadStatus(), loadMatches()]);
  if (!S.slip || slipMatch()) render();
  if (r.imported || !silent) toast(r.imported ? `${r.imported} nouvelle(s) cote(s).` : "Déjà à jour.");
}

function newVersionBanner() {
  if ($("#new-version")) return;
  const b = document.createElement("div");
  b.id = "new-version";
  b.className = "toast";
  b.innerHTML = "🆕 Nouvelle version de Tennis Value téléchargée : ferme cet onglet puis rouvre l'application avec l'icône.";
  b.style.bottom = "80px";
  document.body.appendChild(b);
}

async function fetchNow() {
  let r;
  try {
    r = await busy("Relevé des cotes en cours…<br><span class='muted'>Jusqu'à 2 minutes.</span>", () => api("/api/fetch", { method: "POST" }));
  } catch (e) { return toast(e.message, true); }
  await Promise.all([loadStatus(), loadMatches()]);
  render();
  toast(r.message);
}

/* ------------------------------------------------------------------ navigation */
function render() {
  document.querySelectorAll("nav a.item[data-page]").forEach((a) => a.classList.toggle("active", a.dataset.page === S.page));
  renderTournaments();
  renderSlip();
  ({ "bons-coups": renderBonsCoups, matchs: renderMatchs, tendances: renderTendances, paris: renderParis, analyse: renderAnalyse,
    reglages: renderReglages }[S.page] || renderBonsCoups)();
}

function route() {
  const [, page = "bons-coups", arg] = location.hash.split("/");
  S.page = ["bons-coups", "matchs", "tendances", "paris", "analyse", "reglages"].includes(page) ? page : "bons-coups";
  if (S.page === "matchs" && arg) {
    const ev = decodeURIComponent(arg);
    S.filter = { tour: "all", tournament: null, value: false };
    render();
    if (!S.open.has(ev)) toggleMatch(ev);
    setTimeout(() => { const el = document.getElementById(`m-${ev}`); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }, 150);
    return;
  }
  render();
  window.scrollTo(0, 0);
}

document.addEventListener("click", async (e) => {
  const t = e.target.closest("[data-act]");
  if (!t) return;
  const act = t.dataset.act;
  if (act === "pick") {
    e.stopPropagation();
    const same = S.slip && S.slip.ev === t.dataset.ev && S.slip.side === +t.dataset.side;
    S.slip = same ? null : { ev: t.dataset.ev, side: +t.dataset.side };
    renderSlip();
    document.querySelectorAll(".odd.selected").forEach((b) => b.classList.remove("selected"));
    if (!same) { t.classList.add("selected"); $("#slip").classList.add("show"); }
  } else if (act === "toggle") toggleMatch(t.dataset.ev);
  else if (act === "side") { S.oddsSide[t.dataset.ev] = +t.dataset.side; renderDetail(t.dataset.ev); }
  else if (act === "tour") { S.filter.tour = t.dataset.v; renderMatchs(); }
  else if (act === "value") { S.filter.value = !S.filter.value; renderMatchs(); }
  else if (act === "tournament") { e.preventDefault(); S.filter.tournament = t.dataset.t || null; if (S.page !== "matchs") location.hash = "#/matchs"; else render(); }
  else if (act === "slip-clear") { S.slip = null; render(); }
  else if (act === "slip-save") saveSlip();
  else if (act === "movers") { S.moversHours = +t.dataset.v; renderTendances(); }
  else if (act === "goto") location.hash = `#/matchs/${encodeURIComponent(t.dataset.ev)}`;
  else if (act === "settle") {
    try { await api(`/api/bets/${t.dataset.id}/settle`, { method: "POST", body: JSON.stringify({ result: t.dataset.r }) }); } catch (err) { return toast(err.message, true); }
    toast(`Pari ${RESULT[t.dataset.r].toLowerCase()}.`);
    await loadStatus(); renderParis();
  } else if (act === "del") {
    if (!confirm("Supprimer ce pari ?")) return;
    await api(`/api/bets/${t.dataset.id}`, { method: "DELETE" });
    await loadStatus(); renderParis();
  } else if (act === "manual") {
    const body = { match: $("#man-match").value.trim(), selection: $("#man-sel").value.trim(), book: $("#man-book").value.trim() || "autre",
      odds: parseFloat($("#man-odds").value), stake: parseFloat($("#man-stake").value) };
    if (!body.match || !body.selection || !(body.odds > 1) || !(body.stake > 0)) return toast("Remplis le match, le joueur, la cote et la mise.", true);
    try { await api("/api/bets", { method: "POST", body: JSON.stringify(body) }); } catch (err) { return toast(err.message, true); }
    toast("Pari enregistré."); await loadStatus(); renderParis();
  } else if (act === "save-settings") saveSettings();
  else if (act === "rebuild") rebuild();
});

document.addEventListener("input", (e) => {
  if (e.target.id === "slip-odds") { S.slip.odds = parseFloat(e.target.value) || null; slipCalc(); }
  else if (e.target.id === "slip-stake") { S.slip.stake = parseFloat(e.target.value) || 0; slipCalc(); }
});
document.addEventListener("change", (e) => {
  if (e.target.id !== "slip-book") return;
  const m = slipMatch(), side = S.slip.side, b = m.books.find((x) => x.key === e.target.value);
  S.slip.book = e.target.value;
  S.slip.odds = b ? b[`odds_${side}`] : S.slip.odds;
  S.slip.stake = null;
  renderSlip();
});
$("#btn-update").addEventListener("click", () => update(false));
$("#btn-fetch").addEventListener("click", fetchNow);
$("#btn-slip").addEventListener("click", () => $("#slip").classList.toggle("show"));
window.addEventListener("hashchange", route);
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (S.page === "matchs") for (const ev of S.open) renderDetail(ev); }, 200); });

(async () => {
  try {
    await Promise.all([loadStatus(), loadMatches()]);
  } catch (e) { toast(`Application injoignable : ${e.message}`, true); return; }
  route();
  update(true);                                   // récupère les relevés de GitHub à l'ouverture…
  setInterval(() => update(true), 30 * 60e3);     // … puis toutes les 30 minutes
  setInterval(renderStatus, 60e3);                // « il y a N min » reste juste
})();
