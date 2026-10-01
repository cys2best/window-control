// jest-environment-jsdom does not expose a `fetch` global (jsdom has never
// implemented it). Individual test files that need to assert on fetch calls
// stub it themselves (see client.test.ts, pairing.test.ts). This fallback
// only fills the gap for tests that don't care about fetch's result and just
// need calling it to not throw synchronously, e.g. ServerContext's host probe.
if (typeof global.fetch !== "function") {
  global.fetch = jest.fn(() => Promise.reject(new Error("fetch not mocked in test")));
}
