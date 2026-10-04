import { parseInvitation } from "./invitations";
const trustedOrigin = "https://remote.example";
test("parses the trusted invitation without opening a socket", () => {
  expect(parseInvitation(`${trustedOrigin}/pair#invite=abcdefghijklmnopqrstuv`, trustedOrigin)).toEqual({ serviceUrl: trustedOrigin, handle: "abcdefghijklmnopqrstuv" });
});
test.each(["https://evil.example/pair#invite=abcdefghijklmnopqrstuv", "http://remote.example/pair#invite=abcdefghijklmnopqrstuv", "bad", `${trustedOrigin}/pair?token=secret#invite=x`])("ignores invalid or untrusted %s", url => {
  expect(parseInvitation(url, trustedOrigin)).toBeNull();
});

import { act, renderHook, waitFor } from "@testing-library/react-native";
import * as Linking from "expo-linking";
import { useInvitationRouting } from "./invitations";
jest.mock("expo-linking", () => ({ getInitialURL: jest.fn(), addEventListener: jest.fn() }));
const invitation = `${trustedOrigin}/pair#invite=abcdefghijklmnopqrstuv`;
let receive!: (event: { url: string }) => void;
let remove: jest.Mock;
beforeEach(() => {
  jest.clearAllMocks(); remove = jest.fn();
  (Linking.getInitialURL as jest.Mock).mockResolvedValue(null);
  (Linking.addEventListener as jest.Mock).mockImplementation((_, handler) => { receive = handler; return { remove }; });
});

test("cold link waits for hydration and navigation readiness, repeated warm link routes once", async () => {
  (Linking.getInitialURL as jest.Mock).mockResolvedValue(invitation);
  const navigation = { isReady: jest.fn().mockReturnValue(false), navigate: jest.fn() };
  const view = await renderHook<() => void, { ready: boolean }>(({ ready }) => useInvitationRouting(navigation, ready, trustedOrigin), { initialProps: { ready: false } });
  await waitFor(() => expect(Linking.getInitialURL).toHaveBeenCalled());
  await act(async () => { receive({ url: invitation }); });
  expect(navigation.navigate).not.toHaveBeenCalled();
  await view.rerender({ ready: true });
  expect(navigation.navigate).not.toHaveBeenCalled();
  navigation.isReady.mockReturnValue(true); await act(async () => { view.result.current(); });
  expect(navigation.navigate).toHaveBeenCalledTimes(1);
  expect(navigation.navigate).toHaveBeenCalledWith("Pair", { invitation });
  await act(async () => { receive({ url: invitation }); });
  expect(navigation.navigate).toHaveBeenCalledTimes(1);
  await view.unmount(); expect(remove).toHaveBeenCalledTimes(1);
});

test("warm link arriving before cold URL resolution is deduplicated, wrong origin is ignored", async () => {
  let finish!: (url: string) => void;
  (Linking.getInitialURL as jest.Mock).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const navigation = { isReady: () => true, navigate: jest.fn() };
  const view = await renderHook(() => useInvitationRouting(navigation, true, trustedOrigin));
  await act(async () => { receive({ url: invitation.replace("remote.example", "evil.example") }); });
  expect(navigation.navigate).not.toHaveBeenCalled();
  await act(async () => { receive({ url: invitation }); finish(invitation); });
  expect(navigation.navigate).toHaveBeenCalledTimes(1); await view.unmount();
});

test("distinct warm invitation routes exactly once and rejected initial URL leaves local flow", async () => {
  (Linking.getInitialURL as jest.Mock).mockRejectedValue(new Error("unavailable"));
  const navigation = { isReady: () => true, navigate: jest.fn() };
  const view = await renderHook(() => useInvitationRouting(navigation, true, trustedOrigin));
  await act(async () => {}); expect(navigation.navigate).not.toHaveBeenCalled();
  await act(async () => { receive({ url: invitation }); receive({ url: invitation.replace("abcdefghijklmnopqrstuv", "zyxwvutsrqponmlkjihgfe") }); });
  expect(navigation.navigate).toHaveBeenCalledTimes(2); await view.unmount();
});
