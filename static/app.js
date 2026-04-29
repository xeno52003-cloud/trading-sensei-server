// 🥷 Trading Sensei — PWA dashboard
// Talks to /api/* with a JWT obtained via PIN login.

const API = "";
const STORAGE_TOKEN = "ts.token";
const STORAGE_DEVICE = "ts.device_id";

const state = {
  token: localStorage.getItem(STORAGE_TOKEN),
  deviceId: localStorage.getItem(STORAGE_DEVICE) || generateDeviceId(),
  tab: "home",
  account: null,
  trades: [],
  alerts: [],
  ea: null,
  summary: null,
  socket: null,
};

localStorage.setItem(STORAGE_DEVICE, state.deviceId);

// ============================================
// Helpers
// ============================================

function generateDeviceId() {
  return "dev-" + Math.random().toString(36).slice(2, 12);
}

function $(sel) { return document.querySelector(sel); }

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;

  const res = await fetch(API + path, { ...opts, headers });
  if (res.status === 401) {
    logout();
    throw new Error("unauthorized");
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

function fmtMoney(n, opts = {}) {
  if (n == null || isNaN(n)) return "—";
  const sign = opts.sign && n >= 0 ? "+" : "";
  return sign + "$" + Number(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtNumber(n, dp = 2) {
  if (n == null || isNaN(n)) return "—";
  return Number(n).toFixed(dp);
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return d.toLocaleDateString();
}

function toast(msg, kind = "") {
  const t = el("div", { class: `toast ${kind}` }, msg);
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2400);
}

// ============================================
// Auth
// ============================================

let pinInput = "";

function renderAuth(error = "") {
  const dots = [0, 1, 2, 3, 4, 5].map((i) =>
    el("div", { class: `pin-dot ${pinInput.length > i ? "filled" : ""} ${error ? "error" : ""}` })
  );

  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "⌫"].map((k) => {
    if (k === "") return el("div", { class: "pin-key", style: "background: transparent;" });
    if (k === "⌫") return el("button", { class: "pin-key delete", onclick: () => pinPress("delete") }, "⌫");
    return el("button", { class: "pin-key", onclick: () => pinPress(k) }, k);
  });

  const root = $("#app");
  root.innerHTML = "";
  root.appendChild(el("div", { class: "auth" },
    el("div", { class: "ninja" }, "🥷"),
    el("h1", {}, "Trading Sensei"),
    el("div", { class: "subtitle" }, "Enter your 6-digit PIN"),
    el("div", { class: "pin-dots" }, ...dots),
    el("div", { class: "error-msg" }, error),
    el("div", { class: "pin-pad" }, ...keys),
    el("div", { class: "hint" }, "Demo: any 6 digits")
  ));
}

async function pinPress(key) {
  if (key === "delete") {
    pinInput = pinInput.slice(0, -1);
    renderAuth();
    return;
  }
  if (pinInput.length >= 6) return;
  pinInput += key;
  renderAuth();
  if (pinInput.length === 6) {
    const pin = pinInput;
    try {
      const res = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ pin, device_id: state.deviceId, user_id: "default" }),
      });
      state.token = res.token;
      localStorage.setItem(STORAGE_TOKEN, res.token);
      pinInput = "";
      bootApp();
    } catch (e) {
      pinInput = "";
      renderAuth(e.message || "Login failed");
    }
  }
}

function logout() {
  state.token = null;
  localStorage.removeItem(STORAGE_TOKEN);
  if (state.socket) { state.socket.disconnect(); state.socket = null; }
  pinInput = "";
  renderAuth();
}

// ============================================
// Data loading
// ============================================

