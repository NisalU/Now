/**
 * Strategy Builder — create and manage custom trading strategies.
 * Self-contained module: injects its own button + panel into the DOM.
 */
(function () {
  "use strict";

  /* ── constants ─────────────────────────────────────────────────────────── */
  const API = {
    strategies: "/api/strategy-builder/strategies",
    config:     "/api/strategy-builder/config",
    test:       "/api/strategy-builder/test",
  };

  const CONDITION_TYPES = [
    { value: "price_above_ema",    label: "Price above EMA",         params: [{ key: "period", label: "EMA Period", type: "select", options: [7, 25, 99] }] },
    { value: "price_below_ema",    label: "Price below EMA",         params: [{ key: "period", label: "EMA Period", type: "select", options: [7, 25, 99] }] },
    { value: "ema_cross_above",    label: "EMA cross UP",            params: [{ key: "fast", label: "Fast EMA", type: "select", options: [7, 25] }, { key: "slow", label: "Slow EMA", type: "select", options: [25, 99] }] },
    { value: "ema_cross_below",    label: "EMA cross DOWN",          params: [{ key: "fast", label: "Fast EMA", type: "select", options: [7, 25] }, { key: "slow", label: "Slow EMA", type: "select", options: [25, 99] }] },
    { value: "rsi_above",          label: "RSI above threshold",     params: [{ key: "threshold", label: "Threshold", type: "number", min: 1, max: 99, def: 60 }, { key: "period", label: "Period", type: "number", min: 2, max: 50, def: 14 }] },
    { value: "rsi_below",          label: "RSI below threshold",     params: [{ key: "threshold", label: "Threshold", type: "number", min: 1, max: 99, def: 40 }, { key: "period", label: "Period", type: "number", min: 2, max: 50, def: 14 }] },
    { value: "volume_spike",       label: "Volume spike",            params: [{ key: "multiplier", label: "× Avg Volume", type: "number", min: 1.1, max: 10, def: 1.5, step: 0.1 }] },
    { value: "candle_bullish",     label: "Bullish candle body",     params: [{ key: "body_pct", label: "Min body ratio (0–1)", type: "number", min: 0.1, max: 0.99, def: 0.5, step: 0.05 }] },
    { value: "candle_bearish",     label: "Bearish candle body",     params: [{ key: "body_pct", label: "Min body ratio (0–1)", type: "number", min: 0.1, max: 0.99, def: 0.5, step: 0.05 }] },
    { value: "delta_positive",     label: "Delta net positive",      params: [{ key: "n_candles", label: "Candles", type: "number", min: 1, max: 20, def: 3 }] },
    { value: "delta_negative",     label: "Delta net negative",      params: [{ key: "n_candles", label: "Candles", type: "number", min: 1, max: 20, def: 3 }] },
    { value: "price_change_above", label: "Price % change above",    params: [{ key: "pct", label: "% Change", type: "number", min: 0.1, max: 20, def: 1, step: 0.1 }, { key: "n_candles", label: "Candles", type: "number", min: 1, max: 50, def: 5 }] },
    { value: "price_change_below", label: "Price % change below",    params: [{ key: "pct", label: "% Change", type: "number", min: 0.1, max: 20, def: 1, step: 0.1 }, { key: "n_candles", label: "Candles", type: "number", min: 1, max: 50, def: 5 }] },
    { value: "atr_expansion",      label: "ATR expansion (vol spike)", params: [{ key: "multiplier", label: "× Avg ATR", type: "number", min: 1.1, max: 5, def: 1.3, step: 0.1 }] },
    { value: "cvd_rising",         label: "CVD rising (buyers in)",  params: [{ key: "n_candles", label: "Candles", type: "number", min: 3, max: 20, def: 5 }] },
    { value: "cvd_falling",        label: "CVD falling (sellers in)", params: [{ key: "n_candles", label: "Candles", type: "number", min: 3, max: 20, def: 5 }] },
    { value: "near_support",       label: "Price near support",      params: [{ key: "atr_mult", label: "ATR distance", type: "number", min: 0.1, max: 3, def: 0.5, step: 0.1 }] },
    { value: "near_resistance",    label: "Price near resistance",   params: [{ key: "atr_mult", label: "ATR distance", type: "number", min: 0.1, max: 3, def: 0.5, step: 0.1 }] },
  ];

  /* ── state ──────────────────────────────────────────────────────────────── */
  var state = {
    panel:       "closed",   // "closed" | "list" | "create" | "edit" | "config"
    strategies:  [],
    builtinConfig: null,
    editing:     null,       // strategy id being edited
    form: {
      name: "", description: "", weight: 8,
      signal_direction: "bullish", logic: "AND",
      conditions: [],
    },
    testResult:  null,
    saving:      false,
    loading:     false,
  };

  /* ── CSS ────────────────────────────────────────────────────────────────── */
  var css = `
    #sb-btn {
      position: fixed; bottom: 80px; right: 18px; z-index: 9000;
      background: #1a1f2e; border: 1px solid #2d3348; color: #c5c9d4;
      padding: 10px 16px; border-radius: 8px; cursor: pointer;
      font-size: 13px; font-family: inherit; letter-spacing: .03em;
      display: flex; align-items: center; gap: 8px;
      box-shadow: 0 4px 20px rgba(0,0,0,.45);
      transition: border-color .15s, color .15s;
    }
    #sb-btn:hover { border-color: #5b8af5; color: #fff; }
    #sb-btn svg   { width:16px; height:16px; flex-shrink:0; }

    #sb-panel {
      position: fixed; top: 0; right: 0; bottom: 0;
      width: 480px; max-width: 100vw;
      background: #10131c; border-left: 1px solid #1e2437;
      z-index: 9100; display: flex; flex-direction: column;
      transform: translateX(100%); transition: transform .22s cubic-bezier(.4,0,.2,1);
      font-family: inherit; color: #c5c9d4;
    }
    #sb-panel.open { transform: translateX(0); }

    .sb-header {
      padding: 18px 20px 14px;
      border-bottom: 1px solid #1e2437;
      display: flex; align-items: center; justify-content: space-between;
      flex-shrink: 0;
    }
    .sb-title { font-size: 15px; font-weight: 600; color: #e8eaf0; letter-spacing:.02em; }
    .sb-close {
      background: none; border: none; color: #6b7280; cursor: pointer;
      font-size: 20px; line-height: 1; padding: 0 4px;
    }
    .sb-close:hover { color: #e8eaf0; }

    .sb-tabs {
      display: flex; gap: 2px; padding: 10px 16px 0;
      border-bottom: 1px solid #1e2437; flex-shrink: 0;
    }
    .sb-tab {
      padding: 8px 14px; font-size: 12px; font-weight: 500;
      border: none; background: none; color: #6b7280; cursor: pointer;
      border-bottom: 2px solid transparent; margin-bottom: -1px;
      transition: color .12s, border-color .12s;
    }
    .sb-tab.active   { color: #5b8af5; border-bottom-color: #5b8af5; }
    .sb-tab:hover:not(.active) { color: #c5c9d4; }

    .sb-body {
      flex: 1; overflow-y: auto; padding: 16px 20px 24px;
    }
    .sb-body::-webkit-scrollbar { width: 4px; }
    .sb-body::-webkit-scrollbar-track { background: transparent; }
    .sb-body::-webkit-scrollbar-thumb { background: #2d3348; border-radius: 4px; }

    /* Form fields */
    .sb-field { margin-bottom: 14px; }
    .sb-label { display: block; font-size: 11px; color: #6b7280; margin-bottom: 5px; letter-spacing:.04em; text-transform: uppercase; }
    .sb-input, .sb-select, .sb-textarea {
      width: 100%; background: #1a1f2e; border: 1px solid #2d3348;
      color: #e8eaf0; padding: 8px 10px; border-radius: 6px;
      font-size: 13px; font-family: inherit; box-sizing: border-box;
      transition: border-color .12s;
    }
    .sb-input:focus, .sb-select:focus, .sb-textarea:focus {
      outline: none; border-color: #5b8af5;
    }
    .sb-textarea { resize: vertical; min-height: 58px; }
    .sb-select option { background: #1a1f2e; }

    .sb-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .sb-row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }

    /* Buttons */
    .sb-btn-primary {
      background: #3b5bdb; color: #fff; border: none; border-radius: 6px;
      padding: 9px 18px; font-size: 13px; font-weight: 600; cursor: pointer;
      font-family: inherit; transition: background .12s;
    }
    .sb-btn-primary:hover  { background: #4c6ef5; }
    .sb-btn-primary:disabled { opacity: .5; cursor: not-allowed; }
    .sb-btn-secondary {
      background: transparent; color: #8b95a8; border: 1px solid #2d3348;
      border-radius: 6px; padding: 8px 14px; font-size: 13px; cursor: pointer;
      font-family: inherit; transition: border-color .12s, color .12s;
    }
    .sb-btn-secondary:hover { border-color: #5b8af5; color: #e8eaf0; }
    .sb-btn-danger {
      background: transparent; color: #e05252; border: 1px solid #3d2020;
      border-radius: 6px; padding: 6px 12px; font-size: 12px; cursor: pointer;
      font-family: inherit;
    }
    .sb-btn-danger:hover { background: #2a1515; }
    .sb-btn-ghost {
      background: none; border: none; color: #6b7280; cursor: pointer;
      font-size: 12px; padding: 4px 8px; border-radius: 4px;
    }
    .sb-btn-ghost:hover { color: #c5c9d4; background: #1a1f2e; }

    /* Strategy cards */
    .sb-strategy-card {
      background: #141824; border: 1px solid #1e2437; border-radius: 8px;
      padding: 14px 16px; margin-bottom: 10px;
      transition: border-color .12s;
    }
    .sb-strategy-card:hover { border-color: #2d3348; }
    .sb-card-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .sb-card-name   { font-size: 13px; font-weight: 600; color: #e8eaf0; flex: 1; }
    .sb-card-badge  {
      font-size: 10px; padding: 2px 7px; border-radius: 3px; font-weight: 600;
      letter-spacing: .04em;
    }
    .sb-badge-bull  { background: #1a3320; color: #4ade80; }
    .sb-badge-bear  { background: #2d1515; color: #f87171; }
    .sb-badge-custom { background: #1a1f3a; color: #818cf8; }
    .sb-card-desc   { font-size: 12px; color: #6b7280; margin-bottom: 10px; }
    .sb-card-conds  { font-size: 11px; color: #4b5563; margin-bottom: 10px; }
    .sb-card-actions { display: flex; gap: 8px; align-items: center; }

    /* Toggle */
    .sb-toggle { position: relative; width: 36px; height: 20px; cursor: pointer; }
    .sb-toggle input { opacity: 0; width: 0; height: 0; }
    .sb-toggle-slider {
      position: absolute; inset: 0; border-radius: 20px;
      background: #2d3348; transition: background .15s;
    }
    .sb-toggle-slider:before {
      content: ""; position: absolute; width: 14px; height: 14px; border-radius: 50%;
      background: #6b7280; top: 3px; left: 3px; transition: transform .15s, background .15s;
    }
    .sb-toggle input:checked + .sb-toggle-slider { background: #1e3a5f; }
    .sb-toggle input:checked + .sb-toggle-slider:before { transform: translateX(16px); background: #5b8af5; }

    /* Condition builder */
    .sb-cond-list { margin: 12px 0; }
    .sb-cond-row {
      background: #141824; border: 1px solid #1e2437; border-radius: 6px;
      padding: 10px 12px; margin-bottom: 8px;
    }
    .sb-cond-row-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
    .sb-cond-row-header .sb-select { flex: 1; }
    .sb-cond-params { display: flex; flex-wrap: wrap; gap: 8px; }
    .sb-cond-param { display: flex; flex-direction: column; min-width: 100px; }
    .sb-cond-param .sb-label { margin-bottom: 3px; }
    .sb-add-cond {
      background: #141824; border: 1px dashed #2d3348; color: #6b7280;
      border-radius: 6px; padding: 8px; width: 100%; cursor: pointer;
      font-size: 12px; font-family: inherit; text-align: center;
      transition: border-color .12s, color .12s;
    }
    .sb-add-cond:hover { border-color: #5b8af5; color: #c5c9d4; }

    /* Test result */
    .sb-test-result {
      background: #141824; border: 1px solid #1e2437; border-radius: 8px;
      padding: 14px 16px; margin-top: 16px;
    }
    .sb-test-title { font-size: 12px; font-weight: 600; color: #8b95a8; margin-bottom: 10px; letter-spacing: .04em; text-transform: uppercase; }
    .sb-test-fired   { color: #4ade80; font-weight: 600; }
    .sb-test-nofired { color: #6b7280; }
    .sb-cond-result  { display: flex; align-items: center; gap: 8px; font-size: 12px; margin-bottom: 4px; }
    .sb-cond-dot     { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
    .sb-dot-green    { background: #4ade80; }
    .sb-dot-red      { background: #f87171; }

    /* Weight slider */
    .sb-weight-row { display: flex; align-items: center; gap: 10px; }
    .sb-weight-slider { flex: 1; accent-color: #5b8af5; }
    .sb-weight-val { font-size: 12px; color: #e8eaf0; font-weight: 600; min-width: 26px; text-align: right; }

    /* Builtin config */
    .sb-builtin-row {
      display: flex; align-items: center; gap: 10px;
      padding: 10px 0; border-bottom: 1px solid #1a1f2e;
    }
    .sb-builtin-row:last-child { border-bottom: none; }
    .sb-builtin-label { font-size: 13px; color: #c5c9d4; flex: 1; }
    .sb-builtin-weight { font-size: 11px; color: #5b8af5; min-width: 28px; text-align: right; }

    /* Threshold sliders */
    .sb-threshold-group { margin-top: 18px; padding-top: 16px; border-top: 1px solid #1e2437; }
    .sb-threshold-label { font-size: 11px; color: #6b7280; margin-bottom: 5px; letter-spacing:.04em; text-transform: uppercase; }

    /* Empty state */
    .sb-empty { text-align: center; color: #4b5563; font-size: 13px; padding: 32px 0; }

    /* Section heading */
    .sb-section-head { font-size: 11px; color: #4b5563; letter-spacing: .06em; text-transform: uppercase; margin: 18px 0 10px; }

    /* Save row */
    .sb-save-row { display: flex; gap: 10px; margin-top: 20px; }

    /* Toast */
    .sb-toast {
      position: fixed; bottom: 32px; left: 50%; transform: translateX(-50%);
      background: #1e2437; color: #e8eaf0; padding: 10px 20px;
      border-radius: 6px; font-size: 13px; z-index: 9999;
      box-shadow: 0 4px 20px rgba(0,0,0,.5);
      animation: sb-fadein .2s ease; pointer-events: none;
    }
    @keyframes sb-fadein { from { opacity: 0; transform: translateX(-50%) translateY(8px); } }
  `;

  /* ── inject CSS ─────────────────────────────────────────────────────────── */
  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  /* ── inject button ──────────────────────────────────────────────────────── */
  var btn = document.createElement("button");
  btn.id = "sb-btn";
  btn.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14M4.93 4.93a10 10 0 0 0 0 14.14"/>
    </svg>
    Strategy Builder
  `;
  document.body.appendChild(btn);

  /* ── inject panel ───────────────────────────────────────────────────────── */
  var panel = document.createElement("div");
  panel.id = "sb-panel";
  panel.innerHTML = `
    <div class="sb-header">
      <span class="sb-title">⚡ Strategy Builder</span>
      <button class="sb-close" id="sb-close">✕</button>
    </div>
    <div class="sb-tabs">
      <button class="sb-tab active" data-tab="list">My Strategies</button>
      <button class="sb-tab"        data-tab="create">Create New</button>
      <button class="sb-tab"        data-tab="config">Engine Config</button>
    </div>
    <div class="sb-body" id="sb-body"></div>
  `;
  document.body.appendChild(panel);

  /* ── helpers ────────────────────────────────────────────────────────────── */
  function toast(msg) {
    var t = document.createElement("div");
    t.className = "sb-toast";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2800);
  }

  function req(method, url, body) {
    return fetch(url, {
      method: method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) { return r.json(); });
  }

  function getSymbol() {
    var el = document.getElementById("symbol");
    return el ? el.value : "APTUSDT";
  }
  function getInterval() {
    var el = document.getElementById("interval");
    return el ? el.value : "5m";
  }

  /* ── condition type lookup ──────────────────────────────────────────────── */
  function getCondType(value) {
    return CONDITION_TYPES.find(function (c) { return c.value === value; }) || CONDITION_TYPES[0];
  }

  function defaultParams(typeValue) {
    var ct = getCondType(typeValue);
    var params = {};
    ct.params.forEach(function (p) {
      if (p.type === "select") params[p.key] = p.options[0];
      else params[p.key] = p.def !== undefined ? p.def : 1;
    });
    return params;
  }

  /* ── form condition management ──────────────────────────────────────────── */
  function addCondition() {
    var type = "price_above_ema";
    state.form.conditions.push({
      type:   type,
      label:  getCondType(type).label,
      params: defaultParams(type),
    });
    renderBody();
  }

  function removeCondition(idx) {
    state.form.conditions.splice(idx, 1);
    renderBody();
  }

  function updateCondType(idx, newType) {
    state.form.conditions[idx].type   = newType;
    state.form.conditions[idx].label  = getCondType(newType).label;
    state.form.conditions[idx].params = defaultParams(newType);
    renderBody();
  }

  function updateCondParam(idx, key, value) {
    state.form.conditions[idx].params[key] = isNaN(Number(value)) ? value : Number(value);
  }

  /* ── render helpers ─────────────────────────────────────────────────────── */
  function renderConditionRow(cond, idx) {
    var ct = getCondType(cond.type);
    var paramsHtml = ct.params.map(function (p) {
      var val = cond.params[p.key] !== undefined ? cond.params[p.key] : (p.def || 1);
      if (p.type === "select") {
        var opts = p.options.map(function (o) {
          return `<option value="${o}" ${val == o ? "selected" : ""}>${o}</option>`;
        }).join("");
        return `<div class="sb-cond-param">
          <span class="sb-label">${p.label}</span>
          <select class="sb-select" style="width:80px" onchange="window._sbCondParam(${idx},'${p.key}',this.value)">${opts}</select>
        </div>`;
      }
      return `<div class="sb-cond-param">
        <span class="sb-label">${p.label}</span>
        <input type="number" class="sb-input" style="width:80px"
          value="${val}" min="${p.min||0}" max="${p.max||999}" step="${p.step||1}"
          onchange="window._sbCondParam(${idx},'${p.key}',this.value)" />
      </div>`;
    }).join("");

    var typeOpts = CONDITION_TYPES.map(function (t) {
      return `<option value="${t.value}" ${cond.type === t.value ? "selected" : ""}>${t.label}</option>`;
    }).join("");

    return `<div class="sb-cond-row">
      <div class="sb-cond-row-header">
        <select class="sb-select" onchange="window._sbCondType(${idx},this.value)">${typeOpts}</select>
        <button class="sb-btn-ghost" onclick="window._sbRemoveCond(${idx})">✕</button>
      </div>
      <div class="sb-cond-params">${paramsHtml}</div>
    </div>`;
  }

  function renderStrategyForm() {
    var f = state.form;
    var condsHtml = f.conditions.map(function (c, i) { return renderConditionRow(c, i); }).join("");

    return `
      <div class="sb-field">
        <label class="sb-label">Strategy Name</label>
        <input id="sb-f-name" class="sb-input" placeholder="e.g. EMA Bounce + Volume" value="${escHtml(f.name)}"
          oninput="window._sbField('name',this.value)" />
      </div>
      <div class="sb-field">
        <label class="sb-label">Description (optional)</label>
        <textarea id="sb-f-desc" class="sb-textarea" placeholder="What does this strategy look for?"
          oninput="window._sbField('description',this.value)">${escHtml(f.description)}</textarea>
      </div>
      <div class="sb-row2">
        <div class="sb-field">
          <label class="sb-label">Signal Direction</label>
          <select id="sb-f-dir" class="sb-select" onchange="window._sbField('signal_direction',this.value)">
            <option value="bullish" ${f.signal_direction === "bullish" ? "selected" : ""}>🟢 Bullish (LONG bias)</option>
            <option value="bearish" ${f.signal_direction === "bearish" ? "selected" : ""}>🔴 Bearish (SHORT bias)</option>
          </select>
        </div>
        <div class="sb-field">
          <label class="sb-label">Condition Logic</label>
          <select id="sb-f-logic" class="sb-select" onchange="window._sbField('logic',this.value)">
            <option value="AND" ${f.logic === "AND" ? "selected" : ""}>AND — all must fire</option>
            <option value="OR"  ${f.logic === "OR"  ? "selected" : ""}>OR — any can fire</option>
          </select>
        </div>
      </div>
      <div class="sb-field">
        <label class="sb-label">Engine Weight (0–20)</label>
        <div class="sb-weight-row">
          <input type="range" class="sb-weight-slider" min="0" max="20"
            value="${f.weight}" id="sb-f-weight"
            oninput="window._sbField('weight',+this.value);document.getElementById('sb-w-val').textContent=this.value" />
          <span class="sb-weight-val" id="sb-w-val">${f.weight}</span>
        </div>
      </div>

      <div class="sb-section-head">Conditions</div>
      <div class="sb-cond-list" id="sb-cond-list">${condsHtml}</div>
      <button class="sb-add-cond" onclick="window._sbAddCond()">+ Add Condition</button>

      <div class="sb-save-row">
        <button class="sb-btn-primary" onclick="window._sbSave()" ${state.saving ? "disabled" : ""}>
          ${state.saving ? "Saving…" : (state.editing ? "Update Strategy" : "Save Strategy")}
        </button>
        <button class="sb-btn-secondary" onclick="window._sbTest()">Test on Chart</button>
        ${state.editing ? `<button class="sb-btn-secondary" onclick="window._sbCancelEdit()">Cancel</button>` : ""}
      </div>

      ${state.testResult ? renderTestResult(state.testResult) : ""}
    `;
  }

  function renderTestResult(tr) {
    var condRows = (tr.condition_results || []).map(function (cr) {
      return `<div class="sb-cond-result">
        <div class="sb-cond-dot ${cr.fired ? "sb-dot-green" : "sb-dot-red"}"></div>
        <span style="color:${cr.fired ? "#4ade80" : "#6b7280"}">${escHtml(cr.label || cr.type)}</span>
        <span style="color:#4b5563;font-size:11px">${JSON.stringify(cr.params)}</span>
      </div>`;
    }).join("");
    var triggered = tr.triggered;
    return `
      <div class="sb-test-result">
        <div class="sb-test-title">Test Result — ${escHtml(tr.symbol)} ${escHtml(tr.interval)}</div>
        <div style="margin-bottom:10px" class="${triggered ? "sb-test-fired" : "sb-test-nofired"}">
          ${triggered ? "✓ TRIGGERED" : "✗ Not triggered"}
          ${tr.reasons && tr.reasons.length ? " — " + escHtml(tr.reasons[0]) : ""}
        </div>
        ${condRows}
      </div>`;
  }

  function renderStrategyCard(s) {
    var dirClass = s.signal_direction === "bullish" ? "sb-badge-bull" : "sb-badge-bear";
    var dirLabel = s.signal_direction === "bullish" ? "LONG" : "SHORT";
    var condSummary = (s.conditions || []).map(function (c) { return c.label || c.type; }).join(" " + s.logic + " ");

    return `<div class="sb-strategy-card">
      <div class="sb-card-header">
        <label class="sb-toggle">
          <input type="checkbox" ${s.enabled ? "checked" : ""}
            onchange="window._sbToggleStrategy('${s.id}',this.checked)" />
          <span class="sb-toggle-slider"></span>
        </label>
        <span class="sb-card-name">${escHtml(s.name)}</span>
        <span class="sb-card-badge ${dirClass}">${dirLabel}</span>
        <span class="sb-card-badge sb-badge-custom">W:${s.weight}</span>
      </div>
      ${s.description ? `<div class="sb-card-desc">${escHtml(s.description)}</div>` : ""}
      ${condSummary ? `<div class="sb-card-conds">Conditions: ${escHtml(condSummary)}</div>` : ""}
      <div class="sb-card-actions">
        <button class="sb-btn-secondary" style="font-size:12px;padding:6px 12px"
          onclick="window._sbEditStrategy('${s.id}')">Edit</button>
        <button class="sb-btn-danger" onclick="window._sbDeleteStrategy('${s.id}')">Delete</button>
      </div>
    </div>`;
  }

  function renderListTab() {
    if (state.loading) return `<div class="sb-empty">Loading…</div>`;
    if (!state.strategies.length) {
      return `<div class="sb-empty">
        No custom strategies yet.<br/><br/>
        <button class="sb-btn-primary" onclick="window._sbSwitchTab('create')">Create your first strategy →</button>
      </div>`;
    }
    return state.strategies.map(renderStrategyCard).join("");
  }

  function renderConfigTab() {
    if (!state.builtinConfig) return `<div class="sb-empty">Loading…</div>`;
    var bc = state.builtinConfig;
    var rows = bc.strategies.map(function (s) {
      return `<div class="sb-builtin-row">
        <label class="sb-toggle">
          <input type="checkbox" ${s.enabled ? "checked" : ""}
            onchange="window._sbBuiltinToggle('${s.key}',this.checked)" />
          <span class="sb-toggle-slider"></span>
        </label>
        <span class="sb-builtin-label">${escHtml(s.label)}</span>
        <span class="sb-builtin-weight" id="bc-wv-${s.key}">${s.weight}</span>
        <input type="range" class="sb-weight-slider" min="0" max="20"
          value="${s.weight}" style="width:90px"
          oninput="window._sbBuiltinWeight('${s.key}',+this.value);document.getElementById('bc-wv-${s.key}').textContent=this.value" />
      </div>`;
    }).join("");

    return `
      <div class="sb-section-head">Built-in Strategy Weights</div>
      ${rows}
      <div class="sb-threshold-group">
        <div class="sb-threshold-label">Signal Threshold (current: <span id="bc-sig-v">${bc.signal_threshold}</span>)</div>
        <input type="range" class="sb-weight-slider" min="5" max="60"
          value="${bc.signal_threshold}" style="width:100%"
          oninput="window._sbThreshold('signal',+this.value);document.getElementById('bc-sig-v').textContent=this.value" />
        <div class="sb-threshold-label" style="margin-top:12px">Strong Threshold (current: <span id="bc-str-v">${bc.strong_threshold}</span>)</div>
        <input type="range" class="sb-weight-slider" min="10" max="80"
          value="${bc.strong_threshold}" style="width:100%"
          oninput="window._sbThreshold('strong',+this.value);document.getElementById('bc-str-v').textContent=this.value" />
      </div>
      <div class="sb-save-row">
        <button class="sb-btn-primary" onclick="window._sbSaveConfig()">Apply Changes</button>
        <button class="sb-btn-secondary" onclick="window._sbResetConfig()">Reset Defaults</button>
      </div>
    `;
  }

  function escHtml(str) {
    return String(str || "")
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ── main render ────────────────────────────────────────────────────────── */
  var activeTab = "list";

  function renderBody() {
    var body = document.getElementById("sb-body");
    if (!body) return;

    // Update tab active states
    document.querySelectorAll(".sb-tab").forEach(function (t) {
      t.classList.toggle("active", t.dataset.tab === activeTab);
    });

    if (activeTab === "list")   body.innerHTML = renderListTab();
    if (activeTab === "create") body.innerHTML = renderStrategyForm();
    if (activeTab === "config") body.innerHTML = renderConfigTab();
  }

  /* ── data loading ───────────────────────────────────────────────────────── */
  function loadStrategies() {
    state.loading = true;
    renderBody();
    req("GET", API.strategies).then(function (data) {
      state.strategies = data.strategies || [];
      state.loading    = false;
      if (activeTab === "list") renderBody();
    }).catch(function () { state.loading = false; renderBody(); });
  }

  function loadBuiltinConfig() {
    req("GET", API.config).then(function (data) {
      state.builtinConfig = data;
      if (activeTab === "config") renderBody();
    });
  }

  /* ── panel open/close ───────────────────────────────────────────────────── */
  function openPanel() {
    panel.classList.add("open");
    loadStrategies();
    loadBuiltinConfig();
  }
  function closePanel() { panel.classList.remove("open"); }

  btn.addEventListener("click", openPanel);
  document.getElementById("sb-close").addEventListener("click", closePanel);

  document.querySelectorAll(".sb-tab").forEach(function (t) {
    t.addEventListener("click", function () {
      window._sbSwitchTab(t.dataset.tab);
    });
  });

  /* ── global callbacks (called from inline HTML) ─────────────────────────── */
  window._sbSwitchTab = function (tab) {
    activeTab = tab;
    if (tab === "list")   loadStrategies();
    if (tab === "config") loadBuiltinConfig();
    renderBody();
  };

  window._sbField = function (key, value) {
    state.form[key] = value;
  };

  window._sbAddCond   = addCondition;
  window._sbRemoveCond = removeCondition;
  window._sbCondType  = updateCondType;
  window._sbCondParam = updateCondParam;

  window._sbSave = function () {
    if (!state.form.name.trim()) { toast("Please enter a strategy name"); return; }
    if (!state.form.conditions.length) { toast("Add at least one condition"); return; }
    state.saving = true;
    renderBody();

    var payload = {
      name:             state.form.name,
      description:      state.form.description,
      weight:           state.form.weight,
      signal_direction: state.form.signal_direction,
      logic:            state.form.logic,
      conditions:       state.form.conditions,
      enabled:          true,
    };

    var p;
    if (state.editing) {
      p = req("PUT", API.strategies + "/" + state.editing, payload);
    } else {
      p = req("POST", API.strategies, payload);
    }

    p.then(function () {
      toast(state.editing ? "Strategy updated!" : "Strategy saved!");
      state.saving    = false;
      state.editing   = null;
      state.testResult = null;
      state.form      = { name: "", description: "", weight: 8, signal_direction: "bullish", logic: "AND", conditions: [] };
      activeTab = "list";
      loadStrategies();
    }).catch(function () {
      state.saving = false;
      renderBody();
      toast("Error saving strategy");
    });
  };

  window._sbCancelEdit = function () {
    state.editing    = null;
    state.testResult = null;
    state.form       = { name: "", description: "", weight: 8, signal_direction: "bullish", logic: "AND", conditions: [] };
    renderBody();
  };

  window._sbTest = function () {
    if (!state.form.conditions.length) { toast("Add conditions first"); return; }
    var payload = {
      strategy_def: {
        name:             state.form.name || "Test",
        signal_direction: state.form.signal_direction,
        logic:            state.form.logic,
        conditions:       state.form.conditions,
      },
      symbol:   getSymbol(),
      interval: getInterval(),
    };
    req("POST", API.test, payload).then(function (data) {
      state.testResult = data;
      renderBody();
    }).catch(function () { toast("Test failed"); });
  };

  window._sbEditStrategy = function (id) {
    var s = state.strategies.find(function (x) { return x.id === id; });
    if (!s) return;
    state.editing    = id;
    state.testResult = null;
    state.form       = {
      name:             s.name,
      description:      s.description || "",
      weight:           s.weight,
      signal_direction: s.signal_direction,
      logic:            s.logic || "AND",
      conditions:       JSON.parse(JSON.stringify(s.conditions || [])),
    };
    activeTab = "create";
    renderBody();
  };

  window._sbDeleteStrategy = function (id) {
    if (!confirm("Delete this strategy?")) return;
    req("DELETE", API.strategies + "/" + id).then(function () {
      toast("Deleted");
      loadStrategies();
    });
  };

  window._sbToggleStrategy = function (id, enabled) {
    req("PUT", API.strategies + "/" + id, { enabled: enabled }).then(function () {
      var s = state.strategies.find(function (x) { return x.id === id; });
      if (s) s.enabled = enabled;
    });
  };

  /* ── engine config callbacks ────────────────────────────────────────────── */
  var _pendingConfig = {};   // key -> { weight?, enabled? }
  var _pendingThresholds = {};

  window._sbBuiltinWeight = function (key, weight) {
    if (!_pendingConfig[key]) _pendingConfig[key] = {};
    _pendingConfig[key].weight = weight;
    if (state.builtinConfig) {
      var s = state.builtinConfig.strategies.find(function (x) { return x.key === key; });
      if (s) s.weight = weight;
    }
  };

  window._sbBuiltinToggle = function (key, enabled) {
    if (!_pendingConfig[key]) _pendingConfig[key] = {};
    _pendingConfig[key].enabled = enabled;
    if (state.builtinConfig) {
      var s = state.builtinConfig.strategies.find(function (x) { return x.key === key; });
      if (s) s.enabled = enabled;
    }
  };

  window._sbThreshold = function (type, value) {
    _pendingThresholds[type] = value;
    if (state.builtinConfig) {
      if (type === "signal") state.builtinConfig.signal_threshold = value;
      if (type === "strong") state.builtinConfig.strong_threshold = value;
    }
  };

  window._sbSaveConfig = function () {
    var strategies = Object.keys(_pendingConfig).map(function (key) {
      return Object.assign({ key: key }, _pendingConfig[key]);
    });
    var payload = { strategies: strategies };
    if (_pendingThresholds.signal !== undefined) payload.signal_threshold = _pendingThresholds.signal;
    if (_pendingThresholds.strong !== undefined) payload.strong_threshold = _pendingThresholds.strong;

    req("POST", API.config, payload).then(function () {
      toast("Engine config applied!");
      _pendingConfig = {};
      _pendingThresholds = {};
      loadBuiltinConfig();
    }).catch(function () { toast("Error applying config"); });
  };

  window._sbResetConfig = function () {
    if (!confirm("Reset all weights to defaults?")) return;
    if (!state.builtinConfig) return;
    var strategies = state.builtinConfig.strategies.map(function (s) {
      return { key: s.key, weight: s.default_weight, enabled: true };
    });
    req("POST", API.config, { strategies: strategies, signal_threshold: 18, strong_threshold: 40 }).then(function () {
      toast("Reset to defaults");
      _pendingConfig = {};
      _pendingThresholds = {};
      loadBuiltinConfig();
    });
  };
})();
