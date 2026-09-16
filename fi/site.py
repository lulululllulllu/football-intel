"""Erzeugt die Website als eine einzige HTML-Datei (keine Server-Logik nötig).

Alle Daten stehen als JSON in der Seite. Der Rechner für eigene Zielquoten läuft im
Browser mit derselben Logik wie der Optimierer in Python.
"""
from __future__ import annotations

import html
import json

from fi import config
from fi.optimizer import Leg, Suggestion


def leg_json(leg: Leg) -> dict:
    names = {**config.LEAGUES, **config.CUPS}
    return {"match_id": leg.match_id, "competition": names[leg.competition]["name"],
            "kickoff_local": leg.kickoff_local, "home": leg.home, "away": leg.away, "label": leg.label,
            "p_market": round(leg.p_market, 4), "p_model": None if leg.p_model is None else round(leg.p_model, 4),
            "n_bookmakers": leg.n_bookmakers, "price": leg.price, "bookmaker": leg.bookmaker,
            "prices": leg.prices}


def suggestion_json(s: Suggestion) -> dict:
    return {"total_odds": s.total_odds, "win_probability": s.win_probability,
            "expected_return": s.expected_return, "legs": [leg_json(l) for l in s.legs]}


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload).replace("__STAND__", html.escape(data["generated_local"]))


TEMPLATE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Wettzettel des Tages</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #edf1ec; --surface: #f8faf7; --slip: #ffffff; --ink: #17231d; --muted: #58685f;
  --line: #c9d3cb; --pitch: #1f7a4a; --loss: #b23a2a; --hold: #8a6d1c;
  --num: "Barlow Condensed", "Arial Narrow", "Roboto Condensed", sans-serif;
  --text: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #121a16; --surface: #18221d; --slip: #1d2922; --ink: #e4ebe6; --muted: #98a89f;
          --line: #2f3d35; --pitch: #5dbb86; --loss: #e27d69; --hold: #d4b45a; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.5 var(--text); }
main { max-width: 760px; margin: 0 auto; padding: 28px 18px 64px; }
h1, h2 { font-family: var(--num); font-weight: 700; letter-spacing: .01em; line-height: 1.05; margin: 0; }
h1 { font-size: clamp(2.2rem, 7vw, 3.2rem); }
h2 { font-size: 1.7rem; margin-top: 48px; }
.stand { color: var(--muted); margin: 6px 0 0; }
.alert { border-left: 4px solid var(--loss); background: var(--surface); padding: 10px 14px; margin-top: 16px; }
.sub { color: var(--muted); margin: 6px 0 14px; max-width: 62ch; }
button, input, select { font: inherit; color: inherit; }
:focus-visible { outline: 3px solid var(--pitch); outline-offset: 2px; }

.targets { display: flex; gap: 8px; margin: 18px 0 14px; flex-wrap: wrap; }
.targets button { font-family: var(--num); font-size: 1.25rem; font-weight: 600; padding: 6px 16px;
  border: 2px solid var(--ink); background: transparent; border-radius: 999px; cursor: pointer; }
.targets button[aria-pressed="true"] { background: var(--ink); color: var(--bg); }

/* Der Wettzettel: das eine auffällige Element der Seite */
.slip { background: var(--slip); padding: 22px 22px 18px; }
.slip-head { position: relative; }
.slip-head::before, .slip-head::after { content: ""; position: absolute; bottom: -10px; width: 18px; height: 18px;
  border-radius: 50%; background: var(--bg); }
.slip-head::before { left: -31px; } .slip-head::after { right: -31px; }
.slip + .slip { margin-top: 14px; }
.slip-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px;
  border-bottom: 2px dashed var(--line); padding-bottom: 14px; }
.odds { font-family: var(--num); font-size: clamp(3.2rem, 14vw, 4.6rem); font-weight: 700; line-height: .9; }
.chance { text-align: right; color: var(--muted); }
.chance strong { display: block; font-family: var(--num); font-size: 2rem; color: var(--ink); line-height: 1; }
.leg { padding: 14px 0; border-bottom: 1px solid var(--line); }
.leg:last-of-type { border-bottom: 0; }
.leg-when { color: var(--muted); font-size: .9rem; }
.leg-pick { display: flex; justify-content: space-between; gap: 10px; font-weight: 600; margin-top: 2px; }
.leg-pick span:last-child { font-family: var(--num); font-size: 1.35rem; }
.leg-facts { color: var(--muted); font-size: .9rem; }
.slip-foot { border-top: 2px dashed var(--line); padding-top: 12px; color: var(--muted); font-size: .92rem; }
.none { background: var(--slip); padding: 22px; }
.none strong { font-family: var(--num); font-size: 2rem; display: block; line-height: 1.1; }

form { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; }
label { display: grid; gap: 4px; font-size: .92rem; color: var(--muted); }
input[type=number], select { background: var(--surface); border: 1px solid var(--line); padding: 8px 10px; }
fieldset { grid-column: 1 / -1; border: 1px solid var(--line); padding: 10px 12px; margin: 0; }
fieldset label { display: inline-flex; align-items: center; gap: 6px; margin: 4px 14px 4px 0; color: var(--ink); }
#builder-out { margin-top: 16px; }

.day { font-family: var(--num); font-size: 1.25rem; font-weight: 600; margin: 22px 0 4px; }
.match { display: grid; grid-template-columns: 4.2rem 1fr; gap: 2px 12px; padding: 10px 0;
  border-top: 1px solid var(--line); }
.match time { font-family: var(--num); font-size: 1.2rem; font-weight: 600; }
.teams { font-weight: 600; }
.comp, .model { color: var(--muted); font-size: .88rem; }
.probs { grid-column: 2; display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .95rem; }
.probs b { font-family: var(--num); font-size: 1.15rem; }
.flag { color: var(--hold); font-size: .88rem; }

.tally { font-size: 1.05rem; max-width: 62ch; }
table { width: 100%; border-collapse: collapse; font-size: .92rem; }
.table-wrap { overflow-x: auto; margin-top: 10px; }
th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
td.num { font-family: var(--num); font-size: 1.1rem; white-space: nowrap; }
.won { color: var(--pitch); font-weight: 700; } .lost { color: var(--loss); font-weight: 700; }
.open { color: var(--muted); }
footer { margin-top: 56px; color: var(--muted); font-size: .88rem; max-width: 62ch; }
@media (prefers-reduced-motion: no-preference) { .slip { animation: print .5s ease-out both; }
  @keyframes print { from { clip-path: inset(0 0 100% 0); } to { clip-path: inset(0 0 0 0); } } }
</style>
</head>
<body>
<main>
  <header>
    <h1>Wettzettel des Tages</h1>
    <p class="stand">Stand __STAND__ Uhr <span id="credits"></span></p>
    <div id="alerts"></div>
  </header>

  <section aria-labelledby="h-tipp">
    <h2 id="h-tipp">Beste Wette je Zielquote</h2>
    <p class="sub">Höchste echte Gewinnchance für die gewählte Quote, nur mit Quoten deiner Buchmacher.</p>
    <div class="targets" id="targets" role="group" aria-label="Zielquote wählen"></div>
    <div id="tipp"></div>
  </section>

  <section aria-labelledby="h-rechner">
    <h2 id="h-rechner">Eigene Zielquote</h2>
    <p class="sub">Rechnet mit den Quoten von heute, direkt hier im Browser.</p>
    <form id="builder">
      <label>Zielquote <input type="number" id="b-target" min="1.2" max="50" step="0.1" value="4"></label>
      <label>Höchstens Tipps <select id="b-legs"><option>1</option><option>2</option><option selected>3</option><option>4</option></select></label>
      <fieldset><legend>Buchmacher</legend><div id="b-books"></div></fieldset>
    </form>
    <div id="builder-out" aria-live="polite"></div>
  </section>

  <section aria-labelledby="h-spiele">
    <h2 id="h-spiele">Spiele der nächsten Tage</h2>
    <p class="sub">Markt = Mittel aller Buchmacher ohne Marge. Das Modell dient nur als Kontrolle.</p>
    <div id="matches"></div>
  </section>

  <section aria-labelledby="h-bilanz">
    <h2 id="h-bilanz">Bilanz aller Empfehlungen</h2>
    <p class="tally" id="tally"></p>
    <div class="table-wrap"><table id="track"></table></div>
  </section>

  <footer>
    Wahrscheinlichkeiten sind Schätzungen, keine Garantie. Eine erwartete Rückzahlung unter 100 % bedeutet
    auf lange Sicht Verlust. Aufstellungen und Verletzungen sind nicht geprüft: Quote und Nachrichten vor dem
    Wetten kontrollieren. Setze nur Geld, dessen Verlust dich nicht stört. Hilfe bei Spielproblemen:
    check-dein-spiel.de.
  </footer>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const pct = (p, d = 0) => (p * 100).toFixed(d).replace(".", ",") + "\u00a0%";
