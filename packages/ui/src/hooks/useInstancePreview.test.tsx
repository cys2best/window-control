import { act, renderHook, waitFor } from "@testing-library/react-native";
import type { ApiClient, PreviewSource } from "@wc/core";
import { useInstancePreview } from "./useInstancePreview";

test("a late preview from the previous client is never rendered", async () => {
  let resolveOld!: (source: PreviewSource) => void;
  const old = { preview: jest.fn(() => new Promise<PreviewSource>(resolve => { resolveOld = resolve; })) } as unknown as ApiClient;
  const current = { preview: jest.fn(() => new Promise<PreviewSource>(() => {})) } as unknown as ApiClient;
  const view = await renderHook<PreviewSource | null, { client: ApiClient }>(({ client }) => useInstancePreview(client, "a"), { initialProps: { client: old } });
  await waitFor(() => expect(old.preview).toHaveBeenCalledTimes(1));
  await view.rerender({ client: current });
  await act(async () => { resolveOld({ uri: "data:image/jpeg;base64,OLD" }); });
  expect(view.result.current).toBeNull();
  await view.unmount();
});

test("three mounted rows start only two previews and cancellation releases capacity", async () => {
  const client = { preview: jest.fn(() => new Promise<PreviewSource>(() => {})) } as unknown as ApiClient;
  const first = await renderHook(() => useInstancePreview(client, "a"));
  const second = await renderHook(() => useInstancePreview(client, "b"));
  const third = await renderHook(() => useInstancePreview(client, "c"));
  await waitFor(() => expect(client.preview).toHaveBeenCalledTimes(2));
  const signal = (client.preview as jest.Mock).mock.calls[0][1].signal;
  await first.unmount();
  expect(signal.aborted).toBe(true);
  await waitFor(() => expect(client.preview).toHaveBeenCalledTimes(3));
  await second.unmount(); await third.unmount();
});

test("late settlement cannot release a successor slot and queued cancellation never starts", async () => {
  let finish!: (source: PreviewSource) => void;
  const client = { preview: jest.fn().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })).mockImplementation(() => new Promise(() => {})) } as unknown as ApiClient;
  const a = await renderHook(() => useInstancePreview(client, "a"));
  const b = await renderHook(() => useInstancePreview(client, "b"));
  const c = await renderHook(() => useInstancePreview(client, "c"));
  const d = await renderHook(() => useInstancePreview(client, "d"));
  await d.unmount(); await a.unmount();
  await act(async () => { finish({ uri: "old" }); });
  expect(client.preview).toHaveBeenCalledTimes(3);
  await b.unmount(); await c.unmount();
});

test("preview rejection leaves the existing placeholder", async () => {
  const client = { preview: jest.fn().mockRejectedValue(new Error("invalid preview")) } as unknown as ApiClient;
  const view = await renderHook(() => useInstancePreview(client, "a"));
  await waitFor(() => expect(client.preview).toHaveBeenCalled());
  expect(view.result.current).toBeNull(); await view.unmount();
});


test("changing serial hides the old image, even when a previous serial is reused", async () => {
  const client = { preview: jest.fn().mockResolvedValueOnce({ uri: "old-a" }).mockImplementation(() => new Promise(() => {})) } as unknown as ApiClient;
  const view = await renderHook<PreviewSource | null, { serial: string }>(({ serial }) => useInstancePreview(client, serial), { initialProps: { serial: "a" } });
  await waitFor(() => expect(view.result.current).toEqual({ uri: "old-a" }));
  await view.rerender({ serial: "b" }); expect(view.result.current).toBeNull();
  await view.rerender({ serial: "a" }); expect(view.result.current).toBeNull(); await view.unmount();
});
