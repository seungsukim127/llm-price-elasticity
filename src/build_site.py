<!DOCTYPE html>
<!--
  Template for the public dashboard. src/build_site.py replaces every
  double-curly-brace placeholder (e.g. LAST_DAY) with real values and writes the result to _site/index.html.
  Tables are filled in by Python (they work without JavaScript);
  the two charts are drawn in the browser by Chart.js from the embedded JSON.
-->
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LLM API prices &amp; usage tracker</title>
<meta name="description" content="Daily prices and token usage for LLMs on OpenRouter, updated automatically.">
<style>
  /* ---- Colors: one place for light mode, one for dark mode ---- */
  :root {
    color-scheme: light;
    --bg: #fcfcfb; --card: #ffffff; --line: #e6e5e1;
    --ink: #0b0b0b; --ink-2: #52514e; --ink-3: #8a8984;
    --accent: #2a78d6; --up: #0c7d0c; --down: #c23434;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #1a1a19; --card: #222220; --line: #383835;
      --ink: #f0efec; --ink-2: #c3c2b7; --ink-3: #8f8e86;
      --accent: #3987e5; --up: #3fbf3f; --down: #e66767;
    }
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--ink);
         font: 15px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 1040px; margin: 0 auto; padding: 40px 20px 64px; }
  h1 { font-size: 28px; line-height: 1.25; margin: 0 0 8px; font-weight: 650; }
  h2 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
  .lede { color: var(--ink-2); max-width: 680px; margin: 0 0 6px; }
  .meta { color: var(--ink-3); font-size: 13px; margin: 0 0 28px; }
  a { color: var(--accent); }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 12px;
          padding: 20px; margin-bottom: 20px; }
  .sub { color: var(--ink-2); font-size: 13px; margin: 0 0 16px; }

  /* headline numbers */
  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
  .stat { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px; }
  .stat .v { font-size: 26px; font-weight: 650; font-variant-numeric: tabular-nums; }
  .stat .l { color: var(--ink-2); font-size: 13px; }

  .chart-box { position: relative; height: 300px; }
  .legend { display: flex; flex-wrap: wrap; gap: 6px 16px; margin: 0 0 12px; padding: 0;
            list-style: none; font-size: 13px; color: var(--ink-2); }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 3px;
              margin-right: 6px; vertical-align: -1px; }

  /* tables and lists */
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th { text-align: left; font-weight: 500; color: var(--ink-3); font-size: 12px;
       border-bottom: 1px solid var(--line); padding: 8px 8px; white-space: nowrap; }
  td { border-bottom: 1px solid var(--line); padding: 8px; vertical-align: top; }
  tr:last-child td { border-bottom: 0; }
  .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
  th.num { text-align: right; }
  .model { font-weight: 500; }
  .slug { color: var(--ink-3); font-size: 12px; word-break: break-all; }
  .muted { color: var(--ink-3); }
  .up { color: var(--up); } .down { color: var(--down); }
  .chg { color: var(--ink-2); }

  /* controls: range buttons, search, model picker */
  .head-row { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: space-between;
              gap: 8px 16px; }
  .controls { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0 0 12px; }
  .seg { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  .seg button, .btn { font: inherit; font-size: 13px; color: var(--ink-2); background: var(--card);
                      border: 0; padding: 0 12px; min-height: 32px; cursor: pointer; }
  .seg button + button { border-left: 1px solid var(--line); }
  .seg button[aria-pressed="true"] { background: var(--accent); color: #fff; }
  .btn { border: 1px solid var(--line); border-radius: 8px; }
  .btn[aria-pressed="true"] { border-color: var(--accent); color: var(--accent); }
  input[type="search"], select { font: inherit; font-size: 14px; color: var(--ink); background: var(--card);
                                 border: 1px solid var(--line); border-radius: 8px; padding: 0 10px;
                                 min-height: 36px; max-width: 100%; }
  input[type="search"] { width: 220px; }
  select { min-width: 0; width: 320px; }
  button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px; }
  .count { color: var(--ink-3); font-size: 13px; }

  /* sortable table headers */
  th button.sort { font: inherit; color: inherit; background: none; border: 0; padding: 0;
                   cursor: pointer; white-space: nowrap; }
  th button.sort::after { content: " \2195"; opacity: 0.35; }
  th[aria-sort="ascending"] button.sort::after { content: " \2191"; opacity: 1; }
  th[aria-sort="descending"] button.sort::after { content: " \2193"; opacity: 1; }
  th[aria-sort] { color: var(--ink); }
  a.model-link { color: var(--ink); text-decoration: none; }
  a.model-link:hover { color: var(--accent); text-decoration: underline; }
  tr.is-hidden { display: none; }
  tr.is-selected td { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .badge { font-size: 11px; font-weight: 500; border: 1px solid var(--line); color: var(--ink-2);
           border-radius: 999px; padding: 0 7px; margin-left: 6px; vertical-align: 1px; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  .two .card { margin-bottom: 0; }
  .two > * { min-width: 0; }
  .events { list-style: none; margin: 0; padding: 0; font-size: 14px; }
  .events li { display: flex; gap: 12px; padding: 7px 0; border-bottom: 1px solid var(--line); }
  .events li:last-child { border-bottom: 0; }
  .events .date { color: var(--ink-3); font-variant-numeric: tabular-nums; min-width: 88px; }
  .events .what { flex: 1; }
  .empty { color: var(--ink-2); font-size: 14px; margin: 0; }
  details { margin-top: 12px; font-size: 13px; color: var(--ink-2); }
  footer { color: var(--ink-3); font-size: 13px; margin-top: 36px; line-height: 1.7; }
  .table-scroll { overflow-x: auto; }
  .table-scroll > table { min-width: 640px; }   /* scroll sideways on phones instead of squashing */

  @media (max-width: 760px) {
    .stats { grid-template-columns: 1fr 1fr; }
    .two { grid-template-columns: 1fr; }
    .two .card { margin-bottom: 20px; }
    input[type="search"], select { width: 100%; }
    /* price changes: put the price on its own line instead of pushing the page wider */
    .events li { flex-wrap: wrap; row-gap: 2px; }
    .events .chg { flex-basis: 100%; padding-left: 100px; text-align: left; }
  }
</style>
</head>
<body>
<main>
  <h1>LLM API prices &amp; usage tracker</h1>
  <p class="lede">How much are developers using each AI model, and what does it cost?
     Daily data from OpenRouter, collected automatically by a student research project
     on how price-sensitive LLM demand is.</p>
  <p class="meta">Usage data through {{LAST_DAY}} · page updated {{UPDATED}} ·
     <a href="https://github.com/seungsukim127/llm-price-elasticity">code &amp; method on GitHub</a></p>

  <!-- 1. Headline numbers -->
  <section class="stats" aria-label="Headline numbers">
    <div class="stat"><div class="v">{{TOKENS_LAST_DAY}}</div><div class="l">tokens processed on {{LAST_DAY}}</div></div>
    <div class="stat"><div class="v">{{N_MODELS_LISTED}}</div><div class="l">models listed today ({{N_FREE}} free variants)</div></div>
    <div class="stat"><div class="v">{{N_MODELS_SEEN}}</div><div class="l">models that reached the daily top 50 since {{FIRST_DAY}}</div></div>
    <div class="stat"><div class="v">{{N_SNAPSHOTS}}</div><div class="l">daily price snapshots since {{TRACKING_SINCE}}</div></div>
  </section>

  <!-- 2. Market size over time -->
  <section class="card">
    <h2>Daily tokens on OpenRouter</h2>
    <p class="sub">All models, prompt + completion tokens. Line = 7-day average; faint line = daily value.
       Log scale makes early growth visible.</p>
    <div class="controls">
      <div class="seg" id="marketRange" role="group" aria-label="Time range">
        <button type="button" data-days="90">3M</button><button type="button" data-days="365" aria-pressed="true">1Y</button><button type="button" data-days="0">All</button>
      </div>
      <div class="seg" id="marketScale" role="group" aria-label="Vertical scale">
        <button type="button" data-scale="linear" aria-pressed="true">Linear</button><button type="button" data-scale="logarithmic">Log</button>
      </div>
    </div>
    <div class="chart-box"><canvas id="marketChart" role="img"
         aria-label="Line chart of daily tokens processed on OpenRouter since {{FIRST_DAY}}"></canvas></div>
  </section>

  <!-- 3. Who gets the tokens -->
  <section class="card">
    <h2>Share of tokens by model provider</h2>
    <p class="sub">Weekly, last 12 months. "Everyone else" includes all models outside the daily top 50.</p>
    <ul class="legend" id="shareLegend"></ul>
    <div class="chart-box"><canvas id="shareChart" role="img"
         aria-label="Stacked area chart of each provider's share of weekly tokens"></canvas></div>
    <details><summary>Show as a table</summary><div class="table-scroll" id="shareTable"></div></details>
  </section>

  <!-- 4. Top models -->
  <section class="card">
    <h2>Most-used models on {{LAST_DAY}}</h2>
    <p class="sub">Top {{TABLE_ROWS}} models. Prices in USD per 1M tokens from today's snapshot. Change = vs. the same
       day a week earlier. Click a column to sort, or a model to see its price and usage over time.</p>
    <div class="controls">
      <input type="search" id="modelSearch" placeholder="Search models" aria-label="Search models">
      <button type="button" class="btn" id="showAll" aria-pressed="false">Show all {{TABLE_ROWS}}</button>
      <span class="count" id="rowCount" aria-live="polite"></span>
    </div>
    <div class="table-scroll">
    <table id="topTable">
      <thead><tr>
        <th class="num"><button type="button" class="sort" data-key="rank">#</button></th>
        <th><button type="button" class="sort" data-key="name">Model</button></th>
        <th class="num"><button type="button" class="sort" data-key="tokens">Tokens</button></th>
        <th class="num"><button type="button" class="sort" data-key="share">Share</button></th>
        <th class="num"><button type="button" class="sort" data-key="change">7-day change</button></th>
        <th class="num"><button type="button" class="sort" data-key="pin">Input $/1M</button></th>
        <th class="num"><button type="button" class="sort" data-key="pout">Output $/1M</button></th>
      </tr></thead>
      <tbody>
{{TOP_ROWS}}
      </tbody>
    </table>
    </div>
  </section>

  <!-- 5. One model: price and usage over time -->
  <section class="card" id="detail">
    <h2>Price and usage of one model</h2>
    <p class="sub">Daily tokens (left axis) and blended price, 3:1 input:output (right axis).
       Prices are tracked from {{TRACKING_SINCE}}, so earlier days show usage only.
       &#9660;/&#9650; mark price cuts/increases that held for 7+ days.</p>
    <div class="controls">
      <select id="modelSelect" aria-label="Model"></select>
      <div class="seg" id="detailRange" role="group" aria-label="Time range">
        <button type="button" data-days="30">1M</button><button type="button" data-days="90" aria-pressed="true">3M</button><button type="button" data-days="365">1Y</button><button type="button" data-days="0">All</button>
      </div>
    </div>
    <div class="chart-box"><canvas id="detailChart" role="img" aria-label="Price and usage of the selected model"></canvas></div>
    <p class="sub" id="detailNote" aria-live="polite" style="margin:12px 0 0">Loading model history&hellip;</p>
    <noscript><p class="empty">This chart needs JavaScript.</p></noscript>
  </section>

  <!-- 6. Price changes and retirements -->
  <div class="two">
    <section class="card">
      <h2>Recent price changes</h2>
      <p class="sub">Blended price (3:1 input:output), last 90 days. Counted only when the new price
         held for 7+ days.</p>
{{PRICE_CHANGES}}
    </section>
    <section class="card">
      <h2>Scheduled retirements</h2>
      <p class="sub">Models and free variants OpenRouter lists with an end date.</p>
{{EXPIRING}}
    </section>
  </div>

  <footer>
    Source: OpenRouter (openrouter.ai/rankings), as of {{AS_OF}}. Usage data licensed under
    <a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>.
    Covers developer API traffic on OpenRouter only, not consumer apps. Token counts come from each
    provider's own tokenizer, so they are not exactly comparable across providers.<br>
    Built by Seungsu Kim · <a href="https://github.com/seungsukim127/llm-price-elasticity">GitHub</a>
  </footer>
</main>

<!-- Data written by build_site.py -->
<script id="data" type="application/json">{{DATA_JSON}}</script>
<script>
// Top-models table: sort, search, show all. Runs even if the charts fail to load.
(function () {
  const table = document.getElementById("topTable");
  if (!table) return;
  const tbody = table.tBodies[0];
  const rows = Array.from(tbody.rows);
  const search = document.getElementById("modelSearch");
  const showAll = document.getElementById("showAll");
  const count = document.getElementById("rowCount");
  const DEFAULT_ROWS = 20;
  let sortKey = "rank", sortDir = 1;

  const value = (tr, key) => {
    const v = tr.dataset[key];
    if (key === "name") return v;
    return v === "" ? null : Number(v);
  };

  function apply() {
    const q = search.value.trim().toLowerCase();
    const all = showAll.getAttribute("aria-pressed") === "true";
    rows.sort((a, b) => {
      const x = value(a, sortKey), y = value(b, sortKey);
      if (x === null && y === null) return 0;
      if (x === null) return 1;            // empty values always last
      if (y === null) return -1;
      return (x < y ? -1 : x > y ? 1 : 0) * sortDir;
    });
    let shown = 0;
    rows.forEach((tr) => {
      const match = !q || tr.dataset.name.includes(q) || tr.dataset.slug.toLowerCase().includes(q);
      const visible = match && (q || all || shown < DEFAULT_ROWS);
      tr.classList.toggle("is-hidden", !visible);
      if (visible) shown += 1;
      tbody.appendChild(tr);
    });
    count.textContent = q ? `${shown} of ${rows.length} match` : `Showing ${shown} of ${rows.length}`;
    table.querySelectorAll("th").forEach((th) => {
      const b = th.querySelector("button.sort");
      if (b && b.dataset.key === sortKey) th.setAttribute("aria-sort", sortDir > 0 ? "ascending" : "descending");
      else th.removeAttribute("aria-sort");
    });
  }

  table.querySelectorAll("button.sort").forEach((b) => b.addEventListener("click", () => {
    const key = b.dataset.key;
    if (key === sortKey) sortDir = -sortDir;
    else { sortKey = key; sortDir = (key === "rank" || key === "name") ? 1 : -1; }  // numbers: biggest first
    apply();
  }));
  search.addEventListener("input", apply);
  showAll.addEventListener("click", () => {
    const on = showAll.getAttribute("aria-pressed") !== "true";
    showAll.setAttribute("aria-pressed", String(on));
    showAll.textContent = on ? "Show top 20" : `Show all ${rows.length}`;
    apply();
  });
  apply();
})();
</script>
<script src="chart.umd.min.js"></script>
<script>
(function () {
  const DATA = JSON.parse(document.getElementById("data").textContent);
  if (!window.Chart) {                     // tables still work without charts
    const note = document.getElementById("detailNote");
    if (note) note.textContent = "Charts could not be loaded.";
    return;
  }

  // Read colors from the CSS variables so light/dark mode stay in one place.
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;

  // Fixed categorical order (validated for color-vision deficiency); "Everyone else" is gray.
  const PALETTE = dark
    ? ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
    : ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"];
  const OTHER = dark ? "#5f5e5a" : "#b4b2a9";

  const fmtTokens = (x) => {
    if (x == null) return "–";
    for (const [u, d] of [["T", 1e12], ["B", 1e9], ["M", 1e6]]) if (Math.abs(x) >= d) return (x / d).toFixed(1) + u;
    return Math.round(x).toString();
  };

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = css("--ink-2");
  Chart.defaults.borderColor = css("--line");

  const tooltip = {
    backgroundColor: css("--card"), titleColor: css("--ink"), bodyColor: css("--ink-2"),
    borderColor: css("--line"), borderWidth: 1, padding: 10, boxPadding: 4,
  };
  const xAxis = {
    grid: { display: false },
    ticks: { maxTicksLimit: window.innerWidth < 600 ? 3 : 8, maxRotation: 0, autoSkip: true },
  };

  // Shared helpers for the time-range buttons.
  const setPressed = (group, btn) =>
    group.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
  const startIndex = (dates, days) => {
    if (!days) return 0;
    const end = new Date(dates[dates.length - 1]);
    const from = new Date(end.getTime() - (days - 1) * 86400000).toISOString().slice(0, 10);
    const i = dates.findIndex((d) => d >= from);
    return i < 0 ? 0 : i;
  };

  // ---- Chart 1: market size (one series, so no legend; the title names it) ----
  const marketChart = new Chart(document.getElementById("marketChart"), {
    type: "line",
    data: {
      labels: DATA.market.dates,
      datasets: [
        { label: "7-day average", data: DATA.market.avg7, borderColor: PALETTE[0],
          borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.2 },
        { label: "Daily", data: DATA.market.total, borderColor: PALETTE[0] + "40",
          borderWidth: 1, pointRadius: 0, pointHoverRadius: 0 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },   // hover anywhere on a date
      plugins: {
        legend: { display: false },
        tooltip: { ...tooltip, callbacks: { label: (c) => ` ${c.dataset.label}: ${fmtTokens(c.parsed.y)} tokens` } },
      },
      scales: {
        x: xAxis,
        y: { type: "linear", beginAtZero: true, border: { display: false },
             ticks: { callback: (v) => fmtTokens(v), maxTicksLimit: 5 } },
      },
    },
  });

  const market = { days: 365, scale: "linear" };
  function drawMarket() {
    const i = startIndex(DATA.market.dates, market.days);
    marketChart.data.labels = DATA.market.dates.slice(i);
    marketChart.data.datasets[0].data = DATA.market.avg7.slice(i);
    marketChart.data.datasets[1].data = DATA.market.total.slice(i);
    const y = marketChart.options.scales.y;
    y.type = market.scale;
    y.beginAtZero = market.scale === "linear";
    if (market.scale === "logarithmic") {
      // label powers of ten only, so log ticks stay readable
      y.ticks.callback = (v) => {
        const e = Math.log10(v);
        return Math.abs(e - Math.round(e)) < 1e-9 ? fmtTokens(v) : "";
      };
    } else {
      y.ticks.callback = (v) => fmtTokens(v);
    }
    marketChart.update();
  }
  const marketRange = document.getElementById("marketRange");
  marketRange.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    market.days = Number(b.dataset.days); setPressed(marketRange, b); drawMarket();
  }));
  const marketScale = document.getElementById("marketScale");
  marketScale.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    market.scale = b.dataset.scale; setPressed(marketScale, b); drawMarket();
  }));
  drawMarket();

  // ---- Chart 2: provider share, 100% stacked area ----
  const series = DATA.share.series;
  const colorOf = (i, name) => (name === "Everyone else" ? OTHER : PALETTE[i % PALETTE.length]);
  new Chart(document.getElementById("shareChart"), {
    type: "line",
    data: {
      labels: DATA.share.dates,
      datasets: series.map((s, i) => ({
        label: s.name, data: s.values, fill: i === 0 ? "origin" : "-1",
        backgroundColor: colorOf(i, s.name), borderColor: css("--card"), borderWidth: 1,
        pointRadius: 0, pointHoverRadius: 0, tension: 0.2,
      })),
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },     // custom HTML legend below the title
        tooltip: { ...tooltip, itemSort: (a, b) => b.datasetIndex - a.datasetIndex,
                   callbacks: { label: (c) => ` ${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } },
      },
      scales: {
        x: xAxis,
        y: { stacked: true, min: 0, max: 100, border: { display: false },
             ticks: { callback: (v) => v + "%", stepSize: 25 } },
      },
    },
  });

  // Legend + table view for chart 2 (so identity never depends on color alone)
  document.getElementById("shareLegend").innerHTML = series
    .map((s, i) => `<li><i style="background:${colorOf(i, s.name)}"></i>${s.name}</li>`).join("");
  const last = DATA.share.dates.length - 1;
  const rows = series.map((s) =>
    `<tr><td>${s.name}</td><td class="num">${s.values[0].toFixed(1)}%</td><td class="num">${s.values[last].toFixed(1)}%</td></tr>`).join("");
  document.getElementById("shareTable").innerHTML =
    `<table><thead><tr><th>Provider</th><th class="num">Week of ${DATA.share.dates[0]}</th>` +
    `<th class="num">Week of ${DATA.share.dates[last]}</th></tr></thead><tbody>${rows}</tbody></table>`;

  // ---- Chart 3: one model's daily tokens and price (history loaded from models.json) ----
  const select = document.getElementById("modelSelect");
  const note = document.getElementById("detailNote");
  const detailRange = document.getElementById("detailRange");
  const detail = { slug: DATA.top.length ? DATA.top[0].slug : null, days: 90 };
  const nameOf = {};
  DATA.top.forEach((r) => { nameOf[r.slug] = r.name; });
  select.innerHTML = DATA.top.map((r) => `<option value="${r.slug}">${r.rank}. ${r.name}</option>`).join("");
  const fmtPrice = (p) => (p == null ? "–" : p === 0 ? "Free" : "$" + (p >= 0.1 ? p.toFixed(2) : p.toFixed(3)));
  let HIST = null, detailChart = null;

  function drawDetail() {
    if (!HIST || !detail.slug) return;
    const m = HIST.models[detail.slug];
    if (!m) { note.textContent = "No history for this model."; return; }
    const i = startIndex(HIST.dates, detail.days);
    const labels = HIST.dates.slice(i);
    const tokens = m.tokens.slice(i), price = m.price.slice(i);
    const byDate = {};
    m.events.forEach((e) => { byDate[e.date] = e; });
    const marks = labels.map((d, k) => (byDate[d] ? price[k] : null));
    const evAt = labels.map((d) => byDate[d] || null);
    const priced = price.filter((p) => p != null);

    const cfg = {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: "Tokens", data: tokens, yAxisID: "y", borderColor: PALETTE[0], borderWidth: 2,
            pointRadius: 0, pointHoverRadius: 4, tension: 0.2, spanGaps: false },
          { label: "Price", data: price, yAxisID: "y2", borderColor: PALETTE[1], borderWidth: 2,
            stepped: true, pointRadius: 0, pointHoverRadius: 4, spanGaps: false },
          { label: "Price change", data: marks, yAxisID: "y2", showLine: false,
            pointStyle: "triangle", pointRadius: 7, pointHoverRadius: 8,
            pointRotation: (c) => (evAt[c.dataIndex] && evAt[c.dataIndex].pct < 0 ? 180 : 0),
            backgroundColor: css("--ink"), borderColor: css("--card"), borderWidth: 1 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: true, position: "top", align: "start",
                    labels: { boxWidth: 10, boxHeight: 10, usePointStyle: true, padding: 12 } },
          tooltip: { ...tooltip, filter: (c) => c.raw != null, callbacks: {
            label: (c) => {
              if (c.datasetIndex === 0) return ` Tokens: ${fmtTokens(c.parsed.y)}`;
              if (c.datasetIndex === 1) return ` Price: ${fmtPrice(c.parsed.y)} per 1M`;
              const e = evAt[c.dataIndex];
              return ` ${e.pct < 0 ? "\u25BC Price cut" : "\u25B2 Price increase"}: ${fmtPrice(e.before)} \u2192 ${fmtPrice(e.after)} (${e.pct > 0 ? "+" : ""}${e.pct.toFixed(0)}%)`;
            } } },
        },
        scales: {
          x: { ...xAxis },
          y: { position: "left", beginAtZero: true, border: { display: false },
               title: { display: true, text: "Tokens per day" },
               ticks: { callback: (v) => fmtTokens(v), maxTicksLimit: 5 } },
          y2: { position: "right", beginAtZero: true, border: { display: false },
                grid: { drawOnChartArea: false },
                title: { display: true, text: "$ per 1M tokens" },
                ticks: { callback: (v) => fmtPrice(v), maxTicksLimit: 5 } },
        },
      },
    };
    if (detailChart) detailChart.destroy();
    detailChart = new Chart(document.getElementById("detailChart"), cfg);
    document.getElementById("detailChart").setAttribute("aria-label",
      `Daily tokens and blended price of ${nameOf[detail.slug] || detail.slug}`);

    const nEv = m.events.length;
    note.textContent = (priced.length ? "" : "No price observed in this range. ") +
      (nEv ? `${nEv} price change${nEv > 1 ? "s" : ""} since tracking started.`
           : "No price change since tracking started.") +
      " Gaps in the token line are days the model was outside the daily top 50.";
    document.querySelectorAll("#topTable tbody tr").forEach((tr) =>
      tr.classList.toggle("is-selected", tr.dataset.slug === detail.slug));
  }

  function choose(slug) {
    if (!(slug in nameOf)) return;
    detail.slug = slug; select.value = slug; drawDetail();
  }
  select.addEventListener("change", () => choose(select.value));
  detailRange.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => {
    detail.days = Number(b.dataset.days); setPressed(detailRange, b); drawDetail();
  }));
  document.querySelectorAll("a.model-link").forEach((a) => a.addEventListener("click", () => {
    choose(a.dataset.slug);              // the href="#detail" then scrolls to the chart
  }));

  fetch("models.json")
    .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
    .then((h) => { HIST = h; drawDetail(); })
    .catch(() => { note.textContent = "Model history could not be loaded."; });
})();
</script>
</body>
</html>