async function refreshAll() {
  try {
    const [account, trades, history, alerts, status, summary, risk] = await Promise.all([
      api("/api/account"),
      api("/api/trades"),
      api("/api/trades/history?limit=20"),
      api("/api/alerts?limit=20"),
      api("/api/status"),
      api("/api/analytics/summary"),
      api("/api/risk/status"),
    ]);
    state.account = account.account;
    state.trades = trades.trades;
    state.history = history.trades;
    state.alerts = alerts.alerts;
    state.ea = status;
    state.summary = summary.summary;
    state.risk = risk.risk;

    // Fallback to OANDA REST when the EA isn't pushing state
    if (!status.ea_connected && status.oanda_configured) {
      const [oa, ot] = await Promise.allSettled([
        api("/api/oanda/account"),
        api("/api/oanda/trades"),
      ]);
      if (oa.status === "fulfilled") state.account = oa.value.account;
      if (ot.status === "fulfilled") state.trades = ot.value.trades;
    }

    renderTab();
  } catch (e) {
    if (e.message !== "unauthorized") toast(e.message, "error");
  }
}

function connectSocket() {
  if (typeof io === "undefined") return;
  state.socket = io({ transports: ["websocket", "polling"] });
  state.socket.on("connect", () => state.socket.emit("join_app", { token: state.token }));
  state.socket.on("auth_error", () => logout());
  state.socket.on("initial_state", (data) => {
    state.account = data.account;
    state.trades = data.trades;
    state.alerts = data.alerts;
    renderTab();
  });
  state.socket.on("account_update", (a) => { state.account = a; renderTab(); });
  state.socket.on("trade_opened", () => refreshAll());
  state.socket.on("trade_closed", () => refreshAll());
  state.socket.on("trade_update", (t) => {
    const idx = state.trades.findIndex((x) => x.id === t.id);
    if (idx >= 0) { state.trades[idx] = t; renderTab(); }
  });
  state.socket.on("new_alert", (a) => {
    state.alerts.unshift(a);
    state.alerts = state.alerts.slice(0, 50);
    toast(a.title);
    renderTab();
  });
}

// ============================================
// Actions
// ============================================

async function toggleEA() {
  const action = state.ea?.ea_running ? "stop" : "start";
  try {
    await api(`/api/ea/${action}`, { method: "POST" });
    toast(`EA ${action} sent`, "success");
  } catch (e) { toast(e.message, "error"); }
}

async function closeAll() {
  if (!confirm("Close ALL open positions?")) return;
  try {
    const res = await api("/api/trades/close-all", { method: "POST" });
    toast(res.message, "success");
  } catch (e) { toast(e.message, "error"); }
}

async function resetBreaker() {
  if (!confirm("Reset the circuit breaker? Trading will resume after the EA receives the next start command.")) return;
  try {
    await api("/api/risk/reset", { method: "POST" });
    toast("Circuit breaker reset", "success");
    refreshAll();
  } catch (e) { toast(e.message, "error"); }
}

async function closeTrade(id) {
  if (!confirm("Close this trade?")) return;
  try {
    await api(`/api/trades/${id}/close`, { method: "POST" });
    toast("Close command sent", "success");
  } catch (e) { toast(e.message, "error"); }
}

// ============================================
// Render — main shell
// ============================================

function renderShell() {
  const root = $("#app");
  root.innerHTML = "";

  root.appendChild(el("div", { class: "header" },
    el("div", {},
      el("h2", {}, "Welcome back 🥷"),
      el("h1", {}, "Trading Sensei")
    ),
    el("button", { class: "pin-key", style: "width:40px;height:40px;font-size:16px;", onclick: logout, title: "Sign out" }, "⏻")
  ));

  const eaUp = state.ea?.ea_connected;
  const oandaUp = !eaUp && state.ea?.oanda_configured;
  const dot = eaUp || oandaUp ? "online" : "";
  const label = eaUp
    ? `EA ${state.ea.ea_running ? "running" : "paused"}`
    : oandaUp
    ? "OANDA REST (read-only)"
    : "Disconnected";
  root.appendChild(el("div", { class: "conn-status" },
    el("div", { class: `conn-dot ${dot}` }),
    el("span", {}, label)
  ));

  if (state.risk?.tripped) {
    const reason = state.risk.tripped_at?.reason || "Risk limit reached";
    root.appendChild(el("div", { class: "breaker-banner" },
      el("div", { class: "breaker-icon" }, "🚨"),
      el("div", { class: "breaker-body" },
        el("div", { class: "breaker-title" }, "Circuit breaker tripped"),
        el("div", { class: "breaker-reason" }, reason)
      ),
      el("button", { class: "breaker-reset", onclick: resetBreaker }, "Reset")
    ));
  }

  root.appendChild(el("div", { id: "tab-content" }));

  const navItems = [
    { id: "home", icon: "🏠", label: "Home" },
    { id: "trades", icon: "📊", label: "Trades" },
    { id: "alerts", icon: "🔔", label: "Alerts" },
    { id: "analytics", icon: "📈", label: "Analytics" },
  ];
  root.appendChild(el("div", { class: "bottom-nav" },
    ...navItems.map((n) =>
      el("button", { class: `nav-item ${state.tab === n.id ? "active" : ""}`, onclick: () => switchTab(n.id) },
        el("div", { class: "nav-icon" }, n.icon),
        el("div", { class: "nav-label" }, n.label)
      )
    )
  ));

  renderTab();
}

