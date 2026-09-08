import React, { createContext, useContext, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { makeClient } from "./client";
import { classifyHostRoute, probeHost, type HostReachability } from "./hostProbe";
import { normalizeBase } from "./urls";
import { authIdentityFromToken, isJwtExpired, type AuthIdentity } from "./supabaseAuth";
import type { SecureStorageAdapter } from "./storage";
import {
  DEFAULT_STREAM_PREFERENCES,
  parseStreamPreferences,
  type StreamPreferences,
} from "./preferences";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  authToken: string | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  clearAuth: () => Promise<void>;
  identity: AuthIdentity | null;
  preferences: StreamPreferences;
  updatePreferences: (patch: Partial<StreamPreferences>) => Promise<void>;
  ready: boolean;
  hostReachability: HostReachability;
  supabaseUrl: string;
  supabaseAnonKey: string;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const TOKEN_KEY = "wc_auth_token";
const PREFERENCES_KEY = "wc_stream_preferences";

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
      (typeof window !== "undefined" && window.location?.origin && window.location.origin !== "null" ? window.location.origin : "") ||
      "").replace(/\/+$/, "");
  const [base, setBaseState] = useState<string | null>(defaultBase || null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [preferences, setPreferences] = useState<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const preferencesRef = useRef<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const [hostReachability, setHostReachability] = useState<HostReachability>(() => ({
    state: "checking",
    route: base ? classifyHostRoute(base) : "lan",
    host: base ? new URL(base).host : "",
    rttMs: null,
  }));

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
    plainStorage.getItem(PREFERENCES_KEY)
      .then((v) => {
        const loaded = parseStreamPreferences(v);
        preferencesRef.current = loaded;
        setPreferences(loaded);
      })
      .finally(() => setPreferencesLoaded(true));
    secureStorage.getItem(TOKEN_KEY)
      .then((v) => {
        if (v) {
          if (isJwtExpired(v)) {
            secureStorage.deleteItem(TOKEN_KEY);
            setAuthTokenState(null);
          } else {
            setAuthTokenState(v);
          }
        }
      })
      .finally(() => setTokenLoaded(true));
  }, [plainStorage, secureStorage, defaultBase]);

  const clearAuth = useCallback(async () => {
    await secureStorage.deleteItem(TOKEN_KEY);
    setAuthTokenState(null);
  }, [secureStorage]);

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
    return makeClient(norm, token || null, clearAuth);
  }, [plainStorage, secureStorage, clearAuth]);

  const updatePreferences = useCallback(async (patch: Partial<StreamPreferences>) => {
    const next = { ...preferencesRef.current, ...patch };
    preferencesRef.current = next;
    setPreferences(next);
    await plainStorage.setItem(PREFERENCES_KEY, JSON.stringify(next));
  }, [plainStorage]);

  // Support Supabase email confirmation / magic link / OAuth redirects containing tokens in hash or query
  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      let token: string | null = null;
      if (window.location?.hash) {
        const hash = window.location.hash.replace(/^#/, "");
        const hashParams = new URLSearchParams(hash);
        token = hashParams.get("access_token");
      }
      if (!token && window.location?.search) {
        const searchParams = new URLSearchParams(window.location.search);
        token = searchParams.get("access_token");
      }
      if (token) {
        const targetBase =
          (window.location?.origin && window.location.origin !== "null"
            ? window.location.origin
            : "") || defaultBase;
        setServer(targetBase, token).then(() => {
          try {
            window.history.replaceState(null, "", window.location.pathname);
          } catch {}
        });
      }
    } catch {}
  }, [setServer, defaultBase]);

  const client = useMemo(
    () => (base ? makeClient(base, authToken, clearAuth) : null),
    [base, authToken, clearAuth]
  );
  const identity = useMemo(() => authIdentityFromToken(authToken), [authToken]);

  const [supabaseUrl, setSupabaseUrl] = useState("");
  const [supabaseAnonKey, setSupabaseAnonKey] = useState("");

  useEffect(() => {
    if (typeof fetch === "undefined" && typeof globalThis.fetch === "undefined") return;
    const fetchFn = typeof fetch !== "undefined" ? fetch : globalThis.fetch;
    const target = base || (typeof window !== "undefined" && window.location?.origin && window.location.origin !== "null" ? window.location.origin : "");
    if (!target) return;
    fetchFn(`${target}/auth/config`)
      .then((r) => r.json())
      .then((cfg) => {
        setSupabaseUrl(cfg.supabase_url || "");
        setSupabaseAnonKey(cfg.supabase_anon_key || "");
      })
      .catch(() => {});
  }, [base]);

  useEffect(() => {
    if (!base) return;
    if (typeof fetch === "undefined" && typeof globalThis.fetch === "undefined") return;
    let current = true;
    const check = () => {
      probeHost(base).then((result) => {
        if (current) setHostReachability(result);
      });
    };
    setHostReachability({
      state: "checking",
      route: classifyHostRoute(base),
      host: new URL(base).host,
      rttMs: null,
    });
    check();
    const interval = setInterval(check, 30_000);
    return () => {
      current = false;
      clearInterval(interval);
    };
  }, [base]);

  const ready = baseLoaded && tokenLoaded && preferencesLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, client, setServer, clearAuth, identity, preferences, updatePreferences, ready, hostReachability, supabaseUrl, supabaseAnonKey }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
