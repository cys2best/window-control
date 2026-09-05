import { plainStorage, secureStorage } from "./storage";

beforeEach(() => { window.localStorage.clear(); });

test("plainStorage round-trips through window.localStorage", async () => {
  await plainStorage.setItem("k", "v");
  expect(await plainStorage.getItem("k")).toBe("v");
  await plainStorage.deleteItem("k");
  expect(await plainStorage.getItem("k")).toBeNull();
});

test("secureStorage round-trips through window.localStorage", async () => {
  await secureStorage.setItem("k", "v");
  expect(await secureStorage.getItem("k")).toBe("v");
});
