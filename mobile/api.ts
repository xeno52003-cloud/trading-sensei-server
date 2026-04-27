/**
 * 🥷 Trading Sensei — REST + WebSocket client
 *
 * Drop into your React Native (or any TS) project. The server's API surface
 * is the single source of truth — keep this file in sync with /api/*.
 */

import AsyncStorage from "@react-native-async-storage/async-storage";
import { io, Socket } from "socket.io-client";

const TOKEN_KEY = "ts.token";
const DEVICE_KEY = "ts.device_id";

export type Account = {
  balance: number;
  equity: number;
  margin_used: number;
  margin_available: number;
  unrealized_pnl: number;
  total_pnl: number;
  open_trades: number;
};

export type Trade = {
  id: string;
  symbol: string;
  type: "BUY" | "SELL";
  lots: number;
  entry_price: number;
  current_price: number;
  stop_loss: number;
  take_profit: number;
  pnl: number;
  pips: number;
  open_time: string;
  close_time?: string;
  status: "OPEN" | "CLOSED";
  signal_strength: number;
};

export type Alert = {
  id: string;
  title: string;
  message: string;
  type: "buy" | "sell" | "profit" | "loss" | "info" | "emergency";
  timestamp: string;
  read: boolean;
};

export type AnalyticsSummary = {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate: number;
  total_pnl: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  max_drawdown_pct: number;
  largest_win: number;
  largest_loss: number;
  equity_curve: { timestamp: string | null; balance: number }[];
};

export class SenseiAPI {
  baseUrl: string;
  private token: string | null = null;
  private deviceId: string | null = null;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
  }

  async restore(): Promise<boolean> {
    this.token = await AsyncStorage.getItem(TOKEN_KEY);
    this.deviceId = await AsyncStorage.getItem(DEVICE_KEY);
    if (!this.deviceId) {
      this.deviceId = "rn-" + Math.random().toString(36).slice(2, 12);
      await AsyncStorage.setItem(DEVICE_KEY, this.deviceId);
    }
    return Boolean(this.token);
  }

  async login(pin: string): Promise<void> {
    const res = await this.request<{ token: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ pin, device_id: this.deviceId, user_id: "admin" }),
    }, { skipAuth: true });
    this.token = res.token;
    await AsyncStorage.setItem(TOKEN_KEY, res.token);
  }

  async logout(): Promise<void> {
    this.token = null;
    await AsyncStorage.removeItem(TOKEN_KEY);
  }

  isAuthenticated(): boolean {
    return Boolean(this.token);
  }

  // --- domain endpoints ---

  status() { return this.request<{
    online: boolean; ea_connected: boolean; ea_running: boolean;
    oanda_configured: boolean; last_heartbeat: string | null;
  }>("/api/status"); }

  account() { return this.request<{ account: Account }>("/api/account"); }
  oandaAccount() { return this.request<{ account: Account }>("/api/oanda/account"); }

  trades() { return this.request<{ trades: Trade[]; count: number }>("/api/trades"); }
  oandaTrades() { return this.request<{ trades: Trade[]; count: number }>("/api/oanda/trades"); }

  history(limit = 50) {
    return this.request<{ trades: Trade[]; count: number }>(`/api/trades/history?limit=${limit}`);
  }

  alerts(limit = 50) {
    return this.request<{ alerts: Alert[]; count: number }>(`/api/alerts?limit=${limit}`);
  }

  summary() { return this.request<{ summary: AnalyticsSummary }>("/api/analytics/summary"); }

  // --- actions ---

  controlEA(action: "start" | "stop" | "pause") {
    return this.request(`/api/ea/${action}`, { method: "POST" });
  }

  closeTrade(id: string) {
    return this.request(`/api/trades/${id}/close`, { method: "POST" });
  }

  closeAll() {
    return this.request("/api/trades/close-all", { method: "POST" });
  }

  modifyTrade(id: string, sl?: number, tp?: number) {
    return this.request(`/api/trades/${id}/modify`, {
      method: "PUT",
      body: JSON.stringify({ sl, tp }),
    });
  }

  changePin(currentPin: string, newPin: string) {
    return this.request("/api/auth/change-pin", {
      method: "POST",
      body: JSON.stringify({ current_pin: currentPin, new_pin: newPin }),
    });
  }

  registerDevice(token: string, platform: "ios" | "android") {
    return this.request("/api/device/register", {
      method: "POST",
      body: JSON.stringify({ token, platform }),
    });
  }

  // --- websocket ---

  connectSocket(handlers: {
    onAccount?: (a: Account) => void;
    onTradeOpened?: (t: Trade) => void;
    onTradeClosed?: (t: Trade) => void;
    onTradeUpdate?: (t: Trade) => void;
    onAlert?: (a: Alert) => void;
    onAuthError?: () => void;
  }): Socket {
    const socket = io(this.baseUrl, { transports: ["websocket"] });
    socket.on("connect", () => socket.emit("join_app", { token: this.token }));
    socket.on("auth_error", () => handlers.onAuthError?.());
    if (handlers.onAccount)      socket.on("account_update", handlers.onAccount);
    if (handlers.onTradeOpened)  socket.on("trade_opened", handlers.onTradeOpened);
    if (handlers.onTradeClosed)  socket.on("trade_closed", handlers.onTradeClosed);
    if (handlers.onTradeUpdate)  socket.on("trade_update", handlers.onTradeUpdate);
    if (handlers.onAlert)        socket.on("new_alert", handlers.onAlert);
    return socket;
  }

  // --- internals ---

  private async request<T>(
    path: string,
    opts: RequestInit = {},
    flags: { skipAuth?: boolean } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...((opts.headers as Record<string, string>) || {}),
    };
    if (this.token && !flags.skipAuth) headers.Authorization = `Bearer ${this.token}`;

    const res = await fetch(this.baseUrl + path, { ...opts, headers });
    if (res.status === 401) {
      await this.logout();
      throw new Error("unauthorized");
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body as T;
  }
}
