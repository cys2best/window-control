import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { ServerSetup } from "../screens/ServerSetup";
import { Login } from "../screens/Login";
import { InstanceList } from "../screens/InstanceList";
import { Stream } from "../screens/Stream";
import { useServer } from "../api/ServerContext";

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  const { base, authToken } = useServer();
  const initialRoute = !base ? "ServerSetup" : !authToken ? "Login" : "InstanceList";
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}
      initialRouteName={initialRoute}>
      <Stack.Screen name="ServerSetup" component={ServerSetup} />
      <Stack.Screen name="Login" component={Login} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={Stream} />
    </Stack.Navigator>
  );
}
