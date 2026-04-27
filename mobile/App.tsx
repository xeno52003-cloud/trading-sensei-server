/**
 * 🥷 Trading Sensei — React Native app
 *
 * This is the user's App_Simple.tsx adapted to talk to the live server
 * via mobile/api.ts. PIN login → JWT, dashboard pulls balance/trades/
 * alerts and subscribes to Socket.IO push updates.
 *
 * Wire-up: set SERVER_URL below, drop this file plus mobile/api.ts into a
 * fresh `npx react-native@latest init TradingSensei` project, then install
 * the deps listed in mobile/README.md.
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Animated,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { Account, Alert as AlertItem, AnalyticsSummary, SenseiAPI, Trade } from "./api";

const SERVER_URL = "https://your-server.example.com";   // ← set me

const theme = {
  primary: "#6366F1",
  bgDark: "#09090B",
  bgCard: "#18181B",
  bgElevated: "#27272A",
  profit: "#10B981",
  loss: "#EF4444",
  text: "#FAFAFA",
  textSecondary: "#A1A1AA",
  textMuted: "#52525B",
  border: "rgba(255,255,255,0.06)",
};

const api = new SenseiAPI(SERVER_URL);

// ============================================================
// Auth screen
// ============================================================

function AuthScreen({ onLogin }: { onLogin: () => void }) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = useCallback(async (next: string) => {
    setBusy(true);
    try {
      await api.login(next);
      onLogin();
    } catch (e: any) {
      Alert.alert("Login failed", e.message ?? "Unknown error");
      setPin("");
    } finally {
      setBusy(false);
    }
  }, [onLogin]);

  const press = (digit: string) => {
    if (busy || pin.length >= 6) return;
    const next = pin + digit;
    setPin(next);
    if (next.length === 6) submit(next);
  };

  const back = () => setPin((p) => p.slice(0, -1));

  return (
    <SafeAreaView style={styles.authContainer}>
      <StatusBar barStyle="light-content" />
      <Text style={styles.ninja}>🥷</Text>
      <Text style={styles.authTitle}>Trading Sensei</Text>
      <Text style={styles.authSubtitle}>Enter your 6-digit PIN</Text>

      <View style={styles.pinDots}>
        {[0,1,2,3,4,5].map(i => (
          <View key={i} style={[styles.pinDot, pin.length > i && styles.pinDotFilled]} />
        ))}
      </View>

      <View style={styles.pinPad}>
        {["1","2","3","4","5","6","7","8","9","","0","⌫"].map((k, i) => {
          if (k === "") return <View key={i} style={styles.pinKey} />;
          if (k === "⌫") return (
            <TouchableOpacity key={i} style={styles.pinKey} onPress={back}>
              <Text style={styles.pinKeyText}>⌫</Text>
            </TouchableOpacity>
          );
          return (
            <TouchableOpacity key={i} style={styles.pinKey} onPress={() => press(k)}>
              <Text style={styles.pinKeyText}>{k}</Text>
            </TouchableOpacity>
          );
        })}
      </View>

      {busy && <ActivityIndicator color={theme.primary} style={{ marginTop: 16 }} />}
    </SafeAreaView>
  );
}

// ============================================================
// Home screen
// ============================================================

function HomeScreen({ onLogout }: { onLogout: () => void }) {
  const [account, setAccount] = useState<Account | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [eaConnected, setEaConnected] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const socketRef = useRef<ReturnType<typeof api.connectSocket> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [acc, tr, al, st, sm] = await Promise.all([
        api.account(), api.trades(), api.alerts(20), api.status(), api.summary(),
      ]);
      setAccount(acc.account);
      setTrades(tr.trades);
      setAlerts(al.alerts);
      setSummary(sm.summary);
      setEaConnected(st.ea_connected);

      if (!st.ea_connected && st.oanda_configured) {
        const [oa, ot] = await Promise.allSettled([api.oandaAccount(), api.oandaTrades()]);
        if (oa.status === "fulfilled") setAccount(oa.value.account);
        if (ot.status === "fulfilled") setTrades(ot.value.trades);
      }
    } catch (e: any) {
      if (e.message === "unauthorized") onLogout();
    }
  }, [onLogout]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 15000);

    socketRef.current = api.connectSocket({
      onAccount: setAccount,
      onTradeOpened: () => refresh(),
      onTradeClosed: () => refresh(),
      onTradeUpdate: (t) => setTrades(prev => prev.map(p => p.id === t.id ? t : p)),
      onAlert: (a) => setAlerts(prev => [a, ...prev].slice(0, 50)),
      onAuthError: onLogout,
    });

    return () => {
      clearInterval(interval);
      socketRef.current?.disconnect();
    };
  }, [refresh, onLogout]);

  const onRefresh = async () => {
    setRefreshing(true);
    await refresh();
    setRefreshing(false);
  };

  const closeAll = () =>
    Alert.alert("Close all", "Close every open position?", [
      { text: "Cancel", style: "cancel" },
      { text: "Close all", style: "destructive", onPress: () => api.closeAll() },
    ]);

  if (!account) {
    return (
      <View style={[styles.flex, styles.center]}>
        <ActivityIndicator color={theme.primary} />
      </View>
    );
  }

  return (
    <SafeAreaView style={styles.flex}>
      <StatusBar barStyle="light-content" />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 60 }}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={theme.primary} />}
      >
        <View style={styles.header}>
          <View>
            <Text style={styles.headerSubtitle}>Welcome back 🥷</Text>
            <Text style={styles.headerTitle}>Trading Sensei</Text>
          </View>
          <TouchableOpacity style={styles.logoutBtn} onPress={onLogout}>
            <Text style={styles.logoutText}>⏻</Text>
          </TouchableOpacity>
        </View>

        <View style={[styles.connStatus, eaConnected && styles.connOnline]}>
          <Text style={styles.connText}>
            {eaConnected ? "● EA connected" : "○ EA disconnected"}
          </Text>
        </View>

        <View style={styles.balanceCard}>
          <Text style={styles.balanceLabel}>Portfolio Value</Text>
          <Text style={styles.balanceValue}>${account.balance.toFixed(2)}</Text>
          <View style={[
            styles.profitBadge,
            { backgroundColor: account.unrealized_pnl >= 0 ? `${theme.profit}20` : `${theme.loss}20` }
          ]}>
            <Text style={{ color: account.unrealized_pnl >= 0 ? theme.profit : theme.loss, fontWeight: "600" }}>
              {account.unrealized_pnl >= 0 ? "+" : ""}${account.unrealized_pnl.toFixed(2)} unrealized
            </Text>
          </View>

          <View style={styles.statRow}>
            <Stat label="Equity" value={`$${account.equity.toFixed(2)}`} />
            <Stat label="Open" value={String(account.open_trades)} />
            <Stat label="Win Rate" value={summary ? `${summary.win_rate}%` : "—"} />
          </View>
        </View>

        <View style={styles.actions}>
          <ActionButton icon="📊" label={`${trades.length} open`} />
          <ActionButton icon="🚨" label="Close All" danger onPress={closeAll} />
        </View>

        <Text style={styles.section}>Active Trades</Text>
        {trades.length === 0
          ? <Text style={styles.empty}>No open trades</Text>
          : trades.map(t => <TradeCard key={t.id} trade={t} />)}

        <Text style={styles.section}>Recent Alerts</Text>
        {alerts.slice(0, 5).map(a => (
          <View key={a.id} style={styles.alertCard}>
            <Text style={styles.alertTitle}>{a.title}</Text>
            <Text style={styles.alertMessage}>{a.message}</Text>
          </View>
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

// ============================================================
// Building blocks
// ============================================================

const Stat = ({ label, value }: { label: string; value: string }) => (
  <View style={styles.stat}>
    <Text style={styles.statValue}>{value}</Text>
    <Text style={styles.statLabel}>{label}</Text>
  </View>
);

const ActionButton = ({ icon, label, danger, onPress }: {
  icon: string; label: string; danger?: boolean; onPress?: () => void;
}) => (
  <TouchableOpacity
    style={[styles.actionBtn, danger && { borderColor: theme.loss }]}
    onPress={onPress}
  >
    <Text style={styles.actionIcon}>{icon}</Text>
    <Text style={[styles.actionLabel, danger && { color: theme.loss }]}>{label}</Text>
  </TouchableOpacity>
);

const TradeCard = ({ trade }: { trade: Trade }) => (
  <View style={[styles.tradeCard, trade.type === "SELL" && { borderLeftColor: theme.loss }]}>
    <View style={styles.tradeHeader}>
      <Text style={styles.tradeName}>{trade.symbol}</Text>
      <Text style={[styles.tradePnl, { color: trade.pnl >= 0 ? theme.profit : theme.loss }]}>
        {trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(2)}
      </Text>
    </View>
    <Text style={styles.tradeMeta}>{trade.type} • {trade.lots} lots @ {trade.entry_price}</Text>
  </View>
);

// ============================================================
// Root
// ============================================================

export default function App() {
  const [authed, setAuthed] = useState<boolean | null>(null);

  useEffect(() => {
    api.restore().then(setAuthed);
  }, []);

  if (authed === null) {
    return (
      <View style={[styles.flex, styles.center]}>
        <ActivityIndicator color={theme.primary} />
      </View>
    );
  }

  if (!authed) return <AuthScreen onLogin={() => setAuthed(true)} />;
  return <HomeScreen onLogout={async () => { await api.logout(); setAuthed(false); }} />;
}

// ============================================================
// Styles
// ============================================================

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: theme.bgDark },
  center: { alignItems: "center", justifyContent: "center" },

  authContainer: { flex: 1, backgroundColor: theme.bgDark, alignItems: "center", justifyContent: "center", padding: 20 },
  ninja: { fontSize: 80, marginBottom: 8 },
  authTitle: { fontSize: 28, fontWeight: "700", color: theme.text },
  authSubtitle: { fontSize: 16, color: theme.textSecondary, marginTop: 4, marginBottom: 32 },
  pinDots: { flexDirection: "row", gap: 12, marginBottom: 40 },
  pinDot: { width: 16, height: 16, borderRadius: 8, borderWidth: 2, borderColor: theme.textMuted },
  pinDotFilled: { backgroundColor: theme.primary, borderColor: theme.primary },
  pinPad: { flexDirection: "row", flexWrap: "wrap", width: 280, justifyContent: "center", gap: 14 },
  pinKey: { width: 75, height: 75, borderRadius: 40, backgroundColor: theme.bgCard, alignItems: "center", justifyContent: "center" },
  pinKeyText: { fontSize: 28, color: theme.text, fontWeight: "600" },

  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center", padding: 16 },
  headerSubtitle: { fontSize: 14, color: theme.textSecondary },
  headerTitle: { fontSize: 22, fontWeight: "700", color: theme.text },
  logoutBtn: { width: 40, height: 40, borderRadius: 20, backgroundColor: theme.bgCard, alignItems: "center", justifyContent: "center" },
  logoutText: { color: theme.text, fontSize: 18 },

  connStatus: { paddingHorizontal: 16, paddingBottom: 8 },
  connOnline: {},
  connText: { fontSize: 12, color: theme.textSecondary },

  balanceCard: { backgroundColor: theme.bgCard, marginHorizontal: 16, padding: 20, borderRadius: 16, borderWidth: 1, borderColor: theme.border },
  balanceLabel: { fontSize: 12, color: theme.textSecondary, textTransform: "uppercase", letterSpacing: 1 },
  balanceValue: { fontSize: 36, fontWeight: "700", color: theme.text, marginVertical: 6 },
  profitBadge: { alignSelf: "flex-start", paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20 },

  statRow: { flexDirection: "row", marginTop: 16, paddingTop: 16, borderTopWidth: 1, borderTopColor: theme.border },
  stat: { flex: 1, alignItems: "center" },
  statValue: { color: theme.text, fontSize: 16, fontWeight: "600" },
  statLabel: { color: theme.textSecondary, fontSize: 11, marginTop: 2 },

  actions: { flexDirection: "row", gap: 8, paddingHorizontal: 16, marginVertical: 12 },
  actionBtn: { flex: 1, backgroundColor: theme.bgCard, borderRadius: 14, padding: 14, alignItems: "center", borderWidth: 1, borderColor: theme.border },
  actionIcon: { fontSize: 22 },
  actionLabel: { fontSize: 12, color: theme.textSecondary, marginTop: 4 },

  section: { fontSize: 16, fontWeight: "600", color: theme.text, paddingHorizontal: 16, marginTop: 16, marginBottom: 8 },
  empty: { color: theme.textMuted, paddingHorizontal: 16, paddingVertical: 24, textAlign: "center" },

  tradeCard: { backgroundColor: theme.bgCard, marginHorizontal: 16, marginBottom: 8, padding: 14, borderRadius: 14, borderWidth: 1, borderColor: theme.border, borderLeftWidth: 4, borderLeftColor: theme.profit },
  tradeHeader: { flexDirection: "row", justifyContent: "space-between" },
  tradeName: { color: theme.text, fontSize: 16, fontWeight: "700" },
  tradeMeta: { color: theme.textSecondary, fontSize: 12, marginTop: 4 },
  tradePnl: { fontSize: 18, fontWeight: "700" },

  alertCard: { backgroundColor: theme.bgCard, marginHorizontal: 16, marginBottom: 8, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.border },
  alertTitle: { color: theme.text, fontSize: 13, fontWeight: "600" },
  alertMessage: { color: theme.textSecondary, fontSize: 12, marginTop: 2 },
});
