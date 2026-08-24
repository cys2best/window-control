import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import Constants from "expo-constants";
import CookieManager from "@react-native-cookies/cookies";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";
import { discoverServer } from "./discovery";

type Ctx = {
  base: string | null;
  client: ReturnType<typeof makeClient> | null;
  setBase: (url: string) => Promise<void>;
  ready: boolean;
  discovering: boolean;
  serverFound: boolean;
  needsLogin: boolean;
  rediscover: () => Promise<void>;
};
const ServerCtx = createContext<Ctx | null>(null);
const KEY = "wc_base";

function hostOf(url: string): string | null {
  try { return new URL(url).host; } catch { return null; }
}

export function ServerProvider({ children }: { children: React.ReactNode }) {
  const [base, setBaseState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [discovering, setDiscovering] = useState(true);
  const [serverFound, setServerFound] = useState(false);
  const [needsLogin, setNeedsLogin] = useState(false);

  const setBase = useCallback(async (url: string) => {
    const norm = normalizeBase(url);
    await AsyncStorage.setItem(KEY, norm);
    setBaseState(norm);
  }, []);

  const runDiscovery = useCallback(async () => {
    setDiscovering(true);
    const cachedBase = await AsyncStorage.getItem(KEY);
    // Unblock App.tsx's Gate here, before the network probes below -- the
    // Connecting screen owns the "discovery in flight" spinner from this
    // point on, so nothing is gained by holding Gate's blank screen up
    // for the full (up to several seconds) discovery round-trip.
    setReady(true);
    const publicUrl: string | undefined = Constants.expoConfig?.extra?.publicUrl;
    const result = publicUrl ? await discoverServer(publicUrl, { cachedBase }) : null;

    if (result) {
      if (cachedBase && hostOf(cachedBase) !== hostOf(result.base)) {
        // Resolved base points at a different host than last time -- a
        // session cookie for the old host must not leak into the new one.
        try { await CookieManager.clearAll(); } catch {}
      }
      if (result.base !== cachedBase) {
        await setBase(result.base);
      } else {
        setBaseState(result.base);
      }
      setServerFound(true);
      setNeedsLogin(result.status === 401);
    } else {
      setServerFound(false);
      setNeedsLogin(false);
    }
    setDiscovering(false);
  }, [setBase]);

  useEffect(() => {
    runDiscovery();
  }, [runDiscovery]);

  const client = useMemo(() => (base ? makeClient(base) : null), [base]);
  return (
    <ServerCtx.Provider value={{
      base, client, setBase, ready, discovering, serverFound, needsLogin,
      rediscover: runDiscovery,
    }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
