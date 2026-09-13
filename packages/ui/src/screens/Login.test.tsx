import React from "react";
import { act, render, fireEvent, waitFor, cleanup } from "@testing-library/react-native";
import { Login } from "./Login";
import * as supabaseAuth from "@wc/core";
import { useServer } from "@wc/core";
import { theme } from "../theme/tokens";

jest.mock("@wc/core");

afterEach(cleanup);

describe("Login", () => {
  const instances = jest.fn();
  const setServer = jest.fn();
  const navigation = { replace: jest.fn() };

  beforeEach(() => {
    jest.clearAllMocks();
    instances.mockReset().mockResolvedValue([]);
    setServer.mockReset().mockResolvedValue({ instances });
    (supabaseAuth.signInWithPassword as jest.Mock).mockReset().mockResolvedValue({ access_token: "jwt-123" });
    (supabaseAuth.signUpWithPassword as jest.Mock).mockReset().mockResolvedValue({ access_token: "jwt-new" });
    (useServer as jest.Mock).mockReturnValue({
      base: "http://192.168.1.8:8080",
      setServer,
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
      hostReachability: { route: "lan", state: "reachable", host: "192.168.1.8:8080", rttMs: 12 },
    });
  });

  it("signs in and stores the returned JWT via setServer", async () => {
    (supabaseAuth.signInWithPassword as jest.Mock).mockResolvedValue({
      access_token: "jwt-123",
    });
    const { getByPlaceholderText, getByText } = await render(<Login navigation={navigation} />);

    await act(async () => {
      await fireEvent.changeText(getByPlaceholderText("Email"), "a@example.com");
    });
    await act(async () => {
      await fireEvent.changeText(getByPlaceholderText("Password"), "pw");
    });
    await act(async () => {
      await fireEvent.press(getByText("Sign in"));
    });

    await waitFor(() => {
      expect(setServer).toHaveBeenCalledWith("http://192.168.1.8:8080", "jwt-123");
      expect(supabaseAuth.signInWithPassword).toHaveBeenCalledWith(
        "https://project.supabase.co", "anon-key", "a@example.com", "pw",
      );
      expect(navigation.replace).toHaveBeenCalledWith("InstanceList");
    });
  });

  test("create mode requires and forwards the host pairing code", async () => {
    const screen = await render(<Login navigation={navigation} />);
    await fireEvent.press(screen.getByText("CREATE ACCOUNT"));
    await fireEvent.changeText(screen.getByPlaceholderText("Email"), "new@example.com");
    await fireEvent.changeText(screen.getByPlaceholderText("Password"), "pw");
    await fireEvent.changeText(screen.getByPlaceholderText("Host pairing code"), "   ");
    await fireEvent.press(screen.getByText("Create account"));
    expect(await screen.findByText("Enter your host pairing code")).toBeTruthy();
    expect(supabaseAuth.signUpWithPassword).not.toHaveBeenCalled();
    await fireEvent.changeText(screen.getByPlaceholderText("Host pairing code"), "ABCD-1234");
    await fireEvent.press(screen.getByText("Create account"));
    await waitFor(() => expect(supabaseAuth.signUpWithPassword).toHaveBeenCalledWith(
      "https://project.supabase.co", "anon-key", "new@example.com", "pw",
      { redirectTo: "http://192.168.1.8:8080/login", metadata: { host_pairing_code: "ABCD-1234" } },
    ));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
    expect(instances).toHaveBeenCalledTimes(1);
  });

  test("waits for the authenticated host claim before navigation and prevents duplicate submission", async () => {
    let finishClaim!: (value: never[]) => void;
    instances.mockReturnValue(new Promise((resolve) => { finishClaim = resolve; }));
    const screen = await render(<Login navigation={navigation} />);
    await fireEvent.press(screen.getByText("Sign in"));
    await waitFor(() => expect(instances).toHaveBeenCalledTimes(1));
    expect(navigation.replace).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByText("Please wait…"));
    expect(supabaseAuth.signInWithPassword).toHaveBeenCalledTimes(1);
    await act(async () => { finishClaim([]); });
    expect(navigation.replace).toHaveBeenCalledWith("InstanceList");
  });

  test("keeps a locked host on login with the owner enforcement error", async () => {
    instances.mockRejectedValue(Object.assign(new Error("/instances 403"), { status: 403 }));
    const screen = await render(<Login navigation={navigation} />);
    await fireEvent.press(screen.getByText("Sign in"));
    expect(await screen.findByText("This host belongs to another account")).toBeTruthy();
    expect(navigation.replace).not.toHaveBeenCalled();
    expect(screen.getByText("Sign in")).toBeTruthy();
  });

  test.each(["auth", "storage", "claim"])("recovers from a rejected %s request", async (stage) => {
    const failure = new Error("Connection lost");
    if (stage === "auth") (supabaseAuth.signInWithPassword as jest.Mock).mockRejectedValueOnce(failure);
    if (stage === "storage") setServer.mockRejectedValueOnce(failure);
    if (stage === "claim") instances.mockRejectedValueOnce(failure);
    const screen = await render(<Login navigation={navigation} />);
    await fireEvent.press(screen.getByText("Sign in"));
    expect(await screen.findByText("Connection lost")).toBeTruthy();
    expect(navigation.replace).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByText("Sign in"));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("InstanceList"));
  });

  test("confirmation email does not claim or navigate until authentication", async () => {
    (supabaseAuth.signUpWithPassword as jest.Mock).mockResolvedValue({
      needs_confirmation: true, message: "Confirmation email sent. Please check your inbox.",
    });
    const screen = await render(<Login navigation={navigation} />);
    await fireEvent.press(screen.getByText("CREATE ACCOUNT"));
    await fireEvent.changeText(screen.getByPlaceholderText("Host pairing code"), "ABCD-1234");
    await fireEvent.press(screen.getByText("Create account"));
    expect(await screen.findByText("Confirmation email sent. Please check your inbox.")).toBeTruthy();
    expect(setServer).not.toHaveBeenCalled();
    expect(instances).not.toHaveBeenCalled();
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  test("renders the real reachable private host with the neutral relay state", async () => {
    const screen = await render(<Login navigation={navigation} />);
    expect(screen.getByText("LAN · 192.168.1.8:8080")).toHaveStyle({ color: theme.color.telemetry });
    expect(screen.getByText("RELAY IDLE")).toHaveStyle({ color: theme.color.textMuted });
  });

  test("renders a public base as relay and updates a failed probe without an invented RTT", async () => {
    const current = (useServer as jest.Mock)();
    (useServer as jest.Mock).mockReturnValue({ ...current, base: "https://relay.example",
      hostReachability: { route: "relay", state: "reachable", host: "relay.example", rttMs: 38 },
    });
    const screen = await render(<Login navigation={navigation} />);
    expect(screen.getByText("RELAY · relay.example")).toBeTruthy();
    expect(screen.queryByText(/LAN ·/)).toBeNull();
    (useServer as jest.Mock).mockReturnValue({ ...current, base: "https://relay.example",
      hostReachability: { route: "relay", state: "unreachable", host: "relay.example", rttMs: null },
    });
    await screen.rerender(<Login navigation={navigation} />);
    expect(screen.getByText("RELAY · relay.example")).toHaveStyle({ color: theme.color.live });
    expect(screen.queryByText(/\d+\s*ms/)).toBeNull();
  });

  test("password visibility and focused field styling follow user input", async () => {
    const screen = await render(<Login navigation={navigation} />);
    const email = screen.getByPlaceholderText("Email");
    expect(email).toHaveStyle({ height: 50, backgroundColor: theme.color.surface, borderWidth: 1, borderRadius: 11 });
    await fireEvent(email, "focus");
    expect(email).toHaveStyle({ borderColor: theme.color.accent });
    await fireEvent(email, "blur");
    expect(email).toHaveStyle({ borderColor: theme.color.border });
    expect(screen.getByPlaceholderText("Password").props.secureTextEntry).toBe(true);
    await fireEvent.press(screen.getByText("SHOW"));
    expect(screen.getByPlaceholderText("Password").props.secureTextEntry).toBe(false);
    await fireEvent.press(screen.getByText("HIDE"));
    expect(screen.getByPlaceholderText("Password").props.secureTextEntry).toBe(true);
    expect(screen.queryByPlaceholderText("Host pairing code")).toBeNull();
    await fireEvent.press(screen.getByText("CREATE ACCOUNT"));
    expect(screen.getByPlaceholderText("Host pairing code")).toBeTruthy();
    await fireEvent.press(screen.getByText("SIGN IN"));
    expect(screen.queryByPlaceholderText("Host pairing code")).toBeNull();
  });

  it("shows the error message on rejected credentials", async () => {
    (supabaseAuth.signInWithPassword as jest.Mock).mockResolvedValue({
      error: "Invalid login credentials",
    });
    const { getByPlaceholderText, getByText, findByText } = await render(
      <Login navigation={navigation} />
    );

    await act(async () => {
      await fireEvent.changeText(getByPlaceholderText("Email"), "a@example.com");
    });
    await act(async () => {
      await fireEvent.changeText(getByPlaceholderText("Password"), "wrong");
    });
    await act(async () => {
      await fireEvent.press(getByText("Sign in"));
    });

    expect(await findByText("Invalid login credentials")).toBeTruthy();
    expect(setServer).not.toHaveBeenCalled();
  });
});
