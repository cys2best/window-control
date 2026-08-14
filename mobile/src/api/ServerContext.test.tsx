import React from "react";
import { render, waitFor, act } from "@testing-library/react-native";
import { Text } from "react-native";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ServerProvider, useServer } from "./ServerContext";

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock"));

function Probe() {
  const { base, ready, setBase } = useServer();
  return <Text>{ready ? `ready:${base ?? "none"}` : "loading"}</Text>;
}

test("loads persisted base and setBase persists", async () => {
  await AsyncStorage.setItem("wc_base", "http://h:8080");
  const { getByText } = await render(<ServerProvider><Probe /></ServerProvider>);
  await waitFor(() => getByText("ready:http://h:8080"));
});
