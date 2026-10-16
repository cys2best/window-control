import { Platform } from "react-native";

// Expo registers the native families under their asset names. The web app
// loads the same faces from Google Fonts, where CSS requires their family
// names instead. Using the Expo identifiers in a browser silently falls back
// to a serif system face.
const isWeb = Platform.OS === "web";

// EmuCtrl v2 ("Nendojo dark"): warm neutrals, a near-white action colour
// with dark ink on it, kiln for live/exit, squared 2px corners, and
// Be Vietnam Pro at 400/500 only. Nothing outside this palette ships.
export const theme = {
  color: {
    bg: "#141413",
    screen: "#1a1a18",
    streamBg: "#000000",
    surface: "#1a1a18",
    surfaceRaised: "#1e1d1b",
    card: "#1e1d1b",
    cardActive: "#1a1a18",
    glass: "rgba(26,26,24,0.72)",
    border: "#393836",
    hairline: "#292826",
    text: "#faf9f5",
    textMuted: "#87867f",
    textDim: "#87867f",
    accent: "#faf9f5",
    accentInk: "#faf9f5",
    // Text and icons drawn on an accent (action) fill.
    onAccent: "#141413",
    accentPressed: "#b0aea5",
    live: "#e8895c",
    telemetry: "#faf9f5",
    warning: "#d9a73e",
    error: "#e8895c",
    errorBg: "rgba(232,137,92,0.12)",
  },
  net: {
    connected: { dot: "#faf9f5", chipBg: "#1e1d1b", chipFg: "#faf9f5" },
    connecting: { dot: "#d9a73e", chipBg: "rgba(217,167,62,0.10)", chipFg: "#d9a73e" },
    disconnected: { dot: "#e8895c", chipBg: "rgba(232,137,92,0.10)", chipFg: "#e8895c" },
  },
  radius: { card: 2, input: 2, pill: 999, sm: 2 },
  font: {
    regular: isWeb ? "Be Vietnam Pro" : "BeVietnamPro_400Regular",
    medium: isWeb ? "Be Vietnam Pro" : "BeVietnamPro_500Medium",
    // v2 never goes above 500; heavier names keep existing call sites.
    semibold: isWeb ? "Be Vietnam Pro" : "BeVietnamPro_500Medium",
    bold: isWeb ? "Be Vietnam Pro" : "BeVietnamPro_500Medium",
    mono: isWeb ? "JetBrains Mono" : "JetBrainsMono_400Regular",
    monoMedium: isWeb ? "JetBrains Mono" : "JetBrainsMono_500Medium",
    monoBold: isWeb ? "JetBrains Mono" : "JetBrainsMono_700Bold",
  },
} as const;