function switchTab(id) {
  state.tab = id;
  renderShell();
}

function renderTab() {
  const root = $("#tab-content");
  if (!root) return;
  root.innerHTML = "";
  if (state.tab === "home") renderHome(root);
  else if (state.tab === "trades") renderTrades(root);
  else if (state.tab === "alerts") renderAlerts(root);
  else if (state.tab === "analytics") renderAnalytics(root);
}

// ============================================
// Render — Home
// ============================================

function renderHome(root) {
  const a = state.account;
  if (!a) {
    root.appendChild(el("div", { class: "placeholder" }, el("div", { class: "spinner" })));
    return;
  }

  const pnl = (a.balance || 0) - (a.starting_balance || 10000);
  const pnlPct = ((pnl) / 10000 * 100).toFixed(1);
  const isUp = pnl >= 0;

  root.appendChild(el("div", { class: "card balance-card" },
    el("div", { class: "balance-label" }, "Portfolio Value"),
    el("div", { class: "balance-value" }, fmtMoney(a.balance)),
    el("div", { class: `profit-badge ${isUp ? "up" : "down"}` },
      `${isUp ? "+" : ""}${fmtMoney(pnl).replace("$", "$")} (${pnlPct}%)`
    ),
    el("div", { class: "stat-row" },
      el("div", { class: "stat" },
        el("div", { class: "stat-value", style: `color: var(--${a.unrealized_pnl >= 0 ? "profit" : "loss"});` },
          fmtMoney(a.unrealized_pnl, { sign: true })),
        el("div", { class: "stat-label" }, "Unrealized")
      ),
      el("div", { class: "stat-divider" }),
      el("div", { class: "stat" },
        el("div", { class: "stat-value" }, fmtMoney(a.equity)),
        el("div", { class: "stat-label" }, "Equity")
      ),
      el("div", { class: "stat-divider" }),
      el("div", { class: "stat" },
        el("div", { class: "stat-value" }, String(a.open_trades || 0)),
        el("div", { class: "stat-label" }, "Open")
      )
    )
  ));

  const eaActive = state.ea?.ea_running;
  root.appendChild(el("div", { class: "quick-actions" },
    el("button", { class: `action ${eaActive ? "active" : ""}`, onclick: toggleEA },
      el("div", { class: "action-icon" }, eaActive ? "⏸️" : "▶️"),
      el("div", { class: "action-label" }, eaActive ? "Pause EA" : "Start EA")
    ),
    el("button", { class: "action", onclick: () => switchTab("trades") },
      el("div", { class: "action-icon" }, "📊"),
      el("div", { class: "action-label" }, "Trades")
    ),
    el("button", { class: "action", onclick: () => switchTab("analytics") },
      el("div", { class: "action-icon" }, "📈"),
      el("div", { class: "action-label" }, "Analytics")
    ),
    el("button", { class: "action danger", onclick: closeAll },
      el("div", { class: "action-icon" }, "🚨"),
      el("div", { class: "action-label" }, "Close All")
    )
  ));

  root.appendChild(el("div", { class: "section-title" },
    el("h3", {}, "Active Trades"),
    el("button", { class: "link", onclick: () => switchTab("trades") }, "See all")
  ));

  if (!state.trades.length) {
    root.appendChild(el("div", { class: "card empty" }, "No open trades"));
  } else {
    state.trades.slice(0, 2).forEach((t) => root.appendChild(tradeCard(t)));
  }

  root.appendChild(el("div", { class: "section-title" },
    el("h3", {}, "Recent Alerts"),
    el("button", { class: "link", onclick: () => switchTab("alerts") }, "See all")
  ));
  if (!state.alerts.length) {
    root.appendChild(el("div", { class: "card empty" }, "No alerts yet"));
  } else {
    state.alerts.slice(0, 3).forEach((a) => root.appendChild(alertCard(a)));
  }
}

