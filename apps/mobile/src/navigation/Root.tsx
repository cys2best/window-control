import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { RTCPeerConnection } from "react-native-webrtc";
import * as Haptics from "expo-haptics";
import { Account, Pair, InstanceList, Stream } from "@wc/ui";
import { VideoView } from "../platform/VideoView";
import { useServer } from "@wc/core";

const Stack = createNativeStackNavigator();

function StreamScreen(props: any) {
  return (
    <Stream
      {...props}
      RTCImpl={RTCPeerConnection}
      VideoView={VideoView}
      performHaptic={() => { void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light); }}
    />
  );
}

export function RootNavigator() {
  const { authToken } = useServer();
  // A saved token goes to the list even if the host is unreachable right
  // now; a revoked one is caught there by the 401 handler.
  const initialRoute = authToken ? "InstanceList" : "Pair";
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName={initialRoute}>
      <Stack.Screen name="Pair" component={Pair} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Account" component={Account} />
      <Stack.Screen name="Stream" component={StreamScreen} />
    </Stack.Navigator>
  );
}
