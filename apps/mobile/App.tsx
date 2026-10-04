import "react-native-gesture-handler";
import React, { useEffect } from "react";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { NavigationContainer } from "@react-navigation/native";
import { useFonts } from "expo-font";
import {
  SpaceGrotesk_400Regular,
  SpaceGrotesk_500Medium,
  SpaceGrotesk_600SemiBold,
  SpaceGrotesk_700Bold,
} from "@expo-google-fonts/space-grotesk";
import {
  JetBrainsMono_400Regular,
  JetBrainsMono_500Medium,
  JetBrainsMono_700Bold,
} from "@expo-google-fonts/jetbrains-mono";
import { View } from "react-native";
import * as ScreenOrientation from "expo-screen-orientation";
import * as Crypto from "expo-crypto";
import { ServerProvider, useServer } from "@wc/core";
import { plainStorage, secureStorage } from "./src/platform/storage";
import { RootNavigator } from "./src/navigation/Root";
import { theme } from "@wc/ui";

const remoteOrigin = process.env.EXPO_PUBLIC_REMOTE_SERVICE_URL;
const remoteOptions = remoteOrigin ? { trustedOrigin: remoteOrigin, requestId: () => Crypto.randomUUID() } : undefined;

function Gate() {
  const { ready } = useServer();
  if (!ready) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return <NavigationContainer><RootNavigator /></NavigationContainer>;
}

export default function App() {
  const [fontsLoaded] = useFonts({
    SpaceGrotesk_400Regular,
    SpaceGrotesk_500Medium,
    SpaceGrotesk_600SemiBold,
    SpaceGrotesk_700Bold,
    JetBrainsMono_400Regular,
    JetBrainsMono_500Medium,
    JetBrainsMono_700Bold,
  });
  useEffect(() => { ScreenOrientation.lockAsync(ScreenOrientation.OrientationLock.PORTRAIT_UP); }, []);
  if (!fontsLoaded) return <View style={{ flex: 1, backgroundColor: theme.color.bg }} />;
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage} remoteOptions={remoteOptions}><Gate /></ServerProvider>
    </GestureHandlerRootView>
  );
}