const odd = x => x.toFixed(2).replace(".", ",");
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

if (D.credits !== null) document.getElementById("credits").textContent = `, ${D.credits} Quoten-Credits übrig diesen Monat`;
const alerts = [];
if (D.odds_error) alerts.push(`Quoten konnten nicht geladen werden: ${esc(D.odds_error)}`);
D.notes.filter(n => n.startsWith("Team nicht")).forEach(n => alerts.push(esc(n)));
document.getElementById("alerts").innerHTML = alerts.map(a => `<p class="alert">${a}</p>`).join("");

function slipHTML(s, target) {
  const legs = s.legs.map(l => `
    <div class="leg">
      <div class="leg-when">${esc(l.kickoff_local)} Uhr, ${esc(l.competition)}</div>
      <div>${esc(l.home)} – ${esc(l.away)}</div>
      <div class="leg-pick"><span>${esc(l.label)}</span><span>${odd(l.price)}</span></div>
      <div class="leg-facts">bei ${esc(l.bookmaker)}, Markt ${pct(l.p_market)} aus ${l.n_bookmakers} Buchmachern${
        l.p_model === null ? ", kein Modell" : `, Modell ${pct(l.p_model)}`}</div>
    </div>`).join("");
  const warn = s.expected_return > 1 ? " Über 100 % deutet meist auf eine veraltete Quote hin: beim Buchmacher prüfen." : "";
  const multi = s.legs.length > 1 ? " Jeder Tipp muss aufgehen." : "";
  return `<article class="slip">
    <div class="slip-head"><div><div class="leg-when">Quote${target ? " für Ziel " + odd(target) : ""}</div>
      <div class="odds">${odd(s.total_odds)}</div></div>
      <div class="chance"><strong>${pct(s.win_probability, 1)}</strong>Gewinnchance</div></div>
    ${legs}
    <div class="slip-foot">Faire Quote ${odd(1 / s.win_probability)}. Im Schnitt kommen ${pct(s.expected_return)} des Einsatzes zurück.${multi}${warn}</div>
  </article>`;
}

const targetsEl = document.getElementById("targets");
function showTarget(i) {
  [...targetsEl.children].forEach((b, j) => b.setAttribute("aria-pressed", String(i === j)));
  const t = D.targets[i];
  document.getElementById("tipp").innerHTML = t.suggestion ? slipHTML(t.suggestion, t.target)
    : `<div class="none"><strong>Heute keine Wette</strong>${esc(t.reason)}</div>`;
}
D.targets.forEach((t, i) => {
  const b = document.createElement("button");
  b.type = "button"; b.textContent = odd(t.target); b.onclick = () => showTarget(i);
  targetsEl.appendChild(b);
});
if (D.targets.length) showTarget(Math.min(1, D.targets.length - 1));

// Rechner: gleiche Logik wie fi/optimizer.py
const booksEl = document.getElementById("b-books");
booksEl.innerHTML = D.all_bookmakers.map(b => `<label><input type="checkbox" value="${esc(b)}" ${
  D.my_bookmakers.includes(b) ? "checked" : ""}> ${esc(b)}</label>`).join("") || "Keine Quoten vorhanden.";
