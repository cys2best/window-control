import { theme } from "./tokens";

test("theme exposes only the locked dark EmuCtrl palette", () => {
  expect(theme.color).toMatchObject({
    bg: "#06070b",
    surface: "#090a0f",
    surfaceRaised: "#13161f",
    border: "#222738",
    text: "#E6EAF2",
    textMuted: "#7A8496",
    textDim: "#5C6679",
    accent: "#00E5FF",
    live: "#FF5722",
    telemetry: "#10B981",
  });
  expect(theme.font.regular).toBe("SpaceGrotesk_400Regular");
  expect(theme.font.mono).toBe("JetBrainsMono_400Regular");
  expect(JSON.stringify(theme)).not.toMatch(/f2916f|eae7e3|Archivo/i);
});