function tradeCard(t) {
  const isProfit = (t.pnl ?? 0) >= 0;
  const sym = (t.symbol || "").includes("XAU") ? "🥇" : "💱";
  return el("div", { class: `card trade ${t.type === "SELL" ? "sell" : ""}` },
    el("div", { class: "trade-header" },
      el("div", { class: "trade-symbol" },
        el("div", { class: "icon" }, sym),
        el("div", {},
          el("div", { class: "trade-name" }, t.symbol || "—"),
          el("div", { class: "trade-meta" }, `${t.type} • ${t.lots} lots`)
        )
      ),
      el("div", { class: "trade-pnl" },
        el("div", { class: "trade-pnl-value", style: `color: var(--${isProfit ? "profit" : "loss"});` },
          fmtMoney(t.pnl, { sign: true })),
        el("div", { class: "trade-pnl-pips", style: `color: var(--${isProfit ? "profit" : "loss"});` },
          `${isProfit ? "+" : ""}${fmtNumber(t.pips, 1)} pips`)
      )
    ),
    el("div", { class: "trade-details" },
      detail("Entry", fmtNumber(t.entry_price, 2)),
      detail("Current", fmtNumber(t.current_price, 2)),
      detail("SL", fmtNumber(t.stop_loss, 2), "loss"),
      detail("TP", fmtNumber(t.take_profit, 2), "profit")
    ),
    t.status === "OPEN" ? el("div", { class: "trade-actions" },
      el("button", { class: "trade-btn danger", onclick: () => closeTrade(t.id) }, "Close")
    ) : null
  );
}

function detail(label, value, color = "") {
  const style = color ? `color: var(--${color});` : "";
  return el("div", { class: "trade-detail" },
    el("div", { class: "detail-label" }, label),
    el("div", { class: "detail-value", style }, value)
  );
}

// ============================================
// Render — Trades
// ============================================

function renderTrades(root) {
  root.appendChild(el("div", { class: "section-title" }, el("h3", {}, "Open")));
  if (!state.trades.length) {
    root.appendChild(el("div", { class: "card empty" }, "No open trades"));
  } else {
    state.trades.forEach((t) => root.appendChild(tradeCard(t)));
  }

  root.appendChild(el("div", { class: "section-title" }, el("h3", {}, "History")));
  const history = state.history || [];
  if (!history.length) {
    root.appendChild(el("div", { class: "card empty" }, "No trade history"));
  } else {
    history.slice().reverse().forEach((t) => root.appendChild(tradeCard(t)));
  }
}

// ============================================
// Render — Alerts
// ============================================

function renderAlerts(root) {
  if (!state.alerts.length) {
    root.appendChild(el("div", { class: "placeholder" },
      el("div", { class: "emoji" }, "🔔"),
      el("div", { class: "heading" }, "No alerts yet"),
      el("div", { class: "sub" }, "Trade signals will show up here")
    ));
    return;
  }
  state.alerts.forEach((a) => root.appendChild(alertCard(a)));
}

