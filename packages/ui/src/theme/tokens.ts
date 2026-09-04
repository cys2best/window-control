import { Platform } from "react-native";

export const theme = {
  color: {
    accent: "#f2916f",       // coral
    accentInk: "#c96a48",    // link/hover
    bg: "#eae7e3",           // warm app ground
    screen: "#f2f0ed",       // phone-frame screen
    card: "#ffffff",
    cardActive: "#fdeee7",
    streamBg: "#141110",     // stream screen only
    glass: "rgba(250,248,246,0.9)",  // overlays on the stream screen
    text: "#1c1a19",         // ink
    textMuted: "rgba(28,26,25,0.5)",
    error: "#c2452a",
    errorBg: "#fbe5de",
  },
  net: {
    connected:    { dot: "#3f9d6d", chipBg: "#e6f2ea", chipFg: "#2f7a54" },
    connecting:   { dot: "#e0a52c", chipBg: "#fbf0dc", chipFg: "#8a6410" },
    disconnected: { dot: "#c2452a", chipBg: "#fbe5de", chipFg: "#a8391f" },
  },
  radius: { card: 22, input: 18, pill: 999, sm: 16 },
  font: {
    regular: "Archivo_400Regular",
    medium: "Archivo_500Medium",
    semibold: "Archivo_600SemiBold",
    bold: "Archivo_700Bold",
    mono: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" })!,
  },
} as const;