function combos(pool, size, start = 0, acc = [], out = []) {
  if (acc.length === size) { out.push(acc.slice()); return out; }
  for (let i = start; i < pool.length; i++) {
    if (acc.some(a => a.match_id === pool[i].match_id)) continue;
    acc.push(pool[i]); combos(pool, size, i + 1, acc, out); acc.pop();
  }
  return out;
}
function runBuilder() {
  const target = parseFloat(document.getElementById("b-target").value);
  const maxLegs = parseInt(document.getElementById("b-legs").value, 10);
  const books = [...booksEl.querySelectorAll("input:checked")].map(i => i.value);
  const out = document.getElementById("builder-out");
  if (!target || target < 1.2) { out.innerHTML = `<div class="none">Zielquote ab 1,20 eingeben.</div>`; return; }
  if (!books.length) { out.innerHTML = `<div class="none">Mindestens einen Buchmacher auswählen.</div>`; return; }
  const legs = D.legs.map(l => {
    let best = null;
    for (const b of books) if (l.prices[b] && (!best || l.prices[b] > best[1])) best = [b, l.prices[b]];
    return best ? {...l, bookmaker: best[0], price: best[1], er: l.p_market * best[1]} : null;
  }).filter(Boolean).sort((a, b) => b.er - a.er).slice(0, D.rules.pool);
  const low = target * (1 - D.rules.tolerance), high = target * (1 + D.rules.tolerance);
  let found = [];
  for (let n = 1; n <= maxLegs; n++) for (const c of combos(legs, n)) {
    const total = c.reduce((x, l) => x * l.price, 1);
    if (total < low || total > high) continue;
    const p = c.reduce((x, l) => x * l.p_market, 1);
    found.push({legs: c, total_odds: total, win_probability: p, expected_return: p * total});
    if (found.length > 20000) break;
  }
  found.sort((a, b) => b.expected_return - a.expected_return);
  const best = found.slice(0, 3);
  if (!best.length) { out.innerHTML = `<div class="none"><strong>Keine passende Wette</strong>Keine Kombination erreicht ${odd(target)}. Mehr Tipps oder andere Quote versuchen.</div>`; return; }
  if (best[0].expected_return < D.rules.min_return) {
    out.innerHTML = `<div class="none"><strong>Heute keine Wette</strong>Die beste Möglichkeit zahlt im Schnitt nur ${pct(best[0].expected_return)} zurück.</div>`; return; }
  out.innerHTML = best.filter(s => s.expected_return >= D.rules.min_return).map(s => slipHTML(s)).join("");
}
document.getElementById("builder").addEventListener("input", runBuilder);
document.getElementById("builder").addEventListener("submit", e => e.preventDefault());
runBuilder();

// Spiele
const mEl = document.getElementById("matches");
if (!D.matches.length) mEl.innerHTML = `<p class="sub">Keine Spiele in den nächsten Tagen gespeichert.</p>`;
let lastDay = "";
mEl.innerHTML += D.matches.map(m => {
  const day = m.kickoff_local.slice(0, 9);
  const head = day !== lastDay ? `<div class="day">${esc(day)}</div>` : ""; lastDay = day;
  const mk = m.market["1x2"], ou = m.market["ou25"], md = m.model["1x2"], mo = m.model["ou25"];
  const probs = mk ? `<span>1 <b>${pct(mk.H)}</b></span><span>X <b>${pct(mk.D)}</b></span><span>2 <b>${pct(mk.A)}</b></span>`
    + (ou ? `<span>Über 2,5 <b>${pct(ou.over)}</b></span>` : "") : `<span class="comp">Noch keine Quoten</span>`;
  const model = md ? `<div class="model">Modell ${pct(md.H)} / ${pct(md.D)} / ${pct(md.A)}${mo ? `, Über 2,5 ${pct(mo.over)}` : ""}</div>` : "";
  return `${head}<div class="match"><time>${esc(m.kickoff_local.slice(10))}</time>
    <div><div class="teams">${esc(m.home)} – ${esc(m.away)}</div><div class="comp">${esc(m.competition)}</div></div>
    <div class="probs">${probs}</div><div style="grid-column:2">${model}${m.low_data ? `<div class="flag">Wenig Spieldaten, Modell unsicher</div>` : ""}</div></div>`;
}).join("");

// Bilanz
const S = D.summary;
document.getElementById("tally").textContent = S.settled
  ? `${S.settled} Empfehlungen ausgewertet: ${S.won} gewonnen, laut Wahrscheinlichkeiten erwartet waren ${S.expected_wins.toString().replace(".", ",")}. `
    + `Bei 1 € je Wette ergibt das rechnerisch ${S.profit_per_unit >= 0 ? "+" : ""}${S.profit_per_unit.toFixed(2).replace(".", ",")} €.`
  : "Noch keine ausgewerteten Empfehlungen. Ergebnisse erscheinen einige Tage nach den Spielen.";
const cls = {"gewonnen": "won", "verloren": "lost"};
document.getElementById("track").innerHTML = D.track.length ? `<thead><tr><th>Datum</th><th>Tipps</th><th>Quote</th><th>Chance</th><th>Ergebnis</th></tr></thead><tbody>` +
  D.track.map(r => `<tr><td>${esc(r.legs[0].kickoff_local.slice(3, 9))}</td>
    <td>${r.legs.map(l => `${esc(l.home)} – ${esc(l.away)}: ${esc(l.label)}`).join("<br>")}</td>
    <td class="num">${odd(r.total_odds)}</td><td class="num">${pct(r.win_probability)}</td>
    <td class="${cls[r.result] || "open"}">${esc(r.result)}</td></tr>`).join("") + "</tbody>" : "";
</script>
</body>
</html>
"""
