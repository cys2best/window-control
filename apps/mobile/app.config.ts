import type { ConfigContext, ExpoConfig } from "expo/config";

export default function configure({ config }: ConfigContext): ExpoConfig {
  const configured = process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL;
  if (!configured) return config as ExpoConfig;
  let hostname: string;
  try {
    const url = new URL(configured);
    if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) throw new Error();
    hostname = url.hostname;
  } catch { throw new Error("Invalid remote service origin"); }
  return {
    ...config,
    ios: { ...config.ios, associatedDomains: Array.from(new Set([...(config.ios?.associatedDomains ?? []), `applinks:${hostname}`])) },
  } as ExpoConfig;
}
