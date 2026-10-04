import { connectRemote } from "./remote";
import { connectRemoteClient } from "../remote/client";
import { FakeSocket, target, options, authenticate, selection } from "../remote/testUtils";
import { fakePc, fireReady, flush } from "./peer.testUtils";

afterEach(() => jest.useRealTimers());
test("select plus gathering spends one 30 second deadline and only forwards the remaining budget", async () => {
  jest.useFakeTimers(); FakeSocket.sockets = [];
  const client = connectRemoteClient(target, "secret", undefined, options());
  const socket = await authenticate(), pc = fakePc(); pc.iceGatheringState = "gathering";
  const deadline = performance.now()+30000;
  const selected = client.select("A", { deadline });
  jest.advanceTimersByTime(12000); socket.reply(selection);
  const promise = connectRemote({ client, selection: await selected, deadline, RTCImpl: function () { return pc; } });
  const rejected = expect(promise).rejects.toThrow("timeout"); await flush();
  jest.advanceTimersByTime(15000); pc.iceGatheringState = "complete"; pc._fire("icegatheringstatechange", {}); await flush();
  const frame = socket.sent.find(f => f.op === "negotiate");
  expect(frame.payload.timeout_ms).toBe(3000);
  jest.advanceTimersByTime(3000); await rejected;
  expect(performance.now()).toBe(deadline); expect(pc.close).toHaveBeenCalledTimes(1);
  client.dispose();
});
test("abort then late answer sends one exact close and cannot affect a successor", async () => {
  FakeSocket.sockets = []; const client = connectRemoteClient(target, "secret", undefined, options());
  const socket = await authenticate(), pc = fakePc(), abort = new AbortController(), onStream = jest.fn();
  const promise = connectRemote({ client, selection: {...selection, kind:"remote"}, deadline: performance.now()+30000, signal:abort.signal, RTCImpl:function () {return pc;}, onStream });
  const rejected = expect(promise).rejects.toThrow("canceled"); await flush();
  const frame = socket.sent.find(f => f.op === "negotiate"); abort.abort(); await rejected; await flush();
  const successor = client.select("B"); socket.reply({...selection, serial:"B", session_id:"bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", generation:2}); await successor;
  socket.reply({answer:"LATE", session_id:selection.session_id, generation:1}, frame); fireReady(pc); await flush();
  expect(socket.sent.filter(f => f.op === "close").map(f => f.payload)).toEqual([{session_id:selection.session_id,generation:1}]);
  expect(onStream).not.toHaveBeenCalled(); expect(socket.readyState).toBe(1); client.dispose();
});
test("unavailable relay does not prevent direct media readiness", async () => {
  const pc = fakePc(); const remote = {...selection,kind:"remote" as const,relay_available:false};
  const client = {negotiate:async () => ({answer:"ANSWER", session_id:remote.session_id,generation:remote.generation}), closeSession:jest.fn(async () => {})} as any;
  const promise = connectRemote({client,selection:remote,deadline:performance.now()+30000,RTCImpl:function () {return pc;}});
  await flush(); fireReady(pc); const s = await promise; await s.close(); expect(client.closeSession).toHaveBeenCalledTimes(1);
});
test("known cancellation and a late exchange result retire the UUID once", async () => {
  const pc=fakePc(), abort=new AbortController(); let answer!: (v:any)=>void;
  const client={negotiate:()=>new Promise(r=>{answer=r;}),closeSession:jest.fn(async()=>{})} as any;
  const remote={...selection,kind:"remote" as const};
  const promise=connectRemote({client,selection:remote,deadline:performance.now()+30000,signal:abort.signal,RTCImpl:function(){return pc;}});
  const rejected=expect(promise).rejects.toThrow("canceled"); await flush(); abort.abort(); await rejected;
  answer({answer:"LATE",session_id:remote.session_id,generation:1}); await flush();
  expect(client.closeSession).toHaveBeenCalledTimes(1); expect(pc.setRemoteDescription).not.toHaveBeenCalled();
});

test.each([false, true])("terminal direct ICE failure reports relay unavailable only when unavailable (%s)", async relay_available => {
  const pc = fakePc(), remote = { ...selection, kind: "remote" as const, relay_available };
  const client = {negotiate:async()=>({answer:"ANSWER",session_id:remote.session_id,generation:1}),closeSession:async()=>{}} as any;
  const promise = connectRemote({client,selection:remote,deadline:performance.now()+30000,RTCImpl:function(){return pc;}});
  const rejected=expect(promise).rejects.toMatchObject({code:relay_available ? "ice-failed" : "relay_unavailable"});
  await flush(); pc.iceConnectionState="failed"; pc._fire("iceconnectionstatechange",{}); await rejected;
});
