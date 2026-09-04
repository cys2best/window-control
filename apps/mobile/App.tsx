import "react-native-gesture-handler";
import React, { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { NavigationContainer } from "@react-navigation/native";
import { useFonts, Archivo_400Regular, Archivo_500Medium, Archivo_600SemiBold, Archivo_700Bold } from "@expo-google-fonts/archivo";
import { View } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import { ServerProvider, useServer } from "@wc/core";
import { plainStorage, secureStorage } from "./src/platform/storage";
import { RootNavigator } from "./src/navigation/Root";
import { theme } from "./src/theme/tokens";

function Gate() {
  const { ready } = useServer();
  if (!ready) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return <NavigationContainer><RootNavigator /></NavigationContainer>;
}

export default function App() {
  const [fontsLoaded] = useFonts({ Archivo_400Regular, Archivo_500Medium, Archivo_600SemiBold, Archivo_700Bold });
  useEffect(() => { ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP); }, []);
  if (!fontsLoaded) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}><Gate /></ServerProvider>
    </GestureHandlerRootView>
  );
}
