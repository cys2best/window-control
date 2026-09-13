export type QualitySelection = "auto" | "480" | "720" | "1080" | "1440";

export type StreamPreferences = {
  quality: QualitySelection;
  showHudOnConnect: boolean;
  haptics: boolean;
  hideRailWhilePlaying: boolean;
};

export const DEFAULT_STREAM_PREFERENCES: StreamPreferences = {
  quality: "auto",
  showHudOnConnect: false,
  haptics: true,
  hideRailWhilePlaying: false,
};

const QUALITY_SELECTIONS: QualitySelection[] = ["auto", "480", "720", "1080", "1440"];

export function parseStreamPreferences(value: string | null): StreamPreferences {
  try {
    const parsed: unknown = JSON.parse(value ?? "");
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_STREAM_PREFERENCES };
    const preferences = parsed as Record<string, unknown>;
    return {
      quality: typeof preferences.quality === "string" && QUALITY_SELECTIONS.includes(preferences.quality as QualitySelection)
        ? preferences.quality as QualitySelection
        : DEFAULT_STREAM_PREFERENCES.quality,
      showHudOnConnect: typeof preferences.showHudOnConnect === "boolean"
        ? preferences.showHudOnConnect
        : DEFAULT_STREAM_PREFERENCES.showHudOnConnect,
      haptics: typeof preferences.haptics === "boolean" ? preferences.haptics : DEFAULT_STREAM_PREFERENCES.haptics,
      hideRailWhilePlaying: typeof preferences.hideRailWhilePlaying === "boolean"
        ? preferences.hideRailWhilePlaying
        : DEFAULT_STREAM_PREFERENCES.hideRailWhilePlaying,
    };
  } catch {
    return { ...DEFAULT_STREAM_PREFERENCES };
  }
}
