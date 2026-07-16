/* Signal Bot dashboard — WebSocket edition.
   Improvements:
   - Symbol/interval change shows immediate loading state, clears all stale data
   - AI Engine Status widget with live waveform
   - Recent AI Signals table with relative time, confidence bars, symbol filter, clickable rows */
(function () {
  "use strict";

  var C = {
    bg: "#131a22", grid: "#1f2a37", text: "#8b98a5",
    green: "#22c55e", red: "#ef4444", amber: "#f59e0b",
    ema7: "#eab308", ema25: "#38bdf8", ema99: "#a3a3a3",
  };

  var symbolEl  = document.getElementById("symbol");
  var intervalEl = document.getElementById("interval");
  var liveDot   = document.getElementById("live-dot");
  var latencyEl = document.getElementById("latency");
  var priceEl   = document.getElementById("price");
  var tapeEl    = document.getElementById("tape");
  var toastsEl  = document.getElementById("toasts");

  // ── charts ──────────────────────────────────────────────────────────────────
  function baseOptions(el) {
    return {
      width: el.clientWidth,
      height: el.clientHeight,
      layout: { background: { color: "transparent" }, textColor: C.text,
                fontFamily: "'JetBrains Mono', monospace", fontSize: 10 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.grid },
      timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    };
  }

  var chartEl = document.getElementById("chart");
  var chart   = LightweightCharts.createChart(chartEl, baseOptions(chartEl));

  var candleSeries = chart.addCandlestickSeries({
    upColor: C.green, downColor: C.red, borderVisible: false,
    wickUpColor: C.green, wickDownColor: C.red,
  });
  var volumeSeries = chart.addHistogramSeries({
    priceFormat: { type: "volume" }, priceScaleId: "vol",
    priceLineVisible: false, lastValueVisible: false,
  });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  var ema7Series  = chart.addLineSeries({ color: C.ema7,  lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  var ema25Series = chart.addLineSeries({ color: C.ema25, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
  var ema99Series = chart.addLineSeries({ color: C.ema99, lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

  var cvdEl   = document.getElementById("cvd-chart");
  var cvdChart = LightweightCharts.createChart(cvdEl, baseOptions(cvdEl));
  var cvdSeries = cvdChart.addAreaSeries({
    lineColor: C.ema25, topColor: "rgba(56,189,248,0.25)", bottomColor: "rgba(56,189,248,0.02)",
    lineWidth: 2, priceLineVisible: false,
  });

  window.addEventListener("resize", function () {
    chart.applyOptions({ width: chartEl.clientWidth, height: chartEl.clientHeight });
    cvdChart.applyOptions({ width: cvdEl.clientWidth, height: cvdEl.clientHeight });
  });

  var priceLines  = [];
  var trendSeries = [];
  var aiLines     = [];
  var lastAI      = null;
  var lastAIKey   = "";
  var firstLoad   = true;
  var lastCandle  = null;
  var cvdBase     = 0;
  var isLoading   = false;   // true while waiting for snapshot after market switch

  function volBar(c) {
    return {
      time: c.time, value: c.volume,
      color: c.close >= c.open ? "rgba(34,197,94,0.35)" : "rgba(239,68,68,0.35)",
    };
  }

  function clearOverlays() {
    priceLines.forEach(function (pl) { candleSeries.removePriceLine(pl); });
    priceLines = [];
    trendSeries.forEach(function (s) { chart.removeSeries(s); });
    trendSeries = [];
  }

  function addPriceLine(price, color, title, style) {
    priceLines.push(candleSeries.createPriceLine({
      price: price, color: color, lineWidth: 1,
      lineStyle: style === undefined ? 2 : style,
      axisLabelVisible: true, title: title,
    }));
  }

  function addAILine(price, color, title, style, width) {
    aiLines.push(candleSeries.createPriceLine({
      price: price, color: color, lineWidth: width || 1,
      lineStyle: style === undefined ? 2 : style,
      axisLabelVisible: true, title: title,
    }));
  }

  function renderAI(a) {
    aiLines.forEach(function (pl) { candleSeries.removePriceLine(pl); });
    aiLines = [];
    if (!a || a.error || a.symbol !== symbolEl.value) return;
    if (a.signal !== "LONG" && a.signal !== "SHORT") return;
    var col = a.signal === "LONG" ? C.green : C.red;
    if (a.entry != null) addAILine(a.entry, col, "AI " + a.signal + " ENTRY \u00b7 " + a.confidence + "%", 0, 2);
    if (a.stop  != null) addAILine(a.stop,  C.red,   "AI STOP", 2);
    if (a.tp1   != null) addAILine(a.tp1,   C.green, "AI TP1",  2);
    if (a.tp2   != null) addAILine(a.tp2,   C.green, "AI TP2",  2);
  }

  // ── price animation ─────────────────────────────────────────────────────────
  var shownPrice = null, targetPrice = null, priceDigits = 2;
  function digitsFor(p) { return p >= 1000 ? 2 : p >= 1 ? 4 : 6; }

  function fmt(n, digits) {
    if (n === null || n === undefined) return "\u2014";
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits,
    });
  }

  function rafLoop() {
    if (targetPrice !== null) {
      if (shownPrice === null) shownPrice = targetPrice;
      var diff = targetPrice - shownPrice;
      if (Math.abs(diff) > Math.abs(targetPrice) * 1e-7) shownPrice += diff * 0.25;
      else shownPrice = targetPrice;
      priceEl.textContent = fmt(shownPrice, priceDigits);
    }
    requestAnimationFrame(rafLoop);
  }
  requestAnimationFrame(rafLoop);

  var lastFlashPrice = null;
  function flashPrice(p) {
    if (lastFlashPrice !== null && p !== lastFlashPrice) {
      var cls = p > lastFlashPrice ? "flash-up" : "flash-down";
      priceEl.classList.remove("flash-up", "flash-down");
      void priceEl.offsetWidth;
      priceEl.classList.add(cls);
    }
    lastFlashPrice = p;
  }

  // ── loading state (shown immediately on symbol/interval change) ──────────────
  function showLoading(sym, intv) {
    isLoading = true;
    chartEl.classList.add("chart-loading");

    // Verdict badge → loading
    var v = document.getElementById("verdict");
    v.className = "verdict-badge neutral loading-pulse";
    v.textContent = "Loading " + sym + " \u00b7 " + intv + "\u2026";

    // Clear price / chg / tape
    priceEl.classList.remove("flash-up", "flash-down");
    priceEl.textContent = "\u2014";
    var chg = document.getElementById("chg");
    chg.textContent = ""; chg.className = "chg";
    tapeEl.textContent = ""; tapeEl.className = "tape";

    // Reset gauge
    document.getElementById("gauge-needle").style.left = "50%";
    document.getElementById("gauge-score").textContent = "fetching data\u2026";

    // Hide plan + fundamentals
    document.getElementById("plan-card").classList.add("hidden");
    document.getElementById("fund-card").classList.add("hidden");

    // Clear chart data + overlays + AI lines
    clearOverlays();
    renderAI(null);
    candleSeries.setData([]);
    volumeSeries.setData([]);
    ema7Series.setData([]);
    ema25Series.setData([]);
    ema99Series.setData([]);
    cvdSeries.setData([]);

    // Breakdown → skeleton rows
    var host = document.getElementById("breakdown");
    host.innerHTML = "";
    for (var i = 0; i < 10; i++) {
      var sk = document.createElement("div");
      sk.className = "bd-row bd-skeleton";
      sk.innerHTML = '<div class="bd-top"><span class="skel skel-name"></span><span class="skel skel-val"></span></div>' +
                     '<div class="bd-bar"><div class="bd-mid"></div><div class="skel skel-bar"></div></div>' +
                     '<div class="skel skel-why"></div>';
      host.appendChild(sk);
    }
  }

  function clearLoading() {
    isLoading = false;
    chartEl.classList.remove("chart-loading");
    var v = document.getElementById("verdict");
    v.classList.remove("loading-pulse");
  }

  // ── tick / kline ─────────────────────────────────────────────────────────────
  function onTick(t) {
    if (isLoading) return;   // discard stale ticks for previous market
    priceDigits = digitsFor(t.price);
    targetPrice = t.price;
    flashPrice(t.price);
    tapeEl.textContent = (t.sell ? "\u25bc " : "\u25b2 ") + fmt(t.qty, 4) + " @ " + fmt(t.price, priceDigits);
    tapeEl.className = "tape " + (t.sell ? "down" : "up");
    if (lastCandle && t.time / 1000 >= lastCandle.time) {
      lastCandle.close = t.price;
      if (t.price > lastCandle.high) lastCandle.high = t.price;
      if (t.price < lastCandle.low)  lastCandle.low  = t.price;
      candleSeries.update({ time: lastCandle.time, open: lastCandle.open,
                            high: lastCandle.high, low: lastCandle.low, close: lastCandle.close });
      lastCandle.volume = (lastCandle.volume || 0) + t.qty;
      lastCandle.delta  = (lastCandle.delta  || 0) + (t.sell ? -t.qty : t.qty);
      volumeSeries.update(volBar(lastCandle));
      cvdSeries.update({ time: lastCandle.time, value: cvdBase + lastCandle.delta });
    }
  }

  function onKline(m) {
    var c = m.candle;
    if (lastCandle && c.time > lastCandle.time) cvdBase += lastCandle.delta || 0;
    lastCandle = c;
    candleSeries.update({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
    volumeSeries.update(volBar(c));
    cvdSeries.update({ time: c.time, value: cvdBase + (c.delta || 0) });
    priceDigits = digitsFor(c.close);
    targetPrice = c.close;
    flashPrice(c.close);
  }

  // ── snapshot rendering ───────────────────────────────────────────────────────
  function renderChart(d) {
    var ov = d.overlays || {};
    candleSeries.setData(d.candles.map(function (c) {
      return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close };
    }));
    volumeSeries.setData(d.candles.map(volBar));
    ema7Series.setData(ov.ema7 || []);
    ema25Series.setData(ov.ema25 || []);
    ema99Series.setData(ov.ema99 || []);
    cvdSeries.setData(ov.cvd || []);

    lastCandle = d.candles.length ? Object.assign({}, d.candles[d.candles.length - 1]) : null;
    var cvd = ov.cvd || [];
    cvdBase = cvd.length >= 2 ? cvd[cvd.length - 2].value : 0;

    clearOverlays();
    (ov.support    || []).forEach(function (lv) { addPriceLine(lv.price, C.green, "S x" + lv.touches); });
    (ov.resistance || []).forEach(function (lv) { addPriceLine(lv.price, C.red,   "R x" + lv.touches); });

    if (ov.fibonacci)
      ov.fibonacci.levels.forEach(function (lv) { addPriceLine(lv.price, C.amber, "fib " + lv.ratio, 3); });

    if (ov.volume_profile) {
      addPriceLine(ov.volume_profile.poc, "#c084fc", "POC", 0);
      addPriceLine(ov.volume_profile.vah, "#8b98a5", "VAH", 3);
      addPriceLine(ov.volume_profile.val, "#8b98a5", "VAL", 3);
    }
    (ov.order_blocks || []).forEach(function (ob) {
      var col = ob.type === "bullish" ? C.green : C.red;
      addPriceLine(ob.top,    col, "OB " + (ob.type === "bullish" ? "demand" : "supply"), 4);
      addPriceLine(ob.bottom, col, "", 4);
    });
    (ov.fvgs || []).forEach(function (f) {
      addPriceLine(f.mid, f.type === "bullish" ? C.green : C.red, "FVG", 1);
    });
    (ov.trendlines || []).forEach(function (tl) {
      var s = chart.addLineSeries({
        color: tl.type === "support" ? C.green : C.red,
        lineWidth: 1, lineStyle: 0, priceLineVisible: false,
        lastValueVisible: false, crosshairMarkerVisible: false,
      });
      s.setData([{ time: tl.start.time, value: tl.start.price }, { time: tl.end.time, value: tl.end.price }]);
      trendSeries.push(s);
    });

    var markers = [];
    (ov.sweeps || []).forEach(function (sw) {
      markers.push({
        time: sw.time,
        position: sw.type === "bullish" ? "belowBar" : "aboveBar",
        color: sw.type === "bullish" ? C.green : C.red,
        shape: sw.type === "bullish" ? "arrowUp" : "arrowDown",
        text: "SWEEP",
      });
    });
    markers.sort(function (a, b) { return a.time - b.time; });
    candleSeries.setMarkers(markers);

    renderAI(lastAI);

    if (firstLoad) {
      chart.timeScale().fitContent();
      cvdChart.timeScale().fitContent();
      firstLoad = false;
    }
  }

  var shownScore = 0;
  function renderVerdict(d) {
    priceDigits = digitsFor(d.price);
    if (targetPrice === null) targetPrice = d.price;

    var chg = document.getElementById("chg");
    if (d.ticker) {
      var pct = d.ticker.change_pct;
      chg.textContent = (pct >= 0 ? "+" : "") + pct.toFixed(2) + "% 24h";
      chg.className = "chg " + (pct >= 0 ? "up" : "down");
    }

    var v = document.getElementById("verdict");
    if (d.direction === "LONG") {
      v.className = "verdict-badge long";
      v.textContent = (d.strength ? d.strength + " " : "") + "LONG SIGNAL";
    } else if (d.direction === "SHORT") {
      v.className = "verdict-badge short";
      v.textContent = (d.strength ? d.strength + " " : "") + "SHORT SIGNAL";
    } else {
      v.className = "verdict-badge neutral";
      v.textContent = "NEUTRAL \u00b7 waiting for confluence";
    }

    document.getElementById("gauge-needle").style.left = (50 + d.composite / 2) + "%";
    var scoreEl = document.getElementById("gauge-score");
    var from = shownScore, to = d.composite, t0 = performance.now();
    (function step(now) {
      var k = Math.min((now - t0) / 600, 1);
      var val = from + (to - from) * (1 - Math.pow(1 - k, 3));
      scoreEl.textContent = "score " + val.toFixed(1) + " / \u00b1" + d.threshold + " to fire";
      if (k < 1) requestAnimationFrame(step); else shownScore = to;
    })(t0);

    var planCard = document.getElementById("plan-card");
    if (d.plan) {
      planCard.classList.remove("hidden");
      document.getElementById("plan-entry").textContent = fmt(d.plan.entry, priceDigits);
      document.getElementById("plan-stop").textContent  = fmt(d.plan.stop,  priceDigits);
      document.getElementById("plan-tp1").textContent   = fmt(d.plan.tp1,   priceDigits);
      document.getElementById("plan-tp2").textContent   = fmt(d.plan.tp2,   priceDigits);
    } else {
      planCard.classList.add("hidden");
    }
  }

  function renderBreakdown(d) {
    var host = document.getElementById("breakdown");
    // If we have skeleton rows, replace them all
    var hasSkeleton = host.querySelector(".bd-skeleton");
    if (hasSkeleton || host.childElementCount !== d.breakdown.length) host.innerHTML = "";
    var build = host.childElementCount !== d.breakdown.length;

    d.breakdown.forEach(function (b, i) {
      var pct = Math.min(Math.abs(b.score) * 50, 50);
      var cls = b.contribution > 0.5 ? "pos" : b.contribution < -0.5 ? "neg" : "zero";
      var row;
      if (build || !host.children[i]) {
        row = document.createElement("div");
        row.className = "bd-row";
        row.innerHTML =
          '<div class="bd-top"><span class="bd-name"></span><span class="bd-val"></span></div>' +
          '<div class="bd-bar"><div class="bd-mid"></div><div class="bd-fill"></div></div>' +
          '<div class="bd-why"></div>';
        host.appendChild(row);
      } else {
        row = host.children[i];
      }
      row.querySelector(".bd-name").innerHTML = b.label + ' <span class="bd-wt">(w' + b.weight + ")</span>";
      var valEl = row.querySelector(".bd-val");
      valEl.className = "bd-val " + cls;
      valEl.textContent = (b.contribution > 0 ? "+" : "") + b.contribution;
      var fill = row.querySelector(".bd-fill");
      fill.className = "bd-fill " + (b.score >= 0 ? "pos" : "neg");
      fill.style.width = pct + "%";
      row.children[2].textContent = b.reasons.join(" \u00b7 ");
    });
  }

  function renderFundamentals(d) {
    var f = (d.overlays || {}).fundamentals;
    var card = document.getElementById("fund-card");
    if (!f) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    var fr = f.funding_rate * 100;
    var frEl = document.getElementById("f-funding");
    frEl.textContent = fr.toFixed(4) + "%";
    frEl.className = "v " + (fr > 0.03 ? "red" : fr < 0 ? "green" : "");
    document.getElementById("f-oi").textContent   = (f.oi_change_pct >= 0 ? "+" : "") + f.oi_change_pct.toFixed(1) + "%";
    document.getElementById("f-ls").textContent   = f.long_short_ratio.toFixed(2);
    document.getElementById("f-mark").textContent = fmt(f.mark_price, priceDigits);
  }

  function renderSnapshot(d) {
    clearLoading();
    renderChart(d);
    renderVerdict(d);
    renderBreakdown(d);
    renderFundamentals(d);
  }

  // ── AI Engine Status widget ──────────────────────────────────────────────────
  var waveBarsEl = document.getElementById("wave-bars");
  var WAVE_COUNT = 28;
  (function buildWave() {
    for (var i = 0; i < WAVE_COUNT; i++) {
      var b = document.createElement("div");
      b.className = "wave-bar";
      waveBarsEl.appendChild(b);
    }
  })();

  var wavePhase = 0;
  var engineOnline = false;

  function animateWave() {
    wavePhase += 0.07;
    var bars = waveBarsEl.children;
    for (var i = 0; i < bars.length; i++) {
      var base  = Math.sin(wavePhase + i * 0.45) * 0.38 + 0.5;
      var noise = Math.sin(wavePhase * 2.1 + i * 0.85) * 0.18;
      var h = Math.max(0.05, Math.min(1.0, base + noise));
      bars[i].style.height  = (h * 30) + "px";
      bars[i].style.opacity = engineOnline ? (0.25 + h * 0.75) : 0.12;
    }
    requestAnimationFrame(animateWave);
  }
  animateWave();

  function renderEngineStatus(s) {
    if (!s) return;
    engineOnline = s.online;
    var dot      = document.getElementById("es-dot");
    var label    = document.getElementById("es-status-label");
    var version  = document.getElementById("es-version");
    var models   = document.getElementById("es-models");
    var latency  = document.getElementById("es-latency");
    var inference = document.getElementById("es-inference");

    dot.className   = s.online ? "es-dot online" : "es-dot";
    label.textContent = s.online ? "Online" : "Offline";
    version.textContent = s.version || "v4.2";
    models.textContent  = s.active_models ? s.active_models + " active" : "\u2014";

    if (s.latency_ms != null) {
      latency.textContent = s.latency_ms + "ms";
      latency.className = "es-metric-value " +
        (s.latency_ms < 1000 ? "green" : s.latency_ms < 3000 ? "amber" : "red");
    } else {
      latency.textContent = "\u2014";
      latency.className = "es-metric-value";
    }

    var rate = s.inference_per_min || 0;
    inference.textContent = rate >= 1000 ? (rate / 1000).toFixed(1) + "k/min" : rate + "/min";
  }

  // ── Binance status ───────────────────────────────────────────────────────────
  function fetchBinanceStatus() {
    fetch("/api/binance-key-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var configured = d.api_key_configured && d.api_secret_configured;
        var keyEl    = document.getElementById("bnb-key");
        var secretEl = document.getElementById("bnb-secret");
        var ordersEl = document.getElementById("bnb-orders");
        var modeEl   = document.getElementById("bnb-mode");
        var hintEl   = document.getElementById("bnb-hint");

        keyEl.textContent    = d.api_key_configured    ? "Configured" : "Not set";
        keyEl.className      = "es-metric-value " + (d.api_key_configured    ? "green" : "red");
        secretEl.textContent = d.api_secret_configured ? "Configured" : "Not set";
        secretEl.className   = "es-metric-value " + (d.api_secret_configured ? "green" : "red");
        ordersEl.textContent = configured ? "Enabled" : "Read-only";
        ordersEl.className   = "es-metric-value " + (configured ? "green" : "");
        modeEl.textContent   = configured ? "Authenticated" : "Public";
        modeEl.className     = "es-version "  + (configured ? "green" : "");
        hintEl.style.display = configured ? "none" : "";
      })
      .catch(function () {});
  }
  fetchBinanceStatus();

  // ── Recent AI Signals table ──────────────────────────────────────────────────
  var allSignalRows  = [];   // full dataset from server
  var sigFilterMode  = "all"; // "all" | "symbol"
  var filterBtnEl    = document.getElementById("sig-filter-btn");
  var sigCountEl     = document.getElementById("sig-count");

  if (filterBtnEl) {
    filterBtnEl.addEventListener("click", function () {
      sigFilterMode = sigFilterMode === "all" ? "symbol" : "all";
      filterBtnEl.textContent = sigFilterMode === "all" ? "All symbols" : symbolEl.value + " only";
      filterBtnEl.classList.toggle("active", sigFilterMode === "symbol");
      redrawSigTable();
    });
  }

  // Update filter button label when symbol changes
  function updateFilterLabel() {
    if (!filterBtnEl) return;
    filterBtnEl.textContent = sigFilterMode === "all" ? "All symbols" : symbolEl.value + " only";
  }

  function relTime(ts) {
    var diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 5)   return "just now";
    if (diff < 60)  return diff + "s ago";
    if (diff < 3600) return Math.floor(diff / 60) + "m ago";
    if (diff < 86400) return Math.floor(diff / 3600) + "h ago";
    return Math.floor(diff / 86400) + "d ago";
  }

  function absTime(ts) {
    return new Date(ts * 1000).toLocaleString("en-US", {
      month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  }

  // Periodically refresh relative timestamps without re-rendering everything
  var relTimeEls = [];
  setInterval(function () {
    relTimeEls.forEach(function (obj) {
      obj.el.textContent = relTime(obj.ts);
    });
  }, 15000);

  function redrawSigTable() {
    var rows = allSignalRows;
    if (sigFilterMode === "symbol") {
      var sym = symbolEl.value;
      rows = rows.filter(function (r) { return r.symbol === sym; });
    }

    var tbody   = document.getElementById("ai-sig-tbody");
    tbody.innerHTML = "";
    relTimeEls = [];

    if (!rows.length) {
      var empty = document.createElement("tr");
      empty.className = "ai-sig-empty";
      var msg = sigFilterMode === "symbol"
        ? "No AI signals for " + (symbolEl.value || "this symbol") + " yet"
        : "No AI signals yet \u2014 watching for high-quality setups\u2026";
      empty.innerHTML = "<td colspan='5'>" + msg + "</td>";
      tbody.appendChild(empty);
      sigCountEl.textContent = "";
      return;
    }

    sigCountEl.textContent = rows.length + " signal" + (rows.length !== 1 ? "s" : "");

    rows.forEach(function (row, idx) {
      var isLong   = row.direction === "LONG";
      var conf     = row.confidence || 0;
      var confPct  = conf + "%";
      var confBarW = Math.max(4, conf) + "%";
      var confCls  = conf >= 75 ? "conf-high" : conf >= 55 ? "conf-mid" : "conf-low";
      var dirArrow = isLong ? "\u2191" : "\u2193";
      var dirCls   = isLong ? "dir-long" : "dir-short";
      var symCls   = "sym-badge sym-" + (row.symbol || "").replace("USDT", "").toLowerCase();

      var tr = document.createElement("tr");
      tr.setAttribute("data-symbol", row.symbol || "");
      if (idx === 0 && !rows._seeded) tr.classList.add("ai-sig-new");

      // Time cell — relative text, absolute tooltip
      var timeEl = document.createElement("td");
      timeEl.className = "time-cell";
      timeEl.textContent = relTime(row.time);
      timeEl.title = absTime(row.time);
      relTimeEls.push({ el: timeEl, ts: row.time });

      // Symbol badge
      var symTd = document.createElement("td");
      var badge  = document.createElement("span");
      badge.className = symCls;
      badge.textContent = row.symbol || "\u2014";
      symTd.appendChild(badge);

      // Setup type
      var setupTd = document.createElement("td");
      setupTd.className = "setup-cell";
      setupTd.textContent = row.setup_type || "\u2014";
      setupTd.title = row.setup_type || "";

      // Direction
      var dirTd = document.createElement("td");
      dirTd.className = dirCls;
      dirTd.innerHTML = '<span class="dir-arrow">' + dirArrow + "</span> " + row.direction;

      // Confidence with mini bar
      var confTd = document.createElement("td");
      confTd.innerHTML =
        '<div class="conf-cell">' +
          '<div class="conf-bar-track"><div class="conf-bar-fill ' + confCls + '" style="width:' + confBarW + '"></div></div>' +
          '<span class="conf-num ' + confCls + '">' + confPct + '</span>' +
        '</div>';

      tr.appendChild(timeEl);
      tr.appendChild(symTd);
      tr.appendChild(setupTd);
      tr.appendChild(dirTd);
      tr.appendChild(confTd);
      tbody.appendChild(tr);

      // Click row → switch to that symbol
      tr.style.cursor = "pointer";
      tr.addEventListener("click", function () {
        var sym = this.getAttribute("data-symbol");
        if (sym && sym !== symbolEl.value) {
          symbolEl.value = sym;
          symbolEl.dispatchEvent(new Event("change"));
        }
      });
    });
  }

  function renderAISignalsTable(rows) {
    if (!rows) return;
    allSignalRows = rows;
    redrawSigTable();
  }

  // ── signal cards (detail feed) ───────────────────────────────────────────────
  function signalCard(s) {
    var el = document.createElement("div");
    el.className = "sig " + s.direction.toLowerCase();
    var when = new Date(s.time * 1000).toLocaleString();
    el.innerHTML =
      '<div class="sig-top">' +
        '<span class="sig-dir">' + s.direction + (s.strength ? " \u00b7 " + s.strength : "") + "</span>" +
        '<span class="sig-meta">' + s.symbol + " " + s.interval + " \u00b7 " + s.score + "% confidence</span>" +
      '</div>' +
      '<div class="sig-meta">' + when + " \u00b7 @ " + fmt(s.price, digitsFor(s.price)) + "</div>" +
      (s.reasons && s.reasons.length
        ? '<div class="sig-reasons">' + s.reasons.filter(Boolean).slice(0, 2).join(" \u00b7 ") + "</div>"
        : "");
    return el;
  }

  function renderSignals(list) {
    var host = document.getElementById("signals");
    if (!list.length) return;
    host.innerHTML = "";
    list.forEach(function (s) { host.appendChild(signalCard(s)); });
  }

  function pushSignal(s) {
    var host  = document.getElementById("signals");
    var empty = host.querySelector(".empty");
    if (empty) empty.remove();
    var el = signalCard(s);
    el.classList.add("sig-new");
    host.insertBefore(el, host.firstChild);
    toast(s);
  }

  function toast(s) {
    var t = document.createElement("div");
    t.className = "toast " + s.direction.toLowerCase();
    t.innerHTML =
      '<span class="toast-dir">' + s.direction + (s.strength ? " \u00b7 " + s.strength : "") + "</span>" +
      '<span class="toast-meta">' + s.symbol + " " + s.interval + " @ " + fmt(s.price, digitsFor(s.price)) + "</span>";
    toastsEl.appendChild(t);
    setTimeout(function () {
      t.classList.add("toast-out");
      setTimeout(function () { t.remove(); }, 400);
    }, 6000);
  }

  function onAI(a) {
    lastAI = a;
    renderAI(a);
    if (!a || a.error || a.signal === "WAIT") return;
    var key = a.symbol + ":" + a.signal + ":" + a.entry;
    if (key === lastAIKey) return;
    lastAIKey = key;
    pushSignal({
      direction: a.signal,
      strength:  "AI \u00b7 " + a.confidence + "%",
      symbol:    a.symbol,
      interval:  a.interval,
      score:     a.confidence,
      time:      a.updated,
      price:     a.entry != null ? a.entry : a.price,
      reasons:   [a.setup_type, a.orderflow_read].filter(Boolean),
    });
  }

  // ── selectors ────────────────────────────────────────────────────────────────
  function fillSelect(el, values, selected) {
    if (el.childElementCount) return;
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      if (v === selected) o.selected = true;
      el.appendChild(o);
    });
  }

  // ── WebSocket ────────────────────────────────────────────────────────────────
  var ws = null, reconnectDelay = 1000, pingTimer = null;

  function subscribe() {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "subscribe", symbol: symbolEl.value, interval: intervalEl.value }));
    }
  }

  function connect() {
    var proto = location.protocol === "https:" ? "wss:" : "ws:";
    liveDot.className = "dot";
    ws = new WebSocket(proto + "//" + location.host + "/ws");

    ws.onopen = function () {
      reconnectDelay = 1000;
      liveDot.className = "dot live";
      if (symbolEl.value) subscribe();
      clearInterval(pingTimer);
      pingTimer = setInterval(function () {
        if (ws.readyState === WebSocket.OPEN)
          ws.send(JSON.stringify({ type: "ping", t: performance.now() }));
      }, 5000);
    };

    ws.onmessage = function (ev) {
      var m;
      try { m = JSON.parse(ev.data); } catch (e) { return; }
      switch (m.type) {
        case "config":
          fillSelect(symbolEl, m.symbols, m.default_symbol);
          fillSelect(intervalEl, m.intervals, m.default_interval);
          break;
        case "snapshot":
          if (m.data.symbol === symbolEl.value && m.data.interval === intervalEl.value)
            renderSnapshot(m.data);
          break;
        case "tick":
          if (m.symbol === symbolEl.value) onTick(m);
          break;
        case "kline":
          if (m.symbol === symbolEl.value && m.interval === intervalEl.value) onKline(m);
          break;
        case "signal":
          pushSignal(m.data);
          break;
        case "signals":
          renderSignals(m.data);
          break;
        case "ai":
          onAI(m.data);
          break;
        case "engine_status":
          renderEngineStatus(m.data);
          break;
        case "ai_signals_table":
          renderAISignalsTable(m.data);
          break;
        case "pong":
          latencyEl.textContent = Math.max(1, Math.round(performance.now() - m.t)) + "ms";
          break;
        case "error":
          liveDot.className = "dot err";
          break;
      }
    };

    ws.onclose = function () {
      liveDot.className = "dot err";
      clearInterval(pingTimer);
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, 15000);
    };
    ws.onerror = function () { ws.close(); };
  }

  // ── reset on market change ───────────────────────────────────────────────────
  function reset() {
    var sym  = symbolEl.value;
    var intv = intervalEl.value;

    // State
    firstLoad      = true;
    lastCandle     = null;
    lastAI         = null;   // clear old AI → lines get removed in showLoading → renderAI(null)
    lastAIKey      = "";
    lastFlashPrice = null;
    shownPrice     = null;
    targetPrice    = null;
    shownScore     = 0;

    // Immediate visual feedback
    showLoading(sym, intv);
    updateFilterLabel();

    // Update table filter if "symbol" mode
    if (sigFilterMode === "symbol") redrawSigTable();

    // Ask server for the new market
    subscribe();
  }

  symbolEl.addEventListener("change",  reset);
  intervalEl.addEventListener("change", reset);

  connect();
})();