function alertCard(a) {
  const icons = { buy: "📈", sell: "📉", profit: "✅", loss: "❌", info: "ℹ️", emergency: "🚨" };
  return el("div", { class: "alert" },
    el("div", { class: `alert-icon ${a.type}` }, icons[a.type] || "ℹ️"),
    el("div", { style: "flex:1; min-width:0;" },
      el("div", { class: "alert-title" }, a.title),
      el("div", { class: "alert-message" }, a.message),
      el("div", { class: "alert-time" }, fmtTime(a.timestamp))
    )
  );
}

// ============================================
// Render — Analytics
// ============================================

function renderAnalytics(root) {
  const s = state.summary;
  if (!s) {
    root.appendChild(el("div", { class: "placeholder" }, el("div", { class: "spinner" })));
    return;
  }

  root.appendChild(el("div", { class: "card" },
    el("div", { class: "balance-label" }, "Total P&L"),
    el("div", { class: "balance-value", style: `color: var(--${s.total_pnl >= 0 ? "profit" : "loss"});` },
      fmtMoney(s.total_pnl, { sign: true })),
    el("div", { class: "trade-meta" }, `${s.total_trades} closed trades`)
  ));

  root.appendChild(el("div", { class: "card" },
    el("canvas", { class: "chart", id: "equity-chart" })
  ));
  drawEquity(s.equity_curve);

  const r = state.risk;
  const metrics = [
    ["Win Rate", `${s.win_rate}%`, s.win_rate >= 50 ? "profit" : "loss"],
    ["Profit Factor", fmtNumber(s.profit_factor, 2), s.profit_factor >= 1.5 ? "profit" : ""],
    ["Avg Win", fmtMoney(s.avg_win), "profit"],
    ["Avg Loss", fmtMoney(s.avg_loss), "loss"],
    ["Largest Win", fmtMoney(s.largest_win), "profit"],
    ["Largest Loss", fmtMoney(s.largest_loss), "loss"],
    ["Max Drawdown", `${s.max_drawdown_pct}%`, s.max_drawdown_pct < 10 ? "profit" : "loss"],
    ["Avg Trade", fmtMoney(s.avg_trade), s.avg_trade >= 0 ? "profit" : "loss"],
    ...(r ? [
      ["Today's P&L", fmtMoney(r.today_pnl, { sign: true }), r.today_pnl >= 0 ? "profit" : "loss"],
      ["Losing Streak", String(r.losing_streak), r.losing_streak >= 3 ? "loss" : ""],
      ["Peak Balance", fmtMoney(r.peak_balance), ""],
      ["Risk Status", r.tripped ? "Tripped" : "OK", r.tripped ? "loss" : "profit"],
    ] : []),
  ];
  root.appendChild(el("div", { class: "card" },
    el("div", { class: "metrics-grid" },
      ...metrics.map(([label, value, color]) =>
        el("div", { class: "metric" },
          el("div", { class: "metric-label" }, label),
          el("div", { class: `metric-value ${color || ""}` }, value)
        )
      )
    )
  ));
}

function drawEquity(curve) {
  const canvas = $("#equity-chart");
  if (!canvas || !curve?.length) return;
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  ctx.scale(dpr, dpr);

  const w = rect.width, h = rect.height;
  const values = curve.map((p) => p.balance);
  const min = Math.min(...values), max = Math.max(...values);
  const range = max - min || 1;

  ctx.fillStyle = "#18181B";
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = "#6366F1";
  ctx.lineWidth = 2;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = (i / (values.length - 1 || 1)) * w;
    const y = h - ((v - min) / range) * (h - 16) - 8;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();

  const grad = ctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(99, 102, 241, 0.4)");
  grad.addColorStop(1, "rgba(99, 102, 241, 0)");
  ctx.fillStyle = grad;
  ctx.lineTo(w, h);
  ctx.lineTo(0, h);
  ctx.closePath();
  ctx.fill();
}

// ============================================
// Boot
// ============================================

function bootApp() {
  renderShell();
  refreshAll();
  connectSocket();
  setInterval(refreshAll, 15000);
}

if (state.token) {
  bootApp();
} else {
  renderAuth();
}
