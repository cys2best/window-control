import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";

const mockPermission = { current: { granted: true, canAskAgain: true } as any };
const mockRequest = jest.fn();
let mockScan: ((event: { data: string }) => void) | undefined;
jest.mock("expo-camera", () => ({
  useCameraPermissions: () => [mockPermission.current, mockRequest],
  CameraView: (props: any) => {
    mockScan = props.onBarcodeScanned;
    expect(props.barcodeScannerSettings).toEqual({ barcodeTypes: ["qr"] });
    return null;
  },
}));

import { QrScanner } from "./QrScanner";

beforeEach(() => { mockRequest.mockClear(); mockScan = undefined; mockPermission.current = { granted: true, canAskAgain: true }; });

test("reports the first scanned QR code once", async () => {
  const onScanned = jest.fn();
  await render(<QrScanner onScanned={onScanned} onClose={jest.fn()} />);
  await act(async () => { mockScan!({ data: "https://remote.example/pair#invite=abc" }); mockScan!({ data: "again" }); });
  expect(onScanned).toHaveBeenCalledTimes(1);
  expect(onScanned).toHaveBeenCalledWith("https://remote.example/pair#invite=abc");
});

test("asks for camera access, and offers a way out when it is refused", async () => {
  mockPermission.current = { granted: false, canAskAgain: true };
  const onClose = jest.fn();
  const view = await render(<QrScanner onScanned={jest.fn()} onClose={onClose} />);
  expect(mockRequest).toHaveBeenCalled();
  expect(mockScan).toBeUndefined();
  await fireEvent.press(view.getByText("Close"));
  expect(onClose).toHaveBeenCalled();
});
