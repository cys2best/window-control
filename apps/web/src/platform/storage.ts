import type { SecureStorageAdapter } from "@wc/core";

function makeLocalStorageAdapter(): SecureStorageAdapter {
  return {
    getItem: async (key) => (typeof window === "undefined" ? null : window.localStorage.getItem(key)),
    setItem: async (key, value) => { if (typeof window !== "undefined") window.localStorage.setItem(key, value); },
    deleteItem: async (key) => { if (typeof window !== "undefined") window.localStorage.removeItem(key); },
  };
}

export const plainStorage = makeLocalStorageAdapter();
export const secureStorage = makeLocalStorageAdapter();
