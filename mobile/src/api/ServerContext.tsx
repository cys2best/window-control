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
    try {
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
          // The same physical server presents as two different "hosts"
          // depending on which network resolved it (the Tailscale IP vs.
          // the public tunnel hostname) -- that's the normal, expected
          // outcome of this discovery design, not an edge case. clearAll()
          // would nuke cookies for BOTH hosts, forcing a re-login every
          // single network switch (in both directions) even though it's
          // the identical server both times. Scope the clear to only the
          // host actually being left: enumerate its cookies via get() and
          // remove them individually with clearByName() (both are
          // supported cross-platform despite the upstream typings'
          // "iOS only" comment on clearByName -- confirmed against the
          // Android native module source, which implements it too).
          try {
            const leaving = await CookieManager.get(cachedBase);
            await Promise.all(
              Object.keys(leaving).map((name) => CookieManager.clearByName(cachedBase, name))
            );
          } catch {}
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
    } catch {
      // Any unexpected throw here (AsyncStorage rejecting, a malformed
      // /server-info response tripping normalizeBase(), etc.) must not
      // leave `discovering` stuck true forever -- Connecting's Retry
      // button only shows in the !discovering branch, and this function is
      // invoked bare (not awaited) from a mount effect and from Retry's
      // onPress, so an uncaught throw here is also an unhandled rejection.
      // Treat it the same as "nothing reachable", and make sure Gate isn't
      // blocked either, in case the throw happened before the early
      // setReady(true) above ran.
      setReady(true);
      setServerFound(false);
      setNeedsLogin(false);
    } finally {
      setDiscovering(false);
    }
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
