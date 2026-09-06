import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";
import type { SecureStorageAdapter } from "./storage";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  authToken: string | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  ready: boolean;
  supabaseUrl: string;
  supabaseAnonKey: string;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const TOKEN_KEY = "wc_auth_token";

export function ServerProvider({
  children,
  plainStorage,
  secureStorage,
}: {
  children: React.ReactNode;
  plainStorage: SecureStorageAdapter;
  secureStorage: SecureStorageAdapter;
}) {
  const defaultBase =
    ((typeof process !== "undefined" &&
      (process.env?.EXPO_PUBLIC_API_URL || process.env?.NEXT_PUBLIC_API_URL)) ||
      "").replace(/\/+$/, "");
  const [base, setBaseState] = useState<string | null>(defaultBase || null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);

  useEffect(() => {
    plainStorage.getItem(BASE_KEY)
      .then((v) => {
        if (v) {
          setBaseState(v);
        } else if (defaultBase) {
          setBaseState(defaultBase);
        }
      })
      .finally(() => setBaseLoaded(true));
    secureStorage.getItem(TOKEN_KEY)
      .then((v) => { if (v) setAuthTokenState(v); })
      .finally(() => setTokenLoaded(true));
  }, [plainStorage, secureStorage, defaultBase]);

  const setServer = useCallback(async (url: string, token: string) => {
    const norm = normalizeBase(url);
    await plainStorage.setItem(BASE_KEY, norm);
    if (token) {
      await secureStorage.setItem(TOKEN_KEY, token);
    } else {
      await secureStorage.deleteItem(TOKEN_KEY);
    }
    setBaseState(norm);
    setAuthTokenState(token || null);
    return makeClient(norm, token || null);
  }, [plainStorage, secureStorage]);

  const client = useMemo(
    () => (base ? makeClient(base, authToken) : null),
    [base, authToken]
  );

  const [supabaseUrl, setSupabaseUrl] = useState("");
  const [supabaseAnonKey, setSupabaseAnonKey] = useState("");

  useEffect(() => {
    if (!base) return;
    fetch(`${base}/auth/config`)
      .then((r) => r.json())
      .then((cfg) => {
        setSupabaseUrl(cfg.supabase_url || "");
        setSupabaseAnonKey(cfg.supabase_anon_key || "");
      })
      .catch(() => {});
  }, [base]);

  const ready = baseLoaded && tokenLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, client, setServer, ready, supabaseUrl, supabaseAnonKey }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
