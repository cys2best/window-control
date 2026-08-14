import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { ServerSetup } from "../screens/ServerSetup";
import { InstanceList } from "../screens/InstanceList";
import { Stream } from "../screens/Stream";
import { useServer } from "../api/ServerContext";

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  const { base } = useServer();
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}
      initialRouteName={base ? "InstanceList" : "ServerSetup"}>
      <Stack.Screen name="ServerSetup" component={ServerSetup} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={Stream} />
    </Stack.Navigator>
  );
}
