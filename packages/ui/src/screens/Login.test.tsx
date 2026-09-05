import React from "react";
import { act, render, fireEvent, waitFor, cleanup } from "@testing-library/react-native";
import { Login } from "./Login";
import * as supabaseAuth from "@wc/core";
import { useServer } from "@wc/core";

jest.mock("@wc/core");

afterEach(cleanup);

describe("Login", () => {
  const setServer = jest.fn().mockResolvedValue({});
  const navigation = { replace: jest.fn() };

  beforeEach(() => {
    jest.clearAllMocks();
    (useServer as jest.Mock).mockReturnValue({
      base: "http://100.1.1.1:8080",
      setServer,
      supabaseUrl: "https://project.supabase.co",
      supabaseAnonKey: "anon-key",
    });
  });

  it("signs in and stores the returned JWT via setServer", async () => {
    (supabaseAuth.signInWithPassword as jest.Mock).mockResolvedValue({
      access_token: "jwt-123",
    });
    const { getByPlaceholderText, getByText } = await render(<Login navigation={navigation} />);

    await act(async () => {
      fireEvent.changeText(getByPlaceholderText("Email"), "a@example.com");
    });
    await act(async () => {
      fireEvent.changeText(getByPlaceholderText("Password"), "pw");
    });
    await act(async () => {
      fireEvent.press(getByText("Sign in"));
    });

    await waitFor(() => {
      expect(setServer).toHaveBeenCalledWith("http://100.1.1.1:8080", "jwt-123");
      expect(navigation.replace).toHaveBeenCalledWith("InstanceList");
    });
  });

  it("shows the error message on rejected credentials", async () => {
    (supabaseAuth.signInWithPassword as jest.Mock).mockResolvedValue({
      error: "Invalid login credentials",
    });
    const { getByPlaceholderText, getByText, findByText } = await render(
      <Login navigation={navigation} />
    );

    await act(async () => {
      fireEvent.changeText(getByPlaceholderText("Email"), "a@example.com");
    });
    await act(async () => {
      fireEvent.changeText(getByPlaceholderText("Password"), "wrong");
    });
    await act(async () => {
      fireEvent.press(getByText("Sign in"));
    });

    expect(await findByText("Invalid login credentials")).toBeTruthy();
    expect(setServer).not.toHaveBeenCalled();
  });
});
