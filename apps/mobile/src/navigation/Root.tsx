import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { RTCPeerConnection } from "react-native-webrtc";
import { Login, InstanceList, Stream } from "@wc/ui";
import { VideoView } from "../platform/VideoView";
import { useServer } from "@wc/core";

const Stack = createNativeStackNavigator();

function StreamScreen(props: any) {
  return <Stream {...props} RTCImpl={RTCPeerConnection} VideoView={VideoView} />;
}

export function RootNavigator() {
  const { authToken } = useServer();
  const initialRoute = !authToken ? "Login" : "InstanceList";
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName={initialRoute}>
      <Stack.Screen name="Login" component={Login} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={StreamScreen} />
    </Stack.Navigator>
  );
}
