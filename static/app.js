/* Signal Bot dashboard — realtime WebSocket + AI limit-signal charts.
   Key improvements:
   - localStorage symbol/interval persistence (survives page refresh)
   - 15m + 1h AI predict-limit-signal mini-charts
   - Buy/sell pressure bar, tick-rate counter, candle countdown
   - Data-flash pulses on all live-updating elements
   - Skeleton loading on symbol switch
   - Live provider/model display (Groq / OpenRouter) + fallback badge
   - Animated API pipeline (Market Data → AI Analyst → Risk Gate → Critic → Signal Out)
   - Latency sparkline on AI Engine card
   - Pipeline call log */
(function () {
  "use strict";

  /* ── palette ─────────────────────────────────────────────────────────────── */
  var C = {
    bg: "#131a22", grid: "#1f2a37", text: "#8b98a5",
    green: "#22c55e", red: "#ef4444", amber: "#f59e0b",
    ema7: "#eab308", ema25: "#38bdf8", ema99: "#a3a3a3",
    blue: "#38bdf8", purple: "#a855f7", teal: "#0ea5e9",
  };

  /* ── element refs ─────────────────────────────────────────────────────────── */
  var symbolEl   = document.getElementById("symbol");
  var intervalEl = document.getElementById("interval");
  var liveDot    = document.getElementById("live-dot");
  var latencyEl  = document.getElementById("latency");
  var priceEl    = document.getElementById("price");
  var tapeEl     = document.getElementById("tape");
  var toastsEl   = document.getElementById("toasts");
  var tickRateEl = document.getElementById("tick-rate");
  var clockEl    = document.getElementById("live-clock");

  /* ── localStorage keys ───────────────────────────────────────────────────── */
  var LS_SYM = "sb_symbol";
  var LS_INT = "sb_interval";

  /* ── live clock ──────────────────────────────────────────────────────────── */
  function updateClock() {
    if (clockEl) {
      clockEl.textContent = new Date().toLocaleTimeString("en-US", { hour12: false });
    }
  }
  updateClock();
  setInterval(updateClock, 1000);

  /* ── tick-rate counter ───────────────────────────────────────────────────── */
  var _tickCount = 0;
  setInterval(function () {
    if (tickRateEl) {
      var rate = Math.round(_tickCount * 6); // per 10s → per min
      tickRateEl.textContent = rate + " t/m";
      _tickCount = 0;
    }
  }, 10000);

  /* ── buy/sell pressure ───────────────────────────────────────────────────── */
  var _buyVol = 0, _sellVol = 0;
  var pressureBuy  = document.getElementById("pressure-buy");
  var buyPctEl     = document.getElementById("buy-pct");
  var sellPctEl    = document.getElementById("sell-pct");

  function updatePressure() {
    var total = _buyVol + _sellVol;
    if (!total) return;
    var buyPct = Math.round((_buyVol / total) * 100);
    if (pressureBuy) pressureBuy.style.width = buyPct + "%";
    if (buyPctEl)  buyPctEl.textContent  = buyPct + "%";
    if (sellPctEl) sellPctEl.textContent = (100 - buyPct) + "%";
  }

  // Reset pressure window every 60 s
  setInterval(function () { _buyVol = 0; _sellVol = 0; }, 60000);

  /* ── candle countdown ────────────────────────────────────────────────────── */
  var _intervalMs = 3600000; // default 1h
  var _lastCandleTime = 0;
  var countdownEl = document.getElementById("candle-countdown");

  var INTERVAL_MS = { "1m": 60e3, "3m": 180e3, "5m": 300e3, "15m": 900e3,
                      "30m": 1800e3, "1h": 3600e3, "4h": 14400e3, "1d": 86400e3 };

  function updateCountdown() {
    if (!countdownEl || !_lastCandleTime) return;
    var next = (_lastCandleTime + 1) * 1000 + _intervalMs;
    var rem  = Math.max(0, next - Date.now());
    var m = Math.floor(rem / 60000);
    var s = Math.floor((rem % 60000) / 1000);
    countdownEl.textContent = "next candle " + m + ":" + (s < 10 ? "0" : "") + s;
  }
  setInterval(updateCountdown, 1000);

  /* ── data-flash utility ──────────────────────────────────────────────────── */
  function flash(el, cls) {
    if (!el) return;
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
    setTimeout(function () { el.classList.remove(cls); }, 800);
  }

  /* ── chart factory ───────────────────────────────────────────────────────── */
  function baseOptions(el, extra) {
    return Object.assign({
      width:  el.clientWidth,
      height: el.clientHeight,
      layout: { background: { color: "transparent" }, textColor: C.text,
                fontFamily: "'JetBrains Mono', monospace", fontSize: 10 },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
      rightPriceScale: { borderColor: C.grid },
      timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false },
      crosshair: { mode: 0 },
    }, extra || {});
  }

  /* ── main chart ──────────────────────────────────────────────────────────── */
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
    Object.values(_aiCharts).forEach(function (ctx) {
      var el2 = document.getElementById("ai-chart-" + ctx.interval);
      if (el2) ctx.chart.applyOptions({ width: el2.clientWidth, height: el2.clientHeight });
    });
  });

  var priceLines  = [];
  var trendSeries = [];
  var aiLines     = [];
  var lastAI      = null;
  var lastAIKey   = "";
  var firstLoad   = true;
  var lastCandle  = null;
  var cvdBase     = 0;
  var isLoading   = false;

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
    if (a.entry != null) addAILine(a.entry, col, "AI " + a.signal + " ENTRY · " + a.confidence + "%", 0, 2);
    if (a.stop  != null) addAILine(a.stop,  C.red,   "AI STOP", 2);
    if (a.tp1   != null) addAILine(a.tp1,   C.green, "AI TP1",  2);
    if (a.tp2   != null) addAILine(a.tp2,   C.green, "AI TP2",  2);
  }

  /* ── AI predict limit-signal mini-charts (15m + 1h) ─────────────────────── */
  var _aiCharts = {};   // keyed by "15m" | "1h" → { chart, series, lines, interval }

  function miniChartOptions(el) {
    return baseOptions(el, {
      rightPriceScale: { borderColor: C.grid, scaleMargins: { top: 0.1, bottom: 0.1 } },
      timeScale: { borderColor: C.grid, timeVisible: true, secondsVisible: false,
                   rightOffset: 3, fixLeftEdge: false },
    });
  }

  function getOrCreateAIChart(interval) {
    if (_aiCharts[interval]) return _aiCharts[interval];
    var el = document.getElementById("ai-chart-" + interval);
    if (!el || !el.clientWidth) return null;
    var ch = LightweightCharts.createChart(el, miniChartOptions(el));
    var cs = ch.addCandlestickSeries({
      upColor: C.green, downColor: C.red, borderVisible: false,
      wickUpColor: C.green, wickDownColor: C.red,
    });
    _aiCharts[interval] = { chart: ch, series: cs, lines: [], interval: interval };
    return _aiCharts[interval];
  }

  function renderAIChart(msg) {
    if (!msg || !msg.candles || !msg.ai) return;

    var interval = msg.interval;
    var candles  = msg.candles;
    var ai       = msg.ai;

    // Only render for currently selected symbol
    if (msg.symbol !== symbolEl.value) return;

    // Show the chart card, hide the empty state
    var emptyEl = document.getElementById("ai-chart-empty");
    var rowEl   = document.getElementById("ai-chart-row");
    var badgeEl = document.getElementById("ai-chart-badge");
    if (emptyEl) emptyEl.style.display = "none";
    if (rowEl)   rowEl.style.display = "";

    // Update badge
    if (badgeEl && ai.signal) {
      badgeEl.className = "ai-signal-badge " + (ai.signal === "LONG" ? "long" : "short");
      badgeEl.textContent = ai.signal + " · " + (ai.confidence || "?") + "% confidence";
    }

    // Update label
    var labelEl = document.getElementById("ai-chart-" + interval)
      && document.querySelector("#ai-chart-" + interval + "-col .ai-chart-label");
    if (labelEl) labelEl.textContent = interval + " · " + (ai.setup_type || "Signal");

    var ctx = getOrCreateAIChart(interval);
    if (!ctx) return;

    // Set candle data
    ctx.series.setData(candles.map(function (c) {
      return { time: c.time, open: c.open, high: c.high, low: c.low, close: c.close };
    }));

    // Clear old AI lines
    ctx.lines.forEach(function (l) { ctx.series.removePriceLine(l); });
    ctx.lines = [];

    // Draw AI levels
    var col = ai.signal === "LONG" ? C.green : C.red;
    function mkLine(price, color, title, style, width) {
      if (price == null) return;
      ctx.lines.push(ctx.series.createPriceLine({
        price: price, color: color, lineWidth: width || 1,
        lineStyle: style === undefined ? 2 : style,
        axisLabelVisible: true, title: title,
      }));
    }
    mkLine(ai.entry, col,     "ENTRY",   0, 2);
    mkLine(ai.stop,  C.red,   "STOP",    2, 1);
    mkLine(ai.tp1,   C.green, "TP1",     2, 1);
    mkLine(ai.tp2,   C.green, "TP2",     3, 1);

    ctx.chart.timeScale().fitContent();

    // Flash the card
    flash(document.getElementById("ai-chart-card"), "card-flash");
  }

  /* ── price animation ─────────────────────────────────────────────────────── */
  var shownPrice = null, targetPrice = null, priceDigits = 2;
  function digitsFor(p) { return p >= 1000 ? 2 : p >= 1 ? 4 : 6; }

  function fmt(n, digits) {
    if (n == null) return "—";
    return Number(n).toLocaleString("en-US", {
      minimumFractionDigits: digits === undefined ? 2 : digits,
      maximumFractionDigits: digits === undefined ? 2 : digits,
    });
  }

  function rafLoop() {
    if (targetPrice !== null) {
      if (shownPrice === null) shownPrice = targetPrice;
      var diff = targetPrice - shownPrice;
      if (Math.abs(diff) > Math.abs(targetPrice) * 1e-7) shownPrice += diff * 0.22;
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

  /* ── loading skeleton ────────────────────────────────────────────────────── */
  function showLoading(sym, intv) {
    isLoading = true;
    chartEl.classList.add("chart-loading");

    var v = document.getElementById("verdict");
    v.className = "verdict-badge neutral loading-pulse";
    v.textContent = "Loading " + sym + " · " + intv + "…";

    priceEl.classList.remove("flash-up", "flash-down");
    priceEl.textContent = "—";
    var chg = document.getElementById("chg");
    chg.textContent = ""; chg.className = "chg";
    tapeEl.textContent = ""; tapeEl.className = "tape";

    document.getElementById("gauge-needle").style.left = "50%";
    document.getElementById("gauge-score").textContent = "—";
    if (countdownEl) countdownEl.textContent = "";

    document.getElementById("plan-card").classList.add("hidden");
    document.getElementById("fund-card").classList.add("hidden");

    clearOverlays();
    renderAI(null);
    candleSeries.setData([]);
    volumeSeries.setData([]);
    ema7Series.setData([]);
    ema25Series.setData([]);
    ema99Series.setData([]);
    cvdSeries.setData([]);

    // Skeleton breakdown rows
    var host = document.getElementById("breakdown");
    host.innerHTML = "";
    for (var i = 0; i < 10; i++) {
      var sk = document.createElement("div");
      sk.className = "bd-row bd-skeleton";
      sk.innerHTML =
        '<div class="bd-top">' +
          '<span class="skel skel-name"></span>' +
          '<span class="skel skel-val"></span>' +
        '</div>' +
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

  /* ── tick / kline handling ───────────────────────────────────────────────── */
  function onTick(t) {
    _tickCount++;
    if (t.sell) _sellVol += t.qty; else _buyVol += t.qty;
    updatePressure();

    if (isLoading) return;

    priceDigits = digitsFor(t.price);
    targetPrice = t.price;
    flashPrice(t.price);

    // Format tape with size indicator
    var sizeTag = t.qty > 10 ? " ●" : t.qty > 1 ? " ·" : "";
    tapeEl.textContent = (t.sell ? "▼ " : "▲ ") + fmt(t.qty, 3) + sizeTag + "  @ " + fmt(t.price, priceDigits);
    tapeEl.className   = "tape " + (t.sell ? "down" : "up") + (t.qty > 5 ? " big" : "");

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
    if (lastCandle && c.time > lastCandle.time) {
      cvdBase += lastCandle.delta || 0;
    }
    lastCandle = c;
    _lastCandleTime = c.time;
    _intervalMs = INTERVAL_MS[intervalEl.value] || 3600000;

    candleSeries.update({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
    volumeSeries.update(volBar(c));
    cvdSeries.update({ time: c.time, value: cvdBase + (c.delta || 0) });
    priceDigits = digitsFor(c.close);
    targetPrice = c.close;
    flashPrice(c.close);
  }

  /* ── snapshot rendering ──────────────────────────────────────────────────── */
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
    if (lastCandle) {
      _lastCandleTime = lastCandle.time;
      _intervalMs = INTERVAL_MS[intervalEl.value] || 3600000;
    }
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
      s.setData([{ time: tl.start.time, value: tl.start.price },
                 { time: tl.end.time,   value: tl.end.price }]);
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
      flash(chg, "data-flash");
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
      v.textContent = "NEUTRAL · waiting for confluence";
    }

    // Animate gauge needle
    document.getElementById("gauge-needle").style.left = (50 + d.composite / 2) + "%";
    var scoreEl = document.getElementById("gauge-score");
    var from = shownScore, to = d.composite, t0 = performance.now();
    (function step(now) {
      var k = Math.min((now - t0) / 700, 1);
      var val = from + (to - from) * (1 - Math.pow(1 - k, 3));
      scoreEl.textContent = (val > 0 ? "+" : "") + val.toFixed(1) + "  (threshold ±" + d.threshold + ")";
      if (k < 1) requestAnimationFrame(step); else shownScore = to;
    })(t0);

    var planCard = document.getElementById("plan-card");
    if (d.plan) {
      planCard.classList.remove("hidden");
      document.getElementById("plan-entry").textContent = fmt(d.plan.entry, priceDigits);
      document.getElementById("plan-stop").textContent  = fmt(d.plan.stop,  priceDigits);
      document.getElementById("plan-tp1").textContent   = fmt(d.plan.tp1,   priceDigits);
      document.getElementById("plan-tp2").textContent   = fmt(d.plan.tp2,   priceDigits);
      flash(planCard, "card-flash");
    } else {
      planCard.classList.add("hidden");
    }
  }

  function renderBreakdown(d) {
    var host = document.getElementById("breakdown");
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

      row.querySelector(".bd-name").innerHTML =
        b.label + ' <span class="bd-wt">(w' + b.weight + ")</span>";
      var valEl = row.querySelector(".bd-val");
      valEl.className = "bd-val " + cls;
      valEl.textContent = (b.contribution > 0 ? "+" : "") + b.contribution;

      var fill = row.querySelector(".bd-fill");
      fill.className = "bd-fill " + (b.score >= 0 ? "pos" : "neg");
      // Animate bar width after a brief delay so CSS transition fires
      fill.style.width = "0%";
      (function (f, p) {
        requestAnimationFrame(function () {
          requestAnimationFrame(function () { f.style.width = p + "%"; });
        });
      })(fill, pct);

      row.children[2].textContent = b.reasons.join(" · ");
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
    flash(card, "card-flash");
  }

  function renderSnapshot(d) {
    clearLoading();
    renderChart(d);
    renderVerdict(d);
    renderBreakdown(d);
    renderFundamentals(d);
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     AI ENGINE STATUS WIDGET
     Shows: online dot, provider badge, model name, fallback badge,
            latency value + sparkline, inference rate, total calls
  ══════════════════════════════════════════════════════════════════════════════ */
  var waveBarsEl = document.getElementById("wave-bars");
  var WAVE_COUNT = 28;
  (function buildWave() {
    for (var i = 0; i < WAVE_COUNT; i++) {
      var b = document.createElement("div");
      b.className = "wave-bar";
      waveBarsEl.appendChild(b);
    }
  })();

  var wavePhase  = 0;
  var engineOnline = false;

  function animateWave() {
    wavePhase += engineOnline ? 0.08 : 0.03;
    var bars = waveBarsEl.children;
    for (var i = 0; i < bars.length; i++) {
      var base  = Math.sin(wavePhase + i * 0.45) * 0.38 + 0.5;
      var noise = Math.sin(wavePhase * 2.1 + i * 0.85) * 0.18;
      var h = Math.max(0.05, Math.min(1.0, base + noise));
      bars[i].style.height  = (h * 30) + "px";
      bars[i].style.opacity = engineOnline ? (0.25 + h * 0.75) : 0.1;
    }
    requestAnimationFrame(animateWave);
  }
  animateWave();

  /* Latency sparkline — ring buffer of last 20 readings */
  var _latencyRing = [];
  var _sparkCanvas = document.getElementById("latency-spark");
  var _sparkCtx    = _sparkCanvas ? _sparkCanvas.getContext("2d") : null;

  function pushLatencySample(ms) {
    if (ms == null) return;
    _latencyRing.push(ms);
    if (_latencyRing.length > 20) _latencyRing.shift();
    drawSparkline();
  }

  function drawSparkline() {
    if (!_sparkCtx || _latencyRing.length < 2) return;
    var W = _sparkCanvas.width, H = _sparkCanvas.height;
    _sparkCtx.clearRect(0, 0, W, H);
    var min = Math.min.apply(null, _latencyRing);
    var max = Math.max.apply(null, _latencyRing);
    var range = max - min || 1;
    var step = W / (_latencyRing.length - 1);

    _sparkCtx.beginPath();
    _latencyRing.forEach(function (v, i) {
      var x = i * step;
      var y = H - ((v - min) / range) * (H - 2) - 1;
      if (i === 0) _sparkCtx.moveTo(x, y); else _sparkCtx.lineTo(x, y);
    });

    var last = _latencyRing[_latencyRing.length - 1];
    var gradient = _sparkCtx.createLinearGradient(0, 0, W, 0);
    var latColor = last < 1000 ? "#22c55e" : last < 3000 ? "#f59e0b" : "#ef4444";
    gradient.addColorStop(0, "rgba(56,189,248,0.3)");
    gradient.addColorStop(1, latColor);

    _sparkCtx.strokeStyle = gradient;
    _sparkCtx.lineWidth = 1.5;
    _sparkCtx.lineJoin = "round";
    _sparkCtx.stroke();

    // Dot at latest value
    var lx = (_latencyRing.length - 1) * step;
    var ly = H - ((last - min) / range) * (H - 2) - 1;
    _sparkCtx.beginPath();
    _sparkCtx.arc(lx, ly, 2, 0, Math.PI * 2);
    _sparkCtx.fillStyle = latColor;
    _sparkCtx.fill();
  }

  /* Previous provider/model — detect changes for animation */
  var _prevProvider = null;

  function renderEngineStatus(s) {
    if (!s) return;
    engineOnline = s.online;

    var dot       = document.getElementById("es-dot");
    var label     = document.getElementById("es-status-label");
    var version   = document.getElementById("es-version");
    var latency   = document.getElementById("es-latency");
    var inference = document.getElementById("es-inference");
    var totalEl   = document.getElementById("es-total");
    var provBadge = document.getElementById("es-provider-badge");
    var modelName = document.getElementById("es-model-name");
    var fallback  = document.getElementById("es-fallback-badge");

    dot.className       = s.online ? "es-dot online" : "es-dot";
    label.textContent   = s.online ? "Online" : "Offline";
    version.textContent = s.version || "v4.3";

    /* ── Provider + model name ── */
    var prov = s.provider || null;  // "groq" | "openrouter" | null
    if (prov !== _prevProvider) {
      _prevProvider = prov;
      // Animate the model row on provider switch
      if (provBadge) {
        provBadge.classList.remove("provider-switch-anim");
        void provBadge.offsetWidth;
        provBadge.classList.add("provider-switch-anim");
      }
    }

    if (provBadge) {
      if (prov === "groq") {
        provBadge.className    = "es-provider-badge groq";
        provBadge.textContent  = "GROQ";
      } else if (prov === "openrouter") {
        provBadge.className    = "es-provider-badge openrouter";
        provBadge.textContent  = "OPENROUTER";
      } else {
        provBadge.className    = "es-provider-badge muted";
        provBadge.textContent  = "—";
      }
    }

    if (modelName) {
      var mn = s.current_model || "";
      // Strip ":free" suffix for display
      var displayName = mn.replace(/:free$/, "").replace(/^meta-llama\//, "").replace(/^google\//, "");
      modelName.textContent = displayName || "—";
      modelName.title = mn; // full name on hover
    }

    /* ── Fallback badge ── */
    if (fallback) {
      if (s.groq_rate_limited) {
        fallback.classList.remove("hidden");
        var cd = s.groq_cooldown_remaining || 0;
        fallback.textContent = "FALLBACK" + (cd > 0 ? " (" + cd + "s)" : "");
      } else {
        fallback.classList.add("hidden");
      }
    }

    /* ── Latency ── */
    if (s.latency_ms != null) {
      latency.textContent = s.latency_ms + "ms";
      latency.className = "es-metric-value " +
        (s.latency_ms < 1000 ? "green" : s.latency_ms < 3000 ? "amber" : "red");
      pushLatencySample(s.latency_ms);
    } else {
      latency.textContent = "—";
      latency.className   = "es-metric-value";
    }

    /* ── Inference rate ── */
    var rate = s.inference_per_min || 0;
    if (inference) inference.textContent = rate + "/min";

    /* ── Total calls ── */
    if (totalEl) {
      var tot = s.total_inferences || 0;
      totalEl.textContent = tot.toLocaleString();
      // Trigger mid-pipeline animation when new inference detected
      if (tot > _lastInferenceCount) {
        _lastInferenceCount = tot;
        triggerPipelineInFlight(s.current_model, s.provider);
      }
    }

    flash(document.getElementById("engine-status-card"), "card-flash-subtle");
  }

  /* ═══════════════════════════════════════════════════════════════════════════
     API PIPELINE ANIMATION  (4 stages — Risk Gate removed)
     Stage 0: Market Data  →  Stage 1: AI Analyst  →
     Stage 2: Critic Review  →  Stage 3: Signal Out
     Animated with timed transitions and particle flow on connectors.
     Detail panel below shows real AI data: model, confidence, reasoning, critic.
  ══════════════════════════════════════════════════════════════════════════════ */

  var _lastInferenceCount = 0;
  var _pipelineActive     = false;
  var _pipelineTimers     = [];
  var _pipeCallStart      = 0;

  /* Default sub-labels per stage while idle */
  var STAGE_SUBS_IDLE = ["idle", "idle", "idle", "—"];

  function _clearPipelineTimers() {
    _pipelineTimers.forEach(clearTimeout);
    _pipelineTimers = [];
  }

  /** Set a pipeline stage's visual state and update its sub-label */
  function _setPipeStage(idx, state, subText) {
    var stageEl = document.getElementById("pipe-stage-" + idx);
    var subEl   = document.getElementById("pipe-sub-" + idx);
    if (!stageEl) return;
    stageEl.className = "pipe-stage " + state;
    if (subEl && subText !== undefined) subEl.textContent = subText;
  }

  /** Animate the particle on connector idx with an optional color class */
  function _flowConnector(idx, colorCls, dur) {
    var conn     = document.getElementById("pipe-conn-" + idx);
    var particle = document.getElementById("pipe-particle-" + idx);
    if (!conn || !particle) return;
    conn.className = "pipe-connector flowing";
    particle.style.setProperty("--flow-dur", (dur || 0.65) + "s");
    particle.className = "pipe-particle";
    void particle.offsetWidth;
    particle.className = "pipe-particle flowing" + (colorCls ? " " + colorCls : "");
    setTimeout(function () {
      if (conn.classList.contains("flowing")) conn.className = "pipe-connector flowing";
    }, (dur || 0.65) * 1000);
  }

  /** Mark a connector as done/long/short */
  function _doneConnector(idx, state) {
    var conn = document.getElementById("pipe-conn-" + idx);
    if (conn) conn.className = "pipe-connector " + (state || "done");
  }

  /** Reset all pipeline stages + connectors to idle */
  function _resetPipeline() {
    for (var i = 0; i <= 3; i++) _setPipeStage(i, "idle", STAGE_SUBS_IDLE[i]);
    for (var j = 0; j <= 2; j++) {
      var conn = document.getElementById("pipe-conn-" + j);
      if (conn) conn.className = "pipe-connector";
    }
  }

  /** Update the AI Analyst detail panel with real data from the AI result */
  function _updateAnalystDetail(ai) {
    var model = (ai.model_used || ai.model || "—").replace(/:free$/, "");
    var signal = ai.signal || "WAIT";
    var conf   = ai.confidence || 0;
    var setup  = ai.setup_type || "—";
    var reasoning = (ai.reasoning || ai.orderflow_read || "—").slice(0, 220);
    var prov   = ai.provider || (model.indexOf("/") >= 0 ? "openrouter" : "groq");

    var modelEl = document.getElementById("pd-model");
    var sigEl   = document.getElementById("pd-signal");
    var confEl  = document.getElementById("pd-confidence");
    var setupEl = document.getElementById("pd-setup");
    var reasEl  = document.getElementById("pd-reasoning");

    if (modelEl) {
      modelEl.textContent = model || "—";
      modelEl.className = "pipe-detail-val prov-" + prov;
    }
    if (sigEl) {
      sigEl.textContent = signal;
      sigEl.className   = "pipe-detail-val " +
        (signal === "LONG" ? "sig-long" : signal === "SHORT" ? "sig-short" : "sig-wait");
    }
    if (confEl) confEl.textContent = conf ? conf + "%" : "—";
    if (setupEl) setupEl.textContent = setup || "—";
    if (reasEl)  reasEl.textContent  = reasoning;
  }

  /** Update the Critic detail panel */
  function _updateCriticDetail(critic, signal, gateReason) {
    var verdictEl  = document.getElementById("pd-critic-verdict");
    var iconEl     = document.getElementById("pd-critic-icon");
    var labelEl    = document.getElementById("pd-critic-label");
    var critiqueEl = document.getElementById("pd-critique");
    var critiqueRow = document.getElementById("pd-critique-row");
    var concernsEl = document.getElementById("pd-concerns");

    if (!verdictEl) return;

    if (signal !== "LONG" && signal !== "SHORT") {
      // WAIT from the AI itself — critic was never called
      verdictEl.className = "pipe-critic-verdict skipped";
      if (iconEl)  iconEl.textContent  = "—";
      if (labelEl) labelEl.textContent = gateReason
        ? "AI returned WAIT — " + gateReason.slice(0, 80)
        : "AI returned WAIT (no trade setup)";
      if (critiqueRow) critiqueRow.style.display = "none";
      if (concernsEl)  concernsEl.innerHTML = "";
      return;
    }

    if (!critic) {
      // Critic disabled or failed — signal passes through
      verdictEl.className = "pipe-critic-verdict approved";
      if (iconEl)  iconEl.textContent  = "✓";
      if (labelEl) labelEl.textContent = "APPROVED (critic not called)";
      if (critiqueRow) critiqueRow.style.display = "none";
      if (concernsEl)  concernsEl.innerHTML = "";
      return;
    }

    var approved = critic.approve !== false;
    verdictEl.className = "pipe-critic-verdict " + (approved ? "approved" : "rejected");
    if (iconEl)  iconEl.textContent  = approved ? "✓" : "✗";
    if (labelEl) labelEl.textContent = approved ? "APPROVED by critic" : "REJECTED by critic";

    var critique = critic.critique || "";
    if (critiqueEl) critiqueEl.textContent = critique || (approved ? "No concerns." : "Rejected.");
    if (critiqueRow) critiqueRow.style.display = critique ? "" : "none";

    // Concerns list
    if (concernsEl) {
      concernsEl.innerHTML = "";
      (critic.concerns || []).slice(0, 4).forEach(function (c) {
        var d = document.createElement("div");
        d.className = "pipe-concern-item";
        d.textContent = c;
        concernsEl.appendChild(d);
      });
    }
  }

  /**
   * Kick off mid-flight animation — called when engine_status shows a new inference.
   * At this point we don't have the result yet; animate stage 0→1 active.
   */
  function triggerPipelineInFlight(model, provider) {
    if (_pipelineActive) return;
    _pipelineActive = true;
    _pipeCallStart  = Date.now();
    _clearPipelineTimers();

    // Stage 0: Market Data done immediately (data already fetched)
    _setPipeStage(0, "done", "fetched");
    _flowConnector(0, "", 0.5);

    _pipelineTimers.push(setTimeout(function () {
      _doneConnector(0, "done");
      var shortModel = (model || "").replace(/:free$/, "").split("/").pop() || "model";
      _setPipeStage(1, "active", shortModel + "…");
    }, 560));
  }

  /**
   * Complete the pipeline animation when the full AI result arrives.
   * ai: full AI result dict from WebSocket 'ai' message
   */
  function completePipeline(ai) {
    _pipelineActive = false;
    _clearPipelineTimers();

    var signal    = ai.signal || "WAIT";
    var model     = ai.model_used || ai.model || null;
    var provider  = ai.provider || null;
    var latencyMs = ai.latency_ms || (Date.now() - _pipeCallStart);
    var critic    = ai.critic || null;
    var gateReason = ai.gate_reason || null;
    var conf      = ai.confidence || 0;

    var sigClass  = signal === "LONG" ? "done-long" : signal === "SHORT" ? "done-short" : "wait";
    var connClass = signal === "LONG" ? "long" : signal === "SHORT" ? "short" : "done";
    var shortModel = (model || "").replace(/:free$/, "").split("/").pop() || "model";

    // Ensure stage 0 is done
    if (!document.getElementById("pipe-stage-0").classList.contains("done")) {
      _setPipeStage(0, "done", "fetched");
    }
    _doneConnector(0, connClass);

    // Stage 1: AI Analyst — complete with confidence
    var analystSub = shortModel + (conf ? " · " + conf + "%" : "");
    _setPipeStage(1, "done", analystSub);
    _updateAnalystDetail(ai);
    _flowConnector(1, connClass, 0.45);

    _pipelineTimers.push(setTimeout(function () {
      _doneConnector(1, connClass);

      // Stage 2: Critic Review
      _setPipeStage(2, "active", "reviewing…");
      _flowConnector(2, connClass, 0.5);

      _pipelineTimers.push(setTimeout(function () {
        _doneConnector(2, connClass);

        // Determine critic verdict for display
        var criticApproved = !critic || critic.approve !== false;
        var criticState    = signal === "WAIT" ? "wait"
          : criticApproved ? (signal === "LONG" ? "done-long" : "done-short")
          : "wait";  // critic rejected → overridden to WAIT

        var criticSub;
        if (signal !== "LONG" && signal !== "SHORT") {
          criticSub = "skipped (WAIT)";
        } else if (!critic) {
          criticSub = "approved";
        } else if (criticApproved) {
          criticSub = "✓ approved";
        } else {
          criticSub = "✗ rejected";
        }
        _setPipeStage(2, criticState, criticSub);
        _updateCriticDetail(critic, signal, gateReason);

        // Stage 3: Signal Out
        var outSub;
        if (signal === "LONG")       outSub = "▲ LONG";
        else if (signal === "SHORT") outSub = "▼ SHORT";
        else if (gateReason)         outSub = "WAIT · " + gateReason.slice(0, 40);
        else                         outSub = "WAIT";

        _pipelineTimers.push(setTimeout(function () {
          _setPipeStage(3, sigClass, outSub);
        }, 350));

      }, 520));
    }, 500));

    // Meta row
    var lastCallEl = document.getElementById("pipe-last-call");
    var durationEl = document.getElementById("pipe-duration");
    if (lastCallEl) lastCallEl.textContent = "Last call: just now";
    if (durationEl) durationEl.textContent = latencyMs + "ms";

    addPipelineLogEntry(signal, model, provider, latencyMs, conf, gateReason, critic);

    // Reset idle after 30 s
    _pipelineTimers.push(setTimeout(function () {
      _resetPipeline();
    }, 30000));
  }

  /* Keep updating "X ago" on the last-call label */
  var _lastCallTime = 0;
  setInterval(function () {
    if (!_lastCallTime) return;
    var el = document.getElementById("pipe-last-call");
    if (!el) return;
    var diff = Math.round((Date.now() - _lastCallTime) / 1000);
    if (diff < 5)  el.textContent = "Last call: just now";
    else if (diff < 60)  el.textContent = "Last call: " + diff + "s ago";
    else el.textContent = "Last call: " + Math.floor(diff / 60) + "m ago";
  }, 5000);

  /* Pipeline call log (max 30 entries) */
  var _logEntries = 0;

  function addPipelineLogEntry(signal, model, provider, latencyMs, conf, gateReason, critic) {
    _lastCallTime = Date.now();
    var logEl = document.getElementById("pipe-log");
    if (!logEl) return;

    var empty = logEl.querySelector(".pipe-log-empty");
    if (empty) empty.remove();

    var row = document.createElement("div");
    row.className = "pipe-log-row new-entry";

    var now      = new Date().toLocaleTimeString("en-US", { hour12: false });
    var provCls  = provider === "groq" ? "groq" : provider === "openrouter" ? "openrouter" : "unknown";
    var sigCls   = signal === "LONG" ? "long" : signal === "SHORT" ? "short" : "wait";
    var sigLabel = signal || "WAIT";

    // Critic verdict suffix
    var criticTag = "";
    if (signal === "LONG" || signal === "SHORT") {
      if (critic) {
        criticTag = critic.approve !== false
          ? '<span class="pipe-log-critic-ok">✓</span>'
          : '<span class="pipe-log-critic-no">✗</span>';
      }
    }

    // Confidence badge
    var confTag = conf ? '<span class="pipe-log-conf">' + conf + '%</span>' : "";

    row.innerHTML =
      '<span class="pipe-log-time">' + now + '</span>' +
      '<span class="pipe-log-model ' + provCls + '">' + (provider || "?").toUpperCase() + '</span>' +
      '<span class="pipe-log-signal ' + sigCls + '">' + sigLabel + '</span>' +
      criticTag + confTag +
      '<span class="pipe-log-sym">' + (symbolEl ? symbolEl.value : "") + '</span>' +
      '<span class="pipe-log-lat">' + (latencyMs || "—") + 'ms</span>';

    logEl.insertBefore(row, logEl.firstChild);
    setTimeout(function () { row.classList.remove("new-entry"); }, 1000);

    _logEntries++;
    while (logEl.children.length > 30) logEl.removeChild(logEl.lastChild);
  }

  // Init pipeline idle state
  _resetPipeline();

  /* ── Binance status ──────────────────────────────────────────────────────── */
  function fetchBinanceStatus() {
    fetch("/api/binance-key-status")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var configured = d.api_key_configured && d.api_secret_configured;
        document.getElementById("bnb-key").textContent    = d.api_key_configured    ? "Configured" : "Not set";
        document.getElementById("bnb-key").className      = "es-metric-value " + (d.api_key_configured    ? "green" : "red");
        document.getElementById("bnb-secret").textContent = d.api_secret_configured ? "Configured" : "Not set";
        document.getElementById("bnb-secret").className   = "es-metric-value " + (d.api_secret_configured ? "green" : "red");
        document.getElementById("bnb-orders").textContent = configured ? "Enabled"  : "Read-only";
        document.getElementById("bnb-orders").className   = "es-metric-value " + (configured ? "green" : "");
        document.getElementById("bnb-mode").textContent   = configured ? "Authenticated" : "Public";
        document.getElementById("bnb-mode").className     = "es-version " + (configured ? "green" : "");
        document.getElementById("bnb-hint").style.display = configured ? "none" : "";
      })
      .catch(function () {});
  }
  fetchBinanceStatus();

  /* ── Recent AI Signals table ─────────────────────────────────────────────── */
  var allSignalRows = [];
  var sigFilterMode = "all";
  var filterBtnEl   = document.getElementById("sig-filter-btn");
  var sigCountEl    = document.getElementById("sig-count");
  var relTimeEls    = [];

  if (filterBtnEl) {
    filterBtnEl.addEventListener("click", function () {
      sigFilterMode = sigFilterMode === "all" ? "symbol" : "all";
      filterBtnEl.textContent = sigFilterMode === "all" ? "All symbols" : symbolEl.value + " only";
      filterBtnEl.classList.toggle("active", sigFilterMode === "symbol");
      redrawSigTable();
    });
  }

  function updateFilterLabel() {
    if (!filterBtnEl) return;
    filterBtnEl.textContent = sigFilterMode === "all" ? "All symbols" : symbolEl.value + " only";
  }

  function relTime(ts) {
    var diff = Math.floor(Date.now() / 1000) - ts;
    if (diff < 5)    return "just now";
    if (diff < 60)   return diff + "s ago";
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

  setInterval(function () {
    relTimeEls.forEach(function (obj) { obj.el.textContent = relTime(obj.ts); });
  }, 15000);

  function redrawSigTable() {
    var rows = allSignalRows;
    if (sigFilterMode === "symbol") {
      var sym = symbolEl.value;
      rows = rows.filter(function (r) { return r.symbol === sym; });
    }

    var tbody = document.getElementById("ai-sig-tbody");
    tbody.innerHTML = "";
    relTimeEls = [];

    if (!rows.length) {
      var empty = document.createElement("tr");
      empty.className = "ai-sig-empty";
      var msg = sigFilterMode === "symbol"
        ? "No AI signals for " + (symbolEl.value || "this symbol") + " yet"
        : "No AI signals yet — watching for high-quality setups…";
      empty.innerHTML = "<td colspan='5'>" + msg + "</td>";
      tbody.appendChild(empty);
      sigCountEl.textContent = "";
      return;
    }

    sigCountEl.textContent = rows.length + " signal" + (rows.length !== 1 ? "s" : "");

    rows.forEach(function (row, idx) {
      var isLong  = row.direction === "LONG";
      var conf    = row.confidence || 0;
      var confCls = conf >= 75 ? "conf-high" : conf >= 55 ? "conf-mid" : "conf-low";
      var dirArrow = isLong ? "↑" : "↓";
      var dirCls   = isLong ? "dir-long" : "dir-short";
      var symBase  = (row.symbol || "").replace("USDT", "").toLowerCase();
      var symCls   = "sym-badge sym-" + symBase;

      var tr = document.createElement("tr");
      tr.setAttribute("data-symbol", row.symbol || "");
      if (idx === 0) tr.classList.add("ai-sig-new");
      tr.style.cursor = "pointer";

      // Time cell
      var timeEl = document.createElement("td");
      timeEl.className = "time-cell";
      timeEl.textContent = relTime(row.time);
      timeEl.title = absTime(row.time);
      relTimeEls.push({ el: timeEl, ts: row.time });

      // Symbol badge
      var symTd  = document.createElement("td");
      var badge  = document.createElement("span");
      badge.className = symCls;
      badge.textContent = row.symbol || "—";
      symTd.appendChild(badge);

      // Setup
      var setupTd = document.createElement("td");
      setupTd.className = "setup-cell";
      setupTd.textContent = row.setup_type || "—";
      setupTd.title = row.setup_type || "";

      // Direction
      var dirTd = document.createElement("td");
      dirTd.className = dirCls;
      dirTd.innerHTML = '<span class="dir-arrow">' + dirArrow + "</span> " + row.direction;

      // Confidence with bar
      var confTd = document.createElement("td");
      confTd.innerHTML =
        '<div class="conf-cell">' +
          '<div class="conf-bar-track">' +
            '<div class="conf-bar-fill ' + confCls + '" style="width:' + Math.max(4, conf) + '%"></div>' +
          '</div>' +
          '<span class="conf-num ' + confCls + '">' + conf + '%</span>' +
        '</div>';

      tr.appendChild(timeEl);
      tr.appendChild(symTd);
      tr.appendChild(setupTd);
      tr.appendChild(dirTd);
      tr.appendChild(confTd);
      tbody.appendChild(tr);

      tr.addEventListener("click", function () {
        var s = this.getAttribute("data-symbol");
        if (s && s !== symbolEl.value) {
          symbolEl.value = s;
          localStorage.setItem(LS_SYM, s);
          reset();
        }
      });
    });
  }

  function renderAISignalsTable(rows) {
    if (!rows) return;
    allSignalRows = rows;
    redrawSigTable();
  }

  /* ── signal cards (detail feed) ──────────────────────────────────────────── */
  function signalCard(s) {
    var el = document.createElement("div");
    el.className = "sig " + s.direction.toLowerCase();
    var when = new Date(s.time * 1000).toLocaleString();
    el.innerHTML =
      '<div class="sig-top">' +
        '<span class="sig-dir">' + s.direction + (s.strength ? " · " + s.strength : "") + "</span>" +
        '<span class="sig-meta">' + s.symbol + " " + s.interval + " · " + s.score + "% confidence</span>" +
      '</div>' +
      '<div class="sig-meta">' + when + " · @ " + fmt(s.price, digitsFor(s.price)) + "</div>" +
      (s.reasons && s.reasons.length
        ? '<div class="sig-reasons">' + s.reasons.filter(Boolean).slice(0, 2).join(" · ") + "</div>"
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
      '<span class="toast-dir">' + s.direction + (s.strength ? " · " + s.strength : "") + "</span>" +
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

    // Complete the pipeline animation with the full result object
    if (a && !a.error) {
      completePipeline(a);
    } else {
      // Error path — reset pipeline
      _pipelineActive = false;
      _clearPipelineTimers();
      _resetPipeline();
    }

    if (!a || a.error || a.signal === "WAIT") return;
    var key = a.symbol + ":" + a.signal + ":" + a.entry;
    if (key === lastAIKey) return;
    lastAIKey = key;
    pushSignal({
      direction: a.signal,
      strength:  "AI · " + a.confidence + "%",
      symbol:    a.symbol,
      interval:  a.interval,
      score:     a.confidence,
      time:      a.updated,
      price:     a.entry != null ? a.entry : a.price,
      reasons:   [a.setup_type, a.orderflow_read].filter(Boolean),
    });
  }

  /* ── selectors with localStorage restore ────────────────────────────────── */
  function fillSelect(el, values, serverDefault) {
    if (el.childElementCount) return;
    var lsKey = el === symbolEl ? LS_SYM : LS_INT;
    var saved = localStorage.getItem(lsKey);
    // Only use saved value if it's still a valid option
    var pick = (saved && values.indexOf(saved) >= 0) ? saved : serverDefault;
    values.forEach(function (v) {
      var o = document.createElement("option");
      o.value = v; o.textContent = v;
      if (v === pick) o.selected = true;
      el.appendChild(o);
    });
    // If restored to non-default, subscribe after both selects are ready
    if (pick !== serverDefault) {
      setTimeout(function () {
        if (symbolEl.childElementCount && intervalEl.childElementCount) subscribe();
      }, 80);
    }
  }

  /* ── WebSocket ───────────────────────────────────────────────────────────── */
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
          fillSelect(symbolEl,   m.symbols,   m.default_symbol);
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
        case "ai_chart":
          renderAIChart(m);
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

  /* ── reset on market change ──────────────────────────────────────────────── */
  function reset() {
    var sym  = symbolEl.value;
    var intv = intervalEl.value;

    // Persist selection immediately
    localStorage.setItem(LS_SYM, sym);
    localStorage.setItem(LS_INT, intv);

    firstLoad      = true;
    lastCandle     = null;
    lastAI         = null;
    lastAIKey      = "";
    lastFlashPrice = null;
    shownPrice     = null;
    targetPrice    = null;
    shownScore     = 0;
    _buyVol        = 0;
    _sellVol       = 0;
    _lastCandleTime = 0;

    // Reset AI chart section
    var emptyEl = document.getElementById("ai-chart-empty");
    var rowEl   = document.getElementById("ai-chart-row");
    var badgeEl = document.getElementById("ai-chart-badge");
    if (emptyEl) emptyEl.style.display = "";
    if (rowEl)   rowEl.style.display = "none";
    if (badgeEl) { badgeEl.textContent = ""; badgeEl.className = "ai-signal-badge hidden"; }
    // Destroy old mini charts so they reinit for the new symbol
    Object.values(_aiCharts).forEach(function (ctx) { ctx.chart.remove(); });
    _aiCharts = {};

    showLoading(sym, intv);
    updateFilterLabel();
    if (sigFilterMode === "symbol") redrawSigTable();

    subscribe();
  }

  symbolEl.addEventListener("change",  function () { localStorage.setItem(LS_SYM, symbolEl.value);   reset(); });
  intervalEl.addEventListener("change", function () { localStorage.setItem(LS_INT, intervalEl.value); reset(); });

  connect();
})();
