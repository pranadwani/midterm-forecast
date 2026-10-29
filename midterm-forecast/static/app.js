// Front end: draws the map, the pollster checkboxes and the state detail panel.
// All numbers come from the Flask API; this file only displays them.

const state = {
  cycle: null,
  office: "senate",
  pollsters: [],          // [{id, name, grade, polls, states}]
  selected: new Set(),    // pollster ids that are checked
  includePartisan: false,
  data: null,             // last /api/forecast response
  selectedState: null,    // e.g. "PA"
};

const PARTY = {
  DEM: { label: "D", color: "var(--dem)", name: "Democrat" },
  REP: { label: "R", color: "var(--rep)", name: "Republican" },
};
const partyInfo = (p) => PARTY[p] || { label: p ? p[0] : "?", color: "var(--other)", name: p || "Other" };
const pct = (x) => (x > 0.99 ? ">99" : x < 0.01 ? "<1" : Math.round(x * 100)) + "%";
const fmtMargin = (m, a, b) => (Math.abs(m) < 0.05 ? "Tied" : `${m > 0 ? a : b} +${Math.abs(m).toFixed(1)}`);
const lastName = (n) => n.split(" ").slice(-1)[0];

// ------------------------------------------------------------------ API
async function api(path) {
  const res = await fetch(path);
  const body = await res.json();
  if (!res.ok) throw new Error(body.error || res.statusText);
  return body;
}

// ------------------------------------------------------------------ map
const svg = d3.select("#map");
const path = d3.geoPath();   // us-atlas states are already projected to 975 x 610
let statesFeatures = [];
const NAME_TO_ABBR = {};

async function drawMap() {
  const us = await (await fetch("/static/vendor/states-albers-10m.json")).json();
  statesFeatures = topojson.feature(us, us.objects.states).features;
  statesFeatures.forEach((f) => (f.abbr = NAME_TO_ABBR[f.properties.name]));

  svg.append("defs").append("pattern")
    .attr("id", "hatch").attr("width", 6).attr("height", 6)
    .attr("patternUnits", "userSpaceOnUse").attr("patternTransform", "rotate(45)")
    .call((p) => {
      p.append("rect").attr("width", 6).attr("height", 6).attr("fill", "var(--norace)");
      p.append("rect").attr("width", 2).attr("height", 6).attr("fill", "var(--nodata)");
    });

  svg.append("g").selectAll("path")
    .data(statesFeatures)
    .join("path")
    .attr("class", "state")
    .attr("d", path)
    .on("mousemove", showTooltip)
    .on("mouseleave", () => (document.getElementById("tooltip").hidden = true))
    .on("click", (_e, f) => selectState(f.abbr))
    .on("keydown", (e, f) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); selectState(f.abbr); } });

  svg.append("path")
    .datum(topojson.mesh(us, us.objects.states, (a, b) => a !== b))
    .attr("fill", "none").attr("stroke", "var(--line)").attr("stroke-width", 0.75).attr("d", path)
    .style("pointer-events", "none");
}

// Pick the race that colors a state (the regular election if a state has two).
function mainRace(abbr) {
  const races = (state.data?.races || []).filter((r) => r.state_abbrev === abbr);
  return races.find((r) => r.stage === "general") || races[0] || null;
}

function stateColor(race) {
  const fav = race.prob_a >= 0.5 ? race.cand_a.party : race.cand_b.party;
  const strength = (Math.max(race.prob_a, race.prob_b) - 0.5) * 2;   // 0 = toss-up, 1 = certain
  const css = getComputedStyle(document.documentElement);
  const end = css.getPropertyValue(fav === "DEM" ? "--dem" : fav === "REP" ? "--rep" : "--other").trim();
  return d3.interpolateRgb(css.getPropertyValue("--tossup").trim(), end)(0.1 + 0.9 * strength);
}

function paintMap() {
  const withRaces = new Set(state.data?.states_with_races || []);
  svg.selectAll(".state")
    .each(function (f) {
      f.abbr = f.abbr || NAME_TO_ABBR[f.properties.name];
      const race = mainRace(f.abbr);
      const hasRace = withRaces.has(f.abbr) || !!race;
      d3.select(this)
        .classed("norace", !hasRace)
        .classed("nodata", hasRace && !race)
        .classed("selected", f.abbr === state.selectedState)
        .attr("tabindex", hasRace ? 0 : null)
        .attr("aria-label", hasRace ? `${f.properties.name}${race ? "" : ", no polls selected"}` : null)
        .style("fill", race ? stateColor(race) : null);
    });
}

