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
export type TargetUpdateOptions = { isCurrent?: () => boolean };

type Ctx = {
  base: string | null;
  target: ServerTarget | null;
  // The device token issued by this host when the device was paired.
  authToken: string | null;
  // Whether the host accepts this device: true with a valid token or on
  // loopback, false once the host has said no, null until it has answered.
  paired: boolean | null;
  client: ApiClient | null;
  setServer: (base: string, token: string, options?: TargetUpdateOptions) => Promise<ApiClient>;
  setTarget: (target: ServerTarget, token: string, options?: TargetUpdateOptions) => Promise<ApiClient>;
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
    try {
      await ordered(key, async () => {
        if (await secureStorage.getItem(key) === capturedToken) await secureStorage.deleteItem(key);
      });
    } finally {
      if (activeGeneration.current === n) {
        clientRef.current?.dispose();
        clientRef.current = null;
        setClient(null);
        setAuthTokenState(null);
        setPaired(false);
      }
    }
  }, [secureStorage, ordered]);
  const publish = useCallback((selection: ServerTarget | null, token: string | null, n: number, pairedValue: boolean | null): ApiClient | null => {
    if (generation.current !== n) return null;
    const unauthorized = () => { if (selection) void clearCaptured(selection, n, token).catch(() => {}); };
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

  const setTarget = useCallback(async (selection: ServerTarget, token: string, options?: TargetUpdateOptions): Promise<ApiClient> => {
    let normalized: ServerTarget;
    if (selection.kind === "remote") {
      if (!remoteOptions) throw new Error("remote service is not configured");
      const serviceUrl = trustedServiceUrl(selection.serviceUrl, remoteOptions.trustedOrigin, remoteOptions.allowInsecureLocalhost);
      remoteDeviceTokenKey(serviceUrl, selection.installationId);
      if (!token) throw new Error("missing remote token");
      normalized = { kind: "remote", serviceUrl, installationId: selection.installationId };
    } else normalized = { kind: "local", base: normalizeBase(selection.base) };
    const n = ++generation.current;
    const assertCurrent = () => {
      if (generation.current !== n || options?.isCurrent?.() === false) throw new Error("target changed");
    };
    // Keep each snapshot and its rollback inside the queue that owns the key.
    // A canceled write may already have reached storage before it resolves.
    const persist = async (storage: SecureStorageAdapter, key: string, value: string | null, undo: Array<() => Promise<void>>, tokenStage = false) => {
      const beforeWrite = () => {
        // Legacy calls may already have issued independent token writes.
        // Owned pairing calls also fence generation before that boundary.
        if (!tokenStage || options?.isCurrent) assertCurrent();
      };
      beforeWrite();
      const previous = await storage.getItem(key);
      beforeWrite();
      undo.push(() => previous === null ? storage.deleteItem(key) : storage.setItem(key, previous));
      await (value === null ? storage.deleteItem(key) : storage.setItem(key, value));
      assertCurrent();
    };
    const rollback = async (undo: Array<() => Promise<void>>) => {
      // Attempt every restoration even if one storage adapter rejects a write.
      const restored = await Promise.allSettled(undo.reverse().map(restore => restore()));
      const failed = restored.find(result => result.status === "rejected");
      if (failed?.status === "rejected") throw failed.reason;
    };
    try {
      let owned!: ApiClient;
      await ordered(tokenKey(normalized), async () => {
        const tokenUndo: Array<() => Promise<void>> = [];
        try {
          await persist(secureStorage, tokenKey(normalized), token || null, tokenUndo, true);
          // All local/remote markers share this queue through publication or
          // rollback. Different-token writes remain independent until here.
          await ordered(REMOTE_KEY, async () => {
            const markerUndo: Array<() => Promise<void>> = [];
            try {
              if (normalized.kind === "local") {
                const baseUndo: Array<() => Promise<void>> = [];
                markerUndo.push(() => ordered(BASE_KEY, () => rollback(baseUndo)));
                await ordered(BASE_KEY, () => persist(plainStorage, BASE_KEY, normalized.base, baseUndo));
              }
              await persist(plainStorage, REMOTE_KEY, normalized.kind === "remote" ? JSON.stringify(normalized) : null, markerUndo);
              assertCurrent();
              owned = publish(normalized, token || null, n, token ? true : null)!;
              setBaseLoaded(true); setTokenLoaded(true);
            } catch (error) {
              await rollback(markerUndo);
              throw error;
            }
          });
        } catch (error) {
          await rollback(tokenUndo);
          throw error;
        }
      });
      return owned;
    } catch (error) {
      if (generation.current === n && !baseLoaded) setLoadRetry(value => value + 1);
      throw error;
    }
  }, [plainStorage, secureStorage, remoteOptions, publish, ordered, baseLoaded]);
  const setServer = useCallback((url: string, token: string, options?: TargetUpdateOptions) => setTarget({ kind: "local", base: url }, token, options), [setTarget]);
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
