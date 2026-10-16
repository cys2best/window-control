import { Platform } from "react-native";
import { theme } from "./tokens";

test("theme exposes only the locked v2 Nendojo dark palette", () => {
  expect(theme.color).toMatchObject({
    bg: "#141413",
    screen: "#1a1a18",
    surface: "#1a1a18",
    surfaceRaised: "#1e1d1b",
    border: "#393836",
    hairline: "#292826",
    text: "#faf9f5",
    textMuted: "#87867f",
    accent: "#faf9f5",
    onAccent: "#141413",
    live: "#e8895c",
    telemetry: "#faf9f5",
  });
  expect(theme.radius).toMatchObject({ card: 2, input: 2, sm: 2 });
  expect(theme.font.regular).toBe(Platform.OS === "web" ? "Be Vietnam Pro" : "BeVietnamPro_400Regular");
  expect(theme.font.bold).toBe(Platform.OS === "web" ? "Be Vietnam Pro" : "BeVietnamPro_500Medium");
  expect(theme.font.mono).toBe(Platform.OS === "web" ? "JetBrains Mono" : "JetBrainsMono_400Regular");
  // No v1 (cyan/mint/Space Grotesk) value survives.
  expect(JSON.stringify(theme)).not.toMatch(/00E5FF|10B981|FF5722|06070b|222738|Space ?Grotesk|f2916f|eae7e3|Archivo/i);
});
