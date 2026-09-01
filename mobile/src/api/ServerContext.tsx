import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { makeClient } from "./client";
import { normalizeBase } from "./urls";

type ApiClient = ReturnType<typeof makeClient>;

type Ctx = {
  base: string | null;
  authToken: string | null;
  client: ApiClient | null;
  setServer: (base: string, token: string) => Promise<ApiClient>;
  ready: boolean;
};
const ServerCtx = createContext<Ctx | null>(null);
const BASE_KEY = "wc_base";
const TOKEN_KEY = "wc_auth_token";

export function ServerProvider({ children }: { children: React.ReactNode }) {
  const [base, setBaseState] = useState<string | null>(null);
  const [authToken, setAuthTokenState] = useState<string | null>(null);
  const [baseLoaded, setBaseLoaded] = useState(false);
  const [tokenLoaded, setTokenLoaded] = useState(false);

  useEffect(() => {
    AsyncStorage.getItem(BASE_KEY)
      .then((v) => { if (v) setBaseState(v); })
      .finally(() => setBaseLoaded(true));
    SecureStore.getItemAsync(TOKEN_KEY)
      .then((v) => { if (v) setAuthTokenState(v); })
      .finally(() => setTokenLoaded(true));
  }, []);

  const setServer = useCallback(async (url: string, token: string) => {
    const norm = normalizeBase(url);
    await AsyncStorage.setItem(BASE_KEY, norm);
    if (token) {
      await SecureStore.setItemAsync(TOKEN_KEY, token);
    } else {
      await SecureStore.deleteItemAsync(TOKEN_KEY);
    }
    setBaseState(norm);
    setAuthTokenState(token || null);
    return makeClient(norm, token || null);
  }, []);

  const client = useMemo(
    () => (base ? makeClient(base, authToken) : null),
    [base, authToken]
  );
  const ready = baseLoaded && tokenLoaded;
  return (
    <ServerCtx.Provider value={{ base, authToken, client, setServer, ready }}>
      {children}
    </ServerCtx.Provider>
  );
}

export function useServer(): Ctx {
  const c = useContext(ServerCtx);
  if (!c) throw new Error("useServer outside ServerProvider");
  return c;
}
