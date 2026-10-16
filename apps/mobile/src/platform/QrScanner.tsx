import React, { useEffect, useRef } from "react";
import { Pressable, Text, View } from "react-native";
import { CameraView, useCameraPermissions } from "expo-camera";
import { theme } from "@wc/ui";

// Reads the QR code the PC shows for pairing; the pairing screen decides
// whether the text is an address or an invite link.
export function QrScanner({ onScanned, onClose }: { onScanned: (data: string) => void; onClose: () => void }) {
  const [permission, requestPermission] = useCameraPermissions();
  const reported = useRef(false);
  useEffect(() => {
    if (permission && !permission.granted && permission.canAskAgain) void requestPermission();
  }, [permission, requestPermission]);

  const close = (
    <Pressable accessibilityRole="button" onPress={onClose}
      style={{ position: "absolute", bottom: 48, alignSelf: "center", paddingHorizontal: 24, paddingVertical: 12,
        borderRadius: theme.radius.input, backgroundColor: theme.color.surface }}>
      <Text style={{ color: theme.color.text, fontFamily: theme.font.semibold }}>Close</Text>
    </Pressable>
  );
  if (!permission?.granted) {
    return (
      <View style={{ flex: 1, justifyContent: "center", padding: 24 }}>
        <Text style={{ color: theme.color.textMuted, fontFamily: theme.font.regular, textAlign: "center" }}>
          {permission ? "Camera access is off. Allow it in Settings, or paste the pairing link instead." : ""}
        </Text>
        {close}
      </View>
    );
  }
  return (
    <View style={{ flex: 1 }}>
      <CameraView style={{ flex: 1 }} facing="back" barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
        onBarcodeScanned={({ data }) => {
          if (reported.current) return;
          reported.current = true;
          onScanned(data);
        }} />
      {close}
    </View>
  );
}