function showTooltip(event, f) {
  const tip = document.getElementById("tooltip");
  const race = mainRace(f.abbr);
  const hasRace = (state.data?.states_with_races || []).includes(f.abbr);
  if (!race && !hasRace) { tip.hidden = true; return; }
  tip.textContent = race
    ? `${f.properties.name}: ${lastName(race.cand_a.name)} ${pct(race.prob_a)} · ${lastName(race.cand_b.name)} ${pct(race.prob_b)}`
    : `${f.properties.name}: no polls with this selection`;
  const box = document.querySelector(".map-wrap").getBoundingClientRect();
  tip.style.left = `${event.clientX - box.left + 12}px`;
  tip.style.top = `${event.clientY - box.top + 12}px`;
  tip.hidden = false;
}

// ------------------------------------------------------------------ pollster checkboxes
function renderPollsters() {
  const list = document.getElementById("pollsters");
  if (!state.pollsters.length) {
    list.innerHTML = `<p class="explain">No polls loaded for this election yet.</p>`;
    return;
  }
  list.innerHTML = state.pollsters.map((p) => `
    <label class="check">
      <input type="checkbox" value="${p.id}" ${state.selected.has(p.id) ? "checked" : ""}>
      <span>${escapeHtml(p.name)}<br><small>${p.polls} poll${p.polls === 1 ? "" : "s"} in ${p.states} state${p.states === 1 ? "" : "s"}</small></span>
      <span class="grade" title="538 pollster rating, 0.5 to 3.0 stars">${gradeLetter(p.grade)}</span>
    </label>`).join("");
  list.querySelectorAll("input").forEach((box) =>
    box.addEventListener("change", () => {
      const id = Number(box.value);
      box.checked ? state.selected.add(id) : state.selected.delete(id);
      loadForecast();
    }));
}

