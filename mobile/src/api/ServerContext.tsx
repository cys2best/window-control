import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";

type Ctx = {
  base: string | null;
  client: ReturnType<typeof makeClient> | null;
  setBase: (url: string) => Promise<void>;
  ready: boolean;
};
const ServerCtx = createContext<Ctx | null>(null);
const KEY = "wc_base";

export function ServerProvider({ children }: { children: React.ReactNode }) {
  const [base, setBaseState] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(KEY)
      .then((v) => { if (v) setBaseState(v); })
      .finally(() => setReady(true));
  }, []);

  const setBase = useCallback(async (url: string) => {
    const norm = normalizeBase(url);
    await AsyncStorage.setItem(KEY, norm);
    setBaseState(norm);
  }, []);

  const client = useMemo(() => (base ? makeClient(base) : null), [base]);
  return <ServerCtx.Provider value={{ base, client, setBase, ready }}>{children}</ServerCtx.Provider>;
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
