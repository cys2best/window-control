import React, { useEffect, useRef, useState } from "react";
import { View, Text, TextInput, KeyboardAvoidingView, Platform, ScrollView, Pressable } from "react-native";
import { theme } from "../theme/tokens";
import { Button } from "../components/Button";
import { BrandMark } from "../components/BrandMark";
import { NetChip } from "../components/NetChip";
import { normalizeBase, pairDevice, pairRemote, parseRemoteInvite, useServer, type RemoteClientOptions } from "@wc/core";

function deviceName(): string {
  if (Platform.OS === "ios") return "iPhone";
  if (Platform.OS === "android") return "Android";
  return "Browser";
}

const DEFAULT_HOST_PORT = "8080";

// The host app listens on 8080; a host typed without a port would otherwise
// go to port 80. Parsed by hand because React Native's URL does not implement
// `port`/`hostname`. Bracketed IPv6 literals keep working.
function withDefaultPort(url: string): string {
  const match = /^(https?:\/\/)([^/?#]*)(.*)$/i.exec(url);
  if (!match) return url;
  const [, scheme, authority, rest] = match;
  const hostPart = authority.slice(authority.lastIndexOf("@") + 1);
  const hasPort = hostPart.startsWith("[") ? /\]:\d+$/.test(hostPart) : /:\d+$/.test(hostPart);
  return hasPort ? url : `${scheme}${authority}:${DEFAULT_HOST_PORT}${rest}`;
}

export function Pair({ navigation, route, remoteOptions }: { navigation: any; route?: { params?: { invitation?: string } }; remoteOptions?: RemoteClientOptions }) {
  const { base, setServer, setTarget, client, hostReachability, target } = useServer();
  const invitation = route?.params?.invitation;
  const [local, setLocal] = useState(false);
  const remote = Boolean(invitation) && !local;
  const current = useRef({ client, target, invitation, remote });
  current.current = { client, target, invitation, remote };
  const mounted = useRef(true);
  const attempt = useRef(0);
  const busyRef = useRef(false);
  // The web app is served by the host it talks to; a native app has to be
  // told where the host is.
  const needsHost = !remote && (Platform.OS !== "web" || local);
  const [host, setHost] = useState(base ?? "");
  const [code, setCode] = useState("");
  const [focusedField, setFocusedField] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; attempt.current++; }; }, []);
  useEffect(() => { setLocal(false); setCode(""); setError(""); }, [invitation]);
  useEffect(() => { attempt.current++; busyRef.current = false; setBusy(false); }, [client, target, invitation, remote]);

  const submit = async () => {
    if (busyRef.current) return;
    setError("");
    let invite: ReturnType<typeof parseRemoteInvite> | null = null;
    if (remote) {
      try {
        if (!remoteOptions || !invitation) throw new Error("unconfigured service");
        invite = parseRemoteInvite(invitation, remoteOptions.trustedOrigin);
      } catch {
        setError("This invitation is invalid or belongs to another service."); return;
      }
    }
    const origin = typeof window !== "undefined" && window.location?.origin ? window.location.origin : "";
    const entered = needsHost ? host.trim() : (base || origin);
    if (!remote && !entered) { setError("Enter your host address"); return; }
    const digits = code.replace(/\s+/g, "");
    if (!digits) { setError("Enter the pairing code shown on your PC"); return; }
    if (remote && !/^\d{6}$/.test(digits)) { setError("Enter the six-digit pairing code shown on your PC"); return; }
    const captured = current.current;
    const n = ++attempt.current;
    const isCurrent = () => mounted.current && attempt.current === n && current.current.client === captured.client && current.current.target === captured.target && current.current.invitation === captured.invitation && current.current.remote === captured.remote;
    busyRef.current = true; setBusy(true);
    try {
      if (invite) {
        const result = await pairRemote(invite.serviceUrl, invite.handle, digits, deviceName(), remoteOptions!);
        if (!isCurrent()) return;
        const owned = await setTarget({ kind: "remote", serviceUrl: invite.serviceUrl, installationId: result.installationId }, result.token, { isCurrent });
        // setTarget publishes the successor itself. Target-change renders can
        // invalidate isCurrent, so compare the actual returned client owner.
        if (mounted.current && (isCurrent() || (current.current.client === owned && current.current.invitation === captured.invitation && current.current.remote === captured.remote))) navigation.replace("InstanceList");
      } else {
        let url: string;
        try {
          url = normalizeBase(/^https?:\/\//.test(entered) ? entered : `http://${entered}`);
          if (needsHost) url = withDefaultPort(url);
        } catch { if (isCurrent()) setError("That host address isn't valid"); return; }
        const result = await pairDevice(url, digits, deviceName());
        if (!isCurrent()) return;
        if ("error" in result) { setError(result.error); return; }
        const owned = await setServer(url, result.token, { isCurrent });
        if (mounted.current && (isCurrent() || (current.current.client === owned && current.current.invitation === captured.invitation && current.current.remote === captured.remote))) navigation.replace("InstanceList");
      }
    } catch (err: unknown) {
      if (!isCurrent()) return;
      if (remote) {
        const reason = err instanceof Error ? err.message : "";
        setError(reason === "offline" || reason === "pairing offline" ? "Your PC is offline. Open the host and try again."
          : reason === "expired_pairing" ? "This invitation has expired. Create a new one on your PC."
          : reason === "not_paired" ? "That pairing code is incorrect. Check the six digits on your PC."
          : "Pairing is unavailable. Please try again.");
      } else setError(err instanceof Error ? err.message : "Pairing failed. Please try again.");
    } finally {
      if (isCurrent()) { busyRef.current = false; setBusy(false); }
    }
  };

  const fieldStyle = (field: string) => ({
    height: 50, backgroundColor: theme.color.surface, borderWidth: 1,
    borderColor: focusedField === field ? theme.color.accent : theme.color.border,
    borderRadius: theme.radius.input,
    shadowColor: theme.color.accent, shadowOpacity: focusedField === field ? 0.25 : 0,
    shadowRadius: 8, shadowOffset: { width: 0, height: 0 }, elevation: focusedField === field ? 4 : 0,
    paddingHorizontal: 16, fontSize: 15, color: theme.color.text,
  });
  const labelStyle = {
    fontFamily: theme.font.mono, fontSize: 9.5, letterSpacing: 1.3, color: theme.color.textDim, marginBottom: 7,
  };

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: theme.color.screen }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ flexGrow: 1, padding: 24, paddingTop: 64 }}>
        <BrandMark />
        <Text style={{ fontFamily: theme.font.bold, fontSize: 29, letterSpacing: -0.6, color: theme.color.text, marginTop: 26 }}>
          Pair this device
        </Text>
        <Text style={{ fontFamily: theme.font.regular, fontSize: 13.5, lineHeight: 21, color: theme.color.textMuted, marginTop: 9, marginBottom: 22 }}>
          On your PC, open EmuCtrl Host and click Pair device. Enter the code it shows. You only do this once per device.
        </Text>
        {invitation ? <Pressable accessibilityRole="button" onPress={() => { setLocal(value => !value); setError(""); }} style={{ marginBottom: 16 }}><Text style={{ color: theme.color.accent }}>{local ? "Use invitation" : "Advanced: local host"}</Text></Pressable> : null}
        {needsHost || remote ? null : (
          <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 22 }}>
            <NetChip kind={target?.kind} state={hostReachability.state} host={hostReachability.host} />
          </View>
        )}
        {needsHost ? (
          <View style={{ marginBottom: 12 }}>
            <Text style={labelStyle}>HOST ADDRESS</Text>
            <TextInput value={host} onChangeText={(value) => { setHost(value); setError(""); }}
              accessibilityLabel="Host address" placeholder="Host address" placeholderTextColor={theme.color.textDim}
              autoCapitalize="none" autoCorrect={false} keyboardType="url" editable={!busy}
              onFocus={() => setFocusedField("host")} onBlur={() => setFocusedField(null)}
              style={{ ...fieldStyle("host"), fontFamily: theme.font.mono }} />
          </View>
        ) : null}
        <Text style={labelStyle}>PAIRING CODE</Text>
        <TextInput value={code} onChangeText={(value) => { setCode(value); setError(""); }}
          accessibilityLabel="Pairing code" placeholder="Pairing code" placeholderTextColor={theme.color.textDim}
          autoCapitalize="none" autoCorrect={false} keyboardType="number-pad" maxLength={12} editable={!busy}
          onFocus={() => setFocusedField("code")} onBlur={() => setFocusedField(null)}
          onSubmitEditing={() => { void submit(); }}
          style={{ ...fieldStyle("code"), fontFamily: theme.font.mono, letterSpacing: 2 }} />
        {error ? <Text accessibilityRole="alert" style={{ fontFamily: theme.font.semibold, fontSize: 13, color: theme.color.error, marginTop: 12 }}>{error}</Text> : null}
        <View style={{ marginTop: 20 }}>
          <Button label={busy ? "Please wait…" : "Pair"} onPress={() => { void submit(); }} loading={busy} />
        </View>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
