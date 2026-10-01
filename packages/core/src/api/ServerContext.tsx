import React, { createContext, useContext, useEffect, useMemo, useState, useCallback, useRef } from "react";
import { makeClient } from "./client";
import { probeHost, type HostReachability } from "./hostProbe";
import { deviceTokenKey } from "./pairing";
import { normalizeBase } from "./urls";
import type { SecureStorageAdapter } from "./storage";
import {
  DEFAULT_STREAM_PREFERENCES,
  parseStreamPreferences,
  type StreamPreferences,
} from "./preferences";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  // The device token issued by this host when the device was paired.
  authToken: string | null;
  // Whether the host accepts this device: true with a valid token or on
  // loopback, false once the host has said no, null until it has answered.
  paired: boolean | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  clearAuth: () => Promise<void>;
  preferences: StreamPreferences;
  updatePreferences: (patch: Partial<StreamPreferences>) => Promise<void>;
  ready: boolean;
  hostReachability: HostReachability;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const LEGACY_TOKEN_KEY = "wc_auth_token";
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
  const [paired, setPaired] = useState<boolean | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [preferences, setPreferences] = useState<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const preferencesRef = useRef<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const baseRef = useRef<string | null>(base);
  baseRef.current = base;
  const [hostReachability, setHostReachability] = useState<HostReachability>(() => ({
    state: "checking",
    host: base ? new URL(base).host : "",
    rttMs: null,
    paired: null,
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
    // A Supabase session token saved before pairing replaced login.
    secureStorage.deleteItem(LEGACY_TOKEN_KEY).catch(() => {});
  }, [plainStorage, secureStorage, defaultBase]);

  // The device token belongs to one host, so it loads only once the base is
  // known and reloads when the base changes.
  useEffect(() => {
    if (!baseLoaded) return;
    if (!base) {
      setAuthTokenState(null);
      setTokenLoaded(true);
      return;
    }
    let current = true;
    secureStorage.getItem(deviceTokenKey(base))
      .then((v) => { if (current) setAuthTokenState(v || null); })
      .catch(() => { if (current) setAuthTokenState(null); })
      .finally(() => { if (current) setTokenLoaded(true); });
    return () => { current = false; };
  }, [baseLoaded, base, secureStorage]);

  const clearAuth = useCallback(async () => {
    const current = baseRef.current;
    if (current) await secureStorage.deleteItem(deviceTokenKey(current));
    setAuthTokenState(null);
    setPaired(false);
  }, [secureStorage]);

  const setServer = useCallback(async (url: string, token: string) => {
    const norm = normalizeBase(url);
    await plainStorage.setItem(BASE_KEY, norm);
    if (token) {
      await secureStorage.setItem(deviceTokenKey(norm), token);
    } else {
      await secureStorage.deleteItem(deviceTokenKey(norm));
    }
    setBaseState(norm);
    setAuthTokenState(token || null);
    setPaired(token ? true : null);
    return makeClient(norm, token || null, clearAuth);
  }, [plainStorage, secureStorage, clearAuth]);

  const updatePreferences = useCallback(async (patch: Partial<StreamPreferences>) => {
    const next = { ...preferencesRef.current, ...patch };
    preferencesRef.current = next;
    setPreferences(next);
    await plainStorage.setItem(PREFERENCES_KEY, JSON.stringify(next));
  }, [plainStorage]);

  const client = useMemo(
    () => (base ? makeClient(base, authToken, clearAuth) : null),
    [base, authToken, clearAuth]
  );

  useEffect(() => {
    if (!base || !tokenLoaded) return;
    if (typeof fetch === "undefined" && typeof globalThis.fetch === "undefined") return;
    let current = true;
    const check = () => {
      probeHost(base, authToken).then((result) => {
        if (!current) return;
        setHostReachability(result);
        // An unreachable host says nothing about pairing; keep what we knew.
        if (result.paired !== null) setPaired(result.paired);
      });
    };
    setHostReachability({
      state: "checking",
      host: new URL(base).host,
      rttMs: null,
      paired: null,
    });
    check();
    const interval = setInterval(check, 30_000);
    return () => {
      current = false;
      clearInterval(interval);
    };
  }, [base, authToken, tokenLoaded]);

  const ready = baseLoaded && tokenLoaded && preferencesLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, paired, client, setServer, clearAuth, preferences, updatePreferences, ready, hostReachability }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
