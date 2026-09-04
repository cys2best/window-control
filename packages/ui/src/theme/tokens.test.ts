import { theme } from "./tokens";

test("EmuCtrl v3 tokens carry the spec values", () => {
  expect(theme.color.accent).toBe("#f2916f");
  expect(theme.color.bg).toBe("#eae7e3");
  expect(theme.color.card).toBe("#ffffff");
  expect(theme.color.streamBg).toBe("#141110");
  expect(theme.color.error).toBe("#c2452a");
  expect(theme.radius.pill).toBe(999);
  expect(theme.radius.card).toBe(22);
  expect(theme.font.bold).toBe("Archivo_700Bold");
  expect(theme.net.connected.dot).toBe("#3f9d6d");
  expect(theme.net.disconnected.chipBg).toBe("#fbe5de");
});
