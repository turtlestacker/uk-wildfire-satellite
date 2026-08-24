/* UK wildfire activity dashboard — reads pre-built JSON from data/, renders with ECharts + Leaflet. */
(function () {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const tok = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtInt = (n) => Number(n).toLocaleString("en-GB");
  const fmtDate = (s) => new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const fmtDateShort = (s) => new Date(s + "T00:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const charts = {};
  let D = null; // all loaded data
  let map, mapLayers = {};
  let currentRangeDays = 365;

  // ------------------------------------------------------------------ loading
  async function load() {
    const files = ["daily", "events_all", "detections_recent", "annual", "monthly", "cumulative", "persistent_sources", "meta"];
    const res = await Promise.all(files.map((f) => fetch("data/" + f + ".json").then((r) => {
      if (!r.ok) throw new Error(f + ".json: " + r.status);
      return r.json();
    })));
    D = Object.fromEntries(files.map((f, i) => [f, res[i]]));
  }

  // ------------------------------------------------------------------ chrome helpers
  function theme() {
    return {
      surface: tok("--surface"), ink: tok("--ink"), ink2: tok("--ink-2"), muted: tok("--muted"),
      grid: tok("--grid"), axis: tok("--axis"), s1: tok("--series-1"), s1soft: tok("--series-1-soft"),
      s2: tok("--series-2"), s2soft: tok("--series-2-soft"), context: tok("--context"),
      seqLo: tok("--seq-lo"), seqHi: tok("--seq-hi"), border: tok("--border"),
    };
  }
  function baseAxis(t) {
    return {
      axisLine: { lineStyle: { color: t.axis } }, axisTick: { show: false },
      axisLabel: { color: t.muted, fontSize: 12 },
      splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" } },
    };
  }
  function tooltipStyle(t) {
    return {
      backgroundColor: t.surface, borderColor: t.border, borderWidth: 1, padding: [8, 12],
      textStyle: { color: t.ink, fontSize: 12 }, extraCssText: "box-shadow: 0 4px 16px rgba(0,0,0,.12); border-radius: 8px;",
    };
  }
  function row(color, label, value, strong) {
    return '<div style="display:flex;justify-content:space-between;gap:16px;align-items:center">' +
      '<span style="display:inline-flex;align-items:center;gap:6px;color:' + (strong ? "inherit" : "var(--ink-2)") + '">' +
      (color ? '<span style="display:inline-block;width:14px;height:2px;background:' + color + '"></span>' : "") + esc(label) + "</span>" +
      '<strong style="font-variant-numeric:tabular-nums">' + esc(value) + "</strong></div>";
  }
  function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  function mk(chartId) {
    const el = document.getElementById(chartId);
    if (charts[chartId]) charts[chartId].dispose();
    charts[chartId] = echarts.init(el, null, { renderer: "canvas" });
    return charts[chartId];
  }

  // ------------------------------------------------------------------ KPI tiles
  function renderKpis() {
    const m = D.meta.headline, d = D.daily, n = d.dates.length;
    const ytd = m.ytd;
    const vsPrior = ytd.prior_mean ? Math.round((ytd.ytd / ytd.prior_mean - 1) * 100) : null;
    const last90 = (arr) => arr.slice(Math.max(0, n - 90));
    const tiles = [
      { label: "Active fire events, last 7 days", value: fmtInt(m.events_last7), delta: fmtInt(m.new_events_last7) + " new events · " + fmtInt(m.detections_last7) + " detections", spark: last90(d.active_events) },
      { label: "7-day average of active events", value: m.avg7_active_events.toFixed(1), delta: "per day, to " + fmtDate(D.meta.data_end), spark: last90(d.active_events_7d) },
      { label: "Fire events started this year", value: fmtInt(ytd.ytd), delta: ytd.prior_mean != null ? (vsPrior >= 0 ? "+" : "") + vsPrior + "% vs " + ytd.prior_years.length + "-year average for the same period (" + ytd.prior_mean + ")" : "", cls: vsPrior > 0 ? "up" : "down", spark: null, text: ytd.rank ? "Rank " + ytd.rank + " of " + (ytd.prior_years.length + 1) + " years to date · record " + fmtInt(ytd.prior_max) + " (" + ytd.prior_max_year + ")" : "" },
      { label: "Satellite detections this year", value: fmtInt(m.ytd_detections.ytd), delta: m.ytd_detections.prior_mean != null ? "Average for the same period " + fmtInt(Math.round(m.ytd_detections.prior_mean)) + " · record " + fmtInt(m.ytd_detections.prior_max) + " (" + m.ytd_detections.prior_max_year + ")" : "", spark: last90(d.detections), sparkColor: "--series-2" },
    ];
    const root = $("#kpis");
    root.textContent = "";
    for (const t of tiles) {
      const tile = document.createElement("div"); tile.className = "tile";
      const l = document.createElement("div"); l.className = "label"; l.textContent = t.label;
      const v = document.createElement("div"); v.className = "value"; v.textContent = t.value;
      const dl = document.createElement("div"); dl.className = "delta " + (t.cls || ""); dl.textContent = t.delta;
      tile.append(l, v, dl);
      if (t.text) { const x = document.createElement("div"); x.className = "delta"; x.textContent = t.text; tile.append(x); }
      if (t.spark) tile.append(sparkline(t.spark, t.sparkColor || "--series-1"));
      root.append(tile);
    }
  }
  function sparkline(values, colorVar) {
    const w = 200, h = 34, max = Math.max(1, ...values), step = w / (values.length - 1);
    const pts = values.map((v, i) => (i * step).toFixed(1) + "," + (h - (v / max) * (h - 2) - 1).toFixed(1));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 " + w + " " + h); svg.setAttribute("preserveAspectRatio", "none"); svg.classList.add("spark");
    const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    path.setAttribute("points", pts.join(" ")); path.setAttribute("fill", "none"); path.setAttribute("stroke", "var(" + colorVar + ")");
    path.setAttribute("stroke-width", "2"); path.setAttribute("stroke-linejoin", "round"); path.setAttribute("stroke-linecap", "round");
    path.setAttribute("vector-effect", "non-scaling-stroke");
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    const last = pts[pts.length - 1].split(",");
    dot.setAttribute("cx", last[0]); dot.setAttribute("cy", last[1]); dot.setAttribute("r", "3"); dot.setAttribute("fill", "var(" + colorVar + ")");
    svg.append(path, dot);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title"); title.textContent = "Last 90 days"; svg.prepend(title);
    return svg;
  }

  // ------------------------------------------------------------------ time series
  function rangeStart(days) {
    const n = D.daily.dates.length;
    return days > 0 ? D.daily.dates[Math.max(0, n - days)] : D.daily.dates[0];
  }
  function renderTimeSeries() {
    const t = theme(), d = D.daily;
    const dates = d.dates, snppOnly = $("#snpp-only").checked;
    const det = snppOnly ? d.detections_snpp : d.detections;
    const det7 = rolling(det, 7);
    const start = rangeStart(currentRangeDays), end = dates[dates.length - 1];

    const common = (title) => ({
      animation: false, backgroundColor: "transparent",
      grid: { left: 48, right: 16, top: 36, bottom: 30, containLabel: false },
      xAxis: { type: "time", ...baseAxis(t), splitLine: { show: false }, min: start, max: end, axisLabel: { color: t.muted, fontSize: 12, hideOverlap: true } },
      yAxis: { type: "value", ...baseAxis(t), axisLine: { show: false }, minInterval: 1, axisLabel: { color: t.muted, fontSize: 12, formatter: (v) => fmtInt(v) } },
      tooltip: { trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } }, ...tooltipStyle(t), formatter: title },
      legend: { top: 0, left: 0, icon: "rect", itemWidth: 12, itemHeight: 12, textStyle: { color: t.ink2, fontSize: 12 } },
    });

    const ev = mk("chart-events");
    ev.setOption({
      ...common((p) => tipEvents(p)),
      dataZoom: [
        { type: "inside", xAxisIndex: 0, startValue: start, endValue: end, filterMode: "none" },
        { type: "slider", xAxisIndex: 0, startValue: start, endValue: end, height: 18, bottom: 4, borderColor: t.grid, backgroundColor: "transparent",
          fillerColor: t.grid, dataBackground: { lineStyle: { color: t.axis }, areaStyle: { color: t.grid } }, selectedDataBackground: { lineStyle: { color: t.s1 }, areaStyle: { color: t.s1soft } },
          handleStyle: { color: t.surface, borderColor: t.axis }, moveHandleStyle: { color: t.axis }, textStyle: { color: t.muted, fontSize: 11 }, brushSelect: false, filterMode: "none" },
      ],
      grid: { left: 48, right: 16, top: 36, bottom: 52 },
      series: [
        { name: "Active events per day", type: "bar", data: zip(dates, d.active_events), itemStyle: { color: t.s1soft, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 24, barCategoryGap: "20%", large: true, z: 1 },
        { name: "7-day average", type: "line", data: zip(dates, d.active_events_7d), showSymbol: false, lineStyle: { color: t.s1, width: 2, join: "round", cap: "round" }, itemStyle: { color: t.s1 }, z: 2 },
      ],
    });

    const dc = mk("chart-detections");
    dc.setOption({
      ...common((p) => tipDetections(p, snppOnly)),
      dataZoom: [{ type: "inside", xAxisIndex: 0, startValue: start, endValue: end, filterMode: "none" }],
      series: [
        { name: snppOnly ? "Suomi NPP detections per day" : "Detections per day", type: "bar", data: zip(dates, det), itemStyle: { color: t.s2soft, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 24, large: true, z: 1 },
        { name: "7-day average", type: "line", data: zip(dates, det7), showSymbol: false, lineStyle: { color: t.s2, width: 2 }, itemStyle: { color: t.s2 }, z: 2 },
      ],
    });
    echarts.connect([ev, dc]);
    ev.off("datazoom");
    ev.on("datazoom", debounce(() => { renderDailyTable(); }, 200));
    renderDailyTable();
  }
  function tipEvents(params) {
    const i = D.daily.dates.indexOf(params[0].axisValue instanceof Date ? isoDate(params[0].axisValue) : String(params[0].axisValue).slice(0, 10));
    if (i < 0) return "";
    const d = D.daily, t = theme();
    return '<div style="margin-bottom:6px;color:var(--ink-2)">' + esc(fmtDate(d.dates[i])) + (d.product_status[i] === "NRT" ? " · near-real-time" : "") + "</div>" +
      row(t.s1soft, "Active events", fmtInt(d.active_events[i]), true) + row(t.s1, "7-day average", d.active_events_7d[i].toFixed(1)) +
      row(null, "New events", fmtInt(d.new_events[i])) + row(null, "Detections", fmtInt(d.detections[i]));
  }
  function tipDetections(params, snppOnly) {
    const i = D.daily.dates.indexOf(params[0].axisValue instanceof Date ? isoDate(params[0].axisValue) : String(params[0].axisValue).slice(0, 10));
    if (i < 0) return "";
    const d = D.daily, t = theme();
    return '<div style="margin-bottom:6px;color:var(--ink-2)">' + esc(fmtDate(d.dates[i])) + "</div>" +
      row(t.s2soft, snppOnly ? "Suomi NPP detections" : "Detections", fmtInt(snppOnly ? d.detections_snpp[i] : d.detections[i]), true) +
      row(null, "Suomi NPP", fmtInt(d.detections_snpp[i])) + row(null, "NOAA-20", fmtInt(d.detections_noaa20[i])) + row(null, "NOAA-21", fmtInt(d.detections_noaa21[i])) +
      row(null, "Total FRP (MW)", fmtInt(Math.round(d.frp_sum[i])));
  }
  function isoDate(dt) { return new Date(dt).toISOString().slice(0, 10); }
  function zip(a, b) { return a.map((x, i) => [x, b[i]]); }
  function rolling(arr, w) {
    const out = new Array(arr.length); let s = 0;
    for (let i = 0; i < arr.length; i++) { s += arr[i]; if (i >= w) s -= arr[i - w]; out[i] = +(s / Math.min(w, i + 1)).toFixed(2); }
    return out;
  }
  function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }
  function currentZoom() {
    const opt = charts["chart-events"].getOption();
    const dz = opt.dataZoom[0];
    const dates = D.daily.dates;
    const s = dz.startValue != null ? isoDate(dz.startValue) : dates[0];
    const e = dz.endValue != null ? isoDate(dz.endValue) : dates[dates.length - 1];
    return [s, e];
  }
  function renderDailyTable() {
    const [s, e] = currentZoom();
    const d = D.daily, rows = [];
    for (let i = d.dates.length - 1; i >= 0; i--) if (d.dates[i] >= s && d.dates[i] <= e) rows.push(i);
    const limited = rows.slice(0, 400);
    table("#table-daily", ["Date", "Active events", "New events", "Detections", "Suomi NPP", "NOAA-20", "NOAA-21", "7-day avg events", "Data"],
      limited.map((i) => [fmtDate(d.dates[i]), d.active_events[i], d.new_events[i], d.detections[i], d.detections_snpp[i], d.detections_noaa20[i], d.detections_noaa21[i], d.active_events_7d[i].toFixed(1), d.product_status[i] || "—"]),
      [false, true, true, true, true, true, true, true, false]);
    if (rows.length > limited.length) {
      const cap = document.createElement("caption"); cap.textContent = "Showing the most recent 400 of " + fmtInt(rows.length) + " days in range; download the CSV for the full series.";
      cap.style.captionSide = "bottom"; cap.style.color = "var(--muted)"; cap.style.fontSize = ".8rem"; cap.style.padding = "6px";
      $("#table-daily").append(cap);
    }
  }
  function table(sel, head, rows, numeric, onRow) {
    const tbl = $(sel); tbl.textContent = "";
    const thead = document.createElement("thead"), tr = document.createElement("tr");
    head.forEach((h, i) => { const th = document.createElement("th"); th.textContent = h; if (numeric && numeric[i]) th.className = "num"; tr.append(th); });
    thead.append(tr); tbl.append(thead);
    const tb = document.createElement("tbody");
    rows.forEach((r, ri) => {
      const tr = document.createElement("tr");
      r.forEach((c, i) => { const td = document.createElement("td"); td.textContent = typeof c === "number" ? fmtInt(c) : c; if (numeric && numeric[i]) td.className = "num"; tr.append(td); });
      if (onRow) { tr.className = "clickable"; tr.tabIndex = 0; tr.addEventListener("click", () => onRow(ri)); tr.addEventListener("keydown", (e) => { if (e.key === "Enter") onRow(ri); }); }
      tb.append(tr);
    });
    tbl.append(tb);
    return tb;
  }

  // ------------------------------------------------------------------ cumulative comparison (emphasis form)
  function renderCumulative() {
    const t = theme(), c = D.cumulative, years = Object.keys(c.years).map(Number).sort();
    const thisYear = years[years.length - 1];
    const sel = $("#compare-year");
    if (!sel.options.length) {
      const priors = years.filter((y) => y < thisYear).reverse();
      const none = document.createElement("option"); none.value = ""; none.textContent = "none"; sel.append(none);
      priors.forEach((y) => { const o = document.createElement("option"); o.value = y; o.textContent = y; sel.append(o); });
      // default: the prior year with the most events
      const best = priors.reduce((a, y) => (c.years[y][c.years[y].length - 1] > c.years[a][c.years[a].length - 1] ? y : a), priors[0]);
      sel.value = best;
      sel.addEventListener("change", renderCumulative);
    }
    const hl = sel.value ? Number(sel.value) : null;
    const doyToday = dayOfYear(D.meta.data_end);
    const series = [];
    years.filter((y) => y !== thisYear && y !== hl).forEach((y) => series.push({
      name: "Previous years", type: "line", data: zip(c.doy, c.years[y]), showSymbol: false, silent: false,
      lineStyle: { color: t.context, width: 1.5 }, itemStyle: { color: t.context }, emphasis: { disabled: true }, z: 1, _year: y,
    }));
    if (hl) series.push({ name: String(hl), type: "line", data: zip(c.doy, c.years[hl]), showSymbol: false, lineStyle: { color: t.s2, width: 2 }, itemStyle: { color: t.s2 }, z: 2, endLabel: { show: true, formatter: String(hl), color: t.ink2, fontSize: 12 }, _year: hl });
    series.push({ name: String(thisYear), type: "line", data: zip(c.doy.slice(0, doyToday), c.years[thisYear].slice(0, doyToday)), showSymbol: false, lineStyle: { color: t.s1, width: 2.5 }, itemStyle: { color: t.s1 }, z: 3,
      endLabel: { show: true, formatter: (p) => thisYear + ": " + fmtInt(p.value[1]), color: t.ink, fontWeight: 600, fontSize: 12 }, _year: thisYear });
    const ch = mk("chart-cumulative");
    ch.setOption({
      animation: false, backgroundColor: "transparent",
      grid: { left: 48, right: 90, top: 36, bottom: 30 },
      legend: { top: 0, left: 0, icon: "rect", itemWidth: 12, itemHeight: 3, textStyle: { color: t.ink2, fontSize: 12 }, data: [String(thisYear), ...(hl ? [String(hl)] : []), "Previous years"] },
      xAxis: { type: "value", min: 1, max: 366, ...baseAxis(t), splitLine: { show: false }, interval: 30.5, axisLabel: { color: t.muted, fontSize: 12, formatter: (v) => MONTHS[Math.min(11, Math.floor((v - 1) / 30.5))] || "" } },
      yAxis: { type: "value", ...baseAxis(t), axisLine: { show: false }, axisLabel: { color: t.muted, fontSize: 12, formatter: (v) => fmtInt(v) } },
      tooltip: { trigger: "axis", axisPointer: { type: "line", lineStyle: { color: t.axis, width: 1 } }, ...tooltipStyle(t), formatter: (ps) => {
        const doy = ps[0].value[0];
        const byYear = years.map((y) => [y, c.years[y][doy - 1]]).filter((r) => y_ok(r[0], doy, thisYear, doyToday)).sort((a, b) => b[1] - a[1]);
        return '<div style="margin-bottom:6px;color:var(--ink-2)">Day ' + doy + " (" + esc(doyLabel(doy)) + ") — events started so far</div>" +
          byYear.slice(0, 8).map((r) => row(r[0] === thisYear ? t.s1 : (r[0] === hl ? t.s2 : t.context), String(r[0]), fmtInt(r[1]), r[0] === thisYear)).join("") +
          (byYear.length > 8 ? '<div style="color:var(--muted);margin-top:4px">… ' + (byYear.length - 8) + " more years</div>" : "");
      } },
      series,
    });
  }
  function y_ok(y, doy, thisYear, doyToday) { return y !== thisYear || doy <= doyToday; }
  function dayOfYear(iso) { const d = new Date(iso + "T00:00:00Z"); return Math.floor((d - Date.UTC(d.getUTCFullYear(), 0, 1)) / 86400000) + 1; }
  function doyLabel(doy) { const d = new Date(Date.UTC(2021, 0, doy)); return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }); }

  // ------------------------------------------------------------------ heatmap
  function renderHeatmap() {
    const t = theme(), m = D.monthly;
    const years = [...new Set(m.map((r) => r.year))].sort((a, b) => b - a);
    const data = m.map((r) => [r.month - 1, years.indexOf(r.year), r.new_events]);
    const max = Math.max(1, ...m.map((r) => r.new_events));
    const ch = mk("chart-heatmap");
    ch.setOption({
      animation: false, backgroundColor: "transparent",
      grid: { left: 44, right: 16, top: 34, bottom: 56 },
      xAxis: { type: "category", data: MONTHS, ...baseAxis(t), splitLine: { show: false }, axisLine: { show: false }, position: "top" },
      yAxis: { type: "category", data: years, ...baseAxis(t), splitLine: { show: false }, axisLine: { show: false } },
      visualMap: { min: 0, max, calculable: false, orient: "horizontal", left: "center", bottom: 0, itemHeight: 140, itemWidth: 10, text: [fmtInt(max), "0"], textStyle: { color: t.muted, fontSize: 11 }, inRange: { color: [t.seqLo, t.seqHi] } },
      tooltip: { ...tooltipStyle(t), formatter: (p) => { const r = m.find((x) => x.year === years[p.value[1]] && x.month === p.value[0] + 1); return '<div style="margin-bottom:6px;color:var(--ink-2)">' + MONTHS[p.value[0]] + " " + years[p.value[1]] + "</div>" + row(null, "Events started", fmtInt(r.new_events), true) + row(null, "Event-days", fmtInt(r.event_days)) + row(null, "Detections", fmtInt(r.detections)) + (r.largest_event_detections ? row(null, "Largest event (detections)", fmtInt(r.largest_event_detections)) : ""); } },
      series: [{ type: "heatmap", data, itemStyle: { borderColor: t.surface, borderWidth: 2, borderRadius: 3 }, emphasis: { itemStyle: { borderColor: t.ink, borderWidth: 1 } } }],
    });
  }

  // ------------------------------------------------------------------ annual
  function renderAnnual() {
    const t = theme(), a = D.annual;
    const maxIdx = a.reduce((b, r, i) => (r.new_events > a[b].new_events ? i : b), 0);
    const ch = mk("chart-annual");
    ch.setOption({
      animation: false, backgroundColor: "transparent",
      grid: { left: 48, right: 16, top: 24, bottom: 30 },
      xAxis: { type: "category", data: a.map((r) => r.year), ...baseAxis(t), splitLine: { show: false }, axisLabel: { color: t.muted, fontSize: 12, interval: 0, rotate: a.length > 12 ? 45 : 0 } },
      yAxis: { type: "value", ...baseAxis(t), axisLine: { show: false }, axisLabel: { color: t.muted, fontSize: 12, formatter: (v) => fmtInt(v) } },
      tooltip: { ...tooltipStyle(t), formatter: (p) => { const r = a[p.dataIndex]; return '<div style="margin-bottom:6px;color:var(--ink-2)">' + r.year + (r.complete ? "" : " (year to date)") + "</div>" + row(null, "Events started", fmtInt(r.new_events), true) + row(null, "Event-days", fmtInt(r.event_days)) + row(null, "Detections", fmtInt(r.detections)) + row(null, "Peak day", fmtInt(r.peak_active_events) + " events on " + fmtDateShort(r.peak_day)); } },
      series: [{ type: "bar", data: a.map((r, i) => ({ value: r.new_events, itemStyle: { color: r.complete ? t.s1 : t.s1soft, borderRadius: [4, 4, 0, 0] }, label: { show: i === maxIdx || !r.complete, position: "top", color: t.ink2, fontSize: 11, formatter: (p) => fmtInt(p.value) + (r.complete ? "" : " YTD") } })), barMaxWidth: 24, barCategoryGap: "35%" }],
    });
    table("#table-annual", ["Year", "Events started", "Event-days", "Detections", "Days with activity", "Peak day", "Largest event (detections)", "Longest event (days)"],
      a.map((r) => [r.year + (r.complete ? "" : " (YTD)"), r.new_events, r.event_days, r.detections, r.days_with_activity, fmtDateShort(r.peak_day), r.largest_event_detections || 0, r.longest_event_days || 0]),
      [false, true, true, true, true, false, true, true]);
  }

  // ------------------------------------------------------------------ top events table
  function renderTop() {
    const top = D.meta.top_events;
    table("#table-top", ["Start", "Days", "Detections", "Max FRP (MW)", "Location"],
      top.map((e) => [fmtDate(e.start_date), e.duration_days, e.n_detections, Math.round(e.max_frp), e.centroid_lat.toFixed(2) + ", " + e.centroid_lon.toFixed(2)]),
      [false, true, true, true, false],
      (i) => { const e = top[i]; showEventOnMap(e); });
  }
  function showEventOnMap(e) {
    const period = $("#map-period");
    const y = String(e.start_date).slice(0, 4);
    if ([...period.options].some((o) => o.value === y)) { period.value = y; renderMap(); }
    map.setView([e.centroid_lat, e.centroid_lon], 10);
    document.getElementById("map").scrollIntoView({ behavior: "smooth", block: "center" });
    L.popup().setLatLng([e.centroid_lat, e.centroid_lon]).setContent(eventPopup({ start: e.start_date, end: e.end_date, n: e.n_detections, days: e.n_days_detected, max_frp: e.max_frp, lat: e.centroid_lat, lon: e.centroid_lon })).openOn(map);
  }

  // ------------------------------------------------------------------ map
  function initMap() {
    map = L.map("map", { scrollWheelZoom: false, preferCanvas: true }).setView([54.5, -3.2], 6);
    const dark = matchMedia("(prefers-color-scheme: dark)").matches && document.documentElement.dataset.theme !== "light";
    mapLayers.tiles = L.tileLayer("https://{s}.basemaps.cartocdn.com/" + (dark ? "dark_all" : "light_all") + "/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>', subdomains: "abcd", maxZoom: 18,
    }).addTo(map);
    mapLayers.events = L.layerGroup().addTo(map);
    mapLayers.detections = L.layerGroup();
    mapLayers.persistent = L.layerGroup().addTo(map);
    const sel = $("#map-period");
    const end = D.meta.data_end;
    const opts = [["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last 12 months"]];
    const years = [...new Set(D.events_all.start.map((s) => s.slice(0, 4)))].sort().reverse();
    years.forEach((y) => opts.push([y, y]));
    opts.forEach(([v, l]) => { const o = document.createElement("option"); o.value = v; o.textContent = l; sel.append(o); });
    sel.value = "30";
    sel.addEventListener("change", renderMap);
    $("#map-detections").addEventListener("change", renderMap);
    $("#map-persistent").addEventListener("change", renderMap);
    const t = theme();
    D.persistent_sources.forEach((p) => {
      L.circleMarker([p.lat, p.lon], { radius: 5, color: t.surface, weight: 2, fillColor: t.muted, fillOpacity: 0.9 })
        .bindPopup("<b>" + esc(p.name || "Persistent heat source") + "</b><br>Excluded (" + esc(p.origin) + "), radius " + fmtInt(p.radius_m) + " m")
        .addTo(mapLayers.persistent);
    });
    renderMap();
  }
  function renderMap() {
    const t = theme(), v = $("#map-period").value, end = D.meta.data_end;
    let s, e;
    if (v.length === 4) { s = v + "-01-01"; e = v + "-12-31"; } else { const d = new Date(end + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() - Number(v) + 1); s = d.toISOString().slice(0, 10); e = end; }
    mapLayers.events.clearLayers(); mapLayers.detections.clearLayers();
    const ev = D.events_all; let n = 0, nd = 0;
    const maxN = 200;
    for (let i = 0; i < ev.id.length; i++) {
      if (ev.end[i] < s || ev.start[i] > e) continue;
      n++; nd += ev.n[i];
      const r = 4 + 10 * Math.sqrt(Math.min(ev.n[i], maxN) / maxN);
      L.circleMarker([ev.lat[i], ev.lon[i]], { radius: r, color: t.surface, weight: 2, fillColor: t.s1, fillOpacity: 0.75 })
        .bindPopup(eventPopup({ start: ev.start[i], end: ev.end[i], n: ev.n[i], days: ev.days[i], max_frp: ev.max_frp[i], lat: ev.lat[i], lon: ev.lon[i] }))
        .addTo(mapLayers.events);
    }
    if ($("#map-detections").checked) {
      const dr = D.detections_recent; let shown = 0;
      for (let i = 0; i < dr.date.length; i++) {
        if (dr.date[i] < s || dr.date[i] > e) continue;
        shown++;
        L.circleMarker([dr.lat[i], dr.lon[i]], { radius: 2.5, color: t.s2, weight: 1, fillColor: t.s2, fillOpacity: 0.7, interactive: false }).addTo(mapLayers.detections);
      }
      if (!map.hasLayer(mapLayers.detections)) mapLayers.detections.addTo(map);
      if (shown === 0 && dr.date.length && v.length === 4 && v < D.detections_recent.date[0].slice(0, 4)) $("#map-summary").dataset.note = "Individual detections are only available for the most recent 12 months.";
    } else if (map.hasLayer(mapLayers.detections)) map.removeLayer(mapLayers.detections);
    if ($("#map-persistent").checked) { if (!map.hasLayer(mapLayers.persistent)) mapLayers.persistent.addTo(map); } else if (map.hasLayer(mapLayers.persistent)) map.removeLayer(mapLayers.persistent);
    const label = v.length === 4 ? v : $("#map-period").selectedOptions[0].textContent.toLowerCase();
    $("#map-summary").textContent = fmtInt(n) + " fire events (" + fmtInt(nd) + " detections) " + (v.length === 4 ? "in " : "in the ") + label + ($("#map-detections").checked && v.length === 4 && v < D.detections_recent.date[0].slice(0, 4) ? " · individual detections are only shown for the most recent 12 months" : "") + ".";
  }
  function eventPopup(e) {
    const same = e.start === e.end;
    return "<b>Fire event</b><br>" + esc(same ? fmtDate(e.start) : fmtDate(e.start) + " – " + fmtDate(e.end)) + "<br>" +
      fmtInt(e.n) + " detection" + (e.n === 1 ? "" : "s") + " on " + e.days + " day" + (e.days === 1 ? "" : "s") + "<br>Max FRP " + Math.round(e.max_frp) + " MW<br>" +
      '<span style="color:var(--muted)">' + e.lat.toFixed(3) + ", " + e.lon.toFixed(3) + "</span>";
  }

  // ------------------------------------------------------------------ method tables
  function renderMethod() {
    const m = D.meta;
    document.querySelectorAll("[data-cfg]").forEach((el) => { el.textContent = fmtInt(m.config[el.dataset.cfg]); });
    $("#n-persistent").textContent = "Currently " + fmtInt(m.n_persistent_sources) + " sites are excluded (shown in grey on the map).";
    const f = m.funnel, steps = [["Detections in the UK bounding box", f.bbox], ["On UK land", f.uk_land], ["Not flagged as static/offshore by NASA", f.not_static_type], ["Nominal or high confidence", f.confidence], ["Not near a persistent heat source", f.not_persistent]];
    table("#table-funnel", ["Step", "Detections remaining", "Removed"], steps.map((s, i) => [s[0], s[1], i ? steps[i - 1][1] - s[1] : 0]), [false, true, true]);
    if (m.sensitivity && m.sensitivity.length) {
      const eps = [...new Set(m.sensitivity.map((r) => r.eps_m))].sort((a, b) => a - b), gaps = [...new Set(m.sensitivity.map((r) => r.gap_days))].sort((a, b) => a - b);
      const years = [...new Set(m.sensitivity.map((r) => r.year))].sort();
      const head = ["Year"]; eps.forEach((e) => gaps.forEach((g) => head.push(e + " m / " + g + " d")));
      const rows = years.map((y) => { const r = [String(y)]; eps.forEach((e) => gaps.forEach((g) => { const x = m.sensitivity.find((s) => s.year === y && s.eps_m === e && s.gap_days === g); r.push(x ? x.events : 0); })); return r; });
      const tb = table("#table-sensitivity", head, rows, head.map((_, i) => i > 0));
      const defIdx = 1 + eps.indexOf(m.config.cluster_eps_m) * gaps.length + gaps.indexOf(m.config.cluster_max_gap_days);
      tb.querySelectorAll("tr").forEach((tr) => { const td = tr.children[defIdx]; if (td) td.style.fontWeight = "600"; });
      $("#table-sensitivity").querySelectorAll("th")[defIdx].textContent += " (default)";
    } else {
      $("#table-sensitivity").textContent = "";
    }
  }

  // ------------------------------------------------------------------ wiring
  function renderAll() {
    renderKpis(); renderTimeSeries(); renderCumulative(); renderHeatmap(); renderAnnual(); renderTop(); renderMethod();
  }
  function wire() {
    document.querySelectorAll("button.range").forEach((b) => b.addEventListener("click", () => {
      document.querySelectorAll("button.range").forEach((x) => x.classList.toggle("is-active", x === b));
      currentRangeDays = Number(b.dataset.days);
      const s = rangeStart(currentRangeDays), e = D.daily.dates[D.daily.dates.length - 1];
      charts["chart-events"].dispatchAction({ type: "dataZoom", startValue: s, endValue: e });
    }));
    $("#snpp-only").addEventListener("change", renderTimeSeries);
    window.addEventListener("resize", debounce(() => Object.values(charts).forEach((c) => c.resize()), 150));
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderAll(); map.remove(); initMap(); });
  }

  load().then(() => {
    $("#updated").textContent = "Data to " + fmtDate(D.meta.data_end) + " · updated " + new Date(D.meta.updated_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" }) + " · " + fmtInt(D.meta.n_events) + " fire events since " + fmtDate(D.meta.data_start);
    wire(); renderAll(); initMap();
  }).catch((err) => {
    const p = document.createElement("p"); p.className = "error"; p.textContent = "Could not load data: " + err.message;
    document.querySelector("main").prepend(p);
    console.error(err);
  });
})();
