# 🥷 Trading Sensei — React Native starter

This folder is a **starter pack**, not a working RN project. The two files
here (`api.ts`, `App.tsx`) drop into a fresh React Native project and talk
to the live `trading-sensei-server` API.

If you only need a phone-friendly view, the PWA at `/` already works as a
home-screen-installable app. Use this only if you specifically want a
native app (App Store / Play Store, push notifications, biometrics).

## Why a separate project

Your server repo deploys to Railway/Render/Fly. RN deploys to App Store /
Play Store, has its own CI, native code, signing certificates, simulators,
and Metro bundler. Keeping them in one repo creates more friction than it
removes. Extract these files into their own repo as soon as you start
iterating.

## Bootstrap a fresh project

```bash
# Pick a directory OUTSIDE this server repo
cd ~/projects
npx react-native@latest init TradingSensei
cd TradingSensei

# Copy the starter files
cp <path-to-server>/mobile/App.tsx .
cp <path-to-server>/mobile/api.ts .

# Install runtime deps
npm install \
  @react-native-async-storage/async-storage \
  socket.io-client

# iOS only
cd ios && pod install && cd ..

# Set the server URL in App.tsx → SERVER_URL = "https://..."

# Run
npx react-native run-ios       # or run-android
```

## What works out of the box

| Feature                   | File         | Notes                                              |
|---------------------------|--------------|----------------------------------------------------|
| PIN login → JWT           | `api.ts`     | token persisted in AsyncStorage                    |
| Account / trades / alerts | `App.tsx`    | REST poll every 15 s + Socket.IO push updates      |
| OANDA fallback            | `App.tsx`    | auto-used when EA disconnected                     |
| Close-all action          | `App.tsx`    | confirms before sending                            |
| Auto re-login on 401      | `api.ts`     | token cleared, app drops back to PIN screen        |

## What's stubbed and ready to flesh out

- **Biometric login** — replace the PIN pad gate with `react-native-biometrics`
  once you've confirmed `api.restore()` returned a token. Keep the PIN as a
  fallback.
- **Push notifications** — call `api.registerDevice(fcmToken, "ios"|"android")`
  after `messaging().getToken()` resolves. The server already routes pushes
  via Firebase when `FIREBASE_SERVER_KEY` is set.
- **Modify SL/TP UI** — `api.modifyTrade(id, sl, tp)` is wired; build a sheet
  for it.
- **Charts** — drop in `victory-native` or `react-native-chart-kit` and feed
  it `summary.equity_curve`.

## Schema

`api.ts` exports `Account`, `Trade`, `Alert`, `AnalyticsSummary` types that
mirror the server. If you change `webhook_server.py`, update both at the
same time — there's no codegen.
