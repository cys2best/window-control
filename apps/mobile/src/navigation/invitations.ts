import { useCallback, useEffect, useRef } from "react";
import * as Linking from "expo-linking";
import { parseRemoteInvite } from "@wc/core";

export function parseInvitation(url: string, trustedOrigin: string): ReturnType<typeof parseRemoteInvite> | null {
  try { return parseRemoteInvite(url, trustedOrigin); } catch { return null; }
}

type InvitationNavigation = { isReady: () => boolean; navigate: (name: "Pair", params: { invitation: string }) => void };
// Install the listener while the provider hydrates; navigation may become
// ready later. Deduplicate by parsed identity, independent of URL spelling.
export function useInvitationRouting(navigation: InvitationNavigation, ready: boolean, trustedOrigin?: string): () => void {
  const pending = useRef<string[]>([]);
  const seen = useRef(new Set<string>());
  const readyRef = useRef(ready);
  readyRef.current = ready;
  const flush = useCallback(() => {
    if (!readyRef.current || !navigation.isReady()) return;
    for (const invitation of pending.current.splice(0)) navigation.navigate("Pair", { invitation });
  }, [navigation]);
  useEffect(() => {
    let alive = true;
    const accept = (url: string | null) => {
      if (!alive || !url || !trustedOrigin) return;
      const invitation = parseInvitation(url, trustedOrigin);
      if (!invitation) return;
      const key = `${invitation.serviceUrl}#${invitation.handle}`;
      if (seen.current.has(key)) return;
      seen.current.add(key); pending.current.push(url); flush();
    };
    const subscription = Linking.addEventListener("url", event => accept(event.url));
    void Linking.getInitialURL().then(accept).catch(() => {});
    return () => { alive = false; subscription.remove(); };
  }, [trustedOrigin, flush]);
  useEffect(flush, [ready, flush]);
  return flush;
}
