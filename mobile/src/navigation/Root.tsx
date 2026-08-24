import React from "react";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { Connecting } from "../screens/Connecting";
import { Login } from "../screens/Login";
import { InstanceList } from "../screens/InstanceList";
import { Stream } from "../screens/Stream";

const Stack = createNativeStackNavigator();

export function RootNavigator() {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }} initialRouteName="Connecting">
      <Stack.Screen name="Connecting" component={Connecting} />
      <Stack.Screen name="Login" component={Login} />
      <Stack.Screen name="InstanceList" component={InstanceList} />
      <Stack.Screen name="Stream" component={Stream} />
    </Stack.Navigator>
  );
}
