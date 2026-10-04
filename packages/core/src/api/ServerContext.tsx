import React, { createContext, useContext, useEffect, useState, useCallback, useRef } from "react";
import { makeClient, type LocalApiClient } from "./client";
import { probeHost, type HostReachability } from "./hostProbe";
import { deviceTokenKey } from "./pairing";
import { normalizeBase } from "./urls";
import { remoteDeviceTokenKey, type ServerTarget } from "./target";
import { connectRemoteClient, type RemoteApiClient, type RemoteClientOptions } from "../remote/client";
import { trustedServiceUrl } from "../remote/protocol";
import type { SecureStorageAdapter } from "./storage";
import {
  DEFAULT_STREAM_PREFERENCES,
  parseStreamPreferences,
  type StreamPreferences,
} from "./preferences";

export type ApiClient = LocalApiClient | RemoteApiClient;

type Ctx = {
  base: string | null;
  target: ServerTarget | null;
  // The device token issued by this host when the device was paired.
  authToken: string | null;
  // Whether the host accepts this device: true with a valid token or on
  // loopback, false once the host has said no, null until it has answered.
  paired: boolean | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  setTarget: (target: ServerTarget, token: string) => Promise<ApiClient>;
  clearAuth: () => Promise<void>;
  preferences: StreamPreferences;
  updatePreferences: (patch: Partial<StreamPreferences>) => Promise<void>;
  ready: boolean;
  hostReachability: HostReachability;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const REMOTE_KEY = "wc_remote_target";
const LEGACY_TOKEN_KEY = "wc_auth_token";
const PREFERENCES_KEY = "wc_stream_preferences";

export function ServerProvider({
  children,
  plainStorage,
  secureStorage,
  remoteOptions,
}: {
  children: React.ReactNode;
  plainStorage: SecureStorageAdapter;
  secureStorage: SecureStorageAdapter;
  remoteOptions?: RemoteClientOptions;
}) {
  const defaultBase =
    ((typeof process !== "undefined" &&
      (process.env?.EXPO_PUBLIC_API_URL || process.env?.NEXT_PUBLIC_API_URL)) ||
      (typeof window !== "undefined" && window.location?.origin && window.location.origin !== "null" ? window.location.origin : "") ||
      "").replace(/\/+$/, "");
  const [base, setBaseState] = useState<string | null>(defaultBase || null);
  const [target, setTargetState] = useState<ServerTarget | null>(defaultBase ? { kind: "local", base: defaultBase } : null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [paired, setPaired] = useState<boolean | null>(null);
  const [client, setClient] = useState<ApiClient | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [loadRetry, setLoadRetry] = useState(0);
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [preferencesLoaded, setPreferencesLoaded] = useState(false);
  const [preferences, setPreferences] = useState<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const preferencesRef = useRef<StreamPreferences>(DEFAULT_STREAM_PREFERENCES);
  const preferencesEdited = useRef(false);
  const targetRef = useRef<ServerTarget | null>(target);
  const clientRef = useRef<ApiClient | null>(null);
  // Attempts never reuse an ID; the active client keeps its own committed ID.
  const generation = useRef(0);
  const activeGeneration = useRef(0);
  const queues = useRef(new Map<string, Promise<void>>());
  const tokenKey = (value: ServerTarget) => value.kind === "local" ? deviceTokenKey(value.base) : remoteDeviceTokenKey(value.serviceUrl, value.installationId);
  const ordered = useCallback((key: string, work: () => Promise<void>) => {
    const prior = queues.current.get(key) ?? Promise.resolve();
    const next = prior.catch(() => {}).then(work);
    queues.current.set(key, next);
    void next.finally(() => { if (queues.current.get(key) === next) queues.current.delete(key); }).catch(() => {});
    return next;
  }, []);
  const [hostReachability, setHostReachability] = useState<HostReachability>(() => ({
    state: "checking",
    host: base ? new URL(base).host : "",
    rttMs: null,
    paired: null,
  }));

  const clearCaptured = useCallback(async (captured: ServerTarget, n: number, capturedToken: string | null) => {
    if (activeGeneration.current !== n) return;
    const key = tokenKey(captured);
    await ordered(key, async () => {
      if (await secureStorage.getItem(key) === capturedToken) await secureStorage.deleteItem(key);
    });
    if (activeGeneration.current === n) { setAuthTokenState(null); setPaired(false); }
  }, [secureStorage, ordered]);
  const publish = useCallback((selection: ServerTarget | null, token: string | null, n: number, pairedValue: boolean | null): ApiClient | null => {
    if (generation.current !== n) return null;
    const unauthorized = () => { if (selection) void clearCaptured(selection, n, token); };
    const owned = selection?.kind === "remote"
      ? connectRemoteClient(selection, token || "", unauthorized, remoteOptions!)
      : selection?.kind === "local" ? makeClient(selection.base, token, unauthorized) : null;
    clientRef.current?.dispose();
    clientRef.current = owned;
    activeGeneration.current = n;
    targetRef.current = selection;
    setTargetState(selection);
    setBaseState(selection?.kind === "local" ? selection.base : null);
    setAuthTokenState(token);
    setPaired(pairedValue);
    setClient(owned);
    return owned;
  }, [clearCaptured, remoteOptions]);

  useEffect(() => {
    let alive = true;
    const n = generation.current;
    void secureStorage.deleteItem(LEGACY_TOKEN_KEY).catch(() => {});
    void (async () => {
      const [baseResult, markerResult, prefResult] = await Promise.allSettled([
        plainStorage.getItem(BASE_KEY), plainStorage.getItem(REMOTE_KEY), plainStorage.getItem(PREFERENCES_KEY),
      ]);
      const loaded = parseStreamPreferences(prefResult.status === "fulfilled" ? prefResult.value : null);
      if (!alive) return;
      if (!preferencesEdited.current) { preferencesRef.current = loaded; setPreferences(loaded); }
      setPreferencesLoaded(true);
      if (generation.current !== n) return;
      const savedBase = baseResult.status === "fulfilled" ? baseResult.value || defaultBase : defaultBase;
      let selected: ServerTarget | null = savedBase ? { kind: "local", base: savedBase } : null;
      if (markerResult.status === "fulfilled" && markerResult.value && remoteOptions) {
        try {
          const remote = JSON.parse(markerResult.value) as Extract<ServerTarget, { kind: "remote" }>;
          if (remote.kind === "remote") {
            trustedServiceUrl(remote.serviceUrl, remoteOptions.trustedOrigin, remoteOptions.allowInsecureLocalhost);
            remoteDeviceTokenKey(remote.serviceUrl, remote.installationId);
            selected = remote;
          }
        } catch {}
      }
      let token: string | null = null;
      if (selected) { try { token = await secureStorage.getItem(tokenKey(selected)); } catch {} }
      if (!alive || generation.current !== n) return;
      if (selected?.kind === "remote" && !token) {
        selected = savedBase ? { kind: "local", base: savedBase } : null;
        if (selected) { try { token = await secureStorage.getItem(tokenKey(selected)); } catch {} }
      }
      if (!alive || generation.current !== n) return;
      publish(selected, token, n, null);
      setBaseLoaded(true); setTokenLoaded(true);
    })();
    return () => { alive = false; };
  }, [plainStorage, secureStorage, defaultBase, remoteOptions, publish, loadRetry]);

  useEffect(() => () => { generation.current++; activeGeneration.current = -1; clientRef.current?.dispose(); }, []);

  const setTarget = useCallback(async (selection: ServerTarget, token: string): Promise<ApiClient> => {
    let normalized: ServerTarget;
    if (selection.kind === "remote") {
      if (!remoteOptions) throw new Error("remote service is not configured");
      const serviceUrl = trustedServiceUrl(selection.serviceUrl, remoteOptions.trustedOrigin, remoteOptions.allowInsecureLocalhost);
      remoteDeviceTokenKey(serviceUrl, selection.installationId);
      if (!token) throw new Error("missing remote token");
      normalized = { kind: "remote", serviceUrl, installationId: selection.installationId };
    } else normalized = { kind: "local", base: normalizeBase(selection.base) };
    const n = ++generation.current;
    try {
      const key = tokenKey(normalized);
      await ordered(key, () => token ? secureStorage.setItem(key, token) : secureStorage.deleteItem(key));
      if (generation.current !== n) throw new Error("target changed");
      const writes: Promise<void>[] = [];
      if (normalized.kind === "local") {
        writes.push(ordered(BASE_KEY, () => plainStorage.setItem(BASE_KEY, normalized.base)));
        writes.push(ordered(REMOTE_KEY, () => plainStorage.deleteItem(REMOTE_KEY)));
      } else writes.push(ordered(REMOTE_KEY, () => plainStorage.setItem(REMOTE_KEY, JSON.stringify(normalized))));
      await Promise.all(writes);
      if (generation.current !== n) throw new Error("target changed");
      const owned = publish(normalized, token || null, n, token ? true : null)!;
      setBaseLoaded(true); setTokenLoaded(true);
      return owned;
    } catch (error) {
      if (generation.current === n && !baseLoaded) setLoadRetry(value => value + 1);
      throw error;
    }
  }, [plainStorage, secureStorage, remoteOptions, publish, ordered, baseLoaded]);
  const setServer = useCallback((url: string, token: string) => setTarget({ kind: "local", base: url }, token), [setTarget]);
  const clearAuth = useCallback(async () => { if (targetRef.current) await clearCaptured(targetRef.current, activeGeneration.current, authToken); }, [clearCaptured, authToken]);

  const updatePreferences = useCallback(async (patch: Partial<StreamPreferences>) => {
    const next = { ...preferencesRef.current, ...patch };
    preferencesEdited.current = true;
    preferencesRef.current = next;
    setPreferences(next);
    await ordered(PREFERENCES_KEY, () => plainStorage.setItem(PREFERENCES_KEY, JSON.stringify(next)));
  }, [plainStorage, ordered]);

  useEffect(() => {
    if (!target || !client || !tokenLoaded) return;
    const n = activeGeneration.current;
    let alive = true;
    const host = new URL(target.kind === "local" ? target.base : target.serviceUrl).host;
    const check = async () => {
      if (target.kind === "local") {
        if (typeof fetch === "undefined") return;
        const result = await probeHost(target.base, authToken);
        if (alive && activeGeneration.current === n) {
          setHostReachability(result);
          if (result.paired !== null) setPaired(result.paired);
        }
      } else {
        try {
          const rttMs = await client.ping();
          if (alive && activeGeneration.current === n) { setHostReachability({ state: "reachable", host, rttMs, paired: true }); setPaired(true); }
        } catch (error) {
          if (alive && activeGeneration.current === n) {
            const revoked = (error as { code?: string }).code === "not_paired";
            setHostReachability({ state: "unreachable", host, rttMs: null, paired: revoked ? false : null });
            if (revoked) setPaired(false);
          }
        }
      }
    };
    setHostReachability({ state: "checking", host, rttMs: null, paired: null });
    void check();
    const interval = setInterval(() => { void check(); }, 30_000);
    return () => { alive = false; clearInterval(interval); };
  }, [target, client, authToken, tokenLoaded]);

  const ready = baseLoaded && tokenLoaded && preferencesLoaded;
  return (
    <ServerCtx.Provider value={{ base, target, authToken, paired, client, setServer, setTarget, clearAuth, preferences, updatePreferences, ready, hostReachability }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