function shortDate(iso) {
  return new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// 538 rates pollsters from 0.5 to 3.0 stars.
function gradeLetter(g) {
  return g == null ? "unrated" : `${g.toFixed(1)}★`;
}

function applyPreset(name) {
  if (name === "all") state.selected = new Set(state.pollsters.map((p) => p.id));
  if (name === "none") state.selected = new Set();
  if (name === "top5") state.selected = new Set(
    state.pollsters.filter((p) => p.grade != null).slice(0, 5).map((p) => p.id));
  renderPollsters();
  loadForecast();
}

// ------------------------------------------------------------------ data loading
async function loadPollsters() {
  state.pollsters = await api(`/api/pollsters?cycle=${state.cycle}&office=${state.office}`);
  state.selected = new Set(state.pollsters.map((p) => p.id));
  renderPollsters();
}

let requestId = 0;
async function loadForecast() {
  const myId = ++requestId;
  const all = state.selected.size === state.pollsters.length;
  const ids = all ? "" : [...state.selected].join(",") || "0";
  const data = await api(`/api/forecast?cycle=${state.cycle}&office=${state.office}` +
    `&pollsters=${ids}&include_partisan=${state.includePartisan ? 1 : 0}`);
  if (myId !== requestId) return;        // a newer request already finished
  state.data = data;
  if (state.selectedState && !data.states_with_races.includes(state.selectedState)
      && !data.races.some((r) => r.state_abbrev === state.selectedState)) {
    state.selectedState = null;          // e.g. switched to an office with no race there
    history.replaceState(null, "", location.pathname);
  }
  renderNotice();
  renderUpdated();
  renderChamber();
  paintMap();
  renderDetail();
}

function renderUpdated() {
  const el = document.getElementById("updated");
  if (!state.data.updated) { el.hidden = true; return; }
  const when = new Date(state.data.updated * 1000).toLocaleString("en-US",
    { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
  el.textContent = `Polls updated ${when}`;
  el.hidden = false;
}

function renderNotice() {
  const el = document.getElementById("notice");
  const { races } = state.data;
  if (!state.pollsters.length) {
    el.textContent = `No ${state.office} polls are loaded for ${state.cycle} yet. ` +
      "They download automatically when the app starts, so check back shortly.";
    el.hidden = false;
  } else if (!races.length) {
    el.textContent = "None of the selected pollsters surveyed these races. Select more pollsters to see a forecast.";
    el.hidden = false;
  } else {
    el.hidden = true;
  }
}

function renderChamber() {
  const el = document.getElementById("chamber");
  const c = state.data.chamber;
  if (!c || !state.data.races.length) { el.hidden = true; return; }
  const d = c.prob_dem_control, r = c.prob_rep_control;
  const unpolled = c.unpolled_seats_assumed.length;
  el.innerHTML = `
    <h2>Chance of controlling the Senate</h2>
    <div class="control-bar" role="img" aria-label="Democrats ${pct(d)}, Republicans ${pct(r)}">
      <div class="d" style="flex:${Math.max(d, 0.001)}">${d >= 0.12 ? "Democrats " + pct(d) : ""}</div>
      <div class="r" style="flex:${Math.max(r, 0.001)}">${r >= 0.12 ? "Republicans " + pct(r) : ""}</div>
    </div>
    <p>From ${c.n_sims.toLocaleString()} simulated elections. Democrats win a median of
      ${c.median_dem_seats} seats (80% range ${c.dem_seats_80pct_range[0]}–${c.dem_seats_80pct_range[1]}).
      A 50–50 split goes to ${c.tiebreak_party === "DEM" ? "Democrats" : "Republicans"} through the Vice President.
      ${unpolled ? `${unpolled} seat${unpolled === 1 ? "" : "s"} without polls ${unpolled === 1 ? "is" : "are"} assumed to stay with the current party.` : ""}</p>`;
  el.hidden = false;
}

// ------------------------------------------------------------------ detail panel
function selectState(abbr) {
  if (!abbr) return;
  const hasRace = (state.data?.states_with_races || []).includes(abbr) || mainRace(abbr);
  if (!hasRace) return;
  state.selectedState = abbr;
  history.replaceState(null, "", `#${abbr}`);     // shareable link to this state
  paintMap();
  renderDetail();
}

function clearSelection() {
  state.selectedState = null;
  history.replaceState(null, "", location.pathname);
  paintMap();
  renderDetail();
}

// Shown when no state is selected: the most competitive races, one click away.
function renderOverview(el) {
  const races = [...state.data.races]
    .sort((x, y) => Math.abs(x.prob_a - 0.5) - Math.abs(y.prob_a - 0.5))
    .slice(0, 6);
  if (!races.length) {
    el.innerHTML = `<p class="empty">Select a state to see its forecast and the polls behind it.</p>`;
    return;
  }
  const office = state.office === "senate" ? "Senate" : "governor";
  el.innerHTML = `
    <h2>Closest ${office} races</h2>
    <p class="meta">Click a race, or any colored state on the map, to see the polls behind it.</p>
    <ul class="close-list">${races.map((r) => {
      const a = partyInfo(r.cand_a.party), b = partyInfo(r.cand_b.party);
      const fav = r.prob_a >= 0.5 ? r.cand_a : r.cand_b;
      const favP = Math.max(r.prob_a, r.prob_b);
      const stage = r.stage === "special" ? " (special)" : r.stage === "runoff" ? " (runoff)" : "";
      return `<li><button type="button" data-state="${r.state_abbrev}">
        <span class="close-top"><strong>${escapeHtml(r.state)}${stage}</strong>
          <span>${escapeHtml(lastName(fav.name))} ${pct(favP)}</span></span>
        <span class="prob-bar"><span style="flex:${r.prob_a};background:${a.color}"></span><span style="flex:${r.prob_b};background:${b.color}"></span></span>
        <span class="close-names"><span>${escapeHtml(r.cand_a.name)} (${a.label})</span><span>${escapeHtml(r.cand_b.name)} (${b.label})</span></span>
      </button></li>`;
    }).join("")}</ul>`;
  el.querySelectorAll("[data-state]").forEach((btn) =>
    btn.addEventListener("click", () => selectState(btn.dataset.state)));
}

function renderDetail() {
  const el = document.getElementById("detail");
  const abbr = state.selectedState;
  if (!abbr) { renderOverview(el); return; }
  const feature = statesFeatures.find((f) => f.abbr === abbr);
  const races = state.data.races.filter((r) => r.state_abbrev === abbr);
  const title = feature ? feature.properties.name : abbr;
  const office = state.office === "senate" ? "Senate" : "Governor";
  const back = `<button type="button" class="back-btn">← Closest races</button>`;
  if (!races.length) {
    el.innerHTML = `${back}<h2>${title}</h2><p class="meta">${state.cycle} ${office}</p>
      <p class="empty">None of the selected pollsters surveyed this race. Select more pollsters to include it.</p>`;
  } else {
    el.innerHTML = `${back}<h2>${title}</h2>` + races.map(raceHtml).join("");
    races.forEach((r) => drawDistribution(r));
  }
  el.querySelector(".back-btn").addEventListener("click", clearSelection);
}

function raceHtml(r) {
  const a = partyInfo(r.cand_a.party), b = partyInfo(r.cand_b.party);
  const office = state.office === "senate" ? "Senate" : "Governor";
  const stage = r.stage === "general" ? "" : r.stage === "special" ? " special election" : " runoff";
  const lo = r.margin - 1.645 * r.sigma, hi = r.margin + 1.645 * r.sigma;
  const an = lastName(r.cand_a.name), bn = lastName(r.cand_b.name);
  const result = r.actual ? `<p class="result">Actual result: ${fmtMargin(r.actual.margin, an, bn)}.
      ${r.actual.favorite_won ? "The forecast favorite won." : "The forecast favorite lost."}</p>` : "";
  return `
  <div class="race">
    <p class="meta">${state.cycle} ${office}${stage} · as of ${shortDate(r.as_of)}</p>
    <div class="cand"><span class="name" style="color:${a.color}">${escapeHtml(r.cand_a.name)} (${a.label})</span><span class="pct">${pct(r.prob_a)}</span></div>
    <div class="cand"><span class="name" style="color:${b.color}">${escapeHtml(r.cand_b.name)} (${b.label})</span><span class="pct">${pct(r.prob_b)}</span></div>
    <div class="prob-bar"><div style="flex:${r.prob_a};background:${a.color}"></div><div style="flex:${r.prob_b};background:${b.color}"></div></div>
    <p class="explain">Polling average: <strong>${fmtMargin(r.margin, an, bn)}</strong> from ${r.n_polls} poll${r.n_polls === 1 ? "" : "s"}.
      Allowing for typical polling misses, the final margin will probably land between
      ${fmtMargin(lo, an, bn)} and ${fmtMargin(hi, an, bn)}.</p>
    <svg class="dist" id="dist-${r.race_id}" viewBox="0 0 300 70" aria-hidden="true"></svg>
    ${result}
    <details>
      <summary>Polls used and how much each counted</summary>
      <p class="explain">Results shown as ${an}–${bn}.</p>
      <table class="polls">
        <thead><tr><th>Pollster</th><th>Ended</th><th class="num">Result</th><th>Weight</th></tr></thead>
        <tbody>${r.polls.map((p) => `
          <tr><td>${escapeHtml(p.pollster)} <small>${gradeLetter(p.grade)}</small></td>
              <td class="date">${shortDate(p.end_date)}</td>
              <td class="num">${Math.round(p.a_pct)}–${Math.round(p.b_pct)}</td>
              <td><div class="wbar" style="width:${Math.max(4, p.weight_share * 100 / r.polls[0].weight_share)}%" title="${(p.weight_share * 100).toFixed(1)}% of the average"></div></td></tr>`).join("")}
        </tbody>
      </table>
    </details>
  </div>`;
}

// Bell curve of possible final margins: area right of 0 = candidate A wins.
function drawDistribution(r) {
  const el = d3.select(document.getElementById(`dist-${r.race_id}`));
  const W = 300, H = 70, top = 6, base = 52;
  const span = Math.max(Math.abs(r.margin) + 3 * r.sigma, 10);
  const x = d3.scaleLinear([-span, span], [6, W - 6]);
  const pdf = (m) => Math.exp(-0.5 * ((m - r.margin) / r.sigma) ** 2);
  const y = d3.scaleLinear([0, 1], [base, top]);
  const pts = d3.range(-span, span + 0.01, span / 60);
  const area = (filter) => d3.area().x((m) => x(m)).y0(base).y1((m) => y(pdf(m)))(pts.filter(filter));
  const a = partyInfo(r.cand_a.party), b = partyInfo(r.cand_b.party);
  el.append("path").attr("d", area((m) => m <= 0)).attr("fill", b.color).attr("opacity", 0.75);
  el.append("path").attr("d", area((m) => m >= 0)).attr("fill", a.color).attr("opacity", 0.75);
  el.append("line").attr("x1", x(0)).attr("x2", x(0)).attr("y1", top - 4).attr("y2", base)
    .attr("stroke", "var(--ink)").attr("stroke-dasharray", "2 2");
  el.append("text").attr("x", x(0)).attr("y", H - 4).attr("text-anchor", "middle").text("tie");
  el.append("text").attr("x", 6).attr("y", H - 4).text(`${lastName(r.cand_b.name)} wins`);
  el.append("text").attr("x", W - 6).attr("y", H - 4).attr("text-anchor", "end").text(`${lastName(r.cand_a.name)} wins`);
  if (r.actual) {
    const ax = x(Math.max(-span, Math.min(span, r.actual.margin)));
    el.append("line").attr("x1", ax).attr("x2", ax).attr("y1", top - 4).attr("y2", base)
      .attr("stroke", "var(--ink)").attr("stroke-width", 2.5);
    el.append("text").attr("x", ax).attr("y", top + 4).attr("dx", 4).text("actual");
  }
}

// ------------------------------------------------------------------ accuracy dialog
async function showAccuracy() {
  const dialog = document.getElementById("accuracy");
  const body = dialog.querySelector(".dialog-body");
  const rep = await api("/api/backtest");
  if (!rep) {
    body.innerHTML = `<h2>Model accuracy</h2><p>Run <code>python scripts/backtest.py</code> to test the model.</p>`;
  } else {
    const m = rep.model;
    body.innerHTML = `
      <h2>How accurate is this?</h2>
      <p>The model learned how big polling misses usually are from ${rep.trained_on.join(" and ")}
        (${rep.learned.training_races} races), then was tested on the ${rep.tested_on} midterms, which it never saw.</p>
      <p><span class="big">${m.favorite_won_pct}%</span><br>of ${m.races} Senate and governor races in ${rep.tested_on} were won by the forecast favorite.</p>
      <table>
        <tr><th></th><th>This model</th><th>Simple average</th></tr>
        <tr><td>Favorite won</td><td>${m.favorite_won_pct}%</td><td>${rep.simple_average.favorite_won_pct}%</td></tr>
        <tr><td>Average margin miss</td><td>${m.avg_margin_error} pts</td><td>${rep.simple_average.avg_margin_error} pts</td></tr>
      </table>
      <p>Are the percentages honest? Favorites should win about as often as the forecast says:</p>
      <table>
        <tr><th>Favorite's chance</th><th>Races</th><th>Favorite won</th></tr>
        ${m.calibration.map((c) => `<tr><td>${c.bucket}</td><td>${c.races}</td><td>${c.favorite_won_pct}%</td></tr>`).join("")}
      </table>
      <p>Races the favorite lost: ${rep.misses.map((x) => `${x.race} (favorite at ${Math.round(x.favorite_prob * 100)}%)`).join("; ") || "none"}.</p>`;
  }
  dialog.showModal();
}

// ------------------------------------------------------------------ setup
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

async function init() {
  Object.entries({
    AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado",
    CT: "Connecticut", DE: "Delaware", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho",
    IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana",
    ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota",
    MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada",
    NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina",
    ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania",
    RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas",
    UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia",
    WI: "Wisconsin", WY: "Wyoming", DC: "District of Columbia",
  }).forEach(([abbr, name]) => (NAME_TO_ABBR[name] = abbr));

  await drawMap();

  // Show 2026 even before its polls are loaded, but open on the newest election that has data.
  const loaded = [...new Set((await api("/api/cycles")).filter((c) => c.polls > 0).map((c) => c.cycle))];
  const cycles = [...new Set([2026, ...loaded])].sort((a, b) => b - a);
  const select = document.getElementById("cycle");
  select.innerHTML = cycles.map((c) =>
    `<option value="${c}">${c} ${c >= 2026 ? "(upcoming)" : "(with results)"}</option>`).join("");
  state.cycle = loaded.length ? Math.max(...loaded) : 2026;
  select.value = state.cycle;

  select.addEventListener("change", async () => {
    state.cycle = Number(select.value);
    state.selectedState = null;
    history.replaceState(null, "", location.pathname);
    await loadPollsters();
    await loadForecast();
  });
  document.querySelectorAll("input[name=office]").forEach((radio) =>
    radio.addEventListener("change", async () => {
      state.office = radio.value;
      await loadPollsters();
      await loadForecast();
    }));
  document.querySelectorAll("[data-preset]").forEach((btn) =>
    btn.addEventListener("click", () => applyPreset(btn.dataset.preset)));
  document.getElementById("include-partisan").addEventListener("change", (e) => {
    state.includePartisan = e.target.checked;
    loadForecast();
  });
  document.getElementById("accuracy-btn").addEventListener("click", showAccuracy);

  await loadPollsters();
  await loadForecast();
  selectState(location.hash.slice(1).toUpperCase());   // open a linked state, e.g. /#IA
}

init().catch((err) => {
  const el = document.getElementById("notice");
  el.textContent = `Couldn't load the forecast: ${err.message}`;
  el.hidden = false;
});
