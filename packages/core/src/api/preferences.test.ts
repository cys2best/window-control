function preferencesApi(): typeof import("./preferences") {
  return require("./preferences");
}

describe("stream preferences", () => {
  test("round trips every stream preference", () => {
    const { parseStreamPreferences } = preferencesApi();
    const stored = JSON.stringify({
      quality: "1080",
      showHudOnConnect: true,
      haptics: false,
      hideRailWhilePlaying: false,
    });

    expect(parseStreamPreferences(stored)).toEqual({
      quality: "1080",
      showHudOnConnect: true,
      haptics: false,
      hideRailWhilePlaying: false,
    });
  });

  test("uses defaults when persisted preferences are malformed", () => {
    const { parseStreamPreferences, DEFAULT_STREAM_PREFERENCES } = preferencesApi();

    expect(parseStreamPreferences("not-json")).toEqual(DEFAULT_STREAM_PREFERENCES);
  });

  test("ignores unknown and incorrectly typed persisted preference keys", () => {
    const { parseStreamPreferences } = preferencesApi();
    const stored = JSON.stringify({
      quality: "ultra",
      showHudOnConnect: "yes",
      haptics: false,
      hideRailWhilePlaying: 0,
      experimentalMode: true,
    });

    expect(parseStreamPreferences(stored)).toEqual({
      quality: "auto",
      showHudOnConnect: false,
      haptics: false,
      hideRailWhilePlaying: false,
    });
  });
});
