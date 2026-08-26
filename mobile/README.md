# Window Control Mobile Client

React Native (Expo) client for real-time mobile device control via WebRTC.

## Prerequisites

- **Node.js 22** (includes npm)
- **Expo Account** (free): Sign up at [expo.dev](https://expo.dev) for EAS Build access
- **Physical Device or Simulator** with the dev-client installed (see Build steps below)
- **Tailscale Network**: Verify connectivity to your server at `http://100.x.x.x:8080`

## Installation & Build

### Step 1: Install Dependencies
```bash
cd mobile
npm install
```

### Step 2: Build the Dev Client (one-time per platform)

> **Important:** Expo Go will **NOT** work with this app because it uses native WebRTC (`react-native-webrtc`). You must build and install the dev-client on your device first.

Log in to your Expo account:
```bash
npx eas login
```

Build the development client for your platform:
```bash
# For iOS
npx eas build --profile development --platform ios

# For Android
npx eas build --profile development --platform android
```

Once the build completes, install the development client app on your device (iOS via TestFlight or download link; Android via direct install or scan QR code).

> **If you already have a dev-client installed from before this branch:** it must be rebuilt (repeat this step), not just re-bundled via `npx expo start --dev-client`. This branch adds a native module, `@react-native-cookies/cookies`, imported at module scope by `src/api/ServerContext.tsx`. Native modules are compiled into the dev-client binary itself -- an older binary that predates this dependency will hard-crash at startup (before any JS error boundary can catch it) once it loads a bundle that imports it, since re-bundling alone can't add a native module to an already-built binary.

### Step 3: Run the Dev Server
Start the development server with the dev-client profile:
```bash
npx expo start --dev-client
```

This starts the JavaScript bundle server. Connect your physical device or simulator running the dev-client app to the server:
- Launch the dev-client app on your device
- On first launch, the app auto-discovers the server: it asks the baked-in public URL (`app.json`'s `extra.publicUrl`) for the host's current Tailscale/LAN IP via `/server-info`, then connects to whichever of the two answers first
- The resolved base URL persists in AsyncStorage, so subsequent launches skip straight to it

You should see the app load and display the "Looking for your server…" connecting screen, then the instance list.

## Running Tests

Run the unit test suite:
```bash
npm test
```

## Device Verification

See [`docs/device-smoke-test.md`](docs/device-smoke-test.md) for the manual verification checklist to confirm all features work on a real device over Tailscale.
