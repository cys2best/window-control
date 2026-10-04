import configure from "./app.config";
import app from "./app.json";
const config = app.expo as any;
afterEach(() => { delete process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL; });
test("unconfigured service keeps all existing app config and no associated domain", () => {
  delete process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL;
  expect(configure({ config } as any)).toEqual(config);
});
test("configured HTTPS hostname adds only its associated domain and preserves local transport settings", () => {
  process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL = "https://remote.example:443";
  expect(configure({ config } as any)).toEqual({ ...config, ios: { ...config.ios, associatedDomains: ["applinks:remote.example"] } });
});
test.each(["http://remote.example", "https://user:secret@remote.example", "https://remote.example/evil", "bad"])("rejects invalid configured origin %s", url => {
  process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL = url;
  expect(() => configure({ config } as any)).toThrow("Invalid remote service origin");
});
