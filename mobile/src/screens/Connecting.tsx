import React, { useEffect, useRef } from "react";
import { View, Text, ActivityIndicator } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { useServer } from "../api/ServerContext";

// Initial screen: no manual entry any more -- ServerContext resolves the
// server (local IP via /server-info bootstrap, or the baked public URL) on
// its own. This screen just reflects that discovery's state and routes on.
export function Connecting({ navigation }: { navigation: any }) {
  const { discovering, serverFound, needsLogin, rediscover } = useServer();
  const navigated = useRef(false);

  useEffect(() => {
    if (discovering || navigated.current) return;
    if (serverFound) {
      navigated.current = true;
      navigation.replace(needsLogin ? "Login" : "InstanceList");
    }
  }, [discovering, serverFound, needsLogin, navigation]);

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.screen, alignItems: "center",
      justifyContent: "center", padding: 24 }}>
      {discovering ? (
        <>
          <ActivityIndicator color={theme.color.accent} size="large" />
          <Text style={{ fontFamily: theme.font.medium, fontSize: 15, color: theme.color.textMuted,
            marginTop: 18 }}>
            Looking for your server…
          </Text>
        </>
      ) : !serverFound ? (
        <>
          <Text style={{ fontFamily: theme.font.bold, fontSize: 20, color: theme.color.text,
            marginBottom: 8, textAlign: "center" }}>
            Can't find server
          </Text>
          <Text style={{ fontFamily: theme.font.regular, fontSize: 14, lineHeight: 20,
            color: theme.color.textMuted, marginBottom: 24, textAlign: "center" }}>
            No response from your Tailscale network or the public tunnel. Confirm the host is
            reachable, then retry.
          </Text>
          <Button label="Retry" onPress={rediscover} />
        </>
      ) : null}
    </View>
  );
}
