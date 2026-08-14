import "react-native-gesture-handler";
import React from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { NavigationContainer } from "@react-navigation/native";
import { useFonts, Archivo_400Regular, Archivo_500Medium, Archivo_600SemiBold, Archivo_700Bold } from "@expo-google-fonts/archivo";
import { View } from "react-native";
import { ServerProvider, useServer } from "./src/api/ServerContext";
import { RootNavigator } from "./src/navigation/Root";
import { theme } from "./src/theme/tokens";

function Gate() {
  const { ready } = useServer();
  if (!ready) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return <NavigationContainer><RootNavigator /></NavigationContainer>;
}

export default function App() {
  const [fontsLoaded] = useFonts({ Archivo_400Regular, Archivo_500Medium, Archivo_600SemiBold, Archivo_700Bold });
  if (!fontsLoaded) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ServerProvider><Gate /></ServerProvider>
    </GestureHandlerRootView>
  );
}
